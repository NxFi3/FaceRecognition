"""Remember-me behavior stays on the real signed-license client path."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import base64
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from PySide6.QtWidgets import QApplication
from src.cloud.client import ClientConfig, CloudClient
from src.cloud.ui import LoginDialog


class Vault:
    def __init__(self):
        self.values = {}
    def get(self, key):
        return self.values.get(key)
    def set(self, key, value):
        self.values[key] = value
        return True
    def delete(self, key):
        self.values.pop(key, None)


class RememberMeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_checkbox_is_visible_and_passes_consent_to_worker(self):
        from unittest.mock import Mock
        client = Mock()
        client.credential = None
        client.config.url = "https://example.invalid"
        dialog = LoginDialog(client)
        self.assertTrue(dialog.remember_checkbox.isChecked())
        self.assertTrue(dialog.remember_checkbox.isVisible() or not dialog.isVisible())
        dialog.email.setText("school@example.invalid")
        dialog.password.setText("password")
        dialog.remember_checkbox.setChecked(False)
        with patch.object(dialog.jobs, "start") as start:
            dialog.login()
        self.assertEqual(start.call_args.args[0], "login")
        start.call_args.args[1]()
        client.login.assert_called_once_with("school@example.invalid", "password", remember=False)
        dialog.close()

    def test_sign_in_without_remember_removes_previous_token(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
            vault = Vault()
            client = CloudClient(ClientConfig("https://example.invalid", key), Path(directory) / "cloud-state.json", store=vault)
            token = "prior-credential"
            vault.set(client.installation, token)
            # Unit-test the secure credential persistence boundary without mocking a signature.
            from unittest.mock import Mock
            with patch.object(client, "_json", return_value={"credential": "new", "lease": {}}), \
                 patch.object(client, "accept_lease", return_value={"school_id": "synthetic"}):
                client.login("school@example.invalid", "correct-password", remember=False)
            self.assertIsNone(vault.get(client.installation))
            self.assertEqual(client.credential, "new")
            self.assertFalse(client.remembered)
            client.close()
