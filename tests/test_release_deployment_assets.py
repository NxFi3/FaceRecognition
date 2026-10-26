"""Original-model packaging validation; synthetic filenames, no inference."""
import tempfile
import base64
import json
from pathlib import Path
import unittest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from tools.prepare_windows_assets import FACE_MODEL_NAMES
from tools.verify_windows_distribution import ONNX_NAMES, verify_distribution


class ReleaseAssetValidationTests(unittest.TestCase):
    def test_exact_original_model_names_required_instead_of_arbitrary_dat_count(self):
        from tools.prepare_windows_assets import required_assets, FACE_MODEL_NAMES
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'models').mkdir(); face=root/'face';face.mkdir()
            for name in ('yolo26n.onnx','face_detection_yunet_2023mar.onnx'):
                (root/'models'/name).write_bytes(b'synthetic-nonempty-asset')
            for index in range(4):
                (face/f'wrong-{index}.dat').write_bytes(b'not-the-required-model')
            self.assertEqual(len(list((root/'models').glob('*.onnx')))+len(list(face.glob('*.dat'))),6)
            with self.assertRaisesRegex(ValueError,'missing or empty'):
                required_assets(root,face)
            for name in FACE_MODEL_NAMES:
                (face/name).write_bytes(b'synthetic-fixture-not-inference')
            files=required_assets(root,face)
            self.assertEqual({p.name for p in files},set(FACE_MODEL_NAMES)|{'yolo26n.onnx','face_detection_yunet_2023mar.onnx'})
            (face/FACE_MODEL_NAMES[0]).write_bytes(b'')
            with self.assertRaisesRegex(ValueError,'missing or empty'):
                required_assets(root,face)


class PortableDistributionValidationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.source, self.package, self.face = (root/name for name in ('source','package','face'))
        for path in (self.source/'models', self.package/'models', self.face,
                     self.package/'face_recognition_models/models', self.package/'PySide6/Qt/plugins/platforms',
                     self.package/'onnxruntime/capi'):
            path.mkdir(parents=True, exist_ok=True)
        (self.package/'FaceRecognition.exe').write_bytes(b'synthetic executable placeholder')
        for name in ONNX_NAMES:
            (self.source/'models'/name).write_bytes(name.encode())
            (self.package/'models'/name).write_bytes(name.encode())
        for name in FACE_MODEL_NAMES:
            (self.face/name).write_bytes(name.encode())
            (self.package/'face_recognition_models/models'/name).write_bytes(name.encode())
        (self.package/'PySide6/Qt/plugins/platforms/qwindows.dll').write_bytes(b'synthetic Qt plugin')
        (self.package/'_dlib_pybind11.cp311-win_amd64.pyd').write_bytes(b'synthetic dlib binary')
        (self.package/'onnxruntime/capi/onnxruntime_pybind11_state.pyd').write_bytes(b'synthetic ONNX binary')
        (self.source/'models/runtime-assets.json').write_text(json.dumps({'reid_included': False, 'assets': []}))
        key = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        config = {'service_url':'https://synthetic.example.invalid', 'public_key':base64.b64encode(key).decode()}
        self.config = root/'public.json'
        self.config.write_text(json.dumps(config))
        (self.package/'subscription-client.json').write_text(json.dumps(config))

    def verify(self):
        return verify_distribution(self.package, self.source, self.face, self.config)

    def test_complete_synthetic_distribution_matches_reviewed_inputs(self):
        result = self.verify()
        self.assertEqual(result['original_models'], 6)
        self.assertTrue(result['public_config_matches'])
        self.assertFalse(result['reid_included'])

    def test_changed_model_or_public_key_is_rejected(self):
        model = self.package/'models'/ONNX_NAMES[0]
        model.write_bytes(b'wrong model')
        with self.assertRaisesRegex(ValueError, 'ONNX asset'):
            self.verify()
        model.write_bytes((self.source/'models'/ONNX_NAMES[0]).read_bytes())
        key = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        (self.package/'subscription-client.json').write_text(json.dumps({
            'service_url':'https://synthetic.example.invalid', 'public_key':base64.b64encode(key).decode()}))
        with self.assertRaisesRegex(ValueError, 'verification key differs'):
            self.verify()

    def test_missing_face_qt_or_native_assets_are_rejected(self):
        for path, message in (
            (self.package/'face_recognition_models/models'/FACE_MODEL_NAMES[0], 'face model'),
            (self.package/'PySide6/Qt/plugins/platforms/qwindows.dll', 'qwindows.dll'),
            (self.package/'_dlib_pybind11.cp311-win_amd64.pyd', 'dlib extension'),
            (self.package/'onnxruntime/capi/onnxruntime_pybind11_state.pyd', 'ONNX Runtime extension'),
        ):
            original = path.read_bytes()
            path.unlink()
            with self.subTest(path=path.name), self.assertRaisesRegex(ValueError, message):
                self.verify()
            path.write_bytes(original)

    def test_private_or_local_school_files_are_rejected(self):
        for name in ('.env', 'attendance.db', 'school.frkey', 'school.sqlite3'):
            path = self.package/name
            path.write_text('synthetic secret marker')
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'Private or local school data'):
                self.verify()
            path.unlink()

    def test_osnet_cache_must_match_reviewed_manifest(self):
        import hashlib
        name = 'osnet_x0_25_synthetic.pth'
        content = b'synthetic OSNet asset; not model weights'
        digest = hashlib.sha256(content).hexdigest()
        relative = Path('models/torch/hub/checkpoints')/name
        for root in (self.source, self.package):
            path = root/relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        manifest = {'reid_included': True, 'assets': [{'name':name, 'sha256':digest}]}
        (self.source/'models/runtime-assets.json').write_text(json.dumps(manifest))
        self.assertTrue(self.verify()['reid_included'])
        (self.package/relative).write_bytes(b'different')
        with self.assertRaisesRegex(ValueError, 'OSNet weight'):
            self.verify()
