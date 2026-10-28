"""Logical PG recovery and process restart drill, only for the disposable audit stack.

This is neither a live-DB recovery tool nor an offsite/VPS disaster-recovery claim.
"""
from pathlib import Path
import re
import subprocess
import tempfile
import time


def audit_container(name, suffix):
    if not isinstance(name, str) or not re.fullmatch(r'nxfi-audit-[0-9a-f]{8}-'+suffix, name):
        raise ValueError('Only the dedicated disposable audit container is allowed')
    return name


def _run(arguments, **kwargs):
    result = subprocess.run(arguments, stderr=subprocess.PIPE, timeout=60, **kwargs)
    if result.returncode:
        raise RuntimeError('Disposable recovery drill command failed; fixture details withheld')
    return result


SUMMARY_SQL = '''SELECT
 (SELECT COUNT(*) FROM service_school),
 (SELECT COUNT(*) FROM service_device),
 (SELECT COUNT(*) FROM service_order),
 (SELECT COUNT(*) FROM service_verifiedpayment),
 (SELECT COALESCE(SUM(amount_irr),0) FROM service_verifiedpayment),
 (SELECT COUNT(*) FROM service_backup),
 (SELECT COUNT(*) FROM auth_user);'''


def database_summary(container, database='face_service'):
    audit_container(container, 'db')
    if database not in ('face_service', 'nxfi_audit_restored'):
        raise ValueError('Not a disposable recovery database')
    return _run(['docker','exec',container,'psql','-U','face_service','-d',database,'-Atqc',SUMMARY_SQL], stdout=subprocess.PIPE).stdout.decode().strip()


def logical_restore(container):
    audit_container(container, 'db')
    before = database_summary(container)
    created = False
    with tempfile.TemporaryDirectory(prefix='nxfi-private-recovery-drill-') as directory:
        path=Path(directory)/'database.dump'
        try:
            # Private owner-only file from creation, including error paths.
            with path.open('xb') as output:
                path.chmod(0o600)
                _run(['docker','exec',container,'pg_dump','-U','postgres','-d','face_service','-Fc'], stdout=output)
            _run(['docker','exec',container,'createdb','-U','postgres','-O','face_service','nxfi_audit_restored'], stdout=subprocess.DEVNULL)
            created=True
            with path.open('rb') as data:
                _run(['docker','exec','-i',container,'pg_restore','-U','postgres','-d','nxfi_audit_restored','--no-owner','--role=face_service','--exit-on-error'],stdin=data,stdout=subprocess.DEVNULL)
            if before != database_summary(container, 'nxfi_audit_restored'):
                raise AssertionError('Restored synthetic commercial records differ')
            if before != database_summary(container):
                raise AssertionError('Original disposable database was changed by restore')
        finally:
            if created:
                _run(['docker','exec',container,'dropdb','-U','postgres','nxfi_audit_restored'],stdout=subprocess.DEVNULL)
    return {'logical_dump_restore':True,'original_database_unchanged':True,'tables_checked':7}


def restart(container, suffix, health):
    audit_container(container, suffix)
    _run(['docker','container','restart','--time','3',container],stdout=subprocess.DEVNULL)
    deadline=time.monotonic()+40
    while time.monotonic()<deadline:
        try:
            if health():return
        except Exception:
            pass  # Outage is expected; caller supplies a bounded loopback probe.
        time.sleep(.25)
    raise AssertionError('Disposable service did not recover after restart')
