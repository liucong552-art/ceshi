#!/usr/bin/env python3
"""Read-only Hysteria 2 source-IP, journal errors and optional live stream audit.

No monitoring, DPI, automatic ban, or credential disclosure. Optional Traffic
Stats API is only queried on literal loopback, without HTTP redirects.
"""
import argparse
from collections import Counter
import datetime as dt
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib import request, parse

STATE = Path("/var/lib/hy2")
IP_TABLE = "hy2_iplimit"
KNOWN_CATEGORIES = ("auth", "handshake", "timeout", "quic", "network", "other")
MAX_JOURNAL_LINES = 3000


class AuditError(Exception):
    pass


def meta(path):
    if not path.is_file():
        return {}
    ret = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            ret[k] = v
    return ret


def owner(port):
    m = meta(STATE / "main/main.env")
    if m.get("MAIN_PORT") == str(port):
        return {"unit": "hy2.service", "tag": "main", "ip_version": 4}
    found = []
    for path in (STATE / "temp").glob("hy2-temp-*.env"):
        m = meta(path)
        if m.get("PORT") == str(port) and m.get("TAG") == path.stem:
            found.append({"unit": path.stem + ".service",
                          "tag": path.stem, "ip_version": int(m.get("IP_VERSION", "4"))})
    if len(found) != 1:
        raise AuditError("无法唯一确认端口归属 HY2，请先运行 hy2_audit.sh")
    return found[0]


def active_ips(port, version):
    name = f"hy2_il{version}_{port}"
    cmd = ["nft", "-j", "list", "set", "inet", IP_TABLE, name]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"state": "unavailable", "reason": str(exc), "ips": []}
    if result.returncode:
        return {"state": "not-configured-or-unavailable", "ips": []}
    try:
        parsed = json.loads(result.stdout)
        ips = set()
        def walk(obj):
            if isinstance(obj, dict):
                v = obj.get("val")
                if isinstance(v, str):
                    try:
                        ip = ipaddress.ip_address(v)
                        if ip.version == version:
                            ips.add(str(ip))
                    except ValueError:
                        pass
                for v in obj.values():
                    walk(v)
            elif isinstance(obj, list):
                for v in obj:
                    walk(v)
        walk(parsed)
        return {"state": "ok", "ips": sorted(ips, key=lambda x: int(ipaddress.ip_address(x)))}
    except (ValueError, KeyError) as exc:
        return {"state": "unavailable", "reason": str(exc), "ips": []}


def category(message):
    m = message.lower()
    if "auth" in m or "password" in m:
        return "auth"
    if "handshake" in m or "tls" in m:
        return "handshake"
    if "timeout" in m or "deadline exceeded" in m:
        return "timeout"
    if "quic" in m:
        return "quic"
    if any(x in m for x in ("network", "unreachable", "dial", "route")):
        return "network"
    return "other"


def journal_errors(unit, days):
    cmd = ["journalctl", "-u", unit, "--since", f"{days} days ago",
           "-n", str(MAX_JOURNAL_LINES), "-o", "json", "--no-pager"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AuditError("无法读取 journald: " + str(exc)) from exc
    if p.returncode:
        raise AuditError("journalctl 失败（无权限或日志不可用）")
    categories = Counter()
    level_counts = Counter()
    n = 0
    for line in p.stdout.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        # Only warnings/errors are counted. Raw messages can contain auth
        # material, so never print or store them.
        message = str(record.get("MESSAGE", ""))
        nested = {}
        if message.startswith("{"):
            try:
                nested = json.loads(message)
                if not isinstance(nested, dict):
                    nested = {}
            except ValueError:
                nested = {}
        prio = str(record.get("PRIORITY", "6"))
        level = str(nested.get("level", "")).lower()
        is_error = prio.isdigit() and int(prio) <= 4
        if level in ("warn", "warning", "error", "fatal", "panic"):
            is_error = True
        if not is_error:
            continue
        n += 1
        level_counts["error" if prio.isdigit() and int(prio) <= 3 or
                     level in ("error", "fatal", "panic") else "warning"] += 1
        categories[category(message)] += 1
    return {"events_scanned": min(len(p.stdout.splitlines()), MAX_JOURNAL_LINES),
            "warning_error_events": n,
            "levels": dict(level_counts),
            "categories": dict(categories),
            "period_days": days,
            "truncated_possible": len(p.stdout.splitlines()) >= MAX_JOURNAL_LINES}


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AuditError("拒绝 Stats API 重定向到其他地址")


def secure_api_url(value):
    p = parse.urlsplit(value)
    if p.scheme != "http" or p.username or p.password or p.query or p.fragment:
        raise AuditError("Stats API 仅接受 http://127.0.0.1:端口 或 http://[::1]:端口")
    try:
        ip = ipaddress.ip_address(p.hostname or "")
        port = p.port
    except ValueError as exc:
        raise AuditError("API 地址不是字面量 loopback IP/端口") from exc
    if not ip.is_loopback or port is None or not (1 <= port <= 65535):
        raise AuditError("Stats API 地址必须是本机 loopback")
    return f"http://{p.netloc}"


def api_json(url, secret, endpoint):
    headers = {"Accept": "application/json"}
    if secret:
        headers["Authorization"] = secret
    req = request.Request(url + endpoint, headers=headers)
    opener = request.build_opener(NoRedirect(), request.ProxyHandler({}))
    with opener.open(req, timeout=4) as r:
        if r.status != 200:
            raise AuditError("Stats API 返回非 200 状态")
        body = r.read(2_000_000 + 1)
    if len(body) > 2_000_000:
        raise AuditError("Stats API 响应太大")
    return json.loads(body)


def target_label(text):
    # Only display actual recorded destinations, not guessed DNS/domain names.
    if not isinstance(text, str) or not text or len(text) > 256:
        return None
    if re.search(r"[\x00-\x1f]", text) or "@" in text or "/" in text:
        return None
    return text


def stats(url, secret):
    online = api_json(url, secret, "/online")
    current = api_json(url, secret, "/dump/streams")
    if not isinstance(online, dict) or not isinstance(current, dict):
        raise AuditError("Traffic Stats API JSON 结构异常")
    nums = [v for v in online.values() if type(v) is int and v >= 0]
    streams = current.get("streams", [])
    if not isinstance(streams, list):
        raise AuditError("Traffic Stats API streams 结构异常")
    targets = Counter()
    for item in streams:
        if not isinstance(item, dict):
            continue
        target = target_label(item.get("hooked_req_addr")) or target_label(item.get("req_addr"))
        if target:
            targets[target] += 1
    return {"online_client_instances": sum(nums),
            "online_auth_entries": len(nums),
            "active_tcp_streams": len(streams),
            "target_counts": dict(targets.most_common(50)),
            "note": "only current TCP streams; not full historical destinations"}


def main():
    p = argparse.ArgumentParser(description="HY2 read-only abuse evidence, no auto bans")
    p.add_argument("port", nargs="?", default=os.getenv("PORT"),
                   help="HY2 server UDP port (or PORT environment variable)")
    p.add_argument("--days", type=int, default=int(os.getenv("DAYS", "7")))
    p.add_argument("--stats-url", help="optional loopback Hysteria Traffic Stats API address")
    p.add_argument("--secret-file", help="optional root-owned Stats API secret file")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    if os.geteuid() != 0:
        raise AuditError("请以 root 运行")
    if not args.port or not re.fullmatch(r"[0-9]{1,5}", args.port):
        raise AuditError("请填写 HY2 端口号，例如 hy2-abuse 40020")
    port = int(args.port)
    if not 1 <= port <= 65535 or not 1 <= args.days <= 90:
        raise AuditError("端口须在 1..65535，days 须在 1..90")
    own = owner(port)
    result = {"port": port, "node": own["tag"],
              "source_ip_slots": active_ips(port, own["ip_version"]),
              "journal": journal_errors(own["unit"], args.days)}
    if args.stats_url:
        url = secure_api_url(args.stats_url)
        secret = ""
        if args.secret_file:
            file = Path(args.secret_file)
            if not file.is_file() or file.stat().st_mode & 0o077:
                raise AuditError("Stats API secret 文件不存在或权限过宽；必须 root 私有")
            secret = file.read_text(encoding="utf-8").strip()
        result["api"] = stats(url, secret)
    elif args.secret_file:
        raise AuditError("--secret-file 必须与 --stats-url 同用")
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"HY2 来源/异常审计 | UDP {port} | {own['tag']}")
        slots = result["source_ip_slots"]
        print("当前来源 IP 槽位：", ", ".join(slots["ips"]) if slots["state"] == "ok" else slots["state"])
        j = result["journal"]
        print(f"最近 {args.days} 天警告/错误：{j['warning_error_events']} 条"
              f"（最多检查最近 {MAX_JOURNAL_LINES} 条 journald）")
        print("事件类型：", json.dumps(j["categories"], ensure_ascii=False))
        if "api" in result:
            t = result["api"]
            print(f"在线客户端实例：{t['online_client_instances']}；当前 TCP 流：{t['active_tcp_streams']}")
            print("当前 TCP 目标（前 50）：", json.dumps(t["target_counts"], ensure_ascii=False))
        else:
            print("未配置可选 loopback Stats API，无法查询当前 TCP 目标/在线数")
        print("注意：IP 槽位不等于人数；日志条目不等于攻击；不自动封禁。")


if __name__ == "__main__":
    try:
        main()
    except (AuditError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"HY2 审计失败：{exc}", file=sys.stderr)
        sys.exit(1)
