#!/usr/bin/env python3
"""Real non-WG packet tests in two disposable, disconnected network namespaces.

11.70.0.0/24 is a public-shaped fixture for the real SOCKS ACL. It exists ONLY
inside this disconnected lab; no lab interface is connected to the host.
"""
import argparse
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time

from portbw_lab_traffic import SERVER4, SERVER6, CLIENT4, CLIENT6

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()


def run(args, **kw):
    return subprocess.run(list(map(str, args)), text=True, capture_output=True, check=True, timeout=60, **kw)


def require(value, message):
    if not value: raise RuntimeError(message)


def isolated(fresh=False):
    parent = os.environ.get('HY2_TEST_HOST_NETNS', '')
    require(os.geteuid() == 0 and parent and os.readlink('/proc/self/ns/net') != parent, 'Refusing host-network execution')
    links = {item['ifname'] for item in json.loads(run(['ip', '-j', 'link']).stdout)}
    require(links == {'lo'} if fresh else links <= {'lo', 'pbwl0', 'pbwc0'}, 'Unexpected interface in private lab')


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec); sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


def exact(sock, count):
    out = b''
    while len(out) < count:
        chunk = sock.recv(count - len(out))
        if not chunk: raise RuntimeError('Unexpected EOF')
        out += chunk
    return out


def probe(req):
    isolated()
    family = socket.AF_INET6 if ':' in req['address'] else socket.AF_INET
    udp = req['protocol'] == 'udp'
    with socket.socket(family, socket.SOCK_DGRAM if udp else socket.SOCK_STREAM) as sock:
        sock.settimeout(1.5)
        sock.bind((req.get('source', CLIENT6 if family == socket.AF_INET6 else CLIENT4), 0))
        data = b'onepass-wire-probe' * req.get('repeat', 1)
        if udp:
            sock.sendto(data, (req['address'], req['port']))
            require(sock.recv(65536) == data, 'UDP mismatch'); return
        sock.connect((req['address'], req['port']))
        if req['protocol'] == 'socks':
            user, password = req['user'].encode(), req['password'].encode()
            sock.sendall(b'\x05\x01\x02'); require(exact(sock, 2) == b'\x05\x02', 'SOCKS method denied')
            sock.sendall(b'\x01' + bytes([len(user)]) + user + bytes([len(password)]) + password)
            require(exact(sock, 2) == b'\x01\x00', 'SOCKS credentials rejected')
            dest = req.get('destination', 'echo.test').encode()
            sock.sendall(bytes([5, req.get('command', 1), 0, 3, len(dest)]) + dest + req.get('target_port', 43101).to_bytes(2, 'big'))
            reply = exact(sock, 4); require(reply[:2] == b'\x05\x00', 'SOCKS destination denied')
            count = {1: 4, 4: 16}.get(reply[3])
            if count is None: count = exact(sock, 1)[0]
            exact(sock, count + 2)
        sock.sendall(data)
        require(exact(sock, len(data)) == data, 'TCP mismatch')
        if req.get('hold'):
            print('READY', flush=True); time.sleep(req['hold'])


class TCP(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            while chunk := self.request.recv(65536): self.request.sendall(chunk)
        except OSError: pass


class UDP(socketserver.BaseRequestHandler):
    def handle(self):
        data, sock = self.request; sock.sendto(data, self.client_address)


class DNS(socketserver.BaseRequestHandler):
    def handle(self):
        data, sock = self.request
        pos, labels = 12, []
        while pos < len(data) and data[pos]:
            n = data[pos]; labels.append(data[pos+1:pos+1+n].decode()); pos += n + 1
        pos += 1
        if pos + 4 > len(data): return
        address = '127.0.0.1' if '.'.join(labels) == 'private.test' else CLIENT4
        is_a = data[pos:pos+2] == b'\x00\x01'
        header = data[:2] + b'\x81\x80\x00\x01' + (b'\x00\x01' if is_a else b'\x00\x00') + b'\x00\x00\x00\x00'
        answer = b'\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x05\x00\x04' + socket.inet_aton(address) if is_a else b''
        sock.sendto(header + data[12:pos+4] + answer, self.client_address)


class TCP6(socketserver.ThreadingTCPServer):
    address_family = socket.AF_INET6
    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1); super().server_bind()


class UDP6(socketserver.ThreadingUDPServer):
    address_family = socket.AF_INET6
    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1); super().server_bind()


def start_echo(server):
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class Lab:
    def __init__(self, output):
        isolated(fresh=True)
        self.output = output; output.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='portbw-cpu-all-'))
        self.rows, self.processes, self.streams, self.servers = [], [], [], []
        self.child = subprocess.Popen(['unshare', '--net', '--', sys.executable, str(SELF), 'hold'])
        self.processes.append(self.child)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and os.readlink(f'/proc/{self.child.pid}/ns/net') == os.readlink('/proc/self/ns/net'): time.sleep(.02)
        require(os.readlink(f'/proc/{self.child.pid}/ns/net') != os.readlink('/proc/self/ns/net'), 'Client namespace was not isolated')
        run(['ip', 'link', 'add', 'pbwl0', 'type', 'veth', 'peer', 'name', 'pbwc0'])
        run(['ip', 'link', 'set', 'pbwc0', 'netns', self.child.pid])
        for remote, iface, v4, v6 in ((False, 'pbwl0', SERVER4, SERVER6), (True, 'pbwc0', CLIENT4, CLIENT6)):
            call = self.child_run if remote else run
            call(['ip', 'link', 'set', 'lo', 'up'])
            call(['ip', 'address', 'add', v4 + '/24', 'dev', iface])
            call(['ip', '-6', 'address', 'add', v6 + '/64', 'nodad', 'dev', iface])
            call(['ip', 'link', 'set', iface, 'up'])
        self.child_run(['ip', 'address', 'add', '11.70.0.3/24', 'dev', 'pbwc0'])
        self.child_run(['ip', '-6', 'address', 'add', 'fd42:6879:5::3/64', 'nodad', 'dev', 'pbwc0'])
        # A default route exists only to the disconnected fixture peer, never to the host.
        run(['ip', 'route', 'add', 'default', 'via', CLIENT4, 'dev', 'pbwl0'])
        os.environ.update(PORTBW_LAB_ACK='isolated-netns-only', PORTBW_STATE=str(self.root/'pbw-state'),
                          PORTBW_CONF=str(self.root/'pbw-conf'), PORTBW_LOCK=str(self.root/'pbw-run/lock'),
                          PORTBW_RUN=str(self.root/'pbw-run'), PORTBW_UNITS=str(self.root/'units'),
                          S5_STATE=str(self.root/'s5-state'), S5_CONF=str(self.root/'s5-conf'),
                          S5_RUN=str(self.root/'s5-run'), S5_UNITS=str(self.root/'s5-units'))
        self.pbw = load(ROOT/'portbw.py', 'onepass_pbw')
        self.pbw.write_json(self.pbw.CONF/'config.json', {'iface': 'pbwl0', 'tc_enabled': True})
        self.sem = load(ROOT/'tests/portbw_lab_semantics.py', 'onepass_sem')
        self.s5 = load(ROOT/'socks5.py', 'socks5')
        self.s5.prepare_dirs(); self.s5.atomic_json(self.s5.CONF/'settings.json', {'wan_if': 'pbwl0', 'host': SERVER4})
        self.s5.ensure_tables()

    def child_run(self, command, **kw):
        return run(['nsenter', '-t', self.child.pid, '-n', *command], **kw)

    def client(self, protocol, port, *, ipv6=False, **kw):
        request = dict(protocol=protocol, address=SERVER6 if ipv6 else SERVER4, port=port, **kw)
        got = subprocess.run(['nsenter', '-t', str(self.child.pid), '-n', sys.executable, str(SELF), 'client'],
                             input=json.dumps(request), capture_output=True, text=True, timeout=10)
        return got.returncode == 0

    def case(self, name, action):
        started = time.monotonic(); row = {'case': name, 'status': 'PASS'}
        try: action()
        except Exception as error: row.update(status='FAIL', error=str(error)); raise
        finally:
            row['seconds'] = round(time.monotonic()-started, 3); self.rows.append(row)
            report = {'suite': 'isolated-non-WG-extras', 'cases': self.rows, 'lab_dir': str(self.root),
                      'passed': sum(x['status']=='PASS' for x in self.rows), 'failed': sum(x['status']!='PASS' for x in self.rows)}
            (self.output/'result.json').write_text(json.dumps(report, indent=2))
            print(f"{row['status']}: {name}", flush=True)

    def guide(self, kind, *, port, ips=CLIENT4):
        text = (ROOT/'HY2_GUIDE.md').read_text()
        section = text.split('## 九、', 1)[1] if kind.startswith('pause') else text.split('## 十、', 1)[1]
        blocks = re.findall(r'```bash\n(.*?)\n```', section, re.S)
        block = blocks[1 if kind.endswith('remove') else 0]
        if kind.startswith('pause'): block = block.replace('PORT=40022', f'PORT={port}')
        else:
            block = re.sub(r'^P=40001 IPS="[^"]*"', f'P={port} IPS="{ips}"', block)
            block = block.replace('/etc/systemd/system', str(self.root/'ban-units')).replace('/etc/hy2-ipban.nft', str(self.root/'ban.nft')).replace('/run/hy2-ipban.lock', str(self.root/'ban.lock'))
            (self.root/'ban-units').mkdir(exist_ok=True)
            block = 'systemctl() { return 0; }; export -f systemctl\n' + block
        run(['bash', '-Eeuo', 'pipefail', '-c', block])

    def pauses(self):
        port = 45998
        for cls, host, handler in ((socketserver.ThreadingTCPServer, '0.0.0.0', TCP), (socketserver.ThreadingUDPServer, '0.0.0.0', UDP),
                                   (TCP6, '::', TCP), (UDP6, '::', UDP)):
            self.servers.append(start_echo(cls((host, port), handler)))
        for ipv6 in (False, True):
            for proto in ('tcp', 'udp'): require(self.client(proto, port, ipv6=ipv6), 'Unprotected lab baseline failed')
        self.guide('pause-add', port=port)
        for ipv6 in (False, True):
            for proto in ('tcp', 'udp'): require(not self.client(proto, port, ipv6=ipv6), 'Pause rule did not block external fixture')
        with socket.create_connection(('127.0.0.1', port), timeout=2) as sock:
            sock.sendall(b'local'); require(exact(sock, 5)==b'local', 'Pause blocked intentional loopback exception')
        self.guide('pause-remove', port=port)
        for ipv6 in (False, True):
            for proto in ('tcp', 'udp'): require(self.client(proto, port, ipv6=ipv6), 'Unpause did not restore traffic')
        self.guide('ban-add', port=port)
        for proto in ('tcp', 'udp'):
            require(not self.client(proto, port), 'IPv4 blacklist did not drop fixture')
            require(self.client(proto, port, source='11.70.0.3'), 'Blacklist affected a different source')
            require(self.client(proto, port, ipv6=True), 'IPv4 blacklist affected IPv6')
        run(['nft', 'delete', 'table', 'inet', 'hy2_ipban'])
        run(['nft', '-f', self.root/'ban.nft'])
        require(not self.client('udp', port), 'Saved blacklist did not restore')
        self.guide('ban-remove', port=port)
        require(self.client('udp', port), 'Final unban did not restore traffic')
        require(not (self.root/'ban.nft').exists() and not (self.root/'ban.lock').exists(), 'Final unban left persistence files')

    def socks(self):
        backend_log = (self.root/'backend.log').open('w'); self.streams.append(backend_log)
        backend = subprocess.Popen(['nsenter', '-t', str(self.child.pid), '-n', sys.executable, str(SELF), 'backend'], stdout=backend_log, stderr=subprocess.STDOUT)
        self.processes.append(backend); time.sleep(.4)
        args = self.s5.parser().parse_args(['add', '--id', 'lab', '--seconds', '900', '--port', '45888', '--ip-limit', '1', '--sticky', '3', '--connections', '1', '--quota-gib', '0.0001220703125'])
        n = self.s5.make_node(args); n['phase'] = 'active'
        self.s5.atomic_json(self.s5.nodefile(n['id']), n); self.s5.make_user(n, self.s5.settings())
        cfg = self.s5.proxycfg(n['id'])
        cfg.write_text('nserver ' + CLIENT4 + '\n' + cfg.read_text())
        self.servers.append(start_echo(TCP6(('::', n['port']), TCP)))
        require(self.client('tcp', n['port'], ipv6=True), 'SOCKS IPv6 fixture baseline failed')
        self.s5.apply_ip(n); self.s5.apply_quota(n); require(self.s5.ready(n), 'Real SOCKS nft guard is unhealthy')
        require(not self.client('tcp', n['port'], ipv6=True), 'SOCKS IPv6 family guard did not block real packets')
        log_path = self.root/'3proxy.private.log'; log = log_path.open('w'); self.streams.append(log)
        core = subprocess.Popen([self.s5.BIN, str(cfg)], stdout=log, stderr=subprocess.STDOUT); self.processes.append(core)
        auth = dict(user=n['username'], password=n['password'])
        deadline = time.monotonic()+8
        while time.monotonic()<deadline:
            if self.client('socks', n['port'], **auth): break
            require(core.poll() is None, '3proxy stopped before readiness'); time.sleep(.1)
        else: raise RuntimeError('Actual 3proxy authentication/echo never became ready')
        require(not self.client('socks', n['port'], user=n['username'], password='wrong'), 'Wrong SOCKS password accepted')
        require(not self.client('socks', n['port'], destination='private.test', **auth), 'Private DNS target bypassed SOCKS ACL')
        require(not self.client('socks', n['port'], target_port=25, **auth), 'SMTP destination bypassed SOCKS ACL')
        require(not self.client('socks', n['port'], command=3, **auth), 'SOCKS UDP ASSOC accepted despite policy')
        require(not self.client('socks', n['port'], source='11.70.0.3', **auth), 'SOCKS IP slot overfilled')
        require(self.s5.occupied_ip_slots(n, self.s5.nft_table(self.s5.TABLE_IP))==1, 'SOCKS active slot count is wrong')
        time.sleep(4)
        require(self.client('socks', n['port'], source='11.70.0.3', **auth), 'Idle SOCKS slot never released')
        held = subprocess.Popen(['nsenter', '-t', str(self.child.pid), '-n', sys.executable, str(SELF), 'client'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.processes.append(held)
        held.stdin.write(json.dumps(dict(protocol='socks', address=SERVER4, port=n['port'], source='11.70.0.3', hold=6, **auth))); held.stdin.close()
        import select
        require(select.select([held.stdout], [], [], 5)[0] and held.stdout.readline().strip()=='READY', 'Concurrent SOCKS fixture did not start')
        require(not self.client('socks', n['port'], source='11.70.0.3', **auth), 'SOCKS concurrent connection limit was bypassed')
        held.terminate(); held.wait(timeout=3); time.sleep(.3)
        self.s5.save_quota(n); used = n['quota']['saved']
        require(used>0, 'SOCKS upload/download not metered')
        self.s5.save_quota(n); require(n['quota']['saved']==used, 'SOCKS save double-counted usage')
        require(not self.client('socks', n['port'], source='11.70.0.3', repeat=32768, **auth), 'SOCKS quota did not cut off large transfer')
        self.s5.save_quota(n)
        require(n['quota']['saved']==n['quota']['original'], 'SOCKS failed before quota exhausted')
        self.s5.apply_quota(n)
        require(not self.client('socks', n['port'], source='11.70.0.3', **auth), 'Exhausted SOCKS quota reopened after restore')
        audit = load(ROOT/'socks5-audit.py', 'onepass_s5audit')
        log.flush(); lines=log_path.read_text().splitlines(); now=int(time.time())
        parsed=[audit.parse_record(line, now-3600, now+1) for line in lines]
        require(any(item is not None for item in parsed), 'Actual 3proxy log could not be parsed by access audit')
        require(any('echo.test' in line for line in lines), 'Actual domain access log missing')

    def run(self):
        self.case('pause_blacklist_real_TCP_UDP_IPv4_IPv6_restore_cleanup', self.pauses)
        self.case('SOCKS_real_auth_ACL_IP_TTL_quota_restore_access_logs', self.socks)
        self.pbw.commit(43000, 125000, 125000, self.pbw.cfg())
        self.case('portbw_TCP_UDP_IPv4_IPv6_shared_ingress_cap', lambda: self.sem.aggregate(self.pbw, self.pbw.cfg(), self.child.pid, self.root, 'candidate', 12))
        self.case('portbw_IPv4_IPv6_shared_egress_cap', lambda: self.sem.outbound(self.pbw, self.pbw.cfg(), self.child.pid, self.root, 'candidate', 12))
        self.case('portbw_deleted_rule_foreign_rule_corrupt_sibling', lambda: self.sem.faults(self.pbw, self.pbw.cfg(), 'candidate'))
        self.case('portbw_actual_auto_hold_cooldown_restore', lambda: self.sem.lifecycle(self.pbw, self.pbw.cfg(), self.child.pid, self.root, 'candidate'))

    def close(self):
        for proc in reversed(getattr(self, 'processes', [])):
            if proc.poll() is None:
                proc.terminate()
                try: proc.wait(timeout=3)
                except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=3)
        for stream in getattr(self, 'streams', []): stream.close()
        for server in getattr(self, 'servers', []): server.shutdown(); server.server_close()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('mode', choices=['suite', 'hold', 'client', 'backend'])
    parser.add_argument('--work', type=Path)
    args=parser.parse_args()
    if args.mode=='hold':
        isolated(); time.sleep(1800); return
    if args.mode=='client':
        try: probe(json.load(sys.stdin))
        except (RuntimeError, OSError): raise SystemExit(2)
        return
    if args.mode=='backend':
        isolated(); start_echo(socketserver.ThreadingTCPServer((CLIENT4,43101),TCP)); start_echo(socketserver.ThreadingUDPServer((CLIENT4,53),DNS)); time.sleep(1800); return
    require(args.work is not None, 'Report directory required')
    lab=Lab.__new__(Lab)
    try: lab.__init__(args.work); lab.run()
    finally:
        if lab is not None: lab.close()


if __name__=='__main__': main()
