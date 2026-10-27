#!/usr/bin/env python3
"""Validate all source files and every literal generated payload, without root."""
from pathlib import Path
import hashlib
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
count = 0
for path in sorted(ROOT.glob('*.py')):
    compile(path.read_text(), str(path), 'exec')
for path in sorted(ROOT.glob('*.sh')) + sorted((ROOT / 'tests').glob('*.sh')):
    subprocess.run(['bash', '-n', str(path)], check=True)
    source = path.read_text()
    # Find literal installer payloads, including nested heredocs with unique ends.
    for match in re.finditer(r"<<'([A-Za-z_][A-Za-z_0-9]*)'\n", source):
        stop = re.search(r'^' + re.escape(match[1]) + r'$', source[match.end():], re.M)
        if stop is None:
            raise AssertionError(f'unterminated {path.name}:{match[1]}')
        body = source[match.end():match.end() + stop.start()]
        if body.startswith('#!/usr/bin/env bash'):
            result = subprocess.run(['bash', '-n'], input=body, text=True, capture_output=True)
            if result.returncode:
                raise AssertionError(f'{path.name}:{match[1]}: {result.stderr}')
            count += 1
        elif body.startswith('#!/usr/bin/env python3'):
            compile(body, path.name + ':' + match[1], 'exec')
            count += 1
print(f'PASS: source syntax and {count} generated shell/Python payloads')
installer = (ROOT / 'portbw-install.sh').read_text()
expected = re.search(r"^EXPECTED_SHA='([0-9a-f]{64})'", installer, re.M)[1]
assert hashlib.sha256((ROOT / 'portbw.py').read_bytes()).hexdigest() == expected
guide = (ROOT / 'HY2_GUIDE.md').read_text()
blocks = re.findall(r'^```bash\n(.*?)^```', guide, re.M | re.S)
for index, block in enumerate(blocks, 1):
    result = subprocess.run(['bash', '-n'], input=block, text=True, capture_output=True)
    if result.returncode:
        raise AssertionError(f'HY2_GUIDE.md block {index}: {result.stderr}')
print(f'PASS: portbw source hash and {len(blocks)} documented Bash blocks')
