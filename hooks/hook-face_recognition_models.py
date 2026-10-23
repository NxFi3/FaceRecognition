"""Collect original assets without executing the package's pkg_resources import."""
from importlib.metadata import distribution
from pathlib import Path
root = Path(distribution('face_recognition_models').locate_file('face_recognition_models/models'))
datas = [(str(path), 'face_recognition_models/models') for path in root.glob('*.dat')]
if len(datas) != 4:
    raise RuntimeError('Expected all four original face_recognition_models assets')
