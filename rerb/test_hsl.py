"""Tests du lecteur HSL (Helsinki) : prévisions GTFS-RT décodées et positions MQTT (HFP) -> format de position.py.

Cadrage : docs/cadrage-tache-3.md (E5, S4). Le `trip_id` du temps réel est vide : un train se retrouve par
(route_id, direction_id, jour d'exploitation, heure de départ). Les structures d'entrée sont des dictionnaires
simples (le décodage protobuf se fait dans le script d'exploration, rerb/ reste en bibliothèque standard).
Données inventées.
"""
import unittest
from datetime import datetime, timezone

from hsl import SEUIL_RAME, choisir_rame, cle_course, lire_point_hfp, lire_train_hsl
from mbta import PointGps
from position import Etat, Train, estimer

RECU = datetime(2026, 11, 13, 10, 1, 28, tzinfo=timezone.utc)
T0 = 1794560881  # epoch (s)


def arret(stop, arrivee=None, depart=None, relation="SCHEDULED"):
    return {"stop_id": stop, "arrival": arrivee, "departure": depart, "schedule_relationship": relation}


def maj(arrets, relation="SCHEDULED"):
    return {"route_id": "3001I", "direction_id": 0, "start_date": "20261113", "start_time": "11:16:00",
            "schedule_relationship": relation, "stop_time_updates": arrets}


class TestCleCourse(unittest.TestCase):
    def test_cle_temps_reel(self):
        self.assertEqual(cle_course("3001I", 0, "20261113", "11:16:00"), ("3001I", 0, "20261113", "1116"))

    def test_cle_hfp_equivalente(self):
        # HFP : dir 1 ou 2 (GTFS : 0 ou 1), start « 11:16 », oday « 2026-11-13 »
        vp = {"route": "3001I", "dir": "1", "oday": "2026-11-13", "start": "11:16"}
        p = lire_point_hfp({"VP": {**vp, "tst": "2026-11-13T09:16:05.250Z", "lat": 60.3, "long": 25.0,
                                    "loc": "GPS", "stop": None}}, RECU)
        self.assertEqual(p.course, cle_course("3001I", 0, "20261113", "11:16:00"))


class TestApresMinuit(unittest.TestCase):
    """Revue R2 point 1. GTFS-RT écrit « 25:51 » pour un départ à 01:51 du lendemain calendaire (jour d'exploitation
    J) ; HFP écrit « 01:51 ». On ajoute 24 h si le départ tombe un jour calendaire (heure d'Helsinki) après `oday`,
    d'après l'instant `tst` du message. Un départ à 03:30 du jour d'exploitation lui-même reste « 0330 »."""

    def point(self, tst, **vp):
        base = {"route": "3001R", "dir": "1", "oday": "2026-11-12", "start": "01:51", "tst": tst,
                "lat": 60.3, "long": 25.0, "stop": None}
        return lire_point_hfp({"VP": {**base, **vp}}, RECU)

    def test_cle_hfp_rejoint_la_cle_temps_reel(self):
        p = self.point("2026-11-12T23:59:00Z")  # 01:59 le 13/11 à Helsinki
        self.assertEqual(p.course, cle_course("3001R", 0, "20261112", "25:51:00"))

    def test_depart_avant_minuit_vu_apres_minuit_reste_inchange(self):
        p = self.point("2026-11-12T22:10:00Z", start="23:50")  # 00:10 le 13/11 à Helsinki, parti la veille
        self.assertEqual(p.course[3], "2350")

    def test_depart_apres_minuit_du_jour_d_exploitation_meme(self):
        p = self.point("2026-11-13T01:40:00Z", oday="2026-11-13", start="03:30")  # 03:40 le 13/11 : même jour
        self.assertEqual(p.course[3], "0330")

    def test_depart_de_jour(self):
        self.assertEqual(self.point("2026-11-12T10:00:00Z", start="11:16").course[3], "1116")

    def test_sens_invalide_donne_pas_de_course(self):
        for d in ("0", "3", "x", ""):
            self.assertIsNone(self.point("2026-11-12T10:00:00Z", dir=d).course)


class TestLireTrainHsl(unittest.TestCase):
    def test_passages_et_heures_utc(self):
        train = lire_train_hsl(maj([arret("A", T0, T0 + 30), arret("B", T0 + 200, None)]), RECU)
        self.assertIsInstance(train, Train)
        self.assertEqual([p.gare for p in train.passages], ["A", "B"])
        self.assertEqual(train.passages[0].arrivee, datetime.fromtimestamp(T0, timezone.utc))
        self.assertEqual(train.passages[0].depart, datetime.fromtimestamp(T0 + 30, timezone.utc))
        self.assertIsNone(train.passages[1].depart)
        self.assertEqual(train.recorded_at, RECU)

    def test_course_annulee(self):
        train = lire_train_hsl(maj([arret("A", T0, T0 + 30)], relation="CANCELED"), RECU)
        self.assertTrue(all(p.annule for p in train.passages))

    def test_arret_saute_retire(self):
        train = lire_train_hsl(maj([arret("A", T0, T0 + 30), arret("S", None, None, "SKIPPED"),
                                    arret("B", T0 + 200, None)]), RECU)
        self.assertEqual([p.gare for p in train.passages], ["A", "B"])

    def test_arret_no_data_garde_sans_heure(self):
        train = lire_train_hsl(maj([arret("A", None, None, "NO_DATA")]), RECU)
        self.assertEqual(len(train.passages), 1)
        self.assertIsNone(train.passages[0].arrivee)

    def test_liste_vide(self):
        self.assertEqual(lire_train_hsl(maj([]), RECU).passages, [])


class TestLirePointHfp(unittest.TestCase):
    def message(self, **extra):
        vp = {"route": "3001I", "dir": "2", "oday": "2026-11-13", "start": "08:36", "tst": "2026-11-13T06:59:52.751Z",
              "lat": 60.320441, "long": 25.049801, "loc": "GPS", "stop": None}
        vp.update(extra)
        return {"VP": vp}

    def test_champs(self):
        p = lire_point_hfp(self.message(), RECU)
        self.assertEqual(p.course, ("3001I", 1, "20261113", "0836"))
        self.assertEqual((p.latitude, p.longitude), (60.320441, 25.049801))
        self.assertEqual(p.instant, datetime(2026, 11, 13, 6, 59, 52, 751000, tzinfo=timezone.utc))
        self.assertEqual(p.recu, RECU)

    def test_statut_a_quai_si_stop_renseigne(self):
        self.assertEqual(lire_point_hfp(self.message(stop="1020503"), RECU).statut, "STOPPED_AT")
        self.assertEqual(lire_point_hfp(self.message(stop=None), RECU).statut, "IN_TRANSIT_TO")

    def test_sans_position_ou_sans_course(self):
        p = lire_point_hfp(self.message(lat=None, long=None), RECU)
        self.assertIsNone(p.latitude)
        self.assertIsNone(lire_point_hfp(self.message(route=None), RECU).course)


class TestArretsPassesHsl(unittest.TestCase):
    """S5 : HSL laisse les arrêts déjà desservis dans la liste ; `estimer` les ignore, pas de filtre à ajouter."""

    def test_segment_choisi_malgre_les_arrets_passes(self):
        a = datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc).timestamp()
        train = lire_train_hsl(maj([arret("A", a, a), arret("B", a + 300, a + 300), arret("C", a + 600, None)]), RECU)
        e = estimer(train, datetime.fromtimestamp(a + 420, timezone.utc), lambda x, y: 1000.0)
        self.assertEqual((e.etat, e.gare, e.gare_suivante), (Etat.EN_ROUTE, "B", "C"))
        self.assertAlmostEqual(e.fraction, 0.4)


class TestChoisirRame(unittest.TestCase):
    """S4, option C : plusieurs véhicules sous une même clé de course au même instant."""

    @staticmethod
    def cand(veh, nord_m):  # nord_m : décalage vers le nord en mètres (1 degré de latitude = 111 195 m)
        return veh, PointGps(("3001R", 0, "20261113", "0420"), 60.0 + nord_m / 111195, 25.0, RECU, "IN_TRANSIT_TO", RECU)

    def test_seuil(self):
        self.assertEqual(SEUIL_RAME, 300.0)

    def test_un_seul_vehicule(self):
        c = self.cand("06323", 0)
        self.assertEqual(choisir_rame([c]), (c[1], None, 0))

    def test_rame_couplee_garde_le_plus_petit_numero(self):
        a, b = self.cand("06328", 150), self.cand("06323", 0)
        point, motif, n = choisir_rame([a, b])
        self.assertIs(point, b[1])
        self.assertEqual((motif, n), ("autre unité d'une rame couplée", 1))

    def test_pile_au_seuil_accepte(self):
        a, b = self.cand("1", 0), self.cand("2", 299)
        self.assertIsNotNone(choisir_rame([a, b])[0])

    def test_collision_ecartee_et_comptee(self):
        a, b, c = self.cand("1", 0), self.cand("2", 150), self.cand("3", 5000)
        self.assertEqual(choisir_rame([a, b, c]), (None, "course ambiguë (plusieurs trains à plus de 300 m)", 3))

    def test_ecart_mesure_entre_toutes_les_paires(self):
        # 0, 200, 400 m : voisins à 200 m mais 400 m entre les extrêmes -> ambigu
        self.assertIsNone(choisir_rame([self.cand("1", 0), self.cand("2", 200), self.cand("3", 400)])[0])


if __name__ == "__main__":
    unittest.main()
