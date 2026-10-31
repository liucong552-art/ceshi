"""Extract the actual installer payloads, including nested heredocs."""
from pathlib import Path
import os
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def heredocs(source):
    lines = source.splitlines()
    i = 0
    while i < len(lines):
        m = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z_0-9]*)['\"]?\s*$", lines[i])
        if not m:
            i += 1
            continue
        marker, start = m[1], i + 1
        i = start
        while i < len(lines) and lines[i].strip('\t') != marker:
            i += 1
        if i == len(lines):
            raise AssertionError(f'missing heredoc terminator {marker}:{start}')
        body = '\n'.join(lines[start:i]) + '\n'
        yield lines[start - 1], marker, body
        yield from heredocs(body)
        i += 1


def payload(name):
    for header, marker, body in heredocs((ROOT / 'hy2.sh').read_text(encoding='utf-8')):
        if f'/{name}"' in header:
            return body
    raise AssertionError(name)


def bash(source, check=True):
    if os.name == 'nt':
        source = '''python3() { python "$@"; }
install() { if [[ "$1" == -d ]]; then mkdir -p "${@: -1}"; else command install "$@"; fi; }
''' + source
    result = subprocess.run(['bash', '-s'], input=source, text=True, encoding='utf-8',
                            capture_output=True, timeout=30)
    if check and result.returncode:
        raise AssertionError(f'Bash exited {result.returncode}: {result.stdout}\n{result.stderr}')
    return result


def library(name):
    return '\n'.join(line for line in payload(name).splitlines()
                     if not line.startswith('source /usr/local/lib/hy2/')) + '\n'
