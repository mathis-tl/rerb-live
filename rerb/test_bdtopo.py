"""T2 étape 2 : appariement bâtiments OSM / BD TOPO par recouvrement surfacique et priorité des hauteurs.
Données inventées, aucun fichier IGN, aucun réseau."""
import struct
import unittest

from bdtopo import (enrichir, croiser, dedoublonner, lambert93, decoder_geometrie, aire_polygone, IndexIGN, apparier, choisir_hauteur, taux_recouvrement)


def carre(x, y, c):
    return [[(x, y), (x + c, y), (x + c, y + c), (x, y + c), (x, y)]]


def ign(poly, h, e=None):
    return {"parts": [poly], "h": h, "e": e}


def wkb_polygone(anneaux, z=False, ordre="<"):
    t = 1003 if z else 3
    s = struct.pack(ordre + "BI", 1 if ordre == "<" else 0, t) + struct.pack(ordre + "I", len(anneaux))
    for a in anneaux:
        s += struct.pack(ordre + "I", len(a))
        for p in a:
            s += struct.pack(ordre + ("ddd" if z else "dd"), *(p + ((5.0,) if z else ())))
    return s


def wkb_multi(polys, z=False):
    t = 1006 if z else 6
    s = struct.pack("<BI", 1, t) + struct.pack("<I", len(polys))
    for p in polys:
        s += wkb_polygone(p, z)
    return s


class TestLambert(unittest.TestCase):
    def test_origine(self):
        x, y = lambert93(3.0, 46.5)
        self.assertAlmostEqual(x, 700000.0, delta=0.01)
        self.assertAlmostEqual(y, 6600000.0, delta=0.01)

    def test_paris(self):
        x, y = lambert93(2.3522, 48.8566)       # Paris : ordre de grandeur connu (652 469 ; 6 862 036)
        self.assertAlmostEqual(x, 652469, delta=60)
        self.assertAlmostEqual(y, 6862036, delta=60)

    def test_distance_conservee(self):
        x0, y0 = lambert93(2.30, 48.80)
        x1, y1 = lambert93(2.30, 48.81)          # 0,01° de latitude ≈ 1 112 m
        self.assertAlmostEqual(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5, 1112, delta=10)


class TestDecodage(unittest.TestCase):
    def blob(self, wkb, flags=0x05, srs=2154):
        env = struct.pack("<6d", 0, 1, 0, 1, 0, 1) if flags == 0x05 else b""
        return b"GP" + bytes([0, flags]) + struct.pack("<i", srs) + env + wkb

    def test_multipolygone_z_avec_enveloppe(self):
        g = decoder_geometrie(self.blob(wkb_multi([carre(0, 0, 10)], z=True)))
        self.assertEqual(len(g), 1)
        self.assertAlmostEqual(aire_polygone(g[0]), 100.0)

    def test_polygone_2d_sans_enveloppe(self):
        g = decoder_geometrie(self.blob(wkb_polygone(carre(0, 0, 4)), flags=0x01))
        self.assertAlmostEqual(aire_polygone(g[0]), 16.0)

    def test_deux_parties(self):
        g = decoder_geometrie(self.blob(wkb_multi([carre(0, 0, 2), carre(10, 10, 3)])))
        self.assertEqual(sorted(aire_polygone(p) for p in g), [4.0, 9.0])

    def test_polygone_sans_anneau_ou_anneau_vide_refuse(self):
        for anneaux in ([], [[]]):
            with self.subTest(anneaux=anneaux):
                with self.assertRaises(ValueError):
                    decoder_geometrie(self.blob(wkb_polygone(anneaux)))

    def test_vide_ou_inconnu(self):
        self.assertIsNone(decoder_geometrie(self.blob(b"", flags=0x01 | 0x10)))        # bit « vide »
        self.assertIsNone(decoder_geometrie(b"XX\x00\x01"))
        self.assertIsNone(decoder_geometrie(None))
        with self.assertRaises(ValueError):
            decoder_geometrie(self.blob(struct.pack("<BI", 1, 99) + b"\0" * 8))


class TestAire(unittest.TestCase):
    def test_trou_retire(self):
        p = carre(0, 0, 10) + [[(2, 2), (4, 2), (4, 4), (2, 4), (2, 2)]]
        self.assertAlmostEqual(aire_polygone(p), 96.0)

    def test_sens_indifferent(self):
        self.assertAlmostEqual(aire_polygone([[(0, 0), (0, 3), (3, 3), (3, 0), (0, 0)]]), 9.0)


class TestRecouvrement(unittest.TestCase):
    def test_decalage_moitie(self):
        a = carre(0, 0, 10)
        idx = IndexIGN([ign(carre(5, 0, 10), 12.0)])
        (i, aire_i), = taux_recouvrement(a, idx)
        self.assertAlmostEqual(aire_i, 50.0, delta=3.0)

    def test_forme_en_L_centroide_hors_batiment(self):
        # L de 10 x 10 dont le coin 5..10 x 5..10 est vide : le centroïde (4,17 ; 4,17) est dedans, mais on teste l'aire
        L = [[(0, 0), (10, 0), (10, 5), (5, 5), (5, 10), (0, 10), (0, 0)]]
        idx = IndexIGN([ign(carre(5, 5, 5), 20.0)])
        self.assertEqual(taux_recouvrement(L, idx), [])        # le carré voisin ne touche pas le L

    def test_trou_du_batiment_ign_non_compte(self):
        b = carre(0, 0, 10) + [[(3, 3), (7, 3), (7, 7), (3, 7), (3, 3)]]
        idx = IndexIGN([ign(b, 9.0)])
        (i, aire_i), = taux_recouvrement(carre(0, 0, 10), idx)
        self.assertAlmostEqual(aire_i, 84.0, delta=4.0)


class TestApparier(unittest.TestCase):
    def test_un_pour_un(self):
        r, raison = apparier(carre(0, 0, 10), IndexIGN([ign(carre(0.5, 0.5, 10), 14.0, 4)]))
        self.assertIsNone(raison)
        self.assertAlmostEqual(r["hauteur"], 14.0)
        self.assertAlmostEqual(r["etages"], 4.0)
        self.assertGreater(r["couverture"], 0.8)

    def test_un_pour_plusieurs_moyenne_ponderee_par_aire(self):
        # A (20 x 10) recouvre B1 (10 x 10, 30 m) et B2 (5 x 10, 10 m) : (100*30 + 50*10) / 150 = 23,33
        idx = IndexIGN([ign(carre(0, 0, 10), 30.0), ign([[(10, 0), (15, 0), (15, 10), (10, 10), (10, 0)]], 10.0)])
        A = [[(0, 0), (20, 0), (20, 10), (0, 10), (0, 0)]]
        r, raison = apparier(A, idx)
        self.assertIsNone(raison)
        self.assertAlmostEqual(r["hauteur"], 23.33, delta=1.0)
        self.assertEqual(r["n"], 2)

    def test_plusieurs_osm_dans_un_grand_ign(self):
        idx = IndexIGN([ign(carre(0, 0, 100), 25.0)])
        for x in (10, 50):
            r, _ = apparier(carre(x, 10, 10), idx)
            self.assertAlmostEqual(r["hauteur"], 25.0)

    def test_recouvrement_insuffisant_refuse(self):
        r, raison = apparier(carre(0, 0, 10), IndexIGN([ign(carre(8, 8, 10), 14.0)]))   # 4 % de A
        self.assertIsNone(r)
        self.assertEqual(raison, "recouvrement_insuffisant")

    def test_sans_candidat(self):
        r, raison = apparier(carre(0, 0, 10), IndexIGN([ign(carre(500, 500, 10), 14.0)]))
        self.assertEqual((r, raison), (None, "sans_candidat"))

    def test_hauteur_absente_ou_absurde_ecartee(self):
        for h in (None, 0.0, -3.0, 900.0):
            r, raison = apparier(carre(0, 0, 10), IndexIGN([ign(carre(0, 0, 10), h)]))
            self.assertEqual((r, raison), (None, "sans_hauteur"), h)

    def test_hauteur_absente_mais_etages_presents(self):
        r, raison = apparier(carre(0, 0, 10), IndexIGN([ign(carre(0, 0, 10), None, 5)]))
        self.assertIsNone(raison)
        self.assertIsNone(r["hauteur"])
        self.assertAlmostEqual(r["etages"], 5.0)

    def test_annexe_ne_donne_pas_sa_hauteur_a_la_maison(self):
        # petite annexe 3 m (10 % de A) + maison en face sans hauteur : pas assez de surface cotée
        idx = IndexIGN([ign(carre(0, 0, 3), 3.0), ign(carre(3, 0, 7), None)])
        r, raison = apparier(carre(0, 0, 10), idx)
        self.assertIsNone(r)
        self.assertEqual(raison, "sans_hauteur")


class TestPriorite(unittest.TestCase):
    def test_ordre(self):
        ign_ok = {"hauteur": 20.0, "etages": 4.0}
        self.assertEqual(choisir_hauteur(12.0, "osm", ign_ok, 9.0, "defaut"), (12.0, "osm"))
        self.assertEqual(choisir_hauteur(15.0, "etages", ign_ok, 9.0, "defaut"), (20.0, "ign"))
        self.assertEqual(choisir_hauteur(9.0, "defaut", ign_ok, 9.0, "defaut"), (20.0, "ign"))

    def test_etages_ign_si_pas_de_hauteur(self):
        self.assertEqual(choisir_hauteur(9.0, "defaut", {"hauteur": None, "etages": 5.0}, 9.0, "defaut"), (15.0, "etages"))
        self.assertEqual(choisir_hauteur(18.0, "etages", {"hauteur": None, "etages": 5.0}, 9.0, "defaut"), (18.0, "etages"))

    def test_sans_appariement_garde_osm(self):
        self.assertEqual(choisir_hauteur(9.0, "defaut", None, 9.0, "defaut"), (9.0, "defaut"))
        self.assertEqual(choisir_hauteur(15.0, "etages", None, 9.0, "defaut"), (15.0, "etages"))

    def test_etages_ign_absurdes(self):
        self.assertEqual(choisir_hauteur(9.0, "defaut", {"hauteur": None, "etages": 0.0}, 9.0, "defaut"), (9.0, "defaut"))
        self.assertEqual(choisir_hauteur(9.0, "defaut", {"hauteur": None, "etages": 200.0}, 9.0, "defaut"), (9.0, "defaut"))


class TestCroiser(unittest.TestCase):
    def test_projection_puis_appariement(self):
        # bâtiment OSM de ~10 m autour de (2,3 ; 48,8) et son jumeau IGN en Lambert 93
        lon, lat = 2.30, 48.80
        osm = {"p": [[lon, lat], [lon + 0.00014, lat], [lon + 0.00014, lat + 0.00009], [lon, lat + 0.00009], [lon, lat]]}
        pts = [lambert93(*p) for p in osm["p"]]
        res = croiser([osm, {"p": [[3.0, 46.5], [3.0001, 46.5], [3.0001, 46.5001], [3.0, 46.5001], [3.0, 46.5]]}],
                      [{"h": 17.0, "e": 5, "parts": [[pts]]}])
        self.assertAlmostEqual(res[0][0]["hauteur"], 17.0)
        self.assertEqual(res[1], (None, "sans_candidat"))

    def test_enrichir_applique_la_priorite_et_compte(self):
        def bat(lon, lat, h, src):
            return {"p": [[lon, lat], [lon + 0.00014, lat], [lon + 0.00014, lat + 0.00009], [lon, lat + 0.00009], [lon, lat]], "h": h, "h_src": src}
        a, b, c, d = bat(2.30, 48.80, 9.0, "defaut"), bat(2.31, 48.80, 12.0, "osm"), bat(2.32, 48.80, 15.0, "etages"), bat(2.33, 48.80, 9.0, "defaut")
        lots = [{"h": h, "e": None, "parts": [[[lambert93(*p) for p in x["p"]]]]} for h, x in ((17.123, a), (30.0, b), (21.0, c))]
        comptes = enrichir([a, b, c, d], lots)
        self.assertEqual((a["h"], a["h_src"]), (17.1, "ign"))
        self.assertEqual((b["h"], b["h_src"]), (12.0, "osm"))
        self.assertEqual((c["h"], c["h_src"]), (21.0, "ign"))
        self.assertEqual((d["h"], d["h_src"]), (9.0, "defaut"))
        self.assertEqual(comptes["h_src"], {"ign": 2, "osm": 1, "defaut": 1})
        self.assertEqual(comptes["refus_sans_candidat"], 1)
        self.assertEqual(comptes["changes"], {"defaut->ign": 1, "etages->ign": 1})

    def test_dedoublonner(self):
        lot = {"h": 3.0, "e": None, "parts": [[[[0, 0], [1, 0], [1, 1], [0, 0]]]]}      # comme après lecture JSON : points en listes
        self.assertEqual(len(dedoublonner([lot, dict(lot), {**lot, "h": 4.0}])), 2)


if __name__ == "__main__":
    unittest.main()
