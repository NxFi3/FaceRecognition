import sys
import time
import types
from pathlib import Path
from concurrent.futures import Future
import numpy as np
from src.global_identity import GlobalIdentityManager


def test_short_pass_receives_three_faces_with_bounded_duplicate_suppression(worker_module, app_helpers, monkeypatch):
    module = worker_module
    tracked = []
    class Tracker(module.SmartPersonTracker):
        def update(self, *args, **kwargs):
            result = super().update(*args, **kwargs)
            if result and not tracked:
                tracked.append(result[0])
            return result
    class Detector:
        def __init__(self, *args, **kwargs): pass
        def detect(self, frame):
            return [{'bbox': (1, 1, 100, 125), 'confidence': .9}]
    class FaceDetector:
        def setInputSize(self, *args): pass
        def detect(self, frame):
            return None, np.array([[20, 10, 40, 40, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, .9]], np.float32)
    class Matcher:
        students = [{'id': 7, 'student_code': 'S7', 'name': 'Synthetic'}]
        def recognize_crop_with_embedding(self, crop, bbox):
            # Source pixels must not have preview overlays or color swaps.
            assert tuple(crop[0, 0]) == (11, 77, 201)
            return np.array([1., 0.], np.float32), (0, .2, .1)
    class Executor:
        def __init__(self):
            self.jobs = []
            self.tick = 0
            self.attempts = []
            self.peak = 0
        @property
        def pending(self): return sum(not job[0].done() for job in self.jobs)
        def submit(self, fn, *args, **kwargs):
            assert self.pending == 0  # one track must never duplicate an in-flight job
            future = Future()
            self.jobs.append((future, self.tick + 4, fn, args, kwargs))
            self.attempts.append((self.tick, kwargs))
            self.peak = max(self.peak, self.pending)
            return future
        def advance(self):
            self.tick += 1
            for future, due, fn, args, kwargs in self.jobs:
                if not future.done() and self.tick >= due:
                    future.set_result(fn(*args, **kwargs))
    executor = Executor()
    matcher = Matcher()
    app = types.ModuleType('src.app')
    for name, value in dict(CAMERA_WIDTH=128, CAMERA_HEIGHT=128, YUNET_MODEL_PATH=Path(__file__),
            YUNET_NMS_THRESHOLD=.3, YUNET_SCORE_THRESHOLD=.6, YUNET_TOP_K=100,
            FaceMatcher=lambda: matcher, _open_camera=None,
            _prepare_face_crop=app_helpers['_prepare_face_crop'], get_camera_settings=lambda: (1, 30),
            frame_to_qimage=lambda frame: frame).items():
        setattr(app, name, value)
    monkeypatch.setitem(sys.modules, 'src.app', app)
    monkeypatch.setattr(module, 'get_setting', lambda key, default=None: default)
    monkeypatch.setattr(module, 'PersonDetector', Detector)
    monkeypatch.setattr(module, 'SmartPersonTracker', Tracker)
    monkeypatch.setattr(module.cv2, 'FaceDetectorYN', types.SimpleNamespace(create=lambda *a: FaceDetector()))
    monkeypatch.setattr(module, '_get_shared_face_executor', lambda: executor)
    # Avoid optional backend initialization; association itself remains real/motion-only.
    monkeypatch.setitem(sys.modules, 'torch', None)
    worker = module.SmartCameraWorker(camera_config={'role': 'observe'}, identity_manager=GlobalIdentityManager())
    worker._resolve_person_model = lambda: 'unused'
    monkeypatch.setattr(module, 'add_event', lambda **kwargs: 1)
    class Capture:
        width = height = 128
        fps = 30
        def __init__(self, *a, **kw): pass
        def start(self): pass
        def stop(self, **kwargs): return True
        def wait_for_frame(self, *args, **kwargs):
            executor.advance()
            if executor.tick > 16:
                worker.stop()
                return None, None, None
            return executor.tick, np.full((128, 128, 3), (11, 77, 201), np.uint8), time.monotonic()
    monkeypatch.setattr(module, 'LatestFrameCapture', Capture)
    worker.run()
    assert not worker.error.values
    assert tracked[0].student_id == 7
    assert [n for n, _ in executor.attempts] == [2, 6, 10]
    assert all(kwargs['max_queue_delay'] is None for _, kwargs in executor.attempts)
    assert executor.peak == 1
    assert len(tracked[0].face_cache) == 3
