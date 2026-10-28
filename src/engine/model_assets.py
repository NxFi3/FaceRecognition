"""Locate the unchanged dlib assets without deprecated/vulnerable pkg_resources.

face_recognition_models 0.3.0 uses pkg_resources solely for these file paths.
This is a packaging compatibility layer, not a model or inference replacement.
"""
from importlib.util import find_spec
from pathlib import Path

try:
    from ..localization import text as tr
except ImportError:
    from localization import text as tr
import sys

_legacy_directory = None


def model_directory():
    if getattr(sys, 'frozen', False):
        root = Path(sys._MEIPASS) / 'face_recognition_models' / 'models'
    elif _legacy_directory is not None:
        return _legacy_directory
    else:
        spec = find_spec('face_recognition_models')
        if spec is None or spec.origin is None:
            raise RuntimeError(tr('Install face_recognition_models==0.3.0'))
        root = Path(spec.origin).parent / 'models'
    return root


def pose_predictor_model_location():
    return str(model_directory() / 'shape_predictor_68_face_landmarks.dat')


def pose_predictor_five_point_model_location():
    return str(model_directory() / 'shape_predictor_5_face_landmarks.dat')


def face_recognition_model_location():
    return str(model_directory() / 'dlib_face_recognition_resnet_model_v1.dat')


def cnn_face_detector_model_location():
    return str(model_directory() / 'mmod_human_face_detector.dat')


def install_legacy_model_compat():
    # The optional legacy src/enroll.py imports face_recognition, which in turn
    # imports the upstream locator. Keep that CLI working with modern setuptools.
    global _legacy_directory
    _legacy_directory = model_directory()
    sys.modules.setdefault('face_recognition_models', sys.modules[__name__])
