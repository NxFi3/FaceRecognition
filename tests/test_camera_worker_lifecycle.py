"""Regression checks for camera-worker ownership across enrollment.

These checks are intentionally hardware-free: the bug can be caused by thread
lifecycle ordering, so its invariants must be testable without a physical camera.
"""
import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def source_for(path):
    return (ROOT / path).read_text(encoding="utf-8")


def module_for(path):
    return ast.parse(source_for(path), filename=path)


def class_node(tree, name):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name!r} not found")


def method_node(cls, name):
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"method {cls.name}.{name} not found")


def sets_running_true(node):
    for child in ast.walk(node):
        if not isinstance(child, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
            and target.attr == "_running"
            for target in child.targets
        ):
            continue
        if isinstance(child.value, ast.Constant) and child.value.value is True:
            return True
    return False


class CameraWorkerLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_source = source_for("src/app.py")
        cls.app_tree = ast.parse(cls.app_source, filename="src/app.py")
        cls.smart_source = source_for("src/engine/smart_camera_worker.py")
        cls.smart_tree = ast.parse(
            cls.smart_source, filename="src/engine/smart_camera_worker.py"
        )

    def test_camera_workers_do_not_overwrite_an_early_stop_request(self):
        cases = (
            (self.app_tree, "CameraWorker"),
            (self.app_tree, "EnrollmentWorker"),
            (self.app_tree, "EnrollmentPreviewWorker"),
            (self.smart_tree, "SmartCameraWorker"),
        )
        for tree, class_name in cases:
            with self.subTest(worker=class_name):
                cls = class_node(tree, class_name)
                init = method_node(cls, "__init__")
                run = method_node(cls, "run")
                self.assertTrue(
                    sets_running_true(init),
                    "running state must be initialized before QThread.start()",
                )
                self.assertFalse(
                    sets_running_true(run),
                    "run() must not undo stop() when cancellation races thread startup",
                )

    def test_enrollment_close_paths_release_worker_before_dismissing_dialog(self):
        cls = class_node(self.app_tree, "EnrollmentDialog")
        for method_name in ("accept", "reject", "closeEvent"):
            with self.subTest(method=method_name):
                method = method_node(cls, method_name)
                segment = ast.get_source_segment(self.app_source, method)
                self.assertIn("self._stop_workers()", segment)

        helper = method_node(cls, "_stop_workers")
        helper_source = ast.get_source_segment(self.app_source, helper)
        self.assertIn("worker.stop()", helper_source)
        self.assertIn("worker.wait(timeout_ms)", helper_source)
        self.assertIn("worker.isRunning()", helper_source)

    def test_main_camera_stop_keeps_reference_when_thread_times_out(self):
        cls = class_node(self.app_tree, "MainWindow")
        method = method_node(cls, "stop_camera")
        segment = ast.get_source_segment(self.app_source, method)
        self.assertIn("if not worker.wait(5000):", segment)
        self.assertIn("return False", segment)
        self.assertLess(
            segment.index("return False"),
            segment.index("self.camera_workers.clear()"),
            "a live QThread must not be forgotten and raced by a new camera owner",
        )

    def test_enrollment_entry_points_require_main_camera_to_stop(self):
        cls = class_node(self.app_tree, "MainWindow")
        for method_name in ("add_student_dialog", "reenroll_selected_student"):
            with self.subTest(method=method_name):
                method = method_node(cls, method_name)
                segment = ast.get_source_segment(self.app_source, method)
                self.assertIn("if not self.stop_camera():", segment)
                self.assertIn('tr("Camera is busy")', segment)

    def test_automatic_camera_restart_is_bounded_and_uses_backoff(self):
        cls = class_node(self.app_tree, "MainWindow")
        method = method_node(cls, "_camera_worker_finished")
        segment = ast.get_source_segment(self.app_source, method)
        self.assertIn("self._camera_restart_attempts >= 3", segment)
        self.assertIn("delay_ms = min(1500 * (2 ** (self._camera_restart_attempts - 1)), 6000)", segment)
        self.assertIn("QTimer.singleShot(delay_ms, restart)", segment)
        self.assertIn("self.start_camera(reset_retry_budget=False)", segment)


if __name__ == "__main__":
    unittest.main()
