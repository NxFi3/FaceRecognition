"""Offline recovery CLI; no server, account token or active license required."""
import argparse
from pathlib import Path
import tempfile

from filelock import FileLock
from src.cloud.local_export import bound_school, export_attendance
from src.cloud.recovery import import_key, make_bundle, unpack_bundle, install_bundle, recover_interrupted_restore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('export', 'restore', 'csv', 'recover-interrupted'))
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--key', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--input', type=Path)
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error('Use the existing school database; activate/bind a new installation before restoring.')
    with FileLock(str(args.database.parent / '.application.lock'), timeout=0):
        recover_interrupted_restore(args.database)
        if args.operation == 'recover-interrupted':
            return
        if args.operation == 'csv':
            if not args.output:
                parser.error('--output required')
            export_attendance(args.database, args.output)
            return
        if not args.key:
            parser.error('--key required; the server cannot recover a lost school key')
        school = bound_school(args.database)
        key = import_key(args.key, school)
        if args.operation == 'export':
            if not args.output:
                parser.error('--output required (must not exist)')
            make_bundle(args.database, args.output, school, key)
        else:
            if not args.input:
                parser.error('--input required; this replaces current database and photos')
            with tempfile.TemporaryDirectory(prefix='.local-restore-', dir=args.database.parent) as directory:
                unpack_bundle(args.input, directory, school, key)
                install_bundle(directory, args.database)
    print('Recovery operation completed. Keep school data and recovery keys private.')


if __name__ == '__main__':
    main()
