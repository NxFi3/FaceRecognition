from importlib.metadata import distribution
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.engine import model_assets


class ModelAssetTests(unittest.TestCase):
    def test_exact_upstream_model_paths_preserved(self):
        root = Path(distribution('face_recognition_models').locate_file('face_recognition_models/models'))
        for function, filename in (
            (model_assets.pose_predictor_model_location, 'shape_predictor_68_face_landmarks.dat'),
            (model_assets.pose_predictor_five_point_model_location, 'shape_predictor_5_face_landmarks.dat'),
            (model_assets.face_recognition_model_location, 'dlib_face_recognition_resnet_model_v1.dat'),
            (model_assets.cnn_face_detector_model_location, 'mmod_human_face_detector.dat')):
            self.assertEqual(Path(function()).resolve(), (root / filename).resolve())
            self.assertTrue(Path(function()).is_file())
    def test_frozen_assets_use_bundle_root_not_current_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(model_assets.sys, 'frozen', True, create=True), patch.object(model_assets.sys, '_MEIPASS', directory, create=True):
                self.assertEqual(Path(model_assets.face_recognition_model_location()), Path(directory) / 'face_recognition_models/models/dlib_face_recognition_resnet_model_v1.dat')
