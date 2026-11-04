#!/usr/bin/env bash
# Keep-cycle reset for an existing managed HY2 temporary node.
set -Eeuo pipefail
trap 'echo "❌ ${BASH_SOURCE[0]}:${LINENO}: ${BASH_COMMAND}" >&2' ERR
umask 077
source /usr/local/lib/hy2/common.sh
source /usr/local/lib/hy2/quota-lib.sh

[[ "$(id -u)" -eq 0 ]] || hy2_die "需要 root"
PORT="${1:-}"
GIB="${2:-}"
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] && (( PORT >= 1 && PORT <= 65535 )) \
  || hy2_die "用法: pq_keepcycle.sh <临时端口> <新配额GiB>"
[[ -n "$GIB" ]] || hy2_die "用法: pq_keepcycle.sh <临时端口> <新配额GiB>"
BYTES="$(hy2_parse_gib_to_bytes "$GIB")" || hy2_die "新配额必须为正 GiB"
[[ "$BYTES" =~ ^[0-9]+$ ]] && (( BYTES > 0 )) || hy2_die "非法配额"

hy2_ensure_runtime_dirs
hy2_acquire_lock_fd 7 "${HY2_LOCK_DIR}/temp.lock" 120 "temp 锁繁忙"
export HY2_TEMP_LOCK_HELD=1
hy2_pq_lock

TEMP_META="$(hy2_temp_meta_by_port "$PORT")"
[[ -n "$TEMP_META" && -f "$TEMP_META" ]] || hy2_die "不是受管 HY2 临时节点"
TAG="$(hy2_meta_get "$TEMP_META" TAG)"
Q="$(hy2_quota_meta_file "$PORT")"
[[ -f "$Q" ]] || hy2_die "此节点没有配额；使用 pq_add.sh"
[[ "$(hy2_meta_get "$Q" PORT)" == "$PORT" \
 && "$(hy2_meta_get "$Q" OWNER_KIND)" == "temp" \
 && "$(hy2_meta_get "$Q" OWNER_TAG)" == "$TAG" ]] \
 || hy2_die "配额归属与节点不符"
ORIGINAL="$(hy2_meta_get "$Q" ORIGINAL_LIMIT_BYTES)"
EXPECTED="$(hy2_meta_get "$TEMP_META" PQ_LIMIT_BYTES)"
[[ "$ORIGINAL" =~ ^[0-9]+$ && "$ORIGINAL" == "$EXPECTED" ]] \
 || hy2_die "节点与配额额度不一致"
START="$(hy2_meta_get "$TEMP_META" CREATE_EPOCH)"
DURATION="$(hy2_meta_get "$TEMP_META" DURATION_SECONDS)"
EXPIRE="$(hy2_meta_get "$TEMP_META" EXPIRE_EPOCH)"
INTERVAL="$(hy2_meta_get "$Q" RESET_INTERVAL_SECONDS)"
NEXT="$(hy2_meta_get "$Q" NEXT_RESET_EPOCH)"
Q_CREATED="$(hy2_meta_get "$Q" CREATED_EPOCH)"
for N in "$START" "$DURATION" "$EXPIRE" "$INTERVAL" "$NEXT" "$Q_CREATED"; do
  [[ "$N" =~ ^[0-9]+$ ]] || hy2_die "时间元数据异常"
done
NOW="$(date +%s)"
(( START > 0 && START <= NOW && DURATION > 0 && EXPIRE > NOW \
   && EXPIRE == START + DURATION )) || hy2_die "节点已到期或时间数据异常"
case "$(hy2_pq_state "$PORT")" in
  active|exhausted) ;;
  *) hy2_die "现有 nft 配额异常，拒绝重设" ;;
esac
if (( INTERVAL == 0 && NEXT == 0 )); then
  if (( DURATION > 2592000 )); then
    INTERVAL=2592000
    NEXT=$(( START + ((NOW - START) / INTERVAL + 1) * INTERVAL ))
  fi
elif (( INTERVAL > 0 && NEXT > 0 )); then
  if (( NEXT <= NOW )); then
    NEXT=$(( NEXT + ((NOW - NEXT) / INTERVAL + 1) * INTERVAL ))
  fi
else
  hy2_die "重置周期字段不完整，拒绝修改"
fi
BACKUP="$(mktemp -d "/root/pq-keepcycle-${PORT}-XXXXXXXX")"
chmod 700 "$BACKUP"
cp -p -- "$Q" "$BACKUP/quota.env"
cp -p -- "$TEMP_META" "$BACKUP/node.env"
echo "原配额和节点数据备份：${BACKUP}"
(
  trap '' INT TERM HUP
  hy2_pq_write_meta "$PORT" "$BYTES" 0 "$BYTES" temp "$TAG" "$DURATION" "$EXPIRE" \
    "$NEXT" "$INTERVAL" "$Q_CREATED" "$NOW" "$NOW" 1
  hy2_pq_rebuild_port "$PORT" "$BYTES"
  hy2_meta_upsert "$TEMP_META" PQ_LIMIT_BYTES "$BYTES"
  hy2_meta_upsert "$TEMP_META" PQ_GIB "$GIB"
  hy2_meta_upsert "$Q" REBUILD_PENDING 0
) || hy2_die "重设失败；保留备份和待重建标记，请检查 doctor/pq_audit.sh"
echo "✅ 端口 ${PORT} 重设为 $(hy2_human_bytes "$BYTES")，已用流量清零"
if (( INTERVAL > 0 )); then
  TZ=Asia/Shanghai date -d "@$NEXT" "+下次自动重置（北京时间）：%F %T"
else
  echo "此节点无周期，保持无周期"
fi
