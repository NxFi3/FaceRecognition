"""Read-only storage inventory. No names, embeddings, images or credentials output."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3


def inventory(database):
    database = Path(database)
    with closing(sqlite3.connect(f'{database.resolve().as_uri()}?mode=ro', uri=True)) as connection:
        students = connection.execute('SELECT COUNT(*) FROM students').fetchone()[0]
        embedding_bytes, embeddings = connection.execute('SELECT COALESCE(SUM(length(embedding)),0), COUNT(*) FROM face_embeddings').fetchone()
        attendances = connection.execute('SELECT COUNT(*) FROM attendance').fetchone()[0]
        page_count = connection.execute('PRAGMA page_count').fetchone()[0]
        page_size = connection.execute('PRAGMA page_size').fetchone()[0]
    photos = database.parent / 'early_exit_review'
    paths = [p for p in photos.rglob('*') if p.is_file() and not p.is_symlink()] if photos.exists() else []
    return {'students': students, 'embeddings': embeddings, 'embedding_bytes': embedding_bytes,
            'attendance_rows': attendances, 'sqlite_logical_bytes': page_count * page_size,
            'event_photo_count': len(paths), 'event_photo_bytes': sum(p.stat().st_size for p in paths),
            'cloud_ciphertext_limit_bytes': 128 * 1024 ** 2,
            'note': 'Inventory only; compressed/encrypted size is checked on actual backup creation. No capacity guarantee.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=Path('data/attendance.db'))
    args = parser.parse_args()
    print(json.dumps(inventory(args.database), indent=2))


if __name__ == '__main__':
    main()
