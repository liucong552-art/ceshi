#!/usr/bin/env python3
"""Root-only disposable kernel lab. Never modifies the host routing/firewall.

Run explicitly: sudo python3 tests/netns_lab.py
It is NOT discovered as an ordinary unit test.
"""
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import time
import uuid

from helpers import ROOT, heredocs, library


def cmd(*args, check=True, input=None):
    return subprocess.run(args, input=input, text=True, capture_output=True, check=check, timeout=30)


def main():
    if os.geteuid() != 0:
        raise SystemExit('root is required for the disposable network namespace lab')
    prefix = 'hy2lab-' + uuid.uuid4().hex[:8]
    ns = [prefix + '-' + suffix for suffix in ('nat', 'peer', 'wan')]
    nat, peer, wan = ns

    def run(n, *args, **kw):
        return cmd('ip', 'netns', 'exec', n, *args, **kw)

    try:
        for n in ns:
            cmd('ip', 'netns', 'add', n)
            run(n, 'ip', 'link', 'set', 'lo', 'up')
        for a, b, aname, bname, aip, bip in (
            (nat, peer, 'underlay', 'underlay', '192.0.2.2/24', '192.0.2.1/24'),
            (nat, wan, 'wan0', 'internet', '198.51.100.1/24', '198.51.100.2/24'),
        ):
            remote = 'remote0'
            run(a, 'ip', 'link', 'add', aname, 'type', 'veth', 'peer', 'name', remote)
            run(a, 'ip', 'link', 'set', remote, 'netns', b)
            run(b, 'ip', 'link', 'set', remote, 'name', bname)
            for n, name, address in ((a, aname, aip), (b, bname, bip)):
                run(n, 'ip', 'addr', 'add', address, 'dev', name)
                run(n, 'ip', 'link', 'set', name, 'up')
        # A real unprotected fallback exists, so fail-close tests are meaningful.
        run(peer, 'ip', 'route', 'add', 'default', 'via', '192.0.2.2')
        run(wan, 'ip', 'addr', 'add', '169.254.169.254/32', 'dev', 'internet')
        run(nat, 'ip', 'route', 'add', '169.254.169.254/32', 'via', '198.51.100.2')
        with tempfile.TemporaryDirectory(prefix=prefix) as directory:
            root = Path(directory)
            (root / 'wireguard').mkdir()
            keys = {}
            for n, interface, address, port in ((nat, 'wg-exit', '10.66.66.2/24', 44444),
                                                (peer, 'wg-nat', '10.66.66.1/24', 33333)):
                key = cmd('wg', 'genkey').stdout
                keys[n] = cmd('wg', 'pubkey', input=key).stdout.strip()
                keyfile = root / (interface + '.key')
                keyfile.write_text(key)
                keyfile.chmod(0o600)
                run(n, 'ip', 'link', 'add', interface, 'type', 'wireguard')
                run(n, 'wg', 'set', interface, 'private-key', str(keyfile), 'listen-port', str(port))
                run(n, 'ip', 'addr', 'add', address, 'dev', interface)
                run(n, 'ip', 'link', 'set', interface, 'up')
            run(nat, 'wg', 'set', 'wg-exit', 'peer', keys[peer], 'allowed-ips', '10.66.66.1/32',
                'endpoint', '192.0.2.1:33333', 'persistent-keepalive', '1')
            run(peer, 'wg', 'set', 'wg-nat', 'peer', keys[nat], 'allowed-ips', '0.0.0.0/0',
                'endpoint', '192.0.2.2:44444', 'persistent-keepalive', '1')
            run(peer, 'ip', 'route', 'add', '198.51.100.0/24', 'dev', 'wg-nat')
            run(peer, 'ip', 'route', 'add', '169.254.169.254/32', 'dev', 'wg-nat')
            time.sleep(1)
            (root / 'wireguard/wg-exit.env').write_text('WG_ADDR=10.66.66.2/24\nWAN_IF=wan0\n')
            firewall = next(body for _, marker, body in heredocs((ROOT / 'nat.sh').read_text())
                            if marker == 'WG_EXIT_FIREWALL')
            firewall = firewall.replace('/etc/wireguard/', str(root / 'wireguard') + '/')
            firewall = firewall.replace('/run/lock', str(root / 'lock'))

            def repair():
                run(nat, 'bash', '-s', '--', '--manager', 'wg-exit', input=firewall)

            def ping(target):
                return run(peer, 'ping', '-n', '-c', '1', '-W', '1', target, check=False).returncode == 0

            repair()
            assert ping('198.51.100.2'), 'public WAN forwarding failed'
            assert not ping('169.254.169.254'), 'metadata destination escaped isolation'
            assert not ping('10.66.66.2'), 'peer reached NAT host administrative network'
            for _ in range(3):
                repair()
            assert ping('198.51.100.2'), 'repeat repair broke forwarding'
            rules = run(nat, 'iptables-save').stdout
            assert rules.count('-A FORWARD -m comment --comment "vr-wg:wg-exit"') <= 1
            # Destructive only to the disposable namespace.
            run(nat, 'iptables', '-F')
            repair()
            assert not ping('169.254.169.254'), 'firewall recovery lost deny rules'

            # Exercise actual nft quota and dynamic IPv4/IPv6-set builders.
            libraries = library('common.sh') + library('quota-lib.sh') + library('iplimit-lib.sh')
            setup = f'''
HY2_LOCK_DIR={shlex.quote(directory)}
HY2_STATE_DIR={shlex.quote(directory)}
hy2_ensure_runtime_dirs() {{ :; }}
hy2_pq_rebuild_port 40000 1000
hy2_il_rebuild_port 40000 1 120 4
hy2_il_unlock
hy2_il_rebuild_port 40001 1 120 6
'''
            run(nat, 'bash', '-s', input=libraries + setup)
            # A UDP listener is unnecessary: nft sees the inbound packet even
            # if the kernel then sends ICMP port-unreachable.
            packet = "import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.sendto(b'x'*100,('10.66.66.2',40000))"
            # INPUT isolation belongs to the NAT role; remove it only in this
            # namespace when testing the unrelated HY2 quota/IP-limit role.
            run(nat, 'iptables', '-F', 'VRI_WG_EXIT')
            run(peer, 'python3', '-c', packet)
            data = json.loads(run(nat, 'nft', '-j', 'list', 'quota', 'inet', 'hy2_pq', 'hy2_pq_q_40000').stdout)
            quota = next(item['quota'] for item in data['nftables'] if 'quota' in item)
            assert quota.get('used', 0) == 128, quota  # IPv4 + UDP + payload
            # Reverse direction also uses the same quota object.
            run(nat, 'python3', '-c', "import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind(('10.66.66.2',40000));s.sendto(b'y'*100,('10.66.66.1',40002))")
            data = json.loads(run(nat, 'nft', '-j', 'list', 'quota', 'inet', 'hy2_pq', 'hy2_pq_q_40000').stdout)
            quota = next(item['quota'] for item in data['nftables'] if 'quota' in item)
            assert quota.get('used', 0) == 256, quota
            for _ in range(10):
                run(peer, 'python3', '-c', packet)
            before = run(nat, 'nft', '-j', 'list', 'counter', 'inet', 'hy2_pq', 'hy2_pq_in_40000').stdout
            run(peer, 'python3', '-c', packet)
            after = run(nat, 'nft', '-j', 'list', 'counter', 'inet', 'hy2_pq', 'hy2_pq_in_40000').stdout
            assert json.loads(before) == json.loads(after), 'exhausted quota still counted accepted packets'
            (root / 'wireguard/wg-nat.env').write_text('WG_PORT=33333\nMARK=2333\nTABLE_ID=100\nRULE_PRIORITY=31000\nFAILCLOSE_METRIC=42760\nWG_ROUTE_METRIC=10\n')
            guard = next(body for _, marker, body in heredocs((ROOT / 'vpswg.sh').read_text())
                         if marker == 'WG_NAT_GUARD')
            guard = guard.replace('/etc/wireguard/', str(root / 'wireguard') + '/')
            guard = guard.replace('/run/lock', str(root / 'peer-lock')).replace('/var/lib/hy2/temp', str(root / 'temp'))
            run(peer, 'bash', '-s', '--', '--manager', 'wg-nat', input=guard)
            assert run(peer, 'ping', '-m', '2333', '-c', '1', '-W', '2', '198.51.100.2').returncode == 0
            run(peer, 'ip', 'link', 'del', 'wg-nat')
            assert run(peer, 'ping', '-m', '2333', '-c', '1', '-W', '1', '198.51.100.2', check=False).returncode != 0, 'marked traffic leaked after WG deletion'
            # Simultaneous route and firewall removal: device-bound sockets
            # must fail even with a working public fallback route.
            run(peer, 'iptables', '-F')
            run(peer, 'ip', 'rule', 'del', 'priority', '31000')
            bound = "import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.setsockopt(socket.SOL_SOCKET,socket.SO_BINDTODEVICE,b'wg-nat\\0');s.sendto(b'leak',('198.51.100.2',9999))"
            assert run(peer, 'python3', '-c', bound, check=False).returncode != 0, 'missing bind device accepted traffic'
            print('PASS: real WireGuard + NAT forwarding, metadata/host isolation, repeat/flush recovery, nft IPv4/IPv6 syntax, bidirectional quota/exhaustion, WG deletion and device-bound fail-close')
    finally:
        for n in reversed(ns):
            cmd('ip', 'netns', 'del', n, check=False)


if __name__ == '__main__':
    main()
