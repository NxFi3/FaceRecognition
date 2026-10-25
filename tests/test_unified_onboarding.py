import base64
import io
import gettext
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication
from src.cloud.client import ClientConfig
from src.cloud.ui import LoginDialog
from src import localization
from tools.package_client import stage
from tools.compile_translations import compile_catalog
from tools.desktop_dev import validate_sandbox


class OnboardingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_bilingual_login_preserves_typed_fields_and_no_network_on_switch(self):
        client = Mock(); client.credential = None; client.config.url = 'https://synthetic.invalid'
        localization.configure(self.app, 'en')
        dialog = LoginDialog(client)
        dialog.email.setText('synthetic@example.invalid'); dialog.password.setText('Unsaved!')
        self.assertEqual(dialog.login_button.text(), 'Sign in and activate')
        # Isolate QSettings persistence from the real user's preferences.
        with tempfile.TemporaryDirectory() as directory, patch('PySide6.QtCore.QSettings', lambda *args: QSettings(str(Path(directory)/'settings.ini'), QSettings.IniFormat)):
            dialog.language_box.setCurrentIndex(1)
            self.assertEqual(dialog.email.text(), 'synthetic@example.invalid')
            self.assertEqual(dialog.password.text(), 'Unsaved!')
            self.assertEqual(dialog.login_button.text(), 'ورود و فعال‌سازی')
            self.assertEqual(self.app.layoutDirection(), Qt.RightToLeft)
            localization.configure(self.app)
            self.assertEqual(localization.language(), 'fa')
        client.login.assert_not_called(); client.renew.assert_not_called()
        dialog.close(); localization.configure(self.app, 'en')

    def test_public_config_is_staged_and_unknown_secret_fields_fail_closed(self):
        key = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        value = {'service_url':'https://synthetic.invalid', 'public_key':base64.b64encode(key).decode()}
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory)/'source.json', Path(directory)/'public/config.json'
            source.write_text(json.dumps(value))
            stage(source, target)
            self.assertEqual(ClientConfig.load(target).public_key, key)
            for url in ['https://synthetic.invalid:invalid', 'https://@synthetic.invalid', 'https://synthetic.invalid:0', 'http://synthetic.invalid']:
                source.write_text(json.dumps({**value, 'service_url':url}))
                with self.assertRaises(Exception): stage(source, target)
                self.assertEqual(ClientConfig.load(target).public_key, key)
            source.write_text(json.dumps({**value, 'private_key':'MUST NEVER PACKAGE'}))
            with self.assertRaises(Exception): stage(source, target)
            self.assertNotIn('MUST NEVER PACKAGE', target.read_text())

    def test_frozen_config_ignores_source_and_working_directory(self):
        with tempfile.TemporaryDirectory() as directory, patch('src.cloud.client.sys.frozen', True, create=True), patch('src.cloud.client.sys._MEIPASS', directory, create=True), patch.object(ClientConfig, 'load') as loader:
            ClientConfig.packaged('/untrusted/source')
            loader.assert_called_once_with(Path(directory)/'subscription-client.json')

    def test_development_sandbox_rejects_production_and_distributed_builds(self):
        with patch('sys.frozen', True, create=True):
            with self.assertRaises(RuntimeError): validate_sandbox('/unused')
        with self.assertRaises(RuntimeError): validate_sandbox(Path(__file__).resolve().parents[1]/'data')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/'attendance.db').touch()
            with self.assertRaises(RuntimeError): validate_sandbox(root)

    def test_catalog_compilation_and_committed_catalog_match(self):
        root = Path(__file__).resolve().parents[1]
        messages = json.loads((root/'src/locales/fa.json').read_text())
        compiled = compile_catalog(messages)
        self.assertEqual(compiled, (root/'service/locale/fa/LC_MESSAGES/django.mo').read_bytes())
        translator = gettext.GNUTranslations(io.BytesIO(compiled))
        self.assertEqual(translator.gettext('Language'), 'زبان')
