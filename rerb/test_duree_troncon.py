"""Tâche 4, T1 : durée minimale plausible par tronçon (gares voisines). Fonctions pures, données inventées.

Observé : départ de A (VehicleAtStop vrai puis faux), arrivée à B (listé sans être à quai, puis à quai) ;
chaque événement est un intervalle [bas, haut] entre deux relevés, on prend le milieu.
"""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from duree_troncon import (Observation, apparier_troncons, arrivees_observees, centile, classer, cle_troncon,
                           duree_min_plausible, fournisseur_duree_min, table_duree_min)


def h(s):
    return datetime(2026, 11, 16, 10, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=s)


class TestCentile(unittest.TestCase):
    def test_bornes_et_milieu(self):
        v = [5, 1, 3, 2, 4]
        self.assertEqual(centile(v, 0), 1)
        self.assertEqual(centile(v, 50), 3)
        self.assertEqual(centile(v, 100), 5)

    def test_interpolation_lineaire(self):
        self.assertAlmostEqual(centile([1, 2, 3, 4, 5], 10), 1.4)

    def test_vide(self):
        self.assertIsNone(centile([], 10))


class TestArrivees(unittest.TestCase):
    def test_arrivee_vue_en_route_puis_a_quai(self):
        rel = [(h(0), {"c"}, set()), (h(30), {"c"}, {"c"})]
        arr, refus = arrivees_observees(rel)
        self.assertEqual(arr, [("c", h(0), h(30))])
        self.assertEqual(sum(refus.values()), 0)

    def test_deja_a_quai_pas_d_arrivee(self):
        rel = [(h(0), {"c"}, {"c"}), (h(30), {"c"}, {"c"})]
        self.assertEqual(arrivees_observees(rel)[0], [])

    def test_apparu_directement_a_quai_refuse_et_compte(self):
        rel = [(h(0), set(), set()), (h(30), {"c"}, {"c"})]
        arr, refus = arrivees_observees(rel)
        self.assertEqual(arr, [])
        self.assertEqual(refus["arrivée non vue avant (train apparu à quai)"], 1)

    def test_trou_de_releves_refuse_et_compte(self):
        rel = [(h(0), {"c"}, set()), (h(200), {"c"}, {"c"})]
        arr, refus = arrivees_observees(rel)
        self.assertEqual(arr, [])
        self.assertEqual(refus["trou de relevés trop long"], 1)


def obs(duree, de="1", vers="2", course="c", incert=30.0, annonce=None):
    return Observation(course, de, vers, float(duree), incert, h(0), annonce)


class TestApparier(unittest.TestCase):
    voisin = staticmethod(lambda a, b: {a, b} == {"1", "2"} or {a, b} == {"2", "3"})

    def test_depart_puis_arrivee_voisine(self):
        deps = [("1", "c", h(0), h(30))]      # milieu 15
        arrs = [("2", "c", h(90), h(120))]    # milieu 105
        o, refus = apparier_troncons(deps, arrs, self.voisin)
        self.assertEqual(len(o), 1)
        self.assertEqual((o[0].de, o[0].vers, o[0].duree_s), ("1", "2", 90.0))
        self.assertEqual(o[0].incertitude_s, 30.0)   # (30 + 30) / 2
        self.assertEqual(sum(refus.values()), 0)

    def test_arrivee_non_voisine_refusee(self):
        deps = [("1", "c", h(0), h(30))]
        arrs = [("3", "c", h(200), h(230))]
        o, refus = apparier_troncons(deps, arrs, self.voisin)
        self.assertEqual(o, [])
        self.assertEqual(refus["arrivée à une gare non voisine (gare sautée ou arrivée manquée)"], 1)

    def test_sans_arrivee_refuse(self):
        o, refus = apparier_troncons([("1", "c", h(0), h(30))], [], self.voisin)
        self.assertEqual(o, [])
        self.assertEqual(refus["aucune arrivée observée ensuite"], 1)

    def test_autre_course_ignoree(self):
        deps = [("1", "c", h(0), h(30))]
        arrs = [("2", "autre", h(90), h(120))]
        o, refus = apparier_troncons(deps, arrs, self.voisin)
        self.assertEqual(o, [])

    def test_arrivee_manquee_puis_depart_suivant(self):
        # départ 1, arrivée à 2 manquée, départ de 2 observé : le prochain événement n'est pas une arrivée
        deps = [("1", "c", h(0), h(30)), ("2", "c", h(200), h(230))]
        arrs = [("3", "c", h(400), h(430))]
        o, refus = apparier_troncons(deps, arrs, self.voisin)
        self.assertEqual(refus["arrivée manquée (départ suivant observé avant)"], 1)
        self.assertEqual(len(o), 1)                 # 2 -> 3 est valide
        self.assertEqual((o[0].de, o[0].vers), ("2", "3"))


class TestDureeMin(unittest.TestCase):
    def test_peu_d_observations_plancher(self):
        v, src, n = duree_min_plausible([100.0, 110.0], plancher_s=43.0)
        self.assertEqual((v, src, n), (43.0, "plancher", 2))

    def test_assez_d_observations_centile(self):
        valeurs = [100.0, 110.0, 120.0, 130.0, 140.0, 150.0]
        v, src, n = duree_min_plausible(valeurs, plancher_s=43.0, centile_p=10, n_min=5)
        self.assertAlmostEqual(v, 105.0)
        self.assertEqual((src, n), ("estimée", 6))

    def test_jamais_sous_le_plancher(self):
        v, src, _ = duree_min_plausible([20.0] * 6, plancher_s=43.0)
        self.assertEqual((v, src), (43.0, "plancher"))


class TestClasser(unittest.TestCase):
    # médiane observée du tronçon 100 s, durée minimale plausible 90 s, plancher 43 s
    def c(self, observe, annonce, incert=20.0):
        return classer(Observation("c", "1", "2", observe, incert, h(0), annonce), 90.0, 43.0, 100.0)

    def test_normal(self):
        self.assertEqual(self.c(100, 100), "normal")

    def test_observation_impossible(self):
        self.assertEqual(self.c(10, 100, incert=20), "observation impossible")
        self.assertEqual(self.c(40, 100, incert=60), "observation impossible")   # milieu sous le plancher de 43 s

    def test_annonce_trop_courte_observe_normal_est_erreur_de_donnee(self):
        self.assertEqual(self.c(100, 30), "erreur de donnée (annoncé trop court, observé normal)")

    def test_annonce_trop_courte_observe_long_est_ambigu(self):
        self.assertEqual(self.c(200, 30), "ambigu (annoncé trop court, observé long)")

    def test_observe_long_et_annonce_longue_ralentissement(self):
        self.assertEqual(self.c(200, 200), "ralentissement annoncé")

    def test_observe_long_annonce_normale(self):
        self.assertEqual(self.c(200, 100), "ralentissement non annoncé")

    def test_annonce_inconnue(self):
        self.assertEqual(self.c(100, None), "annonce non lue")


class TestTable(unittest.TestCase):
    def test_cle_sans_orientation(self):
        self.assertEqual(cle_troncon("2", "1"), cle_troncon("1", "2"))

    def test_trop_d_observations_impossibles_neutralise_le_minimum(self):
        # plancher 1000/33 = 30,3 s ; 4 impossibles (10 s) + 6 valides = 40 % > 30 % : retour au plancher
        dist = {cle_troncon("1", "2"): 1000.0}
        bruitees = [obs(10)] * 4 + [obs(d) for d in (100, 110, 120, 130, 140, 150)]
        a = table_duree_min(bruitees, dist, vitesse_ms=33.0)[cle_troncon("1", "2")]
        self.assertEqual(a["source"], "plancher")
        self.assertAlmostEqual(a["duree_min_s"], 1000 / 33.0)
        self.assertAlmostEqual(a["part_impossibles"], 0.4)

    def test_part_impossibles_sous_le_seuil_garde_l_estimation(self):
        # 2 impossibles + 8 valides = 20 % : estimation conservée
        dist = {cle_troncon("1", "2"): 1000.0}
        ok = [obs(10)] * 2 + [obs(d) for d in (100, 110, 120, 130, 140, 150, 160, 170)]
        a = table_duree_min(ok, dist, vitesse_ms=33.0)[cle_troncon("1", "2")]
        self.assertEqual(a["source"], "estimée")
        self.assertAlmostEqual(a["part_impossibles"], 0.2)

    def test_table(self):
        observations = [obs(d) for d in (100, 110, 120, 130, 140, 150)] + [obs(50, de="2", vers="3")]
        dist = {cle_troncon("1", "2"): 1000.0, cle_troncon("2", "3"): 800.0}
        t = table_duree_min(observations, dist, vitesse_ms=33.0)
        a = t[cle_troncon("1", "2")]
        self.assertAlmostEqual(a["duree_min_s"], 105.0)
        self.assertEqual((a["source"], a["n"]), ("estimée", 6))
        b = t[cle_troncon("2", "3")]
        self.assertAlmostEqual(b["duree_min_s"], 800 / 33.0)
        self.assertEqual(b["source"], "plancher")

    def test_observation_sous_le_plancher_exclue_meme_avec_grande_incertitude(self):
        # 30 s pour 1 000 m (plancher 30,3 s) : le milieu est impossible ; l'incertitude de ±40 s ne la rend pas valide
        observations = [obs(30, incert=40)] + [obs(d) for d in (100, 110, 120, 130, 140)]
        a = table_duree_min(observations, {cle_troncon("1", "2"): 1000.0})[cle_troncon("1", "2")]
        self.assertEqual(a["n"], 5)

    def test_observation_impossible_exclue_du_centile(self):
        observations = [obs(5, incert=0)] + [obs(d) for d in (100, 110, 120, 130, 140)]
        dist = {cle_troncon("1", "2"): 1000.0}
        a = table_duree_min(observations, dist)[cle_troncon("1", "2")]
        self.assertEqual(a["n"], 5)    # la 6e (5 s pour 1 000 m) est écartée


class TestFournisseur(unittest.TestCase):
    def ecrire(self, contenu):
        d = tempfile.mkdtemp()
        chemin = f"{d}/t.json"
        with open(chemin, "w", encoding="utf-8") as f:
            f.write(contenu)
        return chemin

    def test_lecture_et_orientation_indifferente(self):
        chemin = self.ecrire(json.dumps({"troncons": {"1|2": {"duree_min_s": 105.0, "source": "estimée", "n": 6}}}))
        f = fournisseur_duree_min(chemin)
        self.assertEqual(f("STIF:StopArea:SP:1:", "STIF:StopArea:SP:2:"), (105.0, "estimée"))
        self.assertEqual(f("STIF:StopArea:SP:2:", "STIF:StopArea:SP:1:"), (105.0, "estimée"))

    def test_troncon_inconnu_ou_reference_illisible(self):
        f = fournisseur_duree_min(self.ecrire(json.dumps({"troncons": {}})))
        self.assertIsNone(f("STIF:StopArea:SP:1:", "STIF:StopArea:SP:9:"))
        self.assertIsNone(f("n'importe quoi", None))

    def test_fichier_absent_ou_illisible_tout_inconnu(self):
        self.assertIsNone(fournisseur_duree_min("/inexistant/x.json")("STIF:StopArea:SP:1:", "STIF:StopArea:SP:2:"))
        self.assertIsNone(fournisseur_duree_min(self.ecrire("pas du json"))("STIF:StopArea:SP:1:", "STIF:StopArea:SP:2:"))

    def test_table_mal_formee_se_replie_sur_le_plancher(self):
        ref = ("STIF:StopArea:SP:1:", "STIF:StopArea:SP:2:")
        for contenu in ({"troncons": []}, {"troncons": {"1|2": 5}}, {"troncons": {"1|2": {"source": "x"}}},
                        {"troncons": {"1|2": {"duree_min_s": "abc", "source": "x"}}},
                        {"troncons": {"1|2": {"duree_min_s": 50}}}):
            with self.subTest(contenu=contenu):
                self.assertIsNone(fournisseur_duree_min(self.ecrire(json.dumps(contenu)))(*ref))


class TestRefusTropLong(unittest.TestCase):
    def test_arrivee_apres_plus_de_15_min_refusee(self):
        o, refus = apparier_troncons([("1", "c", h(0), h(30))], [("2", "c", h(2000), h(2030))], lambda a, b: True)
        self.assertEqual(o, [])
        self.assertEqual(refus["arrivée trop tardive (plus de 15 min)"], 1)


if __name__ == "__main__":
    unittest.main()
