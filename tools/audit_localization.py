"""List remaining static Persian source candidates, not a claim of coverage.

Some candidates are logs, migration history, or intentional language names; review
manually. Never reads runtime school databases, images, credentials or backups.
"""
import ast
import json
from pathlib import Path
import re


def inventory():
    root = Path(__file__).resolve().parents[1]
    entries = []
    paths = list((root/'src').rglob('*.py')) + list((root/'service').glob('*.py')) + list((root/'service/templates').rglob('*.html'))
    for path in sorted(paths):
        if path.name.startswith('test'): continue
        content = path.read_text(encoding='utf8')
        if path.suffix == '.py':
            candidates = [(node.lineno,node.value) for node in ast.walk(ast.parse(content)) if isinstance(node,ast.Constant) and isinstance(node.value,str)]
        else:
            candidates = [(number,line) for number,line in enumerate(content.splitlines(),1)]
        found = [{'line':number, 'text':text[:160]} for number,text in candidates if re.search('[\u0600-\u06ff]',text)]
        if found: entries.append({'file':str(path.relative_to(root)), 'candidates':found})
    return {'scope':'static review candidates, not percentage translated or runtime findings', 'files':entries}


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args=parser.parse_args()
    result=inventory()
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    print(len(result['files']), 'source files still need localization review')


if __name__ == '__main__': main()
