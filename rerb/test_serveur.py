"""Tâche 4, J3 : serveur local (cache, plafond d'appels, panne PRIM, /trains). AUCUN réseau : fausse source.

Plan : docs/plan-tache-4.md (J3, arrêt S3 : 1 appel / 120 s à la demande, plafond 1 000 par jour).
Données inventées ; les heures sont en UTC.
"""
import http.server
import json
import math
import tempfile
import threading
import unittest
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from geometry import NoValidDistance
from lissage import Lisseur
from memoire import Memoire
from position import Etat
from gare import ReleveurGares
from magasin import ErreurMagasin, MagasinFaux, MagasinLocal
from serveur import (CLE_BLOCAGE, CSV_GARES, DEMI_VOIE_M, DELAI_PRIM_S, FENETRE_NUIT, INTERVALLE_S, NOM_VERROU, NUIT_DEBUT_H, NUIT_FIN_H,
                     PARIS, PLAFOND_JOUR, TRACE, VERROU_S, ErreurPrim, appeler_prim, jour_paris, Plafond, Releve, Releveur, calculer_trains,
                     chaine_demo, charger_geo, construire_contexte, consolider_livraison, creer_serveur, lire_cle)

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


def localiser(gare, suivante, distance_m):
    """Fausse géométrie : 1 000 m entre gares ; latitude = 48 + avancement ; cap 90° (est)."""
    return (48.0 + distance_m / 1000.0, 2.0, 90.0)


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


def _nourrir(lisseur, releve, cas):
    """Comme `charge_utile` : chaque relevé reçu est avancé une seule fois dans le lisseur (identité du relevé)."""
    vus = cas.__dict__.setdefault("_releves_vus", [])
    if not any(v is releve for v in vus):
        lisseur.avancer({ref: train for ref, (train, _r) in releve.trains.items()}, releve.recu)
        vus.append(releve)


class TestCalculerTrains(unittest.TestCase):
    def test_en_route_avec_position_et_age(self):
        r = releve_de(course("T1", [appel(A, 0, 25), appel(B, 335, 395)], enregistre=0))
        t = calculer_trains(r, T0 + timedelta(seconds=180), distance, localiser, nom)[0]
        self.assertEqual(t["course"], "T1")
        self.assertEqual(t["etat"], Etat.EN_ROUTE.value)
        self.assertEqual((t["gare"], t["gare_suivante"]), ("Gare A", "Gare B"))
        self.assertAlmostEqual(t["lat"], 48.5)        # f = (180-25)/(335-25) = 0,5
        self.assertEqual(t["cap"], 90.0)
        self.assertEqual(t["age_s"], 180)
        self.assertFalse(t["perime"])

    def test_position_avance_avec_le_temps(self):
        r = releve_de(course("T1", [appel(A, 0, 60), appel(B, 300, 360)]))
        t1 = calculer_trains(r, T0 + timedelta(seconds=120), distance, localiser, nom)[0]["lat"]
        t2 = calculer_trains(r, T0 + timedelta(seconds=240), distance, localiser, nom)[0]["lat"]
        self.assertLess(t1, t2)

    def test_a_quai(self):
        r = releve_de(course("T1", [appel(A, 0, 25), appel(B, 335, 395)]))
        t = calculer_trains(r, T0 + timedelta(seconds=20), distance, localiser, nom)[0]
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
        def casse(gare, suivante, distance_m):
            raise NoValidDistance("aucun candidat")
        r = releve_de(course("T1", [appel(A, 0, 60), appel(B, 300, 360)]))
        t = calculer_trains(r, T0 + timedelta(seconds=180), distance, casse, nom)[0]
        self.assertIsNone(t["lat"])
        self.assertIn("position impossible", t["motif"])

    def test_nom_de_gare_inconnu_pas_de_plantage(self):
        def nom_cassant(ref):
            raise KeyError(ref)
        r = releve_de(course("T1", [appel(A, 0, 60), appel(B, 300, 360)]), course("T2", [appel(A, 0, 60), appel(B, 300, 360)]))
        trains = calculer_trains(r, T0 + timedelta(seconds=180), distance, localiser, nom_cassant)
        self.assertEqual(len(trains), 2)             # un nom introuvable ne fait pas disparaître les trains
        self.assertEqual(trains[0]["gare"], A)       # on affiche la référence brute plutôt que de planter

    def test_horaires_impossibles_vitesse_limitee_et_marquee(self):
        # 1 000 m en 10 s = 100 m/s : impossible. Affiché : au plus 33 m/s depuis le départ, train marqué incohérent.
        r = releve_de(course("T1", [appel(A, 0, 25), appel(B, 35, 95)]))
        t = calculer_trains(r, T0 + timedelta(seconds=30), distance, localiser, nom)[0]
        self.assertTrue(t["horaires_incoherents"])
        self.assertAlmostEqual(t["vitesse_implicite_ms"], 100.0)
        self.assertAlmostEqual(t["lat"], 48 + 165.0 / 1000.0)        # localiser(): lat = 48 + distance/1000 ; 33 m/s × 5 s
        self.assertIsNotNone(t["motif_vitesse"])

    def test_horaires_normaux_non_modifies(self):
        r = releve_de(course("T1", [appel(A, 0, 25), appel(B, 335, 395)]))
        t = calculer_trains(r, T0 + timedelta(seconds=180), distance, localiser, nom)[0]
        self.assertFalse(t["horaires_incoherents"])
        self.assertAlmostEqual(t["lat"], 48.5)                       # inchangé : 1 000 m en 310 s = 3,2 m/s
        self.assertAlmostEqual(t["vitesse_implicite_ms"], 1000 / 310)

    def test_juste_sous_la_limite_non_marque(self):
        r = releve_de(course("T1", [appel(A, 0, 60), appel(B, 91, 200)]))
        t = calculer_trains(r, T0 + timedelta(seconds=65), distance, localiser, nom)[0]
        self.assertFalse(t["horaires_incoherents"])

    def test_duree_min_estimee_remplace_la_limite_unique(self):
        # 1 000 m en 60 s = 16,7 m/s : plausible pour la limite unique, mais la durée minimale estimée du tronçon est 120 s.
        r = releve_de(course("T1", [appel(A, 0, 25), appel(B, 85, 145)]))
        t = calculer_trains(r, T0 + timedelta(seconds=55), distance, localiser, nom,
                            duree_min=lambda a, b: (120.0, "estimée"))[0]
        self.assertTrue(t["horaires_incoherents"])
        self.assertTrue(t["correction_estimee"])
        self.assertEqual(t["duree_min_s"], 120.0)
        self.assertEqual(t["source_duree_min"], "estimée")
        self.assertAlmostEqual(t["lat"], 48 + 0.25)                  # 30 s écoulées sur 120 s plausibles : 250 m (au lieu de 500 m)
        self.assertIn("correction estimée", t["motif_vitesse"])

    def test_duree_annoncee_au_dessus_du_minimum_non_modifiee(self):
        r = releve_de(course("T1", [appel(A, 0, 25), appel(B, 145, 205)]))
        t = calculer_trains(r, T0 + timedelta(seconds=55), distance, localiser, nom,
                            duree_min=lambda a, b: (120.0, "estimée"))[0]
        self.assertFalse(t["horaires_incoherents"])
        self.assertFalse(t["correction_estimee"])
        self.assertAlmostEqual(t["lat"], 48.25)                      # 30 s sur 120 s annoncées : 250 m, inchangé

    def test_troncon_inconnu_repli_plancher_33_ms(self):
        r = releve_de(course("T1", [appel(A, 0, 25), appel(B, 35, 95)]))
        t = calculer_trains(r, T0 + timedelta(seconds=30), distance, localiser, nom, duree_min=lambda a, b: None)[0]
        self.assertTrue(t["horaires_incoherents"])
        self.assertAlmostEqual(t["lat"], 48 + 165.0 / 1000.0)        # comme avant : 33 m/s x 5 s
        self.assertEqual(t["source_duree_min"], "plancher")
        self.assertAlmostEqual(t["duree_min_s"], 1000 / 33.0)

    def test_duree_min_jamais_sous_le_plancher(self):
        # une table fautive qui donnerait 10 s pour 1 000 m ne doit pas relâcher la limite de 33 m/s
        r = releve_de(course("T1", [appel(A, 0, 25), appel(B, 35, 95)]))
        t = calculer_trains(r, T0 + timedelta(seconds=30), distance, localiser, nom,
                            duree_min=lambda a, b: (10.0, "estimée"))[0]
        self.assertAlmostEqual(t["duree_min_s"], 1000 / 33.0)
        self.assertAlmostEqual(t["lat"], 48 + 165.0 / 1000.0)

    def test_a_quai_pas_de_vitesse(self):
        r = releve_de(course("T1", [appel(A, 0, 25), appel(B, 335, 395)]))
        t = calculer_trains(r, T0 + timedelta(seconds=20), distance, localiser, nom)[0]
        self.assertFalse(t["horaires_incoherents"])
        self.assertFalse(t["correction_estimee"])
        self.assertIsNone(t["vitesse_implicite_ms"])

    def test_retard_de_la_gare_suivante(self):
        r = releve_de(course("T1", [appel(A, 0, 60), appel(B, 300, 360, vise_arr=180, vise_dep=240)]))
        t = calculer_trains(r, T0 + timedelta(seconds=120), distance, localiser, nom)[0]
        self.assertEqual(t["retard_s"], 120.0)

    def test_retard_inconnu_si_pas_d_heure_prevue(self):
        c = {"StopPointRef": {"value": B}, "ExpectedArrivalTime": h(300), "ExpectedDepartureTime": h(360)}
        r = releve_de(course("T1", [appel(A, 0, 60), c]))
        t = calculer_trains(r, T0 + timedelta(seconds=120), distance, localiser, nom)[0]
        self.assertIsNone(t["retard_s"])

    def test_a_quai_transmet_la_gare_suivante_pour_le_cap(self):
        appels = []

        def espion(gare, suivante, distance_m):
            appels.append((gare, suivante, distance_m))
            return (48.0, 2.0, 45.0)
        r = releve_de(course("T1", [appel(A, 0, 25), appel(B, 335, 395), appel(C, 635, 695)]))
        calculer_trains(r, T0 + timedelta(seconds=20), distance, espion, nom)
        self.assertEqual(appels[0], (A, B, 0.0))   # 1er appel = position actuelle ; les suivants sont les points d'incertitude

    def test_a_quai_au_terminus_pas_de_gare_suivante(self):
        appels = []

        def espion(gare, suivante, distance_m):
            appels.append((gare, suivante))
            return (48.0, 2.0, None)
        r = releve_de(course("T1", [appel(A, 0, 60), appel(B, 300, 360)]))
        t = calculer_trains(r, T0 + timedelta(seconds=330), distance, espion, nom)[0]
        self.assertEqual(appels[0], (B, None))   # 1er appel = position actuelle ; les suivants sont les points d'incertitude
        self.assertIsNone(t["cap"])

    def test_au_terminus_drapeau_pour_la_carte(self):
        espion = lambda gare, suivante, distance_m: (48.0, 2.0, None)
        r = releve_de(course("T1", [appel(A, 0, 60), appel(B, 300, 360)]))
        au_terminus = calculer_trains(r, T0 + timedelta(seconds=330), distance, espion, nom)[0]
        self.assertEqual((au_terminus["etat"], au_terminus["terminus"]), ("à quai", True), "arrivé au terminus : le séjour de 60 s s'affiche en rouge")
        a_quai_normal = calculer_trains(r, T0 + timedelta(seconds=30), distance, espion, nom)[0]
        self.assertFalse(a_quai_normal["terminus"], "à quai à l'origine : pas le terminus")
        en_route = calculer_trains(r, T0 + timedelta(seconds=150), distance, espion, nom)[0]
        self.assertFalse(en_route["terminus"])

    def test_non_place_cap_vide(self):
        annule = appel(B, 300, 360)
        annule["ArrivalStatus"] = "CANCELLED"
        r = releve_de(course("T1", [appel(A, 0, 60), annule]))
        t = calculer_trains(r, T0 + timedelta(seconds=180), distance, localiser, nom)[0]
        self.assertIsNone(t["cap"])

    def test_aucun_train_masque(self):
        r = releve_de(course("T1", [appel(A, 0, 60), appel(B, 300, 360)]),
                      course("T2", []), course("T3", [appel(A, 900, 960), appel(B, 1200, 1260)]))
        refs = sorted(t["course"] for t in calculer_trains(r, T0 + timedelta(seconds=120), distance, localiser, nom))
        self.assertEqual(refs, ["T1", "T2", "T3"])


    # --- incertitude : chemin le long des rails entre t - delta et t + delta (J2 bis : médiane 44 s, 90e centile 117 s) ---
    def _en_route(self, secondes=180):
        r = releve_de(course("T1", [appel(A, 0, 25), appel(B, 335, 395)], enregistre=0))
        return calculer_trains(r, T0 + timedelta(seconds=secondes), distance, localiser, nom)[0]

    def test_incertitude_deltas_et_extremites(self):
        inc = self._en_route()["incertitude"]
        self.assertEqual((inc["mediane_s"], inc["p90_s"]), (44.0, 117.0))
        # f = (t - 25) / 310 ; lat = 48 + f ; t = 180 : médiane 136 s -> 224 s, p90 63 s -> 297 s
        self.assertAlmostEqual(inc["mediane"][0][0], 48 + 111 / 310)
        self.assertAlmostEqual(inc["mediane"][-1][0], 48 + 199 / 310)
        self.assertAlmostEqual(inc["p90"][0][0], 48 + 38 / 310)
        self.assertAlmostEqual(inc["p90"][-1][0], 48 + 272 / 310)
        self.assertGreaterEqual(len(inc["p90"]), 20)   # échantillonnage dense : la droite entre deux points ne coupe pas un virage

    def test_incertitude_chemin_ordonne_et_contient_la_position_actuelle(self):
        r = releve_de(course("T1", [appel(A, 0, 60), appel(B, 300, 360)], enregistre=0))
        rang = {A: 0, B: 1}
        cumulee = lambda gare, suivante, d: (48.0 + rang[gare] + d / 1000.0, 2.0, 90.0)   # B après A : latitude croissante
        t = calculer_trains(r, T0 + timedelta(seconds=180), distance, cumulee, nom)[0]
        inc = t["incertitude"]
        for chemin in (inc["mediane"], inc["p90"]):
            lats = [pt[0] for pt in chemin]
            self.assertEqual(lats, sorted(lats))
            self.assertTrue(any(abs(lat - t["lat"]) < 1e-9 for lat in lats))
            self.assertTrue(all(len(pt) == 2 for pt in chemin))

    def test_incertitude_mediane_incluse_dans_p90(self):
        inc = self._en_route()["incertitude"]
        self.assertLess(len(inc["mediane"]), len(inc["p90"]))
        for pt in inc["mediane"]:
            self.assertIn(pt, inc["p90"])

    def test_incertitude_point_non_calculable_omis_jamais_invente(self):
        # à t = 20 s (à quai en A, 1re arrivée à 0 s), t - 44 s et t - 117 s tombent avant la 1re gare : omis
        t = self._en_route(20)
        chemin = t["incertitude"]["p90"]
        self.assertTrue(chemin)
        self.assertTrue(all(pt[0] >= 48.0 for pt in chemin))
        self.assertLess(len(chemin), len(self._en_route(180)["incertitude"]["p90"]))

    def test_incertitude_localiser_echoue_sur_un_point_pas_de_plantage(self):
        def limite(gare, suivante, distance_m):
            if distance_m > 600:
                raise ValueError("hors tracé")
            return localiser(gare, suivante, distance_m)
        r = releve_de(course("T1", [appel(A, 0, 25), appel(B, 335, 395)], enregistre=0))
        t = calculer_trains(r, T0 + timedelta(seconds=180), distance, limite, nom)[0]
        chemin = t["incertitude"]["p90"]
        self.assertTrue(chemin and all(pt[0] <= 48.6 for pt in chemin))
        self.assertTrue(48.55 < max(pt[0] for pt in chemin) <= 48.6)   # arrêté à la limite (600 m), rien d'inventé au-delà
        self.assertAlmostEqual(t["lat"], 48.5)

    def test_incertitude_absente_si_non_place(self):
        r = releve_de(course("T1", [appel(A, 100, 60), appel(B, 300, 360)]))   # départ avant l'arrivée : heures incohérentes
        t = calculer_trains(r, T0 + timedelta(seconds=180), distance, localiser, nom)[0]
        self.assertEqual(t["etat"], Etat.NON_PLACE.value)
        self.assertIsNone(t["incertitude"])

    def test_incertitude_a_quai_toute_la_plage_pas_de_trait_de_longueur_nulle(self):
        # arrêt de 1 h : tous les instants donnent la même position -> un seul point, la page ne dessine rien
        r = releve_de(course("T1", [appel(A, 0, 3600), appel(B, 3900, 3960)], enregistre=0))
        t = calculer_trains(r, T0 + timedelta(seconds=1800), distance, localiser, nom)[0]
        self.assertLessEqual(len(t["incertitude"]["p90"]), 1)
        self.assertLessEqual(len(t["incertitude"]["mediane"]), 1)

    def test_incertitude_terminus_ne_depasse_pas_le_terminus(self):
        r = releve_de(course("T1", [appel(A, 0, 60), appel(B, 300, 360)], enregistre=0))
        t = calculer_trains(r, T0 + timedelta(seconds=330), distance, localiser, nom)[0]   # à quai au terminus B
        self.assertEqual(t["etat"], "à quai")
        chemin = t["incertitude"]["p90"]
        self.assertTrue(chemin)
        self.assertTrue(all(pt[0] <= 48.0 + 1.0 + 1e-9 for pt in chemin))   # jamais au-delà de B (lat 49)

    def test_incertitude_utilise_la_correction_de_duree(self):
        # 1 000 m annoncés en 60 s (100 m/s) : corrigé à 120 s ; le chemin suit la position corrigée, pas la position brute
        r = releve_de(course("T1", [appel(A, 0, 25), appel(B, 85, 145)], enregistre=0))
        corr = lambda a, b: (120.0, "estimée")
        t = calculer_trains(r, T0 + timedelta(seconds=55), distance, localiser, nom, duree_min=corr)[0]
        brut = 48 + (30 / 60)    # sans correction : f = 0,5
        self.assertLess(t["lat"], brut)
        self.assertTrue(any(abs(pt[0] - t["lat"]) < 1e-9 for pt in t["incertitude"]["p90"]))
        self.assertLess(max(pt[0] for pt in t["incertitude"]["p90"]), 48 + 1.0)

    def test_incertitude_a_cheval_sur_deux_troncons_reste_ordonnee(self):
        rang = {A: 0, B: 1, C: 2}

        def cumulee(gare, suivante, distance_m):   # latitude croissante le long de toute la ligne (1 000 m par tronçon)
            return (48.0 + rang[gare] + distance_m / 1000.0, 2.0, 90.0)
        r = releve_de(course("T1", [appel(A, 0, 60), appel(B, 300, 360), appel(C, 600, 660)], enregistre=0))
        t = calculer_trains(r, T0 + timedelta(seconds=310), distance, cumulee, nom)[0]   # à quai en B (arrêt 300-360 s)
        lats = [pt[0] for pt in t["incertitude"]["p90"]]
        self.assertEqual(lats, sorted(lats))
        self.assertLess(lats[0], 48 + 1.0)    # début sur le tronçon A -> B
        self.assertGreaterEqual(lats[-1], 48 + 1.0)   # fin en B ou après

    def test_incertitude_serialisable_en_json(self):
        json.dumps(self._en_route())


class TestLissageDansLeServeur(unittest.TestCase):
    """Étape 3 du plan séjour à quai : une révision de PRIM ne fait pas sauter le train (rerb/lissage.py)."""

    def trains(self, releve, t, lisseur=None):
        if lisseur is not None:
            _nourrir(lisseur, releve, self)
        return calculer_trains(releve, T0 + timedelta(seconds=t), distance, localiser, nom, lisseur=lisseur)

    def test_premiere_observation_identique_a_sans_lisseur(self):
        r = releve_de(course("T1", [appel(A, 0, 60), appel(B, 300, 360)]))
        self.assertEqual(self.trains(r, 150, Lisseur(tolerance_s=30.0, biais_s=0.0)), self.trains(r, 150))

    def test_revision_de_90_s_ne_fait_pas_sauter_le_train(self):
        r1 = releve_de(course("T1", [appel(A, 0, 60), appel(B, 300, 360)]))
        r2 = releve_de(course("T1", [appel(A, 0, 60), appel(B, 390, 450)]), recu=140)   # reçu après r1 : la rampe part de 140 s
        l = Lisseur(tolerance_s=30.0, biais_s=0.0)
        self.trains(r1, 140, l)
        avant = self.trains(r1, 150)[0]["lat"]
        brut = self.trains(r2, 150)[0]["lat"]
        lisse = self.trains(r2, 150, l)[0]["lat"]
        self.assertGreater(abs(brut - avant), 0.1)        # sans lissage, le train recule de plus de 100 m
        self.assertLess(abs(lisse - avant), 0.01)         # avec : moins de 10 m
        self.assertLess(self.trains(r2, 150, l)[0]["lat"] - 48.0, 1.0)

    def test_l_incertitude_suit_le_train_lisse(self):
        r1 = releve_de(course("T1", [appel(A, 0, 60), appel(B, 300, 360)]))
        r2 = releve_de(course("T1", [appel(A, 0, 60), appel(B, 390, 450)]), recu=140)
        l = Lisseur(tolerance_s=30.0, biais_s=0.0)
        self.trains(r1, 140, l)
        t = self.trains(r2, 150, l)[0]
        self.assertTrue(any(abs(pt[0] - t["lat"]) < 1e-9 for pt in t["incertitude"]["p90"]))

    def test_course_absente_du_releve_est_oubliee(self):
        l = Lisseur(tolerance_s=30.0, biais_s=0.0)
        self.trains(releve_de(course("T1", [appel(A, 0, 60), appel(B, 3000, 3060)])), 10, l)
        self.trains(releve_de(course("T2", [appel(A, 0, 60), appel(B, 3000, 3060)])), 20, l)
        # T1 revient avec une heure révisée : elle repart de la valeur PRIM (mémoire purgée), pas d'une rampe
        r = releve_de(course("T1", [appel(A, 0, 60), appel(B, 3090, 3150)]))
        self.assertEqual(self.trains(r, 30, l), self.trains(r, 30))

    def test_le_serveur_http_utilise_un_lisseur(self):
        dossier = tempfile.TemporaryDirectory()
        self.addCleanup(dossier.cleanup)
        f = Fausse([livraison(course("T1", [appel(A, 0, 60), appel(B, 300, 360)]))])
        releveur = Releveur(f, Plafond(MagasinLocal(Path(dossier.name) / "p.json"), PLAFOND_JOUR, horloge=lambda: T0), intervalle_s=INTERVALLE_S)
        maintenant = T0 + timedelta(seconds=180)
        l = Lisseur(tolerance_s=30.0, biais_s=0.0)
        serveur = creer_serveur(releveur, distance, localiser, nom, port=0, horloge=lambda: maintenant, lisseur=l)
        threading.Thread(target=serveur.serve_forever, daemon=True).start()
        self.addCleanup(serveur.server_close)
        self.addCleanup(serveur.shutdown)
        urllib.request.urlopen(f"http://127.0.0.1:{serveur.server_address[1]}/trains").read()
        self.assertTrue(l.modifie("T1") is False and l.suivi_s("T1") == 0.0)   # la course est connue du lisseur de ce serveur (un seul relevé : suivi nul)
        self.assertEqual(l.adoptee("T1", B), T0 + timedelta(seconds=300))


class TestEtiquettes(unittest.TestCase):
    """Étape 4 du plan : « périmé » est remplacé par deux faits distincts (décision de Mathis, 18/11)."""

    def trains(self, releve, t, lisseur=None):
        if lisseur is not None:
            _nourrir(lisseur, releve, self)
        return calculer_trains(releve, T0 + timedelta(seconds=t), distance, localiser, nom, lisseur=lisseur)[0]

    def test_donnees_anciennes_remplace_perime_sans_changer_le_seuil(self):
        r = releve_de(course("T1", [appel(A, 0, 60), appel(B, 3000, 3060)]), recu=0)
        for t_s, attendu in ((900, False), (901, True)):          # seuil de 15 min, strict
            t = self.trains(r, t_s)
            self.assertEqual(t["donnees_anciennes"], attendu)
            self.assertEqual(t["perime"], attendu, "ancien champ conservé un temps pour la compatibilité")

    def _suivi(self, vise_dep_b=360, jusqu_a=700, retard_b=0):
        """Un relevé toutes les 120 s (avec la même heure estimée) jusqu'à `jusqu_a` ; renvoie le dernier train calculé."""
        l, t = Lisseur(tolerance_s=30.0, biais_s=0.0), None
        for recu in list(range(0, jusqu_a, 120)) + [jusqu_a]:
            r = releve_de(course("T1", [appel(A, 0, 60), appel(B, 3000 + retard_b, 3060 + retard_b, vise_arr=3000, vise_dep=vise_dep_b + 2700)]), recu=recu)
            t = self.trains(r, recu, l)
        return t

    def test_horaire_theorique_apres_10_min_sans_aucun_ecart(self):
        self.assertTrue(self._suivi(jusqu_a=700)["horaire_theorique"])

    def test_pas_d_etiquette_avant_10_min_d_observation(self):
        self.assertFalse(self._suivi(jusqu_a=599)["horaire_theorique"])

    def test_pas_d_etiquette_si_prim_annonce_un_retard(self):
        self.assertFalse(self._suivi(retard_b=120)["horaire_theorique"])

    def test_pas_d_etiquette_si_prim_a_deja_modifie_une_heure(self):
        r1 = releve_de(course("T1", [appel(A, 0, 60), appel(B, 3000, 3060)]), recu=0)
        r2 = releve_de(course("T1", [appel(A, 0, 60), appel(B, 3010, 3070, vise_arr=3010, vise_dep=3070)]), recu=120)
        l = Lisseur(tolerance_s=30.0, biais_s=0.0)
        self.trains(r1, 0, l)
        self.trains(r2, 120, l)                                    # PRIM a bougé de 10 s : il y a du temps réel sur ce train
        r3 = releve_de(course("T1", [appel(A, 0, 60), appel(B, 3010, 3070, vise_arr=3010, vise_dep=3070)]), recu=700)
        self.assertFalse(self.trains(r3, 700, l)["horaire_theorique"])

    def test_retenu_est_servi_quand_prim_repousse_l_arrivee_de_plus_de_120_s(self):
        l = Lisseur(tolerance_s=15.0, biais_s=0.0)
        self.trains(releve_de(course("T1", [appel(A, 0, 60), appel(B, 300, 360)]), recu=100), 100, l)
        r = releve_de(course("T1", [appel(A, 0, 60), appel(B, 520, 580, vise_arr=300, vise_dep=360)]), recu=110)
        self.assertFalse(self.trains(r, 110, l)["retenu"])          # la révision vient d'arriver : le train roule encore à sa vitesse
        arrete = self.trains(r, 400, l)
        self.assertEqual((arrete["etat"], arrete["retenu"]), ("en route", True))
        self.assertFalse(self.trains(r, 530, l)["retenu"])          # arrivé à la nouvelle heure
        sans = releve_de(course("T1", [appel(A, 0, 60), appel(B, 300, 360)]), recu=0)
        self.assertFalse(self.trains(sans, 200)["retenu"])

    def test_pas_d_etiquette_sur_un_releve_fige(self):
        # PRIM tombe : le même relevé (recu = 0) est servi pendant 700 s ; aucune étiquette « Horaire théorique »
        r = releve_de(course("T1", [appel(A, 0, 60), appel(B, 3000, 3060)]), recu=0)
        l = Lisseur(tolerance_s=30.0, biais_s=0.0)
        for t_s in range(0, 701, 60):
            t = self.trains(r, t_s, l)
        self.assertFalse(t["horaire_theorique"])

    def test_pas_d_etiquette_sans_lisseur(self):
        r = releve_de(course("T1", [appel(A, 0, 60), appel(B, 3000, 3060)]))
        self.assertFalse(self.trains(r, 700)["horaire_theorique"])

    def test_les_deux_etiquettes_sont_independantes(self):
        l, t = Lisseur(tolerance_s=30.0, biais_s=0.0), None
        for recu in (0, 120, 240, 360, 480, 600, 700):             # 700 s de relevés distincts, heures PRIM inchangées
            r = Releve(T0 + timedelta(seconds=recu), releve_de(course("T1", [appel(A, 0, 60), appel(B, 3000, 3060)])).trains)
            t = self.trains(r, 1000 if recu == 700 else recu, l)
        self.assertTrue(t["donnees_anciennes"] and t["horaire_theorique"])   # course enregistrée à 0 s, vue à 1 000 s : anciennes ET horaire inchangé


class TestPlafond(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.addCleanup(self.dossier.cleanup)
        self.fichier = Path(self.dossier.name) / "plafond.json"
        self.instant = T0                      # 16/11/2026 11:00 à Paris

    def plafond(self, maximum=3):
        return Plafond(MagasinLocal(self.fichier), maximum, horloge=lambda: self.instant)

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

    def test_repart_a_minuit_a_paris(self):
        p = self.plafond(1)
        p.compter()
        self.assertFalse(p.autorise())
        self.instant = datetime(2026, 11, 16, 22, 59, 59, tzinfo=timezone.utc)   # 23:59:59 à Paris : même jour
        self.assertFalse(p.autorise())
        self.instant = datetime(2026, 11, 16, 23, 0, 0, tzinfo=timezone.utc)     # 00:00 à Paris le 17 : nouveau jour
        self.assertTrue(p.autorise())
        self.assertEqual(p.utilises, 0)

    def test_jour_de_paris_et_non_utc(self):
        # 14/07/2026 22:30 UTC = 15/07 00:30 à Paris (été) : le compteur doit repartir de zéro, alors que l'UTC est encore le 14
        self.assertEqual(jour_paris(datetime(2026, 7, 14, 22, 30, tzinfo=timezone.utc)), date(2026, 7, 15))
        self.instant = datetime(2026, 7, 14, 21, 0, tzinfo=timezone.utc)         # 23:00 à Paris le 14
        p = self.plafond(5)
        p.compter()
        p.compter()
        self.instant = datetime(2026, 7, 14, 22, 30, tzinfo=timezone.utc)
        self.assertEqual(p.utilises, 0)
        p.compter()
        self.assertEqual(p.utilises, 1)

    def test_fichier_illisible_repart_de_zero(self):
        self.fichier.write_text("pas du json")
        self.assertEqual(self.plafond(3).utilises, 0)

    def test_ancien_format_du_fichier_est_repris(self):
        # INVARIANT (magasin.py) : le fichier d'avant T2 `{"jour", "n"}` garde ses appels du jour, sinon le quota serait sous-estimé
        self.fichier.write_text(json.dumps({"jour": "2026-11-16", "n": 7}))
        self.assertEqual(self.plafond(1300).utilises, 7)
        self.instant = datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc)
        self.assertEqual(self.plafond(1300).utilises, 0)


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


class TestAppelerPrim(unittest.TestCase):
    """appeler_prim contre un faux serveur LOCAL : la clé ne doit jamais suivre une redirection."""

    def serveur(self, comportement):
        vus = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                vus.append((self.path, self.headers.get("apikey")))
                comportement(self)

            def log_message(self, *a):
                pass
        s = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        self.addCleanup(s.server_close)
        self.addCleanup(s.shutdown)
        return f"http://127.0.0.1:{s.server_address[1]}", vus

    def test_redirection_non_suivie(self):
        def h(req):
            req.send_response(302)
            req.send_header("Location", "/ailleurs")
            req.end_headers()
        base, vus = self.serveur(h)
        with self.assertRaises(ErreurPrim) as cm:
            appeler_prim("CLE-DE-TEST", base + "/depart")
        self.assertEqual(cm.exception.statut, 302)
        self.assertEqual([p for p, _ in vus], ["/depart"])        # /ailleurs n'a jamais reçu la clé
        self.assertNotIn("CLE-DE-TEST", str(cm.exception))

    def test_reponse_trop_grande_refusee(self):
        import serveur
        ancien = serveur.TAILLE_MAX_REPONSE
        serveur.TAILLE_MAX_REPONSE = 100
        self.addCleanup(setattr, serveur, "TAILLE_MAX_REPONSE", ancien)

        def h(req):
            corps = b'{"Siri":{"ServiceDelivery":{"x":"' + b"a" * 500 + b'"}}}'
            req.send_response(200)
            req.send_header("Content-Length", str(len(corps)))
            req.end_headers()
            req.wfile.write(corps)
        base, _ = self.serveur(h)
        with self.assertRaises(ErreurPrim) as cm:
            appeler_prim("CLE-DE-TEST", base + "/x")
        self.assertIn("trop grande", str(cm.exception))

    def test_reponse_normale(self):
        def h(req):
            corps = b'{"Siri":{"ServiceDelivery":{"ok":1}}}'
            req.send_response(200)
            req.send_header("Content-Length", str(len(corps)))
            req.end_headers()
            req.wfile.write(corps)
        base, vus = self.serveur(h)
        self.assertEqual(appeler_prim("CLE-DE-TEST", base + "/x"), {"ok": 1})
        self.assertEqual(vus[0][1], "CLE-DE-TEST")


class TestReleveur(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.addCleanup(self.dossier.cleanup)

    def releveur(self, fausse, maximum=PLAFOND_JOUR, intervalle=INTERVALLE_S, fenetre_nuit=FENETRE_NUIT):
        plafond = Plafond(MagasinFaux(), maximum, horloge=lambda: T0)
        return Releveur(fausse, plafond, intervalle_s=intervalle, fenetre_nuit=fenetre_nuit), plafond

    def liv(self, ref="T1"):
        return livraison(course(ref, [appel(A, 0, 60), appel(B, 300, 360)]))

    def test_exceptions_inattendues_donnent_une_erreur_pas_un_plantage(self):
        import http.client
        for exc in (TypeError("x"), AttributeError("x"), http.client.IncompleteRead(b"")):
            r, p = self.releveur(Fausse([exc]))
            self.assertEqual(r.actualiser(T0), "erreur", repr(exc))
            self.assertIsNotNone(r.derniere_erreur)
            self.assertIsNotNone(p.magasin.prendre_verrou(NOM_VERROU, VERROU_S))   # le verrou est bien relâché

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
        self.assertEqual(r.actualiser(T0 + timedelta(seconds=INTERVALLE_S - 1)), "trop tôt")
        self.assertEqual(f.appels, 1)
        self.assertEqual(r.actualiser(T0 + timedelta(seconds=INTERVALLE_S)), "ok")
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


def heure_paris(annee, mois, jour, h, mi=0, s=0):
    """Instant UTC d'une heure civile de Paris, converti par zoneinfo (jamais calculé à la main)."""
    return datetime(annee, mois, jour, h, mi, s, tzinfo=PARIS).astimezone(timezone.utc)


def releveur_de_test(fausse, maximum=PLAFOND_JOUR, intervalle=INTERVALLE_S, fenetre_nuit=FENETRE_NUIT):
    plafond = Plafond(MagasinFaux(), maximum, horloge=lambda: T0)
    return Releveur(fausse, plafond, intervalle_s=intervalle, fenetre_nuit=fenetre_nuit), plafond


class TestNuitIntervallePlafond(unittest.TestCase):
    """T2 : coupure 01:00-05:00 (Paris), intervalle 60 s, plafond 1 300 par jour (date de Paris)."""

    def test_constantes_de_production(self):
        self.assertEqual((INTERVALLE_S, PLAFOND_JOUR, NUIT_DEBUT_H, NUIT_FIN_H, DELAI_PRIM_S, VERROU_S), (60, 1300, 1, 5, 20, 35))

    def test_le_verrou_survit_au_delai_prim(self):
        # INVARIANT (serveur.py) : sinon deux appels PRIM partent en parallèle et le plafond est faux
        self.assertGreater(VERROU_S, DELAI_PRIM_S + 10)
        m = MagasinFaux()
        m.prendre_verrou(NOM_VERROU, VERROU_S)
        m.avancer(DELAI_PRIM_S)
        self.assertIsNone(m.prendre_verrou(NOM_VERROU, VERROU_S))            # pendant l'appel le plus long : toujours pris
        m.avancer(VERROU_S - DELAI_PRIM_S)
        self.assertIsNotNone(m.prendre_verrou(NOM_VERROU, VERROU_S))         # expiré : repris

    def test_bornes_de_la_nuit_a_paris(self):
        f = Fausse([livraison(course("T1", [appel(A, 0, 60), appel(B, 300, 360)]))] * 2)
        r, _ = releveur_de_test(f)
        self.assertEqual(r.actualiser(heure_paris(2026, 11, 17, 0, 59, 59)), "ok")
        self.assertEqual(r.actualiser(heure_paris(2026, 11, 17, 1, 0, 0)), "nuit")
        self.assertEqual(r.actualiser(heure_paris(2026, 11, 17, 4, 59, 59)), "nuit")
        self.assertEqual(r.actualiser(heure_paris(2026, 11, 17, 5, 0, 0)), "ok")
        self.assertEqual(f.appels, 2)

    def test_changements_d_heure(self):
        r, _ = releveur_de_test(Fausse([]))
        cas = (
            (datetime(2026, 10, 24, 23, 0, tzinfo=timezone.utc), 1, True),       # 25/10 01:00 CEST : coupure
            (datetime(2026, 10, 24, 22, 59, 59, tzinfo=timezone.utc), 0, False),  # 25/10 00:59:59 CEST
            (datetime(2026, 10, 25, 3, 59, 59, tzinfo=timezone.utc), 4, True),    # 25/10 04:59:59 CET (heure d'hiver)
            (datetime(2026, 10, 25, 4, 0, tzinfo=timezone.utc), 5, False),        # 25/10 05:00 CET
            (datetime(2027, 3, 28, 0, 0, tzinfo=timezone.utc), 1, True),          # 28/03 01:00 CET, avant le passage à 02:00
            (datetime(2027, 3, 27, 23, 59, 59, tzinfo=timezone.utc), 0, False),   # 28/03 00:59:59 CET
            (datetime(2027, 3, 28, 2, 59, 59, tzinfo=timezone.utc), 4, True),     # 28/03 04:59:59 CEST
            (datetime(2027, 3, 28, 3, 0, tzinfo=timezone.utc), 5, False),         # 28/03 05:00 CEST
        )
        for instant, heure, attendu in cas:
            with self.subTest(instant=instant.isoformat()):
                self.assertEqual(instant.astimezone(PARIS).hour, heure)   # garde-fou sur les calculs d'instants
                self.assertEqual(r.est_nuit(instant), attendu)

    def test_nuit_garde_le_releve_sans_appel_ni_comptage(self):
        f = Fausse([livraison(course("T1", [appel(A, 0, 60), appel(B, 300, 360)]))] * 2)
        r, p = releveur_de_test(f)
        veille = heure_paris(2026, 11, 16, 23, 0)
        self.assertEqual(r.actualiser(veille), "ok")
        nuit = heure_paris(2026, 11, 17, 2, 0)
        self.assertEqual(r.actualiser(nuit), "nuit")
        self.assertEqual(f.appels, 1)
        self.assertEqual(p.compte(nuit), 0)                  # le compteur du 17 n'a pas bougé
        self.assertEqual(r.releve.recu, veille)              # le relevé de la veille reste servi, son âge croît
        self.assertEqual(r.actualiser(heure_paris(2026, 11, 17, 5, 0)), "ok")

    def test_demo_sans_coupure(self):
        r, _ = releveur_de_test(Fausse([livraison(course("T1", [appel(A, 0, 60), appel(B, 300, 360)]))]), fenetre_nuit=None)
        self.assertEqual(r.actualiser(heure_paris(2026, 11, 17, 2, 0)), "ok")

    def test_intervalle_de_60_secondes(self):
        f = Fausse([livraison(course("T1", [appel(A, 0, 60), appel(B, 300, 360)]))] * 2)
        r, _ = releveur_de_test(f, fenetre_nuit=None)
        self.assertEqual(r.actualiser(T0), "ok")
        self.assertEqual(r.actualiser(T0 + timedelta(seconds=59)), "trop tôt")
        self.assertEqual(r.actualiser(T0 + timedelta(seconds=60)), "ok")

    def test_plafond_de_1300_appels_par_jour(self):
        # 00:00 à Paris : 1 301 tentatives à 60 s d'écart tiennent dans le même jour civil de Paris
        debut = heure_paris(2026, 11, 16, 0, 0)
        f = Fausse([livraison(course("T1", [appel(A, 0, 60), appel(B, 300, 360)])) for _ in range(PLAFOND_JOUR + 1)])
        r, p = releveur_de_test(f, fenetre_nuit=None)
        for n in range(PLAFOND_JOUR):
            self.assertEqual(r.actualiser(debut + timedelta(seconds=INTERVALLE_S * n)), "ok", n)
        self.assertEqual(r.actualiser(debut + timedelta(seconds=INTERVALLE_S * PLAFOND_JOUR)), "plafond")
        self.assertEqual(f.appels, PLAFOND_JOUR)
        self.assertEqual(p.compte(debut), PLAFOND_JOUR)

    def test_service_nuit_dans_trains_et_statut(self):
        f = Fausse([livraison(course("T1", [appel(A, 0, 60), appel(B, 300, 360)]))])
        r, _ = releveur_de_test(f)
        instant = {"t": heure_paris(2026, 11, 17, 0, 30)}
        serveur = creer_serveur(r, distance, localiser, nom, port=0, horloge=lambda: instant["t"])
        threading.Thread(target=serveur.serve_forever, daemon=True).start()
        self.addCleanup(serveur.server_close)
        self.addCleanup(serveur.shutdown)
        base = f"http://127.0.0.1:{serveur.server_address[1]}"
        de_jour = json.loads(urllib.request.urlopen(base + "/trains").read())
        self.assertIsNone(de_jour["service"])
        self.assertEqual(len(de_jour["trains"]), 1)
        instant["t"] = heure_paris(2026, 11, 17, 2, 0)
        de_nuit = json.loads(urllib.request.urlopen(base + "/trains").read())
        self.assertEqual(de_nuit["service"], "nuit")
        self.assertEqual(len(de_nuit["trains"]), 1)          # cache gardé pendant la coupure
        statut = json.loads(urllib.request.urlopen(base + "/statut").read())
        self.assertEqual(statut["service"], "nuit")
        self.assertNotIn("trains", statut)
        self.assertEqual(f.appels, 1)


class TestMagasinDansLeReleveur(unittest.TestCase):
    """T2 : l'état partagé (verrou, blocage, compteur) passe par le magasin ; une panne = aucun appel."""

    def test_panne_du_magasin_aucun_appel_releve_garde(self):
        f = Fausse([livraison(course("T1", [appel(A, 0, 60), appel(B, 300, 360)]))] * 2)
        r, p = releveur_de_test(f, fenetre_nuit=None)
        self.assertEqual(r.actualiser(T0), "ok")
        p.magasin.panne = True
        self.assertEqual(r.actualiser(T0 + timedelta(seconds=INTERVALLE_S)), "magasin")
        self.assertEqual(f.appels, 1)
        self.assertEqual(r.releve.recu, T0)
        self.assertIn("magasin", r.derniere_erreur)
        p.magasin.panne = False
        self.assertEqual(p.compte(T0), 1)                    # rien n'a été compté pendant la panne

    def test_blocage_ecrit_par_une_copie_lu_par_l_autre(self):
        magasin = MagasinFaux()
        plafond = Plafond(magasin, PLAFOND_JOUR, horloge=lambda: T0)
        f1, f2 = Fausse([ErreurPrim("HTTP 401", 401)]), Fausse([livraison(course("T1", [appel(A, 0, 60), appel(B, 300, 360)]))])
        copie1 = Releveur(f1, plafond, fenetre_nuit=None)
        copie2 = Releveur(f2, plafond, fenetre_nuit=None)
        self.assertEqual(copie1.actualiser(T0), "erreur")
        self.assertEqual(copie2.actualiser(T0 + timedelta(hours=3)), "bloqué")
        self.assertTrue(copie2.bloque)
        self.assertEqual(f2.appels, 0)
        self.assertIsNotNone(magasin.lire(CLE_BLOCAGE))

    def test_deux_copies_un_seul_appel_pour_deux_requetes_simultanees(self):
        magasin = MagasinFaux()
        plafond = Plafond(magasin, PLAFOND_JOUR, horloge=lambda: T0)
        appels, resultats = [], []

        def fetch_copie1():
            appels.append("copie1")
            resultats.append(copie2.actualiser(T0))         # la requête de l'autre copie arrive pendant l'appel
            return livraison(course("T1", [appel(A, 0, 60), appel(B, 300, 360)]))

        def fetch_copie2():
            appels.append("copie2")
            return livraison(course("T1", [appel(A, 0, 60), appel(B, 300, 360)]))
        copie1 = Releveur(fetch_copie1, plafond, fenetre_nuit=None)
        copie2 = Releveur(fetch_copie2, plafond, fenetre_nuit=None)
        self.assertEqual(copie1.actualiser(T0), "ok")
        self.assertEqual(resultats, ["occupé"])
        self.assertEqual(appels, ["copie1"])

    def test_magasin_en_panne_sur_le_blocage_ne_plante_pas_le_serveur(self):
        r, p = releveur_de_test(Fausse([]), fenetre_nuit=None)
        p.magasin.panne = True
        self.assertEqual(r.actualiser(T0), "magasin")
        self.assertFalse(r.bloque)


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
        plafond = Plafond(MagasinLocal(Path(self.dossier.name) / "p.json"), PLAFOND_JOUR, horloge=lambda: T0)
        self.fausse = f
        self.releveur = Releveur(f, plafond, intervalle_s=INTERVALLE_S)
        self.horloge = lambda: T0 + timedelta(seconds=180)
        self.web = Path(self.dossier.name) / "web"
        (self.web / "vendor").mkdir(parents=True)
        (self.web / "index.html").write_text("<html>carte</html>", encoding="utf-8")
        (self.web / "vendor" / "lib.js").write_text("var a=1;", encoding="utf-8")
        (self.web / "vendor" / "police.woff2").write_bytes(b"wOF2")
        (self.web / ".secret").write_text("non", encoding="utf-8")
        (self.web / "note.exe").write_text("non", encoding="utf-8")
        (Path(self.dossier.name) / "dehors.txt").write_text("hors racine", encoding="utf-8")
        self.geo = {"trace": [[[2.0, 48.0], [2.1, 48.1]]], "gares": [{"nom": "Gare A", "lon": 2.0, "lat": 48.0}]}
        self.appels_gare = []

        def fetch_gare(gare):
            self.appels_gare.append(gare)
            return {"StopMonitoringDelivery": [{"MonitoredStopVisit": [{"MonitoredVehicleJourney": {
                "LineRef": {"value": "STIF:Line::C01743:"}, "FramedVehicleJourneyRef": {"DatedVehicleJourneyRef": "T1"},
                "DestinationName": [{"value": "Aéroport CDG"}], "VehicleJourneyName": [{"value": "EAGE09"}],
                "MonitoredCall": {"VehicleAtStop": False, "ExpectedDepartureTime": h(400), "AimedDepartureTime": h(300)}}}]}]}
        self.gares = ReleveurGares(fetch_gare, Plafond(MagasinLocal(Path(self.dossier.name) / "pg.json"), 100, horloge=lambda: T0))
        self.serveur = creer_serveur(self.releveur, distance, localiser, nom, port=0, horloge=self.horloge,
                                     racine_web=self.web, geo=self.geo, releveur_gares=self.gares,
                                     gares={"58774": "Massy - Palaiseau", "47940": "Massy - Verrières"})
        threading.Thread(target=self.serveur.serve_forever, daemon=True).start()
        self.addCleanup(self.serveur.server_close)
        self.addCleanup(self.serveur.shutdown)
        self.base = f"http://127.0.0.1:{self.serveur.server_address[1]}"

    def test_sans_visiteur_aucun_appel_prim(self):
        # chargement à la demande : le serveur lancé, personne sur la page = zéro appel PRIM (ni trains ni gares)
        self.assertEqual(self.fausse.appels, 0)
        self.assertEqual(self.appels_gare, [])
        self.assertEqual(self.releveur.plafond.utilises, 0)

    def test_ecoute_localhost_seulement(self):
        self.assertEqual(self.serveur.server_address[0], "127.0.0.1")

    def test_trains_json(self):
        with urllib.request.urlopen(self.base + "/trains") as r:
            corps = json.loads(r.read())
            self.assertEqual(r.headers["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual([t["course"] for t in corps["trains"]], ["T1"])
        # cache vide : la requête déclenche l'appel, au « maintenant » de l'horloge (T0 + 180 s)
        self.assertEqual(corps["releve_recu"], (T0 + timedelta(seconds=180)).isoformat())
        self.assertEqual(corps["plafond"], {"utilises": 1, "maximum": PLAFOND_JOUR})
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

    def test_accueil_sert_index(self):
        with urllib.request.urlopen(self.base + "/") as r:
            self.assertEqual(r.read(), b"<html>carte</html>")
            self.assertTrue(r.headers["Content-Type"].startswith("text/html"))

    def test_fichier_statique_et_type(self):
        with urllib.request.urlopen(self.base + "/vendor/lib.js") as r:
            self.assertEqual(r.read(), b"var a=1;")
            self.assertTrue(r.headers["Content-Type"].startswith("text/javascript"))

    def test_police_woff2_servie_avec_son_type(self):
        with urllib.request.urlopen(self.base + "/vendor/police.woff2") as r:
            self.assertEqual(r.read(), b"wOF2")
            self.assertEqual(r.headers["Content-Type"], "font/woff2")

    # --- fichiers statiques : revalidation (ETag), compression, HEAD (analyse de fluidité du 21/11)
    def _requete(self, chemin, methode="GET", **en_tetes):
        req = urllib.request.Request(self.base + chemin, method=methode, headers=en_tetes)
        try:
            return urllib.request.urlopen(req)
        except urllib.error.HTTPError as e:
            return e

    def test_statique_revalidable_avec_etag_et_304(self):
        with self._requete("/vendor/lib.js") as r:
            etag = r.headers["ETag"]
            self.assertEqual(r.headers["Cache-Control"], "no-cache", "toujours revalidé (le code change), mais 304 sans corps si identique")
        self.assertTrue(etag)
        r = self._requete("/vendor/lib.js", **{"If-None-Match": etag})
        self.assertEqual(r.code, 304)
        self.assertEqual(r.read(), b"")
        r = self._requete("/vendor/lib.js", **{"If-None-Match": '"autre"'})
        self.assertEqual(r.code, 200)

    def test_etag_change_quand_le_fichier_change(self):
        with self._requete("/vendor/lib.js") as r:
            avant = r.headers["ETag"]
        (self.web / "vendor" / "lib.js").write_text("var a=2222;", encoding="utf-8")
        with self._requete("/vendor/lib.js") as r:
            self.assertNotEqual(r.headers["ETag"], avant)

    def test_donnees_dynamiques_restent_sans_cache(self):
        for route in ("/trains", "/geo"):
            with self._requete(route) as r:
                self.assertEqual(r.headers["Cache-Control"], "no-store", route)
                self.assertIsNone(r.headers["ETag"])

    def test_gros_fichier_texte_compresse_en_gzip_si_demande(self):
        import gzip
        gros = json.dumps({"batiments": [[2.1, 48.7]] * 2000})
        (self.web / "vendor" / "gros.json").write_text(gros, encoding="utf-8")
        with self._requete("/vendor/gros.json", **{"Accept-Encoding": "gzip"}) as r:
            brut = r.read()
            self.assertEqual(r.headers["Content-Encoding"], "gzip")
            self.assertEqual(r.headers["Vary"], "Accept-Encoding")
            self.assertEqual(r.headers["Content-Length"], str(len(brut)))
        self.assertEqual(gzip.decompress(brut).decode("utf-8"), gros)
        self.assertLess(len(brut), len(gros) / 3)

    def test_pas_de_gzip_sans_demande_ni_pour_les_petits_fichiers_ni_les_polices(self):
        gros = json.dumps({"batiments": [[2.1, 48.7]] * 2000})
        (self.web / "vendor" / "gros.json").write_text(gros, encoding="utf-8")
        (self.web / "vendor" / "grosse.woff2").write_bytes(b"x" * 5000)
        with self._requete("/vendor/gros.json", **{"Accept-Encoding": "identity"}) as r:
            self.assertIsNone(r.headers["Content-Encoding"])
            self.assertEqual(r.read().decode("utf-8"), gros)
        with self._requete("/vendor/lib.js", **{"Accept-Encoding": "gzip"}) as r:
            self.assertIsNone(r.headers["Content-Encoding"])
        with self._requete("/vendor/grosse.woff2", **{"Accept-Encoding": "gzip"}) as r:
            self.assertIsNone(r.headers["Content-Encoding"], "woff2 est déjà compressé")

    def test_etag_distinct_selon_la_compression(self):
        gros = json.dumps({"batiments": [[2.1, 48.7]] * 2000})
        (self.web / "vendor" / "gros.json").write_text(gros, encoding="utf-8")
        with self._requete("/vendor/gros.json", **{"Accept-Encoding": "gzip"}) as r:
            e1 = r.headers["ETag"]
        with self._requete("/vendor/gros.json") as r:
            e2 = r.headers["ETag"]
        self.assertNotEqual(e1, e2, "un cache intermédiaire ne doit pas mélanger les deux versions")

    def test_head_repond_comme_get_sans_corps(self):
        with self._requete("/vendor/lib.js", "HEAD") as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(r.headers["Content-Length"], "8")
            self.assertEqual(r.read(), b"")
        self.assertEqual(self._requete("/secret", "HEAD").code, 404)

    def test_statique_refuse_sortie_de_racine_dotfile_et_type_inconnu(self):
        import http.client
        for chemin in ("/../dehors.txt", "/%2e%2e/dehors.txt", "/vendor/../../dehors.txt", "/.secret", "/note.exe",
                       "/vendor", "/vendor/", "/%00", "/vendor/lib.js%00.png"):
            c = http.client.HTTPConnection("127.0.0.1", self.serveur.server_address[1])
            c.request("GET", chemin)          # chemin brut, sans normalisation par urllib
            reponse = c.getresponse()
            corps = reponse.read()
            self.assertEqual(reponse.status, 404, chemin)
            self.assertNotIn(b"hors racine", corps, chemin)
            c.close()

    def test_geo(self):
        with urllib.request.urlopen(self.base + "/geo") as r:
            self.assertEqual(json.loads(r.read()), self.geo)

    def test_csp_bloque_tout_reseau_externe_sur_chaque_reponse(self):
        for chemin in ("/", "/vendor/lib.js", "/trains", "/geo"):
            with urllib.request.urlopen(self.base + chemin) as r:
                csp = r.headers["Content-Security-Policy"]
            for directive in ("default-src 'none'", "script-src 'self'", "connect-src 'self'", "img-src 'self' data: blob:"):
                self.assertIn(directive, csp, chemin)
            self.assertNotIn("http", csp)
            self.assertNotIn("*", csp)

    def test_en_tetes_de_securite(self):
        with urllib.request.urlopen(self.base + "/") as r:
            self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(r.headers["Referrer-Policy"], "no-referrer")

    def test_demo_faux_par_defaut_et_annonce_vrai_en_demo(self):
        with urllib.request.urlopen(self.base + "/statut") as r:
            self.assertIs(json.loads(r.read())["demo"], False)
        demo = creer_serveur(self.releveur, distance, localiser, nom, port=0, horloge=self.horloge,
                             racine_web=self.web, geo=self.geo, demo=True)
        threading.Thread(target=demo.serve_forever, daemon=True).start()
        self.addCleanup(demo.server_close)
        self.addCleanup(demo.shutdown)
        with urllib.request.urlopen(f"http://127.0.0.1:{demo.server_address[1]}/statut") as r:
            self.assertIs(json.loads(r.read())["demo"], True)

    def test_gare_prochains_trains(self):
        with urllib.request.urlopen(self.base + "/gare?ref=58774") as r:
            corps = json.loads(r.read())
        self.assertEqual((corps["ref"], corps["gare"], corps["statut"]), ("58774", "Massy - Palaiseau", "ok"))
        self.assertEqual([p["course"] for p in corps["passages"]], ["T1"])
        self.assertEqual(corps["passages"][0]["retard_s"], 100.0)
        self.assertEqual(corps["plafond"], {"utilises": 1, "maximum": 100})
        self.assertIs(corps["demo"], False)
        self.assertEqual(self.appels_gare, ["STIF:StopArea:SP:58774:"])

    def test_gare_cache_pas_de_second_appel(self):
        for _ in range(3):
            urllib.request.urlopen(self.base + "/gare?ref=58774").read()
        self.assertEqual(len(self.appels_gare), 1)

    def test_gare_refus_des_references_non_listees(self):
        import http.client
        for chemin in ("/gare", "/gare?ref=", "/gare?ref=999", "/gare?ref=58774%26x%3D1", "/gare?ref=58774&ref=47940",
                       "/gare?ref=../etc", "/gare?ref=58774%00", "/gare?autre=58774", "/gare?ref=%20 58774"):
            c = http.client.HTTPConnection("127.0.0.1", self.serveur.server_address[1])
            c.request("GET", chemin.replace(" ", "%20"))
            reponse = c.getresponse()
            reponse.read()
            self.assertEqual(reponse.status, 404, chemin)
            c.close()
        self.assertEqual(self.appels_gare, [])     # aucune de ces requêtes ne doit provoquer d'appel PRIM

    def test_gare_sans_releveur_404(self):
        s = creer_serveur(self.releveur, distance, localiser, nom, port=0, horloge=self.horloge, racine_web=self.web)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        self.addCleanup(s.server_close)
        self.addCleanup(s.shutdown)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(f"http://127.0.0.1:{s.server_address[1]}/gare?ref=58774")
        self.assertEqual(cm.exception.code, 404)

    def test_methode_post_refusee(self):
        requete = urllib.request.Request(self.base + "/trains", data=b"x", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(requete)
        self.assertIn(cm.exception.code, (405, 501))


@unittest.skipUnless(TRACE.exists() and CSV_GARES.exists(), "fichiers statiques absents")
class TestContexteReel(unittest.TestCase):
    def test_geo_reel(self):
        geo = charger_geo()
        self.assertEqual(len(geo["gares"]), 47)
        self.assertGreater(len(geo["trace"]), 10)
        self.assertIn("58774", {g["ref"] for g in geo["gares"]})
        self.assertTrue(all(g["ref"].isdigit() for g in geo["gares"]))
        lon, lat = geo["trace"][0][0]
        self.assertTrue(2.0 < lon < 3.0 and 48.5 < lat < 49.1, (lon, lat))

    def test_chaine_demo_massy_gare_du_nord(self):
        chaine = chaine_demo()
        self.assertEqual(chaine[0], "STIF:StopArea:SP:58774:")
        self.assertGreater(len(chaine), 15)
        self.assertEqual(len(set(chaine)), len(chaine))

    def test_cap_en_route_et_a_quai_sur_donnees_reelles(self):
        distance_, localiser_, nom_ = construire_contexte()
        r = releve_de(course("T1", [appel(self.MASSY_PAL, 0, 60), appel(self.MASSY_VER, 300, 360)]))
        en_route = calculer_trains(r, T0 + timedelta(seconds=180), distance_, localiser_, nom_)[0]
        a_quai = calculer_trains(r, T0 + timedelta(seconds=30), distance_, localiser_, nom_)[0]
        for t in (en_route, a_quai):
            self.assertIsNotNone(t["cap"], t["motif"])
            self.assertTrue(0.0 <= t["cap"] < 360.0)
        # à quai, la rame est posée SUR la voie (pas au centre de la gare) : pas de saut quand elle démarre (gare dense : départ à 30 s)
        un_metre = calculer_trains(r, T0 + timedelta(seconds=30 + 270 / 300.0), distance_, localiser_, nom_)[0]
        ecart = math.hypot((un_metre["lon"] - a_quai["lon"]) * 74000, (un_metre["lat"] - a_quai["lat"]) * 111000)
        self.assertLess(ecart, 10.0, ecart)
        # Massy - Verrières est au nord-est de Massy - Palaiseau (+1 046 m vers le nord, +1 127 m vers l'est : cap direct ~47°)
        self.assertTrue(20.0 < en_route["cap"] < 70.0, en_route["cap"])
        self.assertTrue(20.0 < a_quai["cap"] < 70.0, a_quai["cap"])

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


class TestContinuiteAuxGares(unittest.TestCase):
    """Aucune téléportation aux gares (retour de Mathis, Châtelet) : la fin d'un tronçon = le quai de la gare = le début du suivant."""

    def setUp(self):
        self.distance, self.localiser, self.nom = construire_contexte()
        self.chaine = chaine_demo()

    @staticmethod
    def metres(a, b):
        return math.hypot((b[1] - a[1]) * 74000, (b[0] - a[0]) * 111000)

    def test_arrivee_quai_depart_sont_le_meme_point(self):
        pires = []
        for avant, gare, apres in zip(self.chaine, self.chaine[1:], self.chaine[2:]):
            arrivee = self.localiser(avant, gare, self.distance(avant, gare))[:2]
            quai = self.localiser(gare, apres, 0.0)[:2]
            depart = self.localiser(gare, apres, 1.0)[:2]
            pires.append((max(self.metres(arrivee, quai), self.metres(quai, depart)), self.nom(gare)))
        pire = max(pires)
        self.assertLess(pire[0], 5.0, f"saut de {pire[0]:.0f} m à {pire[1]}")

    def test_voie_autour_du_train_dans_le_sens_de_la_marche(self):
        """La carte pose chaque caisse sur ce tracé : il doit passer par la position du train, aller dans le sens de la marche, sans trou."""
        for a, b in zip(self.chaine[:6], self.chaine[1:7]):
            longueur = self.distance(a, b)
            for d in (0.0, 60.0, longueur / 2, longueur):
                voie = self.localiser.voie(a, b, d)
                lat, lon, cap = self.localiser(a, b, d)
                self.assertGreaterEqual(len(voie), 2)
                self.assertLess(min(self.metres((lat, lon), (y, x)) for x, y in voie), 8.0, "la position du train est sur le tracé")
                pas = [self.metres((q[1], q[0]), (p[1], p[0])) for p, q in zip(voie, voie[1:])]
                self.assertLessEqual(max(pas), 9.0, "un point tous les 8 m au plus : les courbes restent fines")
                self.assertLessEqual(sum(pas), 2 * DEMI_VOIE_M + 10)
            milieu = self.localiser.voie(a, b, longueur / 2)
            (x0, y0), (x1, y1) = milieu[0], milieu[-1]
            if self.metres((y0, x0), (y1, x1)) > 100:   # sens de la marche : le cap du train pointe du début vers la fin du tracé
                cap_voie = math.degrees(math.atan2((x1 - x0) * 74, (y1 - y0) * 111)) % 360
                self.assertLess(abs((cap_voie - self.localiser(a, b, longueur / 2)[2] + 180) % 360 - 180), 40)

    def test_voie_absente_au_terminus(self):
        self.assertIsNone(self.localiser.voie(self.chaine[-1], None, 0.0))

    def test_calculer_trains_envoie_la_voie(self):
        a, b = self.chaine[0], self.chaine[1]
        voie_a_envoyer = [[2.0, 48.0], [2.001, 48.0]]
        def loc(gare, suivante, distance_m):
            return (48.0, 2.0, 90.0)
        loc.voie = lambda gare, suivante, distance_m: voie_a_envoyer if suivante else None
        r = releve_de(course("T1", [appel(A, 0, 60), appel(B, 300, 360)]))
        en_route = calculer_trains(r, T0 + timedelta(seconds=150), distance, loc, nom)[0]
        self.assertEqual(en_route["voie"], voie_a_envoyer)
        terminus = calculer_trains(r, T0 + timedelta(seconds=330), distance, loc, nom)[0]
        self.assertIsNone(terminus["voie"], "pas de tronçon au terminus : la carte garde la dernière voie")
        sans = calculer_trains(r, T0 + timedelta(seconds=150), distance, lambda *x: (48.0, 2.0, 90.0), nom)[0]
        self.assertIsNone(sans["voie"], "localiser sans voie : la rame sera droite")

    def test_terminus_au_meme_point_que_l_arrivee(self):
        a, b = self.chaine[-2], self.chaine[-1]
        arrivee = self.localiser(a, b, self.distance(a, b))[:2]
        self.assertLess(self.metres(arrivee, self.localiser(b, None, 0.0)[:2]), 5.0)

    def test_pas_de_saut_superieur_a_la_vitesse_max_sur_toute_la_chaine(self):
        pas, pire = 3.0, 0.0
        for a, b in zip(self.chaine, self.chaine[1:]):
            longueur, d, precedent = self.distance(a, b), 0.0, None
            while d <= longueur:
                p = self.localiser(a, b, d)[:2]
                if precedent:
                    pire = max(pire, self.metres(precedent, p) - 14.7 * pas)   # le pas simulé avance de 14,7 m/s
                precedent, d = p, d + 14.7 * pas
        self.assertLess(pire, 10.0, f"écart de {pire:.0f} m au-delà du déplacement attendu")

    def test_ecart_a_la_voie_borne_sur_tous_les_troncons(self):
        # Revue 17/11 : avec le passage « le plus proche », le quai de Massy - Palaiseau était sur une autre voie : train jusqu'à 148 m des rails.
        # Le quai est maintenant le passage central (médoïde) : le train reste sur la voie des tronçons voisins.
        from geometry import _dist
        from troncon import chemin_troncon
        from serveur import _charger
        from stops import stop_area_number
        gares, geom, res = _charger(*[__import__("serveur").CSV_GARES, __import__("serveur").TRACE])
        pire, pire_chatelet = 0.0, 0.0
        for a, b in geom.neighbor_pairs():
            chemin = chemin_troncon(geom, res, a, b)
            ra, rb = f"STIF:StopArea:SP:{a}:", f"STIF:StopArea:SP:{b}:"
            for f in (0.0, 0.5, 1.0):
                lat, lon, _ = self.localiser(ra, rb, f * self.distance(ra, rb))
                ecart = 1e9
                for p, q in zip(chemin, chemin[1:]):
                    n = max(1, int(_dist(p, q) // 10))
                    ecart = min(ecart, *(_dist((lat, lon), (p[0] + (q[0] - p[0]) * k / n, p[1] + (q[1] - p[1]) * k / n)) for k in range(n + 1)))
                if "45102" in (a, b):   # Châtelet : le tracé des morceaux voisins s'arrête ~120 m avant la gare (défaut des données, pas du quai)
                    pire_chatelet = max(pire_chatelet, ecart)
                else:
                    pire = max(pire, ecart)
        self.assertLess(pire, 40.0, f"écart à la voie de {pire:.0f} m hors Châtelet")
        self.assertLess(pire_chatelet, 130.0, f"écart à la voie de {pire_chatelet:.0f} m vers Châtelet")

    def test_position_reste_pres_des_rails(self):
        # le décalage progressif ne doit pas écarter le train du tracé de plus de 150 m
        a, b = "STIF:StopArea:SP:58774:", "STIF:StopArea:SP:47940:"
        for f in (0.0, 0.25, 0.5, 0.75, 1.0):
            lat, lon, cap = self.localiser(a, b, f * self.distance(a, b))
            self.assertTrue(48.7 < lat < 48.75 and 2.2 < lon < 2.3)
            self.assertTrue(cap is None or 0 <= cap < 360)


if __name__ == "__main__":
    unittest.main()
