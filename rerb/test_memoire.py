"""Tests de la mémoire M2 (décision P3, 13/11/2026) : garder la dernière heure connue des gares que PRIM retire.

Réparation : le départ mémorisé d'une gare retirée ne peut pas dépasser l'arrivée réestimée à la gare
suivante ; l'heure réparée est marquée (`repare`), toute heure issue de la mémoire est marquée (`reconstruit`).

Décor : A (origine, départ 08:00:00), B (08:03:00 / 08:04:00), C (08:07:00 / 08:08:00), D (terminus, 08:10:00).
Distances inventées : A-B 2 400 m, B-C 3 000 m, C-D 1 000 m.
"""
import unittest
from datetime import datetime, timezone

from memoire import Memoire
from position import DistanceInconnue, Etat, Passage, Train, estimer

DIST = {frozenset(p): m for p, m in (("AB", 2400.0), ("BC", 3000.0), ("CD", 1000.0), ("AC", 5400.0))}


def distance(a, b):
    try:
        return DIST[frozenset((a, b))]
    except KeyError:
        raise DistanceInconnue(f"{a}{b}") from None


def h(hms):
    hh, mm, ss = (int(x) for x in hms.split(":"))
    return datetime(2026, 11, 12, hh, mm, ss, tzinfo=timezone.utc)


def p(gare, arr, dep):
    return Passage(gare, h(arr) if arr else None, h(dep) if dep else None)


A = lambda: p("A", None, "08:00:00")  # noqa: E731
B = lambda: p("B", "08:03:00", "08:04:00")  # noqa: E731
C = lambda: p("C", "08:07:00", "08:08:00")  # noqa: E731
D = lambda: p("D", "08:10:00", None)  # noqa: E731


def train(*passages, recorded="08:00:30"):
    return Train(list(passages), h(recorded))


def par_gare(t):
    return {x.gare: x for x in t.passages}


class TestMemoire(unittest.TestCase):
    def test_m1_premier_releve_inchange(self):
        m = Memoire()
        t = train(A(), B(), C(), D())
        out = m.consolider("k", t)
        self.assertEqual(out.passages, t.passages)
        self.assertFalse(any(x.reconstruit or x.repare for x in out.passages))

    def test_m2_gare_retiree_gardee_et_marquee(self):
        m = Memoire()
        m.consolider("k", train(A(), B(), C(), D()))
        out = m.consolider("k", train(B(), C(), D(), recorded="08:05:00"))
        g = par_gare(out)
        self.assertEqual(sorted(g), ["A", "B", "C", "D"])
        self.assertTrue(g["A"].reconstruit)
        self.assertFalse(g["A"].repare)
        self.assertEqual(g["A"].depart, h("08:00:00"))
        self.assertFalse(g["B"].reconstruit)
        # la fonction retrouve la position sur le 1er tronçon (avant : « position inconnue »)
        e = estimer(out, h("08:01:30"), distance, seuil_perime=h("08:30:00") - h("08:00:00"))
        self.assertEqual((e.etat, e.gare, e.gare_suivante), (Etat.EN_ROUTE, "A", "B"))
        self.assertAlmostEqual(e.distance_m, 1200.0)
        self.assertTrue(e.reconstruit)
        self.assertFalse(e.repare)

    def test_m3_reparation_depart_apres_arrivee_reestimee(self):
        m = Memoire()
        m.consolider("k", train(A(), B(), C(), D()))
        # relevé suivant : A et B retirées ; C réestimée plus tôt (08:03:30) que l'ancien départ de B (08:04:00)
        out = m.consolider("k", train(p("C", "08:03:30", "08:08:00"), D(), recorded="08:05:00"))
        g = par_gare(out)
        self.assertEqual(g["B"].depart, h("08:03:30"))  # ramené à l'arrivée de C
        self.assertTrue(g["B"].repare)
        self.assertTrue(g["B"].reconstruit)
        self.assertEqual(g["B"].arrivee, h("08:03:00"))  # arrivée intacte
        self.assertFalse(g["A"].repare)
        e = estimer(out, h("08:03:10"), distance)  # B : 08:03:00 à 08:03:30
        self.assertEqual((e.etat, e.gare), (Etat.A_QUAI, "B"))
        self.assertTrue(e.reconstruit and e.repare)
        self.assertNotEqual(estimer(out, h("08:01:30"), distance).etat, Etat.NON_PLACE)

    def test_m3b_reparation_en_chaine(self):
        # C réestimée à 08:02:00 : B et A doivent aussi être ramenées en arrière
        m = Memoire()
        m.consolider("k", train(A(), B(), C(), D()))
        out = m.consolider("k", train(p("C", "08:02:00", "08:08:00"), D()))
        g = par_gare(out)
        self.assertEqual(g["B"].depart, h("08:02:00"))
        self.assertEqual(g["B"].arrivee, h("08:02:00"))  # arrivée ramenée au départ réparé
        self.assertEqual(g["A"].depart, h("08:00:00"))   # A n'a pas besoin de réparation
        self.assertTrue(g["B"].repare and not g["A"].repare)
        for instant in ("07:59:00", "08:01:00", "08:02:30", "08:09:00"):
            self.assertNotEqual(estimer(out, h(instant), distance, seuil_perime=h("09:00:00") - h("08:00:00")).etat,
                                Etat.NON_PLACE)

    def test_m4_gares_retirees_sur_plusieurs_releves(self):
        m = Memoire()
        m.consolider("k", train(A(), B(), C(), D()))
        m.consolider("k", train(B(), C(), D()))
        out = m.consolider("k", train(C(), D()))
        g = par_gare(out)
        self.assertEqual(sorted(g), ["A", "B", "C", "D"])
        self.assertTrue(g["A"].reconstruit and g["B"].reconstruit)
        self.assertFalse(g["C"].reconstruit)
        e = estimer(out, h("08:01:30"), distance, seuil_perime=h("09:00:00") - h("08:00:00"))
        self.assertEqual((e.etat, e.gare, e.gare_suivante), (Etat.EN_ROUTE, "A", "B"))

    def test_m5_gare_disparue_au_milieu_est_oubliee(self):
        # le train devient direct : B disparaît entre A et C. Elle n'est pas « retirée après passage ».
        m = Memoire()
        m.consolider("k", train(A(), B(), C(), D()))
        out = m.consolider("k", train(A(), C(), D()))
        self.assertEqual(sorted(par_gare(out)), ["A", "C", "D"])

    def test_m6_aucune_gare_commune_pas_de_memoire(self):
        m = Memoire()
        m.consolider("k", train(A(), B()))
        out = m.consolider("k", train(C(), D()))
        self.assertEqual(sorted(par_gare(out)), ["C", "D"])

    def test_m7_trains_independants(self):
        m = Memoire()
        m.consolider("k1", train(A(), B(), C()))
        m.consolider("k2", train(p("A", None, "09:00:00"), p("B", "09:03:00", "09:04:00")))
        out = m.consolider("k1", train(B(), C()))
        self.assertEqual(par_gare(out)["A"].depart, h("08:00:00"))  # pas celle de k2

    def test_m8_nouvelle_heure_remplace_l_ancienne(self):
        m = Memoire()
        m.consolider("k", train(A(), B(), C(), D()))
        out = m.consolider("k", train(A(), p("B", "08:03:40", "08:04:20"), C(), D()))
        self.assertEqual(par_gare(out)["B"].arrivee, h("08:03:40"))
        self.assertFalse(par_gare(out)["B"].reconstruit)

    def test_m9_recorded_at_du_dernier_releve(self):
        m = Memoire()
        m.consolider("k", train(A(), B(), recorded="08:00:00"))
        out = m.consolider("k", train(B(), recorded="08:07:00"))
        self.assertEqual(out.recorded_at, h("08:07:00"))

    def test_m11_gare_annulee_retiree_n_est_pas_gardee(self):
        # une gare annulée n'a pas été desservie : la garder ferait refuser tout le train (mesure du 13/11 :
        # 242 refus en plus sur 35 703 observations)
        m = Memoire()
        m.consolider("k", train(Passage("A", None, h("08:00:00"), annule=True), B(), C(), D()))
        out = m.consolider("k", train(B(), C(), D()))
        self.assertEqual(sorted(par_gare(out)), ["B", "C", "D"])
        self.assertNotEqual(estimer(out, h("08:05:00"), distance).etat, Etat.NON_PLACE)

    def test_m11b_gare_annulee_dans_le_releve_courant_reste_refusee(self):
        # la règle du cadrage ne change pas : une gare annulée dans le relevé courant fait refuser le train
        m = Memoire()
        out = m.consolider("k", train(Passage("A", None, h("08:00:00"), annule=True), B()))
        self.assertEqual(estimer(out, h("08:01:00"), distance).etat, Etat.NON_PLACE)

    def test_m10_pas_de_memoire_sans_ancrage_dans_la_liste_precedente_vide(self):
        m = Memoire()
        out = m.consolider("k", Train([], h("08:00:00")))
        self.assertEqual(out.passages, [])
        out = m.consolider("k", train(B(), C()))
        self.assertEqual(sorted(par_gare(out)), ["B", "C"])


class TestMarquesDansEstimation(unittest.TestCase):
    def test_estimation_sans_memoire_n_est_pas_marquee(self):
        e = estimer(train(A(), B(), C(), D()), h("08:01:30"), distance)
        self.assertFalse(e.reconstruit or e.repare)

    def test_a_quai_sur_une_gare_de_memoire_est_marquee(self):
        t = train(Passage("A", None, h("08:00:00"), reconstruit=True), B(), C(), D())
        e = estimer(t, h("08:00:00"), distance)
        self.assertEqual((e.etat, e.gare), (Etat.A_QUAI, "A"))
        self.assertTrue(e.reconstruit)

    def test_en_route_utilise_une_gare_de_memoire_est_marque(self):
        t = train(A(), Passage("B", h("08:03:00"), h("08:04:00"), reconstruit=True, repare=True), C(), D())
        e = estimer(t, h("08:01:30"), distance)
        self.assertEqual(e.etat, Etat.EN_ROUTE)
        self.assertTrue(e.reconstruit and e.repare)


if __name__ == "__main__":
    unittest.main()
