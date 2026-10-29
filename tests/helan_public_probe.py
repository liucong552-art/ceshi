#!/usr/bin/env python3
"""Read-only SSH export for the Windows public-client test.

Stdout contains a credential descriptor and must be captured privately by SSH,
never pasted into chat. No service, account, firewall, or route is changed.
"""
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import socket
import subprocess
import sys


def metadata(path):
    result = {}
    for line in Path(path).read_text().splitlines():
        key, separator, value = line.partition('=')
        if separator and re.fullmatch(r'[A-Z][A-Z0-9_]*', key):
            result.setdefault(key, value)
    return result


def descriptor(meta, password, addresses):
    domain, port = meta.get('HY_DOMAIN', ''), meta.get('MAIN_PORT', '')
    if (len(domain) > 253 or '.' not in domain or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                                                   for label in domain.split('.'))):
        raise RuntimeError('Invalid installed domain')
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise RuntimeError('Invalid installed port')
    if not password or password != meta.get('MAIN_PASSWORD'):
        raise RuntimeError('Installed credentials are missing or inconsistent')
    addresses = sorted(set(addresses))
    if len(addresses) != 1 or not ipaddress.IPv4Address(addresses[0]).is_global:
        raise RuntimeError('This acceptance requires exactly one public A address')
    config = {'server': domain + ':' + port, 'auth': password, 'tls': {'sni': domain}}
    if meta.get('ENABLE_SALAMANDER', '0') == '1':
        obfs = meta.get('SALAMANDER_PASSWORD', '')
        if not obfs: raise RuntimeError('Missing configured Salamander password')
        config['obfs'] = {'type': 'salamander', 'salamander': {'password': obfs}}
    return {'schema': 1, 'manager_version': meta.get('MANAGER_VERSION'),
            'expected_exit_ip': addresses[0], 'client': config}


def main():
    if os.geteuid() != 0 or platform.node().split('.')[0] != 'helan':
        raise RuntimeError('Read-only export is restricted to Helan root')
    subprocess.run(['systemctl', 'is-active', '--quiet', 'hy2.service'], check=True)
    meta = metadata('/var/lib/hy2/main/main.env')
    password = Path('/var/lib/hy2/main/main.password').read_text().strip()
    addresses = [item[4][0] for item in socket.getaddrinfo(meta['HY_DOMAIN'], None, socket.AF_INET, socket.SOCK_DGRAM)]
    print(json.dumps(descriptor(meta, password, addresses)))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Never include the credential descriptor in diagnostics.
        print('Public test export failed: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
