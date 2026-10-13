import unittest


class EngineImportTests(unittest.TestCase):
    def test_core_engine_modules_import(self):
        from src.engine import camera_capture
        from src.engine import global_identity
        from src.engine import reid
        from src.engine import smart_events
        from src.engine import smart_tracking
        from src.engine import smart_camera_worker

        self.assertTrue(all((
            camera_capture,
            global_identity,
            reid,
            smart_events,
            smart_tracking,
            smart_camera_worker,
        )))

    def test_application_module_imports_without_starting_gui(self):
        from src import app

        self.assertTrue(app.YUNET_MODEL_PATH.exists())


if __name__ == "__main__":
    unittest.main()
