#!/usr/bin/env python3
"""Compile every nested payload; ShellCheck the actual generated Bash."""
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from helpers import heredocs  # noqa: E402


def main():
    out = ROOT / '.generated'
    out.mkdir(exist_ok=True)
    scripts = list(ROOT.glob('*.sh'))
    python_count = 0
    for path in scripts[:]:
        for i, (header, marker, body) in enumerate(heredocs(path.read_text(encoding='utf-8'))):
            if body.startswith('#!/usr/bin/env bash'):
                target = out / f'{path.stem}-{i}.sh'
                target.write_text(body, encoding='utf-8', newline='\n')
                scripts.append(target)
            elif marker == 'PY' or body.startswith('#!/usr/bin/env python'):
                compile(body, f'{path.name}:{header}', 'exec')
                python_count += 1
    for path in scripts:
        subprocess.run(['bash', '-n', str(path)], check=True)
    if not shutil.which('shellcheck'):
        raise SystemExit('ShellCheck is required (apt-get install shellcheck)')
    # Sourced libraries intentionally export globals; runtime absolute imports
    # do not exist in CI. Keep all other warning/error diagnostics blocking.
    subprocess.run(['shellcheck', '--severity=warning', '--exclude=SC1090,SC1091,SC2034',
                    *map(str, scripts)], check=True)
    print(f'checked {len(scripts)} Bash sources/payloads and {python_count} Python payloads')


if __name__ == '__main__':
    main()
