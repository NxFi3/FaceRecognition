import csv
from pathlib import Path

from database import init_db, get_today_attendance

OUTPUT_DIR = Path("exports")


def main():
    init_db()

    rows = get_today_attendance()

    if not rows:
        print("No attendance records for today.")
        return

    OUTPUT_DIR.mkdir(exist_ok=True)

    date = rows[0][2].split(" ")[0]
    output_file = OUTPUT_DIR / f"attendance_{date}.csv"

    with open(output_file, "w", newline="", encoding="utf-8") as f:

        writer = csv.writer(f)

        writer.writerow(["student_code", "name", "timestamp", "camera_id"])

        writer.writerows(rows)

    print()
    print(f"Exported {len(rows)} attendance records.")
    print(f"File: {output_file}")


if __name__ == "__main__":
    main()
