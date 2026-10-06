from database import init_db, get_today_attendance


def main():
    init_db()

    rows = get_today_attendance()

    print()
    print("=" * 70)
    print("TODAY'S ATTENDANCE")
    print("=" * 70)

    if not rows:
        print("No attendance records.")
        return

    print(f"{'CODE':<15}" f"{'NAME':<25}" f"{'TIME':<20}" f"{'CAMERA':<10}")

    print("-" * 70)

    for student_code, name, timestamp, camera_id in rows:

        time = timestamp.split(" ")[1]

        print(f"{student_code:<15}" f"{name:<25}" f"{time:<20}" f"{camera_id:<10}")

    print("-" * 70)
    print(f"Total present: {len(rows)}")
    print()


if __name__ == "__main__":
    main()
