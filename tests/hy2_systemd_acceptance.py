#!/usr/bin/env python3
"""Helan-only checks of an installed manager, public TLS and reboot recovery.

The launcher installs the pinned candidate before calling prepare.  verify must
run after a real boot ID change and never repairs services before checking them.
Detailed subprocess output and credentials remain in root-only files.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import socket
import socketserver
import struct
import subprocess
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit

from hy2_loopback import port, receive, wait_for

SOURCE_REF = '670c6fbe8a942796db59bbd9d5bb92606a012c30'
CORE_SHA = '907ba8c9693edb104b20582681fb7dc15639d5b64a9cbb616a7b539190a86691'
LIB = 'source /usr/local/lib/hy2/common.sh\nsource /usr/local/lib/hy2/quota-lib.sh\nsource /usr/local/lib/hy2/iplimit-lib.sh\n'
STATE = Path('/var/lib/hy2')
TIMERS = ('hy2-gc.timer', 'pq-save.timer', 'pq-reset.timer', 'hy2-managed-watchdog.timer')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def metadata(path):
    values = {}
    for line in Path(path).read_text().splitlines():
        key, sep, value = line.partition('=')
        if sep and not key.startswith('#'):
            values.setdefault(key, value)
    return values


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def require_work(work, stage):
    require(os.geteuid() == 0 and platform.node().split('.')[0] == 'helan', 'Helan root required')
    require(work.parent == Path('/root') and work.name.startswith('hy2-systemd-realtest.'), 'Unexpected report directory')
    manifest = json.loads((work / 'manifest.json').read_text())
    require(manifest.get('source_ref') == SOURCE_REF, 'Candidate version mismatch')
    if stage == 'verify':
        baseline = json.loads((work / 'baseline.json').read_text())
        require(baseline['boot_id'] != boot_id(), 'Boot ID unchanged; reboot Helan before verify')
    return manifest


class TCPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            while data := self.request.recv(65536):
                self.request.sendall(data)
        except (ConnectionError, OSError):
            pass


class UDPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        data, sock = self.request
        sock.sendto(data, self.client_address)


class Acceptance:
    def __init__(self, work, stage, manifest):
        self.work, self.stage, self.manifest = work, stage, manifest
        self.rows = []
        self.sequence = 0

    def command(self, args, *, env=None, timeout=90, check=True):
        self.sequence += 1
        with (self.work / 'commands.private.log').open('a') as log:
            log.write('\n' + shlex.join(map(str, args)) + '\n'); log.flush()
            result = subprocess.run(list(map(str, args)), capture_output=True, text=True,
                                    env=dict(os.environ, **(env or {})), timeout=timeout)
            log.write(result.stdout + result.stderr)
        if check and result.returncode:
            raise RuntimeError(f'{Path(args[0]).name} failed ({result.returncode}); see commands.private.log')
        return result

    def shell(self, body):
        return self.command(['bash', '-Eeuo', 'pipefail', '-c', LIB + body]).stdout.strip()

    def cli(self, name, *args, **kwargs):
        return self.command(['/usr/local/sbin/' + name, *map(str, args)], **kwargs)

    def case(self, name, action):
        started = time.monotonic()
        row = {'case': name, 'status': 'PASS'}
        try:
            action()
        except Exception as error:
            row.update(status='FAIL', error=str(error))
            raise
        finally:
            row['seconds'] = round(time.monotonic() - started, 3)
            self.rows.append(row)
            report = {'source_ref': SOURCE_REF, 'stage': self.stage, 'boot_id': boot_id(),
                      'cases': self.rows, 'passed': sum(r['status'] == 'PASS' for r in self.rows),
                      'failed': sum(r['status'] != 'PASS' for r in self.rows),
                      'public_udp_client_tested': False, 'real_wg_nat_tested': False,
                      'actual_acme_renewal_tested': False,
                      'reboot_acceptance_complete': self.stage == 'verify' and len(self.rows) == 7
                      and all(r['status'] == 'PASS' for r in self.rows)}
            (self.work / (self.stage + '-result.json')).write_text(json.dumps(report, indent=2))
            print(f"{row['status']}: {name} ({row['seconds']}s)", flush=True)

    def node(self, kind):
        if kind == 'main':
            meta = metadata(STATE / 'main/main.env')
            return dict(kind=kind, tag='hy2', port=int(meta['MAIN_PORT']), password=meta['MAIN_PASSWORD'],
                        domain=meta['HY_DOMAIN'], unit='hy2.service', expires=None,
                        config='/etc/hysteria/main.yaml', url='/root/hy2_main_url.txt')
        tag = 'hy2-temp-' + self.manifest['run_id'] + '-' + kind
        meta = metadata(STATE / 'temp' / (tag + '.env'))
        return dict(kind=kind, tag=tag, port=int(meta['PORT']), password=meta['PASSWORD'],
                    domain=meta['HY_DOMAIN'], unit=tag + '.service', expires=int(meta['EXPIRE_EPOCH']),
                    config='/etc/hysteria/temp/' + tag + '.yaml', url=str(STATE / 'temp' / (tag + '.url')))

    def quota(self, node):
        fields = self.shell(f"hy2_pq_snapshot {node['port']}").split('|')
        require(len(fields) == 4, 'Invalid quota snapshot')
        return [fields[0], *map(int, fields[1:])]

    def active(self, node):
        return int(self.shell(f"hy2_il_active_count {node['port']}"))

    def units(self):
        for unit in (*TIMERS, 'hy2.service', 'hy2-managed-shutdown-save.service'):
            self.command(['systemctl', 'is-enabled', '--quiet', unit])
            self.command(['systemctl', 'is-active', '--quiet', unit])
        self.command(['systemctl', 'is-enabled', '--quiet', 'hy2-managed-restore.service'])
        for node in self.nodes_if_created():
            self.command(['systemctl', 'is-enabled', '--quiet', node['unit']])
            self.command(['systemctl', 'is-active', '--quiet', node['unit']])
        binary = self.command(['bash', '-c', 'command -v hysteria']).stdout.strip()
        require(digest(binary) == CORE_SHA, 'Installed official core checksum mismatch')

    def nodes_if_created(self):
        return [self.node(k) for k in ('a', 'b')
                if (STATE / 'temp' / ('hy2-temp-' + self.manifest['run_id'] + '-' + k + '.env')).exists()]

    def probe(self, node, *, exhaust=False, blocked=False):
        self.sequence += 1
        stem = self.work / f"client-{self.sequence}-{node['kind']}"
        socks_port, udp_port = port(), port(socket.SOCK_DGRAM)
        tcp = socketserver.ThreadingTCPServer(('127.0.0.1', 0), TCPHandler)
        udp = socketserver.ThreadingUDPServer(('127.0.0.1', 0), UDPHandler)
        tcp.daemon_threads = udp.daemon_threads = True
        for server in (tcp, udp):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        config = {'server': '127.0.0.1:' + str(node['port']), 'auth': node['password'],
                  'tls': {'sni': node['domain']},
                  'socks5': {'listen': '127.0.0.1:' + str(socks_port)},
                  'udpForwarding': [{'listen': '127.0.0.1:' + str(udp_port),
                                     'remote': '127.0.0.1:' + str(udp.server_address[1])}]}
        # No ca override and no insecure: use the OS public trust store.
        cfg = stem.with_suffix('.json'); cfg.write_text(json.dumps(config))
        log_path = stem.with_suffix('.private.log')
        proc = None
        try:
            with log_path.open('w') as log:
                proc = subprocess.Popen(['hysteria', '--log-level', 'debug', 'client', '-c', str(cfg)],
                                        stdout=log, stderr=subprocess.STDOUT,
                                        env=dict(os.environ, HYSTERIA_DISABLE_UPDATE_CHECK='1'))
                if blocked:
                    deadline = time.monotonic() + 7
                    while time.monotonic() < deadline and proc.poll() is None:
                        require('SOCKS5 server listening' not in log_path.read_text(), 'Exhausted node authenticated')
                        time.sleep(0.1)
                    require('SOCKS5 server listening' not in log_path.read_text(), 'Exhausted node authenticated')
                    return
                wait_for(lambda: 'SOCKS5 server listening' in log_path.read_text(), [proc], seconds=12)
                def transfer(data):
                    with socket.create_connection(('127.0.0.1', socks_port), timeout=4) as sock:
                        sock.sendall(b'\x05\x01\x00')
                        require(receive(sock, 2) == b'\x05\x00', 'SOCKS authentication failed')
                        sock.sendall(b'\x05\x01\x00\x01' + socket.inet_aton('127.0.0.1') + struct.pack('!H', tcp.server_address[1]))
                        reply = receive(sock, 4)
                        require(reply[:2] == b'\x05\x00', 'SOCKS connect failed')
                        size = {1: 4, 4: 16}.get(reply[3])
                        if size is None: size = receive(sock, 1)[0]
                        receive(sock, size + 2)
                        sock.sendall(data)
                        require(receive(sock, len(data)) == data, 'TCP data mismatch')
                transfer(b'Helan official TLS and systemd\x00' * (32 if exhaust else 2048))
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.settimeout(4)
                    sock.sendto(b'Helan actual UDP', ('127.0.0.1', udp_port))
                    require(sock.recv(1024) == b'Helan actual UDP', 'UDP data mismatch')
                if exhaust:
                    failed = False
                    try:
                        transfer(b'quota-cutoff\x00' * 262144)
                    except (OSError, RuntimeError):
                        failed = True
                    require(failed, 'Quota exhaustion did not stop actual proxy transfer')
                    require(self.quota(node)[0] == 'exhausted', 'Transfer failed before quota was exhausted')
        finally:
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try: proc.wait(timeout=4)
                except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=3)
            for server in (tcp, udp):
                server.shutdown(); server.server_close()
            cfg.unlink(missing_ok=True)

    def subscriptions(self):
        raw = Path('/root/hy2_temp_subscription.txt').read_text()
        encoded = Path('/root/hy2_temp_subscription_base64.txt').read_text().strip()
        require(base64.b64decode(encoded).decode() == raw, 'Temp base64 subscription mismatch')
        for node in self.nodes_if_created():
            line = Path(node['url']).read_text().strip()
            require(line in raw.splitlines(), 'Active temp missing from subscription')
            parsed = urlsplit(line)
            require(parsed.scheme == 'hy2' and parsed.port == node['port'], 'Invalid share URI')
            require(unquote(parsed.username) == node['password'], 'Share URI credential mismatch')
            require(parse_qs(parsed.query).get('sni') == [node['domain']], 'Share URI SNI mismatch')
            require('insecure' not in parse_qs(parsed.query), 'Unexpected insecure share URI')

    def create(self, kind, gib, seconds=604800):
        self.cli('hy2_mktemp.sh', env={'id': self.manifest['run_id'] + '-' + kind,
                 'IP_VERSION': '4', 'IP_LIMIT': '1', 'IP_STICKY_SECONDS': '600',
                 'PQ_GIB': gib, 'D': str(seconds), 'LANDING': 'local', 'SERVER_ADDR': self.manifest['domain']}, timeout=240)
        node = self.node(kind)
        self.command(['systemctl', 'is-active', '--quiet', node['unit']])
        self.command(['systemctl', 'is-enabled', '--quiet', node['unit']])
        return node

    def prepare(self):
        self.case('01_installed_units_and_first_boot_shutdown_save', self.units)
        self.case('02_public_CA_TLS_main_TCP_UDP', lambda: self.probe(self.node('main')))
        def create_pair():
            self.create('a', '0.0625')
            self.create('b', '0.0001220703125')
            self.subscriptions()
        self.case('03_actual_temp_CLI_and_share_subscriptions', create_pair)
        def protect():
            main = self.node('main')
            self.cli('pq_add.sh', main['port'], '0.0625')
            self.cli('ip_set.sh', main['port'], '1', '600')
            for node in (main, self.node('a')):
                before = self.quota(node)[2]
                self.probe(node)
                require(self.quota(node)[2] > before and self.active(node) == 1, 'Installed protections did not account for traffic')
        self.case('04_main_and_temp_actual_quota_and_IP_accounting', protect)
        self.case('05_systemd_temp_actual_proxy_quota_cutoff', lambda: self.probe(self.node('b'), exhaust=True))
        def watchdog():
            node = self.node('a'); before = self.quota(node)[2]
            self.command(['systemctl', 'start', 'hy2-managed-watchdog.service'], timeout=330)
            require(self.quota(node)[2] >= before and self.active(node) == 1, 'Healthy watchdog lost usage or active IP')
            require(self.quota(self.node('b'))[0] == 'exhausted', 'Watchdog reset exhausted quota')
        self.case('06_actual_watchdog_preserves_active_and_exhausted_nodes', watchdog)
        def reload_hook():
            node = self.node('a'); expires, password = node['expires'], node['password']
            used = self.quota(node)[2]
            self.command(['bash', '/usr/local/lib/hy2/acme-reload.sh'], timeout=300)
            after = self.node('a')
            require(after['expires'] == expires and after['password'] == password and self.quota(after)[2] >= used,
                    'Certificate reload changed credentials, expiry or used quota')
            self.probe(self.node('main')); self.probe(after)
            self.subscriptions()
        self.case('07_actual_certificate_reload_hook_and_transport', reload_hook)
        def restart_main():
            old = self.command(['systemctl', 'show', 'hy2.service', '-p', 'MainPID', '--value']).stdout.strip()
            self.command(['systemctl', 'kill', '--kill-whom=main', '--signal=KILL', 'hy2.service'])
            def ready():
                got = self.command(['systemctl', 'show', 'hy2.service', '-p', 'MainPID', '--value']).stdout.strip()
                return got.isdigit() and int(got) > 0 and got != old
            wait_for(ready, seconds=25)
            self.probe(self.node('main'))
        self.case('08_actual_systemd_failure_restart', restart_main)
        def expiry():
            node = self.create('expiry', '0.0625', seconds=15)
            self.manifest['expiry_tag'] = node['tag']; self.manifest['expiry_port'] = node['port']
            (self.work / 'manifest.json').write_text(json.dumps(self.manifest, indent=2))
            def gone():
                return (not (STATE / 'temp' / (node['tag'] + '.env')).exists()
                        and not Path('/etc/systemd/system/' + node['unit']).exists()
                        and node['tag'] not in Path('/root/hy2_temp_subscription.txt').read_text())
            wait_for(gone, seconds=65)
            require(not (STATE / 'quota' / (str(node['port']) + '.env')).exists(), 'Expired quota metadata remains')
            require(not (STATE / 'iplimit' / (str(node['port']) + '.env')).exists(), 'Expired IP metadata remains')
        self.case('09_actual_timeout_ExecStopPost_expiry_cleanup', expiry)
        def checkpoint():
            self.cli('pq_save_state.sh', timeout=180)
            nodes = {}
            for kind in ('main', 'a', 'b'):
                node = self.node(kind)
                nodes[kind] = {'port': node['port'], 'expires': node['expires'], 'quota': self.quota(node),
                               'config_sha256': digest(node['config']), 'url_sha256': digest(node['url'])}
                require(nodes[kind]['quota'][2] > 0, 'No traffic recorded before reboot')
            require(nodes['b']['quota'][0] == 'exhausted', 'Exhausted test node was reset')
            self.units(); self.subscriptions()
            (self.work / 'baseline.json').write_text(json.dumps({'boot_id': boot_id(), 'nodes': nodes}, indent=2))
        self.case('10_save_checkpoint_for_real_reboot', checkpoint)

    def verify(self):
        baseline = json.loads((self.work / 'baseline.json').read_text())
        def automatic():
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                ready = all(self.command(['systemctl', 'is-active', '--quiet', self.node(k)['unit']], check=False).returncode == 0
                            for k in ('main', 'a', 'b'))
                if ready: break
                time.sleep(1)
            self.units()
            props = self.command(['systemctl', 'show', 'hy2-managed-restore.service',
                                  '-p', 'Result', '-p', 'ExecMainStatus', '-p', 'ExecMainStartTimestampMonotonic']).stdout
            values = dict(line.split('=', 1) for line in props.splitlines() if '=' in line)
            require(values.get('Result') == 'success' and values.get('ExecMainStatus') == '0'
                    and int(values.get('ExecMainStartTimestampMonotonic', '0')) > 0, 'Automatic boot restore did not complete successfully')
        self.case('01_new_boot_automatic_restore_and_services', automatic)
        def quotas():
            for kind, saved in baseline['nodes'].items():
                node = self.node(kind); current = self.quota(node)
                require(current[0] == saved['quota'][0] and current[1] == saved['quota'][1], 'Quota state or limit changed across reboot')
                require(saved['quota'][2] <= current[2] <= min(current[1], saved['quota'][2] + 65536),
                        'Quota reset or unexpected extra charge across reboot')
            require(self.quota(self.node('b')) == ['exhausted', 131072, 131072, 0], 'Exhausted node was reopened after reboot')
        self.case('02_active_and_exhausted_quotas_survive_reboot', quotas)
        def identities():
            for kind, saved in baseline['nodes'].items():
                node = self.node(kind)
                require(node['port'] == saved['port'] and node['expires'] == saved['expires']
                        and digest(node['config']) == saved['config_sha256'] and digest(node['url']) == saved['url_sha256'],
                        'Port, credentials, config or absolute expiry changed across reboot')
            self.subscriptions()
        self.case('03_credentials_absolute_expiry_and_subscriptions_survive', identities)
        def transports():
            for kind in ('main', 'a'):
                node = self.node(kind); before = self.quota(node)[2]
                self.probe(node)
                require(self.quota(node)[2] > before and self.active(node) == 1, 'Restored protections are not accounting for actual traffic')
        self.case('04_post_boot_trusted_TLS_TCP_UDP_with_protections', transports)
        self.case('05_post_boot_exhausted_HY2_still_rejects_auth', lambda: self.probe(self.node('b'), blocked=True))
        def cleanup():
            a, b = self.node('a'), self.node('b')
            self.cli('hy2_cleanup_one.sh', a['tag'], env={'FORCE': '1'})
            require(self.quota(b)[0] == 'exhausted', 'Deleting first node changed second node quota')
            self.cli('hy2_cleanup_one.sh', b['tag'], env={'FORCE': '1'})
            for node in (a, b):
                for area, stem in (('temp', node['tag']), ('quota', str(node['port'])), ('iplimit', str(node['port']))):
                    require(not (STATE / area / (stem + '.env')).exists(), 'Test node metadata remains after cleanup')
                require(not Path('/etc/systemd/system/' + node['unit']).exists(), 'Test node service remains after cleanup')
            require(not Path('/etc/systemd/system/' + self.manifest['expiry_tag'] + '.service').exists(), 'Expired service returned after reboot')
            require(Path('/root/hy2_temp_subscription.txt').read_text().strip() == '', 'Removed nodes remain in subscription')
        self.case('06_cleanup_test_nodes_and_preserve_other_port', cleanup)
        def finish():
            node = self.node('main')
            self.cli('pq_del.sh', node['port'])
            self.cli('ip_del.sh', node['port'])
            self.probe(node)
            self.cli('hy2_doctor.sh', '--strict', timeout=180)
        self.case('07_remove_temporary_main_limits_and_strict_doctor', finish)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['prepare', 'verify'])
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    manifest = require_work(args.work.resolve(), args.stage)
    suite = Acceptance(args.work.resolve(), args.stage, manifest)
    getattr(suite, args.stage)()
    print(f'PASS: {len(suite.rows)} checks; real WG-NAT and external UDP still pending', flush=True)


if __name__ == '__main__':
    main()
