"""Tests du lecteur de prévisions MBTA (Boston) -> Train de position.py.

Cadrage : docs/cadrage-tache-3.md (E5 : Boston d'abord, jointure par identifiant de course).
Données inventées au format JSON:API de l'API MBTA v3 (heures avec décalage -05:00).
"""
import unittest
from datetime import datetime, timezone

from datetime import timedelta

from mbta import apparier, grouper_par_course, lire_gps_mbta, lire_train_mbta
from position import Train

RECU = datetime(2026, 11, 13, 5, 0, 4, tzinfo=timezone.utc)


def prediction(trip, stop, seq, arrivee, depart, relation=None):
    return {
        "attributes": {
            "arrival_time": arrivee,
            "departure_time": depart,
            "stop_sequence": seq,
            "schedule_relationship": relation,
        },
        "relationships": {
            "stop": {"data": {"id": stop}},
            "trip": {"data": {"id": trip} if trip else None},
        },
    }


COURSE = [
    prediction("T1", "B", 20, "2026-11-13T00:31:27-05:00", "2026-11-13T00:32:17-05:00"),
    prediction("T1", "A", 10, None, "2026-11-13T00:25:00-05:00"),
    prediction("T1", "C", 30, "2026-11-13T00:40:00-05:00", None),
]


class TestLireTrainMbta(unittest.TestCase):
    def test_gares_triees_par_stop_sequence(self):
        train = lire_train_mbta(COURSE, RECU)
        self.assertIsInstance(train, Train)
        self.assertEqual([p.gare for p in train.passages], ["A", "B", "C"])

    def test_heures_converties_en_utc(self):
        train = lire_train_mbta(COURSE, RECU)
        b = train.passages[1]
        self.assertEqual(b.arrivee, datetime(2026, 11, 13, 5, 31, 27, tzinfo=timezone.utc))
        self.assertEqual(b.depart, datetime(2026, 11, 13, 5, 32, 17, tzinfo=timezone.utc))

    def test_origine_sans_arrivee_terminus_sans_depart(self):
        train = lire_train_mbta(COURSE, RECU)
        self.assertIsNone(train.passages[0].arrivee)
        self.assertIsNone(train.passages[2].depart)

    def test_recorded_at_est_l_heure_de_reception(self):
        self.assertEqual(lire_train_mbta(COURSE, RECU).recorded_at, RECU)

    def test_cancelled_donne_annule(self):
        courses = [prediction("T1", "A", 10, None, "2026-11-13T00:25:00-05:00", "CANCELLED")]
        self.assertTrue(lire_train_mbta(courses, RECU).passages[0].annule)

    def test_autre_relation_n_est_pas_annule(self):
        courses = [prediction("T1", "A", 10, None, "2026-11-13T00:25:00-05:00", "SKIPPED")]
        self.assertFalse(lire_train_mbta(courses, RECU).passages[0].annule)

    def test_doublon_de_ligne_ignore(self):
        # même arrêt listé sous deux lignes (CR-Fairmount et CR-Franklin) : une seule copie gardée
        doublees = COURSE + [dict(p) for p in COURSE]
        train = lire_train_mbta(doublees, RECU)
        self.assertEqual(train, lire_train_mbta(COURSE, RECU))
        self.assertEqual([p.gare for p in train.passages], ["A", "B", "C"])

    def test_liste_vide(self):
        self.assertEqual(lire_train_mbta([], RECU).passages, [])


class TestGrouperParCourse(unittest.TestCase):
    def test_regroupe_par_trip_id(self):
        corps = {"data": COURSE + [prediction("T2", "A", 10, None, "2026-11-13T01:00:00-05:00")]}
        groupes = grouper_par_course(corps)
        self.assertEqual(set(groupes), {"T1", "T2"})
        self.assertEqual(len(groupes["T1"]), 3)

    def test_prediction_sans_course_ignoree(self):
        corps = {"data": [prediction(None, "A", 10, None, "2026-11-13T01:00:00-05:00")]}
        self.assertEqual(grouper_par_course(corps), {})


def vehicule(trip, lat, lon, statut, maj):
    return {
        "attributes": {"latitude": lat, "longitude": lon, "current_status": statut, "updated_at": maj},
        "relationships": {"trip": {"data": {"id": trip} if trip else None}},
    }


class TestLireGpsMbta(unittest.TestCase):
    def test_champs_et_utc(self):
        corps = {"data": [vehicule("T1", 42.5, -71.2, "STOPPED_AT", "2026-11-13T00:00:05-05:00")]}
        (p,) = lire_gps_mbta(corps, RECU)
        self.assertEqual((p.course, p.latitude, p.longitude, p.statut), ("T1", 42.5, -71.2, "STOPPED_AT"))
        self.assertEqual(p.instant, datetime(2026, 11, 13, 5, 0, 5, tzinfo=timezone.utc))
        self.assertEqual(p.recu, RECU)

    def test_vehicule_sans_course_garde_course_none(self):
        corps = {"data": [vehicule(None, 42.5, -71.2, "IN_TRANSIT_TO", "2026-11-13T00:00:05-05:00")]}
        self.assertIsNone(lire_gps_mbta(corps, RECU)[0].course)


def utc(h, m, s):
    return datetime(2026, 11, 13, h, m, s, tzinfo=timezone.utc)


def gps(course, instant, recu=None):
    corps = {"data": [vehicule(course, 42.5, -71.2, "IN_TRANSIT_TO", instant.isoformat())]}
    return lire_gps_mbta(corps, recu or instant + timedelta(seconds=5))[0]


def releve(recu, *courses):
    corps = {"data": [p for c in courses for p in COURSE_DE(c)]}
    return (recu, grouper_par_course(corps))


def COURSE_DE(trip):
    return [prediction(trip, "A", 10, None, "2026-11-13T00:25:00-05:00"),
            prediction(trip, "B", 20, "2026-11-13T00:31:27-05:00", None)]


class TestApparier(unittest.TestCase):
    RELEVES = [releve(utc(10, 0, 0), "T1"), releve(utc(10, 0, 4), "T1"), releve(utc(10, 0, 16), "T1")]

    def test_dernier_releve_avant_l_instant_du_gps(self):
        train, motif = apparier(gps("T1", utc(10, 0, 5)), self.RELEVES)
        self.assertIsNone(motif)
        self.assertEqual(train.recorded_at, utc(10, 0, 4))

    def test_releve_a_l_instant_exact_accepte(self):
        train, _ = apparier(gps("T1", utc(10, 0, 4)), self.RELEVES)
        self.assertEqual(train.recorded_at, utc(10, 0, 4))

    def test_jamais_le_futur(self):
        train, motif = apparier(gps("T1", utc(9, 59, 59)), self.RELEVES)
        self.assertIsNone(train)
        self.assertEqual(motif, "aucun relevé de prévisions avant le GPS")

    def test_course_absente(self):
        train, motif = apparier(gps("T9", utc(10, 0, 5)), self.RELEVES)
        self.assertIsNone(train)
        self.assertEqual(motif, "course absente des prévisions")

    def test_sans_course(self):
        self.assertEqual(apparier(gps(None, utc(10, 0, 5)), self.RELEVES)[1], "course absente des prévisions")

    def test_gps_perime_au_dela_de_120_s(self):
        p = gps("T1", utc(10, 0, 5), recu=utc(10, 2, 6))
        self.assertEqual(apparier(p, self.RELEVES), (None, "GPS périmé"))

    def test_gps_a_120_s_pile_accepte(self):
        p = gps("T1", utc(10, 0, 5), recu=utc(10, 2, 5))
        self.assertIsNone(apparier(p, self.RELEVES)[1])

    def test_perime_prioritaire_sur_course_absente(self):
        p = gps("T9", utc(10, 0, 5), recu=utc(10, 5, 0))
        self.assertEqual(apparier(p, self.RELEVES)[1], "GPS périmé")


if __name__ == "__main__":
    unittest.main()
