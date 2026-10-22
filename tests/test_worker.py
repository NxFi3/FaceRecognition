"""Exercise real worker orchestration with opaque models and Qt replaced by fakes."""
import importlib
import sys
import time
import types
from concurrent.futures import Future
from pathlib import Path
import numpy as np
from src.smart_tracking import PersonTrack


class Signal:
    def __init__(self, *args):
        self.values = []
    def emit(self, *args):
        self.values.append(args)


class Thread:
    def __init__(self, *args):
        pass
    def requestInterruption(self):
        pass
    def isInterruptionRequested(self):
        return False


def test_cache_completion_keeps_track_context_and_camera(monkeypatch):
    monkeypatch.setitem(sys.modules, 'dlib', types.ModuleType('dlib'))
    monkeypatch.setitem(sys.modules, 'PySide6', types.ModuleType('PySide6'))
    monkeypatch.setitem(sys.modules, 'PySide6.QtCore', types.SimpleNamespace(QThread=Thread, Signal=Signal))
    monkeypatch.setitem(sys.modules, 'PySide6.QtGui', types.SimpleNamespace(QImage=object))
    sys.modules.pop('src.smart_camera_worker', None)
    worker_module = importlib.import_module('src.smart_camera_worker')
    track = PersonTrack(1, (1, 1, 40, 60))
    track.cache_face_sample(np.zeros((32, 32, 3), np.uint8), (0, 0, 32, 32), 1, 0)
    crossings = []
    class Events:
        def __init__(self, **kwargs): pass
        def on_track_seen(self, *args): pass
        def on_track_lost(self, *args): pass
        def on_crossing(self, track, direction, camera):
            crossings.append((track.track_id, direction, camera))
    class Tracker:
        def __init__(self, **kwargs): self.tracks = {1: track}
        def update(self, detections, frame, frame_no):
            track.last_seen_frame = frame_no
            return [track]
    class Detector:
        def __init__(self, *args, **kwargs): pass
        def detect(self, frame): return []
    class FaceDetector:
        def setInputSize(self, size): pass
        def detect(self, frame): return None, None
    class Line:
        orientation, position = 'vertical', .5
        def __init__(self, **kwargs): pass
        def check(self, *args): return 'enter'
    class Executor:
        pending = 0
        def submit(self, fn, *args):
            f = Future()
            f.set_result(fn(*args))
            return f
    fake_app = types.ModuleType('src.app')
    for name, value in dict(CAMERA_HEIGHT=64, CAMERA_WIDTH=64,
        YUNET_MODEL_PATH=Path(__file__), YUNET_NMS_THRESHOLD=.3,
        YUNET_SCORE_THRESHOLD=.6, YUNET_TOP_K=100,
        FaceMatcher=lambda: types.SimpleNamespace(students=[]),
        _open_camera=None, _prepare_face_crop=None,
        get_camera_settings=lambda: (7, 30), frame_to_qimage=lambda frame: frame).items():
        setattr(fake_app, name, value)
    monkeypatch.setitem(sys.modules, 'src.app', fake_app)
    monkeypatch.setattr(worker_module, 'get_setting', lambda key, default=None: default)
    monkeypatch.setattr(worker_module, 'PersonDetector', Detector)
    monkeypatch.setattr(worker_module, 'SmartPersonTracker', Tracker)
    monkeypatch.setattr(worker_module, 'AttendanceEventEngine', Events)
    monkeypatch.setattr(worker_module, 'LineCrossing', Line)
    monkeypatch.setattr(worker_module, '_get_shared_face_executor', lambda: Executor())
    monkeypatch.setattr(worker_module.cv2, 'FaceDetectorYN', types.SimpleNamespace(create=lambda *a: FaceDetector()))
    worker = worker_module.SmartCameraWorker()
    worker._resolve_person_model = lambda: 'unused'
    worker._finalize_face_cache_task = lambda matcher, samples: None
    class Capture:
        width = height = 64
        fps = 30
        def __init__(self, *args, **kwargs): self.seq = 0
        def start(self): pass
        def stop(self, **kwargs): return True
        def wait_for_frame(self, *args, **kwargs):
            self.seq += 1
            if self.seq > 2:
                worker.stop()
                return None, None, None
            return self.seq, np.zeros((64, 64, 3), np.uint8), time.monotonic()
    monkeypatch.setattr(worker_module, 'LatestFrameCapture', Capture)
    try:
        worker.run()
        assert worker.error.values == []
        assert crossings == [(1, 'enter', 7)]
        assert not track.face_cache
        assert 'pending_crossing' not in track.metadata
    finally:
        sys.modules.pop('src.smart_camera_worker', None)
