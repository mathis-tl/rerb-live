"""Fond plat de toute la ligne (21/11) : cellules du couloir élargi, requête Overpass, classement, simplification. Données inventées, aucun réseau."""
import unittest

from fond_plat import cellules_du_couloir, requete_cellule, convertir, simplifier, decouper


class TestCellules(unittest.TestCase):
    def test_cellule_d_un_point_du_trace_incluse_et_lointaine_exclue(self):
        cles = cellules_du_couloir([(2.30, 48.80)], marge_m=3000, taille_deg=0.05)
        self.assertIn("46_976", cles)                 # floor(2.30/0.05)=46, floor(48.80/0.05)=976
        self.assertNotIn("60_976", cles)

    def test_la_marge_ajoute_les_cellules_voisines(self):
        seul = cellules_du_couloir([(2.275, 48.825)], marge_m=100, taille_deg=0.05)
        large = cellules_du_couloir([(2.275, 48.825)], marge_m=4000, taille_deg=0.05)
        self.assertEqual(len(seul), 1)
        self.assertGreaterEqual(len(large), 4)
        self.assertTrue(seul <= large)

    def test_pas_de_doublon_entre_points_voisins(self):
        cles = cellules_du_couloir([(2.30, 48.80), (2.3001, 48.8001)], marge_m=3000, taille_deg=0.05)
        self.assertEqual(len(cles), len(set(cles)))


class TestRequete(unittest.TestCase):
    def test_boite_de_la_cellule_et_filtres(self):
        q = requete_cellule("46_976", 0.05)
        self.assertIn("[bbox:48.8,2.3,48.85,2.35]", q)
        self.assertIn('way["highway"', q)
        self.assertIn("out geom", q)
        self.assertNotIn("building", q, "pas de bâtiments dans le fond plat")


def _voie(id_, tags, pts):
    return {"type": "way", "id": id_, "tags": tags, "geometry": [{"lon": x, "lat": y} for x, y in pts]}


CARRE = [(2.301, 48.801), (2.302, 48.801), (2.302, 48.802), (2.301, 48.802), (2.301, 48.801)]


class TestConvertir(unittest.TestCase):
    def test_classement_par_famille(self):
        rep = {"elements": [
            _voie(1, {"highway": "primary"}, [(2.31, 48.81), (2.32, 48.81)]),
            _voie(2, {"highway": "tertiary"}, [(2.31, 48.82), (2.32, 48.82)]),
            _voie(3, {"leisure": "park"}, CARRE),
            _voie(4, {"natural": "water"}, CARRE),
            _voie(5, {"waterway": "river"}, [(2.31, 48.83), (2.32, 48.83)]),
            _voie(6, {"landuse": "residential"}, CARRE),
        ]}
        f = convertir(rep, "46_976", 0.05)
        self.assertEqual([r["t"] for r in f["routes"]], ["grand", "moyen"])
        self.assertEqual(len(f["verts"]), 1)
        self.assertEqual(len(f["eaux"]), 1)
        self.assertEqual(len(f["cours_eau"]), 1)
        self.assertEqual(len(f["zones"]), 1)

    def test_objet_garde_seulement_dans_la_cellule_de_son_premier_point(self):
        rep = {"elements": [_voie(1, {"leisure": "park"}, CARRE)]}
        self.assertEqual(len(convertir(rep, "46_976", 0.05)["verts"]), 1)
        self.assertEqual(len(convertir(rep, "47_976", 0.05)["verts"]), 0, "sinon un grand parc serait dessiné deux fois")

    def test_ligne_ouverte_n_est_pas_une_surface(self):
        rep = {"elements": [_voie(1, {"leisure": "park"}, [(2.301, 48.801), (2.302, 48.802)])]}
        self.assertEqual(convertir(rep, "46_976", 0.05)["verts"], [])

    def test_relation_d_eau_prend_ses_contours_exterieurs(self):
        rep = {"elements": [{"type": "relation", "id": 9, "tags": {"natural": "water"}, "members": [
            {"role": "outer", "geometry": [{"lon": x, "lat": y} for x, y in CARRE]},
            {"role": "inner", "geometry": [{"lon": x, "lat": y} for x, y in CARRE]}]}]}
        self.assertEqual(len(convertir(rep, "46_976", 0.05)["eaux"]), 1)

    def test_elements_inconnus_ignores(self):
        rep = {"elements": [_voie(1, {"highway": "footway"}, [(2.30, 48.80), (2.31, 48.80)]), {"type": "node", "id": 2}]}
        f = convertir(rep, "46_976", 0.05)
        self.assertEqual(sum(len(v) for v in f.values()), 0)

    def test_coordonnees_arrondies(self):
        rep = {"elements": [_voie(1, {"highway": "primary"}, [(2.3100049, 48.8100051), (2.32, 48.81)])]}
        self.assertEqual(convertir(rep, "46_976", 0.05)["routes"][0]["c"][0], [2.31, 48.81001])


class TestSimplifier(unittest.TestCase):
    def test_points_alignes_retires_extremites_gardees(self):
        ligne = [[2.0, 48.0], [2.0001, 48.0], [2.0002, 48.0], [2.0003, 48.0]]
        self.assertEqual(simplifier(ligne, 1e-5), [[2.0, 48.0], [2.0003, 48.0]])

    def test_coude_conserve(self):
        ligne = [[2.0, 48.0], [2.001, 48.0], [2.001, 48.001]]
        self.assertEqual(simplifier(ligne, 1e-5), ligne)

    def test_surface_fermee_reste_fermee(self):
        s = simplifier(CARRE and [list(p) for p in CARRE], 1e-6)
        self.assertEqual(s[0], s[-1])


class TestDecouper(unittest.TestCase):
    def test_une_cellule_vide_n_existe_pas(self):
        vide = {"verts": [], "eaux": [], "zones": [], "routes": [], "cours_eau": []}
        self.assertEqual(decouper({"46_976": vide}), {})
        plein = dict(vide, verts=[[[2.3, 48.8]]])
        self.assertEqual(list(decouper({"46_976": plein, "47_976": vide})), ["46_976"])


if __name__ == "__main__":
    unittest.main()
