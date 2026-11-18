"""Audit de la carte : détection (pure) des anomalies d'une série de relevés de /trains. Séries fabriquées, aucun réseau."""
import unittest

from audit_carte import VITESSE_MAX_MS, auditer, url_autorisee


def train(course, lat, lon, etat="en route", gare="A", suivante="B", perime=False):
    return {"course": course, "etat": etat, "lat": lat, "lon": lon, "gare": gare, "gare_suivante": suivante, "perime": perime}


def metres_lat(m):
    return m / 111000.0


class TestAudit(unittest.TestCase):
    def test_marche_normale_aucune_anomalie(self):
        serie = [(3 * i, [train("T", 48 + metres_lat(14 * 3 * i), 2.0)]) for i in range(10)]
        r = auditer(serie)
        self.assertEqual((r["sauts"], r["disparitions"], r["immobiles"], r["bascules_perime"]), ([], [], [], []))

    def test_saut_plus_rapide_que_la_vitesse_max(self):
        serie = [(0, [train("T", 48.0, 2.0)]), (3, [train("T", 48.0 + metres_lat(150), 2.0, etat="à quai")])]
        (s,) = auditer(serie)["sauts"]
        self.assertEqual((s["course"], s["t"], s["avant"], s["apres"]), ("T", 3, "en route", "à quai"))
        self.assertAlmostEqual(s["metres"], 150, delta=2)

    def test_deplacement_a_la_vitesse_max_n_est_pas_un_saut(self):
        serie = [(0, [train("T", 48.0, 2.0)]), (3, [train("T", 48.0 + metres_lat(VITESSE_MAX_MS * 3), 2.0)])]
        self.assertEqual(auditer(serie)["sauts"], [])

    def test_trains_non_places_ignores_pour_les_sauts(self):
        serie = [(0, [train("T", None, None, etat="non placé")]), (3, [train("T", 48.0, 2.0)])]
        self.assertEqual(auditer(serie)["sauts"], [])

    def test_disparition_au_terminus_expliquee(self):
        serie = [(0, [train("T", 48.0, 2.0, etat="à quai", gare="Massy", suivante=None)]), (3, [])]
        (d,) = auditer(serie)["disparitions"]
        self.assertEqual((d["course"], d["cause"]), ("T", "terminus probable"))

    def test_disparition_en_route_sortie_de_la_liste(self):
        serie = [(0, [train("T", 48.0, 2.0)]), (3, [])]
        self.assertEqual(auditer(serie)["disparitions"][0]["cause"], "sortie de la liste PRIM")

    def test_train_qui_reste_mais_n_est_plus_dessine(self):
        serie = [(0, [train("T", 48.0, 2.0)]), (3, [train("T", None, None, etat="terminé", suivante=None)])]
        (d,) = auditer(serie)["disparitions"]
        self.assertEqual(d["cause"], "terminé (plus dessiné)")

    def test_train_immobile_en_route(self):
        serie = [(t, [train("T", 48.0, 2.0)]) for t in range(0, 300, 3)]
        (i,) = auditer(serie, immobile_max_s=120)["immobiles"]
        self.assertEqual((i["course"], i["etat"]), ("T", "en route"))
        self.assertGreaterEqual(i["duree_s"], 120)

    def test_arret_a_quai_normal_pas_signale(self):
        serie = [(t, [train("T", 48.0, 2.0, etat="à quai")]) for t in range(0, 90, 3)]
        self.assertEqual(auditer(serie, immobile_max_s=120)["immobiles"], [])

    def test_bascule_perime(self):
        serie = [(0, [train("T", 48.0, 2.0)]), (3, [train("T", 48.0, 2.0, perime=True)])]
        (b,) = auditer(serie)["bascules_perime"]
        self.assertEqual((b["course"], b["t"], b["perime"]), ("T", 3, True))

    def test_url_locale_seulement(self):
        self.assertTrue(url_autorisee("http://127.0.0.1:8765/trains"))
        self.assertTrue(url_autorisee("http://localhost:8765/trains"))
        for u in ("https://prim.iledefrance-mobilites.fr/x", "http://127.0.0.1.evil.com/trains", "file:///etc/passwd", "http://[::1]:1/"):
            self.assertFalse(url_autorisee(u), u)


if __name__ == "__main__":
    unittest.main()
