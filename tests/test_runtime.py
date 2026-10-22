import threading
import time
import numpy as np
import pytest
from src.runtime import BoundedExecutor, DetectionSchedule, profile
from src.profiling import Metrics


def test_admission_and_cancel_release_slots():
    pool = BoundedExecutor(1, 2)
    gate = threading.Event()
    started = threading.Event()
    def blocking():
        started.set()
        gate.wait(2)
    try:
        first = pool.submit(blocking)
        assert started.wait(1)
        waiting = pool.submit(lambda: 42)
        assert pool.pending == 2
        assert pool.submit(lambda: 1) is None
        assert waiting.cancel()
        replacement = pool.submit(lambda: 42)
        assert replacement is not None
        gate.set()
        first.result(1)
        assert replacement.result(1) == 42
    finally:
        gate.set()
        pool.shutdown()
    assert pool.pending == 0
    with pytest.raises(RuntimeError):
        pool.submit(lambda: 0)
    assert pool.pending == 0


def test_exception_releases_capacity():
    pool = BoundedExecutor(1, 1)
    try:
        f = pool.submit(lambda: 1 / 0)
        with pytest.raises(ZeroDivisionError):
            f.result(1)
    finally:
        pool.shutdown()
    assert pool.pending == 0


def test_empty_scene_backoff_and_motion_wakeup():
    frame = np.zeros((720, 1280, 3), np.uint8)
    scheduler = DetectionSchedule(3, .3)
    assert scheduler.due(frame, 1, {}, now=0)
    assert not scheduler.due(frame, 3, {}, now=.1)
    assert scheduler.due(frame, 4, {}, now=.31)  # bounded slow-arrival delay
    assert scheduler.due(frame + 100, 6, {}, now=.32)
    assert scheduler.due(frame, 9, {1: object()}, now=.33)
    assert not scheduler.due(frame, 10, {1: object()}, now=.34)


def test_profile_and_metrics_bounded(monkeypatch):
    monkeypatch.setenv('FACE_RUNTIME_PROFILE', 'low-power')
    assert profile().torch_threads == 1
    m = Metrics(True)
    for _ in range(3000):
        m.observe('test', .001)
    m.count('camera.1.processed')
    m.gauge('depth', 2)
    report = m.snapshot()
    assert report['stages_ms']['test'] == dict(samples=2048, p50=1, p95=1, mean=1)
    disabled = Metrics()
    disabled.observe('test', 1)
    assert not disabled.samples


def test_expired_optional_work_never_calls_model():
    from src.profiling import StaleWork, measured_job
    with pytest.raises(StaleWork):
        measured_job(time.monotonic() - 1, 'test', lambda: pytest.fail('expired job ran'), max_queue_delay=.5)
    assert measured_job(time.monotonic() - 1, 'critical', lambda: 42) == 42


def test_detector_bounds_ultralytics_lazy_thread_reset(monkeypatch):
    import sys
    import types
    from src.smart_tracking import PersonDetector
    torch_utils = types.SimpleNamespace(NUM_THREADS=8)
    monkeypatch.setitem(sys.modules, 'ultralytics', types.SimpleNamespace(YOLO=lambda path: object()))
    monkeypatch.setitem(sys.modules, 'ultralytics.utils', types.SimpleNamespace(torch_utils=torch_utils))
    monkeypatch.setenv('FACE_RUNTIME_PROFILE', 'low-power')
    monkeypatch.delenv('FACE_TORCH_THREADS', raising=False)
    PersonDetector('unused.pt')
    assert torch_utils.NUM_THREADS == 1


def test_tentative_tracks_expire_early_but_mature_tracks_keep_occlusion_window():
    from src.smart_tracking import SmartPersonTracker, PersonTrack, MAX_MISSES
    tracker = SmartPersonTracker()
    tracker.tracks = {1: PersonTrack(1, (1, 1, 60, 60)),
                      2: PersonTrack(2, (100, 1, 160, 60), hits=3)}
    frame = np.zeros((200, 200, 3), np.uint8)
    for n in range(1, 4):
        tracker.update([], frame, n)
    assert 1 not in tracker.tracks and 2 in tracker.tracks
    for n in range(4, MAX_MISSES + 2):
        tracker.update([], frame, n)
    assert not tracker.tracks


def test_reporter_writes_final_snapshot_on_normal_exit(tmp_path):
    import json
    import os
    import subprocess
    import sys
    path = tmp_path / 'metrics.jsonl'
    env = dict(os.environ, FACE_PROFILE_OUTPUT=str(path))
    subprocess.run([sys.executable, '-c',
        "from src.profiling import metrics; metrics.count('camera.1.processed'); metrics.observe('camera.1.total', .01)"],
        check=True, env=env, timeout=5)
    report = json.loads(path.read_text().splitlines()[-1])
    assert report['counts']['camera.1.processed'] == 1
    assert report['stages_ms']['camera.1.total']['p95'] == 10
