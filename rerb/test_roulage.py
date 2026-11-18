"""Tests de rerb/roulage.py : roulage annoncé, théorique, borne physique, comptage par occurrence et par course.

Relevés fabriqués, aucune donnée réelle, aucun réseau.
"""
import unittest
from datetime import datetime, timedelta, timezone

from roulage import (
    Appel,
    bilan_sous_borne,
    borne_du_troncon,
    categorie,
    lire_course,
    lire_origine,
    mediane_theorique,
    observer,
    mediane,
    moitie,
    rapports,
    stabilite,
    roulage_annonce,
    roulage_theorique,
    troncons,
    une_par_course,
)

T0 = datetime(2026, 11, 16, 6, 0, 0, tzinfo=timezone.utc)


def t(s):
    return T0 + timedelta(seconds=s)


def iso(s):
    return (T0 + timedelta(seconds=s)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def appel_ej(gare, arr_est=None, dep_est=None, arr_theo=None, dep_theo=None, statut=None):
    c = {"StopPointRef": {"value": f"STIF:StopArea:SP:{gare}:"}}
    for nom, v in (("ExpectedArrivalTime", arr_est), ("ExpectedDepartureTime", dep_est),
                   ("AimedArrivalTime", arr_theo), ("AimedDepartureTime", dep_theo)):
        if v is not None:
            c[nom] = iso(v)
    if statut:
        c["ArrivalStatus"] = statut
    return c


def ej(ref, calls, forme_dict=True):
    return {"DatedVehicleJourneyRef": {"value": ref},
            "EstimatedCalls": {"EstimatedCall": calls} if forme_dict else calls}


def ap(gare, arr_est=None, dep_est=None, arr_theo=None, dep_theo=None, annule=False):
    return Appel(gare, None if arr_est is None else t(arr_est), None if dep_est is None else t(dep_est),
                 None if arr_theo is None else t(arr_theo), None if dep_theo is None else t(dep_theo), annule)


class TestLireCourse(unittest.TestCase):
    def test_lit_les_quatre_heures_et_la_reference(self):
        ref, appels = lire_course(ej("C1", [appel_ej("1", 10, 40, 12, 42)]))
        self.assertEqual(ref, "C1")
        self.assertEqual(appels, [ap("1", 10, 40, 12, 42)])

    def test_forme_liste_directe(self):
        _, appels = lire_course(ej("C1", [appel_ej("1", 10, 40)], forme_dict=False))
        self.assertEqual(len(appels), 1)

    def test_pas_de_repli_sur_theorique(self):
        # contrairement à position.lire_train : l'estimée absente reste absente
        _, appels = lire_course(ej("C1", [appel_ej("1", arr_theo=5, dep_theo=9)]))
        self.assertIsNone(appels[0].arr_est)
        self.assertIsNone(appels[0].dep_est)
        self.assertEqual(appels[0].dep_theo, t(9))

    def test_gare_illisible_ignoree(self):
        calls = [appel_ej("1", 10, 40), {"StopPointRef": {"value": "STIF:StopPoint:Q:99:"}, "ExpectedArrivalTime": iso(50)}]
        _, appels = lire_course(ej("C1", calls))
        self.assertEqual([a.gare for a in appels], ["1"])

    def test_annule_detecte(self):
        _, appels = lire_course(ej("C1", [appel_ej("1", 10, 40, statut="CANCELLED")]))
        self.assertTrue(appels[0].annule)

    def test_sans_appels(self):
        self.assertEqual(lire_course({"DatedVehicleJourneyRef": {"value": "C1"}})[1], [])


class TestOrigine(unittest.TestCase):
    def test_lire_origine(self):
        self.assertEqual(lire_origine({"OriginRef": {"value": "STIF:StopArea:SP:58774:"}}), "58774")
        self.assertIsNone(lire_origine({}))
        self.assertIsNone(lire_origine({"OriginRef": {"value": "n'importe quoi"}}))

    def test_origine_reelle_prime_sur_la_position_dans_la_liste(self):
        # course en route : la 1re gare listée (A) n'est PAS l'origine ; l'origine réelle est « Z » (déjà passée)
        appels = [ap("A", dep_est=100, dep_theo=90), ap("B", arr_est=200, arr_theo=190)]
        (o,) = observer(t(0), [("C1", appels, "Z")])
        self.assertFalse(o.a_origine)
        (o,) = observer(t(0), [("C1", appels, "A")])
        self.assertTrue(o.a_origine)

    def test_sans_origine_connue_repli_sur_le_premier(self):
        appels = [ap("A", dep_est=100, dep_theo=90), ap("B", arr_est=200, arr_theo=190)]
        (o,) = observer(t(0), [("C1", appels, None)])
        self.assertTrue(o.a_origine)


class TestTroncons(unittest.TestCase):
    def test_tri_theorique_remet_en_ordre(self):
        a, b, c = ap("A", arr_theo=0, dep_theo=30), ap("B", arr_theo=100, dep_theo=130), ap("C", arr_theo=200, dep_theo=230)
        paires = troncons([c, a, b], tri="theorique")
        self.assertEqual([(x.gare, y.gare) for x, y in paires], [("A", "B"), ("B", "C")])

    def test_tri_estime_differe_du_theorique(self):
        # B est théoriquement avant C, mais estimé après C
        a = ap("A", 0, 30, 0, 30)
        b = ap("B", 500, 530, 100, 130)
        c = ap("C", 200, 230, 200, 230)
        theo = [(x.gare, y.gare) for x, y in troncons([a, b, c], tri="theorique")]
        est = [(x.gare, y.gare) for x, y in troncons([a, b, c], tri="estime")]
        self.assertEqual(theo, [("A", "B"), ("B", "C")])
        self.assertEqual(est, [("A", "C"), ("C", "B")])

    def test_appel_sans_cle_de_tri_ecarte_ses_paires(self):
        a, b, c = ap("A", arr_theo=0, dep_theo=30), ap("B"), ap("C", arr_theo=200, dep_theo=230)
        self.assertEqual(troncons([a, b, c], tri="theorique"), [])

    def test_tri_inconnu_refuse(self):
        with self.assertRaises(ValueError):
            troncons([], tri="autre")


class TestDurees(unittest.TestCase):
    def test_roulage_annonce(self):
        self.assertEqual(roulage_annonce(ap("A", dep_est=30), ap("B", arr_est=100)), 70)

    def test_roulage_annonce_manquant(self):
        self.assertIsNone(roulage_annonce(ap("A", arr_est=10), ap("B", arr_est=100)))
        self.assertIsNone(roulage_annonce(ap("A", dep_est=30), ap("B", dep_est=100)))

    def test_roulage_theorique(self):
        self.assertEqual(roulage_theorique(ap("A", dep_theo=30), ap("B", arr_theo=132)), 102)
        self.assertIsNone(roulage_theorique(ap("A"), ap("B", arr_theo=132)))


class TestBorne(unittest.TestCase):
    def test_sens_direct_et_inverse(self):
        bornes = {"A|B": 80.0}
        self.assertEqual(borne_du_troncon(bornes, "A", "B"), 80.0)
        self.assertEqual(borne_du_troncon(bornes, "B", "A"), 80.0)
        self.assertIsNone(borne_du_troncon(bornes, "A", "C"))


def course_ab(ref, dep_a_est, arr_b_est, theo=(0, 100)):
    return (ref, [ap("A", dep_est=dep_a_est, dep_theo=theo[0]), ap("B", arr_est=arr_b_est, arr_theo=theo[1])])


class TestObserver(unittest.TestCase):
    def test_une_observation_par_troncon(self):
        obs = observer(t(0), [course_ab("C1", 300, 360)])
        self.assertEqual(len(obs), 1)
        o = obs[0]
        self.assertEqual((o.course, o.depart_gare, o.arrivee_gare), ("C1", "A", "B"))
        self.assertEqual((o.annonce, o.theorique, o.depart_est, o.recu), (60, 100, t(300), t(0)))

    def test_annule_ecarte_si_demande(self):
        courses = [("C1", [ap("A", dep_est=300, dep_theo=0), ap("B", arr_est=360, arr_theo=100, annule=True)])]
        self.assertEqual(len(observer(t(0), courses)), 1)
        self.assertEqual(observer(t(0), courses, ecarter_annules=True), [])


class TestUneParCourse(unittest.TestCase):
    def test_garde_le_dernier_releve_avant_le_depart_estime(self):
        obs = (observer(t(0), [course_ab("C1", 300, 360)])
               + observer(t(120), [course_ab("C1", 310, 400)])
               + observer(t(240), [course_ab("C1", 330, 420)])
               + observer(t(400), [course_ab("C1", 340, 450)]))  # reçu après le départ estimé : exclu
        r = une_par_course(obs)
        self.assertEqual(len(r["retenues"]), 1)
        self.assertEqual(r["retenues"][0].recu, t(240))
        self.assertEqual(r["retenues"][0].annonce, 90)

    def test_depart_deja_passe_compte_comme_ecarte(self):
        obs = observer(t(500), [course_ab("C1", 300, 360)])
        r = une_par_course(obs)
        self.assertEqual(r["retenues"], [])
        self.assertEqual(r["ecartes"], {"sans_annonce": 0, "depart_deja_passe": 1})

    def test_sans_annonce_compte_a_part(self):
        obs = observer(t(0), [("C1", [ap("A", dep_theo=0), ap("B", arr_theo=100)])])
        r = une_par_course(obs)
        self.assertEqual(r["ecartes"], {"sans_annonce": 1, "depart_deja_passe": 0})

    def test_courses_distinctes_comptees_separement(self):
        obs = observer(t(0), [course_ab("C1", 300, 360), course_ab("C2", 300, 400)])
        self.assertEqual(len(une_par_course(obs)["retenues"]), 2)


class TestBilan(unittest.TestCase):
    bornes = {"A|B": 70.0}

    def test_strictement_sous_la_borne(self):
        obs = observer(t(0), [course_ab("C1", 300, 370), course_ab("C2", 300, 369)])  # 70 s, 69 s
        b = bilan_sous_borne(obs, self.bornes)
        self.assertEqual((b.sous, b.total), (1, 2))

    def test_occurrence_contre_course(self):
        # C1 sous la borne, vue 3 fois ; C2 au-dessus, vue 1 fois
        obs = []
        for s in (0, 120, 240):
            obs += observer(t(s), [course_ab("C1", 500, 540)])  # 40 s
        obs += observer(t(0), [course_ab("C2", 500, 600)])      # 100 s
        occ = bilan_sous_borne(obs, self.bornes)
        crs = bilan_sous_borne(une_par_course(obs)["retenues"], self.bornes)
        self.assertEqual((occ.sous, occ.total), (3, 4))
        self.assertEqual((crs.sous, crs.total), (1, 2))

    def test_par_troncon_et_sens_inverse(self):
        courses = [("C1", [ap("B", dep_est=300), ap("A", arr_est=340)])]  # sens B -> A, 40 s
        b = bilan_sous_borne(observer(t(0), courses, tri="estime"), self.bornes)
        self.assertEqual(b.par_troncon, {("B", "A"): (1, 1)})

    def test_sans_borne_compte_a_part(self):
        courses = [("C1", [ap("A", dep_est=300), ap("Z", arr_est=340)])]
        b = bilan_sous_borne(observer(t(0), courses, tri="estime"), self.bornes)
        self.assertEqual((b.total, b.sans_borne), (0, 1))

    def test_retenus_filtre_les_troncons(self):
        obs = observer(t(0), [course_ab("C1", 300, 340)])
        b = bilan_sous_borne(obs, self.bornes, retenus=set())
        self.assertEqual(b.total, 0)


class TestCategorie(unittest.TestCase):
    def test_seuils(self):
        self.assertEqual(categorie(0.0), "bas")
        self.assertEqual(categorie(0.10), "bas")
        self.assertEqual(categorie(0.50), "mixte")
        self.assertEqual(categorie(0.90), "haut")
        self.assertEqual(categorie(1.0), "haut")


class TestRapport(unittest.TestCase):
    def test_mediane_theorique_une_valeur_par_course(self):
        obs = []
        for s in (0, 120, 240):  # C1 vue 3 fois : compte une fois
            obs += observer(t(s), [course_ab("C1", 500, 600, theo=(0, 100))])
        obs += observer(t(0), [course_ab("C2", 500, 600, theo=(0, 120))])
        med = mediane_theorique(obs, n_min=2)
        self.assertEqual(med[("A", "B")], (110.0, 2))
        self.assertEqual(mediane_theorique(obs, n_min=3), {})

    def test_rapports(self):
        med = {("A", "B"): (110.0, 2), ("C", "D"): (50.0, 9)}
        realistes = {"A|B": 100.0, "D|C": 50.0}
        self.assertEqual(sorted(rapports(med, realistes)), [1.0, 1.1])


class TestStabilite(unittest.TestCase):
    def test_compare_les_troncons_communs(self):
        a = {("A", "B"): "bas", ("B", "A"): "haut", ("C", "D"): "mixte", ("E", "F"): "bas"}
        b = {("A", "B"): "bas", ("B", "A"): "bas", ("C", "D"): "mixte"}
        communs, identiques, differents = stabilite(a, b)
        self.assertEqual((communs, identiques), (3, 2))
        self.assertEqual(differents, [(("B", "A"), "haut", "bas")])

    def test_aucun_commun(self):
        self.assertEqual(stabilite({("A", "B"): "bas"}, {}), (0, 0, []))


class TestMediane(unittest.TestCase):
    def test_mediane(self):
        self.assertEqual(mediane([3.0, 1.0, 2.0]), 2.0)
        self.assertEqual(mediane([1.0, 2.0]), 1.5)
        self.assertNotEqual(mediane([]), mediane([]))  # nan


class TestOrigineEtRetards(unittest.TestCase):
    def test_origine_marquee_sur_le_premier_troncon_seulement(self):
        appels = [ap("A", dep_est=100, dep_theo=90), ap("B", 200, 240, 190, 230), ap("C", arr_est=330, arr_theo=320)]
        o1, o2 = observer(t(0), [("C1", appels)])
        self.assertTrue(o1.a_origine)
        self.assertFalse(o2.a_origine)

    def test_retards_et_arret(self):
        appels = [ap("A", 100, 160, 90, 130), ap("B", arr_est=260, arr_theo=250)]
        (o,) = observer(t(0), [("C1", appels)])
        self.assertEqual(o.retard_dep, 30)       # 160 - 130
        self.assertEqual(o.retard_arr_a, 10)     # 100 - 90
        self.assertEqual(o.retard_arr_b, 10)     # 260 - 250
        self.assertEqual(o.arret_est, 60)        # 160 - 100
        self.assertEqual(o.arret_theo, 40)       # 130 - 90

    def test_origine_sans_arrivee_pas_d_arret(self):
        (o,) = observer(t(0), [("C1", [ap("A", dep_est=100, dep_theo=90), ap("B", arr_est=200, arr_theo=190)])])
        self.assertIsNone(o.arret_est)
        self.assertIsNone(o.retard_arr_a)
        self.assertEqual(o.retard_dep, 10)

    def test_bilan_filtre_origine(self):
        appels = [ap("A", dep_est=300, dep_theo=0), ap("B", 340, 400, 100, 130), ap("C", arr_est=430, arr_theo=250)]
        obs = observer(t(0), [("C1", appels)])      # A->B 40 s (origine), B->C 30 s (intermédiaire)
        bornes = {"A|B": 70.0, "B|C": 70.0}
        self.assertEqual(bilan_sous_borne(obs, bornes, origine=True).total, 1)
        self.assertEqual(bilan_sous_borne(obs, bornes, origine=False).total, 1)
        self.assertEqual(bilan_sous_borne(obs, bornes).total, 2)


class TestMoitie(unittest.TestCase):
    def test_deux_moities_disjointes_et_stables(self):
        obs = []
        for i in range(40):
            obs += observer(t(0), [course_ab(f"C{i}", 300, 360)])
        a, b = moitie(obs, 0), moitie(obs, 1)
        self.assertEqual(len(a) + len(b), 40)
        self.assertFalse({o.course for o in a} & {o.course for o in b})
        self.assertEqual([o.course for o in moitie(obs, 0)], [o.course for o in a])

    def test_une_course_reste_dans_la_meme_moitie(self):
        obs = observer(t(0), [course_ab("C1", 300, 360)]) + observer(t(120), [course_ab("C1", 310, 360)])
        self.assertIn(len(moitie(obs, 0)), (0, 2))

    def test_k_invalide(self):
        with self.assertRaises(ValueError):
            moitie([], 2)


if __name__ == "__main__":
    unittest.main()
