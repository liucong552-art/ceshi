"""Regression checks for ACME bootstrap order and real-reboot report guards."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hy2_test_support import ROOT, bash, function, module, payload, sandbox


class AcmeFirstInstallTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.prefix = sandbox(self.root)
        self.main = payload('onekey_hy2_main_tls.sh')
        self.home = self.root / 'acme-home'
        self.home.mkdir()

    def rewrite(self, text):
        return text.replace('/root/.acme.sh', str(self.home)).replace('/usr/local/src/hy2-upstream', str(self.root)) \
            .replace('/var/tmp/hy2-acme-install.', str(self.root / 'bootstrap.')).replace('/etc/hysteria/certs', str(self.root / 'certs'))

    def test_bootstrap_satisfies_upstream_current_directory_requirement(self):
        fixture = self.root / 'official-fixture.sh'
        fixture.write_text('''#!/bin/sh
set -e
if [ "$1" = --install ]; then
  test -f ./acme.sh
  cp ./acme.sh "$FAKE_ACME_HOME/acme.sh"
  chmod 700 "$FAKE_ACME_HOME/acme.sh"
fi
''')
        setup = f'''
apt-get() {{ return 0; }}
systemctl() {{ return 0; }}
secure_download_script() {{ cp '{fixture}' "$2"; }}
export FAKE_ACME_HOME='{self.home}'
ACME_EMAIL=test@example.invalid
'''
        bash(self.prefix + setup + self.rewrite(function(self.main, 'ensure_acmesh_zerossl')) + '\nensure_acmesh_zerossl\n')
        self.assertEqual((self.home / 'acme.sh').read_bytes(), fixture.read_bytes())
        self.assertFalse(list(self.root.glob('bootstrap.*')))

    def test_install_cert_deferral_is_scoped_to_that_acme_command(self):
        acme = self.home / 'acme.sh'
        acme.write_text('''#!/bin/sh
set -e
if [ "$1" != --install-cert ]; then exit 0; fi
while [ "$#" -gt 0 ]; do
  case "$1" in
    --key-file) printf key >"$2"; shift 2 ;;
    --fullchain-file) printf cert >"$2"; shift 2 ;;
    --reloadcmd) hook="$2"; shift 2 ;;
    *) shift ;;
  esac
done
"$hook"
''')
        acme.chmod(0o700)
        hook = self.root / 'reload.sh'
        seen = self.root / 'seen'
        hook.write_text(f'#!/bin/sh\nprintf "%s" "${{HY2_ACME_DEFER_RELOAD:-unset}}" >"{seen}"\n')
        hook.chmod(0o700)
        setup = f"HY2_RENEW_HOOK='{hook}'\nwrite_renew_hook() {{ :; }}\nunset HY2_ACME_DEFER_RELOAD\n"
        result = bash(self.prefix + setup + self.rewrite(function(self.main, 'issue_or_renew_cert_zerossl'))
                      + '\nissue_or_renew_cert_zerossl hy2.example.invalid\nprintf "%s" "${HY2_ACME_DEFER_RELOAD-unset}"\n')
        self.assertEqual(seen.read_text(), '1')
        self.assertEqual(result.stdout, 'unset')
        self.assertEqual((self.root / 'certs/hy2.example.invalid/privkey.pem').stat().st_mode & 0o777, 0o600)

    def test_ordinary_renewal_still_rejects_missing_main_state(self):
        hook = self.main.split("<<'HOOK'\n", 1)[1].split('\nHOOK\n', 1)[0]
        hook = hook.replace('/usr/local/lib/hy2', str(self.root / 'lib')).replace('/usr/local/sbin', str(self.root / 'bin'))
        deferred = bash('HY2_ACME_DEFER_RELOAD=1\n' + hook, check=False)
        self.assertEqual(deferred.returncode, 0, deferred.stderr)
        ordinary = bash('unset HY2_ACME_DEFER_RELOAD\n' + hook, check=False)
        self.assertNotEqual(ordinary.returncode, 0)
        self.assertIn('certificate missing or empty', ordinary.stderr)


target = module(ROOT / 'tests/hy2_systemd_acceptance.py', 'hy2_systemd_safety_target')


class RebootAcceptanceGuardTests(unittest.TestCase):
    def test_wrong_host_refuses_before_reading_state(self):
        with patch.object(target.os, 'geteuid', return_value=0), \
             patch.object(target.platform, 'node', return_value='production'), \
             patch.object(Path, 'read_text') as read:
            with self.assertRaisesRegex(RuntimeError, 'Helan root required'):
                target.require_work(Path('/root/hy2-systemd-realtest.fixture'), 'prepare')
            read.assert_not_called()

    def test_same_boot_cannot_be_reported_as_a_reboot(self):
        reads = [json.dumps({'source_ref': target.SOURCE_REF}), json.dumps({'boot_id': 'same-boot'})]
        with patch.object(target.os, 'geteuid', return_value=0), \
             patch.object(target.platform, 'node', return_value='helan'), \
             patch.object(Path, 'read_text', side_effect=reads), \
             patch.object(target, 'boot_id', return_value='same-boot'), \
             patch.object(target.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'Boot ID unchanged'):
                target.require_work(Path('/root/hy2-systemd-realtest.fixture'), 'verify')
            run.assert_not_called()

    def test_last_check_failure_cannot_mark_reboot_acceptance_complete(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(target, 'boot_id', return_value='new-boot'):
            suite = target.Acceptance(Path(temp), 'verify', {})
            with patch('builtins.print'):
                for index in range(6): suite.case(str(index), lambda: None)
                def failure(): raise RuntimeError('deliberate failure')
                with self.assertRaises(RuntimeError): suite.case('last', failure)
            report = json.loads((Path(temp) / 'verify-result.json').read_text())
            self.assertEqual(report['failed'], 1)
            self.assertFalse(report['reboot_acceptance_complete'])


if __name__ == '__main__':
    unittest.main()
