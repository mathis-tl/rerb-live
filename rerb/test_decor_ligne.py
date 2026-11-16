"""Décor de toute la ligne : couloir autour du tracé, requêtes Overpass, fusion, cellules. Données inventées, aucun réseau."""
import unittest

from decor_ligne import Couloir, echantillonner, requetes, fusionner, cellule_de, decouper_cellules


class TestEchantillonner(unittest.TestCase):
    def test_pas_regulier_sur_une_ligne_droite(self):
        trace = [[[2.0, 48.0], [2.0, 48.01]]]            # ~1 112 m vers le nord
        pts = echantillonner(trace, pas_m=250)
        self.assertGreaterEqual(len(pts), 5)
        self.assertLessEqual(len(pts), 7)
        self.assertEqual(pts[0], (2.0, 48.0))

    def test_morceaux_qui_se_recouvrent_sans_doublons(self):
        a = [[2.0, 48.0], [2.0, 48.01]]
        pts = echantillonner([a, a, list(reversed(a))], pas_m=250)
        self.assertEqual(len(pts), len(set(pts)))
        self.assertEqual(len(pts), len(echantillonner([a], pas_m=250)))

    def test_morceaux_decales_de_phase_ne_doublent_pas_les_points(self):
        a = [[2.0, 48.0], [2.0, 48.02]]
        b = [[2.00005, 48.00040], [2.00005, 48.02]]      # même voie à ~5 m, échantillonnée avec une autre phase
        seul = echantillonner([a], pas_m=250)
        double = echantillonner([a, b], pas_m=250)
        self.assertLessEqual(len(double), len(seul) + 2)

    def test_trace_vide(self):
        self.assertEqual(echantillonner([], 250), [])

    def test_pas_invalide(self):
        with self.assertRaises(ValueError):
            echantillonner([[[2.0, 48.0], [2.0, 48.01]]], pas_m=0)


class TestRequetes(unittest.TestCase):
    def test_decoupe_en_paquets_avec_chevauchement(self):
        pts = [(2.0 + i * 0.001, 48.0) for i in range(70)]
        r = requetes(pts, rayon_m=350, par_requete=30)
        self.assertEqual(len(r), 3)
        self.assertTrue(all(q.startswith("[out:json]") and "out geom" in q for q in r))
        self.assertIn("around:350,", r[0])

    def test_coupe_aux_grands_ecarts(self):
        # deux tronçons de 5 points séparés de ~11 km : jamais de couloir tracé entre les deux
        a = [(2.0 + i * 0.002, 48.0) for i in range(5)]
        b = [(2.0 + i * 0.002, 48.1) for i in range(5)]
        r = requetes(a + b, rayon_m=350, par_requete=30, ecart_max_m=700)
        self.assertEqual(len(r), 2)
        self.assertNotIn("48.0,2.008,48.1,2.0", r[0] + r[1])

    def test_un_seul_point_ne_plante_pas(self):
        self.assertEqual(len(requetes([(2.0, 48.0)], 350, 30)), 1)

    def test_aucun_point(self):
        self.assertEqual(requetes([], 350, 30), [])

    def test_coordonnees_dans_l_ordre_lat_lon(self):
        q = requetes([(2.25, 48.75), (2.251, 48.751)], 350, 30)[0]
        self.assertIn("350,48.75,2.25,48.751,2.251", q)


class TestCouloir(unittest.TestCase):
    def setUp(self):
        # voie droite plein nord, 2 km, points tous les 250 m
        self.c = Couloir(echantillonner([[[2.0, 48.0], [2.0, 48.018]]], 250), rayon_m=350)

    def test_sur_la_voie(self):
        self.assertTrue(self.c.contient(2.0, 48.009))

    def test_a_300_m_dedans_a_500_m_dehors(self):
        self.assertTrue(self.c.contient(2.0 + 300 / 74000, 48.009))     # ~300 m a l'est
        self.assertFalse(self.c.contient(2.0 + 500 / 74000, 48.009))

    def test_au_dela_des_extremites(self):
        self.assertFalse(self.c.contient(2.0, 48.03))
        self.assertTrue(self.c.contient(2.0, 48.0 - 100 / 111000))

    def test_loin(self):
        self.assertFalse(self.c.contient(3.0, 49.0))

    def test_couloir_vide(self):
        self.assertFalse(Couloir([], 350).contient(2.0, 48.0))

    def test_une_geometrie_dans_le_couloir_des_qu_un_sommet_y_est(self):
        self.assertTrue(self.c.touche([(2.01, 48.009), (2.0 + 200 / 74000, 48.009)]))
        self.assertFalse(self.c.touche([(2.01, 48.009), (2.02, 48.009)]))
        self.assertFalse(self.c.touche([]))


class TestFusionner(unittest.TestCase):
    def test_doublons_par_type_et_id(self):
        a = {"elements": [{"type": "way", "id": 1}, {"type": "way", "id": 2}]}
        b = {"elements": [{"type": "way", "id": 2}, {"type": "relation", "id": 2}]}
        f = fusionner([a, b])
        self.assertEqual(len(f["elements"]), 3)

    def test_reponse_invalide(self):
        with self.assertRaises(ValueError):
            fusionner([{"x": 1}])


class TestCellules(unittest.TestCase):
    def test_cle_stable(self):
        self.assertEqual(cellule_de(2.2588, 48.7255, 0.02), "112_2436")
        self.assertEqual(cellule_de(2.2588, 48.7255, 0.02), cellule_de(2.2599, 48.7299, 0.02))
        self.assertNotEqual(cellule_de(2.2588, 48.7255, 0.02), cellule_de(2.2788, 48.7255, 0.02))

    def test_decoupage_garde_tout(self):
        decor = {"batiments": [{"p": [[2.0, 48.0], [2.001, 48.0], [2.001, 48.001], [2.0, 48.0]], "h": 9.0},
                               {"p": [[2.5, 48.5], [2.501, 48.5], [2.501, 48.501], [2.5, 48.5]], "h": 9.0}],
                 "routes": [{"c": [[2.0, 48.0], [2.1, 48.0]], "t": "petit"}], "rails": [], "quais": [], "verts": [],
                 "eaux": [], "cours_eau": [], "source": "x"}
        cells = decouper_cellules(decor, 0.02)
        self.assertEqual(sum(len(c["batiments"]) for c in cells.values()), 2)
        self.assertEqual(sum(len(c["routes"]) for c in cells.values()), 1)
        self.assertEqual(len(cells), 2)       # la route commence dans la cellule du 1er bâtiment

    def test_cellules_vides_non_ecrites(self):
        cells = decouper_cellules({"batiments": [], "routes": [], "rails": [], "quais": [], "verts": [], "eaux": [], "cours_eau": []}, 0.02)
        self.assertEqual(cells, {})


if __name__ == "__main__":
    unittest.main()
