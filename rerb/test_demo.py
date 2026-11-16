"""Tâche 4, J4 : source de DÉMONSTRATION (trains inventés qui roulent sur le vrai tracé). Aucun réseau, aucune clé.

Sert à voir la carte sans appeler PRIM. Les données sont fausses par construction : le serveur l'annonce (`demo`).
"""
import unittest
from datetime import datetime, timedelta, timezone

from demo import livraison_demo, stop_monitoring_demo
from gare import lire_passages
from memoire import Memoire
from position import Etat, estimer
from serveur import consolider_livraison

T = datetime(2026, 11, 16, 12, 0, 0, tzinfo=timezone.utc)
CHAINE = [f"STIF:StopArea:SP:{n}:" for n in (1, 2, 3, 4, 5)]


def distance(a, b):
    return 1500.0 * abs(CHAINE.index(a) - CHAINE.index(b))


class TestDemo(unittest.TestCase):
    def trains(self, maintenant=T, n=8):
        return consolider_livraison(livraison_demo(maintenant, CHAINE, distance, nombre=n), Memoire())

    def test_un_train_par_course_references_uniques(self):
        liv = livraison_demo(T, CHAINE, distance, nombre=8)
        refs = [ej["DatedVehicleJourneyRef"] for ej in liv["EstimatedTimetableDelivery"][0]["EstimatedJourneyVersionFrame"][0]["EstimatedVehicleJourney"]]
        self.assertEqual(len(refs), 8)
        self.assertEqual(len(set(refs)), 8)

    def test_toutes_les_gares_de_la_chaine_dans_chaque_train_normal(self):
        for ref, (train, _) in self.trains().items():
            if "ANNULE" in ref:
                continue
            self.assertEqual(sorted(p.gare for p in train.passages), sorted(CHAINE), ref)

    def test_les_deux_sens_existent(self):
        sens = set()
        for ref, (train, _) in self.trains().items():
            if "ANNULE" in ref:
                continue
            ordre = [p.gare for p in sorted(train.passages, key=lambda p: p.arrivee or p.depart)]
            sens.add(ordre[0] == CHAINE[0])
        self.assertEqual(sens, {True, False})

    def test_au_moins_un_train_en_route_et_un_a_quai_possibles_dans_la_journee(self):
        etats = set()
        for minute in range(0, 120, 3):
            instant = T + timedelta(minutes=minute)
            for ref, (train, _) in self.trains(instant).items():
                etats.add(estimer(train, instant, distance).etat)
        self.assertIn(Etat.EN_ROUTE, etats)
        self.assertIn(Etat.A_QUAI, etats)

    def test_cas_particuliers_presents(self):
        trains = self.trains(n=8)
        annule = [t for r, (t, _) in trains.items() if "ANNULE" in r]
        peri = [t for r, (t, _) in trains.items() if "PERIME" in r]
        self.assertEqual(len(annule), 1)
        self.assertEqual(len(peri), 1)
        self.assertEqual(estimer(annule[0], T, distance).etat, Etat.NON_PLACE)
        self.assertTrue(estimer(peri[0], T, distance).perime)

    def test_des_retards_existent(self):
        retards = [v for _, (_, r) in self.trains().items() for v in r.values()]
        self.assertTrue(any(v >= 60 for v in retards))
        self.assertTrue(any(v == 0 for v in retards))

    def test_deterministe(self):
        self.assertEqual(livraison_demo(T, CHAINE, distance, nombre=6), livraison_demo(T, CHAINE, distance, nombre=6))


class TestStopMonitoringDemo(unittest.TestCase):
    def nom(self, ref):
        return f"Gare {CHAINE.index(ref) + 1}"

    def test_passages_a_la_gare_du_milieu(self):
        rep = stop_monitoring_demo(T, CHAINE[2], CHAINE, distance, self.nom)
        passages = lire_passages(rep, T, limite=50)
        self.assertGreater(len(passages), 0)
        heures = [p["heure_estimee"] for p in passages]
        self.assertEqual(heures, sorted(heures))
        self.assertTrue(all(p["destination"] in ("Gare 1", "Gare 5") for p in passages))

    def test_destination_est_le_terminus_du_sens(self):
        rep = stop_monitoring_demo(T, CHAINE[2], CHAINE, distance, self.nom)
        visites = rep["StopMonitoringDelivery"][0]["MonitoredStopVisit"]
        for v in visites:
            j = v["MonitoredVehicleJourney"]
            ref = j["FramedVehicleJourneyRef"]["DatedVehicleJourneyRef"]
            if ref.startswith("DEMO-A-") or ref.startswith("DEMO-PERIME-"):
                self.assertEqual(j["DestinationName"][0]["value"], "Gare 5", ref)
            elif ref.startswith("DEMO-R-"):
                self.assertEqual(j["DestinationName"][0]["value"], "Gare 1", ref)

    def test_a_quai_coherent_avec_les_heures(self):
        for minute in range(0, 90, 2):
            instant = T + timedelta(minutes=minute)
            rep = stop_monitoring_demo(instant, CHAINE[2], CHAINE, distance, self.nom)
            for p in lire_passages(rep, instant, limite=50):
                if p["a_quai"]:
                    return
        self.fail("jamais de train à quai en 90 min")

    def test_gare_hors_chaine_reponse_vide(self):
        rep = stop_monitoring_demo(T, "STIF:StopArea:SP:999:", CHAINE, distance, self.nom)
        self.assertEqual(lire_passages(rep, T), [])


if __name__ == "__main__":
    unittest.main()
