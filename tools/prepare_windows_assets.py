"""Validate release assets and optionally copy an EXISTING OSNet cache, never download models."""
import argparse
import hashlib
import importlib.util
from importlib.metadata import distributions
import sys
import json
from pathlib import Path
import shutil

from src.cloud.client import ClientConfig
from src.engine.model_assets import model_directory


FACE_MODEL_NAMES = (
    'shape_predictor_68_face_landmarks.dat',
    'shape_predictor_5_face_landmarks.dat',
    'dlib_face_recognition_resnet_model_v1.dat',
    'mmod_human_face_detector.dat',
)


def required_assets(root, face_model_root):
    """Require the exact original assets, not an arbitrary count of .dat files.

    This verifies presence/nonempty files; the executable's --smoke-test must
    still load the models. No model is downloaded, replaced or converted here.
    """
    root, face_model_root = Path(root), Path(face_model_root)
    files = [root / 'models' / name for name in ('yolo26n.onnx', 'face_detection_yunet_2023mar.onnx')]
    files += [face_model_root / name for name in FACE_MODEL_NAMES]
    if any(not path.is_file() or path.stat().st_size == 0 for path in files):
        raise ValueError('Required original ONNX/dlib assets missing or empty')
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--torch-cache', type=Path)
    parser.add_argument('--allow-no-reid', action='store_true', help='Only if the validated pilot baseline did not use OSNet')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    from tools.package_client import stage
    source = root / 'deploy/generated/subscription-client.json'
    if not source.is_file():
        source = root / 'subscription-client.json'  # CI's public repository variables
    stage(source, root / 'build/public/subscription-client.json')
    try:
        files = required_assets(root, model_directory())
    except (RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    if args.torch_cache:
        if not all(importlib.util.find_spec(name) is not None for name in ('torch', 'torchvision', 'torchreid')):
            parser.error('Install the existing CPU-only Re-ID dependencies before packaging')
        weights = list((args.torch_cache / 'hub/checkpoints').glob('*osnet*x0_25*.pth'))
        if not weights:
            parser.error('Provide the existing validated TORCH_HOME cache containing OSNet x0.25 weights; no replacement/download is performed')
        destination = root / 'models/torch/hub/checkpoints'
        destination.mkdir(parents=True, exist_ok=True)
        for source in weights:
            target = destination / source.name
            if source.resolve() != target.resolve():
                shutil.copy2(source, target)
            files.append(target)
    elif not args.allow_no_reid:
        parser.error('Re-ID must be preserved. Supply --torch-cache, or explicitly --allow-no-reid only for an already validated baseline without OSNet')
    result = {'python': sys.version, 'reid_included': bool(args.torch_cache), 'assets': [],
              'dependencies': sorted({d.metadata['Name'] + '==' + d.version for d in distributions() if d.metadata['Name']})}
    for path in files:
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(65536), b''):
                digest.update(chunk)
        result['assets'].append({'name': path.name, 'bytes': path.stat().st_size, 'sha256': digest.hexdigest()})
    (root / 'models/runtime-assets.json').write_text(json.dumps(result, indent=2), encoding='utf8')
    print('Release assets validated; reid_included=' + str(result['reid_included']))


if __name__ == '__main__':
    main()
