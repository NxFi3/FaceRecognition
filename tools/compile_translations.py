"""Compile the reviewed English→Persian JSON catalog to a standard GNU gettext MO.

No network translation or build-time dependency is used. Run after catalog edits.
"""
import json
from pathlib import Path
import struct


def compile_catalog(messages):
    entries = {'': 'Content-Type: text/plain; charset=UTF-8\nLanguage: fa\nPlural-Forms: nplurals=2; plural=(n > 1);\n', **messages}
    keys = sorted(entries)
    originals = [key.encode('utf8') for key in keys]
    translations = [entries[key].encode('utf8') for key in keys]
    count = len(keys)
    cursor = 28 + 16 * count
    table_original, table_translation = [], []
    for value in originals:
        table_original.append((len(value), cursor))
        cursor += len(value) + 1
    for value in translations:
        table_translation.append((len(value), cursor))
        cursor += len(value) + 1
    return (struct.pack('<7I', 0x950412de, 0, count, 28, 28 + 8*count, 0, 0)
            + b''.join(struct.pack('<2I', *entry) for entry in table_original + table_translation)
            + b''.join(value + b'\0' for value in originals + translations))


def main():
    root = Path(__file__).resolve().parents[1]
    messages = json.loads((root/'src/locales/fa.json').read_text(encoding='utf8'))
    target = root/'service/locale/fa/LC_MESSAGES/django.mo'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(compile_catalog(messages))
    print(f'Compiled {len(messages)} reviewed messages')


if __name__ == '__main__':
    main()
