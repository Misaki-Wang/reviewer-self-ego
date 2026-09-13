#!/usr/bin/env python3
"""Export HTML code blocks verbatim; never edit or paraphrase prompt text."""
import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONDITIONS = [
    ('exp1_quality', 'example-qtrack', 1, '1'),
    ('exp2_self_llm', 'example-self-llm', 0, '2'),
    ('exp2_other_llm', 'example-other-llm', 0, '2'),
    ('exp2_self_human', 'example-self-human', 0, '2'),
    ('exp2_other_human', 'example-other-human', 0, '2'),
    ('exp3_neutral', 'example-context-baseline', 0, '3'),
    ('exp3_very_positive', 'example-evaluation-very-positive', 0, '3'),
    ('exp3_positive', 'example-evaluation-positive', 0, '3'),
    ('exp3_negative', 'example-evaluation-negative', 0, '3'),
    ('exp3_very_negative', 'example-evaluation-very-negative', 0, '3'),
    ('exp3_years', 'example-effort-years', 0, '3'),
    ('exp3_months', 'example-effort-months', 0, '3'),
    ('exp3_days', 'example-effort-days', 0, '3'),
]


def digest(data):
    return hashlib.sha256(data).hexdigest()


class PromptHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = {}
        self.current = None
        self.in_code = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'details':
            self.current = dict(attrs).get('id')
            if self.current:
                self.blocks[self.current] = []
        if tag == 'code' and self.current:
            self.in_code = True
            self.parts = []

    def handle_data(self, data):
        if self.in_code:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'code' and self.in_code:
            self.blocks[self.current].append(''.join(self.parts))
            self.in_code = False
        if tag == 'details':
            self.current = None


def extract(source):
    parser = PromptHTML()
    parser.feed(source.read_text(encoding='utf-8'))
    system = parser.blocks['example-qtrack'][0]
    for key in ('example-attribution-system', 'example-user-context-system'):
        if parser.blocks[key][0] != system:
            raise ValueError(f'System prompt differs: {key}')
    result = {'system.txt': system}
    for name, block, index, _ in CONDITIONS:
        result[f'{name}.user.txt'] = parser.blocks[block][index]
    return result


def export(source, destination, check=False):
    files = extract(source)
    manifest = {
        'source': source.name,
        'source_sha256': digest(source.read_bytes()),
        'system_file': 'system.txt',
        'files': {name: digest(text.encode('utf-8')) for name, text in files.items()},
        'conditions': [dict(id=name, experiment=exp, user_file=f'{name}.user.txt',
                            source_block=block, source_code_index=index)
                       for name, block, index, exp in CONDITIONS],
    }
    if check:
        for name, text in files.items():
            if (destination / name).read_bytes() != text.encode('utf-8'):
                raise ValueError(f'Prompt differs from HTML: {name}')
        saved = json.loads((destination / 'manifest.json').read_text())
        if saved['files'] != manifest['files'] or saved['conditions'] != manifest['conditions']:
            raise ValueError('Prompt manifest differs from HTML')
    else:
        destination.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            (destination / name).write_bytes(text.encode('utf-8'))
        (destination / 'manifest.json').write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return manifest


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source', type=Path, default=HERE.parent / 'main_experiment.html')
    ap.add_argument('--destination', type=Path, default=HERE / 'prompts')
    ap.add_argument('--check', action='store_true')
    args = ap.parse_args()
    m = export(args.source, args.destination, args.check)
    print(f'{"Verified" if args.check else "Exported"} {len(m["conditions"])} user prompts and shared system prompt.')
