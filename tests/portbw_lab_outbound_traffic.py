#!/usr/bin/env python3
"""One-way UDP egress measurement for the private onepass network lab."""
import argparse
import json
import socket
import time
from portbw_lab_traffic import SERVER4, SERVER6, CLIENT4, CLIENT6, guard, paced_source


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('role', choices=['source', 'sink'])
    parser.add_argument('--family', choices=['ipv4', 'ipv6'], required=True)
    parser.add_argument('--source-port', type=int, required=True)
    parser.add_argument('--seconds', type=float, required=True)
    parser.add_argument('--mbit', type=float, required=True)
    args = parser.parse_args(); guard()
    family = socket.AF_INET6 if args.family == 'ipv6' else socket.AF_INET
    server, client = (SERVER6, CLIENT6) if family == socket.AF_INET6 else (SERVER4, CLIENT4)
    with socket.socket(family, socket.SOCK_DGRAM) as sock:
        if family == socket.AF_INET6: sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        sock.settimeout(.2)
        if args.role == 'source':
            sock.bind((server, args.source_port))
            count = paced_source(sock, (client, args.source_port + 1), args.seconds, args.mbit, True)
        else:
            sock.bind((client, args.source_port + 1)); count = 0
            deadline = time.monotonic() + args.seconds
            while time.monotonic() < deadline:
                try: count += len(sock.recv(65536))
                except socket.timeout: pass
    print('OUTBOUND_TRAFFIC=' + json.dumps({'bytes': count}), flush=True)


if __name__ == '__main__': main()
