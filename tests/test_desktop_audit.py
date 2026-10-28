"""Independent audit regressions; no camera, model, or real account data."""
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
from unittest.mock import patch

import test_cloud_client as fixture
from src.cloud.client import AccessDenied, CloudClient


class AuthenticationExchangeOrderingTests(unittest.TestCase):
    setUp = fixture.CloudClientTests.setUp
    envelope = fixture.CloudClientTests.envelope
    transport = fixture.CloudClientTests.transport
    login = fixture.CloudClientTests.login

    def test_parallel_account_sync_cannot_overwrite_new_revocation_with_old_active_reply(self):
        self.login()
        first_entered, release, second_started = (threading.Event() for _ in range(3))
        original_epoch = self.client._auth_epoch
        count_lock = threading.Lock()
        requests = []
        epoch_count = 0
        def epoch():
            nonlocal epoch_count
            value = original_epoch()
            with count_lock:
                epoch_count += 1
                if epoch_count == 2:
                    second_started.set()
            return value
        def transport(method, url, **kwargs):
            requests.append(url)
            if len(requests) == 1:
                envelope = self.envelope(kwargs['json']['nonce'])
                first_entered.set()
                self.assertTrue(release.wait(2))
                return fixture.Response({'lease': envelope})
            return fixture.Response({'lease': self.envelope(kwargs['json']['nonce'], status='revoked')})
        self.client.transport = transport
        with patch.object(self.client, '_auth_epoch', side_effect=epoch), ThreadPoolExecutor(max_workers=2) as workers:
            older = workers.submit(self.client.renew)
            self.assertTrue(first_entered.wait(2))
            newer = workers.submit(self.client.renew)
            try:
                self.assertTrue(second_started.wait(2))
                # A real outage/long request must not block the in-memory camera gate.
                self.assertTrue(self.client.allowed())
                # With the old implementation this completes the revoked exchange
                # before the delayed active response is released, reproducing the bug.
                try:
                    newer.result(timeout=.1)
                except TimeoutError:
                    pass
                except AccessDenied:
                    pass
            finally:
                release.set()
            older.result(2)
            with self.assertRaises(AccessDenied):
                newer.result(2)
        self.assertEqual(len(requests), 2)
        self.assertFalse(self.client.allowed())
        restarted = CloudClient(self.config, self.client.state_path, store=self.vault)
        self.assertFalse(restarted.allowed())

    def test_login_and_sync_use_new_saved_credential_instead_of_racing_token_rotation(self):
        self.login()
        entered, release, sync_started = (threading.Event() for _ in range(3))
        original_epoch = self.client._auth_epoch
        epoch_count = 0
        def epoch():
            nonlocal epoch_count
            value = original_epoch()
            epoch_count += 1
            if epoch_count == 2:
                sync_started.set()
            return value
        seen_headers = []
        def transport(method, url, **kwargs):
            if url.endswith('/login/'):
                entered.set()
                self.assertTrue(release.wait(2))
                return fixture.Response({'credential': 'new-rotation-token', 'lease': self.envelope(kwargs['json']['nonce'])})
            seen_headers.append(kwargs['headers'].get('Authorization'))
            return fixture.Response({'lease': self.envelope(kwargs['json']['nonce'])})
        self.client.transport = transport
        with patch.object(self.client, '_auth_epoch', side_effect=epoch), ThreadPoolExecutor(max_workers=2) as workers:
            login = workers.submit(self.client.login, 'school@example.com', 'password')
            self.assertTrue(entered.wait(2))
            sync = workers.submit(self.client.renew)
            try:
                self.assertTrue(sync_started.wait(2))
                try:
                    sync.result(timeout=.1)
                except TimeoutError:
                    pass
            finally:
                release.set()
            login.result(2)
            sync.result(2)
        self.assertEqual(seen_headers, ['Bearer new-rotation-token'])
        self.assertTrue(self.client.allowed())
