import tempfile
import unittest
from pathlib import Path

import numpy as np

from src.engine.smart_tracking import PersonDetector, make_person_model_candidates


class OnnxPersonDetectorTests(unittest.TestCase):
    def test_letterbox_returns_rgb_nchw_float32_tensor(self):
        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        tensor, scale, pad_left, pad_top = PersonDetector._letterbox_onnx(
            frame, target_width=200, target_height=200
        )

        self.assertEqual(tensor.shape, (1, 3, 200, 200))
        self.assertEqual(tensor.dtype, np.float32)
        self.assertTrue(tensor.flags.c_contiguous)
        self.assertAlmostEqual(scale, 1.0)
        self.assertEqual(pad_left, 0)
        self.assertEqual(pad_top, 50)

    def test_decode_keeps_people_and_maps_boxes_back_to_frame(self):
        detector = PersonDetector.__new__(PersonDetector)
        detector.conf = 0.20
        frame_shape = (100, 200, 3)
        output = np.asarray(
            [[
                [50, 60, 150, 150, 0.95, 0],  # Person, large enough.
                [10, 75, 30, 95, 0.99, 0],    # Person, too narrow after resize.
                [40, 60, 170, 150, 0.99, 2],   # Not the COCO person class.
                [40, 60, 170, 150, 0.10, 0],  # Below configured confidence.
                [0, 0, 200, 200, float("nan"), 0],
            ]],
            dtype=np.float32,
        )

        detections = detector._decode_onnx_output(
            output,
            frame_shape=frame_shape,
            scale=1.0,
            pad_left=0,
            pad_top=50,
        )

        self.assertEqual(len(detections), 1)
        self.assertEqual(detections[0]["bbox"], (50, 10, 150, 100))
        self.assertAlmostEqual(detections[0]["confidence"], 0.95, places=5)

    def test_decode_rejects_unexpected_output_layout(self):
        detector = PersonDetector.__new__(PersonDetector)
        detector.conf = 0.2
        with self.assertRaisesRegex(RuntimeError, r"Expected [1, N, 6]"):
            detector._decode_onnx_output(
                np.zeros((1, 84, 8400), dtype=np.float32),
                frame_shape=(480, 640, 3),
                scale=1.0,
                pad_left=0,
                pad_top=0,
            )

    def test_onnx_model_is_preferred_when_both_formats_exist(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            models = root / "models"
            models.mkdir()
            onnx_path = models / "yolo26n.onnx"
            root_onnx_path = root / "yolo26n.onnx"
            pt_path = models / "yolo26n.pt"
            onnx_path.touch()
            root_onnx_path.touch()
            pt_path.touch()

            candidates = make_person_model_candidates(root)

            self.assertEqual(candidates[0], onnx_path)
            self.assertEqual(candidates[1], root_onnx_path)
            self.assertEqual(candidates[2], pt_path)

    def test_pt_fallback_remains_available_when_onnx_is_absent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            models = root / "models"
            models.mkdir()
            pt_path = models / "yolo26n.pt"
            pt_path.touch()

            candidates = make_person_model_candidates(root)

            self.assertEqual(candidates[0], pt_path)


if __name__ == "__main__":
    unittest.main()
