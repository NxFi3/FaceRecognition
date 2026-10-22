"""Test UI coordination methods without requiring Qt or creating a window."""
import ast
from pathlib import Path
import threading
from types import SimpleNamespace


def methods():
    tree = ast.parse(Path('src/app.py').read_text())
    window = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'MainWindow')
    names = {'stop_camera', '_reap_camera_workers', '_poll_camera_stop', 'restart_camera', '_render_latest_camera_previews'}
    window.body = [n for n in window.body if isinstance(n, ast.FunctionDef) and n.name in names]
    window.bases = []
    callbacks = []
    namespace = {'QTimer': SimpleNamespace(singleShot=lambda ms, fn: callbacks.append(fn))}
    exec(compile(ast.Module(body=[window], type_ignores=[]), 'gui-methods', 'exec'), namespace)
    return namespace['MainWindow'], callbacks


def test_shutdown_is_nonblocking_and_restart_waits_for_workers():
    Window, callbacks = methods()
    window = Window()
    worker = SimpleNamespace(running=True, stop=lambda: None)
    worker.isRunning = lambda: worker.running
    window.camera_workers = {1: worker}
    window._latest_camera_images_lock = threading.Lock()
    window._latest_camera_images = {1: object()}
    window._stop_poll_scheduled = window._restart_requested = window._close_pending = False
    started = []
    window.start_camera = lambda: started.append(True)
    window.restart_camera()
    assert window.camera_workers == {1: worker}
    assert not window._latest_camera_images
    assert not started and len(callbacks) == 1
    worker.running = False
    callbacks.pop()()
    assert not window.camera_workers and started == [True]


def test_preview_mailbox_is_consumed_once():
    Window, _ = methods()
    window = Window()
    window._latest_camera_images_lock = threading.Lock()
    window._latest_camera_images = {1: object()}
    window.camera_previews = {}  # No visible widgets needed to exercise consumption.
    window._render_latest_camera_previews()
    assert not window._latest_camera_images
    window._render_latest_camera_previews()
