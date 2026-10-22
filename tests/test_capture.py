import queue
import time
import numpy as np
from src.camera_capture import LatestFrameCapture


class Camera:
    def __init__(self):
        self.frames = queue.Queue()
        self.released = False
    def get(self, prop):
        return 30
    def read(self):
        try:
            item = self.frames.get(timeout=.02)
        except queue.Empty:
            return False, None
        return True, item
    def release(self):
        self.released = True


def wait_sequence(capture, expected):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        sequence, frame, timestamp = capture.wait_for_frame(expected-1, .05, True)
        if sequence is not None and sequence >= expected:
            return sequence, frame, timestamp
    raise AssertionError('capture deadline exceeded')


def test_latest_only_no_duplicate_and_restart():
    camera = Camera()
    capture = LatestFrameCapture(0, 30, lambda *_: camera)
    capture.start()
    try:
        frames = [np.full((8, 8, 3), n, np.uint8) for n in range(10)]
        for frame in frames:
            camera.frames.put(frame)
        sequence, frame, timestamp = wait_sequence(capture, 10)
        assert sequence == 10 and frame is frames[-1]
        assert 0 <= time.monotonic() - timestamp < 1
        assert capture.wait_for_frame(sequence, .01) == (None, None)
    finally:
        assert capture.stop()
    assert camera.released
    fresh = Camera()
    capture._open_camera = lambda *_: fresh
    capture.start()
    try:
        assert capture.wait_for_frame(0, .01) == (None, None)
        fresh.frames.put(np.zeros((8, 8, 3), np.uint8))
        assert wait_sequence(capture, 11)[0] == 11
    finally:
        assert capture.stop()


def test_reconnect_after_corrupt_frames():
    broken, good = Camera(), Camera()
    for _ in range(10):
        broken.frames.put(np.zeros((0, 0), np.uint8))
    frame = np.zeros((8, 8, 3), np.uint8)
    good.frames.put(frame)
    cameras = iter([broken, good])
    capture = LatestFrameCapture(0, 30, lambda *_: next(cameras))
    capture.start()
    try:
        assert wait_sequence(capture, 1)[1] is frame
        assert broken.released
    finally:
        assert capture.stop()


def test_two_camera_mailboxes_are_independent():
    cameras = [Camera(), Camera()]
    captures = [LatestFrameCapture(i, 30, lambda camera_id, _: cameras[camera_id]) for i in range(2)]
    for capture in captures:
        capture.start()
    try:
        for i, camera in enumerate(cameras):
            for _ in range(20):
                camera.frames.put(np.full((720, 1280, 3), i, np.uint8))
        for i, capture in enumerate(captures):
            sequence, frame, _ = wait_sequence(capture, 20)
            assert sequence == 20 and frame[0, 0, 0] == i
    finally:
        for capture in captures:
            assert capture.stop()


def test_initial_open_failure_retries_and_stop_interrupts_backoff():
    camera = Camera()
    camera.frames.put(np.zeros((8, 8, 3), np.uint8))
    attempts = []
    def opener(*_):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError('temporarily unplugged')
        return camera
    capture = LatestFrameCapture(0, 30, opener)
    capture.start()
    try:
        assert wait_sequence(capture, 1)[0] == 1
        assert len(attempts) == 2
    finally:
        assert capture.stop()
