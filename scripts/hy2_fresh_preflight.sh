#!/usr/bin/env bash
# Read-only HY2 fresh-VPS checks. No installation, no firewall changes, no output of credentials.
# Deliberately independent of any jieshu / NAT host and of the helan test machine.
set -Eeuo pipefail
umask 077

mode="${1:-}"
case "$mode" in
  before|manager|main|wg) ;;
  -h|--help|help)
    cat <<'USAGE'
用法（HY2 VPS，root）：
  bash hy2_fresh_preflight.sh before  # 全新系统安装 HY2 管理器前
  bash hy2_fresh_preflight.sh manager # hy2.sh 安装完成后
  bash hy2_fresh_preflight.sh main    # 主节点 TLS 部署完成后
  bash hy2_fresh_preflight.sh wg      # 可选：共享 jieshu NAT 对接后

此工具只读、不删除 helan 数据、不安装 NAT、不更改 systemd/WG/nft/portbw。
before 仅适用于尚未安装 HY2 的新 VPS，不是迁移工具。
USAGE
    exit 0
    ;;
  *)
    echo "PRECHECK_FAIL=mode: expected before|manager|main|wg" >&2
    exit 2
    ;;
esac

fail() { printf 'PRECHECK_FAIL=%s\n' "$*" >&2; exit 1; }
pass() { printf 'PASS %s\n' "$*"; }
warn() { printf 'WARN %s\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || fail "missing dependency: $1"; }

[[ "${EUID:-$(id -u)}" -eq 0 ]] || fail "root required"
[[ -f /etc/os-release ]] || fail "/etc/os-release unavailable"
# shellcheck disable=SC1091
source /etc/os-release
need dpkg
case "${ID:-}" in
  debian)
    dpkg --compare-versions "${VERSION_ID:-0}" ge "11" \
      || fail "Debian 11+ required"
    ;;
  ubuntu)
    dpkg --compare-versions "${VERSION_ID:-0}" ge "20.04" \
      || fail "Ubuntu 20.04+ required"
    ;;
  *) fail "only Debian and Ubuntu are supported" ;;
esac
[[ -r /proc/1/comm ]] || fail "PID1 unavailable"
[[ "$(cat /proc/1/comm)" == "systemd" && -d /run/systemd/system ]] \
  || fail "host is not running systemd as PID1"
need systemctl
need bash
pass "supported OS and systemd"
[[ "$mode" == before ]] && need curl

listen_port() {
  local kind="$1" port="$2"
  need ss
  if [[ "$kind" == udp ]]; then
    ss -H -lun 2>/dev/null | awk -v port="$port" '$4 ~ (":" port "$") { found=1 } END { exit !found }'
  else
    ss -H -ltn 2>/dev/null | awk -v port="$port" '$4 ~ (":" port "$") { found=1 } END { exit !found }'
  fi
}

check_manager() {
  local path
  for path in \
    /etc/default/hy2-main \
    /root/onekey_hy2_main_tls.sh \
    /usr/local/sbin/hy2_audit.sh \
    /usr/local/sbin/hy2_doctor.sh \
    /usr/local/sbin/hy2_mktemp.sh \
    /usr/local/sbin/hy2_cleanup_one.sh \
    /usr/local/lib/hy2/quota-lib.sh \
    /usr/local/lib/hy2/iplimit-lib.sh
  do
    [[ -f "$path" ]] || fail "HY2 manager component missing: $path"
  done
  [[ -s /etc/default/hy2-main ]] || fail "HY2 main configuration template empty"
  for unit in hy2-gc.timer pq-save.timer pq-reset.timer hy2-managed-watchdog.timer; do
    systemctl is-active --quiet "$unit" || fail "managed timer inactive: $unit"
  done
  systemctl is-active --quiet hy2-managed-shutdown-save.service \
    || fail "quota shutdown save is not armed"
  pass "HY2 manager installed and timers active"
}

case "$mode" in
  before)
    for path in /etc/default/hy2-main /var/lib/hy2 /etc/hysteria/main.yaml \
                /etc/systemd/system/hy2.service /usr/local/lib/hy2; do
      [[ ! -e "$path" && ! -L "$path" ]] \
        || fail "existing HY2 state found at $path; do not treat as fresh install"
    done
    if command -v ss >/dev/null 2>&1; then
      listen_port udp 443 && fail "UDP/443 already in use"
      listen_port tcp 80 && fail "TCP/80 already in use; standalone ACME needs this port"
      pass "local TCP/80 and UDP/443 appear unoccupied"
    else
      warn "ss not installed yet: cannot inspect TCP/80 or UDP/443"
    fi
    pass "fresh HY2 state is absent"
    ;;
  manager)
    check_manager
    ;;
  main)
    check_manager
    [[ -s /etc/hysteria/main.yaml ]] || fail "main Hysteria configuration missing"
    [[ -s /root/hy2_main_url.txt ]] || fail "main connection URL file missing"
    systemctl is-active --quiet hy2.service || fail "hy2.service inactive"
    listen_port udp 443 || fail "UDP/443 listener missing"
    pass "main HY2 listener active (not a remote client connectivity test)"
    ;;
  wg)
    check_manager
    [[ -x /usr/local/sbin/wg_nat_healthcheck.sh ]] \
      || fail "WG-NAT VPS-side helper missing"
    systemctl is-active --quiet wg-quick@wg-nat.service \
      || fail "WG-NAT VPS-side interface inactive"
    # The healthcheck can print public endpoint details, so never echo its output.
    /usr/local/sbin/wg_nat_healthcheck.sh >/dev/null 2>&1 \
      || fail "WG-NAT healthcheck failed: inspect locally without publishing keys"
    pass "WG-NAT client connected to an existing NAT Peer"
    ;;
esac

printf 'FRESH_PREFLIGHT_%s=PASS\n' "${mode^^}"
printf 'READ_ONLY=YES\n'
