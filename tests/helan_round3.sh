#!/usr/bin/env bash
# Round 3 installs the real manager on the dedicated, fresh Helan test host.
set -Eeuo pipefail
umask 077

if [[ "${1:-}" == --help ]]; then
  cat <<'HELP'
Usage: bash helan_round3.sh [prepare|verify]
prepare requires fresh amd64 Helan, a domain with only an A record pointing to
162.211.231.219, reachable TCP/80 for ZeroSSL, and UDP/443 for the main node.
Prompts locally for HY_DOMAIN and ACME_EMAIL if not already set.
Installs real packages, the HY2 manager, a pinned official core, public TLS,
timers and temporary test nodes. Leaves state ready for a manual reboot.
Does not reboot automatically. Reconnect after reboot, then run verify.
verify checks automatic recovery before invoking any repair commands, removes
its temporary nodes and test limits, and leaves the installed main node running.
Detailed installation logs and credentials remain root-only on Helan.
Real two-machine WG-NAT, an external UDP client, and actual renewal are separate.
HELP
  exit 0
fi
STAGE="${1:-prepare}"
[[ $# -le 1 && ( "$STAGE" == prepare || "$STAGE" == verify ) ]] \
  || { echo '用法：bash helan_round3.sh [prepare|verify]' >&2; exit 1; }
[[ "$EUID" == 0 && "$(hostname -s)" == helan && "$(uname -m)" == x86_64 ]] \
  || { echo '仅允许在专用 amd64 Helan 测试机以 root 执行' >&2; exit 1; }
for tool in curl python3 timeout tee systemctl sha256sum flock; do
  command -v "$tool" >/dev/null 2>&1 || { echo "缺少命令：$tool" >&2; exit 1; }
done
[[ "$(cat /proc/1/comm)" == systemd ]] || { echo '需要真实 systemd 主机' >&2; exit 1; }
exec 8>/run/helan-hy2-round3.lock
flock -n 8 || { echo '第三轮已有任务在运行' >&2; exit 1; }
POINTER=/root/helan_hy2_round3.path
SOURCE_REF=670c6fbe8a942796db59bbd9d5bb92606a012c30
CORE_SHA=907ba8c9693edb104b20582681fb7dc15639d5b64a9cbb616a7b539190a86691

if [[ "$STAGE" == verify ]]; then
  [[ -f "$POINTER" ]] || { echo '尚未完成 prepare' >&2; exit 1; }
  IFS= read -r WORK <"$POINTER"
  [[ "$WORK" =~ ^/root/hy2-systemd-realtest\.[A-Za-z0-9]+$ && -f "$WORK/baseline.json" ]] \
    || { echo '报告目录或重启基线无效，请保留现场' >&2; exit 1; }
  exec > >(tee -a "$WORK/verify.log") 2>&1
  echo '=== 第三轮后半：检查真实重启后的自动恢复 ==='
  date -u '+UTC %Y-%m-%d %H:%M:%S'
  PYTHONDONTWRITEBYTECODE=1 timeout --foreground --kill-after=5s 600s \
    python3 -B "$WORK/source/tests/hy2_systemd_acceptance.py" verify --work "$WORK"
  printf '报告：%s/verify.log\n结果：%s/verify-result.json\n' "$WORK" "$WORK"
  echo '真实 WG-NAT、外部客户端 UDP、自动限速与 CPU 验收仍待完成。'
  exit 0
fi

# Reject existing deployments before installing packages or writing project paths.
for existing in "$POINTER" /usr/local/lib/hy2 /var/lib/hy2 /etc/default/hy2-main \
  /usr/local/sbin/pq_add.sh /usr/local/sbin/ip_set.sh /etc/systemd/system/hy2.service \
  /etc/systemd/system/xray.service /etc/wireguard/wg-nat.env /root/.acme.sh; do
  [[ ! -e "$existing" && ! -L "$existing" ]] \
    || { echo "检测到已有配置：$existing；停止，避免覆盖。请保留现场供检查。" >&2; exit 1; }
done
if command -v hysteria >/dev/null 2>&1; then
  echo '检测到已安装的 Hysteria，停止以避免覆盖' >&2; exit 1
fi
for tool in ss ip; do command -v "$tool" >/dev/null || exit 1; done
[[ -z "$(ss -H -l -t -n 'sport = :80')" && -z "$(ss -H -l -u -n 'sport = :443')" ]] \
  || { echo 'TCP/80 或 UDP/443 已被占用；未进行安装' >&2; exit 1; }

echo '本轮将正式安装管理系统和证书。请使用专用于 Helan 的域名。'
echo '域名只设 DNS A 记录到 162.211.231.219，关闭 CDN 代理；TCP/80 需从公网可达。'
if [[ -z "${HY_DOMAIN:-}" ]]; then read -r -p '域名：' HY_DOMAIN </dev/tty; fi
if [[ -z "${ACME_EMAIL:-}" ]]; then read -r -p 'ZeroSSL 证书邮箱：' ACME_EMAIL </dev/tty; fi
export HY_DOMAIN ACME_EMAIL
HY_DOMAIN="$(python3 - <<'PY'
import ipaddress, os, re, socket
domain = os.environ['HY_DOMAIN'].strip().rstrip('.').encode('idna').decode().lower()
if len(domain) > 253 or '.' not in domain or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', s) for s in domain.split('.')):
    raise SystemExit('域名格式无效')
try:
    ipaddress.ip_address(domain)
except ValueError:
    pass
else:
    raise SystemExit('需要域名，不能填写 IP')
email = os.environ['ACME_EMAIL'].strip()
if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
    raise SystemExit('邮箱格式无效')
v4 = {item[4][0] for item in socket.getaddrinfo(domain, None, socket.AF_INET, socket.SOCK_DGRAM)}
if v4 != {'162.211.231.219'}:
    raise SystemExit('域名所有 A 记录必须指向 Helan；当前：' + ', '.join(sorted(v4)))
try:
    v6 = socket.getaddrinfo(domain, None, socket.AF_INET6, socket.SOCK_DGRAM)
except socket.gaierror:
    v6 = []
if v6:
    raise SystemExit('本轮请使用仅有 A 记录的专用域名，先移除该域名的 AAAA 记录')
print(domain)
PY
)"
ACME_EMAIL="${ACME_EMAIL//[[:space:]]/}"
export HY_DOMAIN ACME_EMAIL

RUNNER_REF="${HY2_TEST_RUNNER_REF:-feature/hy2-jieshu-parity}"
[[ "$RUNNER_REF" =~ ^[0-9a-f]{40}$ || "$RUNNER_REF" == feature/hy2-jieshu-parity ]] || exit 1
WORK="$(mktemp -d /root/hy2-systemd-realtest.XXXXXX)"
mkdir -p "$WORK/source/tests"
printf '%s\n' "$WORK" >"$POINTER"
export HY2_ROUND3_WORK="$WORK" HY2_ROUND3_SOURCE="$SOURCE_REF"
exec > >(tee -a "$WORK/prepare.log") 2>&1
trap 'printf "第三轮停止：第 %s 行。保留现场：%s\n" "$LINENO" "$WORK" >&2' ERR
date -u '+UTC %Y-%m-%d %H:%M:%S'
echo '=== 1/4 下载固定候选管理器、测试程序与官方核心 ==='
curl -fLsS --retry 2 --connect-timeout 10 --max-time 120 \
  "https://raw.githubusercontent.com/liucong552-art/ceshi/$SOURCE_REF/hy2.sh" -o "$WORK/source/hy2.sh"
for file in hy2_systemd_acceptance.py hy2_loopback.py hy2_test_support.py; do
  curl -fLsS --retry 2 --connect-timeout 10 --max-time 120 \
    "https://raw.githubusercontent.com/liucong552-art/ceshi/$RUNNER_REF/tests/$file" -o "$WORK/source/tests/$file"
done
curl -fLsS --retry 2 --connect-timeout 10 --max-time 180 \
  'https://github.com/HyNetworks/hysteria/releases/download/app/v2.13.0/hysteria-linux-amd64' -o "$WORK/hysteria"
printf '%s  %s\n' "$CORE_SHA" "$WORK/hysteria" | sha256sum -c -
chmod 700 "$WORK/hysteria"
python3 - <<'PY'
import json, os, time
from pathlib import Path
work = Path(os.environ['HY2_ROUND3_WORK'])
(work / 'manifest.json').write_text(json.dumps({'source_ref': os.environ['HY2_ROUND3_SOURCE'],
    'domain': os.environ['HY_DOMAIN'], 'run_id': 'helan-r3-' + str(int(time.time()))}, indent=2))
PY

run_private() {
  local label="$1" log="$2"
  shift 2
  echo "$label（完整输出保存在 root 私有日志）"
  if ! "$@" >"$log" 2>&1; then
    echo "该步骤失败，日志：$log；请先提供当前终端输出，勿粘贴含节点密码的完整私有日志。" >&2
    return 1
  fi
}

echo '=== 2/4 正式安装管理器及系统依赖 ==='
run_private '安装管理器和依赖' "$WORK/bundle-install.private.log" \
  timeout --foreground --kill-after=5s 1200s bash "$WORK/source/hy2.sh" install
python3 - <<'PY'
import os, shlex
from pathlib import Path
work = os.environ['HY2_ROUND3_WORK']
settings = {'HY_DOMAIN': os.environ['HY_DOMAIN'], 'HY_IPV6_DOMAIN': '', 'ACME_EMAIL': os.environ['ACME_EMAIL'],
 'HY_LISTEN': '0.0.0.0:443', 'MASQ_URL': 'https://www.apple.com/', 'ENABLE_SALAMANDER': '0',
 'SALAMANDER_PASSWORD': '', 'NODE_NAME': 'HY2-HELAN-TEST', 'TEMP_PORT_START': '44000', 'TEMP_PORT_END': '44100',
 'HYSTERIA_VERSION': 'v2.13.0', 'HYSTERIA_UPDATE_POLICY': 'install-only',
 'HYSTERIA_LOCAL_BINARY': work + '/hysteria',
 'HYSTERIA_BINARY_SHA256': '907ba8c9693edb104b20582681fb7dc15639d5b64a9cbb616a7b539190a86691',
 'HYSTERIA_INSTALLER_URL': 'https://raw.githubusercontent.com/HyNetworks/hysteria/app/v2.13.0/scripts/install_server.sh',
 'HYSTERIA_INSTALLER_SHA256': '2cc5d1c62f132f30c07e75d972144aa5d61ef59f547a04a537e2d9ac505b4344',
 'ACME_SH_VERSION': '3.1.2', 'ACME_INSTALLER_URL': 'https://raw.githubusercontent.com/acmesh-official/acme.sh/3.1.2/acme.sh',
 'ACME_INSTALLER_SHA256': 'c46b41a61c96f67d424e4b4e476907c964b81d53cf94358a9c1d363a4f99c3a4',
 'REMOTE_SCRIPT_POLICY': 'require'}
path = Path('/etc/default/hy2-main')
path.write_text(''.join(k + '=' + shlex.quote(v) + '\n' for k, v in settings.items()))
path.chmod(0o600)
PY
echo '=== 3/4 安装校验核心、申请 ZeroSSL 正式证书、启动主节点 ==='
run_private '签发正式证书并部署主节点；可能需要几分钟' "$WORK/main-install.private.log" \
  timeout --foreground --kill-after=5s 1200s bash /root/onekey_hy2_main_tls.sh
# Future ordinary deployments should reuse the installed binary.
python3 - <<'PY'
from pathlib import Path
p = Path('/etc/default/hy2-main')
p.write_text(''.join('HYSTERIA_LOCAL_BINARY=\n' if line.startswith('HYSTERIA_LOCAL_BINARY=') else line for line in p.read_text().splitlines(keepends=True)))
PY
echo '=== 4/4 真实 systemd、正式 TLS、临时节点与重启基线检查 ==='
PYTHONDONTWRITEBYTECODE=1 timeout --foreground --kill-after=5s 1200s \
  python3 -B "$WORK/source/tests/hy2_systemd_acceptance.py" prepare --work "$WORK"
printf '报告：%s/prepare.log\n结果：%s/prepare-result.json\n' "$WORK" "$WORK"
echo '前半完成，重启验收尚未执行。下一步会断开当前 SSH：systemctl reboot'
echo '重新连接 Helan 后执行：bash /root/helan_hy2_round3.sh verify'
