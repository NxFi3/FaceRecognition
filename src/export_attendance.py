import csv
import os
import tempfile
from pathlib import Path

try:
    from .database import init_db, get_today_attendance
    from .spreadsheet_security import spreadsheet_row
    from .localization import text as tr
except ImportError:
    from database import init_db, get_today_attendance
    from spreadsheet_security import spreadsheet_row
    from localization import text as tr

OUTPUT_DIR = Path("exports")


def main():
    init_db()

    rows = get_today_attendance()

    if not rows:
        print(tr("No attendance records for today."))
        return

    OUTPUT_DIR.mkdir(exist_ok=True)

    date = rows[0][2].split(" ")[0]
    output_file = OUTPUT_DIR / f"attendance_{date}.csv"

    descriptor, temporary = tempfile.mkstemp(prefix='.attendance-', suffix='.tmp', dir=OUTPUT_DIR)
    try:
        with os.fdopen(descriptor, "w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            # Stable machine-readable column names are not localized.
            writer.writerow(["student_code", "name", "timestamp", "camera_id"])
            writer.writerows(spreadsheet_row(row) for row in rows)
            stream.flush()
            os.fsync(stream.fileno())
        # A failed/full-disk export must not truncate yesterday's complete file.
        os.replace(temporary, output_file)
    finally:
        Path(temporary).unlink(missing_ok=True)

    print()
    print(tr("Exported {count} attendance records.").format(count=len(rows)))
    print(tr("File: {path}").format(path=output_file))


if __name__ == "__main__":
    main()
