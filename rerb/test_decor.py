"""Tâche 4, décor 3D : conversion d'un extrait OpenStreetMap (Overpass `out geom`) en fichier compact pour la carte.
Données inventées, aucun réseau."""
import unittest

from decor import convertir, hauteur_m, classe_route, HAUTEUR_DEFAUT_M


def geom(*pts):
    return [{"lat": la, "lon": lo} for lo, la in pts]


CARRE = geom((2.0, 48.0), (2.001, 48.0), (2.001, 48.001), (2.0, 48.001), (2.0, 48.0))
LIGNE = geom((2.0, 48.0), (2.001, 48.001))


def way(tags, g):
    return {"type": "way", "id": 1, "tags": tags, "geometry": g}


class TestHauteur(unittest.TestCase):
    def test_height_explicite(self):
        self.assertEqual(hauteur_m({"height": "12.5"}), (12.5, True))
        self.assertEqual(hauteur_m({"height": "12 m"}), (12.0, True))

    def test_niveaux(self):
        self.assertEqual(hauteur_m({"building:levels": "4"}), (12.0, True))

    def test_defaut_et_marquage(self):
        self.assertEqual(hauteur_m({}), (HAUTEUR_DEFAUT_M, False))
        self.assertEqual(hauteur_m({"height": "grand"}), (HAUTEUR_DEFAUT_M, False))

    def test_valeurs_absurdes_ignorees(self):
        self.assertEqual(hauteur_m({"height": "-3"}), (HAUTEUR_DEFAUT_M, False))
        self.assertEqual(hauteur_m({"height": "9999"}), (HAUTEUR_DEFAUT_M, False))


class TestClasses(unittest.TestCase):
    def test_classes(self):
        self.assertEqual(classe_route("primary"), "grand")
        self.assertEqual(classe_route("tertiary"), "moyen")
        self.assertEqual(classe_route("residential"), "petit")
        self.assertEqual(classe_route("footway"), "pieton")
        self.assertIsNone(classe_route("proposed"))


class TestConvertir(unittest.TestCase):
    def test_batiment(self):
        d, c = convertir({"elements": [way({"building": "yes", "height": "20"}, CARRE)]})
        self.assertEqual(len(d["batiments"]), 1)
        self.assertEqual(d["batiments"][0]["h"], 20.0)
        self.assertEqual(c["batiments"], 1)
        self.assertEqual(c["batiments_hauteur_par_defaut"], 0)

    def test_hauteur_par_defaut_comptee(self):
        _, c = convertir({"elements": [way({"building": "yes"}, CARRE)]})
        self.assertEqual(c["batiments_hauteur_par_defaut"], 1)

    def test_batiment_non_ferme_ecarte_et_compte(self):
        d, c = convertir({"elements": [way({"building": "yes"}, LIGNE)]})
        self.assertEqual(d["batiments"], [])
        self.assertEqual(c["ecartes_non_fermes"], 1)

    def test_relation_garde_les_contours_exterieurs(self):
        rel = {"type": "relation", "tags": {"building": "yes"}, "members": [
            {"type": "way", "role": "outer", "geometry": CARRE}, {"type": "way", "role": "inner", "geometry": CARRE}]}
        d, c = convertir({"elements": [rel]})
        self.assertEqual(len(d["batiments"]), 1)

    def test_routes_rails_verts_eaux(self):
        d, _ = convertir({"elements": [
            way({"highway": "residential"}, LIGNE), way({"highway": "proposed"}, LIGNE),
            way({"railway": "rail"}, LIGNE), way({"railway": "platform"}, CARRE),
            way({"leisure": "park"}, CARRE), way({"natural": "water"}, CARRE), way({"waterway": "river"}, LIGNE)]})
        self.assertEqual([r["t"] for r in d["routes"]], ["petit"])
        self.assertEqual(len(d["rails"]), 1)
        self.assertEqual(len(d["quais"]), 1)
        self.assertEqual(len(d["verts"]), 1)
        self.assertEqual(len(d["eaux"]), 1)
        self.assertEqual(len(d["cours_eau"]), 1)

    def test_coordonnees_arrondies_lon_lat(self):
        d, _ = convertir({"elements": [way({"highway": "residential"}, geom((2.1234567, 48.7654321), (2.2, 48.8)))]})
        self.assertEqual(d["routes"][0]["c"][0], [2.12346, 48.76543])

    def test_element_sans_geometrie_ignore(self):
        d, _ = convertir({"elements": [{"type": "way", "tags": {"building": "yes"}}]})
        self.assertEqual(d["batiments"], [])

    def test_structure_invalide(self):
        with self.assertRaises(ValueError):
            convertir({"pas": "overpass"})


if __name__ == "__main__":
    unittest.main()
