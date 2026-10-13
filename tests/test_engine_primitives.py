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


if __name__ == "__main__":
    unittest.main()
