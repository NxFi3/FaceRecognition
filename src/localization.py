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


def preferred_language():
    from PySide6.QtCore import QSettings
    selected = QSettings('NxFi Technologies', 'FaceRecognition').value('language', 'en')
    return selected if selected in ('en', 'fa') else 'en'


def save_language_preference(selected):
    from PySide6.QtCore import QSettings
    if selected not in ('en', 'fa'):
        raise ValueError('Unsupported language')
    settings = QSettings('NxFi Technologies', 'FaceRecognition')
    settings.setValue('language', selected)
    settings.sync()
    if getattr(settings.status(), 'name', None) != 'NoError':
        raise OSError('Language preference could not be saved.')


def configure(application, selected=None, persist=False):
    from PySide6.QtCore import QSettings, Qt, QTranslator, QLibraryInfo
    global _language
    selected = selected or preferred_language()
    selected = selected if selected in ('en', 'fa') else 'en'
    if persist:
        save_language_preference(selected)
    _language = selected
    previous = getattr(application, '_nxfi_qt_translator', None)
    if previous is not None:
        application.removeTranslator(previous)
        application._nxfi_qt_translator = None
        previous.deleteLater()
    if _language == 'fa':
        translator = QTranslator(application)
        directory = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
        if translator.load('qtbase_fa', directory):
            application.installTranslator(translator)
            application._nxfi_qt_translator = translator
    application.setLayoutDirection(Qt.RightToLeft if _language == 'fa' else Qt.LeftToRight)
    return _language


def error_text(error):
    value = str(error)
    if not value:
        return text('The operation failed. Check your connection, account permissions and available storage, then try again.')
    if value in _catalog:
        return text(value)
    source = next((en for en, fa in _catalog.items() if fa == value), None)
    if source:
        return text(source)
    if _language == 'en' and any('\u0600' <= char <= '\u06ff' for char in value):
        return text('The operation failed. Check your connection, account permissions and available storage, then try again.')
    return value
