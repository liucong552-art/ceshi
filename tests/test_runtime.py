"""Execute generated Bash functions with mocked kernel/service boundaries."""
import shlex
import os
import tempfile
from pathlib import Path
import unittest
from helpers import ROOT, bash, heredocs, library, payload


class GeneratedTests(unittest.TestCase):
    def test_nat_multi_peer_allocation_update_delete(self):
        source = (ROOT / 'nat.sh').read_text(encoding='utf-8').removesuffix('main "$@"\n')
        with tempfile.TemporaryDirectory() as d:
            result = bash(source + f'\nPEER_DIR={shlex.quote(d)}\n' + '''
WG_ADDR=10.66.66.2/24
resolve_endpoint_ipv4() { echo 192.0.2.1; }
[[ "$(auto_allocate_vps_wg_addr first)" == 10.66.66.1/32 ]]
write_peer_file first first.example.com 10.66.66.1/32 AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA= 25
[[ "$(auto_allocate_vps_wg_addr second)" == 10.66.66.3/32 ]]
write_peer_file second second.example.com 10.66.66.3/32 BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB= 25
[[ "$(auto_allocate_vps_wg_addr first)" == 10.66.66.1/32 ]]
write_peer_file first updated.example.com 10.66.66.1/32 CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC= 25
[[ "$(peer_meta_value "$(peer_file first)" vps_endpoint)" == updated.example.com ]]
rm -f -- "$(peer_file first)"
[[ "$(auto_allocate_vps_wg_addr third)" == 10.66.66.1/32 ]]
echo ALLOCATION_OK
''')
            self.assertIn('ALLOCATION_OK', result.stdout)

    @unittest.skipIf(os.name == 'nt', 'requires Linux flock and Unix file permissions')
    def test_real_lock_contention_and_install_rollback(self):
        source = (ROOT / 'hy2.sh').read_text(encoding='utf-8').removesuffix('main "$@"\n')
        with tempfile.TemporaryDirectory() as d:
            prefix = shlex.quote(d)
            result = bash(source + f'\ntest_root={prefix}\n' + '''
INSTALL_UNITS=()
INSTALL_TARGETS=("$test_root/existing" "$test_root/new")
printf before >"$test_root/existing"
chmod 600 "$test_root/existing"
begin_install_transaction
printf after >"$test_root/existing"
printf new >"$test_root/new"
systemctl() { :; }
rollback_install_transaction
[[ "$(cat "$test_root/existing")" == before && ! -e "$test_root/new" ]]
[[ "$(stat -c %a "$test_root/existing")" == 600 ]]
exec 8>"$test_root/contention.lock"
flock -n 8
if (exec 8>&-; exec 9>"$test_root/contention.lock"; flock -n 9); then exit 99; fi
echo ROLLBACK_LOCK_OK
''')
            self.assertIn('ROLLBACK_LOCK_OK', result.stdout)

    def test_watchdog_stop_helper_handles_unset_tag(self):
        source = payload('hy2_managed_watchdog.sh')
        helper = source[source.index('stop_temp_listener_failclosed() {'):]
        helper = helper[:helper.index('\n}\n') + 3]
        with tempfile.TemporaryDirectory() as d:
            result = bash(library('common.sh') + '\nHY2_LOCK_DIR=' + shlex.quote(d) + '\n' + helper + '''
unset tag
systemctl() { :; }
timeout() { shift; "$@"; }
stop_temp_listener_failclosed hy2-temp-test
''')
            self.assertEqual(result.returncode, 0)

    def test_all_nested_bash_and_python_payloads_compile(self):
        counts = {'bash': 0, 'python': 0}
        for path in ROOT.glob('*.sh'):
            for header, marker, body in heredocs(path.read_text(encoding='utf-8')):
                if body.startswith('#!/usr/bin/env bash'):
                    result = bash('bash -n <<\'CHECK_SOURCE\'\n' + body + 'CHECK_SOURCE\n')
                    self.assertEqual(result.returncode, 0, header)
                    counts['bash'] += 1
                elif body.startswith('#!/usr/bin/env python') or marker == 'PY':
                    compile(body, f'{path.name}:{header}', 'exec')
                    counts['python'] += 1
        self.assertGreaterEqual(counts['bash'], 25)
        self.assertGreaterEqual(counts['python'], 10)

    def test_actual_config_and_unit_generators(self):
        result = bash(library('common.sh') + '''
hy2_temp_meta_file() { printf '/var/lib/hy2/temp/%s.env' "$1"; }
hy2_temp_unit_text hy2-temp-probe /etc/hysteria/temp/probe.yaml nat wg-nat
''')
        self.assertIn('Requisite=wg-quick@wg-nat.service', result.stdout)
        self.assertIn('ExecStartPre=/usr/local/sbin/hy2_nat_preflight.sh hy2-temp-probe', result.stdout)
        self.assertIn('--from-stop-post', result.stdout)
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / 'node.yaml'
            bash(library('common.sh') + f'\nhy2_write_server_cfg {shlex.quote(target.as_posix())} "[::]:40000" "secret\x27quoted" /cert /key https://example.com 1 obfs wg-nat 4\n')
            config = target.read_text(encoding='utf-8')
            self.assertIn("password: 'secret''quoted'", config)
            self.assertIn("bindDevice: 'wg-nat'", config)
            self.assertIn('mode: 4', config)
            self.assertIn("listen: '[::]:40000'", config)

    def quota(self, source):
        return bash(library('common.sh') + library('quota-lib.sh') + source)

    def test_save_never_rebuilds_or_refunds_live_generation(self):
        result = self.quota('''
hy2_pq_lock() { :; }
hy2_pq_meta_owner_exists() { return 0; }
hy2_meta_get() {
 case "$2" in
 PORT) echo 40000;; ORIGINAL_LIMIT_BYTES) echo 1000;; SAVED_USED_BYTES) echo 200;;
 REBUILD_PENDING) echo 0;; esac
}
hy2_pq_live_used_bytes() { echo 123; }
hy2_pq_write_meta() { printf 'WRITE:%s:%s:%s:%s\\n' "$2" "$3" "$4" "${15}"; }
hy2_pq_rebuild_port() { echo UNEXPECTED_REBUILD; return 1; }
hy2_log() { :; }
hy2_pq_save_one /dev/null
'''.replace('/dev/null\n', '"$BASH"\n'))
        self.assertIn('WRITE:1000:200:800:323', result.stdout)
        self.assertNotIn('UNEXPECTED', result.stdout)

    def test_unreadable_snapshot_does_not_write_zero(self):
        result = self.quota('''
hy2_pq_lock() { :; }
hy2_pq_meta_owner_exists() { return 0; }
hy2_meta_get() { case "$2" in PORT) echo 40000;; ORIGINAL_LIMIT_BYTES) echo 1000;; SAVED_USED_BYTES) echo 200;; esac; }
hy2_pq_live_used_bytes() { return 1; }
hy2_pq_write_meta() { echo UNSAFE_WRITE; }
if hy2_pq_save_one "$BASH"; then exit 99; fi
''')
        self.assertNotIn('UNSAFE_WRITE', result.stdout)

    def test_intact_restore_is_idempotent(self):
        for state in ('active', 'exhausted'):
            with self.subTest(state=state):
                result = self.quota('''
hy2_pq_lock() { :; }
hy2_pq_meta_owner_exists() { return 0; }
hy2_meta_get() { echo 40000; }
hy2_pq_state() { echo ''' + state + '''; }
hy2_pq_rebuild_port() { echo UNSAFE_REBUILD; return 1; }
hy2_pq_restore_one "$BASH"
''')
                self.assertNotIn('UNSAFE_REBUILD', result.stdout)

    def test_missing_rules_restore_checkpoint_and_legacy_baseline(self):
        for checkpoint in ('350', ''):
            with self.subTest(checkpoint=checkpoint), tempfile.TemporaryDirectory() as d:
                meta = Path(d) / '40000.env'
                meta.write_text('PORT=40000\nORIGINAL_LIMIT_BYTES=1000\nSAVED_USED_BYTES=200\n'
                                f'CHECKPOINT_USED_BYTES={checkpoint}\nLIMIT_BYTES=800\nREBUILD_PENDING=0\n', encoding='utf-8')
                result = self.quota('''
hy2_pq_lock() { :; }
hy2_pq_meta_owner_exists() { return 0; }
hy2_pq_state() { echo stale; }
hy2_pq_live_used_bytes() { return 1; }
hy2_pq_rebuild_port() { printf 'RESTORE:%s:%s\\n' "$1" "$2"; }
hy2_pq_restore_one ''' + shlex.quote(meta.as_posix()) + '\n')
                self.assertIn(f'RESTORE:40000:{1000-int(checkpoint or 200)}', result.stdout)
                self.assertIn('REBUILD_PENDING=0', meta.read_text(encoding='utf-8'))

    def test_shared_quota_is_used_once_per_direction(self):
        result = self.quota('''
HY2_LOCK_DIR="$(mktemp -d)"
trap 'rm -rf -- "$HY2_LOCK_DIR"' EXIT
hy2_pq_lock() { :; }
hy2_pq_ensure_base() { :; }
hy2_pq_append_atomic_deletes() { :; }
nft() { [[ "$1" == -f ]] && cat "$2"; }
hy2_pq_rebuild_port 40000 1000
''')
        self.assertEqual(result.stdout.count('quota name "hy2_pq_q_40000"'), 2)
        self.assertEqual(result.stdout.count('add quota '), 1)

    def test_ip_restore_preserves_active_slots(self):
        result = bash(library('common.sh') + library('iplimit-lib.sh') + '''
hy2_il_meta_owner_exists() { return 0; }
hy2_meta_get() { case "$2" in PORT) echo 40000;; IP_LIMIT) echo 2;; IP_STICKY_SECONDS) echo 120;; IP_VERSION) echo 6;; esac; }
hy2_il_state() { echo active; }
hy2_il_rebuild_port() { echo UNSAFE_EVICTION; return 1; }
hy2_il_restore_one "$BASH"
''')
        self.assertNotIn('UNSAFE_EVICTION', result.stdout)

    def test_first_certificate_hook_and_missing_existing_state(self):
        hook = next(
            body for _, marker, body in heredocs(payload('onekey_hy2_main_tls.sh')) if marker == 'HOOK')
        hook = hook.replace('source /usr/local/lib/hy2/common.sh', '''
HY2_MAIN_STATE_FILE=/nonexistent/hy2-test-main.env
HY2_MAIN_CFG=/nonexistent/hy2-test-main.yaml
HY2_TEMP_LOCK_HELD=1
hy2_ensure_runtime_dirs() { :; }
hy2_log() { :; }
systemctl() { return 3; }
''')
        self.assertEqual(bash(hook).returncode, 0)
        result = bash(hook.replace('systemctl() { return 3; }', 'systemctl() { return 0; }'), check=False)
        self.assertNotEqual(result.returncode, 0)

    def test_clear_all_reports_cleanup_failure(self):
        source = payload('hy2_clear_all.sh').replace('source /usr/local/lib/hy2/common.sh', '''
HY2_LOCK_DIR=/unused
hy2_ensure_runtime_dirs() { :; }
hy2_acquire_lock_fd() { :; }
hy2_collect_temp_tags() { echo hy2-temp-probe; }
''').replace('/usr/local/sbin/hy2_cleanup_one.sh', 'false')
        self.assertNotEqual(bash(source, check=False).returncode, 0)
