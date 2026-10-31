"""Run the generated ACME installer function from an unrelated cwd."""
from pathlib import Path
import shlex
import tempfile
import unittest

from helpers import bash, payload


def ensure_function(root):
    source = payload('onekey_hy2_main_tls.sh')
    function = source[source.index('ensure_acmesh_zerossl() {'):]
    function = function[:function.index('\n}\n') + 3]
    return (function.replace('/root/.acme.sh', (root / 'home').as_posix())
            .replace('/usr/local/src/hy2-upstream', (root / 'source').as_posix()))


# Mimic the actual upstream cwd dependency, not just successful argv parsing.
FIXTURE = '''#!/bin/sh
set -eu
if [ "$1" = --install ]; then
  [ -f ./acme.sh ] || { echo "cannot copy acme.sh" >&2; exit 19; }
  [ "$(basename "$0")" = acme.sh ] || exit 20
  [ "$2" = --home ] || exit 21
  acme_target="$3"
  mkdir -p "$acme_target"
  cp ./acme.sh "$acme_target/acme.sh"
  chmod 700 "$acme_target/acme.sh"
  printf installed >>"$acme_target/account.conf"
fi
'''


class AcmeInstallTests(unittest.TestCase):
    def run_ensure(self, root, fixture, suffix='', check=True):
        source = root / 'source'
        caller = root / 'unrelated-caller'
        source.mkdir()
        caller.mkdir()
        fixture_path = root / 'fixture.sh'
        fixture_path.write_text(fixture, encoding='utf-8', newline='\n')
        return bash('set -Eeuo pipefail\n' + ensure_function(root) + f'''
ACME_EMAIL=operator@example.invalid
fixture={shlex.quote(fixture_path.as_posix())}
hy2_die() {{ echo "$*" >&2; exit 1; }}
apt-get() {{ :; }}
systemctl() {{ :; }}
secure_download_script() {{ cp -- "$fixture" "$2"; }}
cd -- {shlex.quote(caller.as_posix())}
original_cwd="$PWD"
ensure_acmesh_zerossl
[[ "$PWD" == "$original_cwd" ]]
''' + suffix, check=check)

    def test_fresh_install_uses_source_basename_and_preserves_caller_cwd(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.run_ensure(root, FIXTURE)
            self.assertTrue((root / 'home/acme.sh').exists())
            self.assertEqual((root / 'home/account.conf').read_text(), 'installed')
            self.assertFalse((root / 'unrelated-caller/acme.sh').exists())

    def test_repeat_install_keeps_account_and_skips_download(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.run_ensure(root, FIXTURE, '''
secure_download_script() { echo UNEXPECTED_DOWNLOAD; exit 99; }
ensure_acmesh_zerossl
''')
            self.assertEqual((root / 'home/account.conf').read_text(), 'installed')

    def test_install_failure_is_reported_and_account_is_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'home').mkdir()
            (root / 'home/account.conf').write_text('keep-existing-account')
            result = self.run_ensure(root, '#!/bin/sh\nexit 19\n', check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('acme.sh 安装失败', result.stderr)
            self.assertEqual((root / 'home/account.conf').read_text(), 'keep-existing-account')
