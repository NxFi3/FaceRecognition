"""Regression tests for safe recovery of the isolated local pilot workspace."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import run_local_pilot


class LocalPilotWorkspaceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp_root = Path(temp.name)
        self.repo = self.temp_root / "checkout"
        (self.repo / "models").mkdir(parents=True)
        self.pilot = self.temp_root / "pilot-data"
        self.root_patch = patch.object(run_local_pilot, "ROOT", self.repo)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def mark_pilot(self):
        self.pilot.mkdir(parents=True, exist_ok=True)
        (self.pilot / ".nxfi-local-pilot").write_text(
            "Isolated synthetic NxFi pilot; NOT production.\n",
            encoding="utf-8",
        )

    def test_first_run_creates_isolated_directories_and_model_link(self):
        result = run_local_pilot.workspace(self.pilot)

        self.assertEqual(result, self.pilot.resolve())
        self.assertTrue((self.pilot / ".nxfi-local-pilot").is_file())
        self.assertTrue((self.pilot / "models").is_symlink())
        self.assertEqual(
            (self.pilot / "models").resolve(), (self.repo / "models").resolve()
        )
        for name in ("data", "service", "logs", "exports"):
            self.assertTrue((self.pilot / name).is_dir())

    def test_repairs_dangling_model_link_from_removed_checkout(self):
        self.mark_pilot()
        stale_target = self.temp_root / "removed-checkout" / "models"
        (self.pilot / "models").symlink_to(stale_target, target_is_directory=True)
        self.assertFalse((self.pilot / "models").exists())

        run_local_pilot.workspace(self.pilot)

        self.assertTrue((self.pilot / "models").is_symlink())
        self.assertTrue((self.pilot / "models").exists())
        self.assertEqual(
            (self.pilot / "models").resolve(), (self.repo / "models").resolve()
        )

    def test_refuses_to_replace_live_model_link_to_another_checkout(self):
        self.mark_pilot()
        other_models = self.temp_root / "other-checkout" / "models"
        other_models.mkdir(parents=True)
        (other_models / "model.onnx").write_bytes(b"existing")
        (self.pilot / "models").symlink_to(other_models, target_is_directory=True)

        with self.assertRaisesRegex(RuntimeError, "unexpected"):
            run_local_pilot.workspace(self.pilot)

        self.assertEqual((self.pilot / "models").resolve(), other_models.resolve())

    def test_refuses_nonempty_unmarked_pilot_directory(self):
        self.pilot.mkdir()
        (self.pilot / "user-file.txt").write_text("keep me", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "nonempty, unmarked"):
            run_local_pilot.workspace(self.pilot)

        self.assertEqual(
            (self.pilot / "user-file.txt").read_text(encoding="utf-8"), "keep me"
        )


if __name__ == "__main__":
    unittest.main()
