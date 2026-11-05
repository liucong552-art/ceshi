#!/usr/bin/env python3
"""HY2 one-shot VPS acceptance: health + isolated guard/quota/traffic live tests.

Run only on the explicitly designated helan test VPS as root.
No SSH access, reboots, WG restarts or real-client bans are performed.
Every live run creates its own randomly named HY2-NAT test node and a
reboot-persistent, one-shot 30-minute safety cleanup timer.
"""
import argparse
import datetime as dt
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time

STATE = Path("/var/lib/hy2")
UNIT_DIR = Path("/etc/systemd/system")
ROOT = Path("/root")
NETS = ("198.51.100.7/32", "203.0.113.0/24")
TAG_PATTERN = re.compile(r"^hy2-temp-batch-[0-9a-f]{8}$")
PORT_PATTERN = re.compile(r"^[0-9]{1,5}$")
RUNNER = "runner.py"


class TestFailure(Exception):
    pass


def valid_tag(tag):
    return bool(TAG_PATTERN.fullmatch(tag))


def meta(path):
    data = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            data[k] = v.strip().strip('"').strip("'")
    return data


def valid_port(value):
    return bool(PORT_PATTERN.fullmatch(str(value))) and 1024 <= int(value) <= 65535


def quota_anchor(before, after, new_bytes, now):
    fields = ("NEXT_RESET_EPOCH", "RESET_INTERVAL_SECONDS")
    return (
        before.get("RESET_INTERVAL_SECONDS") == "2592000"
        and int(before.get("NEXT_RESET_EPOCH", "0")) > now
        and all(before.get(k) == after.get(k) for k in fields)
        and after.get("ORIGINAL_LIMIT_BYTES") == str(new_bytes)
        and after.get("SAVED_USED_BYTES") == "0"
        and after.get("REBUILD_PENDING") == "0"
    )


def check_machine():
    if os.geteuid() != 0:
        raise TestFailure("only root may run this test")
    if os.uname().nodename.split(".")[0] != "helan":
        raise TestFailure("HOST_GUARD: only helan is authorized; no changes made")


def command(args, timeout=90, env=None, logfile=None, check=True):
    try:
        result = subprocess.run(
            [str(x) for x in args],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise TestFailure(f"command not available / timed out: {args[0]}") from exc
    if logfile:
        Path(logfile).write_text(result.stdout, encoding="utf-8")
        os.chmod(logfile, 0o600)
    if check and result.returncode != 0:
        raise TestFailure(f"command failed ({result.returncode}): {args[0]}")
    return result


def active(unit):
    return command(["systemctl", "is-active", "--quiet", unit], check=False).returncode == 0


def run_systemctl(*args):
    return command(["systemctl", *args], timeout=45)


def meta_for_test(tag):
    if not valid_tag(tag):
        raise TestFailure("refusing invalid tag")
    path = STATE / "temp" / (tag + ".env")
    if not path.is_file():
        return None
    fields = meta(path)
    if fields.get("TAG") != tag or not valid_port(fields.get("PORT", "")):
        raise TestFailure("test node identity/port mismatch; leave for manual review")
    if fields.get("LANDING") != "nat":
        raise TestFailure("test node is not NAT; leave for manual review")
    return fields


def unit_names(tag):
    if not valid_tag(tag):
        raise TestFailure("invalid unit tag")
    suffix = tag.removeprefix("hy2-temp-batch-")
    return "hy2-batch-safety-" + suffix


def setup_safety_timer(tag, workdir):
    """Install an ONCE calendar timer, persisted across VPS restart."""
    unit = unit_names(tag)
    service = UNIT_DIR / (unit + ".service")
    timer = UNIT_DIR / (unit + ".timer")
    runner = workdir / RUNNER
    shutil.copyfile(Path(__file__), runner)
    os.chmod(runner, 0o700)
    stamp = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=30))
    when = stamp.strftime("%Y-%m-%d %H:%M:%S UTC")
    service.write_text(
        "[Unit]\nDescription=Fail-safe HY2 batch-test node cleanup\n"
        "[Service]\nType=oneshot\nTimeoutStartSec=180\n"
        f"ExecStart=/usr/bin/python3 {runner} --cleanup {tag} "
        f"--logdir {workdir}\n",
        encoding="utf-8",
    )
    timer.write_text(
        "[Unit]\nDescription=Reboot-persistent HY2 batch-test cleanup\n"
        f"[Timer]\nOnCalendar={when}\nPersistent=true\n"
        f"Unit={unit}.service\n"
        "[Install]\nWantedBy=timers.target\n",
        encoding="utf-8",
    )
    os.chmod(service, 0o644)
    os.chmod(timer, 0o644)
    try:
        run_systemctl("daemon-reload")
        run_systemctl("enable", "--now", unit + ".timer")
        if not active(unit + ".timer"):
            raise TestFailure("fail-safe timer not active")
    except Exception:
        # No test node has been created yet. Roll back incomplete timer
        # installation, so an activation failure doesn't leave stale units.
        command(["systemctl", "disable", "--now", unit + ".timer"],
                timeout=45, check=False)
        service.unlink(missing_ok=True)
        timer.unlink(missing_ok=True)
        command(["systemctl", "daemon-reload"], timeout=45, check=False)
        raise
    return unit


def remove_timer(tag):
    unit = unit_names(tag)
    command(["systemctl", "disable", "--now", unit + ".timer"], timeout=45, check=False)
    for suffix in (".timer", ".service"):
        (UNIT_DIR / (unit + suffix)).unlink(missing_ok=True)
    run_systemctl("daemon-reload")



def cleanup_portbw_marker(tag, workdir):
    """Undo only the portbw policy created by this run, including fallback boot."""
    marker = workdir / "portbw-owned.json"
    if not marker.is_file():
        return
    try:
        record = json.loads(marker.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise TestFailure("corrupt portbw ownership marker") from exc
    port = record.get("port")
    if record.get("tag") != tag or not valid_port(port) or int(port) == 443:
        raise TestFailure("portbw marker does not match isolated HY2 test tag")
    # If port was reused by another node, refuse to delete an unrelated policy.
    for tmp in (STATE / "temp").glob("hy2-temp-*.env"):
        row = meta(tmp)
        if row.get("PORT") == str(port) and row.get("TAG") != tag:
            raise TestFailure("portbw port reused by a different HY2 node")
    fields = meta_for_test(tag)
    if fields and fields["PORT"] != str(port):
        raise TestFailure("portbw marker and node port mismatch")
    if not shutil.which("portbw"):
        raise TestFailure("portbw CLI is missing; cannot remove test rule")
    command(["portbw", "del", str(port)], timeout=90)
    if (Path("/var/lib/portbw/ports") / (str(port) + ".json")).exists():
        raise TestFailure("portbw config still exists after del")
    marker.unlink()


def cleanup_tag(tag, logdir=None):
    """No effect on unrelated nodes: strict name, metadata and owner checks."""
    check_machine()
    errors = []
    if logdir is not None:
        try:
            cleanup_portbw_marker(tag, Path(logdir))
        except TestFailure as exc:
            errors.append(str(exc))
    fields = meta_for_test(tag)
    if fields is None:
        if active(tag + ".service"):
            errors.append("metadata absent but test service still active")
        if errors:
            raise TestFailure("; ".join(errors))
        return
    port = fields["PORT"]
    q = STATE / "quota" / (port + ".env")
    if q.is_file():
        qmeta = meta(q)
        if qmeta.get("OWNER_KIND") != "temp" or qmeta.get("OWNER_TAG") != tag:
            raise TestFailure("quota ownership mismatch: unsafe to cleanup")
    # Remove ONLY our documentation-only CIDRs on our own unique port.
    # All commands are idempotent, including when a test step was skipped.
    for cidr in NETS:
        try:
            command(["hy2-guard", "unban", port, cidr], timeout=30)
        except TestFailure as exc:
            errors.append(str(exc))
    try:
        command(["hy2-guard", "resume", port], timeout=30)
    except TestFailure as exc:
        errors.append(str(exc))
    try:
        command(
            ["/usr/local/sbin/hy2_cleanup_one.sh", tag],
            env={**os.environ, "FORCE": "1"},
            timeout=140,
            logfile=(Path(logdir) / "cleanup.raw.log") if logdir else None,
        )
    except TestFailure as exc:
        errors.append(str(exc))
    if meta_for_test(tag) is not None or active(tag + ".service"):
        errors.append("test node remains after cleanup")
    if errors:
        raise TestFailure("; ".join(errors))


def check_preflight(workdir):
    check_machine()
    required_bins = (
        "hy2_mktemp_nat.sh", "hy2-guard", "hy2_traffic",
        "pq_audit.sh", "hy2_doctor.sh", "nft", "systemctl",
        "bash", "python3",
    )
    for name in required_bins:
        if not shutil.which(name):
            raise TestFailure("missing required command: " + name)
    if not Path("/usr/local/sbin/hy2_cleanup_one.sh").is_file():
        raise TestFailure("HY2 cleanup tool not installed")
    quota_script = next(
        (p for p in (Path("/root/pq_keepcycle.sh"),
                      Path("/usr/local/sbin/pq_keepcycle.sh")) if p.is_file()),
        None,
    )
    if not quota_script:
        raise TestFailure("pq_keepcycle.sh not installed")
    command(["bash", "-n", str(quota_script)])
    if not all(active(x) for x in (
        "hy2.service", "wg-quick@wg-nat.service", "hy2-manual-guard.service",
    )):
        raise TestFailure("one of HY2, WG or manual guard services is inactive")
    command(["hy2-guard", "audit"], logfile=workdir / "guard-preflight.log")
    stat = os.statvfs("/var/lib/hy2")
    if stat.f_bavail * stat.f_frsize < 80 * 1024 * 1024:
        raise TestFailure("less than 80MiB disk headroom")
    mem_avail = 0
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            mem_avail = int(line.split()[1])
            break
    if mem_avail < 110 * 1024:
        raise TestFailure("less than 110MiB available memory")
    return quota_script


def assert_run(report, name, fn):
    try:
        fn()
    except Exception as exc:
        report.append({"check": name, "result": "FAIL", "detail": str(exc)})
        print("FAIL", name, "-", str(exc), flush=True)
        return False
    report.append({"check": name, "result": "PASS"})
    print("PASS", name, flush=True)
    return True


def status_has_port(line, port, status):
    # Status may have other legitimate ports; only inspect our own.
    return any(
        s.startswith(line + " ") and str(port) in s.split(":", 1)[-1].replace(",", " ").split()
        for s in status.splitlines()
    )


def verify_guard_absent(port):
    status = command(["hy2-guard", "status"]).stdout
    if status_has_port("PAUSED:", port, status):
        raise TestFailure("test port is still paused")
    if any(s.startswith(f"BAN {port}:") for s in status.splitlines()):
        raise TestFailure("test port still has CIDR bans")
    command(["hy2-guard", "audit"])


def test_guard(port):
    # This is rules/CLI validation, NOT a claim of v2rayN traffic blocking.
    verify_guard_absent(port)
    command(["hy2-guard", "pause", port])
    status = command(["hy2-guard", "status"]).stdout
    if not status_has_port("PAUSED:", port, status):
        raise TestFailure("pause rule not reported")
    command(["hy2-guard", "audit"])
    command(["hy2-guard", "resume", port])
    verify_guard_absent(port)

    for cidr in NETS:
        # Never ban a real-client source.
        net = ipaddress.ip_network(cidr)
        if not net.network_address in ipaddress.ip_network("198.51.100.0/24") and not net.network_address in ipaddress.ip_network("203.0.113.0/24"):
            raise TestFailure("source ban is not a documentation subnet")
        command(["hy2-guard", "ban", port, cidr])
    command(["hy2-guard", "audit"])
    command(["hy2-guard", "restore"])
    command(["hy2-guard", "audit"])
    rules = command(["nft", "-a", "list", "table", "inet", "hy2_manual_guard"]).stdout
    for cidr in NETS:
        if f"hy2-guard-ban:{port}:{cidr}" not in rules:
            raise TestFailure("live nft CIDR rule not found after restore")
    for cidr in NETS:
        command(["hy2-guard", "unban", port, cidr])
    verify_guard_absent(port)


def test_keepcycle(tag, port, quota_script, workdir):
    q = STATE / "quota" / (str(port) + ".env")
    tm = STATE / "temp" / (tag + ".env")
    before = meta(q)
    original_next = before.get("NEXT_RESET_EPOCH")
    if before.get("OWNER_KIND") != "temp" or before.get("OWNER_TAG") != tag:
        raise TestFailure("test quota owner mismatched")
    if before.get("ORIGINAL_LIMIT_BYTES") != "21474836":
        raise TestFailure("unexpected initial 0.02 GiB quota")
    if before.get("RESET_INTERVAL_SECONDS") != "2592000":
        raise TestFailure("31d node did not receive the 30d quota cycle")
    if not original_next or int(original_next) <= int(dt.datetime.now().timestamp()):
        raise TestFailure("invalid initial 30d reset anchor")
    result = command(
        ["bash", str(quota_script), str(port), "0.04"],
        timeout=90,
        logfile=workdir / "keepcycle.raw.log",
    )
    backup_match = re.search(r"(/root/pq-keepcycle-[0-9]+-[A-Za-z0-9]+)", result.stdout)
    if backup_match:
        (workdir / "backup_path.txt").write_text(backup_match.group(1), encoding="utf-8")
    after = meta(q)
    if not quota_anchor(before, after, 42949672, int(dt.datetime.now().timestamp())):
        raise TestFailure("quota reset does not preserve old 30d anchor")
    if meta(tm).get("PQ_LIMIT_BYTES") != "42949672":
        raise TestFailure("node quota metadata disagrees with quota meta")
    command(["pq_audit.sh", str(port)], logfile=workdir / "quota-after.raw.log")
    # The live nft quota is rebuilt, not just metadata. Check exact object.
    nft = command(["nft", "list", "quota", "inet", "hy2_pq", f"hy2_pq_q_{port}"]).stdout
    if "42949672" not in nft:
        raise TestFailure("live nft quota does not have new size")


def test_traffic(port, tag, workdir):
    result = command(["hy2_traffic", str(port), "--json"], timeout=90,
                     logfile=workdir / "traffic-json.raw.log")
    try:
        data = json.loads(result.stdout)
    except ValueError as exc:
        raise TestFailure("hy2_traffic JSON is malformed") from exc
    users = [x for x in data.get("users", []) if x.get("tag") == tag]
    if len(users) != 1 or users[0].get("port") != int(port):
        raise TestFailure("test traffic node not found in JSON")
    if data.get("timezone") != "Asia/Shanghai":
        raise TestFailure("traffic timezone mismatch")


def create_test_node(tag, workdir, duration=2678400):
    suffix = tag.removeprefix("hy2-temp-")
    env = {**os.environ, "id": suffix, "IP_VERSION": "4",
           "IP_LIMIT": "1", "PQ_GIB": "0.02", "D": str(duration)}
    command(["hy2_mktemp_nat.sh"], env=env, timeout=240,
            logfile=workdir / "create-with-secret.raw.log")
    fields = meta_for_test(tag)
    if fields is None:
        raise TestFailure("created HY2 test node metadata missing")
    port = fields["PORT"]
    if port == "443":
        raise TestFailure("refuse to test the main HY2 port")
    if not active(tag + ".service"):
        raise TestFailure("test HY2 temp service inactive")
    return port


def remove_backup(workdir, port):
    f = workdir / "backup_path.txt"
    if not f.is_file():
        return
    path = Path(f.read_text().strip())
    if re.fullmatch(rf"/root/pq-keepcycle-{re.escape(str(port))}-[A-Za-z0-9]+", str(path)):
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)


def report_out(workdir, results, live, cleanup, timer_retained, mode=None):
    summary = {
        "machine": "helan",
        "mode": mode or ("live" if live else "check"),
        "pass_count": sum(x["result"] == "PASS" for x in results),
        "fail_count": sum(x["result"] == "FAIL" for x in results),
        "skip_count": sum(x["result"] == "SKIP" for x in results),
        "overall": "PASS" if all(x["result"] in ("PASS", "SKIP") for x in results) and cleanup else "FAIL",
        "cleanup_ok": cleanup,
        "fallback_timer_retained": timer_retained,
        "results": results,
    }
    target = workdir / "report.json"
    target.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    os.chmod(target, 0o600)
    print("TEST_COUNTS=PASS:{pass_count} FAIL:{fail_count} SKIP:{skip_count}".format(**summary))
    print("FINAL_RESULT=" + summary["overall"])
    print("CLEANUP=" + ("PASS" if cleanup else "FAIL"))
    print("RESULT_FILE=" + str(target))
    print("只分享 report.json；不要分享 *.raw.log（其中可能含链接和密码）")
    return summary["overall"] == "PASS"


def main(live):
    check_machine()
    os.umask(0o077)
    lock_path = Path("/run/hy2-batch-accept.lock")
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise TestFailure("another HY2 batch acceptance is running") from exc

        key = secrets.token_hex(4)
        tag = f"hy2-temp-batch-{key}"
        workdir = ROOT / ("hy2-batch-accept-" + key)
        workdir.mkdir(mode=0o700)
        results = []
        cleanup_ok = True
        timer_ready = False
        new_port = None
        backup_port = None
        timer_retained = False
        fatal = False

        try:
            quota_script = None
            def preflight():
                nonlocal quota_script
                quota_script = check_preflight(workdir)
            if not assert_run(results, "host/dependencies/owner/guard/disk/memory", preflight):
                raise TestFailure("preflight failed")
            assert_run(results, "HY2 doctor", lambda: command(
                ["hy2_doctor.sh", "--strict"], timeout=90,
                logfile=workdir / "doctor.raw.log"))
            assert_run(results, "managed timers", lambda: check_timers())
            assert_run(results, "daily traffic main JSON", lambda: main_traffic_check(workdir))
            assert_run(results, "WireGuard marked route", check_wg_route)
            if Path("/root/hy2-abuse.py").is_file():
                assert_run(results, "read-only abuse audit", lambda: command(
                    ["python3", "/root/hy2-abuse.py", "443", "--days", "1"],
                    timeout=90, logfile=workdir / "abuse.raw.log"))
            else:
                results.append({"check": "read-only abuse audit", "result": "SKIP",
                                "detail": "hy2-abuse.py not installed"})
                print("SKIP read-only abuse audit (not installed)")
            if live and any(item["result"] == "FAIL" for item in results):
                raise TestFailure("read-only checks failed; skipping live mutations")
            if live:
                setup_safety_timer(tag, workdir)
                timer_ready = True
                print("SAFEGUARD=30_MIN_PERSISTENT_TIMER", flush=True)
                def create():
                    nonlocal new_port, backup_port
                    new_port = create_test_node(tag, workdir)
                    backup_port = new_port
                if not assert_run(results, "isolated 31-day HY2-NAT node create", create):
                    raise TestFailure("node creation failed")
                for name, fn in (
                    ("test-node daily traffic JSON",
                     lambda: test_traffic(new_port, tag, workdir)),
                    ("temporary-node guard pause/resume + /32 + /24 + restore",
                     lambda: test_guard(new_port)),
                    ("quota reset 0.02 to 0.04 GiB retaining 30-day anchor",
                     lambda: test_keepcycle(tag, new_port, quota_script, workdir)),
                    ("nft quota and original service health",
                     lambda: check_after(new_port)),
                ):
                    if not assert_run(results, name, fn):
                        raise TestFailure("stopped after failed live test")
        except Exception as exc:
            fatal = True
            print("STOP", str(exc), flush=True)
        finally:
            if timer_ready:
                try:
                    cleanup_tag(tag, workdir)
                    assert_run(results, "forced temporary-node cleanup",
                               lambda: verify_clean(tag, new_port))
                except Exception as exc:
                    cleanup_ok = False
                    results.append({"check": "forced temporary-node cleanup",
                                    "result": "FAIL", "detail": str(exc)})
                    print("FAIL forced temporary-node cleanup:", str(exc))
                if cleanup_ok:
                    try:
                        remove_timer(tag)
                        if backup_port is not None:
                            remove_backup(workdir, backup_port)
                    except Exception as exc:
                        cleanup_ok = False
                        print("FAIL timer/backup cleanup:", str(exc))
                else:
                    timer_retained = True
                    print("安全清理计时器已保留，30 分钟兜底将继续尝试。")
            if fatal:
                results.append({"check": "overall execution", "result": "FAIL",
                                "detail": "stopped after unsafe or failed precondition"})
            passed = report_out(workdir, results, live, cleanup_ok, timer_retained)

        return 0 if passed else 1


def check_timers():
    for unit in (
        "hy2-gc.timer", "pq-save.timer", "pq-reset.timer",
        "hy2-managed-watchdog.timer", "hy2-traffic.timer",
        "hy2-traffic-shutdown.service",
    ):
        if not active(unit):
            raise TestFailure(unit + " not active")


def main_traffic_check(workdir):
    result = command(["hy2_traffic", "main", "--json"], timeout=90,
                     logfile=workdir / "main-traffic.raw.log")
    obj = json.loads(result.stdout)
    if obj.get("timezone") != "Asia/Shanghai":
        raise TestFailure("daily traffic timezone not Beijing")
    if not any(x.get("tag") == "main" for x in obj.get("users", [])):
        raise TestFailure("main traffic record absent")


def check_wg_route():
    result = command(["ip", "-4", "route", "get", "1.1.1.1", "mark", "2333"])
    if "dev wg-nat" not in result.stdout:
        raise TestFailure("marked route not through wg-nat")


def check_after(port):
    for unit in ("hy2.service", "wg-quick@wg-nat.service"):
        if not active(unit):
            raise TestFailure("service became inactive: " + unit)
    command(["hy2-guard", "audit"])
    command(["pq_audit.sh", str(port)])


def verify_clean(tag, port):
    if meta_for_test(tag) is not None or active(tag + ".service"):
        raise TestFailure("test-node state not removed")
    if port is not None:
        verify_guard_absent(port)
    for unit in ("hy2.service", "wg-quick@wg-nat.service"):
        if not active(unit):
            raise TestFailure("main service failed after cleanup")



def skip_check(results, name, reason):
    print("SKIP", name, "-", reason, flush=True)
    results.append({"check": name, "result": "SKIP", "detail": reason})


def test_watchdog_healthy(tag, workdir):
    """No induced outages. Execute normal watchdog on an isolated healthy node."""
    other = []
    for path in (STATE / "temp").glob("hy2-temp-*.env"):
        if path.stem != tag:
            other.append(path.stem)
    if other:
        return "existing other temporary nodes: skip potentially mutating shared watchdog"
    fields = meta_for_test(tag)
    if fields is None or not active(tag + ".service"):
        raise TestFailure("watchdog target missing before healthy-scan")
    command(["/usr/local/sbin/hy2_managed_watchdog.sh"],
            timeout=110, logfile=workdir / "watchdog.raw.log")
    fields = meta_for_test(tag)
    if fields is None or not active(tag + ".service"):
        raise TestFailure("healthy watchdog scan disrupted isolated node")
    if fields.get("WATCHDOG_FAILURES", "0") not in ("", "0"):
        raise TestFailure("watchdog introduced failure state on healthy node")
    return None


def test_portbw_idle_auto(tag, port, workdir):
    """Test auto configuration, idle sampling and removal (NOT load triggering)."""
    if not shutil.which("portbw"):
        return "portbw is not installed"
    config = Path("/etc/portbw/config.json")
    if not config.is_file():
        return "portbw has no managed config"
    try:
        conf = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "portbw config unreadable; do not mutate"
    if conf.get("tc_enabled") is not True:
        return "portbw tc mode not enabled; auto requires tc"
    if not active("portbw-watch.service"):
        return "portbw resident watch not active"
    existing = list((Path("/var/lib/portbw/ports")).glob("*.json"))
    if existing:
        return "another portbw policy exists; avoid changing shared sampling"
    if not active(tag + ".service"):
        raise TestFailure("test listener unavailable for portbw")
    command(["portbw", "audit"], timeout=60)
    marker = workdir / "portbw-owned.json"
    if marker.exists():
        raise TestFailure("owned portbw marker already exists")
    marker.write_text(json.dumps({"tag": tag, "port": int(port)}) + "\n",
                      encoding="utf-8")
    os.chmod(marker, 0o600)
    command(["portbw", "auto", "set", str(port),
             "--up", "5", "--down", "5", "--trigger", "85",
             "--after", "8s", "--auto-up", "2", "--auto-down", "2",
             "--hold", "120s", "--cooldown", "0s"],
            timeout=90, logfile=workdir / "portbw-set.raw.log")
    rec = Path("/var/lib/portbw/ports") / (str(port) + ".json")
    if not rec.is_file():
        raise TestFailure("portbw auto config not saved")
    opts = json.loads(rec.read_text(encoding="utf-8")).get("auto", {})
    if opts.get("after") != 8 or opts.get("trigger_bp") != 8500:
        raise TestFailure("portbw auto options do not match test")
    command(["portbw", "auto", "status", str(port)], timeout=60,
            logfile=workdir / "portbw-status.raw.log")
    command(["portbw", "audit"], timeout=90,
            logfile=workdir / "portbw-audit.raw.log")
    # Actual high-load slowdown is NOT claimed; this is idle-state wiring.
    command(["portbw", "auto", "off", str(port)], timeout=90)
    cleanup_portbw_marker(tag, workdir)
    command(["portbw", "audit"], timeout=60)
    return None


def await_natural_expiry(tag, port, timeout_seconds=260):
    """Allow HY2's real timeout/ExecStopPost + GC to expire its own node."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() <= deadline:
        fields = meta_for_test(tag)
        if fields is None and not active(tag + ".service"):
            q = STATE / "quota" / (str(port) + ".env")
            il = STATE / "iplimit" / (str(port) + ".env")
            if q.exists() or il.exists():
                raise TestFailure("expired node left quota or iplimit metadata")
            return
        time.sleep(3)
    raise TestFailure("temporary node was not automatically cleaned by expiry")


def maintenance():
    """Second automated batch: passive watchdog, portbw idle auto, real TTL GC."""
    check_machine()
    os.umask(0o077)
    lockfile = Path("/run/hy2-batch-accept.lock")
    with lockfile.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise TestFailure("another batch is in progress") from exc
        key = secrets.token_hex(4)
        tag = "hy2-temp-batch-" + key
        workdir = ROOT / ("hy2-batch-accept-" + key)
        workdir.mkdir(mode=0o700)
        results = []
        timer_ready = False
        cleanup_ok = True
        timer_retained = False
        fatal = False
        port = None
        try:
            # A no-op preflight failure cannot create a long-lived node.
            if not assert_run(results, "host, dependencies, guard and service preflight",
                              lambda: check_preflight(workdir)):
                raise TestFailure("preflight failed")
            for name, fn in (
                ("HY2 doctor", lambda: command(
                    ["hy2_doctor.sh", "--strict"], timeout=90,
                    logfile=workdir / "doctor.raw.log")),
                ("managed timers", check_timers),
                ("WG marked exit route", check_wg_route),
            ):
                if not assert_run(results, name, fn):
                    raise TestFailure("maintenance preflight failed")
            if not Path("/usr/local/sbin/hy2_managed_watchdog.sh").is_file():
                raise TestFailure("watchdog script missing")
            # Install a persistent cleanup first; the test node lives 180s.
            setup_safety_timer(tag, workdir)
            timer_ready = True
            print("SAFEGUARD=30_MIN_PERSISTENT_TIMER", flush=True)
            def make():
                nonlocal port
                port = create_test_node(tag, workdir, duration=180)
            if not assert_run(results, "isolated 180s HY2-NAT node creation", make):
                raise TestFailure("test node creation failed")
            for name, fn in (
                ("healthy watchdog scan", lambda: test_watchdog_healthy(tag, workdir)),
                ("portbw automatic-policy idle lifecycle", lambda: test_portbw_idle_auto(
                    tag, port, workdir)),
            ):
                if not (meta_for_test(tag) and active(tag + ".service")):
                    raise TestFailure("temporary node expired before tests completed")
                try:
                    reason = fn()
                    if reason:
                        skip_check(results, name, reason)
                    else:
                        results.append({"check": name, "result": "PASS"})
                        print("PASS", name, flush=True)
                except Exception as exc:
                    results.append({"check": name, "result": "FAIL", "detail": str(exc)})
                    print("FAIL", name, str(exc), flush=True)
                    raise TestFailure("maintenance live test failed") from exc
            print("WAITING_FOR_NAT_TEST_NODE_NATURAL_EXPIRY", flush=True)
            if not assert_run(results, "real TTL expiry and automatic cleanup",
                              lambda: await_natural_expiry(tag, port)):
                raise TestFailure("natural expiry failed")
            if not assert_run(results, "guard/primary services after expiry",
                              lambda: verify_clean(tag, port)):
                raise TestFailure("post-expiry health check failed")
        except Exception as exc:
            fatal = True
            print("STOP", str(exc), flush=True)
        finally:
            if timer_ready:
                try:
                    cleanup_tag(tag, workdir)
                    assert_run(results, "safety cleanup verified",
                               lambda: verify_clean(tag, port))
                except Exception as exc:
                    cleanup_ok = False
                    results.append({"check": "safety cleanup verified",
                                    "result": "FAIL", "detail": str(exc)})
                    print("FAIL safety cleanup:", str(exc), flush=True)
                if cleanup_ok:
                    try:
                        remove_timer(tag)
                    except Exception as exc:
                        cleanup_ok = False
                        results.append({"check": "cleanup timer removal",
                                        "result": "FAIL", "detail": str(exc)})
                        print("FAIL timer removal:", str(exc), flush=True)
                else:
                    timer_retained = True
                    print("Safety timer retained for its single scheduled cleanup attempt")
            if fatal:
                results.append({"check": "overall execution", "result": "FAIL"})
            passed = report_out(workdir, results, True, cleanup_ok,
                                timer_retained, mode="maintenance")
        return 0 if passed else 1


def run_failsafe(tag, logdir):
    check_machine()
    os.umask(0o077)
    if not valid_tag(tag):
        raise TestFailure("invalid cleanup tag")
    cleanup_tag(tag, logdir)
    remove_timer(tag)
    (logdir / "fallback-cleanup-PASS.txt").write_text("CLEANUP=PASS\n", encoding="utf-8")
    print("FALLBACK_CLEANUP=PASS")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="HY2 test-VPS batch live regression")
    ap.add_argument("--live", action="store_true",
                    help="automatically create, test and delete a dedicated HY2 node")
    ap.add_argument("--maintenance", action="store_true",
                    help="180s isolated node: watchdog healthy scan, portbw idle auto, TTL cleanup")
    ap.add_argument("--cleanup", metavar="TAG", help=argparse.SUPPRESS)
    ap.add_argument("--logdir", type=Path, help=argparse.SUPPRESS)
    args = ap.parse_args()
    try:
        if args.cleanup:
            if not args.logdir or not str(args.logdir).startswith("/root/hy2-batch-accept-"):
                raise TestFailure("invalid fallback workdir")
            sys.exit(run_failsafe(args.cleanup, args.logdir))
        if args.maintenance and args.live:
            raise TestFailure("choose --live or --maintenance, not both")
        sys.exit(maintenance() if args.maintenance else main(args.live))
    except (TestFailure, OSError) as exc:
        print("BATCH_FAIL:", str(exc), file=sys.stderr)
        sys.exit(1)
