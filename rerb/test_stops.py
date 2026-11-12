"""Tests de la tâche 2 : relier une gare PRIM (StopArea) à ses coordonnées.

Les tests sur les échantillons réels sont ignorés si data/ est absent
(data/ n'est pas versionné).
"""
import json
import unittest
from pathlib import Path

from stops import (
    StopNotFound,
    load_stops,
    lookup,
    stop_area_number,
    stop_area_refs,
)

RACINE = Path(__file__).resolve().parent.parent
CSV = Path(__file__).resolve().parent / "donnees/arrets_rerb_C01743.csv"  # versionné
ECHANTILLONS = [
    RACINE / "data/raw/A_estimated_timetable.json",
    RACINE / "data/raw/C_estimated_timetable_plus60s.json",
]


class TestNumero(unittest.TestCase):
    def test_extrait_le_numero(self):
        self.assertEqual(stop_area_number("STIF:StopArea:SP:58774:"), "58774")

    def test_refuse_un_quai(self):
        # un quai (StopPoint) n'est pas une gare : erreur claire
        with self.assertRaises(StopNotFound):
            stop_area_number("STIF:StopPoint:Q:12345:")

    def test_refuse_du_n_importe_quoi(self):
        for mauvais in ["", "58774", "STIF:StopArea:SP::", None]:
            with self.assertRaises(StopNotFound):
                stop_area_number(mauvais)


@unittest.skipUnless(CSV.exists(), "fichier des gares absent")
class TestFichierStatique(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stops = load_stops(CSV)

    def test_47_gares(self):
        self.assertEqual(len(self.stops), 47)

    def test_massy_palaiseau(self):
        s = lookup(self.stops, "STIF:StopArea:SP:58774:")
        self.assertEqual(s.name, "Massy - Palaiseau")
        self.assertAlmostEqual(s.lat, 48.72551907, places=6)
        self.assertAlmostEqual(s.lon, 2.25876773, places=6)

    def test_massy_verrieres_distincte(self):
        pal = lookup(self.stops, "STIF:StopArea:SP:58774:")
        ver = lookup(self.stops, "STIF:StopArea:SP:47940:")
        self.assertEqual(ver.name, "Massy - Verrières")
        self.assertNotEqual((pal.lat, pal.lon), (ver.lat, ver.lon))

    def test_gare_inconnue(self):
        with self.assertRaises(StopNotFound):
            lookup(self.stops, "STIF:StopArea:SP:1:")

    def test_coordonnees_en_ile_de_france(self):
        for s in self.stops.values():
            self.assertTrue(48.0 < s.lat < 49.5, s)
            self.assertTrue(1.8 < s.lon < 3.2, s)

    def test_echantillons_reels_tous_relies(self):
        trouves = False
        for chemin in ECHANTILLONS:
            if not chemin.exists():
                continue
            trouves = True
            refs = stop_area_refs(json.loads(chemin.read_text()))
            self.assertGreater(len(refs), 0)
            for ref in refs:
                lookup(self.stops, ref)  # lève StopNotFound si inconnue
        if not trouves:
            self.skipTest("data/raw absent")

    def test_echantillon_A_47_gares(self):
        if not ECHANTILLONS[0].exists():
            self.skipTest("data/raw absent")
        refs = stop_area_refs(json.loads(ECHANTILLONS[0].read_text()))
        self.assertEqual(len(refs), 47)


if __name__ == "__main__":
    unittest.main()
