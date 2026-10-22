import sys
import time
import cv2
import numpy as np
from src.camera_capture import LatestFrameCapture


def test_crop_padding_color_geometry_and_ownership(app_helpers, worker_module):
    frame = np.full((720, 1280, 3), (13, 71, 201), np.uint8)
    crop, bbox = app_helpers['_prepare_face_crop'](frame, (100, 100, 140, 140))
    # Existing 20% padding and 2.5x small-face upscale remain identical.
    assert crop.shape == (140, 140, 3) and bbox == (20, 20, 120, 120)
    assert tuple(crop[70, 70]) == (13, 71, 201)
    compact, compact_bbox = worker_module.SmartCameraWorker._compact_face_sample(crop, bbox)
    track = worker_module.SmartPersonTracker().update(
        [{'bbox': (50, 50, 200, 300), 'confidence': .9}], frame, 1)[0]
    track.cache_face_sample(compact, compact_bbox, .8, 1)
    crop[:] = 0
    assert tuple(track.face_cache[0]['crop'][70, 70]) == (13, 71, 201)
    assert tuple(frame[100, 100]) == (13, 71, 201)


def test_grbg_ps3_eye_mosaic_decodes_to_bgr_without_channel_swap():
    mosaic = np.empty((8, 8), np.uint8)
    mosaic[0::2, 0::2] = mosaic[1::2, 1::2] = 71  # G
    mosaic[0::2, 1::2] = 201  # R in GRBG
    mosaic[1::2, 0::2] = 13   # B
    original = mosaic.copy()
    class Camera:
        def __init__(self): self.reads = 0
        def set(self, *args): return True
        def get(self, prop): return 30 if prop == cv2.CAP_PROP_FPS else 8
        def read(self):
            self.reads += 1
            if self.reads == 1:
                return True, mosaic.reshape(1, -1)
            time.sleep(.01)
            return False, None
        def release(self): pass
    camera = Camera()
    capture = LatestFrameCapture(0, 30, lambda *_: camera, raw_bayer=True)
    capture.start()
    try:
        seq, frame = capture.wait_for_frame(timeout=1)
        assert seq == 1 and tuple(frame[3, 3]) == (13, 71, 201)
        assert np.array_equal(mosaic, original)
    finally:
        assert capture.stop()


def test_known_ps3_raw_default_and_explicit_override(app_helpers, monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setattr(Path, 'read_text', lambda *a, **kw: 'ov534')
    settings = {}
    app_helpers['get_setting'] = lambda key, default=None: settings.get(key, default)
    raw = app_helpers['_camera_raw_bayer_setting']
    assert raw(1, 0, True) and raw(2, 1, True)
    assert not raw(1, 0, False)
    settings['camera_1_raw_bayer'] = '0'
    assert not raw(1, 0, True)
    monkeypatch.setattr(Path, 'read_text', lambda *a, **kw: 'Generic UVC webcam')
    assert not raw(2, 1, True)
