#!/usr/bin/env python3
"""Exercise generated server configs with an official HY2 binary on loopback.

No systemd installation, firewall changes, public listener, or WG changes.
Usage: python3 tests/hy2_loopback.py /path/to/hysteria
"""
import hashlib
import argparse
import json
import os
from pathlib import Path
import shlex
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time

from hy2_test_support import bash, payload


def wait_for(predicate, processes=(), seconds=8):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        if any(proc.poll() is not None for proc in processes):
            raise RuntimeError('HY2 exited before becoming ready')
        time.sleep(0.05)
    raise RuntimeError('loopback readiness timeout')


def port(kind=socket.SOCK_STREAM):
    with socket.socket(socket.AF_INET, kind) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def receive(sock, length):
    data = b''
    while len(data) < length:
        piece = sock.recv(length - len(data))
        if not piece:
            raise RuntimeError('unexpected EOF')
        data += piece
    return data


class TCPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        while True:
            data = self.request.recv(65536)
            if not data:
                return
            self.request.sendall(data)


class UDPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        data, sock = self.request
        sock.sendto(data, self.client_address)


def run(binary, bind_device=False):
    results = []
    with tempfile.TemporaryDirectory(prefix='hy2-loopback-') as temp:
        root = Path(temp)
        cert, key = root / 'cert.pem', root / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-subj', '/CN=hy2.test', '-addext', 'subjectAltName=DNS:hy2.test',
                        '-keyout', str(key), '-out', str(cert)], check=True, capture_output=True)
        tcp = socketserver.ThreadingTCPServer(('127.0.0.1', 0), TCPHandler)
        udp = socketserver.ThreadingUDPServer(('127.0.0.1', 0), UDPHandler)
        tcp.daemon_threads = udp.daemon_threads = True
        for server in (tcp, udp):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        processes, logs = [], []

        def start(config, mode, name):
            log = (root / (name + '.log')).open('w+')
            logs.append(log)
            proc = subprocess.Popen([str(binary), '--log-level', 'debug', mode, '-c', str(config)],
                                    stdout=log, stderr=subprocess.STDOUT,
                                    env=dict(os.environ, HYSTERIA_DISABLE_UPDATE_CHECK='1'))
            processes.append(proc)
            return proc, Path(log.name)

        try:
            modes = [('plain', False, ''), ('salamander', True, '')]
            if bind_device:
                modes.append(('bind-device', False, 'lo'))
            for name, obfs, device in modes:
                server_port, socks_port, forward_port = port(socket.SOCK_DGRAM), port(), port(socket.SOCK_DGRAM)
                config = root / (name + '-server.yaml')
                password = 'b' * 32
                args = [str(config), '127.0.0.1:' + str(server_port), password,
                        str(cert), str(key), 'https://www.apple.com/', '1' if obfs else '0',
                        'test-obfs-password', device, '4']
                bash(payload('common.sh') + '\nhy2_write_server_cfg ' + ' '.join(map(shlex.quote, args)))
                server, server_log = start(config, 'server', name + '-server')
                wait_for(lambda: 'server up and running' in server_log.read_text(), [server])
                client_cfg = {
                    'server': '127.0.0.1:' + str(server_port), 'auth': password,
                    'tls': {'sni': 'hy2.test', 'ca': str(cert)},
                    'socks5': {'listen': '127.0.0.1:' + str(socks_port)},
                    'udpForwarding': [{'listen': '127.0.0.1:' + str(forward_port),
                                       'remote': '127.0.0.1:' + str(udp.server_address[1])}],
                }
                if obfs:
                    client_cfg['obfs'] = {'type': 'salamander', 'salamander': {'password': 'test-obfs-password'}}
                client_path = root / (name + '-client.json')
                client_path.write_text(json.dumps(client_cfg))
                client, client_log = start(client_path, 'client', name + '-client')
                wait_for(lambda: 'SOCKS5 server listening' in client_log.read_text(), [server, client])
                with socket.create_connection(('127.0.0.1', socks_port), timeout=4) as sock:
                    sock.sendall(b'\x05\x01\x00')
                    assert receive(sock, 2) == b'\x05\x00'
                    sock.sendall(b'\x05\x01\x00\x01' + socket.inet_aton('127.0.0.1') + struct.pack('!H', tcp.server_address[1]))
                    reply = receive(sock, 4)
                    assert reply[:2] == b'\x05\x00', reply
                    size = {1: 4, 4: 16}.get(reply[3])
                    if size is None:
                        size = receive(sock, 1)[0]
                    receive(sock, size + 2)
                    message = b'HY2 generated config TCP echo\x00' * 4096
                    sock.sendall(message)
                    assert receive(sock, len(message)) == message
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.settimeout(4)
                    message = b'HY2 UDP echo'
                    sock.sendto(message, ('127.0.0.1', forward_port))
                    assert sock.recv(4096) == message
                wait_for(lambda: all(s in server_log.read_text() for s in ('TCP request', 'UDP request')), [server, client])
                results.append({'mode': name, 'tls_verification': 'PASS', 'tcp': 'PASS', 'udp': 'PASS', 'access_logs': 'PASS'})
                client.terminate(); client.wait(timeout=5)

                bad_cfg = dict(client_cfg, auth='wrong-password')
                bad_path = root / (name + '-bad.json')
                bad_path.write_text(json.dumps(bad_cfg))
                bad, bad_log = start(bad_path, 'client', name + '-bad')
                wait_for(lambda: bad.poll() is not None)
                assert bad.returncode != 0 and 'auth' in bad_log.read_text().lower(), bad_log.read_text()
                results[-1]['invalid_auth_rejected'] = 'PASS'
                server.terminate(); server.wait(timeout=5)
        except Exception:
            for log in logs:
                log.flush()
                print('\n' + log.name + '\n' + Path(log.name).read_text()[-6000:], file=sys.stderr)
            raise
        finally:
            for proc in processes:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill(); proc.wait(timeout=3)
            for log in logs:
                log.close()
            for server in (tcp, udp):
                server.shutdown(); server.server_close()
    print(json.dumps({'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(), 'results': results}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path)
    parser.add_argument('--bind-device', action='store_true', help='Also test bindDevice=lo; needs netlink/socket privileges')
    args = parser.parse_args()
    run(args.binary.resolve(), args.bind_device)
