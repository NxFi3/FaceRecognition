import unittest
from types import SimpleNamespace

import numpy as np

from src.engine.global_identity import GlobalIdentityManager
from src.engine.smart_events import AttendancePresenceState


def known_track(track_id, student_id, embedding, code="ST", name="Student"):
    return SimpleNamespace(
        track_id=track_id,
        student_id=student_id,
        student_code=code,
        student_name=name,
        appearance=np.asarray(embedding, dtype=np.float32),
    )


def unknown_track(track_id, embedding):
    return SimpleNamespace(
        track_id=track_id,
        student_id=None,
        student_code=None,
        student_name=None,
        appearance=np.asarray(embedding, dtype=np.float32),
    )


class GlobalIdentityDailyTests(unittest.TestCase):
    def setUp(self):
        self.state = AttendancePresenceState()
        self.manager = GlobalIdentityManager(presence_state=self.state)
        self.manager.register_camera("cam_entry", "entry")
        self.manager.register_camera("cam_exit", "exit")

    def test_present_student_can_match_at_exit_after_long_delay(self):
        with self.state.lock:
            self.state.present.add(17)

        entry = known_track(1, 17, [1.0, 0.0, 0.0, 0.0], "A017", "A")
        self.manager.register_face_identity(
            entry,
            "cam_entry",
            17,
            "A017",
            "A",
            now=1000.0,
        )
        self.manager.track_lost(entry, "cam_entry")

        # Age exceeds the short hand-off window. Same-day presence allows
        # a conservative entry -> exit candidate to be evaluated.
        exit_track = unknown_track(2, [1.0, 0.0, 0.0, 0.0])
        match = self.manager.observe_track(
            exit_track,
            "cam_exit",
            now=1000.0 + 60.0 * 60.0,
        )

        self.assertIsNotNone(match)
        self.assertEqual(match.student_id, 17)
        self.assertAlmostEqual(match.similarity, 1.0)

    def test_student_no_longer_present_is_not_a_daily_exit_candidate(self):
        entry = known_track(1, 17, [1.0, 0.0, 0.0, 0.0], "A017", "A")
        self.manager.register_face_identity(
            entry, "cam_entry", 17, "A017", "A", now=1000.0
        )
        self.manager.track_lost(entry, "cam_entry")

        match = self.manager.observe_track(
            unknown_track(2, [1.0, 0.0, 0.0, 0.0]),
            "cam_exit",
            now=1000.0 + 60.0 * 60.0,
        )

        self.assertIsNone(match)

    def test_near_tied_student_appearances_are_left_unresolved(self):
        with self.state.lock:
            self.state.present.update({17, 18})

        first_embedding = np.asarray([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        second_embedding = np.asarray([1.0, 0.002, 0.0, 0.0], dtype=np.float32)
        first = known_track(1, 17, first_embedding, "A017", "A")
        second = known_track(2, 18, second_embedding, "A018", "B")
        self.manager.register_face_identity(
            first, "cam_entry", 17, "A017", "A", now=1000.0
        )
        self.manager.register_face_identity(
            second, "cam_entry", 18, "A018", "B", now=1001.0
        )
        self.manager.track_lost(first, "cam_entry")
        self.manager.track_lost(second, "cam_entry")

        match = self.manager.observe_track(
            unknown_track(3, first_embedding),
            "cam_exit",
            now=1001.0 + 60.0 * 60.0,
        )

        self.assertIsNone(match)

    def test_daily_rollover_clears_profiles_and_presence(self):
        with self.state.lock:
            self.state.present.add(17)

        entry = known_track(1, 17, [1.0, 0.0, 0.0, 0.0], "A017", "A")
        self.manager.register_face_identity(
            entry, "cam_entry", 17, "A017", "A", now=1000.0
        )
        self.assertIn(17, self.manager._profiles)

        # Simulate the manager having been left running over midnight.
        self.manager._current_date = "2000-01-01"
        result = self.manager.observe_track(
            unknown_track(3, [1.0, 0.0, 0.0, 0.0]),
            "cam_exit",
            now=2000.0,
        )

        self.assertIsNone(result)
        self.assertEqual(self.manager._profiles, {})
        self.assertNotIn(17, self.state.present)


    def test_long_delay_requires_strong_appearance_similarity(self):
        with self.state.lock:
            self.state.present.add(17)

        entry = known_track(1, 17, [1.0, 0.0, 0.0, 0.0], "A017", "A")
        self.manager.register_face_identity(
            entry, "cam_entry", 17, "A017", "A", now=1000.0
        )
        self.manager.track_lost(entry, "cam_entry")

        similarity = 0.86
        query = [similarity, (1.0 - similarity ** 2) ** 0.5, 0.0, 0.0]
        match = self.manager.observe_track(
            unknown_track(2, query),
            "cam_exit",
            now=1000.0 + 60.0 * 60.0,
        )

        self.assertIsNone(match)

    def test_provisional_reid_embedding_does_not_pollute_trusted_profile(self):
        entry = known_track(1, 17, [1.0, 0.0, 0.0, 0.0], "A017", "A")
        self.manager.register_face_identity(
            entry, "cam_entry", 17, "A017", "A", now=1000.0
        )
        profile = self.manager._profiles[17]
        original_count = len(profile.embeddings)

        provisional = known_track(2, 17, [0.0, 1.0, 0.0, 0.0], "A017", "A")
        provisional.metadata = {"identity_source": "reid"}
        self.manager.observe_track(provisional, "cam_exit", now=1001.0)

        self.assertEqual(len(profile.embeddings), original_count)
        np.testing.assert_allclose(
            profile.embeddings[0],
            np.asarray([1.0, 0.0, 0.0, 0.0], dtype=np.float32),
        )


if __name__ == "__main__":
    unittest.main()
