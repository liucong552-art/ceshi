#!/usr/bin/env bash
# Round 2 runs real nft rules only in a disposable network namespace.
set -Eeuo pipefail
umask 077

if [[ "${1:-}" == --help ]]; then
  cat <<'HELP'
Usage: bash helan_round2.sh
Root, hostname helan, Linux amd64, and network namespace privileges required.
Tests the pinned HY2 quota/IP helpers against real kernel UDP traffic.
Includes actual HY2 encrypted proxy cutoff at a small test quota.
Does not install the HY2 manager or change host network rules/services.
If nft is missing, APT downloads signed packages for private extraction only;
no packages are installed. APT package lists may be refreshed.
Keeps reports under /root/hy2-kernel-realtest.*; removes downloaded tooling.
HELP
  exit 0
fi
[[ $# == 0 && "$EUID" == 0 && "$(hostname -s)" == helan && "$(uname -m)" == x86_64 ]] \
  || { echo '请以 root 在 amd64 Helan 测试机执行；可用 --help 查看说明' >&2; exit 1; }
for tool in curl python3 openssl sha256sum timeout tee unshare ip flock readlink dpkg-deb; do
  command -v "$tool" >/dev/null 2>&1 || { echo "缺少依赖：$tool" >&2; exit 1; }
done

WORK="$(mktemp -d /root/hy2-kernel-realtest.XXXXXX)"
SOURCE="$WORK/source"
RUNNER_REF="${HY2_TEST_RUNNER_REF:-feature/hy2-jieshu-parity}"
[[ "$RUNNER_REF" =~ ^[0-9a-f]{40}$ || "$RUNNER_REF" == feature/hy2-jieshu-parity ]] \
  || { echo '测试脚本版本参数无效' >&2; exit 1; }
mkdir -p "$SOURCE/tests" "$WORK/runtime" "$WORK/toolroot" "$WORK/debs/partial"
TEST_PID=''

cleanup() {
  local rc=$?
  trap - EXIT INT TERM HUP
  if [[ -n "$TEST_PID" ]]; then
    kill -TERM -- "-$TEST_PID" 2>/dev/null || true
    wait "$TEST_PID" 2>/dev/null || true
  fi
  # Use system libraries for cleanup even if a private tool failed to load.
  unset LD_LIBRARY_PATH
  rm -rf -- "$SOURCE" "$WORK/toolroot" "$WORK/debs"
  rm -f -- "$WORK/hysteria"
  printf '\n第二轮退出码：%s\n报告：%s\n' "$rc" "$WORK/round2.log"
  if [[ -f "$WORK/result.json" ]]; then printf '结果：%s\n' "$WORK/result.json"; fi
  echo '测试网络命名空间已退出；仍需完成正式安装、真实 WG-NAT 与重启验收。'
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
exec > >(tee -a "$WORK/round2.log") 2>&1
trap 'printf "第二轮停止：第 %s 行，退出码 %s\n" "$LINENO" "$?" >&2' ERR

echo '=== 1/5 确认网络隔离能力 ==='
export HY2_TEST_HOST_NETNS
HY2_TEST_HOST_NETNS="$(readlink /proc/self/ns/net)"
unshare --net -- sh -c '[ "$(readlink /proc/self/ns/net)" != "$HY2_TEST_HOST_NETNS" ]'
printf '主机网络：%s\n' "$HY2_TEST_HOST_NETNS"
date -u '+UTC %Y-%m-%d %H:%M:%S'

echo '=== 2/5 准备 nft 工具（不安装系统包） ==='
if ! command -v nft >/dev/null 2>&1; then
  command -v apt-get >/dev/null 2>&1 || { echo 'nft 缺失且没有 apt-get' >&2; exit 1; }
  echo '仅下载 Debian 包并解包到本轮临时目录；不启动 nftables.service。'
  apt-get update -o Acquire::Retries=2
  apt-get -y --download-only --no-install-recommends --no-upgrade \
    -o "Dir::Cache::archives=$WORK/debs/" install nftables
  found=0
  for deb in "$WORK/debs/"*.deb; do
    [[ -f "$deb" ]] || continue
    package="$(dpkg-deb -f "$deb" Package)"
    # Keep the machine's existing glibc and dynamic loader in use.
    case "$package" in libc6*|libc-bin) continue ;; esac
    dpkg-deb -x "$deb" "$WORK/toolroot"
    found=$((found + 1))
  done
  (( found > 0 )) || { echo '没有取得可用的 nft 测试工具包' >&2; exit 1; }
  export PATH="$WORK/toolroot/usr/sbin:$WORK/toolroot/usr/bin:$PATH"
  export LD_LIBRARY_PATH="$WORK/toolroot/usr/lib/x86_64-linux-gnu:$WORK/toolroot/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
nft --version

echo '=== 3/5 下载固定候选代码和校验后的官方 HY2 核心 ==='
CANDIDATE_REF=d4c405bc19e2c2c2ce06d8c3c6be6376fe3617e8
RAW="https://raw.githubusercontent.com/liucong552-art/ceshi/$CANDIDATE_REF"
for file in hy2.sh tests/hy2_test_support.py tests/hy2_loopback.py; do
  curl -fLsS --retry 2 --connect-timeout 10 --max-time 120 "$RAW/$file" -o "$SOURCE/$file"
done
curl -fLsS --retry 2 --connect-timeout 10 --max-time 120 \
  "https://raw.githubusercontent.com/liucong552-art/ceshi/$RUNNER_REF/tests/hy2_kernel_acceptance.py" \
  -o "$SOURCE/tests/hy2_kernel_acceptance.py"
curl -fLsS --retry 2 --connect-timeout 10 --max-time 180 \
  'https://github.com/HyNetworks/hysteria/releases/download/app/v2.13.0/hysteria-linux-amd64' \
  -o "$WORK/hysteria"
printf '%s  %s\n' '907ba8c9693edb104b20582681fb7dc15639d5b64a9cbb616a7b539190a86691' "$WORK/hysteria" | sha256sum -c -
chmod 700 "$WORK/hysteria"
printf '候选代码：%s\n测试脚本：%s\n' "$CANDIDATE_REF" "$RUNNER_REF"

echo '=== 4/5 隔离网络中运行 21 项真实内核测试 ==='
echo '逐项显示结果；测试会故意用尽小配额、占满 IP 槽位并删除隔离网络内的测试表。'
export PYTHONDONTWRITEBYTECODE=1
TMPDIR="$WORK/runtime" timeout --kill-after=5s 360s unshare --net -- \
  python3 -B "$SOURCE/tests/hy2_kernel_acceptance.py" --work "$WORK" --binary "$WORK/hysteria" &
TEST_PID=$!
if wait "$TEST_PID"; then
  TEST_PID=''
else
  status=$?
  TEST_PID=''
  exit "$status"
fi

echo '=== 5/5 核对结果 ==='
python3 - "$WORK/result.json" <<'PY'
import json, sys
with open(sys.argv[1], encoding='utf-8') as stream:
    report = json.load(stream)
if len(report.get('cases', [])) != 21 or report.get('passed') != 21 or report.get('failed') != 0:
    raise SystemExit('第二轮没有全部通过，请保留完整输出和诊断目录')
print('PASS: 21/21；仅限本轮内核防护与本机 HY2 配额测试，不代表完整生产验收。')
PY
