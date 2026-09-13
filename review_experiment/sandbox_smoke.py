#!/usr/bin/env python3
"""Exercise the real OS sandbox with harmless fixture files, without calling an LLM."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

if __package__:
    from .runner import permission_options
else:
    from runner import permission_options


def check(codex='codex'):
    with tempfile.TemporaryDirectory(prefix='review-access-probe-') as temp:
        root = Path(temp).resolve()
        work = root / 'work'
        work.mkdir()
        pdf = root / 'provided.pdf'
        pdf.write_text('PROVIDED_CONTENT')
        outside = root / 'unprovided.txt'
        outside.write_text('SHOULD_NOT_BE_READ')
        skill = root / 'pdf-skill/SKILL.md'
        skill.parent.mkdir()
        skill.write_text('Fixture PDF skill')
        (work / 'manuscript.pdf').symlink_to(pdf)
        (work / 'escape-link.txt').symlink_to(outside)
        code = '''import json, pathlib, socket
work, pdf, outside, skill = map(pathlib.Path, __import__('sys').argv[1:])
assert pdf.read_text() == 'PROVIDED_CONTENT'
assert (work / 'manuscript.pdf').read_text() == 'PROVIDED_CONTENT'
assert skill.read_text() == 'Fixture PDF skill'
(work / 'scratch.txt').write_text('allowed scratch')
for path in [outside, work / 'escape-link.txt', outside.parent / '..' / outside.parent.name / outside.name]:
    try: path.read_bytes()
    except PermissionError: pass
    else: raise AssertionError('Outside read permitted: ' + str(path))
try: pdf.write_text('mutation')
except PermissionError: pass
else: raise AssertionError('Provided PDF modification permitted')
try: list(outside.parent.iterdir())
except PermissionError: pass
else: raise AssertionError('Outside directory listing permitted')
# A private local test socket, no outbound Internet request.
sock = socket.socket()
try: sock.bind(('127.0.0.1', 0))
except PermissionError: pass
else: raise AssertionError('Network binding permitted')
finally: sock.close()
print(json.dumps({'provided_pdf_read': True, 'scratch_write': True, 'outside_read_denied': True,
                  'symlink_escape_denied': True, 'directory_search_denied': True,
                  'pdf_write_denied': True, 'network_denied': True}))
'''
        command = [codex, 'sandbox', *permission_options(work, pdf, skill),
                   '--permission-profile', 'review_only', '--cd', str(work), '--',
                   str(Path(sys.executable).resolve()), '-c', code,
                   str(work), str(pdf), str(outside), str(skill)]
        completed = subprocess.run(command, text=True, capture_output=True, timeout=30)
        if completed.returncode:
            raise RuntimeError(completed.stderr + completed.stdout)
        return json.loads(completed.stdout.strip())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex', default='codex')
    args = parser.parse_args()
    print(json.dumps(check(args.codex), indent=2))
