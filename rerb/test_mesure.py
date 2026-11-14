"""Tests de la mesure d'erreur en mètres le long de la voie (niveau 1 et repères 0a, 0b).

Cadrage : docs/cadrage-tache-3.md §13 et §14 (E1 : erreur = PK estimé moins PK du GPS, positif = estimé en
avance ; E3 : repères 0a prochaine gare et 0b dernière gare ; E6 : trains à quai comptés à part).
Décor commun (exemple E3) : gare A à PK 0 m, gare B à PK 2 400 m, GPS à PK 1 000 m.
"""
import unittest

from mesure import mesurer, resumer
from position import Estimation, Etat

PKS = {"A": 0.0, "B": 2400.0}


def en_route(fraction, gare="A", suivante="B"):
    return Estimation(Etat.EN_ROUTE, gare=gare, gare_suivante=suivante, fraction=fraction,
                      distance_m=fraction * 2400.0)


class TestMesurer(unittest.TestCase):
    def test_exemple_e3(self):
        mesure, motif = mesurer(en_route(0.5), PKS, 1000.0)
        self.assertIsNone(motif)
        self.assertEqual(mesure.categorie, "en route")
        self.assertAlmostEqual(mesure.erreurs["0a"], 1400.0)   # placé à B : en avance
        self.assertAlmostEqual(mesure.erreurs["0b"], -1000.0)  # placé à A : en retard
        self.assertAlmostEqual(mesure.erreurs["niveau1"], 200.0)  # 1 200 m estimés - 1 000 m réels

    def test_invariant_0a_positif_0b_negatif_si_gps_entre_les_gares(self):
        for gps in (1.0, 1000.0, 2399.0):
            m, _ = mesurer(en_route(0.3), PKS, gps)
            self.assertGreater(m.erreurs["0a"], 0)
            self.assertLess(m.erreurs["0b"], 0)

    def test_voie_parcourue_dans_le_sens_decroissant(self):
        # train de B vers A : la gare précédente est B (PK 2 400), la suivante A (PK 0).
        # « Positif = en avance » se lit dans le sens de marche : mêmes signes que dans le sens croissant.
        mesure, _ = mesurer(en_route(0.5, gare="B", suivante="A"), PKS, 1000.0)
        self.assertAlmostEqual(mesure.erreurs["0a"], 1000.0)     # placé à A (0 m) : le train est encore à 1 000 m de A
        self.assertAlmostEqual(mesure.erreurs["0b"], -1400.0)    # placé à B (2 400 m) : 1 400 m en retard
        self.assertAlmostEqual(mesure.erreurs["niveau1"], -200.0)  # estimé 1 200 m, réel 1 000 m : en retard de 200 m

    def test_invariant_0a_positif_0b_negatif_dans_les_deux_sens(self):
        for gare, suivante in (("A", "B"), ("B", "A")):
            m, _ = mesurer(en_route(0.3, gare=gare, suivante=suivante), PKS, 1000.0)
            self.assertGreater(m.erreurs["0a"], 0)
            self.assertLess(m.erreurs["0b"], 0)

    def test_a_quai_compte_a_part(self):
        e = Estimation(Etat.A_QUAI, gare="B", distance_m=0.0)
        mesure, motif = mesurer(e, PKS, 2350.0)
        self.assertIsNone(motif)
        self.assertEqual(mesure.categorie, "à quai")
        self.assertAlmostEqual(mesure.erreurs["niveau1"], 50.0)
        self.assertAlmostEqual(mesure.erreurs["0a"], 50.0)
        self.assertAlmostEqual(mesure.erreurs["0b"], 50.0)

    def test_etats_sans_position_sont_ecartes_avec_motif(self):
        for etat in (Etat.POSITION_INCONNUE, Etat.TERMINE, Etat.NON_PLACE):
            mesure, motif = mesurer(Estimation(etat, motif="x"), PKS, 1000.0)
            self.assertIsNone(mesure)
            self.assertEqual(motif, f"état : {etat.value}")

    def test_gare_sans_pk(self):
        mesure, motif = mesurer(en_route(0.5, suivante="Z"), PKS, 1000.0)
        self.assertIsNone(mesure)
        self.assertEqual(motif, "gare sans PK")


class TestCasLimites(unittest.TestCase):
    def test_pk_egaux_pas_d_erreur(self):
        mesure, motif = mesurer(en_route(0.5), {"A": 100.0, "B": 100.0}, 100.0)
        self.assertIsNone(motif)
        self.assertAlmostEqual(mesure.erreurs["niveau1"], 0.0)

    def test_a_quai_gare_absente(self):
        e = Estimation(Etat.A_QUAI, gare="Z", distance_m=0.0)
        self.assertEqual(mesurer(e, PKS, 0.0), (None, "gare sans PK"))


class TestResumer(unittest.TestCase):
    def test_un_seul_point(self):
        r = resumer([-30.0])
        self.assertEqual((r['n'], r['biais'], r['mediane_abs'], r['p90_abs']), (1, -30.0, 30.0, 30.0))

    def test_vide(self):
        self.assertEqual(resumer([])["n"], 0)

    def test_statistiques(self):
        r = resumer([-100.0, 50.0, 10.0, -10.0, 300.0])
        self.assertEqual(r["n"], 5)
        self.assertAlmostEqual(r["biais"], 50.0)            # moyenne signée
        self.assertAlmostEqual(r["mediane_abs"], 50.0)      # |.| = 10 10 50 100 300
        self.assertAlmostEqual(r["p90_abs"], 300.0)         # rang du 90e centile : ceil(0,9 x 5) = 5e valeur


if __name__ == "__main__":
    unittest.main()
