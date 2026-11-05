#!/usr/bin/env python3
"""Read-only release readiness gate for HY2 test VPS helan.

Aggregates previously generated on-host acceptance reports. Does not rerun
destructive tests, change nftables, stop/start units, or contain secrets.
It distinguishes routine functionality from host-dependent and long-horizon
acceptance cases, and must not be taken as proof of v2rayN client behavior.
"""
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys

ROOT = Path("/root")
EXPECTED_MODES = ("live", "maintenance", "edges")
OPTIONAL_MODES = ("recovery",)
TEMP = Path("/var/lib/hy2/temp")
MAIN_DEFAULTS = Path("/etc/default/hy2-main")


def run(*args, timeout=40):
    try:
        obj = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
        return obj.returncode == 0, obj.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False, ""


def check_machine():
    if os.geteuid() != 0:
        raise RuntimeError("root required")
    if os.uname().nodename.split(".")[0] != "helan":
        raise RuntimeError("host guard: only test VPS helan (MEGABOX forbidden)")


def get_env(path, key):
    if not path.is_file():
        return ""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip("'").strip('"')
    except OSError:
        pass
    return ""


def load_reports(root=ROOT):
    """Select most-recent passing report per mode; include skipped dimensions."""
    best = {}
    for item in root.glob("hy2-batch-accept-*/report.json"):
        if item.is_symlink() or not item.is_file():
            continue
        try:
            data = json.loads(item.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        mode = data.get("mode")
        if mode not in EXPECTED_MODES + OPTIONAL_MODES:
            continue
        if not (data.get("machine") == "helan" and
                data.get("overall") == "PASS" and
                data.get("cleanup_ok") is True and
                data.get("fallback_timer_retained") is False):
            continue
        rows = data.get("results")
        if not isinstance(rows, list) or not rows:
            continue
        if any(not isinstance(r, dict) or r.get("result") not in ("PASS", "SKIP")
               for r in rows):
            continue
        passed = sum(r.get("result") == "PASS" for r in rows)
        skipped = sum(r.get("result") == "SKIP" for r in rows)
        try:
            mtime = item.stat().st_mtime
        except OSError:
            continue
        if mode not in best or mtime > best[mode]["_mtime"]:
            best[mode] = {
                "result": "PASS",
                "pass_count": passed,
                "skip_count": skipped,
                "check_names_skipped": [str(r.get("check", "")) for r in rows
                                        if r.get("result") == "SKIP"],
                "_mtime": mtime,
            }
    for mode in best:
        best[mode].pop("_mtime", None)
    return best


def current_services():
    targets = (
        "hy2.service", "wg-quick@wg-nat.service",
        "hy2-manual-guard.service", "hy2-gc.timer",
        "pq-save.timer", "pq-reset.timer",
        "hy2-managed-watchdog.timer",
    )
    values = {}
    for unit in targets:
        values[unit] = run("systemctl", "is-active", "--quiet", unit)[0]
    return values


def current_readonly_audits():
    checks = {
        "hy2_doctor_strict": ("hy2_doctor.sh", "--strict"),
        "hy2_guard": ("hy2-guard", "audit"),
        "pq_audit": ("pq_audit.sh",),
    }
    if shutil.which("portbw"):
        checks["portbw_audit"] = ("portbw", "audit")
    else:
        checks["portbw_audit"] = None
    return {key: (run(*argv)[0] if argv else None)
            for key, argv in checks.items()}


def ipv6_readiness():
    """Only a suitability gate. No IPv6 client or server is touched."""
    domain = get_env(MAIN_DEFAULTS, "HY_IPV6_DOMAIN")
    if not domain:
        return {"ready": False, "reason": "HY_IPV6_DOMAIN not configured"}
    name = domain.removeprefix("[").removesuffix("]")
    if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", name):
        return {"ready": False, "reason": "invalid configured IPv6 domain"}
    ok, addrs = run("ip", "-6", "-o", "addr", "show", "scope", "global")
    if not ok or "inet6" not in addrs:
        return {"ready": False, "reason": "no global IPv6 address"}
    try:
        records = socket.getaddrinfo(name, None, socket.AF_INET6,
                                     socket.SOCK_DGRAM)
    except (socket.gaierror, UnicodeError, OSError):
        records = []
    if not records:
        return {"ready": False, "reason": "IPv6 AAAA resolution unavailable"}
    # Do not claim this confirms that the AAAA record points at this host.
    return {"ready": True,
            "reason": "domain/AAAA/global IPv6 seen; binding and client still untested"}


def other_nodes():
    if not TEMP.is_dir():
        return None
    return sum(1 for p in TEMP.glob("hy2-temp-*.env") if p.is_file())


def gate():
    check_machine()
    reports = load_reports()
    services = current_services()
    audits = current_readonly_audits()
    ipv6 = ipv6_readiness()
    users = other_nodes()
    all_modes = all(m in reports for m in EXPECTED_MODES)
    watchdog_recovery_accepted = "recovery" in reports and reports["recovery"]["skip_count"] == 0
    core_health = all(services.values()) and all(v is True for v in audits.values()
                                                  if v is not None)
    routine = "PASS" if all_modes and core_health else "INCOMPLETE"
    return {
        "machine": "helan",
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "routine_gate": routine,
        "historical_local_batch_reports": reports,
        "current_services": services,
        "current_audits": audits,
        "other_temp_nodes_count": users,
        "ipv6_preflight": ipv6,
        "outstanding": [
            {
                "name": "Watchdog isolated service stop and real recovery",
                "category": "isolated_failure_test",
                "status": "PASS" if watchdog_recovery_accepted else "PENDING",
                "reason": ("real isolated watchdog recovery report exists" if watchdog_recovery_accepted
                           else "healthy check skipped in maintenance; active repair not verified"),
            },
            {
                "name": "Watchdog three failures and 72h grace persistence",
                "category": "long_horizon_or_fault_simulation",
                "status": "SEPARATE_EVIDENCE_REQUIRED",
                "reason": "a single service restart is not a 72-hour grace period test",
            },
            {
                "name": "portbw auto slowdown and recovery under sustained real traffic",
                "category": "external_client_stress",
                "status": "PENDING",
                "reason": "only idle automatic-policy lifecycle tested",
            },
            {
                "name": "NAT VPS peer probe and NAT-side reboot",
                "category": "different_host",
                "status": "PENDING",
                "reason": "must be tested on NAT machine, not helan",
            },
            {
                "name": "IPv6 HY2 client and family isolation",
                "category": "conditional",
                "status": "READY_FOR_ISOLATED_TEST" if ipv6["ready"] else "SKIP_ENVIRONMENT",
                "reason": ipv6["reason"],
            },
            {
                "name": "real 30-day quota roll, certificate renewal, abrupt power loss",
                "category": "long_horizon_or_destructive",
                "status": "SEPARATE_EVIDENCE_REQUIRED",
                "reason": "not safe to simulate as a routine regression",
            },
        ],
        "interpretation": (
            "PASS indicates current health and locally present routine reports only. "
            "It is not a certificate for untested scenarios and does not replace "
            "previous independently observed v2rayN testing."
        ),
    }


def main():
    report = gate()
    out = ROOT / "hy2-release-gate-report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.chmod(out, 0o600)
    print("ROUTINE_GATE=" + report["routine_gate"])
    for mode in EXPECTED_MODES:
        row = report["historical_local_batch_reports"].get(mode)
        if row:
            print(f"{mode.upper()}=PASS pass:{row['pass_count']} skip:{row['skip_count']}")
        else:
            print(f"{mode.upper()}=MISSING_LOCAL_PASS_REPORT")
    wd = report["historical_local_batch_reports"].get("recovery")
    if wd:
        print(f"RECOVERY=PASS pass:{wd['pass_count']} skip:{wd['skip_count']}")
    else:
        print("RECOVERY=PENDING")
    print("OTHER_TEMP_NODES=" + str(report["other_temp_nodes_count"]))
    print("IPV6_PREFLIGHT=" + ("READY" if report["ipv6_preflight"]["ready"] else "SKIP"))
    remaining = [x for x in report["outstanding"] if x["status"] == "PENDING"]
    print(f"OUTSTANDING_ISOLATED_TESTS={len(remaining)} plus IPv6-if-ready and long-horizon")
    print("REPORT_FILE=" + str(out))
    print("READ_ONLY=YES")
    return 0 if report["routine_gate"] == "PASS" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError) as exc:
        print("RELEASE_GATE_ERROR=" + str(exc), file=sys.stderr)
        sys.exit(2)
