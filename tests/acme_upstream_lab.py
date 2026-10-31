#!/usr/bin/env python3
"""Actual pinned upstream install, in temp dirs with isolated cron/network I/O.

No CA calls, certificates, root access, user profile changes, or system cron.
Run: python3 tests/acme_upstream_lab.py /path/to/pinned/acme.sh
"""
import hashlib
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

from test_acme_install import ensure_function

EXPECTED_SHA256 = 'c46b41a61c96f67d424e4b4e476907c964b81d53cf94358a9c1d363a4f99c3a4'


def main():
    upstream = Path(sys.argv[1]).resolve()
    if hashlib.sha256(upstream.read_bytes()).hexdigest() != EXPECTED_SHA256:
        raise SystemExit('upstream acme.sh 3.1.2 SHA-256 mismatch; refusing to execute')
    with tempfile.TemporaryDirectory(prefix='hy2-acme-lab-') as directory:
        root = Path(directory)
        for name in ('source', 'caller', 'bin'):
            (root / name).mkdir()
        (root / 'cron').touch()
        stubs = {
            'crontab': '#!/bin/sh\nif [ "$1" = -l ]; then cat "$ACME_LAB_CRON"; else cat >"$ACME_LAB_CRON"; fi\n',
            # Upstream checks its repository hash after install. Block actual
            # HTTP I/O while returning a harmless hash response for this lab.
            'curl': '#!/bin/sh\nprintf \'{"sha":"offline-lab"}\\n\'\n',
            'wget': '#!/bin/sh\nexit 99\n',
        }
        for name, body in stubs.items():
            path = root / 'bin' / name
            path.write_text(body, encoding='utf-8', newline='\n')
            path.chmod(0o700)
        env = dict(os.environ, PATH=str(root / 'bin') + os.pathsep + os.environ['PATH'],
                   ACME_LAB_CRON=str(root / 'cron'))
        for name in ('LE_WORKING_DIR', 'LE_CONFIG_HOME', 'CA_HOME', 'ACCOUNT_CONF_PATH',
                     'CERT_HOME', 'PROFILE', '_ACME_IN_CRON'):
            env.pop(name, None)
        legacy = root / 'source/get_acme.sh'
        shutil.copyfile(upstream, legacy)
        # Reproduce the user's real upstream cp failure before testing the fix.
        old = subprocess.run(['sh', str(legacy), '--install', '--home', str(root / 'old-home'),
                              '--no-profile', '-m', 'operator@example.invalid'],
                             cwd=root / 'caller', env=env, capture_output=True, text=True, timeout=30)
        assert old.returncode != 0 and 'cannot copy acme.sh' in old.stdout + old.stderr, old
        body = 'set -Eeuo pipefail\n' + ensure_function(root) + f'''
ACME_EMAIL=operator@example.invalid
hy2_die() {{ echo "$*" >&2; exit 1; }}
apt-get() {{ :; }}
systemctl() {{ :; }}
secure_download_script() {{ cp -- {shlex.quote(upstream.as_posix())} "$2"; }}
# Exercise the actual upstream --install only. CA selection/registration is
# outside this offline test and must not contact a CA.
sh() {{
  if [[ "$1" == ./acme.sh && "$2" == --install ]]; then
    command sh "$@"
  else
    return 0
  fi
}}
ensure_acmesh_zerossl
secure_download_script() {{ echo "unexpected repeat download" >&2; exit 99; }}
ensure_acmesh_zerossl
'''
        run = subprocess.run(['bash', '-s'], input=body, cwd=root / 'caller', env=env,
                             capture_output=True, text=True, timeout=60)
        assert run.returncode == 0, run.stdout + run.stderr
        installed = root / 'home/acme.sh'
        assert installed.is_file() and os.access(installed, os.X_OK)
        version = subprocess.run(['sh', str(installed), '--version'], cwd=root / 'caller', env=env,
                                 capture_output=True, text=True, check=True, timeout=10)
        assert '3.1.2' in version.stdout, version.stdout
        assert '--cron --home' in (root / 'cron').read_text()
        assert (root / 'home/account.conf').exists()
        assert not (root / 'caller/acme.sh').exists()
        print('PASS: pinned upstream 3.1.2 reproduces old cwd failure; fixed install/repeat/version/isolated cron succeed')


if __name__ == '__main__':
    main()
