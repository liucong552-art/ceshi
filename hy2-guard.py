#!/usr/bin/env python3
"""HY2-owned, independently persisted port pause and IP/CIDR bans.

This never edits HY2 quota/IP-limit tables or the WireGuard routing policy.
All nft updates use a single atomic batch, with separate owned chains.
"""
import argparse
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

TABLE = "hy2_manual_guard"
ROOT = Path("/var/lib/hy2/manual-guard")
STATE = ROOT / "state.json"
LOCK = Path("/run/lock/hy2-manual-guard.lock")
DEST = Path("/usr/local/sbin/hy2-guard")
UNIT = Path("/etc/systemd/system/hy2-manual-guard.service")
TEMP_STATE = Path("/var/lib/hy2/temp")
MAIN_META = Path("/var/lib/hy2/main/main.env")


class GuardError(Exception):
    pass


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def require_root():
    if os.geteuid() != 0:
        raise GuardError("需要 root 权限")


def port_number(value):
    if not re.fullmatch(r"[0-9]{1,5}", str(value)):
        raise GuardError("端口必须为 1..65535 的十进制数")
    n = int(value)
    if not 1 <= n <= 65535:
        raise GuardError("端口超出范围")
    return n


def network(value):
    try:
        return str(ipaddress.ip_network(value, strict=True))
    except ValueError as exc:
        raise GuardError(f"非法 IP 或 CIDR，网段必须使用规范网络地址：{value}") from exc


def canonical(data):
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise GuardError("无效的防火墙元数据版本")
    paused = data.get("paused")
    bans = data.get("bans")
    if not isinstance(paused, list) or not isinstance(bans, dict):
        raise GuardError("防火墙元数据字段异常")
    pset = set()
    for p in paused:
        if not isinstance(p, int) or isinstance(p, bool):
            raise GuardError("暂停端口格式异常")
        pset.add(port_number(p))
    found = {}
    for port, addresses in bans.items():
        p = port_number(port)
        if not isinstance(addresses, list):
            raise GuardError("黑名单字段异常")
        found[str(p)] = sorted({network(a) for a in addresses},
                               key=lambda x: (ipaddress.ip_network(x).version, x))
    return {"schema": 1, "paused": sorted(pset),
            "bans": dict(sorted(found.items(), key=lambda kv: int(kv[0])))}


def empty_state():
    return {"schema": 1, "paused": [], "bans": {}}


def load():
    if not STATE.exists():
        return empty_state()
    try:
        return canonical(json.loads(STATE.read_text(encoding="utf-8")))
    except (ValueError, TypeError, OSError) as exc:
        raise GuardError(f"拒绝加载损坏的状态文件 {STATE}: {exc}") from exc


def save(data):
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(ROOT, 0o700)
    fd, tmp = tempfile.mkstemp(prefix=".guard-", dir=ROOT)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as h:
            json.dump(canonical(data), h, sort_keys=True, ensure_ascii=False, indent=2)
            h.write("\n")
            h.flush()
            os.fsync(h.fileno())
        os.replace(tmp, STATE)
        root_fd = os.open(ROOT, os.O_DIRECTORY)
        try:
            os.fsync(root_fd)
        finally:
            os.close(root_fd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def get_meta(path, key):
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1]
    return ""


def require_hy2_port(port):
    # Prevent accidental block of SSH or unrelated services on the VPS.
    if get_meta(MAIN_META, "MAIN_PORT") == str(port):
        return
    for path in TEMP_STATE.glob("hy2-temp-*.env"):
        if get_meta(path, "PORT") == str(port):
            return
    raise GuardError(f"端口 {port} 不属于受管理的 HY2 节点；禁止修改其他服务")


def table_exists():
    return subprocess.run(["nft", "list", "table", "inet", TABLE],
                          stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0


def nft_commands(data, exists):
    data = canonical(data)
    cmds = []
    if exists:
        # Atomic batch: there is no packet-processing gap between flush/add.
        cmds += [f"flush chain inet {TABLE} input",
                 f"flush chain inet {TABLE} pre"]
    else:
        cmds += [f"add table inet {TABLE}",
                 f"add chain inet {TABLE} input {{ type filter hook input priority -20; policy accept; }}",
                 f"add chain inet {TABLE} pre {{ type filter hook prerouting priority -150; policy accept; }}"]
    for port in data["paused"]:
        cmds.append(f'add rule inet {TABLE} input iifname != "lo" meta l4proto {{ tcp, udp }} th dport {port} counter drop comment "hy2-guard-pause:{port}"')
    for p, addresses in data["bans"].items():
        for cidr in addresses:
            typ = "ip6" if ipaddress.ip_network(cidr).version == 6 else "ip"
            cmds.append(f'add rule inet {TABLE} pre {typ} saddr {cidr} meta l4proto {{ tcp, udp }} th dport {p} counter drop comment "hy2-guard-ban:{p}:{cidr}"')
    return "\n".join(cmds) + "\n"


def apply(data):
    if not shutil.which("nft"):
        raise GuardError("缺少 nftables")
    batch = nft_commands(data, table_exists())
    try:
        run(["nft", "-f", "-"], input=batch)
    except subprocess.CalledProcessError as exc:
        raise GuardError(f"nft 原子规则更新失败：{exc}") from exc


def update(data):
    old = load()
    fresh = canonical(data)
    # nft -f is all-or-nothing; if disk persistence fails restore old rules.
    apply(fresh)
    try:
        save(fresh)
    except OSError:
        apply(old)
        raise


def lock():
    LOCK.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    handle = open(LOCK, "a+", encoding="utf-8")
    fcntl.flock(handle, fcntl.LOCK_EX)
    return handle


def status():
    obj = load()
    print("=== HY2 managed manual firewall ===")
    print("PAUSED:", ", ".join(map(str, obj["paused"])) or "-")
    for p, addresses in obj["bans"].items():
        print(f"BAN {p}:", ", ".join(addresses) or "-")
    if not obj["bans"]:
        print("BANS: -")


def audit():
    obj = load()
    p = subprocess.run(["nft", "-a", "list", "table", "inet", TABLE],
                       text=True, capture_output=True)
    if p.returncode:
        raise GuardError("独立防火墙表不存在；运行 hy2-guard restore")
    for port in obj["paused"]:
        if f'comment "hy2-guard-pause:{port}"' not in p.stdout:
            raise GuardError(f"暂停规则丢失：{port}")
    for port, addresses in obj["bans"].items():
        for addr in addresses:
            if f'comment "hy2-guard-ban:{port}:{addr}"' not in p.stdout:
                raise GuardError(f"黑名单规则丢失：{port} {addr}")
    print(f"GUARD AUDIT OK: {len(obj['paused'])} paused, "
          f"{sum(map(len, obj['bans'].values()))} bans")


def install():
    require_root()
    if not shutil.which("nft"):
        raise GuardError("请先安装 nftables")
    ROOT.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(ROOT, 0o700)
    if table_exists() and not STATE.exists():
        raise GuardError("已存在同名 nft 表但无管理记录，拒绝接管")
    src = Path(__file__).resolve()
    DEST.parent.mkdir(parents=True, exist_ok=True)
    if src != DEST:
        shutil.copyfile(src, DEST)
    os.chmod(DEST, 0o755)
    if not STATE.exists():
        save(empty_state())
    UNIT.write_text(
        "[Unit]\nDescription=Restore managed HY2 port pauses and CIDR bans\n"
        "After=local-fs.target nftables.service\nBefore=network-pre.target\n"
        "Wants=network-pre.target\n\n"
        "[Service]\nType=oneshot\nRemainAfterExit=yes\n"
        "ExecStart=/usr/local/sbin/hy2-guard restore\n\n"
        "[Install]\nWantedBy=multi-user.target\n", encoding="utf-8")
    os.chmod(UNIT, 0o644)
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "hy2-manual-guard.service"])
    apply(load())
    run(["systemctl", "start", "hy2-manual-guard.service"])
    print("GUARD INSTALL OK; no HY2/WG services restarted")


def cli():
    parser = argparse.ArgumentParser(
        description="HY2 independent port pause and source IP/CIDR blacklist")
    choices = parser.add_subparsers(dest="command", required=True)
    for command in ("pause", "resume", "clear", "status", "audit",
                    "restore", "install"):
        p = choices.add_parser(command)
        if command in ("pause", "resume", "clear"):
            p.add_argument("port")
    for command in ("ban", "unban"):
        p = choices.add_parser(command)
        p.add_argument("port")
        p.add_argument("network")
    args = parser.parse_args()
    require_root()
    if args.command == "install":
        install()
        return
    if args.command == "status":
        status()
        return
    if args.command == "audit":
        audit()
        return
    with lock():
        if args.command == "restore":
            apply(load())
            print("GUARD RESTORE OK")
            return
        port = port_number(args.port)
        if args.command in ("pause", "ban"):
            require_hy2_port(port)
        state = load()
        if args.command == "pause":
            state["paused"].append(port)
        elif args.command == "resume":
            state["paused"] = [p for p in state["paused"] if p != port]
        elif args.command == "clear":
            state["bans"].pop(str(port), None)
        elif args.command in ("ban", "unban"):
            address = network(args.network)
            state["bans"].setdefault(str(port), [])
            if args.command == "ban":
                state["bans"][str(port)].append(address)
            else:
                state["bans"][str(port)] = [
                    a for a in state["bans"][str(port)] if a != address]
                if not state["bans"][str(port)]:
                    del state["bans"][str(port)]
        update(state)
        print(f"GUARD {args.command.upper()} OK: {port}")


if __name__ == "__main__":
    try:
        cli()
    except (GuardError, OSError, subprocess.CalledProcessError) as exc:
        print(f"GUARD ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
