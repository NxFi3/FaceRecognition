"""Source-only, isolated actual MainWindow preview. Never a production admission path.

python -m tools.desktop_dev --data-dir /tmp/nxfi-ui-sandbox --language en
Cameras are deliberately disabled in this UI workflow. No service or fake license.
"""
import argparse
from pathlib import Path
import sys


def validate_sandbox(directory):
    if getattr(sys, 'frozen', False):
        raise RuntimeError('Development UI is unavailable in distributed builds')
    repository = Path(__file__).resolve().parents[1]
    if not (repository/'.git').exists():
        raise RuntimeError('A source checkout is required')
    target = Path(directory).resolve()
    if target == repository or repository in target.parents:
        raise RuntimeError('Use a separate disposable directory outside the checkout')
    if target.exists() and any(target.iterdir()) and not (target/'.nxfi-development-only').is_file():
        raise RuntimeError('Refusing a nonempty directory without the development marker')
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    (target/'.nxfi-development-only').touch(mode=0o600)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True, type=Path)
    parser.add_argument('--language', choices=['en', 'fa'], default='en')
    parser.add_argument('--screenshot', type=Path)
    args = parser.parse_args()
    target = validate_sandbox(args.data_dir)
    from src import database
    database.DB_PATH = target/'attendance.db'
    from src import app as desktop
    desktop.BASE_DIR = target
    from src.localization import configure
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    application = QApplication([])
    configure(application, args.language)
    application.setStyle('Fusion')

    class DevelopmentWindow(desktop.MainWindow):
        def start_camera(self):
            pass  # Deliberate development harness; production method is unchanged.

    window = DevelopmentWindow()
    window.setWindowTitle('DEVELOPMENT ONLY · NxFi FaceRecognition · no cameras')
    window.statusBar().showMessage('DEVELOPMENT ONLY — synthetic sandbox, no commercial activation, cameras disabled')
    window.show()
    if args.screenshot:
        def capture():
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            if not window.grab().save(str(args.screenshot)):
                raise RuntimeError('Screenshot could not be saved')
            window.close()
            application.quit()
        QTimer.singleShot(800, capture)
    return application.exec()


if __name__ == '__main__':
    raise SystemExit(main())
