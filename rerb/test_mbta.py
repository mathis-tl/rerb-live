"""Tests du lecteur de prévisions MBTA (Boston) -> Train de position.py.

Cadrage : docs/cadrage-tache-3.md (E5 : Boston d'abord, jointure par identifiant de course).
Données inventées au format JSON:API de l'API MBTA v3 (heures avec décalage -05:00).
"""
import unittest
from datetime import datetime, timezone

from mbta import grouper_par_course, lire_train_mbta
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


if __name__ == "__main__":
    unittest.main()
