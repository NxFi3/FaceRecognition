"""Static verification of a portable Windows build; it does not launch the EXE."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from src.cloud.client import ClientConfig
from src.engine.model_assets import model_directory
from tools.prepare_windows_assets import FACE_MODEL_NAMES


ONNX_NAMES = ('face_detection_yunet_2023mar.onnx', 'yolo26n.onnx')
FORBIDDEN_NAMES = {'.env', 'attendance.db', 'subscription-server.json', 'license-private-key.pem'}


def _same_file_contents(first: Path, second: Path) -> bool:
    def digest(path):
        with path.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').digest()
    return first.is_file() and second.is_file() and first.stat().st_size > 0 and digest(first) == digest(second)


def verify_distribution(package, source, face_models, public_config):
    """Compare packaged original assets/config with the reviewed build inputs."""
    package, source, face_models, public_config = map(Path, (package, source, face_models, public_config))
    if not (package / 'FaceRecognition.exe').is_file():
        raise ValueError('Portable executable is missing')
    staged = ClientConfig.load(public_config)
    packaged = ClientConfig.load(package / 'subscription-client.json')
    if staged != packaged:
        raise ValueError('Packaged public service URL or verification key differs from staged configuration')

    for name in ONNX_NAMES:
        if not _same_file_contents(source / 'models' / name, package / 'models' / name):
            raise ValueError(f'Packaged original ONNX asset missing or changed: {name}')
    for name in FACE_MODEL_NAMES:
        if not _same_file_contents(face_models / name, package / 'face_recognition_models' / 'models' / name):
            raise ValueError(f'Packaged original face model missing or changed: {name}')

    manifest = json.loads((source / 'models' / 'runtime-assets.json').read_text(encoding='utf8'))
    listed_weights = {item['name']: item['sha256'] for item in manifest['assets']
                      if item['name'].lower().endswith('.pth')}
    if bool(listed_weights) != (manifest['reid_included'] is True):
        raise ValueError('OSNet asset manifest and release policy disagree')
    checkpoint_root = Path('models/torch/hub/checkpoints')
    bundled_weights = {item.name for item in (package / checkpoint_root).glob('*.pth')}
    if bundled_weights != set(listed_weights):
        raise ValueError('Packaged OSNet weights differ from the reviewed manifest')
    for name, expected_digest in listed_weights.items():
        original = source / checkpoint_root / name
        bundled = package / checkpoint_root / name
        if not _same_file_contents(original, bundled):
            raise ValueError(f'Packaged OSNet weight missing or changed: {name}')
        with original.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != expected_digest:
                raise ValueError(f'OSNet weight hash differs from the reviewed manifest: {name}')

    files = [item for item in package.rglob('*') if item.is_file()]
    if not any(item.name.lower() == 'qwindows.dll' and
               'platforms' in [part.lower() for part in item.relative_to(package).parts]
               for item in files):
        raise ValueError('Qt Windows platform plugin qwindows.dll is missing')
    if not any(item.name.lower().endswith('.pyd') and 'dlib' in item.name.lower() for item in files):
        raise ValueError('Native dlib extension is missing')
    if not any(item.name.lower().endswith('.pyd') and 'onnxruntime_pybind11_state' in item.name.lower()
               for item in files):
        raise ValueError('Native ONNX Runtime extension is missing')
    for item in files:
        name = item.name.lower()
        if name in FORBIDDEN_NAMES or name.endswith(('.frkey', '.sqlite', '.sqlite3')):
            raise ValueError('Private or local school data file found in portable distribution')

    return {'original_models': len(ONNX_NAMES) + len(FACE_MODEL_NAMES),
            'public_config_matches': True, 'qt_platform_plugin': True,
            'native_dlib_and_onnxruntime': True, 'reid_included': manifest['reid_included']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, default=Path('dist/FaceRecognition'))
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--public-config', type=Path, default=Path('build/public/subscription-client.json'))
    args = parser.parse_args()
    result = verify_distribution(args.package, args.source, model_directory(), args.public_config)
    print('Static Windows distribution check passed:', result)
    print('A real Windows launch, offline license and camera test are still required.')


if __name__ == '__main__':
    main()
