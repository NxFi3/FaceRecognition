import time
import unittest
from unittest.mock import patch

import numpy as np

from src.engine.smart_camera_worker import SmartCameraWorker
from src.engine.smart_tracking import PersonTrack


class FakeMatcher:
    def __init__(self):
        self.encode_calls = 0
        self.embedding = np.asarray([1.0, 0.0, 0.0, 0.0], dtype=np.float32)

    def recognize_crop_with_embedding(self, crop, bbox):
        self.encode_calls += 1
        return self.embedding.copy(), self.match_embedding(self.embedding)

    @staticmethod
    def match_embedding(embedding):
        return 0, 0.10, 0.12


class FaceRecognitionPipelineTests(unittest.TestCase):
    def test_unknown_person_track_noise_is_not_persisted(self):
        worker = SmartCameraWorker()
        with patch("src.engine.smart_camera_worker.add_event") as add_event:
            result = worker._record_event(
                event_type="UNKNOWN_PERSON",
                student_id=None,
                track_id=9876,
                camera_id="camera_0",
                confidence=0.0,
                metadata={"direction": "enter"},
            )
        self.assertIsNone(result)
        add_event.assert_not_called()

    def test_meaningful_event_is_still_persisted(self):
        worker = SmartCameraWorker()
        with patch("src.engine.smart_camera_worker.add_event", return_value=321) as add_event:
            result = worker._record_event(
                event_type="PERSON_EXITED",
                student_id=12,
                track_id=7,
                camera_id="camera_0",
                confidence=0.91,
                metadata={},
            )
        self.assertEqual(result, 321)
        add_event.assert_called_once()

    def _track(self):
        return PersonTrack(
            track_id=7,
            bbox=(10, 10, 100, 180),
        )

    def test_recognition_task_preserves_observation_frame(self):
        matcher = FakeMatcher()
        started = time.perf_counter() - 0.01
        result = SmartCameraWorker._recognize_task(
            matcher,
            np.zeros((32, 32, 3), dtype=np.uint8),
            (0, 0, 32, 32),
            7,
            123,
            started,
        )

        self.assertEqual(result[0], 7)
        self.assertEqual(result[1], 123)
        np.testing.assert_allclose(result[2], matcher.embedding)
        self.assertEqual(result[3], (0, 0.10, 0.12))
        self.assertGreaterEqual(result[4], result[5])
        self.assertGreaterEqual(result[5], 0.0)

    def test_embedding_is_attached_to_its_own_frame_not_completion_frame(self):
        track = self._track()
        crop = np.zeros((40, 30, 3), dtype=np.uint8)
        track.cache_face_sample(crop, (0, 0, 30, 40), 0.8, frame_no=10)
        track.cache_face_sample(crop, (0, 0, 30, 40), 0.7, frame_no=20)
        embedding = np.asarray([0.5, 0.5, 0.5], dtype=np.float32)

        attached = SmartCameraWorker._cache_recognition_embedding(
            track,
            frame_no=10,
            embedding=embedding,
        )

        self.assertTrue(attached)
        by_frame = {sample["frame_no"]: sample for sample in track.face_cache}
        np.testing.assert_allclose(by_frame[10]["embedding"], embedding)
        self.assertIsNone(by_frame[20]["embedding"])

    def test_finalizer_reuses_embeddings_without_running_encoder_again(self):
        matcher = FakeMatcher()
        embedding = matcher.embedding.copy()
        samples = [
            {
                "crop": np.zeros((20, 20, 3), dtype=np.uint8),
                "bbox": (0, 0, 20, 20),
                "quality": 0.9,
                "frame_no": 1,
                "embedding": embedding.copy(),
            },
            {
                "crop": np.zeros((20, 20, 3), dtype=np.uint8),
                "bbox": (0, 0, 20, 20),
                "quality": 0.8,
                "frame_no": 2,
                "embedding": embedding.copy(),
            },
        ]

        result = SmartCameraWorker._finalize_face_cache_task(matcher, samples)

        self.assertIsNotNone(result)
        self.assertEqual(result["student_index"], 0)
        self.assertEqual(result["votes"], 2)
        self.assertEqual(matcher.encode_calls, 0)

    def test_cache_finalizer_returns_track_and_camera_context(self):
        matcher = FakeMatcher()
        track = self._track()
        samples = [
            {
                "crop": np.zeros((20, 20, 3), dtype=np.uint8),
                "bbox": (0, 0, 20, 20),
                "quality": 0.9,
                "frame_no": i,
                "embedding": matcher.embedding.copy(),
            }
            for i in (1, 2)
        ]

        returned_track, result, camera_id = (
            SmartCameraWorker._finalize_face_cache_for_track(
                matcher, samples, track, 4
            )
        )

        self.assertIs(returned_track, track)
        self.assertEqual(camera_id, 4)
        self.assertIsNotNone(result)

    def test_strong_photo_like_match_still_requires_three_confirmations(self):
        track = self._track()
        crop = np.zeros((80, 80, 3), dtype=np.uint8)
        track.cache_face_sample(crop, (0, 0, 80, 80), 0.8, frame_no=30)

        strong = SmartCameraWorker._required_face_confirmations(
            track, 30, distance=0.30, stability=0.35
        )
        weak = SmartCameraWorker._required_face_confirmations(
            track, 30, distance=0.45, stability=0.48
        )

        self.assertEqual(strong, 3)
        self.assertEqual(weak, 3)


class DeferredCrossingTests(unittest.TestCase):
    def test_face_identity_does_not_execute_a_pending_exit_twice(self):
        from src.engine.smart_events import AttendanceEventEngine, AttendancePresenceState

        state = AttendancePresenceState()
        state.present.add(17)
        emitted = []

        def add_event_callback(**event):
            emitted.append(event)
            return len(emitted)

        engine = AttendanceEventEngine(
            add_event_callback=add_event_callback,
            mark_attendance_callback=lambda *args: True,
            get_setting_callback=lambda key, default=None: default,
            state=state,
        )
        track = PersonTrack(
            track_id=9,
            bbox=(10, 10, 100, 180),
            student_id=17,
            student_code="A017",
            student_name="Student",
            identity_confidence=0.9,
            last_crossing="exit",
            metadata={"pending_crossing": {"direction": "exit"}},
        )

        engine.on_face_identity(track, 17, "A017", "Student", 0.98, "cam_exit")
        self.assertIn(17, state.present)
        self.assertEqual(
            sum(event["event_type"] == "PERSON_EXITED" for event in emitted),
            0,
        )

        track.metadata.pop("pending_crossing")
        engine.on_crossing(track, "exit", "cam_exit")
        self.assertNotIn(17, state.present)
        self.assertEqual(
            sum(event["event_type"] == "PERSON_EXITED" for event in emitted),
            1,
        )


if __name__ == "__main__":
    unittest.main()
