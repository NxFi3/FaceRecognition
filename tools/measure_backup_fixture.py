"""One small synthetic storage check; NOT a camera/FPS or hardware benchmark."""
from contextlib import closing
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import patch
import uuid

import numpy as np
from src import database
from src.cloud.recovery import make_bundle
from tools.backup_inventory import inventory


def main():
    with tempfile.TemporaryDirectory(prefix='school-storage-fixture-') as directory:
        root = Path(directory)
        path = root / 'attendance.db'
        school = str(uuid.uuid4())
        with patch.object(database, 'DB_PATH', path):
            database.init_db()
        rng = np.random.default_rng(42)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("INSERT INTO app_settings VALUES('cloud_school_id',?)", (school,))
            connection.executemany('INSERT INTO students(id,student_code,name) VALUES(?,?,?)', ((i, str(i), 'Synthetic student') for i in range(1, 901)))
            connection.executemany('INSERT INTO face_embeddings(student_id,embedding) VALUES(?,?)', ((i, rng.normal(size=128).astype(np.float64).tobytes()) for i in range(1, 901) for _ in range(5)))
            connection.executemany('INSERT INTO attendance(student_id,date,timestamp,camera_id) VALUES(?,?,?,?)', ((i, str(date(2026,1,1)+timedelta(days=d)), str(date(2026,1,1)+timedelta(days=d))+' 08:00:00', 'camera_0') for d in range(180) for i in range(1, 901)))
        photos = root / 'early_exit_review'
        photos.mkdir()
        for i in range(100):
            (photos / f'event_{i}.jpg').write_bytes(os.urandom(256 * 1024))
        result = inventory(path)
        result['fixture'] = '900 synthetic students, five 128-float64 blobs each, 180 attendance days, 100 incompressible 256-KiB dummy photo files (not measured school footage)'
        result['encrypted_bundle_bytes'] = make_bundle(path, root / 'fixture.frbackup', school, os.urandom(32))
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
