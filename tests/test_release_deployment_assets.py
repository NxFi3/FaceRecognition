"""Original-model packaging validation; synthetic filenames, no inference."""
import tempfile
from pathlib import Path
import unittest


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
