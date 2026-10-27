#!/usr/bin/env bash
# Helan round 1: read-only inventory and isolated HY2 transport tests.
set -Eeuo pipefail
umask 077

if [[ "${1:-}" == --help ]]; then
  cat <<'HELP'
Usage: bash helan_round1.sh
Run as root on the Helan test VPS (hostname helan), Linux x86_64.
Records existing services, nft table names and interface names read-only.
Downloads a pinned HY2 core into a private test directory and verifies SHA256.
Uses temporary loopback listeners and a test-only TLS trust root.
Tests plain, Salamander, and bindDevice=lo, including TCP/UDP and bad passwords.
Does not install HY2 management, change firewall rules, or restart services.
Does not test public reachability, kernel quotas/IP limits, real WG or reboot.
Requires curl, python3, openssl, sha256sum, timeout and tee; no automatic apt.
Retains diagnostics under /root/hy2-realtest.* and removes downloaded payloads.
HELP
  exit 0
fi
[[ $# == 0 ]] || { echo '未知参数；使用 --help 查看说明' >&2; exit 2; }
[[ "$EUID" == 0 ]] || { echo '请在 root@helan 执行' >&2; exit 1; }
[[ "$(hostname -s)" == helan ]] || { echo '目标不是 helan，停止测试' >&2; exit 1; }
[[ "$(uname -m)" == x86_64 ]] || { echo '此轮固定测试 Linux amd64 核心，当前架构不匹配' >&2; exit 1; }

missing=()
for tool in curl python3 openssl sha256sum timeout tee mktemp; do
  command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
done
if (( ${#missing[@]} )); then
  printf '缺少依赖：%s\n' "${missing[*]}" >&2
  echo '先执行 apt-get update && apt-get install -y curl ca-certificates python3 openssl coreutils，再重新运行本命令。' >&2
  exit 1
fi

WORK="$(mktemp -d /root/hy2-realtest.XXXXXX)"
SOURCE="$WORK/source"
mkdir -m 700 -p "$SOURCE/tests" "$WORK/runtime"
TEST_PID=''

cleanup() {
  local rc=$?
  trap - EXIT INT TERM HUP
  if [[ -n "$TEST_PID" ]]; then
    # GNU timeout owns this process group, containing only this test's children.
    kill -TERM -- "-$TEST_PID" 2>/dev/null || true
    wait "$TEST_PID" 2>/dev/null || true
  fi
  rm -rf -- "$SOURCE" "$WORK/runtime"
  rm -f -- "$WORK/hysteria"
  if (( rc == 0 )); then
    printf '\n第一轮完成；报告：%s\n' "$WORK/round1.log"
    printf '结构化结果：%s\n' "$WORK/result.json"
    echo '这只代表本机 HY2 传输测试通过，尚未完成配额/IP/WG/重启验收。'
  else
    printf '\n第一轮未全部通过，退出码=%s；报告：%s\n' "$rc" "$WORK/round1.log" >&2
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
exec > >(tee -a "$WORK/round1.log") 2>&1
trap 'printf "测试停止：第 %s 行，退出码 %s\n" "$LINENO" "$?" >&2' ERR

echo '=== 1/4 Helan 只读状态记录 ==='
date -u '+UTC %Y-%m-%d %H:%M:%S'
hostname
uname -r
sed -n '/^PRETTY_NAME=/p' /etc/os-release
printf 'CPU 数：'; getconf _NPROCESSORS_ONLN
if command -v free >/dev/null 2>&1; then free -m; fi
if command -v systemctl >/dev/null 2>&1; then
  systemctl list-units --all --type=service --no-pager --no-legend \
    'xray*' 'hy2*' 'wg-quick@*' 'socks5*' '3proxy*' 'portbw*' 'pq-*' || true
fi
echo '--- 现有管理路径（只显示存在性） ---'
for path in /etc/default/hy2-main /etc/default/vless-reality /var/lib/hy2/temp \
    /var/lib/vless-reality/temp /etc/wireguard/wg-nat.conf; do
  if [[ -e "$path" ]]; then printf 'EXISTS %s\n' "$path"; fi
done
echo '--- nft 表名 ---'
if command -v nft >/dev/null 2>&1; then nft list tables || true; else echo 'nft 未安装'; fi
echo '--- 接口与 WG 接口名 ---'
if command -v ip >/dev/null 2>&1; then ip -brief link show || true; fi
if command -v wg >/dev/null 2>&1; then wg show interfaces || true; fi

echo '=== 2/4 下载固定版本测试源码与核心 ==='
CANDIDATE_REF=d4c405bc19e2c2c2ce06d8c3c6be6376fe3617e8
RAW="https://raw.githubusercontent.com/liucong552-art/ceshi/$CANDIDATE_REF"
for file in hy2.sh tests/hy2_test_support.py tests/hy2_loopback.py; do
  printf '下载 %s\n' "$file"
  curl -fLsS --retry 2 --connect-timeout 10 --max-time 120 "$RAW/$file" -o "$SOURCE/$file"
done
curl -fLsS --retry 2 --connect-timeout 10 --max-time 180 \
  'https://github.com/HyNetworks/hysteria/releases/download/app/v2.13.0/hysteria-linux-amd64' \
  -o "$WORK/hysteria"
printf '%s  %s\n' '907ba8c9693edb104b20582681fb7dc15639d5b64a9cbb616a7b539190a86691' "$WORK/hysteria" | sha256sum -c -
chmod 700 "$WORK/hysteria"

echo '=== 3/4 普通 / Salamander / lo 网卡绑定真实 TCP+UDP 测试 ==='
echo '通常约 30–90 秒；测试监听仅使用本机回环地址。'
TMPDIR="$WORK/runtime" timeout --kill-after=5s 180s \
  python3 -B "$SOURCE/tests/hy2_loopback.py" "$WORK/hysteria" --bind-device \
  >"$WORK/result.json" 2>"$WORK/transport.stderr.log" &
TEST_PID=$!
if wait "$TEST_PID"; then
  TEST_PID=''
else
  status=$?
  TEST_PID=''
  cat "$WORK/transport.stderr.log"
  exit "$status"
fi

echo '=== 4/4 实测结果 ==='
python3 - "$WORK/result.json" <<'PY'
import json, sys
with open(sys.argv[1], encoding='utf-8') as stream:
    report = json.load(stream)
rows = report.get('results', [])
if {row.get('mode') for row in rows} != {'plain', 'salamander', 'bind-device'}:
    raise SystemExit('结果缺少预期模式，不能判定通过')
checks = ('tls_verification', 'tcp', 'udp', 'access_logs', 'invalid_auth_rejected')
if any(row.get(check) != 'PASS' for row in rows for check in checks):
    raise SystemExit('存在未通过的传输检查')
print(json.dumps(report, indent=2, ensure_ascii=False))
print('PASS: 3 种模式 × 5 项传输检查（共 15 项）')
PY
