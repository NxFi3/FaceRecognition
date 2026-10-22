"""Run the actual application with its configured cameras, then report metrics."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=['low-power', 'balanced', 'performance'], default='balanced')
    parser.add_argument('--seconds', type=int, default=120)
    parser.add_argument('--output', type=Path, default=Path('runtime-profile.jsonl'))
    args = parser.parse_args()
    if args.seconds < 5:
        parser.error('--seconds must be at least 5')
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        parser.error('Output already exists; choose a new --output path')
    env = dict(os.environ, FACE_RUNTIME_PROFILE=args.profile,
               FACE_PROFILE_OUTPUT=str(output), FACE_PROFILE_SECONDS=str(args.seconds))
    print('Use normal login. Measurement run closes the window after the requested duration.', flush=True)
    result = subprocess.run([sys.executable, '-m', 'src.app'],
                            cwd=Path(__file__).resolve().parents[1], env=env)
    if output.exists():
        lines = output.read_text(encoding='utf-8').splitlines()
        if lines:
            report = json.loads(lines[-1])
            print(json.dumps(report, indent=2))
            if not report['processed_fps']:
                print('No processed frames recorded: check model paths, camera settings and startup errors.')
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
