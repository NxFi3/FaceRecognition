"""Small signed-license verifier and OS-vault checkpoint. No network or model work.

The OS vault is authoritative; the JSON mirror is not a secret or a trust anchor.
This detects ordinary clock/file rollback, not a privileged whole-machine snapshot.
"""
import base64
import json
import math
import time
import uuid
import zlib
from pathlib import Path

try:
    from ..localization import text as tr
except ImportError:
    from localization import text as tr

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

CLOCK_TOLERANCE = 300


def verify(envelope, key, installation, school=None, nonce=None):
    raw = base64.b64decode(envelope['payload'], validate=True)
    if len(raw) > 32768:
        raise ValueError(tr('License too large'))
    Ed25519PublicKey.from_public_bytes(key).verify(base64.b64decode(envelope['signature'], validate=True), raw)
    value = json.loads(raw)
    if (value['version'] != 2 or value['audience'] != 'face-recognition-desktop'
            or value['device_id'] != installation or (nonce is not None and value['nonce'] != nonce)
            or (school is not None and value['school_id'] != school)):
        raise ValueError(tr('License binding/version mismatch'))
    uuid.UUID(value['school_id'])
    uuid.UUID(value['license_id'])
    for name in ('issued_at', 'subscription_starts_at', 'subscription_expires_at'):
        if type(value[name]) not in (int, float) or not math.isfinite(value[name]):
            raise ValueError(tr('Invalid license date'))
    if value['status'] not in ('active', 'revoked'):
        raise ValueError(tr('Invalid license status'))
    if (type(value['student_limit']) is not int or not 0 <= value['student_limit'] <= 1000000
            or type(value['device_limit']) is not int or not 1 <= value['device_limit'] <= 100):
        raise ValueError(tr('Invalid license capacity'))
    if value['status'] == 'active' and not (value['student_limit'] > 0 and
            value['subscription_starts_at'] <= value['issued_at'] < value['subscription_expires_at']):
        raise ValueError(tr('Invalid active subscription interval'))
    return value


def boot_id():
    try:
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    except OSError:
        # On Windows the persisted wall-time floor still works across reboots.
        return None


class Checkpoint:
    def __init__(self, store, installation):
        self.store, self.key = store, installation + ':offline-v2'

    def load(self):
        raw = self.store.get(self.key)
        if not raw:
            return None
        if raw.startswith('z2:'):
            decoder = zlib.decompressobj()
            decoded = decoder.decompress(base64.b64decode(raw[3:], validate=True), 65536)
            if not decoder.eof or decoder.unconsumed_tail or decoder.unused_data:
                raise ValueError(tr('Oversized/corrupt protected checkpoint'))
            value = json.loads(decoded)
        else:
            value = json.loads(raw)  # Development checkpoint compatibility.
        if any(not isinstance(value.get(name), (int, float)) or not math.isfinite(value[name]) for name in ('time', 'tick', 'wall', 'generation')):
            raise ValueError(tr('Invalid protected time state'))
        return value

    def save(self, envelope, trusted_time, tick, generation, wall=None, suspicious=False, previous=None):
        value = {'envelope': envelope, 'time': trusted_time, 'tick': tick,
                 'boot': boot_id(), 'generation': generation, 'wall': time.time() if wall is None else wall, 'suspicious': suspicious, 'previous': previous}
        self._write(value)
        return value

    def _write(self, value):
        # Compact the signed envelope for Windows generic-credential blob limits.
        packed = 'z2:' + base64.b64encode(zlib.compress(json.dumps(value, separators=(',', ':')).encode())).decode()
        if not self.store.set(self.key, packed):
            raise OSError(tr("The operating system's secure storage is unavailable. Offline activation is not possible."))

    def finish(self):
        value = self.load()
        if value and value.get('previous') is not None:
            value['previous'] = None
            self._write(value)


def envelope_hash(envelope):
    import hashlib
    return hashlib.sha256(json.dumps(envelope, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
