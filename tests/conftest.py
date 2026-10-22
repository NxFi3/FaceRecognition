import importlib
import sys
import types
import pytest


@pytest.fixture
def worker_module(monkeypatch):
    """Real orchestration, fake Qt/dlib imports; no biometric model execution."""
    class Signal:
        def __init__(self, *args): self.values = []
        def emit(self, *args): self.values.append(args)
    class Thread:
        def __init__(self, *args): self.interrupted = False
        def requestInterruption(self): self.interrupted = True
        def isInterruptionRequested(self): return self.interrupted
    monkeypatch.setitem(sys.modules, 'dlib', types.ModuleType('dlib'))
    monkeypatch.setitem(sys.modules, 'PySide6', types.ModuleType('PySide6'))
    monkeypatch.setitem(sys.modules, 'PySide6.QtCore', types.SimpleNamespace(QThread=Thread, Signal=Signal))
    monkeypatch.setitem(sys.modules, 'PySide6.QtGui', types.SimpleNamespace(QImage=object))
    sys.modules.pop('src.smart_camera_worker', None)
    module = importlib.import_module('src.smart_camera_worker')
    yield module
    sys.modules.pop('src.smart_camera_worker', None)


@pytest.fixture
def app_helpers():
    # Load the existing pure image/config helpers without importing Qt/dlib.
    import ast
    from pathlib import Path
    import cv2
    import numpy as np
    tree = ast.parse(Path('src/app.py').read_text())
    names = {'_prepare_face_crop', '_camera_prefers_raw_bayer', '_camera_raw_bayer_setting'}
    constants = {'FACE_CROP_PADDING', 'SMALL_FACE_THRESHOLD', 'SMALL_FACE_UPSCALE'}
    nodes = [node for node in tree.body if
             isinstance(node, ast.FunctionDef) and node.name in names or
             isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in constants for t in node.targets)]
    namespace = dict(cv2=cv2, np=np, sys=sys, Path=Path)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'app-helpers', 'exec'), namespace)
    return namespace
