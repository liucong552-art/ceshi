#!/usr/bin/env python3
"""Real nft/UDP checks of shipped HY2 helpers, ONLY inside a fresh net namespace.

The shell launcher supplies HY2_TEST_HOST_NETNS and creates the namespace.
This program refuses to modify networking if that isolation cannot be verified.
"""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import shlex
import socket
import socketserver
import subprocess
import tempfile
import threading
import time
import unittest

from hy2_test_support import module, payload, sandbox
from hy2_loopback import TCPHandler, receive, wait_for

V6 = ('fd42:6879:3200::1', 'fd42:6879:3200::2', 'fd42:6879:3200::3')
WORK = None
BINARY = None


def command(args, check=True):
    return subprocess.run(args, capture_output=True, text=True, timeout=20, check=check)


def ensure_isolation():
    expected = os.environ.get('HY2_TEST_HOST_NETNS', '')
    try:
        current = os.readlink('/proc/self/ns/net')
    except OSError as error:
        raise RuntimeError('Refusing networking changes: cannot verify network namespace') from error
    if os.geteuid() != 0 or not expected or expected == current:
        raise RuntimeError('Refusing networking changes outside the launcher-created namespace')
    links = json.loads(command(['ip', '-j', 'link', 'show']).stdout)
    if {row['ifname'] for row in links} != {'lo'}:
        raise RuntimeError('Expected a fresh namespace containing only lo')
    command(['ip', 'link', 'set', 'lo', 'up'])
    for address in V6:
        command(['ip', '-6', 'addr', 'add', address + '/128', 'dev', 'lo', 'nodad'])
    command(['nft', '-j', 'list', 'tables'])


class Echo:
    def __init__(self, dual=False, reply=None):
        self.dual = dual
        self.reply = reply
        self.received = 0
        self.closed = threading.Event()
        self.sock = socket.socket(socket.AF_INET6 if dual else socket.AF_INET, socket.SOCK_DGRAM)
        if dual:
            self.sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        self.sock.bind(('::' if dual else '127.0.0.1', 0))
        self.port = self.sock.getsockname()[1]
        self.sock.settimeout(0.1)
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while not self.closed.is_set():
            try:
                data, address = self.sock.recvfrom(65536)
                self.received += 1
                self.sock.sendto(data if self.reply is None else self.reply, address)
            except socket.timeout:
                continue
            except OSError:
                if self.closed.is_set():
                    return
                # Kernel DROP can surface as a send error on some setups.

    def close(self):
        self.closed.set()
        self.thread.join(timeout=1)
        self.sock.close()


def exchange(echo, source='127.0.0.2', data=b'hy2-kernel-probe', timeout=0.35):
    family = socket.AF_INET6 if ':' in source else socket.AF_INET
    dest = V6[0] if family == socket.AF_INET6 else '127.0.0.1'
    with socket.socket(family, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.bind((source, 0))
        try:
            sock.sendto(data, (dest, echo.port))
            reply = sock.recv(65536)
            return reply == (data if echo.reply is None else echo.reply)
        except (socket.timeout, ConnectionRefusedError, ConnectionResetError):
            return False


class KernelCases(unittest.TestCase):
    def setUp(self):
        # These table names exist only in this test's isolated network namespace.
        for table in ('hy2_pq', 'hy2_iplimit'):
            command(['nft', 'delete', 'table', 'inet', table], check=False)
        self.root = Path(tempfile.mkdtemp(prefix=self._testMethodName + '-', dir=WORK / 'runtime'))
        self.prefix = sandbox(self.root) + f'source {shlex.quote(str(self.root / "lib/iplimit-lib.sh"))}\n'
        (self.root / 'subscriptions').mkdir()
        for name in ('quota-query.py', 'watchdog-batch.py'):
            text = payload(name).replace('/var/lib/hy2', str(self.root / 'state'))
            (self.root / 'lib' / name).write_text(text)
        for name in ('hy2_managed_watchdog.sh', 'hy2_temp_sub.sh'):
            text = payload(name)
            text = text.replace('/root/hy2_temp_subscription', str(self.root / 'subscriptions/hy2_temp_subscription'))
            text = text.replace('/root/.hy2_temp_subscription', str(self.root / 'subscriptions/.hy2_temp_subscription'))
            text = text.replace('/usr/local/lib/hy2', str(self.root / 'lib'))
            text = text.replace('/usr/local/sbin', str(self.root / 'bin'))
            path = self.root / 'bin' / name
            path.write_text(text)
            path.chmod(0o700)

    def tearDown(self):
        snapshot = {}
        for table in ('hy2_pq', 'hy2_iplimit'):
            got = command(['nft', '-j', 'list', 'table', 'inet', table], check=False)
            snapshot[table] = json.loads(got.stdout) if got.returncode == 0 else {'error': got.stderr}
        (self.root / 'final-nft.json').write_text(json.dumps(snapshot, indent=2))

    def shell(self, script, check=True):
        proc = subprocess.run(['bash', '-Eeuo', 'pipefail', '-c', self.prefix + script],
                              capture_output=True, text=True, timeout=40)
        with (self.root / 'helpers.log').open('a') as log:
            log.write(script + '\nSTDOUT:\n' + proc.stdout + '\nSTDERR:\n' + proc.stderr + '\n')
        if check and proc.returncode:
            self.fail(f'Helper exit {proc.returncode}: {proc.stdout}\n{proc.stderr}')
        return proc

    def call(self, name, *args):
        return self.shell(name + ' ' + ' '.join(shlex.quote(str(a)) for a in args)).stdout.strip()

    def echo(self, dual=False, reply=None):
        server = Echo(dual, reply)
        self.addCleanup(server.close)
        return server

    def quota(self, echo, limit=8192):
        self.call('hy2_pq_add_managed_port', echo.port, limit, 'manual', '', 0, 0)

    def iplimit(self, echo, limit=1, sticky=120, version=4):
        self.call('hy2_il_add_managed_port', echo.port, limit, sticky, 'manual', '', version)

    def snapshot(self, echo):
        state, original, used, left = self.call('hy2_pq_snapshot', echo.port).split('|')
        return state, int(original), int(used), int(left)

    def metadata(self, kind, echo):
        path = self.root / 'state' / kind / (str(echo.port) + '.env')
        result = {}
        for line in path.read_text().splitlines():
            if '=' in line:
                key, value = line.split('=', 1)
                result.setdefault(key, value)
        return result

    def watchdog(self):
        proc = subprocess.run(['bash', str(self.root / 'bin/hy2_managed_watchdog.sh')],
                              text=True, capture_output=True, timeout=60)
        (self.root / 'watchdog.log').write_text(proc.stdout + proc.stderr)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_01_baseline_ipv4_and_ipv6_udp(self):
        echo = self.echo(dual=True)
        self.assertTrue(exchange(echo))
        self.assertTrue(exchange(echo, V6[1]))

    def test_02_quota_counts_upload_and_download(self):
        echo = self.echo()
        self.quota(echo)
        self.assertTrue(exchange(echo, data=b'x' * 64))
        counters = [int(self.call('hy2_pq_counter_bytes', 'hy2_pq_' + d + '_' + str(echo.port)))
                    for d in ('in', 'out')]
        self.assertTrue(all(n > 0 for n in counters), counters)
        state, limit, used, left = self.snapshot(echo)
        self.assertEqual(state, 'active')
        self.assertGreaterEqual(used, sum(counters))
        self.assertEqual(used + left, limit)

    def test_03_upload_over_quota_is_dropped_before_server(self):
        echo = self.echo()
        self.quota(echo, 1024)
        self.assertFalse(exchange(echo, data=b'x' * 2048))
        self.assertEqual(echo.received, 0)
        self.assertEqual(self.snapshot(echo), ('exhausted', 1024, 1024, 0))

    def test_04_download_over_quota_is_dropped(self):
        echo = self.echo(reply=b'd' * 4096)
        self.quota(echo, 1024)
        self.assertFalse(exchange(echo, data=b'u'))
        self.assertEqual(echo.received, 1)
        self.assertEqual(self.snapshot(echo), ('exhausted', 1024, 1024, 0))

    def test_05_ipv4_ipv6_share_one_quota(self):
        echo = self.echo(dual=True)
        self.quota(echo, 2048)
        self.assertTrue(exchange(echo))
        first = self.snapshot(echo)[2]
        self.assertTrue(exchange(echo, V6[1]))
        self.assertGreater(self.snapshot(echo)[2], first)
        self.assertFalse(exchange(echo, V6[1], b'x' * 4096))
        self.assertFalse(exchange(echo))
        self.assertFalse(exchange(echo, V6[1]))

    def test_06_save_restore_does_not_reset_or_double_count(self):
        echo = self.echo()
        self.quota(echo)
        self.assertTrue(exchange(echo, data=b'x' * 128))
        used = self.snapshot(echo)[2]
        meta = self.root / 'state/quota' / (str(echo.port) + '.env')
        self.shell('hy2_pq_lock\nhy2_pq_save_one ' + shlex.quote(str(meta)) + '\nhy2_pq_unlock')
        self.assertEqual(self.snapshot(echo)[2], used)
        command(['nft', 'delete', 'table', 'inet', 'hy2_pq'])
        self.call('hy2_pq_restore_one', meta)
        self.assertEqual(self.snapshot(echo)[2], used)
        self.shell('hy2_pq_lock\nhy2_pq_save_one ' + shlex.quote(str(meta)) + '\nhy2_pq_unlock')
        self.assertEqual(self.snapshot(echo)[2], used)
        self.assertTrue(exchange(echo))
        self.assertGreater(self.snapshot(echo)[2], used)

    def test_07_unreadable_kernel_state_is_not_saved_as_zero(self):
        echo = self.echo()
        self.quota(echo)
        self.assertTrue(exchange(echo))
        meta = self.root / 'state/quota' / (str(echo.port) + '.env')
        self.shell('hy2_pq_lock\nhy2_pq_save_one ' + shlex.quote(str(meta)) + '\nhy2_pq_unlock')
        before = meta.read_bytes()
        command(['nft', 'delete', 'table', 'inet', 'hy2_pq'])
        proc = self.shell('hy2_pq_lock\nhy2_pq_save_one ' + shlex.quote(str(meta)), check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(meta.read_bytes(), before)

    def test_08_exhausted_quota_stays_blocked_after_restore(self):
        echo = self.echo()
        self.quota(echo, 1024)
        self.assertFalse(exchange(echo, data=b'x' * 2048))
        meta = self.root / 'state/quota' / (str(echo.port) + '.env')
        self.shell('hy2_pq_lock\nhy2_pq_save_one ' + shlex.quote(str(meta)) + '\nhy2_pq_unlock')
        command(['nft', 'delete', 'table', 'inet', 'hy2_pq'])
        self.call('hy2_pq_restore_one', meta)
        self.assertEqual(self.snapshot(echo), ('exhausted', 1024, 1024, 0))
        self.assertFalse(exchange(echo))

    def test_09_ipv4_ip_slots_and_family_isolation(self):
        echo = self.echo(dual=True)
        self.iplimit(echo, version=4)
        self.assertTrue(exchange(echo, '127.0.0.2'))
        self.assertFalse(exchange(echo, '127.0.0.3'))
        self.assertFalse(exchange(echo, V6[1]))
        self.assertEqual(self.call('hy2_il_active_ips', echo.port), '127.0.0.2')
        self.assertEqual(self.call('hy2_il_active_count', echo.port), '1')

    def test_10_ipv6_ip_slots_and_family_isolation(self):
        echo = self.echo(dual=True)
        self.iplimit(echo, version=6)
        self.assertTrue(exchange(echo, V6[1]))
        self.assertFalse(exchange(echo, V6[2]))
        self.assertFalse(exchange(echo))
        self.assertEqual(self.call('hy2_il_active_ips', echo.port), V6[1])
        self.assertEqual(self.call('hy2_il_active_count', echo.port), '1')

    def test_11_concurrent_sources_cannot_overfill_one_slot(self):
        echo = self.echo()
        self.iplimit(echo)
        sources = ['127.0.0.' + str(n) for n in range(2, 10)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda source: exchange(echo, source), sources))
        self.assertEqual(sum(results), 1, dict(zip(sources, results)))
        admitted = sources[results.index(True)]
        self.assertEqual(self.call('hy2_il_active_ips', echo.port), admitted)

    def test_12_slot_refresh_and_idle_release(self):
        echo = self.echo()
        self.iplimit(echo, sticky=3)
        self.assertTrue(exchange(echo, '127.0.0.2'))
        time.sleep(1.7)
        self.assertTrue(exchange(echo, '127.0.0.2'))
        last_refresh = time.monotonic()
        time.sleep(1.7)
        # First claim would have expired; the refreshed entry must still block B.
        self.assertFalse(exchange(echo, '127.0.0.3'))
        admitted = False
        deadline = last_refresh + 8
        while time.monotonic() < deadline:
            if exchange(echo, '127.0.0.3'):
                admitted = True
                break
            time.sleep(0.1)
        self.assertTrue(admitted, 'idle slot was not released within the bounded GC wait')

    def test_13_healthy_watchdog_preserves_active_slots_and_usage(self):
        echo = self.echo()
        self.quota(echo)
        self.iplimit(echo, limit=2)
        self.assertTrue(exchange(echo, '127.0.0.2'))
        self.assertTrue(exchange(echo, '127.0.0.3'))
        ips = set(self.call('hy2_il_active_ips', echo.port).split())
        used = self.snapshot(echo)[2]
        self.watchdog()
        self.assertEqual(set(self.call('hy2_il_active_ips', echo.port).split()), ips)
        self.assertEqual(self.snapshot(echo)[2], used)
        self.assertFalse(exchange(echo, '127.0.0.4'))

    def test_14_watchdog_restores_deleted_quota_table(self):
        echo = self.echo()
        self.quota(echo)
        self.assertTrue(exchange(echo))
        meta = self.root / 'state/quota' / (str(echo.port) + '.env')
        self.shell('hy2_pq_lock\nhy2_pq_save_one ' + shlex.quote(str(meta)) + '\nhy2_pq_unlock')
        used = self.snapshot(echo)[2]
        command(['nft', 'delete', 'table', 'inet', 'hy2_pq'])
        self.assertEqual(self.call('hy2_pq_state', echo.port), 'stale')
        self.watchdog()
        self.assertEqual(self.snapshot(echo)[2], used)
        self.assertEqual(self.call('hy2_pq_state', echo.port), 'active')
        self.assertTrue(exchange(echo))

    def test_15_watchdog_restores_deleted_ip_table(self):
        echo = self.echo(dual=True)
        self.iplimit(echo)
        self.assertTrue(exchange(echo))
        command(['nft', 'delete', 'table', 'inet', 'hy2_iplimit'])
        self.assertEqual(self.call('hy2_il_state', echo.port), 'stale')
        self.watchdog()
        self.assertTrue(exchange(echo))
        self.assertFalse(exchange(echo, '127.0.0.3'))
        self.assertFalse(exchange(echo, V6[1]))

    def test_16_watchdog_recovers_interrupted_quota_commit(self):
        echo = self.echo()
        self.quota(echo)
        self.assertTrue(exchange(echo))
        _, limit, used, left = self.snapshot(echo)
        meta = self.root / 'state/quota' / (str(echo.port) + '.env')
        self.shell('hy2_pq_lock\n' + '\n'.join(
            'hy2_meta_upsert ' + shlex.quote(str(meta)) + ' ' + key + ' ' + str(value)
            for key, value in [('SAVED_USED_BYTES', used), ('LIMIT_BYTES', left), ('REBUILD_PENDING', 1)]))
        self.assertEqual(self.call('hy2_pq_state', echo.port), 'stale')
        self.watchdog()
        self.assertEqual(self.snapshot(echo), ('active', limit, used, left))
        self.assertEqual(self.metadata('quota', echo)['REBUILD_PENDING'], '0')

    def test_17_unlimited_ipv4_still_blocks_ipv6(self):
        echo = self.echo(dual=True)
        self.call('hy2_il_apply_family_guard', echo.port, 4)
        self.assertTrue(exchange(echo, '127.0.0.2'))
        self.assertTrue(exchange(echo, '127.0.0.3'))
        self.assertFalse(exchange(echo, V6[1]))

    def test_18_unlimited_ipv6_still_blocks_ipv4(self):
        echo = self.echo(dual=True)
        self.call('hy2_il_apply_family_guard', echo.port, 6)
        self.assertTrue(exchange(echo, V6[1]))
        self.assertTrue(exchange(echo, V6[2]))
        self.assertFalse(exchange(echo))

    def test_19_removing_ip_limit_preserves_family_guard(self):
        echo = self.echo(dual=True)
        self.iplimit(echo)
        self.assertTrue(exchange(echo))
        self.assertFalse(exchange(echo, '127.0.0.3'))
        self.call('hy2_il_delete_and_apply_family_guard', echo.port, 4)
        self.assertTrue(exchange(echo, '127.0.0.3'))
        self.assertFalse(exchange(echo, V6[1]))
        self.assertFalse((self.root / 'state/iplimit' / (str(echo.port) + '.env')).exists())

    def test_20_deleting_one_port_preserves_another(self):
        first, second = self.echo(), self.echo()
        for echo in (first, second):
            self.quota(echo)
            self.iplimit(echo)
            self.assertTrue(exchange(echo))
        self.call('hy2_pq_delete_managed_port', first.port)
        self.call('hy2_il_delete_managed_port', first.port)
        self.assertTrue(exchange(first, '127.0.0.3'))
        self.assertFalse(exchange(second, '127.0.0.3'))
        self.assertTrue(exchange(second))
        self.assertEqual(self.call('hy2_pq_state', second.port), 'active')
        for kind in ('quota', 'iplimit'):
            self.assertFalse((self.root / 'state' / kind / (str(first.port) + '.env')).exists())
        dump = json.loads(command(['nft', '-j', 'list', 'ruleset']).stdout)
        for row in dump['nftables']:
            for kind in ('counter', 'quota', 'set', 'rule'):
                record = row.get(kind, {})
                self.assertFalse(record.get('name', '').endswith('_' + str(first.port)))
                self.assertFalse(record.get('comment', '').endswith('-' + str(first.port)))

    def test_21_real_hy2_encrypted_proxy_is_cut_off_at_quota(self):
        tcp = socketserver.ThreadingTCPServer(('127.0.0.1', 0), TCPHandler)
        tcp.daemon_threads = True
        threading.Thread(target=tcp.serve_forever, daemon=True).start()
        self.addCleanup(tcp.server_close)
        self.addCleanup(tcp.shutdown)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reserve:
            reserve.bind(('127.0.0.1', 0)); server_port = reserve.getsockname()[1]
        with socket.socket() as reserve:
            reserve.bind(('127.0.0.1', 0)); socks_port = reserve.getsockname()[1]
        cert, key = self.root / 'cert.pem', self.root / 'key.pem'
        command(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                 '-subj', '/CN=hy2.test', '-addext', 'subjectAltName=DNS:hy2.test',
                 '-keyout', str(key), '-out', str(cert)])
        server_cfg, client_cfg = self.root / 'server.yaml', self.root / 'client.json'
        password = 'b' * 32
        self.call('hy2_write_server_cfg', server_cfg, '127.0.0.1:' + str(server_port), password,
                  cert, key, 'https://www.apple.com/', 0, '', '', 4)
        client_cfg.write_text(json.dumps({'server': '127.0.0.1:' + str(server_port), 'auth': password,
                                         'tls': {'sni': 'hy2.test', 'ca': str(cert)},
                                         'socks5': {'listen': '127.0.0.1:' + str(socks_port)}}))
        class Node:
            port = server_port
        self.quota(Node(), 32768)
        self.iplimit(Node())
        processes, streams = [], []
        def start(mode, config):
            log_path = self.root / (mode + '.log')
            stream = log_path.open('w'); streams.append(stream)
            proc = subprocess.Popen([str(BINARY), '--log-level', 'debug', mode, '-c', str(config)],
                                    stdout=stream, stderr=subprocess.STDOUT,
                                    env=dict(os.environ, HYSTERIA_DISABLE_UPDATE_CHECK='1'))
            processes.append(proc)
            return proc, log_path
        def transfer(data):
            with socket.create_connection(('127.0.0.1', socks_port), timeout=2) as sock:
                sock.sendall(b'\x05\x01\x00'); self.assertEqual(receive(sock, 2), b'\x05\x00')
                request = b'\x05\x01\x00\x01' + socket.inet_aton('127.0.0.1') + tcp.server_address[1].to_bytes(2, 'big')
                sock.sendall(request)
                reply = receive(sock, 4); self.assertEqual(reply[:2], b'\x05\x00')
                size = {1: 4, 4: 16}.get(reply[3])
                if size is None: size = receive(sock, 1)[0]
                receive(sock, size + 2)
                sock.sendall(data)
                return receive(sock, len(data))
        try:
            server, server_log = start('server', server_cfg)
            wait_for(lambda: 'server up and running' in server_log.read_text(), [server])
            client, client_log = start('client', client_cfg)
            wait_for(lambda: 'SOCKS5 server listening' in client_log.read_text(), [server, client])
            self.assertEqual(transfer(b'warm-up' * 128), b'warm-up' * 128)
            self.assertEqual(self.call('hy2_il_active_count', server_port), '1')
            with self.assertRaises((TimeoutError, OSError, RuntimeError)):
                transfer(b'quota-cutoff' * 16384)
            self.assertEqual(self.snapshot(Node()), ('exhausted', 32768, 32768, 0))
            control = self.echo()
            self.assertTrue(exchange(control), 'an unrelated port must keep working')
        finally:
            for proc in reversed(processes):
                if proc.poll() is None:
                    proc.terminate()
                    try: proc.wait(timeout=4)
                    except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=3)
            for stream in streams: stream.close()


class ReportResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rows = []
    def startTest(self, test):
        super().startTest(test)
        self.started = time.monotonic()
    def record(self, test, status, error=None):
        self.rows.append({'case': test.id().split('.')[-1], 'status': status,
                          'seconds': round(time.monotonic() - self.started, 3), 'error': error})
    def addSuccess(self, test):
        super().addSuccess(test); self.record(test, 'PASS')
    def addFailure(self, test, err):
        super().addFailure(test, err); self.record(test, 'FAIL', self._exc_info_to_string(err, test))
    def addError(self, test, err):
        super().addError(test, err); self.record(test, 'ERROR', self._exc_info_to_string(err, test))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--binary', type=Path, required=True)
    args = parser.parse_args()
    WORK, BINARY = args.work.resolve(), args.binary.resolve()
    ensure_isolation()
    result = unittest.TextTestRunner(verbosity=2, resultclass=ReportResult).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(KernelCases))
    report = {'source_ref': 'd4c405bc19e2c2c2ce06d8c3c6be6376fe3617e8',
              'netns': os.readlink('/proc/self/ns/net'), 'cases': result.rows,
              'passed': sum(row['status'] == 'PASS' for row in result.rows),
              'failed': len(result.failures) + len(result.errors),
              'full_install_wg_reboot_tested': False}
    (WORK / 'result.json').write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps({k: v for k, v in report.items() if k != 'cases'}, indent=2), flush=True)
    raise SystemExit(0 if result.wasSuccessful() else 1)
