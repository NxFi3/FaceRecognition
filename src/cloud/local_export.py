"""Read-only export independent of subscription/network state."""
from contextlib import closing
import csv
from pathlib import Path
import sqlite3
import uuid

try:
    from ..spreadsheet_security import spreadsheet_row
except ImportError:
    from spreadsheet_security import spreadsheet_row


def bound_school(database):
    with closing(sqlite3.connect(f'{Path(database).resolve().as_uri()}?mode=ro', uri=True)) as connection:
        row = connection.execute("SELECT value FROM app_settings WHERE key='cloud_school_id'").fetchone()
    if not row:
        raise ValueError('ابتدا اتصال دیتابیس به مدرسه در فعال‌سازی اولیه لازم است.')
    return str(uuid.UUID(row[0]))


def export_attendance(database, output):
    with closing(sqlite3.connect(f'{Path(database).resolve().as_uri()}?mode=ro', uri=True)) as connection:
        cursor = connection.execute('SELECT s.student_code, s.name, a.date, a.timestamp, a.status, a.notes FROM attendance a JOIN students s ON s.id=a.student_id ORDER BY a.date,a.id')
        # Exclusive creation avoids accidental loss of an earlier export.
        with open(output, 'x', encoding='utf-8-sig', newline='') as destination:
            import os
            os.chmod(output, 0o600)
            writer = csv.writer(destination)
            writer.writerow([column[0] for column in cursor.description])
            for row in cursor:
                # Prevent spreadsheet formula injection from user-controlled fields.
                writer.writerow(spreadsheet_row(row))
