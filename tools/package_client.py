"""Release operator stages validated PUBLIC configuration; customers never run this."""
import argparse
import json
from pathlib import Path
from src.cloud.client import ClientConfig


def stage(source, output):
    source, output = Path(source), Path(output)
    config = ClientConfig.load(source)
    import base64
    value = {'service_url': config.url, 'public_key': base64.b64encode(config.public_key).decode('ascii')}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2)+'\n', encoding='utf8')
    temporary.replace(output)
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('deploy/generated/subscription-client.json'))
    parser.add_argument('--output', type=Path, default=Path('build/public/subscription-client.json'))
    args = parser.parse_args()
    stage(args.source, args.output)
    print('Public service origin and verification key staged; no signing key included.')


if __name__ == '__main__':
    main()
