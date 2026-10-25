"""Small desktop catalog. No network service, inference dependency or telemetry."""
import json
from pathlib import Path

import sys
_root = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).parent
_catalog = json.loads((_root/'locales/fa.json').read_text(encoding='utf8'))
_language = 'en'


def language():
    return _language


def text(message):
    return _catalog.get(message, message) if _language == 'fa' else message


def configure(application, selected=None, persist=False):
    from PySide6.QtCore import QSettings, Qt
    global _language
    settings = QSettings('NxFi Technologies', 'FaceRecognition')
    selected = selected or settings.value('language', 'en')
    _language = selected if selected in ('en', 'fa') else 'en'
    if persist:
        settings.setValue('language', _language)
        settings.sync()
    application.setLayoutDirection(Qt.RightToLeft if _language == 'fa' else Qt.LeftToRight)
    return _language


def error_text(error):
    value = str(error)
    source = next((en for en, fa in _catalog.items() if fa == value), None)
    if source:
        return text(source)
    if _language == 'en' and any('\u0600' <= char <= '\u06ff' for char in value):
        return text('Unable to sign in. Check your account, subscription and connection, then try again.')
    return value
