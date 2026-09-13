#!/usr/bin/env python3
"""Exercise the real OS sandbox with harmless fixture files, without calling an LLM."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from review_experiment.runner import build_command, permission_options


def check(codex='codex', ripgrep=None):
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
        tool_path = ''
        if ripgrep:
            ripgrep = Path(ripgrep).resolve()
            args = SimpleNamespace(codex=codex, model='sandbox-probe', effort='low',
                                   pdf_skill=skill, ripgrep=ripgrep)
            settings = build_command(args, work, 'Unused by this OS-only probe', pdf)
            setting = next(s for s in settings if s.startswith('shell_environment_policy.set='))
            tool_path = json.JSONDecoder().raw_decode(setting.split('"PATH"=', 1)[1])[0]
        code = '''import errno, json, pathlib, socket, subprocess, sys
work, pdf, outside, skill = map(pathlib.Path, sys.argv[1:5])
rg, tool_path = sys.argv[5:7]
assert pdf.read_text() == 'PROVIDED_CONTENT'
assert (work / 'manuscript.pdf').read_text() == 'PROVIDED_CONTENT'
assert skill.read_text() == 'Fixture PDF skill'
(work / 'scratch.txt').write_text('allowed scratch')
for path in [outside, work / 'escape-link.txt', outside.parent / '..' / outside.parent.name / outside.name]:
    try: path.read_bytes()
    except (PermissionError, FileNotFoundError): pass
    else: raise AssertionError('Outside read permitted: ' + str(path))
try: pdf.write_text('mutation')
except OSError as exc:
    assert exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS), exc
else: raise AssertionError('Provided PDF modification permitted')
try: visible = {p.name for p in outside.parent.iterdir()}
except (PermissionError, FileNotFoundError): pass
else:
    # Linux may expose a synthetic mount parent containing only explicit grants.
    assert visible <= {'work', 'provided.pdf', 'pdf-skill'}, visible
    assert outside.name not in visible, visible
# A private local test socket, no outbound Internet request.
sock = None
try:
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
except PermissionError: pass
else: raise AssertionError('Network binding permitted')
finally:
    if sock is not None: sock.close()
result = {'provided_pdf_read': True, 'scratch_write': True, 'outside_read_denied': True,
          'symlink_escape_denied': True, 'outside_directory_contents_hidden': True,
          'pdf_write_denied': True, 'network_denied': True}
if rg:
    env = {'PATH': tool_path}
    version = subprocess.run(['rg', '--version'], env=env, cwd=work,
                             capture_output=True, text=True, check=True, timeout=5)
    match = subprocess.run(['rg', '-n', 'allowed', 'scratch.txt'], env=env, cwd=work,
                           capture_output=True, text=True, check=True, timeout=5)
    assert match.stdout.strip() == '1:allowed scratch', match.stdout
    denied = subprocess.run(['rg', 'SHOULD_NOT_BE_READ', str(outside)], env=env,
                            cwd=work, capture_output=True, text=True, timeout=5)
    assert denied.returncode != 0 and 'SHOULD_NOT_BE_READ' not in denied.stdout
    result.update(ripgrep_version=version.stdout.splitlines()[0],
                  ripgrep_search=True, ripgrep_outside_read_denied=True)
print(json.dumps(result))
'''
        command = [codex, 'sandbox', *permission_options(work, pdf, skill, codex, ripgrep=ripgrep),
                   '--permission-profile', 'review_only', '--cd', str(work), '--',
                   str(Path(sys.executable).resolve()), '-c', code,
                   str(work), str(pdf), str(outside), str(skill), str(ripgrep or ''), tool_path]
        completed = subprocess.run(command, text=True, capture_output=True, timeout=30)
        if completed.returncode:
            raise RuntimeError(completed.stderr + completed.stdout)
        return json.loads(completed.stdout.strip())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex', default='codex')
    parser.add_argument('--ripgrep', type=Path)
    args = parser.parse_args()
    print(json.dumps(check(args.codex, args.ripgrep), indent=2))
