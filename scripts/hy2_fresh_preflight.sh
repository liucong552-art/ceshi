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
  bash hy2_fresh_preflight.sh wg      # 可选：共享 jieshu NAT 对接后，只读状态核验（不做出口请求）

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
                /etc/hysteria/config.yaml /etc/systemd/system/hy2.service \
                /etc/systemd/system/hysteria-server.service /usr/local/lib/hy2; do
      [[ ! -e "$path" && ! -L "$path" ]] \
        || fail "existing HY2 state found at $path; do not treat as fresh install"
    done
    # Never report a clean install if the original upstream Hysteria is
    # already present outside this manager's own state layout.
    if command -v hysteria >/dev/null 2>&1; then
      fail "existing Hysteria executable found; not a blank VPS"
    fi
    if systemctl list-unit-files --no-legend hysteria-server.service 2>/dev/null \
         | grep -q '^hysteria-server.service'; then
      fail "existing upstream Hysteria service found; not a blank VPS"
    fi
    # Fail closed rather than silently skipping occupied-port checks.
    need ss
    listen_port udp 443 && fail "UDP/443 already in use"
    listen_port tcp 80 && fail "TCP/80 already in use; standalone ACME needs this port"
    pass "local TCP/80 and UDP/443 appear unoccupied"
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
    need wg
    need ip
    [[ -r /etc/wireguard/wg-nat.env ]] \
      || fail "WG-NAT state file missing"
    systemctl is-active --quiet wg-quick@wg-nat.service \
      || fail "WG-NAT VPS-side interface inactive"

    # Passive inspection only. Installed wg_nat_healthcheck.sh is NOT
    # read-only: it calls sysctl -w and wg_nat_guard.sh to repair rules.
    # Never invoke that helper from the READ_ONLY=YES path.
    local_mark="$(sed -n 's/^MARK=//p' /etc/wireguard/wg-nat.env | head -n1)"
    [[ "$local_mark" =~ ^(0[xX][0-9a-fA-F]+|[0-9]+)$ ]] \
      || fail "WG-NAT state has an invalid mark"
    peers="$(wg show wg-nat peers 2>/dev/null)" \
      || fail "cannot inspect wg-nat peers"
    [[ -n "$peers" ]] || fail "no WG-NAT peer is configured"
    hs="$(wg show wg-nat latest-handshakes 2>/dev/null)" \
      || fail "cannot inspect WG-NAT handshakes"
    now="$(date +%s)"
    handshake_max="${HANDSHAKE_MAX:-180}"
    [[ "$handshake_max" =~ ^[0-9]{1,5}$ ]] \
      && (( handshake_max >= 1 && handshake_max <= 86400 )) \
      || fail "HANDSHAKE_MAX must be 1..86400 seconds"
    awk -v now="$now" -v max="$handshake_max" \
      'NF >= 2 && $2 ~ /^[0-9]+$/ && $2 > 0 &&
       now >= $2 && now - $2 <= max { fresh=1 }
       END { exit !fresh }' <<<"$hs" \
      || fail "WG-NAT has no recent handshake"

    # Route lookup queries the kernel and sends no network packets.
    route="$(ip -4 route get 1.1.1.1 mark "$local_mark" 2>/dev/null)" \
      || fail "cannot inspect marked WG-NAT route"
    [[ "$route" == *" dev wg-nat "* || "$route" == *" dev wg-nat" ]] \
      || fail "marked route does not point to wg-nat"

    # Inspect the fail-closed state WITHOUT invoking the guard's repair path.
    table_id="$(sed -n 's/^TABLE_ID=//p' /etc/wireguard/wg-nat.env | head -n1)"
    [[ "$table_id" =~ ^[0-9]{1,10}$ ]] \
      || fail "WG-NAT policy table ID missing or invalid"
    routes="$(ip -4 route show table "$table_id" 2>/dev/null)" \
      || fail "cannot inspect WG-NAT policy table"
    grep -qE '^default dev wg-nat([[:space:]]|$)' <<<"$routes" \
      || fail "WG-NAT table has no tunnel default route"
    grep -qE '^prohibit default([[:space:]]|$)' <<<"$routes" \
      || fail "WG-NAT table is missing prohibit fail-closed fallback"
    systemctl is-enabled --quiet wg-nat-guard@wg-nat.timer \
      || fail "WG-NAT fail-closed guard timer not enabled"
    systemctl is-active --quiet wg-nat-guard@wg-nat.timer \
      || fail "WG-NAT fail-closed guard timer not active"
    pass "WG-NAT peer, recent handshake, marked route and passive fail-closed checks"
    warn "actual NAT exit IP / external client connectivity NOT checked by read-only mode"
    ;;
esac

printf 'FRESH_PREFLIGHT_%s=PASS\n' "${mode^^}"
printf 'READ_ONLY=YES\n'
