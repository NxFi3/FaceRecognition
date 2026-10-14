import tempfile
import unittest
from pathlib import Path

import numpy as np

from src.engine.smart_tracking import (
    bbox_center,
    cosine,
    foot_point,
    iou,
    make_person_model_candidates,
    PersonTrack,
    SmartPersonTracker,
)


class TrackingPrimitiveTests(unittest.TestCase):
    def test_iou_for_identical_boxes(self):
        self.assertAlmostEqual(iou((0, 0, 10, 10), (0, 0, 10, 10)), 1.0)

    def test_iou_for_non_overlapping_boxes(self):
        self.assertEqual(iou((0, 0, 10, 10), (20, 20, 30, 30)), 0.0)

    def test_iou_for_partial_overlap(self):
        self.assertAlmostEqual(iou((0, 0, 10, 10), (5, 0, 15, 10)), 1.0 / 3.0)

    def test_cosine_similarity_handles_valid_and_missing_vectors(self):
        self.assertAlmostEqual(cosine(np.array([1, 0]), np.array([1, 0])), 1.0)
        self.assertAlmostEqual(cosine(np.array([1, 0]), np.array([0, 1])), 0.0)
        self.assertEqual(cosine(None, np.array([1, 0])), 0.0)

    def test_bbox_points(self):
        self.assertEqual(bbox_center((0, 0, 10, 20)), (5.0, 10.0))
        self.assertEqual(foot_point((0, 0, 10, 20)), (5.0, 20.0))

    def test_model_discovery_prefers_models_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            models = root / "models"
            models.mkdir()
            (models / "yolo26n.pt").touch()
            (root / "yolo26n.pt").touch()

            candidates = make_person_model_candidates(root)

            self.assertEqual(candidates[0], models / "yolo26n.pt")
            self.assertEqual(candidates[1], root / "yolo26n.pt")


class _FakeReID:
    def __init__(self):
        self.calls = 0

    def embed_batch(self, images):
        self.calls += 1
        return np.ones((len(images), 4), dtype=np.float32)


class TrackerReIDSchedulingTests(unittest.TestCase):
    def test_stale_track_without_appearance_does_not_force_every_reid_call(self):
        reid = _FakeReID()
        tracker = SmartPersonTracker(reid=reid, reid_interval=10)
        tracker.tracks[1] = PersonTrack(
            track_id=1,
            bbox=(110, 110, 190, 190),
            appearance=None,
            misses=1,
        )
        frame = np.zeros((200, 200, 3), dtype=np.uint8)
        detections = [{"bbox": (10, 10, 90, 90), "confidence": 0.9}]

        tracker.update(detections, frame, frame_no=3)

        self.assertEqual(reid.calls, 0)
        self.assertEqual(tracker.last_reid_reason, "throttled")

    def test_active_track_without_appearance_triggers_bootstrap_reid(self):
        reid = _FakeReID()
        tracker = SmartPersonTracker(reid=reid, reid_interval=10)
        tracker.tracks[1] = PersonTrack(
            track_id=1,
            bbox=(10, 10, 90, 180),
            appearance=None,
            misses=0,
        )
        frame = np.zeros((200, 200, 3), dtype=np.uint8)
        detections = [{"bbox": (10, 10, 90, 180), "confidence": 0.9}]

        tracker.update(detections, frame, frame_no=3)

        self.assertEqual(reid.calls, 1)
        self.assertEqual(tracker.last_reid_reason, "active_missing_appearance")
        self.assertIsNotNone(tracker.tracks[1].appearance)


if __name__ == "__main__":
    unittest.main()
