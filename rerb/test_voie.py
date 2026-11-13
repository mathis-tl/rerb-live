"""Tests : distance le long d'une voie unique (Boston, Helsinki) et PK d'un point.

Décision P1 (13/11/2026) : un point (gare ou GPS) à plus de 150 m de la voie est rejeté.
Tests synthétiques toujours exécutés ; tests sur fichiers statiques réels ignorés si data/ est absent.
"""
import math
import unittest
from pathlib import Path

from position import DistanceInconnue
from voie import (
    PkAmbigu,
    PointHorsVoie,
    Voie,
    charger_arrets,
    charger_formes,
    charger_trips,
    distance_sur_voie,
    pk_gares,
)

RACINE = Path(__file__).resolve().parent.parent
STATIC = RACINE / "data/static"

LAT0, LON0 = 48.85, 2.35
M_LAT = 111195.0
M_LON = 111195.0 * math.cos(math.radians(LAT0))


def pt(x, y):
    """(est, nord) en mètres -> (lat, lon)."""
    return (LAT0 + y / M_LAT, LON0 + x / M_LON)


def ligne(x0, y0, x1, y1, pas=100.0):
    n = max(1, int(math.hypot(x1 - x0, y1 - y0) // pas))
    return [pt(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n) for i in range(n + 1)]


def boucle():
    """Aller vers l'est (y=0, PK 0 à 1000), demi-tour nord (1000 à 1100), retour vers l'ouest (y=100, 1100 à 2100)."""
    return ligne(0, 0, 1000, 0) + ligne(1000, 0, 1000, 100)[1:] + ligne(1000, 100, 0, 100)[1:]


class TestPk(unittest.TestCase):
    def test_longueur(self):
        self.assertAlmostEqual(Voie(ligne(0, 0, 1000, 0)).longueur, 1000.0, delta=1.0)

    def test_pk_decale_sur_le_cote(self):
        v = Voie(ligne(0, 0, 1000, 0))
        lat, lon = pt(300, 20)
        self.assertAlmostEqual(v.pk(lat, lon), 300.0, delta=1.0)

    def test_projection_sur_le_segment_pas_sur_le_sommet(self):
        # un seul segment de 1 000 m : le sommet le plus proche serait à 500 m, le PK vrai est 500 m
        v = Voie([pt(0, 0), pt(1000, 0)])
        lat, lon = pt(500, 10)
        self.assertAlmostEqual(v.pk(lat, lon), 500.0, delta=1.0)

    def test_voie_en_L(self):
        v = Voie(ligne(0, 0, 1000, 0) + ligne(1000, 0, 1000, 1000)[1:])
        lat, lon = pt(1010, 500)
        self.assertAlmostEqual(v.pk(lat, lon), 1500.0, delta=1.0)

    def test_rayon_150_m(self):
        v = Voie(ligne(0, 0, 1000, 0))
        self.assertAlmostEqual(v.pk(*pt(500, 149)), 500.0, delta=1.0)
        with self.assertRaises(PointHorsVoie):
            v.pk(*pt(500, 151))

    def test_rayon_reglable(self):
        v = Voie(ligne(0, 0, 1000, 0), rayon=50.0)
        with self.assertRaises(PointHorsVoie):
            v.pk(*pt(500, 60))

    def test_extremites(self):
        v = Voie(ligne(0, 0, 1000, 0))
        self.assertAlmostEqual(v.pk(*pt(-30, 0)), 0.0, delta=1.0)
        self.assertAlmostEqual(v.pk(*pt(1030, 0)), 1000.0, delta=1.0)


class TestBoucle(unittest.TestCase):
    def setUp(self):
        self.v = Voie(boucle(), rayon=60.0)

    def test_deux_passages_sans_indice_est_ambigu(self):
        c = self.v.candidats(*pt(500, 50))
        self.assertEqual(len(c), 2)
        self.assertAlmostEqual(c[0].pk, 500.0, delta=1.0)
        self.assertAlmostEqual(c[1].pk, 1600.0, delta=1.0)
        with self.assertRaises(PkAmbigu):
            self.v.pk(*pt(500, 50))

    def test_indice_apres_choisit_le_bon_passage(self):
        self.assertAlmostEqual(self.v.pk(*pt(500, 50), apres=0.0), 500.0, delta=1.0)
        self.assertAlmostEqual(self.v.pk(*pt(500, 50), apres=800.0), 1600.0, delta=1.0)

    def test_aucun_passage_apres_l_indice(self):
        with self.assertRaises(PointHorsVoie):
            self.v.pk(*pt(500, 50), apres=1700.0)

    def test_point_unique_n_est_pas_ambigu(self):
        self.assertAlmostEqual(self.v.pk(*pt(300, -30)), 300.0, delta=1.0)


class TestGares(unittest.TestCase):
    def test_pk_des_gares_dans_l_ordre(self):
        v = Voie(ligne(0, 0, 3000, 0))
        gares = [("A", *pt(500, 10)), ("B", *pt(1500, -10)), ("C", *pt(2500, 5))]
        pks = pk_gares(v, gares)
        self.assertEqual([g for g, _ in pks], ["A", "B", "C"])
        for (_, pk), attendu in zip(pks, (500.0, 1500.0, 2500.0)):
            self.assertAlmostEqual(pk, attendu, delta=1.0)

    def test_pk_des_gares_sur_une_boucle(self):
        v = Voie(boucle(), rayon=60.0)
        # A à l'aller, puis B près du retour : B prend le passage après A (1 600 m, pas 500 m)
        gares = [("A", *pt(800, -30)), ("B", *pt(500, 50))]
        pks = dict(pk_gares(v, gares))
        self.assertAlmostEqual(pks["A"], 800.0, delta=1.0)
        self.assertAlmostEqual(pks["B"], 1600.0, delta=1.0)

    def test_p2_premiere_gare_ambigue_prend_le_premier_passage(self):
        # boucle (HSL lignes I et P) : la 1re gare touche la voie à l'aller et au retour (PK 500 et 1 600)
        v = Voie(boucle(), rayon=60.0)
        gares = [("A", *pt(500, 50)), ("B", *pt(800, -30))]
        with self.assertRaises(PkAmbigu):  # par défaut : refus
            pk_gares(v, gares)
        pks = dict(pk_gares(v, gares, premier_passage=True))
        self.assertAlmostEqual(pks["A"], 500.0, delta=1.0)
        self.assertAlmostEqual(pks["B"], 800.0, delta=1.0)

    def test_p2_gare_hors_ordre_est_signalee(self):
        # B (PK 200) est située avant A (PK 500, 1er passage) : introuvable après A -> signalé
        v = Voie(boucle(), rayon=60.0)
        with self.assertRaises(PointHorsVoie):
            pk_gares(v, [("A", *pt(500, 50)), ("B", *pt(200, -30))], premier_passage=True)

    def test_p2_suite_de_la_boucle_apres_la_premiere_gare(self):
        v = Voie(boucle(), rayon=60.0)
        gares = [("A", *pt(500, 50)), ("B", *pt(800, -30)), ("C", *pt(500, 50 + 0.0))]
        # A -> 500 (1er passage), B -> 800, C (même point que A) -> passage suivant, 1 600
        pks = pk_gares(v, gares, premier_passage=True)
        self.assertAlmostEqual(pks[0][1], 500.0, delta=1.0)
        self.assertAlmostEqual(pks[1][1], 800.0, delta=1.0)
        self.assertAlmostEqual(pks[2][1], 1600.0, delta=1.0)

    def test_gare_hors_voie_est_signalee(self):
        v = Voie(ligne(0, 0, 3000, 0))
        with self.assertRaises(PointHorsVoie):
            pk_gares(v, [("A", *pt(500, 10)), ("Z", *pt(1500, 400))])

    def test_distance_entre_gares_est_la_difference_de_pk(self):
        d = distance_sur_voie({"A": 500.0, "B": 1500.0, "C": 2500.0})
        self.assertAlmostEqual(d("A", "B"), 1000.0)
        self.assertAlmostEqual(d("C", "A"), 2000.0)
        self.assertEqual(d("B", "B"), 0.0)
        with self.assertRaises(DistanceInconnue):
            d("A", "Z")


FORMES_HSL = STATIC / "hsl/shapes.txt"
FORMES_MBTA = STATIC / "mbta/shapes.txt"


@unittest.skipUnless(FORMES_HSL.exists() and FORMES_MBTA.exists(), "fichiers statiques absents (data/ non versionné)")
class TestFichiersReels(unittest.TestCase):
    def test_hsl_unite_de_shape_dist_traveled(self):
        # le fichier HSL donne une distance cumulée : on vérifie son unité par rapport à notre calcul
        import csv
        dernier = {}
        with FORMES_HSL.open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                dernier[r["shape_id"]] = float(r["shape_dist_traveled"])
        ids = sorted(dernier)[:5]
        formes = charger_formes(FORMES_HSL, ids)
        self.assertEqual(sorted(formes), ids)
        for i in ids:
            ours = Voie(formes[i]).longueur
            self.assertAlmostEqual(ours / (dernier[i] * 1000.0), 1.0, delta=0.02, msg=i)

    def test_mbta_chargement(self):
        trips = charger_trips(STATIC / "mbta/trips.txt")
        self.assertGreater(len(trips), 1000)
        une = next(iter(trips.values()))
        formes = charger_formes(FORMES_MBTA, [une])
        self.assertIn(une, formes)
        self.assertGreater(Voie(formes[une]).longueur, 1000.0)

    def test_arrets_avec_coordonnees(self):
        arrets = charger_arrets(STATIC / "hsl/stops.txt")
        lat, lon = arrets["1010107"]
        self.assertTrue(59 < lat < 61 and 24 < lon < 26)


if __name__ == "__main__":
    unittest.main()
