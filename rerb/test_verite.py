"""Tâche 4, J2 : vérité RER B à Massy - Palaiseau (`VehicleAtStop` de stop-monitoring contre `estimer`).

Plan : docs/plan-tache-4.md (J2, arrêt S2). Seuils fixés AVANT de regarder les résultats : rappel et précision
bon à 80 %, inquiétant sous 60 % ; heure de départ : médiane de |écart| 120 s, 90e centile 300 s.
Fonctions pures sur des ensembles de références de courses ; données inventées.
"""
import unittest
from datetime import datetime, timedelta, timezone

from verite import (SEUILS, classer_a_quai, departs_observes, ecart_depart, rapport, verdict)


def h(secondes):
    return datetime(2026, 11, 13, 10, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=secondes)


class TestClasserAQuai(unittest.TestCase):
    def test_comptes_exclusifs(self):
        # vrais : 1 2 3 4 ; listés par stop-monitoring : 1 2 3 4 5 6 ; connus de estimated-timetable : 1 2 3 5 6 7
        # estimés à quai : 1 2 5 7
        c = classer_a_quai(vrais={"1", "2", "3", "4"}, visibles={"1", "2", "3", "4", "5", "6"},
                           connus={"1", "2", "3", "5", "6", "7"}, predits={"1", "2", "5", "7"})
        self.assertEqual(c["vrai positif"], 2)                    # 1 et 2
        self.assertEqual(c["manqué : autre état estimé"], 1)      # 3 connu mais estimé ailleurs
        self.assertEqual(c["manqué : train absent des prévisions"], 1)   # 4 inconnu de estimated-timetable
        self.assertEqual(c["faux positif"], 1)                    # 5 : listé, VehicleAtStop faux
        self.assertEqual(c["estimé à quai, absent de stop-monitoring"], 1)   # 7

    def test_rien(self):
        c = classer_a_quai(set(), set(), set(), set())
        self.assertTrue(all(v == 0 for v in c.values()))


class TestDepartsObserves(unittest.TestCase):
    def test_sortie_entre_deux_releves(self):
        r = [(h(0), {"A"}), (h(120), set()), (h(240), set())]
        self.assertEqual(departs_observes(r), [("A", h(0), h(120))])

    def test_train_toujours_a_quai_deux_releves(self):
        r = [(h(0), {"A"}), (h(120), {"A"}), (h(240), set())]
        self.assertEqual(departs_observes(r), [("A", h(120), h(240))])

    def test_releve_manquant_trop_long_ecarte(self):
        r = [(h(0), {"A"}), (h(400), set())]   # trou de 400 s : départ inobservable
        self.assertEqual(departs_observes(r), [])

    def test_derniere_observation_sans_suite(self):
        self.assertEqual(departs_observes([(h(0), {"A"})]), [])

    def test_plusieurs_trains(self):
        r = [(h(0), {"A", "B"}), (h(120), {"B"}), (h(240), set())]
        self.assertEqual(sorted(departs_observes(r)), [("A", h(0), h(120)), ("B", h(120), h(240))])


class TestEcartDepart(unittest.TestCase):
    def test_signe_positif_si_estimation_en_avance(self):
        # observé entre 10:02:10 et 10:04:10 (milieu 10:03:10) ; estimé 10:03:00 -> l'estimation est en AVANCE de 10 s
        bas, haut = h(130), h(250)
        self.assertEqual(ecart_depart(h(180), bas, haut)[0], 10.0)

    def test_negatif_si_estimation_en_retard(self):
        self.assertEqual(ecart_depart(h(200), h(130), h(250))[0], -10.0)

    def test_indiscernable_du_bruit(self):
        # écart de 50 s pour un intervalle de 120 s (demi-largeur 60 s) : dans l'incertitude
        e, dans = ecart_depart(h(190 - 50), h(130), h(250))
        self.assertTrue(dans)
        e, dans = ecart_depart(h(0), h(130), h(250))
        self.assertFalse(dans)


class TestVerdict(unittest.TestCase):
    def test_seuils_fixes_d_avance(self):
        self.assertEqual(SEUILS, {"rappel": (0.80, 0.60), "précision": (0.80, 0.60),
                                  "médiane départ": (120.0, None), "p90 départ": (300.0, None)})

    def test_rappel_precision(self):
        self.assertEqual(verdict("rappel", 0.80), "bon")
        self.assertEqual(verdict("rappel", 0.79), "moyen")
        self.assertEqual(verdict("rappel", 0.60), "moyen")
        self.assertEqual(verdict("rappel", 0.59), "inquiétant")

    def test_heure_de_depart(self):
        self.assertEqual(verdict("médiane départ", 120.0), "bon")
        self.assertEqual(verdict("médiane départ", 121.0), "pas bon")
        self.assertEqual(verdict("p90 départ", 301.0), "pas bon")


class TestRapport(unittest.TestCase):
    def test_taux_avec_comptes_bruts(self):
        c = {"vrai positif": 6, "manqué : autre état estimé": 3, "manqué : train absent des prévisions": 1,
             "faux positif": 2, "estimé à quai, absent de stop-monitoring": 4}
        r = rapport(c)
        self.assertEqual(r["rappel"], (6, 10))            # 6 sur 10 vrais
        self.assertEqual(r["précision"], (6, 8))          # 6 sur (6 + 2 faux positifs listés)
        self.assertEqual(r["précision, absents inclus"], (6, 12))

    def test_zero_vrai(self):
        r = rapport({"vrai positif": 0, "manqué : autre état estimé": 0, "manqué : train absent des prévisions": 0,
                     "faux positif": 0, "estimé à quai, absent de stop-monitoring": 0})
        self.assertEqual(r["rappel"], (0, 0))


if __name__ == "__main__":
    unittest.main()
