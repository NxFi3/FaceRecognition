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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--torch-cache', type=Path)
    parser.add_argument('--allow-no-reid', action='store_true', help='Only if the validated pilot baseline did not use OSNet')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    ClientConfig.load(root / 'subscription-client.json')
    files = [root / 'models' / name for name in ('yolo26n.onnx', 'face_detection_yunet_2023mar.onnx')]
    files += sorted(model_directory().glob('*.dat'))
    if len(files) != 6 or any(not path.is_file() for path in files):
        parser.error('Required original ONNX/dlib assets missing')
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
