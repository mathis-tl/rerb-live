"""Tâche 4, J4 : panneau « prochains trains à une gare » (stop-monitoring). AUCUN réseau : réponses inventées.

Format de la réponse : celui observé dans les relevés de la collecte 2 (MonitoredStopVisit, VehicleAtStop,
ExpectedDepartureTime, DepartureStatus "cancelled"...). Le cache est de 60 s PAR GARE, avec un plafond d'appels commun.
"""
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from gare import ReleveurGares, lire_passages
from serveur import ErreurPrim, Plafond

T0 = datetime(2026, 11, 16, 10, 0, 0, tzinfo=timezone.utc)
LIGNE_B, LIGNE_C = "STIF:Line::C01743:", "STIF:Line::C01727:"


def h(s):
    return (T0 + timedelta(seconds=s)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def visite(ref, arrivee, depart, vise_dep=None, a_quai=False, ligne=LIGNE_B, destination="Aéroport CDG", statut=None,
           mission="EAGE09"):
    appel = {"VehicleAtStop": a_quai, "ExpectedArrivalTime": h(arrivee), "ExpectedDepartureTime": h(depart),
             "AimedArrivalTime": h(arrivee), "AimedDepartureTime": h(vise_dep if vise_dep is not None else depart)}
    if statut:
        appel["DepartureStatus"] = statut
    return {"MonitoredVehicleJourney": {
        "LineRef": {"value": ligne}, "FramedVehicleJourneyRef": {"DatedVehicleJourneyRef": ref},
        "DestinationName": [{"value": destination}], "VehicleJourneyName": [{"value": mission}], "MonitoredCall": appel}}


def reponse(*visites):
    return {"StopMonitoringDelivery": [{"MonitoredStopVisit": list(visites)}]}


class TestLirePassages(unittest.TestCase):
    def test_champs_et_retard(self):
        r = reponse(visite("T1", 290, 300, vise_dep=180, destination="Mitry - Claye"))
        p = lire_passages(r, T0)[0]
        self.assertEqual(p["course"], "T1")
        self.assertEqual(p["destination"], "Mitry - Claye")
        self.assertEqual(p["mission"], "EAGE09")
        self.assertEqual(p["heure_estimee"], (T0 + timedelta(seconds=300)).isoformat())
        self.assertEqual(p["heure_prevue"], (T0 + timedelta(seconds=180)).isoformat())
        self.assertEqual(p["retard_s"], 120.0)
        self.assertFalse(p["a_quai"])
        self.assertFalse(p["annule"])

    def test_tri_par_heure_et_limite(self):
        r = reponse(*[visite(f"T{i}", 100 + i * 60, 110 + i * 60) for i in (5, 1, 3, 2, 4, 6, 7, 8, 9, 10)])
        refs = [p["course"] for p in lire_passages(r, T0, limite=8)]
        self.assertEqual(refs, [f"T{i}" for i in (1, 2, 3, 4, 5, 6, 7, 8)])

    def test_garde_seulement_le_rer_b(self):
        r = reponse(visite("B1", 100, 110), visite("C1", 100, 110, ligne=LIGNE_C))
        self.assertEqual([p["course"] for p in lire_passages(r, T0)], ["B1"])

    def test_train_deja_parti_ecarte_sauf_a_quai(self):
        r = reponse(visite("PARTI", -600, -590), visite("QUAI", -20, 40, a_quai=True), visite("FUTUR", 200, 210))
        self.assertEqual([p["course"] for p in lire_passages(r, T0)], ["QUAI", "FUTUR"])

    def test_depart_vieux_de_moins_d_une_minute_encore_liste(self):
        r = reponse(visite("JUSTE", -70, -50))        # parti il y a 50 s : encore affiché (marge de 60 s)
        self.assertEqual(len(lire_passages(r, T0)), 1)

    def test_annule_signale_et_garde(self):
        r = reponse(visite("X", 100, 110, statut="cancelled"))
        p = lire_passages(r, T0)[0]
        self.assertTrue(p["annule"])

    def test_a_quai(self):
        p = lire_passages(reponse(visite("Q", -10, 50, a_quai=True)), T0)[0]
        self.assertTrue(p["a_quai"])

    def test_terminus_sans_depart_utilise_l_arrivee(self):
        v = visite("TERM", 100, 110)
        del v["MonitoredVehicleJourney"]["MonitoredCall"]["ExpectedDepartureTime"]
        del v["MonitoredVehicleJourney"]["MonitoredCall"]["AimedDepartureTime"]
        p = lire_passages(reponse(v), T0)[0]
        self.assertEqual(p["heure_estimee"], (T0 + timedelta(seconds=100)).isoformat())

    def test_sans_heure_prevue_retard_inconnu(self):
        v = visite("X", 100, 110)
        del v["MonitoredVehicleJourney"]["MonitoredCall"]["AimedDepartureTime"]
        del v["MonitoredVehicleJourney"]["MonitoredCall"]["AimedArrivalTime"]
        self.assertIsNone(lire_passages(reponse(v), T0)[0]["retard_s"])

    def test_visite_sans_heure_ignoree(self):
        v = visite("X", 100, 110)
        for k in ("ExpectedArrivalTime", "ExpectedDepartureTime"):
            del v["MonitoredVehicleJourney"]["MonitoredCall"][k]
        self.assertEqual(lire_passages(reponse(v), T0), [])

    def test_reponse_vide_ou_sans_visite(self):
        self.assertEqual(lire_passages({"StopMonitoringDelivery": [{}]}, T0), [])
        self.assertEqual(lire_passages({"StopMonitoringDelivery": [{"MonitoredStopVisit": None}]}, T0), [])

    def test_reponse_illisible_leve_une_erreur_claire(self):
        with self.assertRaises(ValueError):
            lire_passages({"pas": "siri"}, T0)


class Fausse:
    def __init__(self, reponses):
        self.reponses, self.appels = list(reponses), []

    def __call__(self, gare):
        self.appels.append(gare)
        r = self.reponses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class TestReleveurGares(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.addCleanup(self.dossier.cleanup)

    def releveur(self, fausse, maximum=5000, intervalle=60):
        plafond = Plafond(Path(self.dossier.name) / "p.json", maximum, lambda: date(2026, 11, 16))
        return ReleveurGares(fausse, plafond, intervalle_s=intervalle), plafond

    def ok(self, ref="T1"):
        return reponse(visite(ref, 100, 110))

    def test_premier_appel_et_cache_60_s(self):
        f = Fausse([self.ok("A"), self.ok("B")])
        r, _ = self.releveur(f)
        res = r.passages("58774", T0)
        self.assertEqual(res["statut"], "ok")
        self.assertEqual([p["course"] for p in res["passages"]], ["A"])
        res = r.passages("58774", T0 + timedelta(seconds=59))
        self.assertEqual(res["statut"], "cache")
        self.assertEqual(res["age_s"], 59)
        self.assertEqual(len(f.appels), 1)
        res = r.passages("58774", T0 + timedelta(seconds=61))
        self.assertEqual(res["statut"], "ok")
        self.assertEqual([p["course"] for p in res["passages"]], ["B"])
        self.assertEqual(len(f.appels), 2)

    def test_cache_separe_par_gare(self):
        f = Fausse([self.ok("A"), self.ok("B")])
        r, _ = self.releveur(f)
        r.passages("1", T0)
        r.passages("2", T0)
        self.assertEqual(f.appels, ["1", "2"])

    def test_le_cache_reste_valable_et_les_heures_se_recalculent(self):
        f = Fausse([reponse(visite("A", 100, 110), visite("B", 500, 510))])
        r, _ = self.releveur(f, intervalle=1000)     # cache valable 1 000 s : aucun nouvel appel
        r.passages("1", T0)
        res = r.passages("1", T0 + timedelta(seconds=300))     # A est parti depuis 190 s : retiré, B reste
        self.assertEqual([p["course"] for p in res["passages"]], ["B"])

    def test_plafond_atteint_garde_le_cache(self):
        f = Fausse([reponse(visite("A", 900, 910)), self.ok("B")])
        r, p = self.releveur(f, maximum=1)
        r.passages("1", T0)
        res = r.passages("1", T0 + timedelta(seconds=500))
        self.assertEqual(res["statut"], "plafond")
        self.assertEqual([x["course"] for x in res["passages"]], ["A"])   # vieux, mais avec son âge
        self.assertEqual(res["age_s"], 500)
        self.assertEqual(len(f.appels), 1)

    def test_plafond_atteint_sans_cache_liste_vide(self):
        r, _ = self.releveur(Fausse([]), maximum=0)
        res = r.passages("1", T0)
        self.assertEqual((res["statut"], res["passages"], res["age_s"]), ("plafond", [], None))

    def test_erreur_garde_le_dernier_releve_et_reessaie_apres_l_intervalle(self):
        f = Fausse([self.ok("A"), ErreurPrim("HTTP 502", 502), self.ok("B")])
        r, _ = self.releveur(f)
        r.passages("1", T0)
        res = r.passages("1", T0 + timedelta(seconds=70))
        self.assertEqual(res["statut"], "erreur")
        self.assertEqual(res["erreur"], "HTTP 502")
        self.assertEqual([p["course"] for p in res["passages"]], ["A"])
        self.assertEqual(r.passages("1", T0 + timedelta(seconds=100))["statut"], "cache")   # pas de rafale d'appels
        self.assertEqual(r.passages("1", T0 + timedelta(seconds=135))["statut"], "ok")

    def test_cle_refusee_bloque_toutes_les_gares(self):
        f = Fausse([ErreurPrim("HTTP 401", 401), self.ok()])
        r, _ = self.releveur(f)
        self.assertEqual(r.passages("1", T0)["statut"], "erreur")
        self.assertTrue(r.bloque)
        self.assertEqual(r.passages("2", T0 + timedelta(hours=1))["statut"], "bloqué")
        self.assertEqual(len(f.appels), 1)

    def test_exceptions_inattendues_ne_plantent_pas(self):
        import http.client
        for exc in (TypeError("x"), AttributeError("x"), http.client.IncompleteRead(b"")):
            r, _ = self.releveur(Fausse([exc]))
            res = r.passages("1", T0)
            self.assertEqual(res["statut"], "erreur", repr(exc))
            self.assertEqual(res["passages"], [])
            self.assertEqual(r.passages("1", T0 + timedelta(seconds=1))["statut"], "cache")   # pas de rafale d'appels

    def test_heure_sans_fuseau_ne_plante_pas(self):
        v = visite("X", 100, 110)
        v["MonitoredVehicleJourney"]["MonitoredCall"]["ExpectedDepartureTime"] = "2026-11-16T10:05:00"   # sans Z
        r, _ = self.releveur(Fausse([reponse(v)]))
        self.assertEqual(r.passages("1", T0)["statut"], "erreur")

    def test_reponse_illisible_est_une_erreur(self):
        r, _ = self.releveur(Fausse([{"pas": "siri"}]))
        res = r.passages("1", T0)
        self.assertEqual(res["statut"], "erreur")
        self.assertIn("illisible", res["erreur"])

    def test_un_seul_appel_a_la_fois_par_gare(self):
        interne = []

        def fetch(gare):
            interne.append(r.passages(gare, T0)["statut"])     # rappel pendant l'appel : refusé
            return self.ok()
        r, _ = self.releveur(fetch)
        self.assertEqual(r.passages("1", T0)["statut"], "ok")
        self.assertEqual(interne, ["occupé"])


if __name__ == "__main__":
    unittest.main()
