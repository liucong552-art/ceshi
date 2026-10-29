#!/usr/bin/env python3
"""Paced echo traffic, restricted to the onepass disposable network lab."""
import argparse
import json
import os
import socket
import threading
import time

SERVER4, CLIENT4 = '11.70.0.1', '11.70.0.2'
SERVER6, CLIENT6 = 'fd42:6879:5::1', 'fd42:6879:5::2'


def guard():
    expected = os.environ.get('HY2_TEST_HOST_NETNS', '')
    if (not expected or os.readlink('/proc/self/ns/net') == expected
            or os.environ.get('PORTBW_LAB_ACK') != 'isolated-netns-only'):
        raise RuntimeError('Refusing traffic outside the isolated lab')


def paced_source(sock, target, seconds, mbit, udp):
    size = 1200 if udp else 4096
    data = b'x' * size
    start, sent = time.monotonic(), 0
    deadline = start + seconds
    while time.monotonic() < deadline:
        try:
            count = sock.sendto(data, target) if udp else sock.send(data)
            sent += count
        except socket.timeout:
            continue
        except (ConnectionError, OSError):
            break
        delay = start + sent * 8 / (mbit * 1e6) - time.monotonic()
        if delay > 0: time.sleep(min(delay, max(0, deadline - time.monotonic())))
    return sent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('role', choices=['server', 'client'])
    parser.add_argument('--proto', choices=['tcp', 'udp'], required=True)
    parser.add_argument('--family', choices=['ipv4', 'ipv6'], required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--seconds', type=float, required=True)
    parser.add_argument('--mbit', type=float, default=3)
    args = parser.parse_args(); guard()
    family = socket.AF_INET6 if args.family == 'ipv6' else socket.AF_INET
    server = SERVER6 if family == socket.AF_INET6 else SERVER4
    client = CLIENT6 if family == socket.AF_INET6 else CLIENT4
    udp = args.proto == 'udp'
    with socket.socket(family, socket.SOCK_DGRAM if udp else socket.SOCK_STREAM) as sock:
        if family == socket.AF_INET6: sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        sock.settimeout(.25)
        if args.role == 'server':
            sock.bind((server, args.port))
            deadline, received = time.monotonic() + args.seconds, 0
            if udp:
                while time.monotonic() < deadline:
                    try:
                        data, peer = sock.recvfrom(65536); received += len(data); sock.sendto(data, peer)
                    except socket.timeout: continue
            else:
                sock.listen(2)
                conn = None
                while conn is None and time.monotonic() < deadline:
                    try: conn, _ = sock.accept()
                    except socket.timeout: pass
                if conn is None: raise RuntimeError('TCP lab client never connected')
                with conn:
                    conn.settimeout(.25)
                    while time.monotonic() < deadline:
                        try:
                            data = conn.recv(65536)
                            if not data: break
                            received += len(data); conn.sendall(data)
                        except socket.timeout: continue
                        except ConnectionError: break
            print(json.dumps({'rx_bytes': received}), flush=True)
        else:
            sock.bind((client, 0))
            if not udp: sock.connect((server, args.port))
            stop = threading.Event()
            def drain():
                while not stop.is_set():
                    try:
                        data = sock.recv(65536)
                        if not data: return
                    except socket.timeout: pass
                    except OSError: return
            reader = threading.Thread(target=drain, daemon=True); reader.start()
            sent = paced_source(sock, (server, args.port), args.seconds, args.mbit, udp)
            stop.set(); reader.join(.4)
            print(json.dumps({'tx_bytes': sent}), flush=True)


if __name__ == '__main__': main()
