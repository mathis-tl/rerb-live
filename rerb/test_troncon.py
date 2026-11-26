"""Latitude et longitude d'un train à X mètres de la gare A sur le tronçon A -> B (gares voisines ou non).

Synthétique d'abord, puis données réelles du RER B (ignorées si data/ est absent).
"""
import math
import unittest
from pathlib import Path

from geometry import Geometry, cumulative, load_morceaux, station_passes
from position import Reseau
from stops import load_stops
from troncon import cap_degres, chemin_troncon, point_sur_troncon

RACINE = Path(__file__).resolve().parent.parent
CSV = Path(__file__).resolve().parent / "donnees/arrets_rerb_C01743.csv"
TRACE = RACINE / "data/static/rerb/rerb_trace_C01743.json"

LAT0, LON0 = 48.85, 2.35
M_LAT = 111195.0
M_LON = 111195.0 * math.cos(math.radians(LAT0))


def pt(x, y):
    return (LAT0 + y / M_LAT, LON0 + x / M_LON)


def ligne(x0, y0, x1, y1, pas=100.0):
    n = max(1, int(round(math.hypot(x1 - x0, y1 - y0) / pas)))
    return [pt(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n) for i in range(n + 1)]


def xy(p):
    return ((p[1] - LON0) * M_LON, (p[0] - LAT0) * M_LAT)


class TestSynthetique(unittest.TestCase):
    """Trois gares alignées : A à 0 m, B à 1 000 m, C à 2 500 m, sur deux morceaux qui se chevauchent."""

    def setUp(self):
        stations = {"A": pt(0, 0), "B": pt(1000, 0), "C": pt(2500, 0)}
        self.g = Geometry([ligne(0, 0, 1500, 0), ligne(900, 0, 2500, 0)], stations)
        self.r = Reseau({p: self.g.distance(*p) for p in self.g.neighbor_pairs()})

    def test_troncon_voisin(self):
        x, y = xy(point_sur_troncon(self.g, self.r, "A", "B", 400.0))
        self.assertAlmostEqual(x, 400.0, delta=5)
        self.assertAlmostEqual(y, 0.0, delta=5)

    def test_troncon_non_voisin_enchaine_les_voisins(self):
        # A -> C passe par B : 1 500 m après A = 500 m après B, sur le 2e tronçon
        x, _ = xy(point_sur_troncon(self.g, self.r, "A", "C", 1500.0))
        self.assertAlmostEqual(x, 1500.0, delta=8)

    def test_sens_inverse(self):
        x, _ = xy(point_sur_troncon(self.g, self.r, "C", "A", 500.0))
        self.assertAlmostEqual(x, 2000.0, delta=8)

    def test_chemin_sans_doublon_a_la_jonction(self):
        c = chemin_troncon(self.g, self.r, "A", "C")
        self.assertEqual(len(c), len(set(c)))
        self.assertAlmostEqual(cumulative(c)[-1], self.r.distance("A", "C"), delta=10)

    def test_distance_zero_et_totale(self):
        c = chemin_troncon(self.g, self.r, "A", "C")
        self.assertEqual(point_sur_troncon(self.g, self.r, "A", "C", 0.0), c[0])
        self.assertEqual(point_sur_troncon(self.g, self.r, "A", "C", 1e9), c[-1])

    def test_meme_gare(self):
        self.assertEqual(point_sur_troncon(self.g, self.r, "B", "B", 0.0), pt(1000, 0))


@unittest.skipUnless(CSV.exists() and TRACE.exists(), "données statiques absentes")
class TestRerbReel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        stops = load_stops(CSV)
        cls.num = {s.name: n for n, s in stops.items()}
        cls.g = Geometry(load_morceaux(TRACE), {n: (s.lat, s.lon) for n, s in stops.items()})
        cls.r = Reseau({p: cls.g.distance(*p) for p in cls.g.neighbor_pairs()})

    def proche_du_trace(self, p, rayon=50.0):
        return any(station_passes(pts, cum, p[0], p[1], radius=rayon) for pts, cum in self.g._lines)

    def test_semi_direct_antony_massy_palaiseau(self):
        a, b = self.num["Antony"], self.num["Massy - Palaiseau"]
        total = self.r.distance(a, b)
        c = chemin_troncon(self.g, self.r, a, b)
        self.assertAlmostEqual(cumulative(c)[-1], total, delta=0.02 * total)
        for f in (0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0):
            self.assertTrue(self.proche_du_trace(point_sur_troncon(self.g, self.r, a, b, f * total)), f)

    def test_les_deux_extremites_sont_les_gares(self):
        a, b = self.num["Massy - Verrières"], self.num["Massy - Palaiseau"]
        c = chemin_troncon(self.g, self.r, a, b)
        for p, n in ((c[0], a), (c[-1], b)):
            s = self.g._stations[n]
            self.assertLess(math.hypot((p[0] - s[0]) * M_LAT, (p[1] - s[1]) * M_LON), 150)

    def test_toutes_les_paires_de_gares_voisines_dans_les_deux_sens(self):
        for (a, b) in self.g.neighbor_pairs():
            for x, y in ((a, b), (b, a)):
                total = self.r.distance(x, y)
                p = point_sur_troncon(self.g, self.r, x, y, total / 2)
                self.assertTrue(self.proche_du_trace(p), (x, y))


class TestCap(unittest.TestCase):
    """Cap = direction de marche en degrés, 0 = nord, 90 = est, sens horaire."""

    def test_quatre_points_cardinaux(self):
        p = (48.0, 2.0)
        self.assertAlmostEqual(cap_degres(p, (48.001, 2.0)), 0.0, places=3)
        self.assertAlmostEqual(cap_degres(p, (48.0, 2.001)), 90.0, places=3)
        self.assertAlmostEqual(cap_degres(p, (47.999, 2.0)), 180.0, places=3)
        self.assertAlmostEqual(cap_degres(p, (48.0, 1.999)), 270.0, places=3)

    def test_nord_est_a_cette_latitude(self):
        # 1e-3 degré de latitude = 111 m ; 1e-3 degré de longitude à 48° = 74,5 m : le cap n'est pas 45°
        c = cap_degres((48.0, 2.0), (48.001, 2.001))
        self.assertAlmostEqual(c, math.degrees(math.atan2(74.5, 111.0)), delta=0.3)

    def test_points_identiques_pas_de_cap(self):
        self.assertIsNone(cap_degres((48.0, 2.0), (48.0, 2.0)))


if __name__ == "__main__":
    unittest.main()
