import unittest
from unittest.mock import patch
from tools.release_recovery_drill import audit_container, database_summary, logical_restore, restart


class ReleaseDrillTests(unittest.TestCase):
    def test_destructive_hooks_reject_arbitrary_containers_before_subprocess(self):
        with patch('tools.release_recovery_drill.subprocess.run') as run:
            for name in ['production-db', 'nxfi-audit-12345678-web', 'nxfi-audit-nothex-db', 'nxfi-audit-12345678-db;echo unsafe']:
                with self.assertRaises(ValueError): logical_restore(name)
            with self.assertRaises(ValueError): database_summary('nxfi-audit-12345678-db', 'production')
            with self.assertRaises(ValueError): restart('production-web','web',lambda:True)
            run.assert_not_called()
        self.assertEqual(audit_container('nxfi-audit-1234abcd-db','db'), 'nxfi-audit-1234abcd-db')
