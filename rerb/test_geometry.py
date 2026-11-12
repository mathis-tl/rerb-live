"""Tests de la tâche 3 (géométrie) : distance le long des rails entre deux gares.

Tests synthétiques (toujours exécutés) puis tests sur les données réelles
(ignorés si data/ est absent : data/ n'est pas versionné).
"""
import math
import unittest
from pathlib import Path

from geometry import (
    Geometry,
    NoValidDistance,
    choose_distance,
    clean_retraces,
    cumulative,
    load_morceaux,
    station_passes,
)
from stops import load_stops

RACINE = Path(__file__).resolve().parent.parent
CSV = Path(__file__).resolve().parent / "donnees/arrets_rerb_C01743.csv"
TRACE = RACINE / "data/static/rerb/rerb_trace_C01743.json"

LAT0, LON0 = 48.85, 2.35
M_LAT = 111195.0
M_LON = 111195.0 * math.cos(math.radians(LAT0))


def pt(x, y):
    """Point à x mètres à l'est et y mètres au nord de l'origine -> (lat, lon)."""
    return (LAT0 + y / M_LAT, LON0 + x / M_LON)


def ligne(x0, y0, x1, y1, pas=100.0):
    n = max(1, int(round(math.hypot(x1 - x0, y1 - y0) / pas)))
    return [pt(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n) for i in range(n + 1)]


def avec_pointe(bruit=0.0, suite_m=1000):
    """Voie de 0 à 1 000 m, pointe aller-retour de 1 000 m vers l'est, puis suite de suite_m mètres vers le sud."""
    aller = ligne(0, 0, 1000, 0)
    pointe_aller = ligne(1000, 0, 2000, 0)[1:]
    pointe_retour = [pt(x_ + bruit, y_) for (x_, y_) in
                     [(2000 - 100.0 * i, 0.0) for i in range(0, 11)]][1:]
    suite = ligne(1000, 0, 1000, -suite_m)[1:]
    return aller + pointe_aller + pointe_retour + suite


class TestNettoyage(unittest.TestCase):
    def test_pointe_exacte_supprimee(self):
        pts = avec_pointe()
        brut = cumulative(pts)[-1]
        propre = clean_retraces(pts)
        self.assertAlmostEqual(brut, 4000, delta=20)
        self.assertAlmostEqual(cumulative(propre)[-1], 2000, delta=20)

    def test_pointe_avec_bruit_de_3_m_supprimee(self):
        propre = clean_retraces(avec_pointe(bruit=3.0))
        self.assertAlmostEqual(cumulative(propre)[-1], 2000, delta=30)

    def test_voie_sans_pointe_inchangee(self):
        pts = ligne(0, 0, 3000, 0) + ligne(3000, 0, 3000, 1000)[1:]
        self.assertEqual(len(clean_retraces(pts)), len(pts))

    def test_voie_en_u_large_non_effacee(self):
        # deux voies parallèles à 50 m : un morceau qui fait demi-tour sur l'autre voie n'est pas un retour exact
        pts = ligne(0, 0, 1000, 0) + ligne(1000, 50, 0, 50)
        self.assertEqual(len(clean_retraces(pts)), len(pts))


class TestPassages(unittest.TestCase):
    def test_projection_entre_deux_sommets(self):
        # segment de 247 m sans sommet ; gare à 99 m de la voie, au milieu du segment
        pts = [pt(0, 0), pt(247, 0), pt(500, 0)]
        passes = station_passes(pts, cumulative(pts), *pt(130, 99))
        self.assertEqual(len(passes), 1)
        self.assertAlmostEqual(passes[0].pk, 130, delta=1.0)
        self.assertAlmostEqual(passes[0].offset, 99, delta=1.0)

    def test_gare_trop_loin_ignoree(self):
        pts = ligne(0, 0, 1000, 0)
        self.assertEqual(station_passes(pts, cumulative(pts), *pt(500, 400)), [])

    def test_deux_passages_avant_nettoyage_un_apres(self):
        pts = avec_pointe()
        gare = pt(1000, 20)
        self.assertEqual(len(station_passes(pts, cumulative(pts), *gare)), 2)
        propre = clean_retraces(pts)
        self.assertEqual(len(station_passes(propre, cumulative(propre), *gare)), 1)


class TestChoix(unittest.TestCase):
    def test_minimum_valide(self):
        # 1 365 m < ligne droite (rejeté), 1 476 m valide, 2 500 m > 1,35 x (rejeté)
        self.assertEqual(choose_distance([1365, 1476, 2500, 1480], straight=1476), 1476)

    def test_aucun_valide(self):
        with self.assertRaises(NoValidDistance):
            choose_distance([100, 5000], straight=1500)

    def test_aucun_candidat(self):
        with self.assertRaises(NoValidDistance):
            choose_distance([], straight=1500)

    def test_seuils_reglables(self):
        self.assertEqual(choose_distance([1400, 1476], straight=1476, low=0.9), 1400)


class TestGeometrySynthetique(unittest.TestCase):
    def setUp(self):
        stations = {"A": pt(0, 0), "B": pt(1000, 0), "C": pt(1000, -300)}
        propre = ligne(0, 0, 1000, 0) + ligne(1000, 0, 1000, -300)[1:]
        self.g = Geometry([avec_pointe(suite_m=300), propre], stations)

    def test_distance_ignore_la_pointe(self):
        self.assertAlmostEqual(self.g.distance("A", "B"), 1000, delta=15)
        self.assertAlmostEqual(self.g.distance("A", "C"), 1300, delta=20)

    def test_virage_trop_serre_pour_le_filtre(self):
        # limite connue : angle droit, rails 2 000 m pour 1 414 m en ligne droite (rapport 1,41 > 1,35).
        # Pour deux gares éloignées, il faudra additionner des couples voisins plutôt que mesurer directement.
        stations = {"A": pt(0, 0), "C": pt(1000, -1000)}
        g = Geometry([ligne(0, 0, 1000, 0) + ligne(1000, 0, 1000, -1000)[1:]], stations)
        with self.assertRaises(NoValidDistance):
            g.distance("A", "C")

    def test_distance_symetrique(self):
        self.assertAlmostEqual(self.g.distance("B", "A"), self.g.distance("A", "B"), delta=1e-6)

    def test_gare_inconnue(self):
        with self.assertRaises(KeyError):
            self.g.distance("A", "Z")

    def test_aucun_morceau_ne_contient_le_couple(self):
        stations = {"A": pt(0, 0), "Z": pt(50000, 50000)}
        g = Geometry([ligne(0, 0, 1000, 0)], stations)
        with self.assertRaises(NoValidDistance):
            g.distance("A", "Z")


@unittest.skipUnless(TRACE.exists() and CSV.exists(), "tracé réel absent (data/static)")
class TestDonneesReelles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        stops = load_stops(CSV)
        cls.stops = stops
        cls.g = Geometry(load_morceaux(TRACE), {n: (s.lat, s.lon) for n, s in stops.items()})
        cls.num = {s.name: n for n, s in stops.items()}

    def dist(self, a, b):
        return self.g.distance(self.num[a], self.num[b])

    def test_massy_verrieres_palaiseau(self):
        # avant correction : 4 423 m, 3 031 m, 1 589 m selon le morceau ; mesure : environ 1 541 m
        self.assertAlmostEqual(self.dist("Massy - Verrières", "Massy - Palaiseau"), 1541, delta=20)

    def test_aulnay_blanc_mesnil(self):
        # morceau le plus court : 1 365 m (faux, plus court que la ligne droite) ; Google Maps : environ 1 481 m
        self.assertAlmostEqual(self.dist("Aulnay-sous-Bois", "Le Blanc-Mesnil"), 1476, delta=20)

    def test_verrieres_baconnets(self):
        # médiane après nettoyage : 2 189 m (faux) ; Google Maps : environ 1,2 km
        d = self.dist("Massy - Verrières", "Les Baconnets")
        self.assertTrue(1150 < d < 1250, d)

    def test_antony_massy_palaiseau(self):
        # médiane : 5 733 m (pointe de Massy) ; minimum valide : 4 752 m
        d = self.dist("Antony", "Massy - Palaiseau")
        self.assertTrue(4600 < d < 4900, d)

    def test_chatelet_saint_michel(self):
        # cas non tranché (tunnel) : on prend le minimum valide, 1 008 m
        d = self.dist("Châtelet - Les Halles", "Saint-Michel Notre-Dame")
        self.assertAlmostEqual(d, 1008, delta=25)

    def test_46_couples_voisins_tous_calculables_et_plausibles(self):
        # chaque distance entre gares voisines est entre 0,99 et 1,35 fois la ligne droite
        n = 0
        for (a, b) in self.g.neighbor_pairs():
            d = self.g.distance(a, b)
            vol = self.g.straight(a, b)
            self.assertTrue(0.99 * vol <= d <= 1.35 * vol, (a, b, d, vol))
            n += 1
        self.assertEqual(n, 46)


if __name__ == "__main__":
    unittest.main()
