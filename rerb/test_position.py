"""Tests de la tâche 3 : estimer la position d'un train à l'instant t (niveau 1).

Cadrage : docs/cadrage-tache-3.md. Numéros de tests = tableau validé par Mathis (13/11/2026).
Décor commun : gares A, B, C ; A→B = 2 400 m, B→C = 3 000 m (inventés).

    gare   arrivée    départ
    A      aucune     08:00:00   (origine)
    B      08:03:00   08:04:00
    C      08:07:00   aucun      (terminus)

Les tests sur l'échantillon réel sont ignorés si data/ est absent (non versionné).
"""
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import position
from geometry import Geometry, load_morceaux
from position import (
    DistanceInconnue,
    Etat,
    Passage,
    Reseau,
    Train,
    distance_depuis_reseau,
    estimer,
    lire_train,
)
from stops import load_stops

RACINE = Path(__file__).resolve().parent.parent
CSV = Path(__file__).resolve().parent / "donnees/arrets_rerb_C01743.csv"
TRACE = RACINE / "data/static/rerb/rerb_trace_C01743.json"
ECHANTILLON = RACINE / "data/raw/A_estimated_timetable.json"


def h(hms):
    """'08:01:30' -> datetime UTC du 12/11/2026."""
    hh, mm, ss = (int(x) for x in hms.split(":"))
    return datetime(2026, 11, 12, hh, mm, ss, tzinfo=timezone.utc)


def distance_test(a, b):
    table = {frozenset(("A", "B")): 2400.0, frozenset(("B", "C")): 3000.0,
             frozenset(("A", "C")): 5400.0}
    try:
        return table[frozenset((a, b))]
    except KeyError:
        raise DistanceInconnue(f"{a}-{b}") from None


def passage(gare, arrivee, depart, annule=False):
    return Passage(gare, h(arrivee) if arrivee else None, h(depart) if depart else None, annule)


def base(recorded="08:01:00", **remplace):
    p = {
        "A": passage("A", None, "08:00:00"),
        "B": passage("B", "08:03:00", "08:04:00"),
        "C": passage("C", "08:07:00", None),
    }
    p.update(remplace)
    return Train(list(p.values()), h(recorded) if recorded else None)


def ou(train, t, **kw):
    return estimer(train, h(t), distance_test, **kw)


class TestBase(unittest.TestCase):
    def test_1_en_route_au_milieu(self):
        e = ou(base(), "08:01:30")
        self.assertEqual(e.etat, Etat.EN_ROUTE)
        self.assertEqual((e.gare, e.gare_suivante), ("A", "B"))
        self.assertAlmostEqual(e.fraction, 0.5)
        self.assertAlmostEqual(e.distance_m, 1200.0)

    def test_2_en_route_deuxieme_troncon(self):
        # B : arrêt annoncé 60 s ramené à 25 s (décision du 18/11) : départ 08:03:25, arrivée C inchangée 08:07:00
        e = ou(base(), "08:05:30")
        self.assertEqual(e.etat, Etat.EN_ROUTE)
        self.assertEqual((e.gare, e.gare_suivante), ("B", "C"))
        self.assertAlmostEqual(e.fraction, 125 / 215)
        self.assertAlmostEqual(e.distance_m, 3000.0 * 125 / 215)

    def test_3_a_quai(self):
        e = ou(base(), "08:03:20")
        self.assertEqual(e.etat, Etat.A_QUAI)
        self.assertEqual(e.gare, "B")
        self.assertEqual(e.distance_m, 0.0)

    def test_4_borne_arrivee(self):
        e = ou(base(), "08:03:00")
        self.assertEqual((e.etat, e.gare), (Etat.A_QUAI, "B"))

    def test_5_borne_depart(self):
        e = ou(base(), "08:03:25")   # arrivée 08:03:00 + séjour 25 s
        self.assertEqual((e.etat, e.gare), (Etat.A_QUAI, "B"))
        self.assertEqual(ou(base(), "08:03:26").etat, Etat.EN_ROUTE)

    def test_6_pas_encore_parti(self):
        e = ou(base(), "07:59:00")
        self.assertEqual(e.etat, Etat.POSITION_INCONNUE)
        self.assertIsNone(e.distance_m)
        self.assertIn("1re gare", e.motif)

    def test_7_termine(self):
        # terminus sans départ : reste à quai SEJOUR_TERMINUS (60 s) après l'arrivée, puis « terminé »
        e = ou(base(), "08:08:01")
        self.assertEqual(e.etat, Etat.TERMINE)
        self.assertIsNone(e.distance_m)

    def test_7_bis_reste_a_quai_au_terminus_60_s(self):
        # sinon le train disparaît de la carte à l'instant où il arrive (retour de Mathis, 17/11)
        for h in ("08:07:00", "08:07:30", "08:08:00"):
            e = ou(base(), h)
            self.assertEqual((e.etat, e.gare), (Etat.A_QUAI, "C"), h)
        self.assertEqual(ou(base(), "08:08:01").etat, Etat.TERMINE)

    def test_7_ter_terminus_avec_depart_annonce_inchange(self):
        # si PRIM donne un départ au dernier arrêt, on garde cette heure (pas de séjour ajouté)
        t = base(C=passage("C", "08:07:00", "08:07:10"))
        self.assertEqual(ou(t, "08:07:10").etat, Etat.A_QUAI)
        self.assertEqual(ou(t, "08:07:11").etat, Etat.TERMINE)


CHATELET = "STIF:StopArea:SP:45102:"     # gare dense (30 s)
PALAISEAU = "STIF:StopArea:SP:47009:"    # gare ordinaire (25 s)


def dist3000(a, b):
    return 3000.0


def train_ligne(gare, arrivee, depart):
    """Origine X (départ 08:00:00) -> `gare` (arrivée, départ) -> terminus Z (arrivée 08:10:00)."""
    return Train([passage("X", None, "08:00:00"), passage(gare, arrivee, depart), passage("Z", "08:10:00", None)], h("08:01:00"))


class TestSejourQuai(unittest.TestCase):
    """Décisions de Mathis (18/11) : séjour à quai 25 s (30 s en gare dense) ; PRIM annonce 63 à 77 s, mesuré 17 à 32 s."""

    def etat(self, train, t):
        return estimer(train, h(t), dist3000)

    def test_arret_annonce_63_s_devient_25_s(self):
        t = train_ligne(PALAISEAU, "08:03:00", "08:04:03")
        self.assertEqual(self.etat(t, "08:03:25").etat, Etat.A_QUAI)
        e = self.etat(t, "08:03:26")
        self.assertEqual((e.etat, e.gare, e.gare_suivante), (Etat.EN_ROUTE, PALAISEAU, "Z"))

    def test_gare_dense_77_s_devient_30_s(self):
        t = train_ligne(CHATELET, "08:03:00", "08:04:17")
        self.assertEqual(self.etat(t, "08:03:30").etat, Etat.A_QUAI)
        self.assertEqual(self.etat(t, "08:03:31").etat, Etat.EN_ROUTE)

    def test_toutes_les_gares_denses_decidees(self):
        for numero in ("45102", "462394", "44877", "43833", "58774", "462398", "473364"):
            t = train_ligne(f"STIF:StopArea:SP:{numero}:", "08:03:00", "08:04:17")
            self.assertEqual(self.etat(t, "08:03:30").etat, Etat.A_QUAI, numero)
            self.assertEqual(self.etat(t, "08:03:31").etat, Etat.EN_ROUTE, numero)
        self.assertEqual(sorted(position.GARES_DENSES), sorted(["45102", "462394", "44877", "43833", "58774", "462398", "473364"]))

    def test_gare_ordinaire_pas_dense(self):
        t = train_ligne(PALAISEAU, "08:03:00", "08:04:17")
        self.assertEqual(self.etat(t, "08:03:26").etat, Etat.EN_ROUTE)

    def test_arret_plus_court_que_le_sejour_inchange(self):
        t = train_ligne(PALAISEAU, "08:03:00", "08:03:15")
        self.assertEqual(self.etat(t, "08:03:15").etat, Etat.A_QUAI)
        self.assertEqual(self.etat(t, "08:03:16").etat, Etat.EN_ROUTE)

    def test_arret_egal_au_sejour_inchange(self):
        t = train_ligne(PALAISEAU, "08:03:00", "08:03:25")
        self.assertEqual(self.etat(t, "08:03:25").etat, Etat.A_QUAI)
        self.assertEqual(self.etat(t, "08:03:26").etat, Etat.EN_ROUTE)

    def test_arret_au_plafond_est_raccourci_au_dela_inchange(self):
        # 120 s : encore raccourci ; 121 s et 180 s : vraie retenue annoncée par PRIM (arrêts normaux observés le 18/11 : 97 s au plus), on ne touche pas
        t120 = train_ligne(PALAISEAU, "08:03:00", "08:05:00")
        self.assertEqual(self.etat(t120, "08:03:26").etat, Etat.EN_ROUTE)
        for fin in ("08:05:01", "08:06:00", "08:07:00"):
            t = train_ligne(PALAISEAU, "08:03:00", fin)
            self.assertEqual(self.etat(t, "08:03:26").etat, Etat.A_QUAI, fin)   # toujours à quai : l'arrêt annoncé est conservé
            self.assertEqual(self.etat(t, fin).etat, Etat.A_QUAI, fin)
        self.assertEqual(position.ARRET_ANNONCE_MAX, timedelta(seconds=120))

    def test_gare_inconnue_ou_sans_numero_prend_le_sejour_par_defaut(self):
        t = train_ligne("B", "08:03:00", "08:04:00")
        self.assertEqual(self.etat(t, "08:03:25").etat, Etat.A_QUAI)
        self.assertEqual(self.etat(t, "08:03:26").etat, Etat.EN_ROUTE)

    def test_terminus_et_origine_inchanges(self):
        t = train_ligne(PALAISEAU, "08:03:00", "08:03:20")
        self.assertEqual(self.etat(t, "08:10:00").gare, "Z")
        self.assertEqual(self.etat(t, "08:11:00").etat, Etat.A_QUAI)   # 60 s au terminus
        self.assertEqual(self.etat(t, "08:11:01").etat, Etat.TERMINE)
        self.assertEqual(self.etat(t, "08:00:00").gare, "X")            # origine sans arrivée : à quai à son départ

    def test_premier_passage_avec_arrivee_et_depart_est_raccourci_comme_une_gare_ordinaire(self):
        # choix figé (revue du 18/11) : PRIM retire les gares déjà passées, la 1re gare de la liste est souvent intermédiaire ;
        # une vraie origine qui annonce une arrivée ET un battement de 25 à 180 s est donc raccourcie aussi
        t = Train([passage(PALAISEAU, "08:00:00", "08:01:03"), passage("Z", "08:10:00", None)], h("08:00:00"))
        self.assertEqual(self.etat(t, "08:00:25").etat, Etat.A_QUAI)
        self.assertEqual(self.etat(t, "08:00:26").etat, Etat.EN_ROUTE)

    def test_terminus_avec_depart_annonce_long_inchange(self):
        t = Train([passage("X", None, "08:00:00"), passage("Z", "08:10:00", "08:12:00")], h("08:01:00"))
        self.assertEqual(self.etat(t, "08:12:00").etat, Etat.A_QUAI)

    def test_drapeaux_reconstruit_et_repare_conserves(self):
        t = Train([passage("X", None, "08:00:00"),
                   Passage(PALAISEAU, h("08:03:00"), h("08:04:03"), reconstruit=True, repare=True),
                   passage("Z", "08:10:00", None)], h("08:01:00"))
        e = self.etat(t, "08:03:10")
        self.assertEqual(e.etat, Etat.A_QUAI)
        self.assertTrue(e.reconstruit and e.repare)
        e = self.etat(t, "08:05:00")
        self.assertTrue(e.reconstruit and e.repare)

    def test_continuite_et_vitesse_recalee(self):
        # départ raccourci : le train repart à 08:03:25 et arrive toujours à 08:10:00 (heure PRIM)
        t = train_ligne(PALAISEAU, "08:03:00", "08:04:03")
        e0 = self.etat(t, "08:03:26")
        self.assertLess(e0.fraction, 0.01)
        self.assertEqual(e0.duree_s, 395.0)                    # 08:03:25 -> 08:10:00 (avant : 357 s)
        e_fin = self.etat(t, "08:09:59")
        self.assertGreater(e_fin.fraction, 0.99)
        avant = 3000.0 / 357.0
        apres = e0.longueur_m / e0.duree_s
        self.assertLess(apres, avant)                          # il roule moins vite pour arriver à l'heure PRIM
        self.assertAlmostEqual(apres, 3000.0 / 395.0)


class TestHeuresManquantesEtDesordre(unittest.TestCase):
    def test_8_origine_sans_arrivee(self):
        # A n'a pas d'arrivée : arrivée = départ = 08:00:00
        e = ou(base(), "08:00:00")
        self.assertEqual((e.etat, e.gare), (Etat.A_QUAI, "A"))
        e = ou(base(), "07:59:59")
        self.assertEqual(e.etat, Etat.POSITION_INCONNUE)

    def test_9_terminus_sans_depart(self):
        # C n'a pas de départ : départ = arrivée = 08:07:00
        e = ou(base(), "08:07:00")
        self.assertEqual((e.etat, e.gare), (Etat.A_QUAI, "C"))

    def test_10_passages_en_desordre(self):
        t = base()
        melange = Train([t.passages[2], t.passages[0], t.passages[1]], t.recorded_at)
        self.assertEqual(ou(melange, "08:01:30"), ou(t, "08:01:30"))
        self.assertEqual(ou(melange, "08:05:30"), ou(t, "08:05:30"))

    def test_11_heure_estimee_absente_ou_presente(self):
        # via lire_train : estimée sinon théorique ; l'estimée l'emporte.
        def appel(ref, **champs):
            return {"StopPointRef": {"value": ref}, **champs}

        ej = {
            "RecordedAtTime": "2026-11-12T08:01:00.000Z",
            "EstimatedCalls": {"EstimatedCall": [
                appel("A", AimedDepartureTime="2026-11-12T08:00:00.000Z"),
                appel("B", AimedArrivalTime="2026-11-12T08:03:00.000Z",
                      AimedDepartureTime="2026-11-12T08:04:00.000Z"),
                appel("C", ExpectedArrivalTime="2026-11-12T08:07:00.000Z",
                      AimedArrivalTime="2026-11-12T08:06:00.000Z"),
            ]},
        }
        train = lire_train(ej)
        self.assertEqual(ou(train, "08:03:20").etat, Etat.A_QUAI)  # B : heures théoriques
        # C : estimée 08:07:00 (et non théorique 08:06:00)
        self.assertEqual(ou(train, "08:06:30").etat, Etat.EN_ROUTE)
        self.assertEqual(ou(train, "08:07:00").etat, Etat.A_QUAI)
        self.assertEqual(train.recorded_at, h("08:01:00"))

    def test_lire_train_accepte_une_liste_directe_et_ignore_arrived(self):
        ej = {
            "RecordedAtTime": "2026-11-12T08:01:00.000Z",
            "EstimatedCalls": [
                {"StopPointRef": {"value": "A"}, "ExpectedDepartureTime": "2026-11-12T08:00:00.000Z",
                 "DepartureStatus": "ARRIVED"},
            ],
        }
        train = lire_train(ej)
        self.assertEqual(len(train.passages), 1)
        self.assertFalse(train.passages[0].annule)

    def test_lire_train_detecte_cancelled(self):
        ej = {
            "RecordedAtTime": "2026-11-12T08:01:00.000Z",
            "EstimatedCalls": {"EstimatedCall": [
                {"StopPointRef": {"value": "A"}, "ExpectedDepartureTime": "2026-11-12T08:00:00.000Z",
                 "DepartureStatus": "CANCELLED"},
            ]},
        }
        self.assertTrue(lire_train(ej).passages[0].annule)


class TestRefus(unittest.TestCase):
    def assertNonPlace(self, e, morceau_motif):
        self.assertEqual(e.etat, Etat.NON_PLACE)
        self.assertIsNone(e.distance_m)
        self.assertIn(morceau_motif, e.motif)

    def test_12_heures_incoherentes_chevauchement(self):
        # C arrive à 08:03:10 : avant même la fin du séjour de B (arrivée 08:03:00 + 25 s)
        t = base(C=passage("C", "08:03:10", None))
        self.assertNonPlace(ou(t, "08:01:30"), "incohérentes")

    def test_12c_chevauchement_absorbe_par_le_sejour_raccourci(self):
        # C arrive à 08:03:30 alors que PRIM annonçait B partant à 08:04:00 : avec le départ recalculé (08:03:25) il n'y a plus de chevauchement
        t = base(C=passage("C", "08:03:30", None))
        self.assertEqual(ou(t, "08:01:30").etat, Etat.EN_ROUTE)

    def test_12b_heures_incoherentes_depart_avant_arrivee(self):
        # B part à 08:02:00 avant d'être arrivé (08:03:00)
        t = base(B=passage("B", "08:03:00", "08:02:00"))
        self.assertNonPlace(ou(t, "08:01:30"), "incohérentes")

    def test_13_train_annule(self):
        t = base(B=passage("B", "08:03:00", "08:04:00", annule=True))
        self.assertNonPlace(ou(t, "08:01:30"), "annulé")

    def test_14_heures_manquantes(self):
        t = base(B=passage("B", None, None))
        self.assertNonPlace(ou(t, "08:01:30"), "manquantes")

    def test_14b_aucun_passage(self):
        self.assertNonPlace(ou(Train([], h("08:01:00")), "08:01:30"), "aucun passage")

    def test_distance_inconnue_donne_non_place(self):
        t = base()
        e = estimer(t, h("08:01:30"), lambda a, b: (_ for _ in ()).throw(DistanceInconnue("x")))
        self.assertNonPlace(e, "distance")


class TestGaresSauteesEtFraicheur(unittest.TestCase):
    def test_15_gare_sautee(self):
        # train direct A -> C : B absente de la liste
        t = Train([passage("A", None, "08:00:00"), passage("C", "08:06:00", None)], h("08:01:00"))
        e = ou(t, "08:03:00")
        self.assertEqual((e.etat, e.gare, e.gare_suivante), (Etat.EN_ROUTE, "A", "C"))
        self.assertAlmostEqual(e.fraction, 0.5)
        self.assertAlmostEqual(e.distance_m, 2700.0)

    def test_15b_somme_des_couples_voisins(self):
        # distance A->C = 2 400 + 3 000, dans les deux sens, par le chemin unique de l'arbre
        r = Reseau({("A", "B"): 2400.0, ("B", "C"): 3000.0})
        self.assertAlmostEqual(r.distance("A", "C"), 5400.0)
        self.assertAlmostEqual(r.distance("C", "A"), 5400.0)
        self.assertAlmostEqual(r.distance("A", "B"), 2400.0)
        self.assertEqual(r.distance("B", "B"), 0.0)
        t = Train([passage("A", None, "08:00:00"), passage("C", "08:06:00", None)], h("08:01:00"))
        e = estimer(t, h("08:03:00"), r.distance)
        self.assertAlmostEqual(e.distance_m, 2700.0)

    def test_15d_reseau_chemin_des_gares(self):
        r = Reseau({("A", "B"): 2400.0, ("B", "C"): 3000.0, ("B", "D"): 500.0})
        self.assertEqual(r.chemin("A", "C"), ["A", "B", "C"])
        self.assertEqual(r.chemin("C", "A"), ["C", "B", "A"])
        self.assertEqual(r.chemin("D", "C"), ["D", "B", "C"])
        self.assertEqual(r.chemin("B", "B"), ["B"])
        with self.assertRaises(DistanceInconnue):
            r.chemin("A", "Z")

    def test_15e_reseau_chemin_non_relie(self):
        r = Reseau({("A", "B"): 2400.0, ("X", "Y"): 100.0})
        with self.assertRaises(DistanceInconnue):
            r.chemin("A", "X")

    def test_15c_reseau_gare_inconnue_ou_non_reliee(self):
        r = Reseau({("A", "B"): 2400.0, ("X", "Y"): 100.0})
        with self.assertRaises(DistanceInconnue):
            r.distance("A", "Z")
        with self.assertRaises(DistanceInconnue):
            r.distance("A", "X")  # deux composantes séparées

    def test_16_donnees_perimees(self):
        e = ou(base(recorded="07:40:00"), "08:01:30")
        self.assertTrue(e.perime)
        e_frais = ou(base(), "08:01:30")
        self.assertEqual((e.etat, e.distance_m), (e_frais.etat, e_frais.distance_m))

    def test_17_donnees_fraiches(self):
        e = ou(base(recorded="08:01:00"), "08:01:30")
        self.assertFalse(e.perime)

    def test_16b_seuil_reglable_et_strict(self):
        # 15 min pile : pas périmé (seuil strict) ; seuil abaissé à 5 min : périmé
        t = base(recorded="08:00:00")
        self.assertFalse(ou(t, "08:15:00").perime)
        self.assertTrue(ou(t, "08:15:01").perime)
        self.assertTrue(ou(t, "08:06:00", seuil_perime=timedelta(minutes=5)).perime)

    def test_16c_date_de_mise_a_jour_inconnue_est_perimee(self):
        self.assertTrue(ou(base(recorded=None), "08:01:30").perime)


class TestSolidite(unittest.TestCase):
    def test_18_gare_precedente_retiree(self):
        # le train vient de quitter A : la liste commence à B
        t = Train([passage("B", "08:03:00", "08:04:00"), passage("C", "08:07:00", None)], h("08:01:00"))
        e = ou(t, "08:01:30")
        self.assertEqual(e.etat, Etat.POSITION_INCONNUE)
        self.assertIn("1re gare", e.motif)
        # même état que « pas encore parti » (non distinguable au niveau 1)
        self.assertEqual(e.etat, ou(base(), "07:59:00").etat)

    def test_20_purete(self):
        t = base()
        self.assertEqual(ou(t, "08:01:30"), ou(t, "08:01:30"))
        source = Path(position.__file__).read_text(encoding="utf-8")
        for interdit in ("datetime.now", "utcnow", "time.time", "date.today"):
            self.assertNotIn(interdit, source)


class TestPontReseauReel(unittest.TestCase):
    def test_pont_identifiants_prim(self):
        r = Reseau({("1", "2"): 100.0, ("2", "3"): 200.0})
        d = distance_depuis_reseau(r)
        self.assertAlmostEqual(d("STIF:StopArea:SP:1:", "STIF:StopArea:SP:3:"), 300.0)
        with self.assertRaises(DistanceInconnue):
            d("STIF:StopArea:SP:1:", "STIF:StopArea:SP:99:")
        with self.assertRaises(DistanceInconnue):
            d("STIF:StopPoint:Q:1:", "STIF:StopArea:SP:3:")  # un quai n'est pas une gare


@unittest.skipUnless(TRACE.exists() and CSV.exists() and ECHANTILLON.exists(),
                     "données réelles absentes (data/ non versionné)")
class TestEchantillonReel(unittest.TestCase):
    def test_19_56_trains_sans_plantage(self):
        stops = load_stops(CSV)
        g = Geometry(load_morceaux(TRACE), {n: (s.lat, s.lon) for n, s in stops.items()})
        reseau = Reseau({p: g.distance(*p) for p in g.neighbor_pairs()})
        distance = distance_depuis_reseau(reseau)
        data = json.loads(ECHANTILLON.read_text(encoding="utf-8"))
        brut = data["Siri"]["ServiceDelivery"]["EstimatedTimetableDelivery"][0][
            "EstimatedJourneyVersionFrame"][0]["EstimatedVehicleJourney"]
        trains = [lire_train(ej) for ej in brut]
        self.assertEqual(len(trains), 56)
        t = max(tr.recorded_at for tr in trains)
        resultats = [estimer(tr, t, distance) for tr in trains]
        comptes = {}
        for e in resultats:
            comptes[e.etat] = comptes.get(e.etat, 0) + 1
        non_places = comptes.get(Etat.NON_PLACE, 0)
        print(f"\n[test 19] t={t.isoformat()} états={ {k.name: v for k, v in comptes.items()} } "
              f"non placés={non_places}/56 ({100 * non_places / 56:.0f} %) "
              f"périmés={sum(e.perime for e in resultats)}/56")
        for e in resultats:
            if e.etat == Etat.NON_PLACE:
                self.assertTrue(e.motif)
            if e.etat in (Etat.A_QUAI, Etat.EN_ROUTE):
                self.assertGreaterEqual(e.distance_m, 0.0)
        self.assertGreater(len(resultats) - non_places, 0)


if __name__ == "__main__":
    unittest.main()
