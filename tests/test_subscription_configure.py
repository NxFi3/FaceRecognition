import base64
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from src.cloud.client import ClientConfig
from deploy import configure


class ConfigureTests(unittest.TestCase):
    def test_generated_settings_private_and_public_key_matches_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # Redirect only the generator's filesystem root, never real deploy/.env.
            fake_script = root / "configure.py"
            fake_script.write_text(Path(configure.__file__).read_text())
            result = subprocess.run([sys.executable, str(fake_script), "--domain", "schools.example.com", "--email", "owner@example.com"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            values = dict(line.split("=", 1) for line in (root / ".env").read_text().splitlines())
            client = ClientConfig.load(root / "generated" / "subscription-client.json")
            key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(values["LICENSE_SIGNING_KEY"]))
            expected = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            self.assertEqual(client.public_key, expected)
            self.assertNotEqual(values["POSTGRES_PASSWORD"], values["POSTGRES_ADMIN_PASSWORD"])
            self.assertNotIn(values["LICENSE_SIGNING_KEY"], result.stdout)
            self.assertEqual(values["RELEASE_ROOT"], "/data/releases")
            self.assertEqual(values["MANUAL_PAYMENT_INSTRUCTIONS"], "")
            self.assertTrue((root / "releases").is_dir())
            before = (root / ".env").read_bytes()
            repeated = subprocess.run([sys.executable, str(fake_script), "--domain", "schools.example.com", "--email", "owner@example.com"], capture_output=True)
            self.assertNotEqual(repeated.returncode, 0)
            self.assertEqual((root / ".env").read_bytes(), before)
    def test_generator_rejects_shell_and_url_syntax_in_hostname(self):
        with patch.object(sys, "argv", ["configure", "--domain", "https://invalid.example/path", "--email", "owner@example.com"]):
            with self.assertRaises(SystemExit) as failure:
                configure.main()
            self.assertEqual(failure.exception.code, 2)
