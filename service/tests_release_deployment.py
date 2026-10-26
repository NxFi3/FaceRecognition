"""Disposable shell-control regressions; no real Docker, keys or school data."""
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[1]

# These doubles exercise shell failure/state handling, not encryption quality.
DOCKER_DOUBLE = r'''#!/usr/bin/env python3
import json, os, pathlib, signal, sys, time
args=sys.argv[1:]
with open(os.environ['BACKUP_CALLS'],'a') as log: log.write(json.dumps(args)+'\n')
if args[:3] == ['compose','ps','-q']:
    if os.environ.get('WEB_RUNNING') == '1': print('synthetic-container')
elif 'pg_dump' in args:
    print('synthetic database dump')
    if os.environ.get('WAIT_DUMP') == '1':
        pathlib.Path(os.environ['DUMP_STARTED']).touch()
        # Signal the parent shell while this command is still running. POSIX
        # shells defer their trap until foreground execution completes.
        os.kill(os.getppid(), signal.SIGTERM)
    if os.environ.get('FAIL_DUMP') == '1': sys.exit(9)
elif args[:3] == ['compose','start','web']:
    if os.environ.get('FAIL_START') == '1': sys.exit(8)
elif 'tar' in args:
    print('synthetic archived data')
'''
AGE_DOUBLE = r'''#!/usr/bin/env python3
import pathlib,sys
args=sys.argv[1:]
path=pathlib.Path(args[args.index('-o')+1])
# No cryptographic assertion: real encrypted desktop backup tests cover crypto.
path.write_bytes(b'SYNTHETIC ENCRYPTED ARCHIVE')
'''
GIT_DOUBLE = '#!/bin/sh\nprintf "%s\\n" synthetic-commit\n'


@unittest.skipIf(os.name == 'nt', 'POSIX deployment script; native Windows packaging is separately gated')
@unittest.skipUnless((ROOT/'deploy/backup-server.sh').is_file(), 'Host deployment script is not in the minimal service image')
class ServerBackupFailureTests(SimpleTestCase):
    def fixture(self, directory, **changes):
        root = Path(directory)
        for name in ('deploy','tmp','bin','output'):
            (root/name).mkdir()
        (root/'deploy/backup-server.sh').write_text((ROOT/'deploy/backup-server.sh').read_text(encoding='utf8'), encoding='utf8')
        (root/'deploy/.env').write_text('SYNTHETIC_SERVER_KEY=not-deployed\n', encoding='utf8')
        for name,body in [('docker',DOCKER_DOUBLE),('age',AGE_DOUBLE),('git',GIT_DOUBLE)]:
            path=root/'bin'/name;path.write_text(body, encoding='utf8');path.chmod(0o700)
        env=dict(os.environ, PATH=str(root/'bin')+os.pathsep+os.environ['PATH'], TMPDIR=str(root/'tmp'), BACKUP_CALLS=str(root/'calls'), DUMP_STARTED=str(root/'dump-started'))
        env.update(changes)
        return root,env

    def run_backup(self, root, env):
        return subprocess.run(['sh',str(root/'deploy/backup-server.sh'),'age1synthetic',str(root/'output/backup.age')], env=env, capture_output=True, text=True, timeout=15)

    def calls(self, root):
        return [json.loads(line) for line in (root/'calls').read_text(encoding='utf8').splitlines()]

    def assert_no_private_staging(self, root):
        self.assertEqual(list((root/'tmp').iterdir()), [])
        self.assertEqual(list((root/'output').glob('*.partial.*')), [])

    def test_failed_dump_and_restart_cannot_skip_private_staging_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root,env=self.fixture(directory, WEB_RUNNING='1', FAIL_DUMP='1', FAIL_START='1')
            result=self.run_backup(root,env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('could not restart web',result.stderr)
            self.assert_no_private_staging(root)
            self.assertFalse((root/'output/backup.age').exists())

    def test_originally_stopped_service_is_not_started_by_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root,env=self.fixture(directory, WEB_RUNNING='0')
            result=self.run_backup(root,env)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertNotIn(['compose','start','web'],self.calls(root))
            self.assertNotIn(['compose','stop','web'],self.calls(root))
            self.assertTrue((root/'output/backup.age').is_file())
            self.assert_no_private_staging(root)

    def test_running_service_is_restarted_and_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root,env=self.fixture(directory, WEB_RUNNING='1')
            result=self.run_backup(root,env)
            self.assertEqual(result.returncode,0,result.stderr)
            calls=self.calls(root)
            self.assertEqual(calls.count(['compose','stop','web']),1)
            self.assertEqual(calls.count(['compose','start','web']),1)
            before=(root/'output/backup.age').read_bytes()
            result=self.run_backup(root,env)
            self.assertNotEqual(result.returncode,0)
            self.assertEqual((root/'output/backup.age').read_bytes(),before)
            self.assertEqual(self.calls(root),calls)
            self.assert_no_private_staging(root)

    def test_sigterm_aborts_before_later_snapshots_and_cleans_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            root,env=self.fixture(directory, WEB_RUNNING='1', WAIT_DUMP='1')
            result=self.run_backup(root,env)
            self.assertEqual(result.returncode,143,result.stderr)
            self.assertTrue((root/'dump-started').exists())
            self.assertFalse(any('tar' in call for call in self.calls(root)))
            self.assertEqual(self.calls(root).count(['compose','start','web']),1)
            self.assert_no_private_staging(root)
            self.assertFalse((root/'output/backup.age').exists())
