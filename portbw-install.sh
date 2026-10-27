#!/usr/bin/env bash
# Generic managed candidate installer. Never upgrades an unknown or untracked installation.
# Debian / Ubuntu + systemd; no dependency on VLESS, Xray, WireGuard or a specific VPS.
set -Eeuo pipefail
umask 077
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROG=/usr/local/lib/portbw/portbw.py
CLI=/usr/local/sbin/portbw
CONF=/etc/portbw/config.json
STATE=/var/lib/portbw-resident-release
CURRENT="$STATE/current.env"
SOURCE="$HERE/portbw.py"
PREVIOUS_SHA='69058c8069e78d902c4814e1300c5aa38e2b91340f936cdb9cdc09a14936f359'
PREVIOUS_CANDIDATE_SHA='601423e191fd809cda1bd7cbbab285096178b938a131ce4d9fe99fb83b08495f'
EXPECTED_SHA='66a79fd8ff9f4b00c43a62ef3530251cd6934301bfc5412d497b47d94a3f70d1'
KNOWN_TEST_SHA='f0ebe551e8c0345c045a558b39a8b13523b2be8195255c67db13eec3eff5dc1a'
MAIN_SHA='69058c8069e78d902c4814e1300c5aa38e2b91340f936cdb9cdc09a14936f359'
V1_SHA='29676cc3e07c772cd617ed5e2888a80417144576fd279bc654297e5dcdcaa568'
V2_SHA='96f3d4c5d0f24cec94a8555c0daec98b6b3ac8414457434ef9735eab540f3b23'
UNITS=(portbw-restore.service portbw-watch.service portbw-watch.timer)
PATHS=("$PROG" "$CLI" "$CONF" /etc/systemd/system/portbw-restore.service /etc/systemd/system/portbw-watch.service /etc/systemd/system/portbw-watch.timer "$CURRENT")
ACTION=''
IFACE=''
IFACE_EXPLICIT=0
NFT_ONLY=0

say(){ printf '[portbw-release] %s\n' "$*"; }
fail(){ say "STOP: $*" >&2; exit 1; }
sha(){ sha256sum "$1" | awk '{print $1}'; }
usage(){ cat <<'EOF'
用法：
  sudo bash portbw-install.sh preflight-upgrade [--iface NAME]
  sudo bash portbw-install.sh check [--iface NAME] [--nft-only]
  sudo bash portbw-install.sh install [--iface NAME] [--nft-only]
  sudo bash portbw-install.sh verify
  sudo bash portbw-install.sh upgrade [--iface NAME]
  sudo bash portbw-install.sh rollback

仅面向 Debian/Ubuntu + systemd。portbw.py 必须和此安装器在同一目录。
固定 SHA 校验通用发布候选版；适用 Debian/Ubuntu + systemd，不绑定主机名。
首次安装需无旧portbw程序、端口策略、模块专用nft表；不静默迁移别人的安装。
仅升级管理记录与当前文件一致的本系列已知版本；持久备份并支持回滚。
没有--iface时自动找默认路由的物理网卡；若默认路由走WG/tun，必须显式--iface。
EOF
}
if (($#));then ACTION="$1";shift;else usage;exit 2;fi
case "$ACTION" in -h|--help|help)usage;exit 0;;check|install|verify|upgrade|rollback|preflight-upgrade);;*)usage;fail "未知操作：$ACTION";;esac
while (($#));do
  case "$1" in
    --iface) (($# >= 2)) || fail '--iface缺少网卡';IFACE="$2";IFACE_EXPLICIT=1;shift 2;;
    --iface=*)IFACE="${1#*=}";IFACE_EXPLICIT=1;shift;;
    --nft-only)NFT_ONLY=1;shift;;
    *)fail "未知参数：$1";;
  esac
done
if [[ "$ACTION" == verify || "$ACTION" == rollback ]];then
  (( IFACE_EXPLICIT==0 && NFT_ONLY==0 )) || fail "$ACTION不接受网卡/模式参数"
fi
if [[ "$ACTION" == preflight-upgrade && "$NFT_ONLY" == 1 ]];then fail '预检升级不支持--nft-only';fi
if [[ "$ACTION" == upgrade && "$NFT_ONLY" == 1 ]];then fail '已有策略升级不支持切换到--nft-only';fi

source_check(){
  [[ -f "$SOURCE" && ! -L "$SOURCE" ]] || fail "缺少同目录的portbw.py，或文件为符号链接：$SOURCE"
  [[ "$(sha "$SOURCE")" == "$EXPECTED_SHA" ]] || fail "portbw.py 与候选版目标SHA不一致；目标SHA=$EXPECTED_SHA，当前SHA=$(sha "$SOURCE")"
  python3 -B - "$SOURCE" <<'PY' || fail 'Python源码语法检查失败'
import ast,sys
with open(sys.argv[1],encoding='utf-8') as f:ast.parse(f.read())
PY
}
host_check(){
  [[ ${EUID:-$(id -u)} -eq 0 ]] || fail '必须使用root或sudo'
  [[ -f /etc/os-release ]] || fail '无法读取/etc/os-release'
  # shellcheck disable=SC1091
  . /etc/os-release
  case "${ID:-}" in debian|ubuntu);;*)fail "仅支持Debian/Ubuntu；当前ID=${ID:-unknown}";;esac
  [[ -d /run/systemd/system ]] || fail '系统PID1必须是systemd'
  local cmd
  for cmd in python3 sha256sum ip nft tc ss systemctl install awk flock mktemp cp mv readlink;do
    command -v "$cmd" >/dev/null 2>&1 || fail "缺少依赖：$cmd（请先通过发行版软件源安装；本脚本不自动apt）"
  done
  source_check
}
read_managed(){
  [[ -f "$CURRENT" && ! -L "$CURRENT" ]] || fail "不存在通用安装器管理记录 $CURRENT；拒绝改写别的版本"
  local old
  old="$(sed -n 's/^source_sha=//p' "$CURRENT")"
  [[ "$old" == "$V1_SHA" || "$old" == "$V2_SHA" || "$old" == "$PREVIOUS_SHA" || "$old" == "$PREVIOUS_CANDIDATE_SHA" || "$old" == "$EXPECTED_SHA" || "$old" == "$KNOWN_TEST_SHA" ]] || fail '当前安装记录不是已知 v1/v2/前一版/本候选版；拒绝覆盖'
  [[ -f "$PROG" && ! -L "$PROG" && "$(sha "$PROG")" == "$old" ]] || fail '当前已安装源码与管理记录不匹配；拒绝升级或回滚'
  [[ -L "$CLI" && "$(readlink "$CLI")" == "$PROG" ]] || fail '命令入口不是预期的portbw符号链接'
}
read_current_iface(){
  [[ -f "$CONF" && ! -L "$CONF" ]] || fail '已有安装缺少合法配置文件'
  python3 -B - "$CONF" <<'PY'
import json,sys,re
obj=json.load(open(sys.argv[1],encoding='utf-8'))
s=obj.get('iface')
if not isinstance(s,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,15}',s) or s in ('.','..'):raise SystemExit(2)
print(s)
PY
}
resolve_iface(){
  if [[ -z "$IFACE" && -e "$CURRENT" ]];then
    IFACE="$(read_current_iface)" || fail '已有配置的网卡字段无效'
  fi
  if [[ -z "$IFACE" ]];then
    local route=''
    route="$(ip -4 route get 1.1.1.1 2>/dev/null || true)"
    [[ "$route" =~ [[:space:]]dev[[:space:]]([a-zA-Z0-9_.-]+) ]] || route="$(ip -6 route get 2606:4700:4700::1111 2>/dev/null || true)"
    if [[ "$route" =~ [[:space:]]dev[[:space:]]([a-zA-Z0-9_.-]+) ]];then
      IFACE="${BASH_REMATCH[1]}"
    else
      fail '无法识别默认路由网卡；请显式--iface指定实际网卡'
    fi
    case "$IFACE" in lo|wg*|tun*|tap*|tailscale*|docker*|veth*|br-*)fail "自动检测到了虚拟/隧道接口 $IFACE；请明确指定要管理的网卡 --iface NAME";;esac
  fi
  [[ "$IFACE" =~ ^[a-zA-Z0-9_.-]{1,15}$ && "$IFACE" != . && "$IFACE" != .. ]] || fail "非法网卡：$IFACE"
  ip link show dev "$IFACE" >/dev/null 2>&1 || fail "不存在网卡：$IFACE"
}
clean_check(){
  host_check
  local p u
  for p in "${PATHS[@]}";do
    [[ ! -e "$p" && ! -L "$p" ]] || fail "发现既有portbw文件/配置，拒绝覆盖：$p"
  done
  for u in "${UNITS[@]}";do
    [[ "$(systemctl is-active "$u" 2>/dev/null || true)" != active ]] || fail "已有活动portbw服务：$u"
    local frag
    frag="$(systemctl show "$u" -p FragmentPath --value 2>/dev/null || true)"
    [[ -z "$frag" ]] || fail "已有系统内portbw unit：$u ($frag)"
  done
  if [[ -d /var/lib/portbw/ports ]];then
    ! find /var/lib/portbw/ports -maxdepth 1 -name '*.json' -print -quit | grep -q . || fail '已有端口策略，拒绝首次安装覆盖'
  fi
  local nft_tables
  nft_tables="$(nft list tables)" || fail '无法查询nft tables；不能判断是否与旧规则冲突'
  if printf '%s\n' "$nft_tables" | grep -Fxq 'table inet pbw_policy';then
    fail '已有portbw专用nft表，拒绝按干净系统安装'
  fi
  resolve_iface
  say "CHECK PASS: 新安装环境，绑定网卡=$IFACE，模式=$([[ "$NFT_ONLY" == 1 ]] && echo nft-only || echo nft+tc)；未修改系统"
}

BACKUP=''
COMMITTED=0
back_up(){
  local tag="$1" i p u
  install -d -m 0700 "$STATE"
  BACKUP="$(mktemp -d "$STATE/backup-${tag}-XXXXXXXX")"
  for i in "${!PATHS[@]}";do
    p="${PATHS[$i]}"
    if [[ -e "$p" || -L "$p" ]];then cp -a -- "$p" "$BACKUP/$i.old";fi
  done
  for u in "${UNITS[@]}";do
    printf '%s\n' "$(systemctl is-enabled "$u" 2>/dev/null || true)" > "$BACKUP/$u.enabled"
    printf '%s\n' "$(systemctl is-active "$u" 2>/dev/null || true)" > "$BACKUP/$u.active"
  done
  # For forensic recovery ONLY; restore never silently overwrites live policy
  # state files, which may contain newer administrator decisions.
  if [[ -d /var/lib/portbw/ports ]];then
    cp -a -- /var/lib/portbw/ports "$BACKUP/ports.snapshot"
  fi
  say "备份已持久保存（不会自动删除）：$BACKUP"
}
restore_backup(){
  local b="$1" i p u old_enabled old_active
  [[ "$b" == "$STATE"/backup-* && -d "$b" ]] || { say '备份路径无效';return 1; }
  # Validate a managed release backup BEFORE stopping anything or replacing
  # installed files. A missing/corrupted backup is NOT a safe rollback.
  if [[ -e "$b/0.old" || -L "$b/0.old" ]];then
    [[ -f "$b/0.old" && ! -L "$b/0.old" && -f "$b/6.old" ]] || { say '备份缺少有效旧程序/管理记录';return 1; }
    local oldsha
    oldsha="$(sed -n 's/^source_sha=//p' "$b/6.old")"
    [[ "$oldsha" == "$V1_SHA" || "$oldsha" == "$V2_SHA" || "$oldsha" == "$PREVIOUS_SHA" || "$oldsha" == "$PREVIOUS_CANDIDATE_SHA" || "$oldsha" == "$EXPECTED_SHA" || "$oldsha" == "$KNOWN_TEST_SHA" ]] || { say '备份版本标记不是已知发行版';return 1; }
    [[ "$(sha "$b/0.old")" == "$oldsha" ]] || { say '备份程序 SHA 校验失败；拒绝回滚';return 1; }
    [[ -L "$b/1.old" && "$(readlink "$b/1.old")" == "$PROG" ]] || { say '旧 CLI 不是预期符号链接；拒绝回滚';return 1; }
  fi
  say "恢复备份：$b"
  # Never overwrite a binary if systemd may still have it running.
  local u
  for u in portbw-watch.timer portbw-watch.service portbw-restore.service;do
    local active_state
    active_state="$(systemctl show "$u" -p ActiveState --value 2>/dev/null || true)"
    case "$active_state" in
      active|activating|reloading|deactivating)
        timeout 150 systemctl stop "$u" || { say "无法停止 $u，拒绝在线覆盖回滚文件";return 1; } ;;
    esac
  done
  systemctl disable portbw-watch.service portbw-watch.timer portbw-restore.service >/dev/null 2>&1 || true
  for i in "${!PATHS[@]}";do
    p="${PATHS[$i]}"
    rm -f -- "$p"
    if [[ -e "$b/$i.old" || -L "$b/$i.old" ]];then
      install -d -m 0755 "$(dirname "$p")"
      cp -a -- "$b/$i.old" "$p" || return 1
    fi
  done
  systemctl daemon-reload || return 1
  for u in "${UNITS[@]}";do
    old_enabled="$(cat "$b/$u.enabled")"
    case "$old_enabled" in
      enabled|linked)systemctl enable "$u" >/dev/null 2>&1 || return 1;;
      enabled-runtime|linked-runtime)systemctl enable --runtime "$u" >/dev/null 2>&1 || return 1;;
    esac
  done
  # No simultaneous timer/service restart; old unit state is authoritative.
  for u in portbw-restore.service portbw-watch.service portbw-watch.timer;do
    old_active="$(cat "$b/$u.active")"
    if [[ "$old_active" == active ]];then systemctl start --no-block "$u" || return 1;fi
  done
  say '程序、CLI、配置和unit已恢复；现有端口策略与内核限速不清空'
}
transaction_exit(){
  local rc=$?
  trap - EXIT
  if (( COMMITTED == 0 )) && [[ -n "$BACKUP" ]];then
    restore_backup "$BACKUP" || say '自动回滚不完整，请保留备份人工检查'
    say '首次安装失败后可能保留空pbw_policy表/clsact，避免误删除其他内核规则'
  fi
  exit "$rc"
}
write_marker(){
  local backup="$1" tmp="$CURRENT.new.$$"
  printf 'source_sha=%s\nbackup=%s\n' "$EXPECTED_SHA" "$backup" > "$tmp"
  chmod 0600 "$tmp"
  mv -f -- "$tmp" "$CURRENT"
}
verify(){
  host_check
  read_managed
  [[ "$(sha "$PROG")" == "$EXPECTED_SHA" ]] || fail '运行版本不是这份稳定版；请用对应发行包验证'
  [[ -x "$PROG" ]] || fail '已安装Python不可执行'
  python3 -B - "$PROG" <<'PY' || fail 'systemd unit与此源码生成内容不一致'
import pathlib,runpy,sys
m=runpy.run_path(sys.argv[1],run_name='portbw_release_verify')
u=m['units']()
assert set(u)=={'portbw-restore.service','portbw-watch.service'}
for n,b in u.items():assert pathlib.Path('/etc/systemd/system',n).read_text()==b,n
print('原生稳定版systemd units内容一致：PASS')
PY
  [[ "$(systemctl show portbw-watch.service -p Type --value)" == notify ]] || fail 'service Type不等于notify'
  [[ "$(systemctl show portbw-watch.service -p Restart --value)" == always ]] || fail 'service Restart不等于always'
  [[ "$(systemctl show portbw-watch.service -p StartLimitIntervalUSec --value)" == 0 ]] || fail 'StartLimitIntervalSec未解除'
  [[ "$(systemctl show portbw-watch.service -p CPUAccounting --value)" == yes ]] || fail 'CPUAccounting未启用'
  local wd
  wd="$(systemctl show portbw-watch.service -p WatchdogUSec --value)"
  [[ "$wd" != 0 && -n "$wd" ]] || fail 'systemd Watchdog未启用'
  [[ "$(systemctl is-active portbw-watch.timer 2>/dev/null || true)" != active ]] || fail '检测到旧timer仍运行'
  systemctl is-enabled --quiet portbw-watch.service || fail '开机启动未启用'
  local n pid ok=0
  for n in {1..45};do
    if systemctl is-active --quiet portbw-watch.service;then
      pid="$(systemctl show portbw-watch.service -p MainPID --value)"
      if python3 -B - "$pid" "$EXPECTED_SHA" <<'PY'
import json,sys,time
try:
 d=json.load(open('/run/portbw/resident-health.json',encoding='utf-8'))
 assert d['pid']==int(sys.argv[1]) and d['ready'] is True and d['successful_rounds']>=1
 assert d['last_outcome']=='ok' and d['source_sha256']==sys.argv[2]
 assert time.time()-__import__('datetime').datetime.fromisoformat(d['last_success_utc']).timestamp()<20
except (OSError,ValueError,TypeError,KeyError,AssertionError):sys.exit(1)
PY
      then ok=1;break;fi
    fi
    sleep 1
  done
  ((ok == 1)) || fail '45秒内没有拿到当前进程有效的成功巡检与READY状态'
  "$CLI" audit || fail '内核限速策略审计未通过'
  say "VERIFY PASS：已安装稳定版SHA=$EXPECTED_SHA；Watchdog正常，限速规则审计OK"
}
install_fresh(){
  clean_check
  install -d -m 0700 "$STATE"
  exec 9>"$STATE/installer.lock"
  flock -w 120 9 || fail '部署管理锁忙'
  clean_check
  back_up clean
  COMMITTED=0
  trap transaction_exit EXIT
  install -d -m 0755 /usr/local/lib/portbw /usr/local/sbin
  install -m 0755 "$SOURCE" "$PROG.new.$$"
  mv -f -- "$PROG.new.$$" "$PROG"
  ln -s "$PROG" "$CLI"
  local args=(install --iface "$IFACE")
  if (( NFT_ONLY == 1 ));then args+=(--nft-only);fi
  "$CLI" "${args[@]}"
  write_marker "$BACKUP"
  verify
  COMMITTED=1
  trap - EXIT
  say 'INSTALL PASS：修正版首次成功巡检验收；未改外部业务服务（仍需 VPS 实测）'
}
preflight_upgrade(){
  host_check
  read_managed
  say "当前已托管源码 SHA=$(sha "$PROG")；预检仅允许已知受管理版本"
  resolve_iface
  [[ "$(read_current_iface)" == "$IFACE" ]] || fail '已有网卡与目标不一致'
  [[ ! -e /etc/systemd/system/portbw-watch.timer && ! -L /etc/systemd/system/portbw-watch.timer ]] || fail '遗留timer阻断升级'
  [[ "$(systemctl is-active portbw-watch.timer 2>/dev/null || true)" != active ]] || fail '遗留timer仍运行'
  [[ "$(systemctl is-enabled portbw-watch.timer 2>/dev/null || true)" != enabled ]] || fail '遗留timer仍启用'
  "$CLI" audit || fail '升级前内核限速审计失败'
  say "PREFLIGHT PASS：旧版托管记录及SHA一致；网卡=$IFACE；端口策略与内核审计OK；未修改系统"
}
upgrade_managed(){
  preflight_upgrade
  host_check
  read_managed  # Require managed marker + exact source hash before any mutation.
  resolve_iface
  # Existing policies must retain the binding interface, even if this is a no-op update.
  local old_sha previous_iface
  old_sha="$(sha "$PROG")"
  previous_iface="$(read_current_iface)" || fail '现有配置网卡无效'
  [[ "$previous_iface" == "$IFACE" ]] || fail '升级不支持切换绑定网卡；保留原绑定'
  [[ "$old_sha" != "$EXPECTED_SHA" ]] || { say '已安装相同版本，无需升级';verify;return; }
  # A managed v1/v2 resident installation must not have a foreign timer.
  # Explicitly refuse instead of disabling/deleting an unknown unit.
  [[ ! -e /etc/systemd/system/portbw-watch.timer && ! -L /etc/systemd/system/portbw-watch.timer ]] || fail '存在遗留 timer 文件，拒绝隐式迁移'
  [[ "$(systemctl is-active portbw-watch.timer 2>/dev/null || true)" != active ]] || fail '存在运行中的旧 timer，拒绝隐式迁移'
  [[ "$(systemctl is-enabled portbw-watch.timer 2>/dev/null || true)" != enabled ]] || fail '存在启用的旧 timer，拒绝隐式迁移'
  exec 9>"$STATE/installer.lock"
  flock -w 120 9 || fail '部署管理锁忙'
  back_up upgrade
  COMMITTED=0
  trap transaction_exit EXIT
  systemctl stop portbw-watch.service || fail '无法停止旧监控进程'
  install -m 0755 "$SOURCE" "$PROG.new.$$"
  mv -f -- "$PROG.new.$$" "$PROG"
  "$CLI" install   # inherit saved interface and nft-only mode; do NOT silently enable tc
  write_marker "$BACKUP"
  verify
  COMMITTED=1
  trap - EXIT
  say 'UPGRADE PASS：通过常驻 Watchdog 首次健康巡检；持久备份可回滚（仍需 VPS 实测）'
}
rollback_managed(){
  host_check
  read_managed
  [[ "$(sha "$PROG")" == "$EXPECTED_SHA" ]] || fail '当前源码并非此发行版，拒绝使用错误的回滚工具'
  local b
  b="$(sed -n 's/^backup=//p' "$CURRENT")"
  [[ "$b" == "$STATE"/backup-* && -d "$b" ]] || fail '当前安装记录中的备份目录无效'
  if [[ ! -e "$b/0.old" ]];then
    # Fresh installation: never orphan saved policy intent (or active tc/nft).
    if [[ -d /var/lib/portbw/ports ]];then
      ! find /var/lib/portbw/ports -maxdepth 1 -name '*.json' -print -quit | grep -q . || fail '首次安装后已经有端口策略；必须先安全移除/迁移策略再回滚'
    fi
  fi
  exec 9>"$STATE/installer.lock"
  flock -w 120 9 || fail '部署管理锁忙'
  restore_backup "$b" || fail "回滚不完整；备份保留在 $b，请勿手工覆盖运行中文件"
  if [[ -f "$PROG" ]];then
    local prior_sha
    prior_sha="$(sed -n 's/^source_sha=//p' "$CURRENT")"
    [[ "$(sha "$PROG")" == "$prior_sha" ]] || fail '回滚后源码 SHA 与旧版本标记不匹配'
    "$CLI" audit || fail '程序与配置已回滚，但内核策略 audit 未通过；请保留快照人工检查'
  fi
  say 'ROLLBACK PASS：受管理文件与标记已恢复；内核策略未清空；仍应检查旧服务健康状态'
}
case "$ACTION" in
  check)clean_check;;
  preflight-upgrade)preflight_upgrade;;
  install)install_fresh;;
  verify)verify;;
  upgrade)upgrade_managed;;
  rollback)rollback_managed;;
esac
