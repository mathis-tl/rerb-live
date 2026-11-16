"""Tâche 4, J3 : serveur local (cache, plafond d'appels, panne PRIM, /trains). AUCUN réseau : fausse source.

Plan : docs/plan-tache-4.md (J3, arrêt S3 : 1 appel / 120 s à la demande, plafond 1 000 par jour).
Données inventées ; les heures sont en UTC.
"""
import json
import tempfile
import threading
import unittest
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from geometry import NoValidDistance
from memoire import Memoire
from position import Etat
from serveur import (CSV_GARES, TRACE, ErreurPrim, Plafond, Releve, Releveur, calculer_trains, construire_contexte,
                     consolider_livraison, creer_serveur, lire_cle)

T0 = datetime(2026, 11, 16, 10, 0, 0, tzinfo=timezone.utc)
A, B, C = "STIF:StopArea:SP:1:", "STIF:StopArea:SP:2:", "STIF:StopArea:SP:3:"


def h(s):
    return (T0 + timedelta(seconds=s)).strftime("%Y-%m-%dT%H:%M:%SZ")


def appel(gare, arrivee, depart, vise_arr=None, vise_dep=None):
    return {"StopPointRef": {"value": gare}, "ExpectedArrivalTime": h(arrivee), "ExpectedDepartureTime": h(depart),
            "AimedArrivalTime": h(vise_arr if vise_arr is not None else arrivee),
            "AimedDepartureTime": h(vise_dep if vise_dep is not None else depart)}


def livraison(*trains):
    return {"EstimatedTimetableDelivery": [{"EstimatedJourneyVersionFrame": [{"EstimatedVehicleJourney": list(trains)}]}]}


def course(ref, calls, enregistre=0):
    return {"DatedVehicleJourneyRef": ref, "RecordedAtTime": h(enregistre), "EstimatedCalls": calls}


def distance(a, b):
    return 1000.0


def localiser(e):
    return (48.0 + (e.fraction or 0), 2.0)


def nom(ref):
    return {A: "Gare A", B: "Gare B", C: "Gare C"}[ref]


class TestConsolider(unittest.TestCase):
    def test_un_train_et_son_retard(self):
        liv = livraison(course("T1", [appel(A, 0, 60), appel(B, 300, 360, vise_arr=180, vise_dep=240)]))
        trains = consolider_livraison(liv, Memoire())
        train, retards = trains["T1"]
        self.assertEqual([p.gare for p in train.passages], [A, B])
        self.assertEqual(retards[B], 120.0)          # attendu 300 s, prévu 180 s : 2 min de retard
        self.assertEqual(retards[A], 0.0)

    def test_memoire_garde_la_gare_retiree(self):
        memoire = Memoire()
        consolider_livraison(livraison(course("T1", [appel(A, 0, 60), appel(B, 300, 360)])), memoire)
        trains = consolider_livraison(livraison(course("T1", [appel(B, 300, 360)], enregistre=100)), memoire)
        self.assertEqual([p.gare for p in trains["T1"][0].passages], [A, B])
        self.assertTrue(trains["T1"][0].passages[0].reconstruit)

    def test_livraison_vide(self):
        self.assertEqual(consolider_livraison({"EstimatedTimetableDelivery": [{}]}, Memoire()), {})

    def test_course_sans_reference_ignoree(self):
        sans_ref = {"RecordedAtTime": h(0), "EstimatedCalls": [appel(A, 0, 60)]}
        self.assertEqual(consolider_livraison(livraison(sans_ref), Memoire()), {})


def releve_de(*trains, recu=0):
    return Releve(T0 + timedelta(seconds=recu), consolider_livraison(livraison(*trains), Memoire()))


class TestCalculerTrains(unittest.TestCase):
    def test_en_route_avec_position_et_age(self):
        r = releve_de(course("T1", [appel(A, 0, 60), appel(B, 300, 360)], enregistre=0))
        t = calculer_trains(r, T0 + timedelta(seconds=180), distance, localiser, nom)[0]
        self.assertEqual(t["course"], "T1")
        self.assertEqual(t["etat"], Etat.EN_ROUTE.value)
        self.assertEqual((t["gare"], t["gare_suivante"]), ("Gare A", "Gare B"))
        self.assertAlmostEqual(t["lat"], 48.5)        # f = (180-60)/(300-60) = 0,5
        self.assertEqual(t["age_s"], 180)
        self.assertFalse(t["perime"])

    def test_position_avance_avec_le_temps(self):
        r = releve_de(course("T1", [appel(A, 0, 60), appel(B, 300, 360)]))
        t1 = calculer_trains(r, T0 + timedelta(seconds=120), distance, localiser, nom)[0]["lat"]
        t2 = calculer_trains(r, T0 + timedelta(seconds=240), distance, localiser, nom)[0]["lat"]
        self.assertLess(t1, t2)

    def test_a_quai(self):
        r = releve_de(course("T1", [appel(A, 0, 60), appel(B, 300, 360)]))
        t = calculer_trains(r, T0 + timedelta(seconds=30), distance, localiser, nom)[0]
        self.assertEqual((t["etat"], t["gare"]), ("à quai", "Gare A"))

    def test_peri_apres_15_min(self):
        r = releve_de(course("T1", [appel(A, 0, 60), appel(B, 3000, 3060)]), recu=0)
        t = calculer_trains(r, T0 + timedelta(seconds=1000), distance, localiser, nom)[0]
        self.assertTrue(t["perime"])                   # 1000 s > 900 s
        self.assertIsNotNone(t["lat"])                 # placé quand même (décision de la tâche 3)

    def test_non_place_garde_motif_sans_position(self):
        annule = appel(B, 300, 360)
        annule["ArrivalStatus"] = "CANCELLED"
        r = releve_de(course("T1", [appel(A, 0, 60), annule]))
        t = calculer_trains(r, T0 + timedelta(seconds=180), distance, localiser, nom)[0]
        self.assertEqual(t["etat"], Etat.NON_PLACE.value)
        self.assertEqual(t["motif"], "train annulé")
        self.assertIsNone(t["lat"])
        self.assertIsNone(t["lon"])

    def test_localiser_echoue_pas_de_plantage(self):
        def casse(e):
            raise NoValidDistance("aucun candidat")
        r = releve_de(course("T1", [appel(A, 0, 60), appel(B, 300, 360)]))
        t = calculer_trains(r, T0 + timedelta(seconds=180), distance, casse, nom)[0]
        self.assertIsNone(t["lat"])
        self.assertIn("position impossible", t["motif"])

    def test_retard_de_la_gare_suivante(self):
        r = releve_de(course("T1", [appel(A, 0, 60), appel(B, 300, 360, vise_arr=180, vise_dep=240)]))
        t = calculer_trains(r, T0 + timedelta(seconds=120), distance, localiser, nom)[0]
        self.assertEqual(t["retard_s"], 120.0)

    def test_retard_inconnu_si_pas_d_heure_prevue(self):
        c = {"StopPointRef": {"value": B}, "ExpectedArrivalTime": h(300), "ExpectedDepartureTime": h(360)}
        r = releve_de(course("T1", [appel(A, 0, 60), c]))
        t = calculer_trains(r, T0 + timedelta(seconds=120), distance, localiser, nom)[0]
        self.assertIsNone(t["retard_s"])

    def test_aucun_train_masque(self):
        r = releve_de(course("T1", [appel(A, 0, 60), appel(B, 300, 360)]),
                      course("T2", []), course("T3", [appel(A, 900, 960), appel(B, 1200, 1260)]))
        refs = sorted(t["course"] for t in calculer_trains(r, T0 + timedelta(seconds=120), distance, localiser, nom))
        self.assertEqual(refs, ["T1", "T2", "T3"])


class TestPlafond(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.addCleanup(self.dossier.cleanup)
        self.fichier = Path(self.dossier.name) / "plafond.json"
        self.jour = date(2026, 11, 16)

    def plafond(self, maximum=3):
        return Plafond(self.fichier, maximum, lambda: self.jour)

    def test_compte_et_refuse_au_maximum(self):
        p = self.plafond(3)
        for _ in range(3):
            self.assertTrue(p.autorise())
            p.compter()
        self.assertFalse(p.autorise())
        self.assertEqual(p.utilises, 3)

    def test_persiste_entre_deux_demarrages(self):
        p = self.plafond(3)
        p.compter()
        p.compter()
        self.assertEqual(self.plafond(3).utilises, 2)

    def test_repart_le_lendemain(self):
        p = self.plafond(1)
        p.compter()
        self.assertFalse(p.autorise())
        self.jour = date(2026, 11, 17)
        self.assertTrue(p.autorise())
        self.assertEqual(p.utilises, 0)

    def test_fichier_illisible_repart_de_zero(self):
        self.fichier.write_text("pas du json")
        self.assertEqual(self.plafond(3).utilises, 0)


class Fausse:
    """Source PRIM inventée : compte ses appels, peut échouer."""
    def __init__(self, reponses):
        self.reponses, self.appels = list(reponses), 0

    def __call__(self):
        self.appels += 1
        r = self.reponses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class TestReleveur(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.addCleanup(self.dossier.cleanup)
        self.jour = date(2026, 11, 16)

    def releveur(self, fausse, maximum=1000, intervalle=120):
        plafond = Plafond(Path(self.dossier.name) / "p.json", maximum, lambda: self.jour)
        return Releveur(fausse, plafond, intervalle_s=intervalle), plafond

    def liv(self, ref="T1"):
        return livraison(course(ref, [appel(A, 0, 60), appel(B, 300, 360)]))

    def test_premier_appel_remplit_le_cache(self):
        f = Fausse([self.liv()])
        r, _ = self.releveur(f)
        self.assertEqual(r.actualiser(T0), "ok")
        self.assertEqual(f.appels, 1)
        self.assertEqual(r.releve.recu, T0)
        self.assertIn("T1", r.releve.trains)

    def test_pas_de_nouvel_appel_avant_l_intervalle(self):
        f = Fausse([self.liv(), self.liv()])
        r, _ = self.releveur(f)
        r.actualiser(T0)
        self.assertEqual(r.actualiser(T0 + timedelta(seconds=119)), "trop tôt")
        self.assertEqual(f.appels, 1)
        self.assertEqual(r.actualiser(T0 + timedelta(seconds=120)), "ok")
        self.assertEqual(f.appels, 2)

    def test_plafond_atteint_aucun_appel_cache_conserve(self):
        f = Fausse([self.liv(), self.liv()])
        r, p = self.releveur(f, maximum=1)
        r.actualiser(T0)
        self.assertEqual(r.actualiser(T0 + timedelta(seconds=500)), "plafond")
        self.assertEqual(f.appels, 1)
        self.assertIn("T1", r.releve.trains)
        self.assertEqual(p.utilises, 1)

    def test_erreur_garde_le_dernier_releve(self):
        f = Fausse([self.liv(), ErreurPrim("HTTP 502", 502)])
        r, p = self.releveur(f)
        r.actualiser(T0)
        self.assertEqual(r.actualiser(T0 + timedelta(seconds=200)), "erreur")
        self.assertEqual(r.releve.recu, T0)              # inchangé
        self.assertEqual(r.derniere_erreur, "HTTP 502")
        self.assertEqual(p.utilises, 2)                  # un appel tenté compte dans le plafond

    def test_erreur_reessaie_apres_l_intervalle_seulement(self):
        f = Fausse([ErreurPrim("HTTP 502", 502), self.liv()])
        r, _ = self.releveur(f)
        self.assertEqual(r.actualiser(T0), "erreur")
        self.assertEqual(r.actualiser(T0 + timedelta(seconds=10)), "trop tôt")
        self.assertEqual(r.actualiser(T0 + timedelta(seconds=130)), "ok")
        self.assertIsNone(r.derniere_erreur)

    def test_cle_refusee_bloque_tout_appel(self):
        for statut in (401, 403, 429):
            f = Fausse([ErreurPrim(f"HTTP {statut}", statut), self.liv()])
            r, _ = self.releveur(f)
            self.assertEqual(r.actualiser(T0), "erreur")
            self.assertTrue(r.bloque)
            self.assertEqual(r.actualiser(T0 + timedelta(hours=3)), "bloqué")
            self.assertEqual(f.appels, 1)

    def test_un_seul_appel_en_meme_temps(self):
        interne = []

        def fetch():
            interne.append(r.actualiser(T0))             # appel pendant un appel : doit être refusé
            return self.liv()
        r, _ = self.releveur(fetch)
        self.assertEqual(r.actualiser(T0), "ok")
        self.assertEqual(interne, ["occupé"])

    def test_livraison_illisible_est_une_erreur(self):
        f = Fausse([{"pas": "siri"}])
        r, _ = self.releveur(f)
        self.assertEqual(r.actualiser(T0), "erreur")
        self.assertIsNone(r.releve)

    def test_sans_releve_le_cache_est_vide(self):
        r, _ = self.releveur(Fausse([]))
        self.assertIsNone(r.releve)


class TestLireCle(unittest.TestCase):
    def test_lit_la_cle(self):
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text("MBTA_API_KEY=autre\nPRIM_API_KEY=secret123\n")
            self.assertEqual(lire_cle(env), "secret123")

    def test_cle_absente_message_sans_la_valeur(self):
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text("MBTA_API_KEY=valeur_a_ne_pas_afficher\n")
            with self.assertRaises(ValueError) as cm:
                lire_cle(env)
            self.assertNotIn("valeur_a_ne_pas_afficher", str(cm.exception))

    def test_fichier_absent(self):
        with self.assertRaises(ValueError):
            lire_cle(Path("/inexistant/.env"))


class TestHttp(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.addCleanup(self.dossier.cleanup)
        f = Fausse([livraison(course("T1", [appel(A, 0, 60), appel(B, 300, 360)]))])
        plafond = Plafond(Path(self.dossier.name) / "p.json", 1000, lambda: date(2026, 11, 16))
        self.releveur = Releveur(f, plafond, intervalle_s=120)
        self.horloge = lambda: T0 + timedelta(seconds=180)
        self.serveur = creer_serveur(self.releveur, distance, localiser, nom, port=0, horloge=self.horloge)
        threading.Thread(target=self.serveur.serve_forever, daemon=True).start()
        self.addCleanup(self.serveur.server_close)
        self.addCleanup(self.serveur.shutdown)
        self.base = f"http://127.0.0.1:{self.serveur.server_address[1]}"

    def test_ecoute_localhost_seulement(self):
        self.assertEqual(self.serveur.server_address[0], "127.0.0.1")

    def test_trains_json(self):
        with urllib.request.urlopen(self.base + "/trains") as r:
            corps = json.loads(r.read())
            self.assertEqual(r.headers["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual([t["course"] for t in corps["trains"]], ["T1"])
        # cache vide : la requête déclenche l'appel, au « maintenant » de l'horloge (T0 + 180 s)
        self.assertEqual(corps["releve_recu"], (T0 + timedelta(seconds=180)).isoformat())
        self.assertEqual(corps["plafond"], {"utilises": 1, "maximum": 1000})
        self.assertIsNone(corps["erreur"])

    def test_trains_sans_releve(self):
        self.releveur.actualiser = lambda maintenant: "erreur"
        self.releveur.releve = None
        with urllib.request.urlopen(self.base + "/trains") as r:
            corps = json.loads(r.read())
        self.assertEqual(corps["trains"], [])
        self.assertIsNone(corps["releve_recu"])

    def test_route_inconnue_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(self.base + "/secret")
        self.assertEqual(cm.exception.code, 404)


@unittest.skipUnless(TRACE.exists() and CSV_GARES.exists(), "fichiers statiques absents")
class TestContexteReel(unittest.TestCase):
    """Câblage réel (tracé et gares du dépôt, hors ligne) : un train entre Massy - Palaiseau et Massy - Verrières."""
    MASSY_PAL, MASSY_VER = "STIF:StopArea:SP:58774:", "STIF:StopArea:SP:47940:"

    def test_position_sur_les_rails_entre_deux_gares(self):
        distance_, localiser_, nom_ = construire_contexte()
        r = releve_de(course("T1", [appel(self.MASSY_PAL, 0, 60), appel(self.MASSY_VER, 300, 360)]))
        t = calculer_trains(r, T0 + timedelta(seconds=180), distance_, localiser_, nom_)[0]
        self.assertEqual(t["etat"], "en route")
        self.assertEqual((t["gare"], t["gare_suivante"]), ("Massy - Palaiseau", "Massy - Verrières"))
        self.assertIsNotNone(t["lat"], t["motif"])
        self.assertTrue(48.7 < t["lat"] < 48.75 and 2.2 < t["lon"] < 2.3, (t["lat"], t["lon"]))

    def test_a_quai_aux_coordonnees_de_la_gare(self):
        distance_, localiser_, nom_ = construire_contexte()
        r = releve_de(course("T1", [appel(self.MASSY_PAL, 0, 60), appel(self.MASSY_VER, 300, 360)]))
        t = calculer_trains(r, T0 + timedelta(seconds=30), distance_, localiser_, nom_)[0]
        self.assertEqual((t["etat"], t["gare"]), ("à quai", "Massy - Palaiseau"))
        self.assertIsNotNone(t["lat"])


if __name__ == "__main__":
    unittest.main()
