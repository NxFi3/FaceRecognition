"""Atomicity regressions for student and face enrollment persistence."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from src import database
from src.cloud import entitlement


class StudentEnrollmentAtomicityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db_path = Path(self.directory.name) / "attendance.sqlite3"
        self.db_path_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_path_patch.start()
        self.addCleanup(self.db_path_patch.stop)
        self.entitlement_patch = patch.object(entitlement, "_gate", None)
        self.entitlement_patch.start()
        self.addCleanup(self.entitlement_patch.stop)
        database.init_db()

    @staticmethod
    def _sample(value):
        return np.asarray([value, value + 0.25, value + 0.5], dtype=np.float32)

    def _counts(self):
        with closing(database.get_connection()) as conn:
            return (
                conn.execute("SELECT COUNT(*) FROM students").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0],
            )

    def test_student_and_all_embeddings_are_saved_together(self):
        student_id = database.add_student_with_embeddings(
            "ENROLL-001",
            "Synthetic Student",
            [self._sample(0.1), self._sample(0.2), self._sample(0.3)],
        )
        students = database.load_students()
        self.assertEqual(len(students), 1)
        self.assertEqual(students[0]["id"], student_id)
        self.assertEqual(students[0]["student_code"], "ENROLL-001")
        self.assertEqual(len(students[0]["embeddings"]), 3)
        self.assertEqual(self._counts(), (1, 3))

    def test_failed_embedding_write_rolls_back_student_and_partial_samples(self):
        with closing(database.get_connection()) as conn:
            conn.execute(
                """
                CREATE TRIGGER fail_after_first_face_sample
                BEFORE INSERT ON face_embeddings
                WHEN (
                    SELECT COUNT(*) FROM face_embeddings
                    WHERE student_id = NEW.student_id
                ) >= 1
                BEGIN
                    SELECT RAISE(ABORT, 'simulated face-sample write failure');
                END
                """
            )
            conn.commit()

        with self.assertRaisesRegex(sqlite3.IntegrityError, "simulated face-sample write failure"):
            database.add_student_with_embeddings(
                "ENROLL-ROLLBACK",
                "Must Not Persist",
                [self._sample(0.1), self._sample(0.2), self._sample(0.3)],
            )
        self.assertEqual(self._counts(), (0, 0))

    def test_failed_reenrollment_keeps_previous_embeddings(self):
        student_id = database.add_student("ENROLL-KEEP", "Existing Student")
        old_sample = self._sample(0.7)
        database.add_embedding(student_id, old_sample)

        with closing(database.get_connection()) as conn:
            conn.execute(
                """
                CREATE TRIGGER reject_replacement_samples
                BEFORE INSERT ON face_embeddings
                BEGIN
                    SELECT RAISE(ABORT, 'simulated replacement failure');
                END
                """
            )
            conn.commit()

        with self.assertRaisesRegex(sqlite3.IntegrityError, "simulated replacement failure"):
            database.replace_student_embeddings(
                student_id,
                [self._sample(0.1), self._sample(0.2)],
            )

        students = database.load_students()
        self.assertEqual(len(students), 1)
        self.assertEqual(len(students[0]["embeddings"]), 1)
        np.testing.assert_array_equal(students[0]["embeddings"][0], old_sample)

    def test_invalid_or_empty_samples_are_rejected_before_student_creation(self):
        with self.assertRaisesRegex(ValueError, "At least one valid face sample"):
            database.add_student_with_embeddings("ENROLL-EMPTY", "Empty", [])
        with self.assertRaisesRegex(ValueError, "invalid numeric"):
            database.add_student_with_embeddings(
                "ENROLL-NAN",
                "Invalid",
                [np.asarray([0.1, np.nan], dtype=np.float32)],
            )
        with self.assertRaisesRegex(ValueError, "same vector size"):
            database.add_student_with_embeddings(
                "ENROLL-SHAPE",
                "Mismatched",
                [np.asarray([0.1, 0.2], dtype=np.float32), np.asarray([0.3], dtype=np.float32)],
            )
        self.assertEqual(self._counts(), (0, 0))

    def test_missing_class_rolls_back_student_and_samples(self):
        with self.assertRaises(ValueError):
            database.add_student_with_embeddings(
                "ENROLL-BAD-CLASS",
                "Invalid Class",
                [self._sample(0.1)],
                class_id=999999,
            )
        self.assertEqual(self._counts(), (0, 0))


if __name__ == "__main__":
    unittest.main()
