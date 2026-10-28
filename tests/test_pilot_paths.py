"""Pilot paths must never alias the normal on-disk school installation."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.pilot_paths import active_pilot_root


class PilotPathTests(unittest.TestCase):
    def test_normal_installation_does_not_use_pilot(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(active_pilot_root(Path(__file__).resolve().parents[1]))

    def test_unmarked_or_repository_directory_is_rejected(self):
        repo = Path(__file__).resolve().parents[1]
        with patch.dict(os.environ, {"NXFI_LOCAL_PILOT": "1", "NXFI_PILOT_ROOT": str(repo)}):
            with self.assertRaises(RuntimeError):
                active_pilot_root(repo)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"NXFI_LOCAL_PILOT": "1", "NXFI_PILOT_ROOT": tmp}):
                with self.assertRaises(RuntimeError):
                    active_pilot_root(repo)
                (Path(tmp) / ".nxfi-local-pilot").touch()
                self.assertEqual(active_pilot_root(repo), Path(tmp).resolve())
