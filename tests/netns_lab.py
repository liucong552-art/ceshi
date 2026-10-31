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
            (nat, peer, 'wg-exit', 'tun', '10.66.66.2/24', '10.66.66.1/24'),
            (nat, wan, 'wan0', 'internet', '198.51.100.1/24', '198.51.100.2/24'),
        ):
            run(a, 'ip', 'link', 'add', aname, 'type', 'veth', 'peer', 'name', bname)
            run(a, 'ip', 'link', 'set', bname, 'netns', b)
            for n, name, address in ((a, aname, aip), (b, bname, bip)):
                run(n, 'ip', 'addr', 'add', address, 'dev', name)
                run(n, 'ip', 'link', 'set', name, 'up')
        run(peer, 'ip', 'route', 'add', 'default', 'via', '10.66.66.2')
        run(wan, 'ip', 'addr', 'add', '169.254.169.254/32', 'dev', 'internet')
        run(nat, 'ip', 'route', 'add', '169.254.169.254/32', 'via', '198.51.100.2')
        with tempfile.TemporaryDirectory(prefix=prefix) as directory:
            root = Path(directory)
            (root / 'wireguard').mkdir()
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
            print('PASS: NAT public forwarding, metadata/host isolation, repeat/flush recovery, real nft IPv4/IPv6 syntax, shared bidirectional quota and exhaustion')
    finally:
        for n in reversed(ns):
            cmd('ip', 'netns', 'del', n, check=False)


if __name__ == '__main__':
    main()
