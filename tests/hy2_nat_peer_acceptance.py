#!/usr/bin/env python3
"""Read-only compatibility preflight for shared jieshu / HY2 WireGuard NAT.

The existing jieshu NAT installation is reusable by HY2; this checker is NOT
a requirement to reinstall or re-accept NAT. Default invocation is read-only.
Optional --probe uses a pre-installed per-Peer worker directly to send one
data probe: it never calls nat.sh init/add/enable/disable, installs units, or
changes existing timer states. A sent probe is NOT proof of fresh handshake.
"""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys

IFACE = "wg-exit"
PEERS = Path("/etc/wireguard/wg-exit-peers.d")
NAT_SH = Path("/root/nat.sh")
NAME_RE = re.compile(r"[a-zA-Z0-9._-]{1,64}\Z")


def command(argv, timeout=30, check=True):
    try:
        result = subprocess.run([str(x) for x in argv], capture_output=True,
                                text=True, timeout=timeout, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("command unavailable or timed out") from exc
    if check and result.returncode:
        raise RuntimeError("prerequisite command returned error")
    return result


def is_active(unit):
    return command(["systemctl", "is-active", "--quiet", unit],
                   check=False).returncode == 0


def is_enabled(unit):
    return command(["systemctl", "is-enabled", "--quiet", unit],
                   check=False).returncode == 0


def installed_kind():
    source = NAT_SH.read_text(encoding="utf-8")
    vr = "vr_probe_unit()" in source and "vr_wg_peer_probe.sh" in source
    hy2 = "hy2_probe_unit()" in source and "hy2_wg_peer_probe.sh" in source
    if vr == hy2:
        raise RuntimeError("unable to identify exact installed nat.sh probe namespace")
    return "jieshu" if vr else "hy2"


def check_host():
    if os.geteuid() != 0:
        raise RuntimeError("root required")
    node = os.uname().nodename.split(".")[0]
    if node in ("helan", "MEGABOX"):
        raise RuntimeError("must run ONLY on the separate, shared NAT exit host")
    if not NAT_SH.is_file() or not PEERS.is_dir():
        raise RuntimeError("existing managed NAT installation not found")
    if not is_active("wg-quick@" + IFACE + ".service"):
        raise RuntimeError("wg-exit not active")
    command(["wg", "show", IFACE, "peers"])
    installed_kind()


def discover():
    names = []
    for path in sorted(PEERS.glob("*.peer")):
        if path.is_symlink() or not path.is_file() or not NAME_RE.fullmatch(path.stem):
            raise RuntimeError("invalid managed Peer filename")
        names.append(path.stem)
    if not names:
        raise RuntimeError("no managed peers")
    if len(names) > 30:
        raise RuntimeError("too many peers; review before enumeration")
    return names


def unit_for(name, kind):
    if not NAME_RE.fullmatch(name):
        raise RuntimeError("invalid peer name")
    prefix = "vr" if kind == "jieshu" else "hy2"
    unit = command(["systemd-escape", "--template",
                    f"{prefix}-wg-{IFACE}-peer-probe@.timer", name]).stdout.strip()
    if not (unit.startswith(f"{prefix}-wg-{IFACE}-peer-probe@")
            and unit.endswith(".timer")):
        raise RuntimeError("unexpected probe timer")
    return unit


def read_peer(path):
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("invalid peer file")
    data = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        for key in ("PublicKey", "AllowedIPs"):
            if line.strip().startswith(key + " "):
                k, _, v = line.partition("=")
                if k.strip() == key:
                    if key in data:
                        raise RuntimeError("duplicate peer attribute")
                    data[key] = v.strip()
    pub = data.get("PublicKey", "")
    addr = data.get("AllowedIPs", "")
    if not re.fullmatch(r"[A-Za-z0-9+/]{43}=", pub):
        raise RuntimeError("invalid peer public key")
    try:
        network = ipaddress.ip_network(addr, strict=True)
    except ValueError as exc:
        raise RuntimeError("invalid AllowedIPs") from exc
    if network.version != 4 or network.prefixlen != 32:
        raise RuntimeError("expected single IPv4 /32 per managed Peer")
    return pub, addr


def runtime_allowed_ips():
    lines = command(["wg", "show", IFACE, "allowed-ips"]).stdout.splitlines()
    table = {}
    for line in lines:
        split = line.split()
        if len(split) >= 2:
            table[split[0]] = split[1:]
    return table


def wg_snapshot():
    return tuple(command(["wg", "show", IFACE, x]).stdout
                 for x in ("peers", "allowed-ips"))


def check_one(name, kind, do_probe=False):
    pub, cidr = read_peer(PEERS / (name + ".peer"))
    allowed = runtime_allowed_ips()
    if allowed.get(pub) != [cidr]:
        raise RuntimeError("on-disk Peer / runtime WG AllowedIPs mismatch")
    unit = unit_for(name, kind)
    before_timer = (is_active(unit), is_enabled(unit))
    before = wg_snapshot()
    probe = "NOT_RUN"
    if do_probe:
        helper = Path("/usr/local/sbin/" + (
            "vr_wg_peer_probe.sh" if kind == "jieshu" else "hy2_wg_peer_probe.sh"))
        if not helper.is_file():
            probe = "SKIP_HELPER_MISSING"
        else:
            # Run existing worker directly, never nat.sh: nat.sh probe-once
            # rewrites shared unit files, and probe-enable may affect timers.
            result = command([str(helper), IFACE, name], timeout=25)
            if "WG_PROBE_SENT:" in result.stdout:
                probe = "SENT_NOT_HANDSHAKE_PROOF"
            elif "WG_PROBE_SKIP:" in result.stdout:
                probe = "SKIP_WORKER"
            else:
                raise RuntimeError("existing probe worker reported neither sent nor skip")
    if wg_snapshot() != before:
        raise RuntimeError("WG public peer/allowed-ips changed during check")
    if (is_active(unit), is_enabled(unit)) != before_timer:
        raise RuntimeError("existing Peer probe timer changed during check")
    if not is_active("wg-quick@" + IFACE + ".service"):
        raise RuntimeError("wg-exit inactive after check")
    return {"configuration": "PASS", "timer": "UNCHANGED",
            "timer_previously_enabled": before_timer[1], "probe": probe}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Shared jieshu NAT read-only compatibility audit")
    parser.add_argument("--probe", action="store_true",
                        help="opt-in one data probe via existing helper; never alter timers")
    args = parser.parse_args(argv)
    check_host()
    kind = installed_kind()
    results = []
    errors = 0
    for name in discover():
        try:
            result = check_one(name, kind, args.probe)
            results.append(result)
        except Exception as exc:
            errors += 1
            results.append({"configuration": "FAIL", "reason": str(exc)})
    report = {
        "mode": "optional-existing-worker-probe" if args.probe else "read-only",
        "shared_nat_implementation": kind,
        "peer_count": len(results),
        "configuration_pass": sum(x["configuration"] == "PASS" for x in results),
        "failure_count": errors,
        "probe_sent_count": sum(x.get("probe") == "SENT_NOT_HANDSHAKE_PROOF"
                                for x in results),
        "result": "PASS" if not errors else "FAIL",
        "interpretation": (
            "No change to installed NAT or probe timers. Reuses existing WG "
            "forwarding irrespective of HY2/VLESS encapsulated traffic. "
            "This check is optional if jieshu NAT has already been accepted."
        ),
    }
    output = Path("/root/hy2-nat-shared-compat-report.json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    os.chmod(output, 0o600)
    print("NAT_SHARED_COMPAT=" + report["result"])
    print("NAT_INSTALLED_IMPLEMENTATION=" + kind)
    print("NAT_PEERS_CHECKED=" + str(len(results)))
    print("NAT_PROBES_SENT=" + str(report["probe_sent_count"]))
    print("NAT_CHANGES=NONE")
    print("REPORT_FILE=" + str(output))
    return 0 if not errors else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError) as exc:
        print("NAT_COMPAT_ERROR=" + str(exc), file=sys.stderr)
        sys.exit(2)
