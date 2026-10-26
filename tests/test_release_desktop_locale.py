"""Real Qt display regressions; no camera, biometric inference or licensed bypass."""
import ast
import json
import os
from pathlib import Path
import re
import string
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QLibraryInfo, QSettings, Qt
from PySide6.QtWidgets import QApplication, QAbstractButton, QDialog, QDialogButtonBox, QLabel, QLineEdit
from src import app as desktop, database, localization


class DesktopLocalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.patches = [patch.object(database, 'DB_PATH', self.directory/'attendance.db'),
                        patch.object(desktop, 'BASE_DIR', self.directory),
                        patch.object(desktop, '_discover_cameras', return_value=[]),
                        patch.object(desktop, '_configure_cpu_runtime')]
        for fixture in self.patches: fixture.start()
        database.init_db()
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            if hasattr(window, 'event_alert_timer'): window.event_alert_timer.stop()
            window.close()
            window.deleteLater()
        self.application.processEvents()
        localization.configure(self.application, 'en')
        for fixture in reversed(self.patches): fixture.stop()
        self.temporary.cleanup()

    def window(self, locale='en'):
        localization.configure(self.application, locale)
        with patch.object(desktop.MainWindow, 'start_camera'):
            window = desktop.MainWindow()
        self.windows.append(window)
        return window

    def test_real_mainwindow_core_navigation_and_settings_are_bilingual(self):
        for locale, expected in [('en','Camera settings'), ('fa','تنظیمات دوربین')]:
            with self.subTest(locale=locale):
                window = self.window(locale)
                labels = [widget.text() for widget in window.findChildren(QLabel)]
                buttons = [widget.text() for widget in window.findChildren(QAbstractButton)]
                self.assertIn(expected, labels)
                self.assertEqual(window.nav_buttons[1].text(), 'Students' if locale=='en' else 'دانش‌آموزان')
                self.assertEqual(window.settings_camera1_enabled.isChecked(), True)
                if locale == 'en':
                    self.assertFalse([text for text in labels+buttons if re.search('[\u0600-\u06ff]', text)])
                self.assertEqual(window.camera_workers, {})

    def test_manual_status_codes_and_weekdays_do_not_depend_on_language(self):
        for locale in ['en', 'fa']:
            localization.configure(self.application, locale)
            dialog = desktop.ManualAttendanceDialog('Synthetic student', 'late')
            self.windows.append(dialog)
            self.assertEqual([dialog.status_combo.itemData(i) for i in range(dialog.status_combo.count())],
                             ['present','late','excused','absent'])
            self.assertEqual(dialog.status_combo.itemText(0), 'Present' if locale=='en' else 'حاضر')
            self.assertEqual(dialog.values()[0], 'late')
        self.assertEqual([day for day, label in desktop.SchoolCalendarDialog.WEEKDAYS], [5,6,0,1,2,3,4])

    def test_attendance_edit_uses_stored_code_after_locale_switch(self):
        window = self.window('en')
        record = (17,'S17','Synthetic student','2026-10-26 08:00:00','manual','present','',None,None)
        with patch.object(desktop,'get_attendance',return_value=[record]), patch.object(desktop,'get_attendance_stats',return_value=(1,1,0,0,0)):
            window.refresh_attendance()
        item = window.attendance_table.item(0,4)
        self.assertEqual(item.data(Qt.UserRole),'present')
        window.attendance_table.selectRow(0)
        # A different visible language must not default this record to absent.
        item.setText('حاضر')
        localization.configure(self.application,'fa')
        dialog = Mock(); dialog.exec.return_value=QDialog.Accepted; dialog.values.return_value=('present','reviewed')
        with patch.object(desktop,'ManualAttendanceDialog',return_value=dialog) as constructor, patch.object(desktop,'set_attendance_status') as save, patch.object(window,'refresh_dashboard'), patch.object(window,'refresh_attendance'):
            window.edit_selected_attendance()
        self.assertEqual(constructor.call_args.args[1],'present')
        self.assertEqual(save.call_args.args[2],'present')

    def test_missing_stable_status_requests_refresh_instead_of_assuming_absent(self):
        window = self.window()
        record = (17,'S17','Synthetic student',None,'manual','present','',None,None)
        with patch.object(desktop,'get_attendance',return_value=[record]), patch.object(desktop,'get_attendance_stats',return_value=(1,1,0,0,0)):
            window.refresh_attendance()
        window.attendance_table.selectRow(0)
        window.attendance_table.item(0,4).setData(Qt.UserRole,None)
        with patch.object(desktop.QMessageBox,'warning') as warning, patch.object(desktop,'set_attendance_status') as save:
            window.edit_selected_attendance()
        warning.assert_called_once(); save.assert_not_called()

    def test_enrollment_preview_no_camera_state_is_translated(self):
        for locale in ['en','fa']:
            localization.configure(self.application,locale)
            dialog = desktop.EnrollmentDialog()
            self.windows.append(dialog)
            self.application.processEvents()
            self.assertIsNone(dialog.preview_worker)
            self.assertEqual(dialog.windowTitle(), 'Add student' if locale=='en' else 'افزودن دانش‌آموز')
            self.assertEqual(dialog.start_button.text(), 'Start enrollment' if locale=='en' else 'شروع ثبت')
            self.assertIsNone(dialog.camera_combo.currentData())

    def test_native_qt_translator_switches_and_saved_preference_defers_active_change(self):
        translation = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))/'qtbase_fa.qm'
        if not translation.is_file(): self.skipTest('Native Qt Persian catalog missing; packaging must supply it')
        localization.configure(self.application,'en')
        english_box = QDialogButtonBox(QDialogButtonBox.Cancel)
        english = english_box.button(QDialogButtonBox.Cancel).text()
        localization.configure(self.application,'fa')
        persian_box = QDialogButtonBox(QDialogButtonBox.Cancel)
        persian = persian_box.button(QDialogButtonBox.Cancel).text()
        self.assertNotEqual(english,persian)
        self.assertRegex(persian,'[\u0600-\u06ff]')
        native = QSettings
        with patch('PySide6.QtCore.QSettings',lambda *args: native(str(self.directory/'preferences.ini'),native.IniFormat)):
            localization.save_language_preference('en')
            self.assertEqual(localization.preferred_language(),'en')
            self.assertEqual(localization.language(),'fa')
            self.assertEqual(self.application.layoutDirection(),Qt.RightToLeft)
            localization.configure(self.application)
            self.assertEqual(localization.language(),'en')
        restored = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.assertEqual(restored.button(QDialogButtonBox.Cancel).text(),english)

    def test_failed_preference_save_does_not_change_active_language(self):
        localization.configure(self.application,'en')
        settings = Mock()
        settings.status.return_value = QSettings.Status.AccessError
        with patch('PySide6.QtCore.QSettings',return_value=settings):
            with self.assertRaises(OSError):
                localization.configure(self.application,'fa',persist=True)
        self.assertEqual(localization.language(),'en')
        self.assertEqual(self.application.layoutDirection(),Qt.LeftToRight)

    def test_desktop_source_keeps_persian_only_for_legacy_import_aliases(self):
        root = ast.parse(Path(desktop.__file__).read_text(encoding='utf8'))
        parents = {child:node for node in ast.walk(root) for child in ast.iter_child_nodes(node)}
        untranslated = [(node.lineno,node.value) for node in ast.walk(root)
                        if isinstance(node,ast.Constant) and isinstance(node.value,str)
                        and re.search('[\u0600-\u06ff]',node.value)
                        and not isinstance(parents.get(node),ast.Set)]
        self.assertEqual(untranslated,[])
        # Format fields must remain identical across the committed catalog.
        formatter = string.Formatter()
        for english,persian in localization._catalog.items():
            if not isinstance(persian,str): continue
            try:
                en_fields = {name for _,name,_,_ in formatter.parse(english) if name is not None}
                fa_fields = {name for _,name,_,_ in formatter.parse(persian) if name is not None}
            except ValueError:
                continue  # Non-format prose with literal braces is not a template.
            if en_fields: self.assertEqual(en_fields,fa_fields,english)
