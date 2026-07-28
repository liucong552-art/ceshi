#!/usr/bin/env bash
# HY2 five-file edition v3: install the WG-NAT temporary-node entrypoint.
set -Eeuo pipefail
umask 077

HY2_BUNDLE_VERSION="3.3.1"
HY2_BUNDLE_DATE="2026-07-27"
case "${1:-}" in
  version|--version|-V) echo "HY2 natjichang ${HY2_BUNDLE_VERSION} (${HY2_BUNDLE_DATE})"; exit 0 ;;
  check|doctor)
    [[ -x /usr/local/sbin/hy2_doctor.sh ]] && /usr/local/sbin/hy2_doctor.sh || true
    [[ -x /usr/local/sbin/wg_nat_healthcheck.sh ]] || { echo "❌ 缺少 wg_nat_healthcheck.sh" >&2; exit 1; }
    exec /usr/local/sbin/wg_nat_healthcheck.sh
    ;;
esac

HY2_LIB="/usr/local/lib/hy2/common.sh"
HY2_CREATOR="/usr/local/sbin/hy2_mktemp.sh"
PREFLIGHT_TARGET="/usr/local/sbin/hy2_nat_preflight.sh"
CREATOR_TARGET="/usr/local/sbin/hy2_mktemp_nat.sh"
WG_IF="${WG_IF:-wg-nat}"

TX_ACTIVE=0
TX_DIR=""
TARGETS=("$PREFLIGHT_TARGET" "$CREATOR_TARGET")
TEMP_FILES=()

fail() {
  echo "❌ $*" >&2
  exit 1
}

need_root() {
  [[ ${EUID:-0} -eq 0 ]] || fail "请用 root 运行"
}

validate_ifname() {
  local value="$1"
  [[ -n "$value" && ${#value} -le 15 && "$value" =~ ^[A-Za-z0-9_.-]+$ \
     && "$value" != "." && "$value" != ".." ]] || fail "WG_IF 非法：${value}"
}

begin_transaction() {
  local i target
  TX_DIR="$(mktemp -d /var/tmp/hy2-nat-module-transaction.XXXXXX)"
  for i in "${!TARGETS[@]}"; do
    target="${TARGETS[$i]}"
    if [[ -e "$target" || -L "$target" ]]; then
      cp -a -- "$target" "${TX_DIR}/${i}.backup"
      : >"${TX_DIR}/${i}.present"
    fi
  done
  TX_ACTIVE=1
}

rollback_transaction() {
  (( TX_ACTIVE == 1 )) || return 0
  TX_ACTIVE=0
  set +e
  local i target
  for target in "${TEMP_FILES[@]}"; do
    [[ "$target" == /usr/local/sbin/.hy2_nat_preflight.* \
       || "$target" == /usr/local/sbin/.hy2_mktemp_nat.* ]] && rm -f -- "$target"
  done
  for i in "${!TARGETS[@]}"; do
    target="${TARGETS[$i]}"
    rm -f -- "$target"
    if [[ -f "${TX_DIR}/${i}.present" ]]; then
      cp -a -- "${TX_DIR}/${i}.backup" "$target"
    fi
  done
  [[ -n "$TX_DIR" && "$TX_DIR" == /var/tmp/hy2-nat-module-transaction.* ]] && rm -rf -- "$TX_DIR"
  TX_DIR=""
  set -e
}

commit_transaction() {
  local target
  for target in "${TEMP_FILES[@]}"; do
    [[ "$target" == /usr/local/sbin/.hy2_nat_preflight.* \
       || "$target" == /usr/local/sbin/.hy2_mktemp_nat.* ]] && rm -f -- "$target"
  done
  TX_ACTIVE=0
  [[ -n "$TX_DIR" && "$TX_DIR" == /var/tmp/hy2-nat-module-transaction.* ]] && rm -rf -- "$TX_DIR"
  TX_DIR=""
}

on_error() {
  local rc="$1" line="$2" command="$3"
  trap - ERR
  rollback_transaction
  echo "❌ natjichang.sh:${line}: ${command}（退出码 ${rc}）" >&2
  exit "$rc"
}

on_signal() {
  local rc="$1"
  trap - ERR INT TERM HUP
  rollback_transaction
  exit "$rc"
}

trap 'on_error "$?" "$LINENO" "$BASH_COMMAND"' ERR
trap 'on_signal 130' INT
trap 'on_signal 143' TERM
trap 'on_signal 129' HUP

need_root
validate_ifname "$WG_IF"
command -v flock >/dev/null 2>&1 || fail "缺少 flock"
[[ -r "$HY2_LIB" ]] || fail "缺少 ${HY2_LIB}；请先运行同目录的 hy2.sh"
[[ -x "$HY2_CREATOR" ]] || fail "缺少 ${HY2_CREATOR}；请先运行同目录的 hy2.sh"
grep -q 'LANDING="${LANDING:-local}"' "$HY2_CREATOR" \
  || fail "当前 hy2_mktemp.sh 不支持五文件版 NAT，请重新运行同目录的 hy2.sh"
grep -q 'bindDevice' "$HY2_LIB" \
  || fail "当前 HY2 公共库不支持 WireGuard 绑定出口，请重新运行同目录的 hy2.sh"
[[ -x /usr/local/sbin/wg_nat_guard.sh && -x /usr/local/sbin/wg_nat_healthcheck.sh ]] \
  || fail "WireGuard NAT 组件不完整；请先运行同目录的 vpswg.sh"
[[ -f "/etc/wireguard/${WG_IF}.env" ]] \
  || fail "缺少 /etc/wireguard/${WG_IF}.env；请先完成 vpswg.sh 与 NAT 机 Peer 回填"

install -d -m 755 /usr/local/sbin /run/lock /run/hy2 /run/hy2-wg

# 全局锁顺序固定为 HY2 temp -> WG state，和创建、清理脚本一致。
exec 7>/run/hy2/temp.lock
flock -w 120 7 || fail "HY2 临时节点创建/清理任务仍在运行"
exec 8>/run/hy2-wg/temp.lock
flock -w 120 8 || fail "WG-NAT 管理任务仍在运行"
exec 9>/run/lock/hy2-nat-module-install.lock
flock -w 120 9 || fail "另一个 natjichang.sh 正在运行"

begin_transaction

PREFLIGHT_TMP="$(mktemp /usr/local/sbin/.hy2_nat_preflight.XXXXXX)"
CREATOR_TMP="$(mktemp /usr/local/sbin/.hy2_mktemp_nat.XXXXXX)"
TEMP_FILES=("$PREFLIGHT_TMP" "$CREATOR_TMP")

cat >"$PREFLIGHT_TMP" <<'PREFLIGHT'
#!/usr/bin/env bash
set -Eeuo pipefail
trap 'echo "❌ ${BASH_SOURCE[0]}:${LINENO}: ${BASH_COMMAND}" >&2' ERR

# shellcheck disable=SC1091
source /usr/local/lib/hy2/common.sh

TAG="${1:?need TAG}"
hy2_is_valid_temp_tag "$TAG" || hy2_die "非法临时节点 TAG：${TAG}"
META="$(hy2_temp_meta_file "$TAG")"
[[ -f "$META" ]] || hy2_die "meta 不存在：${META}"
[[ "$(hy2_meta_get "$META" LANDING 2>/dev/null || true)" == "nat" ]] \
  || hy2_die "节点不是 NAT 落地类型"

WG_IF="$(hy2_meta_get "$META" WG_IF 2>/dev/null || true)"
MARK="$(hy2_meta_get "$META" MARK 2>/dev/null || true)"
TABLE_ID="$(hy2_meta_get "$META" TABLE_ID 2>/dev/null || true)"
RULE_PRIORITY="$(hy2_meta_get "$META" RULE_PRIORITY 2>/dev/null || true)"
OIF_RULE_PRIORITY="$(hy2_meta_get "$META" OIF_RULE_PRIORITY 2>/dev/null || true)"
HANDSHAKE_MAX="$(hy2_meta_get "$META" HANDSHAKE_MAX 2>/dev/null || true)"
HANDSHAKE_MAX="${HANDSHAKE_MAX:-180}"

[[ -n "$WG_IF" && ${#WG_IF} -le 15 && "$WG_IF" =~ ^[A-Za-z0-9_.-]+$ \
   && "$WG_IF" != "." && "$WG_IF" != ".." ]] || hy2_die "WG_IF 非法"
[[ "$MARK" =~ ^[0-9]+$ && "$TABLE_ID" =~ ^[0-9]+$ \
   && "$RULE_PRIORITY" =~ ^[0-9]+$ && "$OIF_RULE_PRIORITY" =~ ^[0-9]+$ \
   && "$HANDSHAKE_MAX" =~ ^[0-9]+$ ]] || hy2_die "NAT meta 缺少有效策略路由参数"
(( OIF_RULE_PRIORITY >= 1 && OIF_RULE_PRIORITY <= 32765 )) \
  || hy2_die "OIF_RULE_PRIORITY 非法"
(( HANDSHAKE_MAX >= 1 && HANDSHAKE_MAX <= 86400 )) \
  || hy2_die "HANDSHAKE_MAX 非法"

install -d -m 755 /run/lock /run/hy2-wg
exec 9>/run/hy2-wg/temp.lock
flock -w 120 9 || hy2_die "WG-NAT 管理任务仍在运行"

WG_STATE_FILE="/etc/wireguard/${WG_IF}.env"
[[ -f "$WG_STATE_FILE" ]] || hy2_die "缺少 ${WG_STATE_FILE}"
[[ "$(stat -c %u "$WG_STATE_FILE" 2>/dev/null || echo -1)" == "0" ]] \
  || hy2_die "${WG_STATE_FILE} 必须属于 root"
WG_STATE_MODE="$(stat -c %a "$WG_STATE_FILE" 2>/dev/null || echo 777)"
[[ "$WG_STATE_MODE" =~ ^[0-7]{3,4}$ ]] \
  && (( ((8#$WG_STATE_MODE) & 8#022) == 0 )) \
  || hy2_die "${WG_STATE_FILE} 不能被 group/other 写入"

CURRENT_MARK="$(sed -n 's/^MARK=//p' "$WG_STATE_FILE" | head -n1)"
CURRENT_TABLE_ID="$(sed -n 's/^TABLE_ID=//p' "$WG_STATE_FILE" | head -n1)"
CURRENT_RULE_PRIORITY="$(sed -n 's/^RULE_PRIORITY=//p' "$WG_STATE_FILE" | head -n1)"
[[ "$CURRENT_MARK" == "$MARK" && "$CURRENT_TABLE_ID" == "$TABLE_ID" \
   && "$CURRENT_RULE_PRIORITY" == "$RULE_PRIORITY" ]] \
  || hy2_die "WG-NAT 策略参数已变化；请删除并重新创建该 NAT 临时节点"

exec 8>"/run/lock/hy2-nat-oif-${WG_IF}.lock"
flock -w 30 8 || hy2_die "${WG_IF} 的 HY2 OIF 路由锁繁忙"

systemctl is-active --quiet "wg-quick@${WG_IF}.service" \
  || hy2_die "wg-quick@${WG_IF} 未运行"
[[ -x /usr/local/sbin/wg_nat_guard.sh ]] || hy2_die "缺少 wg_nat_guard.sh"
/usr/local/sbin/wg_nat_guard.sh "$WG_IF"

RULE_LINES="$(ip -4 rule show | awk -v p="${OIF_RULE_PRIORITY}:" '$1 == p {print}')"
if [[ -n "$RULE_LINES" ]]; then
  while IFS= read -r line; do
    [[ "$line" == *" oif ${WG_IF} "* && "$line" == *" lookup ${TABLE_ID}"* ]] \
      || hy2_die "OIF_RULE_PRIORITY=${OIF_RULE_PRIORITY} 被其他策略占用：${line}"
  done <<<"$RULE_LINES"
else
  ip -4 rule add priority "$OIF_RULE_PRIORITY" oif "$WG_IF" lookup "$TABLE_ID"
fi

ROUTE_RESULT="$(ip -4 route get 1.1.1.1 oif "$WG_IF" 2>/dev/null || true)"
[[ "$ROUTE_RESULT" == *" dev ${WG_IF} "* || "$ROUTE_RESULT" == *" dev ${WG_IF}" ]] \
  || hy2_die "绑定 ${WG_IF} 的连接未走 WireGuard 策略表 ${TABLE_ID}"

HS="$(wg show "$WG_IF" latest-handshakes 2>/dev/null \
  | awk 'NF>=2{print $2}' | sort -nr | head -n1 || true)"
[[ "$HS" =~ ^[0-9]+$ ]] && (( HS > 0 && $(date +%s) - HS <= HANDSHAKE_MAX )) \
  || hy2_die "WireGuard 握手不存在或已超过 ${HANDSHAKE_MAX}s"
PREFLIGHT

cat >"$CREATOR_TMP" <<'CREATOR'
#!/usr/bin/env bash
set -Eeuo pipefail
export LANDING=nat
exec /usr/local/sbin/hy2_mktemp.sh "$@"
CREATOR

chmod 755 "$PREFLIGHT_TMP" "$CREATOR_TMP"
bash -n "$PREFLIGHT_TMP"
bash -n "$CREATOR_TMP"
mv -f -- "$PREFLIGHT_TMP" "$PREFLIGHT_TARGET"
mv -f -- "$CREATOR_TMP" "$CREATOR_TARGET"

commit_transaction
trap - ERR INT TERM HUP

cat <<DONE
==================================================
✅ HY2 WG-NAT 临时节点模块安装完成

已生成：
- ${PREFLIGHT_TARGET}
- ${CREATOR_TARGET}

创建示例：
  id=nat4 IP_VERSION=4 IP_LIMIT=1 PQ_GIB=1 D=1200 hy2_mktemp_nat.sh
  id=nat6 IP_VERSION=6 IP_LIMIT=1 PQ_GIB=1 D=1200 hy2_mktemp_nat.sh

默认 WireGuard 接口：${WG_IF}
审计：hy2_audit.sh
==================================================
DONE
