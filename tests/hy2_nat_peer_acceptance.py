#!/usr/bin/env python3
"""Scoped live per-Peer probe test for the *NAT exit host*, never HY2/production.

Read existing /etc/wireguard/wg-exit-peers.d/*.peer, exercise probe-once for
each existing managed Peer, and check the timer enable/disable round-trip only
for a single pre-disabled Peer. No wg-quick restart, no route or firewall edits.
Probe emitted != confirmed tunnel reconnection.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

IFACE = "wg-exit"
PEERS = Path("/etc/wireguard/wg-exit-peers.d")
NAT_SH = Path("/root/nat.sh")
NAME_RE = re.compile(r"[a-zA-Z0-9._-]{1,64}\Z")


def command(args, timeout=55, *, check=True):
    try:
        result = subprocess.run([str(v) for v in args], capture_output=True,
                                text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"command unavailable or timeout: {args[0]}") from exc
    if check and result.returncode:
        raise RuntimeError(f"command failed: {args[0]} rc={result.returncode}")
    return result


def is_active(unit):
    return command(["systemctl", "is-active", "--quiet", unit],
                   check=False).returncode == 0


def is_enabled(unit):
    return command(["systemctl", "is-enabled", "--quiet", unit],
                   check=False).returncode == 0


def check_host():
    if os.geteuid() != 0:
        raise RuntimeError("root privileges required")
    name = os.uname().nodename.split(".")[0]
    if name in ("helan", "MEGABOX"):
        raise RuntimeError("host guard: run ONLY on the separate WG NAT exit host")
    if not PEERS.is_dir() or not NAT_SH.is_file():
        raise RuntimeError("not a managed HY2 nat.sh installation: no changes")
    if not is_active("wg-quick@" + IFACE + ".service"):
        raise RuntimeError("NAT WireGuard exit interface not active")
    command(["wg", "show", IFACE, "peers"])


def discover_peers():
    names = []
    for f in sorted(PEERS.glob("*.peer")):
        if f.is_symlink() or not f.is_file() or not NAME_RE.fullmatch(f.stem):
            raise RuntimeError("invalid managed Peer file; fail closed")
        names.append(f.stem)
    if len(names) > 10:
        raise RuntimeError("more than 10 peers: explicit review required")
    return names


def unit_for(name):
    if not NAME_RE.fullmatch(name):
        raise RuntimeError("invalid Peer name")
    x = command(["systemd-escape", "--template",
                 f"hy2-wg-{IFACE}-peer-probe@.timer", name]).stdout.strip()
    if not x.startswith(f"hy2-wg-{IFACE}-peer-probe@") or not x.endswith(".timer"):
        raise RuntimeError("unrecognized systemd timer unit")
    return x


def wg_snapshot():
    # These fields expose public keys/endpoints in memory only, never report.
    return {
        what: command(["wg", "show", IFACE, what], timeout=20).stdout
        for what in ("peers", "allowed-ips")
    }


def test_one_peer(name):
    unit = unit_for(name)
    prior_enabled = is_enabled(unit)
    prior_active = is_active(unit)
    before = wg_snapshot()
    output = command(["bash", str(NAT_SH), "probe-once", name], timeout=90)
    if "WG_PROBE_SENT:" not in output.stdout:
        if "WG_PROBE_SKIP:" in output.stdout:
            return {"probe": "SKIP", "timer": "SKIP",
                    "reason": "probe worker reported missing/mismatched runtime peer"}
        raise RuntimeError("probe-once emitted no documented probe marker")
    if wg_snapshot() != before:
        raise RuntimeError("WireGuard Peer/allowed IP/endpoint configuration changed")
    if not is_active("wg-quick@" + IFACE + ".service"):
        raise RuntimeError("WG exit service not active after probe")
    if prior_enabled or prior_active:
        return {"probe": "PASS", "timer": "SKIP",
                "reason": "pre-existing probe timer kept untouched"}
    # Install timer only for existing and previously disabled peer.
    # Revert it in a finally block, even when the activation check fails.
    try:
        command(["bash", str(NAT_SH), "probe-enable", name], timeout=90)
        if not (is_active(unit) and is_enabled(unit)):
            raise RuntimeError("probe timer did not activate")
    finally:
        command(["bash", str(NAT_SH), "probe-disable", name],
                timeout=90, check=False)
    if is_active(unit) or is_enabled(unit):
        raise RuntimeError("probe timer remains enabled after test")
    if wg_snapshot() != before:
        raise RuntimeError("Peer config changed during timer lifecycle")
    return {"probe": "PASS", "timer": "PASS"}


def main():
    check_host()
    names = discover_peers()
    if not names:
        raise RuntimeError("no managed WG Peer; no action")
    rows = {}
    fail = 0
    for name in names:
        try:
            result = test_one_peer(name)
            rows[name] = result
            print(f"PEER_{len(rows)}_PROBE={result['probe']},TIMER={result['timer']}")
        except Exception as exc:
            fail += 1
            rows[name] = {"probe": "FAIL", "timer": "UNKNOWN",
                          "reason": str(exc)}
            print(f"PEER_{len(rows)}_FAIL={exc}", flush=True)
    clean = is_active("wg-quick@" + IFACE + ".service")
    verified = sum(r["probe"] == "PASS" for r in rows.values())
    report = {
        "role": "NAT_EXIT",
        "machine": "separate NAT exit host (name withheld)",
        "result": ("FAIL" if fail or not clean else "PASS" if verified else "SKIP"),
        "peer_count": len(names),
        "live_probe_count": verified,
        "probe_skips": sum(r["probe"] == "SKIP" for r in rows.values()),
        "timer_pass_count": sum(r["timer"] == "PASS" for r in rows.values()),
        "timer_skips": sum(r["timer"] == "SKIP" for r in rows.values()),
        "failed_count": fail,
        "wg_exit_active": clean,
        "peer_results": list(rows.values()),
        "scope": ("one probe packet sent per matched peer; no guarantee of "
                  "handshake/reboot recovery; pre-existing probe timers preserved"),
    }
    dest = Path("/root/hy2-nat-peer-acceptance-report.json")
    dest.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    os.chmod(dest, 0o600)
    print("NAT_PEER_RESULT=" + report["result"])
    print("NAT_PEER_COUNT=" + str(report["peer_count"]))
    print("NAT_PEER_PROBES=" + str(report["live_probe_count"]))
    print("NAT_PEER_TIMERS=" + str(report["timer_pass_count"]))
    print("REPORT_FILE=" + str(dest))
    print("No WireGuard private keys or original endpoints shown.")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError) as exc:
        print("NAT_PEER_TEST_ERROR=" + str(exc), file=sys.stderr)
        sys.exit(2)
