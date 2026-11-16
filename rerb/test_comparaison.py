"""Comparaison estimated-timetable (ET) / stop-monitoring par gare (SM) : fonctions d'analyse pures. Données inventées."""
import unittest
from datetime import datetime, timedelta, timezone

from comparaison import (age_s, changements, ecarts_et_sm, passages_et, passages_sm, quantile, segments_vitesse)

T0 = datetime(2026, 11, 16, 12, 0, 0, tzinfo=timezone.utc)
B, A = "STIF:Line::C01743:", "STIF:Line::C01742:"
G1, G2, G3 = "STIF:StopArea:SP:1:", "STIF:StopArea:SP:2:", "STIF:StopArea:SP:3:"


def z(s):
    return (T0 + timedelta(seconds=s)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def visite(course, gare, arr, dep, rec=0, ligne=B, quai=False):
    return {"RecordedAtTime": z(rec), "MonitoringRef": {"value": gare}, "MonitoredVehicleJourney": {
        "LineRef": {"value": ligne}, "FramedVehicleJourneyRef": {"DatedVehicleJourneyRef": course},
        "MonitoredCall": {"VehicleAtStop": quai, "ExpectedArrivalTime": z(arr), "ExpectedDepartureTime": z(dep)}}}


def sm(*visites):
    return {"StopMonitoringDelivery": [{"MonitoredStopVisit": list(visites)}]}


class TestPassagesSm(unittest.TestCase):
    def test_champs_et_filtre_ligne(self):
        p = passages_sm(sm(visite("T1", G1, 100, 130, rec=-20), visite("X", G1, 100, 130, ligne=A)))
        self.assertEqual(len(p), 1)
        self.assertEqual((p[0]["course"], p[0]["gare"], p[0]["recorded_at"]), ("T1", G1, T0 - timedelta(seconds=20)))
        self.assertEqual(p[0]["depart"], T0 + timedelta(seconds=130))

    def test_vide_ou_illisible(self):
        self.assertEqual(passages_sm({"StopMonitoringDelivery": [{}]}), [])
        self.assertEqual(passages_sm(None), [])
        self.assertEqual(passages_sm({"x": 1}), [])


class TestPassagesEt(unittest.TestCase):
    def test_depuis_une_livraison(self):
        from memoire import Memoire
        liv = {"EstimatedTimetableDelivery": [{"EstimatedJourneyVersionFrame": [{"EstimatedVehicleJourney": [
            {"DatedVehicleJourneyRef": "T1", "RecordedAtTime": z(-10), "EstimatedCalls": [
                {"StopPointRef": {"value": G1}, "ExpectedArrivalTime": z(100), "ExpectedDepartureTime": z(130)}]}]}]}]}
        p = passages_et(liv, Memoire())
        self.assertEqual((p[0]["course"], p[0]["gare"], p[0]["recorded_at"]), ("T1", G1, T0 - timedelta(seconds=10)))
        self.assertEqual(p[0]["arrivee"], T0 + timedelta(seconds=100))

    def test_illisible(self):
        from memoire import Memoire
        self.assertEqual(passages_et({"x": 1}, Memoire()), [])


def p(course, gare, arr, dep, rec=0):
    return {"course": course, "gare": gare, "arrivee": T0 + timedelta(seconds=arr), "depart": T0 + timedelta(seconds=dep),
            "recorded_at": T0 + timedelta(seconds=rec)}


class TestSegments(unittest.TestCase):
    def test_vitesse_et_marquage(self):
        passages = [p("T1", G1, 0, 30), p("T1", G2, 100, 130), p("T1", G3, 140, 170)]
        n, mauvais, vit = segments_vitesse(passages, lambda a, b: 1000.0, vmax=33.0)
        self.assertEqual(n, 2)
        self.assertEqual([round(v, 1) for v in vit], [round(1000 / 70, 1), 100.0])   # 130->140 s : 1000 m en 10 s
        self.assertEqual(len(mauvais), 1)
        self.assertEqual((mauvais[0]["de"], mauvais[0]["vers"]), (G2, G3))

    def test_courses_separees_et_dt_nul_ecarte(self):
        passages = [p("T1", G1, 0, 30), p("T2", G2, 0, 30), p("T1", G2, 30, 40)]
        n, _, _ = segments_vitesse(passages, lambda a, b: 1000.0)
        self.assertEqual(n, 0)               # T1 : dt = 0 (départ 30 -> arrivée 30) écarté ; T2 : un seul passage

    def test_distance_inconnue_ecartee(self):
        def d(a, b):
            raise KeyError(a)
        n, _, _ = segments_vitesse([p("T1", G1, 0, 30), p("T1", G2, 100, 130)], d)
        self.assertEqual(n, 0)


class TestEcarts(unittest.TestCase):
    def test_ecart_signe_sm_moins_et(self):
        et = [p("T1", G1, 100, 130), p("T2", G1, 200, 230)]
        smp = [p("T1", G1, 110, 150), p("T3", G1, 0, 0)]
        self.assertEqual(ecarts_et_sm(et, smp), [20.0])          # seul T1/G1 est commun : 150 - 130

    def test_depart_absent_utilise_arrivee(self):
        a = dict(p("T1", G1, 100, 130), depart=None)
        b = dict(p("T1", G1, 110, 150), depart=None)
        self.assertEqual(ecarts_et_sm([a], [b]), [10.0])


class TestAgeEtChangements(unittest.TestCase):
    def test_age(self):
        self.assertEqual(age_s(T0 + timedelta(seconds=90), T0 + timedelta(seconds=30)), 60.0)
        self.assertIsNone(age_s(T0, None))

    def test_changements_entre_tours(self):
        tours = [[p("T1", G1, 100, 130), p("T2", G1, 300, 330)],
                 [p("T1", G1, 100, 130), p("T2", G1, 300, 345)],      # T2 change
                 [p("T1", G1, 100, 131), p("T2", G1, 300, 345)]]      # T1 change de 1 s : ignoré (seuil 2 s)
        suivis, changes = changements(tours, seuil_s=2.0)
        self.assertEqual((suivis, changes), (4, 1))

    def test_quantile(self):
        self.assertEqual(quantile([1, 2, 3, 4, 5], 0.5), 3)
        self.assertEqual(quantile([1, 2, 3, 4, 5], 1.0), 5)
        self.assertIsNone(quantile([], 0.5))


if __name__ == "__main__":
    unittest.main()
