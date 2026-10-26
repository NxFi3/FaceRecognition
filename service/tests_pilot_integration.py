"""Exercise the real loopback HTTPS owner portal and signed desktop API.

Runs independently of the service test runner's PostgreSQL fixture. Uses only
synthetic accounts and a new TemporaryDirectory; never touches deployments.
"""
import base64
import json
from pathlib import Path
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
import requests


class LocalPilotIntegrationTests(unittest.TestCase):
    def test_isolated_pilot_serves_real_tls_and_signed_school_license(self):
        with tempfile.TemporaryDirectory(prefix="nxfi-pilot-check-") as directory:
            # Bind a temporary loopback port and then release it for the child.
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            env = os.environ.copy()
            # Intentionally exercise the shell-inheritance protections too.
            env["NXFI_LOCAL_DEVELOPMENT"] = "1"
            env["DJANGO_SETTINGS_MODULE"] = "service.dev_settings"
            args = [sys.executable, "-m", "tools.run_local_pilot",
                    "--data-dir", directory, "--port", str(port), "--server-only"]
            process = subprocess.Popen(args, cwd=Path(__file__).resolve().parents[1], env=env,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                base = "https://localhost:" + str(port)
                ca = Path(directory) / "certs" / "ca.pem"
                creds = Path(directory) / "pilot-test-accounts.txt"
                session = requests.Session()
                session.trust_env = False
                deadline = time.monotonic() + 90
                response = None
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        out, err = process.communicate(timeout=2)
                        self.fail("Pilot subprocess failed: " + err[-1400:] + out[-600:])
                    if ca.exists() and creds.exists():
                        try:
                            response = session.get(base + "/health/", verify=str(ca), timeout=1)
                            if response.status_code == 200:
                                break
                        except requests.RequestException:
                            pass
                    time.sleep(0.2)
                else:
                    self.fail("Pilot HTTPS health did not become ready")
                self.assertEqual(response.json(), {"ok": True})
                self.assertFalse((Path(__file__).resolve().parents[1] / "subscription-client.json").exists())
                self.assertTrue((Path(directory) / ".nxfi-local-pilot").exists())
                cfg = json.loads((Path(directory) / "subscription-client.json").read_text())
                self.assertEqual(cfg["service_url"], base)
                text = creds.read_text()
                def password(prefix):
                    return next(line.split(" / ", 1)[1].strip() for line in text.splitlines() if line.startswith(prefix))
                login = session.post(base + "/api/v1/login/", json={
                    "email": "pilot-school@localhost.invalid", "password": password("Desktop school:"),
                    "device_id": "51723812-48a5-4af8-af79-4106b7ced7b9",
                    "nonce": "N" * 48, "license_version": 2,
                }, verify=str(ca), timeout=5)
                self.assertEqual(login.status_code, 200, login.text[:150])
                payload = login.json()["lease"]
                public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(cfg["public_key"]))
                raw = base64.b64decode(payload["payload"])
                public_key.verify(base64.b64decode(payload["signature"]), raw)
                self.assertEqual(json.loads(raw)["status"], "active")
                # The web login must reject non-owner schools, while the desktop API permits them.
                forbidden = session.post(base + "/login/", data={
                    "username": "pilot-school@localhost.invalid", "password": password("Desktop school:"),
                }, verify=str(ca), timeout=5)
                self.assertNotEqual(forbidden.status_code, 302)
            finally:
                process.terminate()
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)
