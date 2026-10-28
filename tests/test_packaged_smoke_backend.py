import unittest
from unittest.mock import Mock, patch
from pathlib import Path
import tempfile


class PackagedSmokeBackendTests(unittest.TestCase):
    def test_smoke_uses_production_onnx_backend_without_ultralytics_import(self):
        from src import app
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "yolo26n.onnx"
            model.touch()
            with patch("src.engine.smart_tracking.make_person_model_candidates", return_value=[model]), \
                 patch("src.engine.smart_tracking.PersonDetector") as detector, \
                 patch.object(app.dlib, "face_recognition_model_v1"), \
                 patch.object(app.cv2.FaceDetectorYN, "create", return_value=Mock()), \
                 patch("builtins.print"):
                app.run_smoke_test()
            detector.assert_called_once_with(model, device="cpu", imgsz=512)
