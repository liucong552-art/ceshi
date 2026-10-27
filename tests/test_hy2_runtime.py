"""Behavioral checks of the actual HY2 embedded runtime; no host networking."""
import json
from pathlib import Path
import re
import tempfile
import time
import unittest
from unittest.mock import patch

from hy2_test_support import ROOT, bash, function, module, payload, sandbox


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.prefix = sandbox(self.root)

    def test_literal_metadata_first_field_and_atomic_update(self):
        path = self.root / 'meta'
        marker = self.root / 'must-not-exist'
        path.write_text(f'# ignored\nVALUE=$(touch {marker})\nVALUE=second\nURL=https://host/a=b\\c\n')
        result = bash(self.prefix + fr'''
local_test() {{
  local -A HY2_META=()
  hy2_meta_read '{path}'
  printf '%s\n%s\n' "${{HY2_META[VALUE]}}" "$(hy2_meta_get '{path}' URL)"
  hy2_meta_upsert '{path}' URL 'https://other/a=b\c'
}}
local_test
''')
        self.assertIn('$(touch ', result.stdout)
        self.assertIn('https://host/a=b\\c', result.stdout)
        self.assertFalse(marker.exists())
        self.assertIn('URL=https://other/a=b\\c', path.read_text())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertFalse(list(self.root.glob('meta.tmp.*')))

    def test_quota_save_and_interrupted_rebuild_do_not_double_count(self):
        path = self.root / 'state/quota/40001.env'
        path.write_text('PORT=40001\nOWNER_KIND=manual\nORIGINAL_LIMIT_BYTES=100\nSAVED_USED_BYTES=30\nLIMIT_BYTES=70\nREBUILD_PENDING=0\n')
        bash(self.prefix + f'''
hy2_pq_live_used_bytes() {{ echo 25; }}
hy2_pq_rebuild_port() {{ printf '%s\n' "$2" >>'{self.root}/rebuilt'; }}
hy2_pq_save_one '{path}'
''')
        self.assertIn('SAVED_USED_BYTES=55\n', path.read_text())
        self.assertIn('LIMIT_BYTES=45\n', path.read_text())
        self.assertIn('REBUILD_PENDING=0\n', path.read_text())
        path.write_text(path.read_text().replace('REBUILD_PENDING=0', 'REBUILD_PENDING=1'))
        bash(self.prefix + f'''
hy2_pq_live_used_bytes() {{ echo 'must not read old live counters' >&2; return 99; }}
hy2_pq_rebuild_port() {{ printf '%s\n' "$2" >>'{self.root}/rebuilt'; }}
hy2_pq_save_one '{path}'
''')
        self.assertIn('SAVED_USED_BYTES=55\n', path.read_text())
        self.assertIn('REBUILD_PENDING=0\n', path.read_text())
        self.assertEqual((self.root / 'rebuilt').read_text(), '45\n45\n')

    def test_unreadable_quota_does_not_write_a_false_zero(self):
        path = self.root / 'state/quota/40001.env'
        original = 'PORT=40001\nOWNER_KIND=manual\nORIGINAL_LIMIT_BYTES=100\nSAVED_USED_BYTES=90\nLIMIT_BYTES=10\nREBUILD_PENDING=0\n'
        path.write_text(original)
        result = bash(self.prefix + f'''
hy2_pq_live_used_bytes() {{ return 1; }}
if hy2_pq_save_one '{path}'; then exit 98; else exit 0; fi
''')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(path.read_text(), original)

    def test_quota_emits_shared_bidirectional_udp_guards(self):
        bash(self.prefix + f'''
hy2_pq_lock() {{ :; }}
hy2_pq_ensure_base() {{ :; }}
hy2_pq_append_atomic_deletes() {{ :; }}
nft() {{ cp "$2" '{self.root}/batch'; }}
hy2_pq_rebuild_port 40001 1073741824
''')
        batch = (self.root / 'batch').read_text()
        self.assertIn('udp dport 40001 quota name "hy2_pq_q_40001" drop', batch)
        self.assertIn('udp sport 40001 quota name "hy2_pq_q_40001" drop', batch)
        self.assertNotIn('tcp ', batch)
        self.assertEqual(batch.count('add quota '), 1)

    def test_generated_url_quotes_credentials_and_obfuscation(self):
        result = bash(self.prefix + "hy2_build_url 'p:a@ss' '2001:db8::1' 40001 '中文节点' 1 'x&y' 'hy2.example.com'\n")
        self.assertIn('hy2://p%3Aa%40ss@[2001:db8::1]:40001/', result.stdout)
        self.assertIn('obfs-password=x%26y', result.stdout)

    def test_cleanup_serializes_quota_snapshot_and_preserves_parent_lock(self):
        body = function(payload('hy2_cleanup_one.sh'), 'cleanup_save_quota')
        bash(self.prefix + body + '''
hy2_pq_save_one() { [[ "${HY2_PQ_LOCK_HELD:-0}" == 1 ]] || exit 97; }
cleanup_save_quota unused
[[ "${HY2_PQ_LOCK_HELD:-0}" == 0 ]]
hy2_pq_lock
cleanup_save_quota unused
[[ "$HY2_PQ_LOCK_HELD" == 1 ]]
hy2_pq_unlock
''')


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        RuntimeTests.setUp(self)
        self.wd = payload('hy2_managed_watchdog.sh')

    def test_wg_uses_check_time_and_device_route(self):
        path = self.root / 'node.env'
        path.write_text('WG_IF=wg-nat\nMARK=2333\nTABLE_ID=2333\nOIF_RULE_PRIORITY=12333\nHANDSHAKE_MAX=180\n')
        body = function(self.wd, 'wd_nat_guard_healthy') + function(self.wd, 'wd_node_healthy')
        mocks = f'''
systemctl() {{ return 0; }}
hy2_port_is_listening_udp() {{ return 0; }}
ip() {{
  case "$*" in
    '-4 rule show') echo '12333: from all oif wg-nat lookup 2333';;
    '-4 route show table 2333') echo 'prohibit default metric 42760';;
    *) printf '%s\\n' "$*" >'{self.root}/route-args'; echo '1.1.1.1 dev wg-nat src 10.66.66.1';;
  esac
}}
iptables() {{ return 0; }}
'''
        for offset, expected in ((0, 0), (-90, 0), (-240, 1), (240, 1)):
            with self.subTest(offset=offset):
                stamp = int(time.time()) + offset
                result = bash(self.prefix + body + mocks + f'''
now=$(($(date +%s)-60))
wg() {{ echo 'peer {stamp}'; }}
wd_node_healthy hy2-temp-test 40001 '{path}' nat
''', check=False)
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertIn('oif wg-nat', (self.root / 'route-args').read_text())
        result = bash(self.prefix + body + mocks + f'''
now=$(date +%s)
iptables() {{ return 1; }}
wg() {{ echo 'peer $(date +%s)'; }}
wd_node_healthy hy2-temp-test 40001 '{path}' nat
''', check=False)
        self.assertNotEqual(result.returncode, 0)

    def test_persistent_72_hour_grace_and_recovery(self):
        path = self.root / 'node.env'
        path.write_text('WATCHDOG_FAILURES=2\nCREATE_EPOCH=900\n')
        cleaner = self.root / 'bin/hy2_cleanup_one.sh'
        cleaner.write_text(f'#!/bin/sh\ntouch "{self.root}/deleted"\n')
        cleaner.chmod(0o700)
        names = ('wd_failure_count', 'wd_grace_start', 'wd_clear_failure_state', 'wd_record_failed_recovery')
        funcs = ''.join(function(self.wd, name) for name in names).replace('/usr/local/sbin', str(self.root / 'bin'))
        prefix = self.prefix + funcs + 'WD_GRACE_SECONDS=259200\nrc=0\n'
        bash(prefix + f"now=1000; wd_record_failed_recovery '{path}' hy2-temp-test test\n")
        self.assertIn('WATCHDOG_GRACE_START_EPOCH=1000', path.read_text())
        bash(prefix + f"now=1100; wd_record_failed_recovery '{path}' hy2-temp-test test\n")
        self.assertIn('WATCHDOG_GRACE_START_EPOCH=1000', path.read_text())
        bash(prefix + f"wd_final_boot_ready() {{ return 1; }}\nnow=260200; wd_record_failed_recovery '{path}' hy2-temp-test test\n")
        self.assertFalse((self.root / 'deleted').exists())
        bash(prefix + f"wd_final_boot_ready() {{ return 0; }}\nnow=260200; wd_record_failed_recovery '{path}' hy2-temp-test test\n")
        self.assertTrue((self.root / 'deleted').exists())
        bash(prefix + f"wd_clear_failure_state '{path}'\n")
        self.assertIn('WATCHDOG_FAILURES=0', path.read_text())
        self.assertIn('WATCHDOG_GRACE_START_EPOCH=0', path.read_text())


class QueryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name in ('quota-query.py', 'watchdog-batch.py'):
            (self.root / name).write_text(payload(name))
        self.q = module(self.root / 'quota-query.py', 'hy2q')
        self.w = module(self.root / 'watchdog-batch.py', 'hy2w')

    def snapshot(self, used=10):
        return ({'hy2_pq_in_40001': {'bytes': 7}, 'hy2_pq_out_40001': {'bytes': 0}},
                {'hy2_pq_q_40001': {'used': used}},
                {chain: [{'comment': 'hy2-pq-' + part + '-' + direction + '-40001'}
                         for part in ('drop', 'count')]
                 for chain, direction in (('pq_input', 'in'), ('pq_output', 'out'))})

    def test_active_exhausted_and_inconsistent_snapshots(self):
        self.assertEqual(self.q.state('40001', 100, 0, self.snapshot()), 'active')
        self.assertEqual(self.q.state('40001', 100, 90, self.snapshot()), 'exhausted')
        with patch.object(self.q, 'snapshot', side_effect=[self.snapshot(), self.snapshot(100)]):
            with self.assertRaises(ValueError):
                self.q.checked_state('40001', 100, 0)
        for value in (None, True, -1, '0'):
            with self.assertRaises(ValueError):
                self.q.uint(value)

    def test_batch_rejects_pending_and_missing_ip_guard(self):
        quota, ip = self.root / 'quota', self.root / 'ip'
        quota.mkdir(); ip.mkdir()
        meta = quota / '40001.env'
        meta.write_text('ORIGINAL_LIMIT_BYTES=100\nSAVED_USED_BYTES=0\nREBUILD_PENDING=0\n')
        (ip / '40001.env').write_text('IP_VERSION=6\n')
        comments = {'hy2-il-' + part + '-40001' for part in ('refresh', 'claim', 'drop', 'family')}
        def batch(guards):
            with patch.object(self.q, 'snapshot', return_value=self.snapshot()):
                return self.w.batch(quota, ip, self.q, lambda: ({'hy2_il6_40001'}, guards))
        self.assertIn('Q|40001|100|0|0|active', batch(comments))
        self.assertIn('I|40001|6|active', batch(comments))
        self.assertNotIn('I|', batch(comments - {'hy2-il-family-40001'}))
        meta.write_text(meta.read_text().replace('REBUILD_PENDING=0', 'REBUILD_PENDING=1'))
        self.assertNotIn('Q|', batch(comments))


if __name__ == '__main__':
    unittest.main()
