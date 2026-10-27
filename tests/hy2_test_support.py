"""Extract the shipped installer payloads without executing an installation."""
import importlib.util
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def payload(name, installer='hy2.sh'):
    source = (ROOT / installer).read_text()
    match = re.search(r"cat >[^\n]*[\"'/]" + re.escape(name) + r"[\"'] <<'([^']+)'\n", source)
    if match is None:
        raise AssertionError('missing payload: ' + name)
    end = source.index('\n' + match[1] + '\n', match.end())
    return source[match.end():end] + '\n'


def function(source, name):
    match = re.search(r'^' + re.escape(name) + r'\(\) \{\n.*?^\}', source, re.M | re.S)
    if match is None:
        raise AssertionError('missing function: ' + name)
    return match[0] + '\n'


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sandbox(root):
    """All generated runtime paths point under a TemporaryDirectory."""
    replacements = {
        '/usr/local/lib/hy2': str(root / 'lib'),
        '/usr/local/sbin': str(root / 'bin'),
        '/var/lib/hy2': str(root / 'state'),
        '/run/hy2': str(root / 'run'),
        '/var/log/hy2': str(root / 'logs'),
        '/etc/hysteria': str(root / 'etc'),
        '/etc/default/hy2-main': str(root / 'defaults'),
    }
    for name in ('lib', 'bin', 'run', 'logs', 'etc', 'state/main', 'state/temp', 'state/quota', 'state/iplimit'):
        (root / name).mkdir(parents=True, exist_ok=True)
    for name in ('common.sh', 'quota-lib.sh', 'iplimit-lib.sh'):
        text = payload(name)
        for old, new in replacements.items():
            text = text.replace(old, new)
        (root / 'lib' / name).write_text(text)
    return f'source "{root}/lib/common.sh"\nsource "{root}/lib/quota-lib.sh"\nhy2_ensure_runtime_dirs() {{ :; }}\n'


def bash(text, *, check=True):
    result = subprocess.run(['bash', '-Eeuo', 'pipefail', '-c', text], text=True,
                            capture_output=True, timeout=15)
    if check and result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    return result
