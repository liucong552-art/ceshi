#!/usr/bin/env bash
# HY2 five-file edition v3.3.1: fresh-install main node and temporary-node manager.
set -Eeuo pipefail
umask 077

HY2_BUNDLE_VERSION="3.3.1"
HY2_BUNDLE_DATE="2026-07-27"
HY2_STATE_SCHEMA="1"

ENV_FILE="/etc/default/hy2-main"
HY2_LIB_DIR="/usr/local/lib/hy2"
HY2_SBIN_DIR="/usr/local/sbin"
HY2_ROOT_DIR="/root"
HY2_TMPFILES="/etc/tmpfiles.d/hy2.conf"
HY2_LOGROTATE="/etc/logrotate.d/hy2-managed"

INSTALL_TX_ACTIVE=0
INSTALL_TX_DIR=""
declare -A INSTALL_OLD_ENABLED=()
declare -A INSTALL_OLD_ACTIVE=()
INSTALL_UNITS=(
  hy2-managed-restore.service
  hy2-managed-shutdown-save.service
  hy2-managed-watchdog.service
  hy2-managed-watchdog.timer
  hy2-gc.service
  hy2-gc.timer
  pq-save.service
  pq-save.timer
  pq-reset.service
  pq-reset.timer
)
INSTALL_TARGETS=(
  "$ENV_FILE"
  "$HY2_TMPFILES"
  "$HY2_LOGROTATE"
  /root/onekey_hy2_main_tls.sh
  /root/hy2_temp_audit_all.sh
  /usr/local/lib/hy2/common.sh
  /usr/local/lib/hy2/quota-lib.sh
  /usr/local/lib/hy2/iplimit-lib.sh
  /usr/local/lib/hy2/render_table.py
  /usr/local/sbin/pq_add.sh
  /usr/local/sbin/pq_del.sh
  /usr/local/sbin/pq_audit.sh
  /usr/local/sbin/pq_save_state.sh
  /usr/local/sbin/pq_restore_all.sh
  /usr/local/sbin/pq_reset_due.sh
  /usr/local/sbin/ip_set.sh
  /usr/local/sbin/ip_del.sh
  /usr/local/sbin/iplimit_restore_all.sh
  /usr/local/sbin/hy2_run_temp.sh
  /usr/local/sbin/hy2_cleanup_one.sh
  /usr/local/sbin/hy2_clear_all.sh
  /usr/local/sbin/hy2_gc.sh
  /usr/local/sbin/hy2_restore_all.sh
  /usr/local/sbin/hy2_audit.sh
  /usr/local/sbin/hy2_mktemp.sh
  /usr/local/sbin/hy2_temp_sub.sh
  /usr/local/sbin/hy2_managed_watchdog.sh
  /usr/local/sbin/hy2_doctor.sh
  /etc/systemd/system/hy2-managed-restore.service
  /etc/systemd/system/hy2-managed-shutdown-save.service
  /etc/systemd/system/hy2-managed-watchdog.service
  /etc/systemd/system/hy2-managed-watchdog.timer
  /etc/systemd/system/hy2-gc.service
  /etc/systemd/system/hy2-gc.timer
  /etc/systemd/system/pq-save.service
  /etc/systemd/system/pq-save.timer
  /etc/systemd/system/pq-reset.service
  /etc/systemd/system/pq-reset.timer
)

install_tx_key() {
  printf '%s' "$1" | sha256sum | awk '{print $1}'
}


begin_install_transaction() {
  local path key unit
  INSTALL_TX_DIR="$(mktemp -d /var/tmp/hy2-bundle-transaction.XXXXXX)"
  for path in "${INSTALL_TARGETS[@]}"; do
    key="$(install_tx_key "$path")"
    if [[ -e "$path" || -L "$path" ]]; then
      cp -a -- "$path" "${INSTALL_TX_DIR}/${key}"
      : >"${INSTALL_TX_DIR}/${key}.present"
    fi
  done
  for unit in "${INSTALL_UNITS[@]}"; do
    INSTALL_OLD_ENABLED["$unit"]="$(systemctl is-enabled "$unit" 2>/dev/null || true)"
    if systemctl is-active --quiet "$unit" 2>/dev/null; then
      INSTALL_OLD_ACTIVE["$unit"]=1
    else
      INSTALL_OLD_ACTIVE["$unit"]=0
    fi
  done
  INSTALL_TX_ACTIVE=1
  for unit in "${INSTALL_UNITS[@]}"; do
    systemctl stop "$unit" >/dev/null 2>&1 || true
  done
}

restore_unit_enable_state() {
  local unit="$1" state="$2"
  systemctl disable "$unit" >/dev/null 2>&1 || true
  systemctl unmask "$unit" >/dev/null 2>&1 || true
  case "$state" in
    enabled) systemctl enable "$unit" >/dev/null 2>&1 || true ;;
    enabled-runtime) systemctl enable --runtime "$unit" >/dev/null 2>&1 || true ;;
    masked) systemctl mask "$unit" >/dev/null 2>&1 || true ;;
    masked-runtime) systemctl mask --runtime "$unit" >/dev/null 2>&1 || true ;;
  esac
}

rollback_install_transaction() {
  (( INSTALL_TX_ACTIVE == 1 )) || return 0
  INSTALL_TX_ACTIVE=0
  set +e
  trap '' INT TERM HUP
  local unit path key
  for unit in "${INSTALL_UNITS[@]}"; do
    systemctl stop "$unit" >/dev/null 2>&1 || true
  done
  for path in "${INSTALL_TARGETS[@]}"; do
    key="$(install_tx_key "$path")"
    rm -f -- "$path"
    if [[ -f "${INSTALL_TX_DIR}/${key}.present" ]]; then
      install -d -m 755 "$(dirname "$path")"
      cp -a -- "${INSTALL_TX_DIR}/${key}" "$path"
    fi
  done
  systemctl daemon-reload >/dev/null 2>&1 || true
  for unit in "${INSTALL_UNITS[@]}"; do
    restore_unit_enable_state "$unit" "${INSTALL_OLD_ENABLED[$unit]:-}"
    if [[ "${INSTALL_OLD_ACTIVE[$unit]:-0}" == "1" ]]; then
      systemctl start "$unit" >/dev/null 2>&1 || true
    fi
  done
  rm -rf -- "$INSTALL_TX_DIR"
}

install_on_error() {
  local rc=$?
  trap - ERR
  echo "❌ ${BASH_SOURCE[1]:-${BASH_SOURCE[0]}}:${BASH_LINENO[0]:-?}: ${BASH_COMMAND}" >&2
  exit "$rc"
}

install_on_exit() {
  local rc=$?
  trap - EXIT ERR
  rollback_install_transaction || true
  exit "$rc"
}
trap 'install_on_error' ERR
trap 'install_on_exit' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

commit_install_transaction() {
  INSTALL_TX_ACTIVE=0
  rm -rf -- "$INSTALL_TX_DIR"
  INSTALL_TX_DIR=""
}

die() {
  echo "❌ $*" >&2
  exit 1
}

check_supported_os() {
  [[ "$(id -u)" -eq 0 ]] || die "请以 root 运行本脚本"
  (( BASH_VERSINFO[0] >= 4 )) || die "需要 Bash 4.0 或更高版本"
  command -v apt-get >/dev/null 2>&1 || die "本脚本需要 apt-get（Debian/Ubuntu 系）"
  command -v dpkg >/dev/null 2>&1 || die "未找到 dpkg"

  local release_file="${HY2_OS_RELEASE_FILE:-/etc/os-release}"
  local ID="" ID_LIKE="" VERSION_ID="" PRETTY_NAME=""
  local os_id os_id_like os_version pretty major
  if [[ ! -r "$release_file" && "$release_file" == "/etc/os-release" && -r /usr/lib/os-release ]]; then
    release_file="/usr/lib/os-release"
  fi
  [[ -r "$release_file" ]] || die "无法读取 os-release：${release_file}"

  # shellcheck disable=SC1090
  . "$release_file"
  os_id="${ID,,}"
  os_id_like=" ${ID_LIKE,,} "
  os_version="${VERSION_ID:-0}"
  pretty="${PRETTY_NAME:-${ID:-unknown} ${os_version}}"

  case "$os_id" in
    debian)
      major="${os_version%%.*}"
      [[ "$major" =~ ^[0-9]+$ ]] && (( major >= ${HY2_MIN_DEBIAN_MAJOR:-11} )) \
        || die "${pretty} 太旧；最低支持 Debian ${HY2_MIN_DEBIAN_MAJOR:-11}"
      ;;
    ubuntu)
      dpkg --compare-versions "$os_version" ge "${HY2_MIN_UBUNTU_VERSION:-20.04}" \
        || die "${pretty} 太旧；最低支持 Ubuntu ${HY2_MIN_UBUNTU_VERSION:-20.04}"
      ;;
    *)
      if [[ "$os_id_like" == *" debian "* && "${HY2_ALLOW_DEBIAN_DERIVATIVE:-0}" == "1" ]]; then
        echo "⚠️  Debian 衍生系统兼容模式：${pretty}" >&2
      elif [[ "${HY2_ALLOW_UNSUPPORTED_OS:-0}" == "1" ]]; then
        echo "⚠️  未正式支持的系统，按 HY2_ALLOW_UNSUPPORTED_OS=1 继续：${pretty}" >&2
      else
        die "不支持的系统：${pretty}；支持 Debian ${HY2_MIN_DEBIAN_MAJOR:-11}+ / Ubuntu ${HY2_MIN_UBUNTU_VERSION:-20.04}+"
      fi
      ;;
  esac
}

apt_install_with_universe_retry() {
  local -a packages=("$@")
  if apt-get install -y --no-install-recommends "${packages[@]}"; then
    return 0
  fi

  local release_file="${HY2_OS_RELEASE_FILE:-/etc/os-release}"
  local ID=""
  if [[ ! -r "$release_file" && "$release_file" == "/etc/os-release" && -r /usr/lib/os-release ]]; then
    release_file="/usr/lib/os-release"
  fi
  if [[ -r "$release_file" ]]; then
    # shellcheck disable=SC1090
    . "$release_file"
  fi
  if [[ "${ID,,}" == "ubuntu" ]]; then
    echo "⚠️  首次依赖安装失败，尝试启用 Ubuntu Universe 后重试..." >&2
    apt-get install -y --no-install-recommends software-properties-common >/dev/null 2>&1 || true
    if command -v add-apt-repository >/dev/null 2>&1; then
      add-apt-repository -y universe >/dev/null 2>&1 || true
      apt-get update -o Acquire::Retries=3
      apt-get install -y --no-install-recommends "${packages[@]}" && return 0
    fi
  fi
  die "依赖安装失败；请检查软件源、网络以及 Ubuntu Universe 是否已启用"
}

need_basic_tools() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -o Acquire::Retries=3
  apt_install_with_universe_retry \
    ca-certificates curl openssl python3 cron socat nftables iproute2 util-linux \
    coreutils grep sed gawk logrotate systemd procps kmod findutils tar
  local cmd
  for cmd in curl openssl python3 nft ip ss flock timeout sha256sum systemctl getent find tar; do
    command -v "$cmd" >/dev/null 2>&1 || die "依赖安装后仍缺少命令：${cmd}"
  done
  [[ "$(ps -p 1 -o comm= 2>/dev/null | tr -d '[:space:]')" == "systemd" ]] \
    || die "当前系统不是以 systemd 作为 PID 1"
}

acquire_install_locks() {
  install -d -m 755 /run/hy2
  exec 6>/run/hy2/bundle-install.lock
  flock -w 120 6 || die "另一个 HY2 安装任务正在运行"
  exec 7>/run/hy2/temp.lock
  flock -w 120 7 || die "临时节点创建/清理任务仍在运行"
  export HY2_TEMP_LOCK_HELD=1
}

install_dirs() {
  install -d -m 755 "$HY2_LIB_DIR" "$HY2_SBIN_DIR" /etc/hysteria /var/lib/hy2 /run/hy2 /var/log/hy2
  install -d -m 700 /etc/hysteria/temp /var/lib/hy2/main /var/lib/hy2/temp /var/lib/hy2/quota /var/lib/hy2/iplimit
}

install_env_template() {
  if [[ ! -f "$ENV_FILE" ]]; then
    cat >"$ENV_FILE" <<'EOF'
# ==================================================
# HY2 主配置文件
# ==================================================
#
# 说明：
# 1) 主节点固定使用正式证书，监听 443。
# 2) 临时节点使用高端口，复用同一张正式证书。
# 3) 如需切换域名，请先修改 DNS 再更新这里的 HY_DOMAIN。
# 4) 使用 ZeroSSL + acme.sh standalone 申请证书，需要 TCP/80 可达。
#
# IPv4 主节点及 IPv4 临时节点域名；A 记录必须指向本机
HY_DOMAIN=

# 可选 IPv6 临时节点域名；AAAA 记录必须指向本机
HY_IPV6_DOMAIN=

# 主节点固定为 IPv4；客户端仍使用 HY_DOMAIN
HY_LISTEN=0.0.0.0:443

# ZeroSSL 一次性注册 / 证书通知邮箱
ACME_EMAIL=

# HTTP/3 / 反向代理伪装目标
MASQ_URL=https://www.apple.com/

# 是否启用 Salamander：0=关闭，1=开启
ENABLE_SALAMANDER=0

# Salamander 密码（仅在 ENABLE_SALAMANDER=1 时填写）
SALAMANDER_PASSWORD=

# 主节点名称
NODE_NAME=HY2-MAIN

# 临时节点默认端口范围
TEMP_PORT_START=40000
TEMP_PORT_END=50050

# Hysteria 版本。全新安装时 latest 会获取官方当前正式版。
# 已经安装成功后，默认不会因为重复部署主节点而自动升级核心。
HYSTERIA_VERSION=latest

# 核心更新策略：
# install-only = 仅在未安装 Hysteria 时安装（默认，适合生产新机）
# always       = 每次部署主节点都重新运行官方安装器
HYSTERIA_UPDATE_POLICY=install-only

# 官方安装器来源。默认只接受 HTTPS。
HYSTERIA_INSTALLER_URL=https://get.hy2.sh/
ACME_SH_VERSION=3.1.2
# 留空时按 ACME_SH_VERSION 自动生成官方 raw URL
ACME_INSTALLER_URL=

# 可选：填写 64 位 SHA-256 后会强制校验远程安装器。
HYSTERIA_INSTALLER_SHA256=
ACME_INSTALLER_SHA256=

# warn：未填写安装器哈希时记录实际哈希并警告；require：没有哈希就拒绝执行。
REMOTE_SCRIPT_POLICY=warn

# 可选：使用已上传到服务器的 Hysteria 二进制，跳过远程二进制下载。
HYSTERIA_LOCAL_BINARY=

# 可选：填写 64 位 SHA-256 后，会在安装/升级 Hysteria 核心二进制后强制校验其内容。
# 这是对“安装器脚本哈希”之外、针对真正核心二进制的供应链校验。留空时仅记录实际哈希并警告。
# 当 REMOTE_SCRIPT_POLICY=require 且执行核心安装/升级时，必须填写本项。
HYSTERIA_BINARY_SHA256=
EOF
    chmod 600 "$ENV_FILE"
  else
    echo "ℹ️  已存在 ${ENV_FILE}，按原内容保留；本包不负责旧版本配置迁移。"
  fi
  chown root:root "$ENV_FILE"
  chmod 600 "$ENV_FILE"
}

install_tmpfiles() {
  cat >"$HY2_TMPFILES" <<'EOF'
d /run/hy2 0755 root root -
d /var/log/hy2 0755 root root -
EOF
  chmod 644 "$HY2_TMPFILES"
  systemd-tmpfiles --create "$HY2_TMPFILES" >/dev/null 2>&1 || true
}

install_common_lib() {
  cat >"${HY2_LIB_DIR}/common.sh" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail

HY2_BUNDLE_VERSION="3.3.1"
HY2_BUNDLE_DATE="2026-07-27"
HY2_STATE_SCHEMA="1"

HY2_LIB_DIR="/usr/local/lib/hy2"
HY2_SBIN_DIR="/usr/local/sbin"
HY2_STATE_DIR="/var/lib/hy2"
HY2_MAIN_STATE_DIR="${HY2_STATE_DIR}/main"
HY2_TEMP_STATE_DIR="${HY2_STATE_DIR}/temp"
HY2_QUOTA_STATE_DIR="${HY2_STATE_DIR}/quota"
HY2_IPLIMIT_STATE_DIR="${HY2_STATE_DIR}/iplimit"
HY2_ETC_DIR="/etc/hysteria"
HY2_TEMP_CFG_DIR="${HY2_ETC_DIR}/temp"
HY2_DEFAULTS_FILE="/etc/default/hy2-main"
HY2_MAIN_CFG="${HY2_ETC_DIR}/main.yaml"
HY2_MAIN_SERVICE="hy2.service"
HY2_MAIN_STATE_FILE="${HY2_MAIN_STATE_DIR}/main.env"
HY2_MAIN_PASSWORD_FILE="${HY2_MAIN_STATE_DIR}/main.password"
HY2_RENEW_HOOK="/usr/local/lib/hy2/acme-reload.sh"
HY2_LOCK_DIR="/run/hy2"
HY2_LOG_DIR="/var/log/hy2"

hy2_die() {
  echo "❌ $*" >&2
  exit 1
}

hy2_require_root_supported_os() {
  [[ "$(id -u)" -eq 0 ]] || hy2_die "请以 root 身份运行"
  (( BASH_VERSINFO[0] >= 4 )) || hy2_die "需要 Bash 4.0 或更高版本"
  command -v apt-get >/dev/null 2>&1 || hy2_die "本脚本需要 apt-get（Debian/Ubuntu 系）"
  command -v dpkg >/dev/null 2>&1 || hy2_die "未找到 dpkg"

  local release_file="${HY2_OS_RELEASE_FILE:-/etc/os-release}"
  local ID="" ID_LIKE="" VERSION_ID="" PRETTY_NAME=""
  local os_id os_id_like os_version pretty major
  if [[ ! -r "$release_file" && "$release_file" == "/etc/os-release" && -r /usr/lib/os-release ]]; then
    release_file="/usr/lib/os-release"
  fi
  [[ -r "$release_file" ]] || hy2_die "无法读取 os-release：${release_file}"
  # shellcheck disable=SC1090
  . "$release_file"
  os_id="${ID,,}"
  os_id_like=" ${ID_LIKE,,} "
  os_version="${VERSION_ID:-0}"
  pretty="${PRETTY_NAME:-${ID:-unknown} ${os_version}}"
  case "$os_id" in
    debian)
      major="${os_version%%.*}"
      [[ "$major" =~ ^[0-9]+$ ]] && (( major >= ${HY2_MIN_DEBIAN_MAJOR:-11} )) \
        || hy2_die "${pretty} 太旧；最低支持 Debian ${HY2_MIN_DEBIAN_MAJOR:-11}"
      ;;
    ubuntu)
      dpkg --compare-versions "$os_version" ge "${HY2_MIN_UBUNTU_VERSION:-20.04}" \
        || hy2_die "${pretty} 太旧；最低支持 Ubuntu ${HY2_MIN_UBUNTU_VERSION:-20.04}"
      ;;
    *)
      if [[ "$os_id_like" == *" debian "* && "${HY2_ALLOW_DEBIAN_DERIVATIVE:-0}" == "1" ]]; then
        echo "⚠️  Debian 衍生系统兼容模式：${pretty}" >&2
      elif [[ "${HY2_ALLOW_UNSUPPORTED_OS:-0}" == "1" ]]; then
        echo "⚠️  未正式支持的系统，按 HY2_ALLOW_UNSUPPORTED_OS=1 继续：${pretty}" >&2
      else
        hy2_die "不支持的系统：${pretty}；支持 Debian ${HY2_MIN_DEBIAN_MAJOR:-11}+ / Ubuntu ${HY2_MIN_UBUNTU_VERSION:-20.04}+"
      fi
      ;;
  esac
}

hy2_normalize_domain() {
  python3 - "$1" <<'PY'
import ipaddress
import re
import sys
raw = (sys.argv[1] or '').strip().rstrip('.')
if not raw or any(ch.isspace() for ch in raw) or any(ch in raw for ch in '/?#@[]:'):
    raise SystemExit(1)
try:
    ipaddress.ip_address(raw)
    raise SystemExit(1)
except ValueError:
    pass
try:
    value = raw.encode('idna').decode('ascii').lower()
except Exception:
    raise SystemExit(1)
if len(value) > 253:
    raise SystemExit(1)
for label in value.split('.'):
    if not label or len(label) > 63 or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', label):
        raise SystemExit(1)
print(value)
PY
}

hy2_is_public_ip() {
  local value="$1" version="$2"
  python3 - "$value" "$version" <<'PY'
import ipaddress
import sys
try:
    ip = ipaddress.ip_address((sys.argv[1] or '').strip())
    version = int(sys.argv[2])
    raise SystemExit(0 if ip.version == version and ip.is_global else 1)
except Exception:
    raise SystemExit(1)
PY
}

hy2_get_public_ipv4_candidates() {
  local ip url
  {
    for url in https://api.ipify.org https://ifconfig.me/ip https://ipv4.icanhazip.com; do
      ip="$(curl -4fsS --connect-timeout 5 --max-time 15 "$url" 2>/dev/null | tr -d ' \n\r' || true)"
      [[ -n "$ip" ]] && hy2_is_public_ip "$ip" 4 && printf '%s\n' "$ip" || true
    done
    ip -4 -o addr show scope global 2>/dev/null \
      | awk '{split($4,a,"/"); print a[1]}' \
      | while read -r ip; do
          [[ -n "$ip" ]] && hy2_is_public_ip "$ip" 4 && printf '%s\n' "$ip" || true
        done
  } | awk 'NF && !seen[$0]++'
}

hy2_get_public_ipv6_candidates() {
  local ip url
  {
    for url in https://api64.ipify.org https://ifconfig.co/ip; do
      ip="$(curl -6fsS --connect-timeout 5 --max-time 15 "$url" 2>/dev/null | tr -d ' \n\r' || true)"
      [[ -n "$ip" ]] && hy2_is_public_ip "$ip" 6 && printf '%s\n' "$ip" || true
    done
    ip -6 -o addr show scope global 2>/dev/null \
      | awk '{split($4,a,"/"); print a[1]}' \
      | while read -r ip; do
          [[ -n "$ip" ]] && hy2_is_public_ip "$ip" 6 && printf '%s\n' "$ip" || true
        done
  } | awk 'NF && !seen[$0]++'
}

hy2_require_domain_points_here() {
  local domain="$1" version="$2"
  shift 2
  local resolved_ip candidate ok=1
  local -a resolved candidates=("$@")
  if [[ "$version" == "6" ]]; then
    mapfile -t resolved < <(getent ahostsv6 "$domain" 2>/dev/null | awk '{print $1}' | sort -u)
  else
    mapfile -t resolved < <(getent ahostsv4 "$domain" 2>/dev/null | awk '{print $1}' | sort -u)
  fi
  (( ${#resolved[@]} > 0 )) || hy2_die "无法解析 ${domain} 的 IPv${version} 记录"
  (( ${#candidates[@]} > 0 )) || hy2_die "无法检测到本机公网 IPv${version}"
  for resolved_ip in "${resolved[@]}"; do
    for candidate in "${candidates[@]}"; do
      if [[ "$resolved_ip" == "$candidate" ]]; then
        ok=0
        break 2
      fi
    done
  done
  (( ok == 0 )) || hy2_die "${domain} 的 IPv${version} 记录未匹配本机公网地址；DNS=${resolved[*]}；本机=${candidates[*]}"
}

hy2_validate_server_addr() {
  local host="$1" version="$2" rc
  if python3 - "$host" "$version" <<'PY'
import ipaddress
import sys
host, version = sys.argv[1].strip(), int(sys.argv[2])
if host.startswith('[') and host.endswith(']'):
    host = host[1:-1]
try:
    ip = ipaddress.ip_address(host)
except ValueError:
    raise SystemExit(2)
raise SystemExit(0 if ip.version == version else 1)
PY
  then
    return 0
  else
    rc=$?
  fi
  (( rc != 1 )) || return 1
  if [[ "$version" == "6" ]]; then
    getent ahostsv6 "$host" 2>/dev/null | awk 'NF{ok=1} END{exit !ok}'
  else
    getent ahostsv4 "$host" 2>/dev/null | awk 'NF{ok=1} END{exit !ok}'
  fi
}

hy2_url_host() {
  local host="$1"
  if [[ "$host" == \[*\] ]]; then
    printf '%s\n' "$host"
  elif [[ "$host" == *:* ]]; then
    printf '[%s]\n' "$host"
  else
    printf '%s\n' "$host"
  fi
}

hy2_ensure_runtime_dirs() {
  install -d -m 755 \
    "$HY2_LIB_DIR" \
    "$HY2_SBIN_DIR" \
    "$HY2_STATE_DIR" \
    "$HY2_ETC_DIR" \
    "$HY2_LOCK_DIR" \
    "$HY2_LOG_DIR"
  install -d -m 700 \
    "$HY2_MAIN_STATE_DIR" \
    "$HY2_TEMP_STATE_DIR" \
    "$HY2_QUOTA_STATE_DIR" \
    "$HY2_IPLIMIT_STATE_DIR" \
    "$HY2_TEMP_CFG_DIR"
}

hy2_ensure_lock_dir() {
  install -d -m 755 "$HY2_LOCK_DIR"
}

hy2_open_lock_fd() {
  local fd="$1" file="$2"
  case "$fd" in
    5) exec 5>"$file" ;;
    6) exec 6>"$file" ;;
    7) exec 7>"$file" ;;
    8) exec 8>"$file" ;;
    9) exec 9>"$file" ;;
    *) hy2_die "不允许的锁文件描述符：${fd}" ;;
  esac
}

hy2_acquire_lock_fd() {
  local fd="$1" file="$2" wait_seconds="${3:-20}" fail_msg="${4:-锁繁忙}"
  hy2_ensure_lock_dir
  hy2_open_lock_fd "$fd" "$file"
  flock -w "$wait_seconds" "$fd" || hy2_die "$fail_msg"
}

hy2_try_lock_fd() {
  local fd="$1" file="$2"
  hy2_ensure_lock_dir
  hy2_open_lock_fd "$fd" "$file"
  flock -n "$fd"
}

hy2_meta_get() {
  local file="$1" key="$2"
  [[ -f "$file" ]] || return 1
  awk -F= -v k="$key" '$0 !~ /^[[:space:]]*#/ && $1==k {sub($1"=",""); print; exit}' "$file"
}

hy2_write_meta() {
  local file="$1"
  shift
  local tmp
  install -d -m 700 "$(dirname "$file")"
  tmp="$(mktemp "${file}.tmp.XXXXXX")"
  {
    printf 'STATE_SCHEMA=%s\n' "${HY2_STATE_SCHEMA:-1}"
    printf 'MANAGER_VERSION=%s\n' "${HY2_BUNDLE_VERSION:-unknown}"
    printf 'UPDATED_EPOCH=%s\n' "$(date +%s)"
  } >"$tmp"
  local line
  for line in "$@"; do
    printf '%s\n' "$line" >>"$tmp"
  done
  chmod 600 "$tmp"
  mv -f "$tmp" "$file"
}

hy2_meta_upsert() {
  local file="$1" key="$2" value="$3" tmp
  install -d -m 700 "$(dirname "$file")"
  tmp="$(mktemp "${file}.tmp.XXXXXX")"
  if [[ -f "$file" ]] && grep -q "^${key}=" "$file" 2>/dev/null; then
    awk -F= -v k="$key" -v v="$value" '
      BEGIN { done = 0 }
      $1 == k { print k "=" v; done = 1; next }
      { print }
      END { if (!done) print k "=" v }
    ' "$file" >"$tmp"
  else
    if [[ -f "$file" ]]; then
      cat "$file" >"$tmp"
    fi
    printf '%s=%s\n' "$key" "$value" >>"$tmp"
  fi
  chmod 600 "$tmp"
  mv -f "$tmp" "$file"
}

hy2_yaml_quote() {
  python3 - "$1" <<'PY'
import sys
s = sys.argv[1]
print("'" + s.replace("'", "''") + "'")
PY
}

hy2_urlencode() {
  python3 - "$1" <<'PY'
import sys, urllib.parse
print(urllib.parse.quote(sys.argv[1], safe=''))
PY
}

hy2_parse_gib_to_bytes() {
  python3 - "$1" <<'PY'
from decimal import Decimal, ROUND_DOWN
import sys
raw = (sys.argv[1] or '').strip()
try:
    d = Decimal(raw)
    if d <= 0:
        raise ValueError
except Exception:
    raise SystemExit(1)
bytes_val = (d * (1024 ** 3)).to_integral_value(rounding=ROUND_DOWN)
# Bash arithmetic below is signed 64-bit. Leave headroom for packet/counter
# additions so a near-limit quota cannot wrap negative.
if bytes_val > 9000000000000000000:
    raise SystemExit(1)
print(int(bytes_val))
PY
}

hy2_base64_one_line() {
  local base64_help
  base64_help="$(base64 --help 2>/dev/null || true)"
  if [[ "$base64_help" == *"-w"* ]]; then
    base64 -w0
  else
    base64 | tr -d '\n'
  fi
}

hy2_human_bytes() {
  python3 - "$1" <<'PY'
import sys
n = int(sys.argv[1])
units = ['B','KiB','MiB','GiB','TiB']
v = float(n)
for u in units:
    if v < 1024 or u == units[-1]:
        print(f"{v:.2f}{u}")
        break
    v /= 1024.0
PY
}

hy2_pct_text() {
  local used="$1" total="$2"
  python3 - "$used" "$total" <<'PY'
import sys
u = int(sys.argv[1])
t = int(sys.argv[2])
if t <= 0:
    print('N/A')
else:
    print(f"{(u * 100.0) / t:.1f}%")
PY
}

hy2_ttl_human() {
  local expire_epoch="${1:-0}"
  if [[ -z "$expire_epoch" || ! "$expire_epoch" =~ ^[0-9]+$ ]]; then
    printf 'N/A\n'
    return 0
  fi
  local now left d h m s
  now="$(date +%s)"
  left=$((expire_epoch - now))
  if (( left <= 0 )); then
    printf 'expired\n'
    return 0
  fi
  d=$((left / 86400))
  h=$(((left % 86400) / 3600))
  m=$(((left % 3600) / 60))
  s=$((left % 60))
  printf '%02dd%02dh%02dm%02ds\n' "$d" "$h" "$m" "$s"
}

hy2_beijing_time() {
  local epoch="${1:-0}"
  if [[ -z "$epoch" || ! "$epoch" =~ ^[0-9]+$ ]]; then
    printf 'N/A\n'
    return 0
  fi
  TZ='Asia/Shanghai' date -d "@${epoch}" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || printf 'N/A\n'
}

hy2_port_is_listening_udp() {
  local port="$1"
  ss -lunH 2>/dev/null | awk -v p="$port" '$4 ~ ":" p "$" {found=1} END{exit !found}'
}

hy2_wait_unit_and_udp_port() {
  local unit="$1" port="$2"
  local need_consecutive="${3:-3}" max_checks="${4:-12}"
  local consecutive=0 i
  for i in $(seq 1 "$max_checks"); do
    if systemctl is-active --quiet "$unit" 2>/dev/null && hy2_port_is_listening_udp "$port"; then
      consecutive=$((consecutive + 1))
      if (( consecutive >= need_consecutive )); then
        return 0
      fi
    else
      consecutive=0
    fi
    sleep 1
  done
  return 1
}

hy2_unit_state() {
  local unit="$1"
  local state
  state="$(systemctl is-active "$unit" 2>/dev/null || true)"
  case "$state" in
    active|reloading|inactive|failed|activating|deactivating)
      ;;
    "")
      if [[ -f "/etc/systemd/system/${unit}" || -f "/lib/systemd/system/${unit}" ]]; then
        state="inactive"
      else
        state="missing"
      fi
      ;;
  esac
  printf '%s\n' "${state:-missing}"
}

hy2_parse_port_from_listen() {
  local listen="${1:-}"
  if [[ "$listen" =~ ([0-9]+)$ ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return 0
  fi
  return 1
}

hy2_parse_port_from_cfg() {
  local cfg="$1" raw="" listen=""
  [[ -f "$cfg" ]] || return 1

  raw="$(sed -nE 's/^[[:space:]]*listen:[[:space:]]*(.+)[[:space:]]*$/\1/p' "$cfg" | head -n 1)"
  [[ -n "$raw" ]] || return 1

  raw="${raw%%#*}"
  listen="$(printf '%s' "$raw" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//; s/^["'"'"']//; s/["'"'"']$//')"
  [[ -n "$listen" ]] || return 1

  hy2_parse_port_from_listen "$listen"
}

hy2_main_port() {
  local port=""
  port="$(hy2_meta_get "$HY2_MAIN_STATE_FILE" MAIN_PORT 2>/dev/null || true)"
  if [[ "$port" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$port"
    return 0
  fi
  if [[ -f "$HY2_DEFAULTS_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$HY2_DEFAULTS_FILE"
    port="$(hy2_parse_port_from_listen "${HY_LISTEN:-:443}" || true)"
    if [[ "$port" =~ ^[0-9]+$ ]]; then
      printf '%s\n' "$port"
      return 0
    fi
  fi
  printf '443\n'
}

hy2_temp_meta_file() {
  printf '%s/%s.env\n' "$HY2_TEMP_STATE_DIR" "$1"
}

hy2_temp_cfg_file() {
  printf '%s/%s.yaml\n' "$HY2_TEMP_CFG_DIR" "$1"
}

hy2_temp_unit_file() {
  printf '/etc/systemd/system/%s.service\n' "$1"
}

hy2_temp_url_file() {
  printf '%s/%s.url\n' "$HY2_TEMP_STATE_DIR" "$1"
}

hy2_quota_meta_file() {
  printf '%s/%s.env\n' "$HY2_QUOTA_STATE_DIR" "$1"
}

hy2_iplimit_meta_file() {
  printf '%s/%s.env\n' "$HY2_IPLIMIT_STATE_DIR" "$1"
}

hy2_collect_temp_tags() {
  {
    for meta in "$HY2_TEMP_STATE_DIR"/*.env; do
      [[ -f "$meta" ]] || continue
      hy2_meta_get "$meta" TAG || true
    done
    for unit in /etc/systemd/system/hy2-temp-*.service; do
      [[ -f "$unit" ]] || continue
      basename "$unit" .service
    done
  } | awk 'NF {print}' | sort -u
}

hy2_temp_owner_port_from_aux() {
  local tag="$1"
  local file port
  for file in "$HY2_QUOTA_STATE_DIR"/*.env "$HY2_IPLIMIT_STATE_DIR"/*.env; do
    [[ -f "$file" ]] || continue
    if [[ "$(hy2_meta_get "$file" OWNER_TAG 2>/dev/null || true)" == "$tag" ]]; then
      port="$(hy2_meta_get "$file" PORT 2>/dev/null || true)"
      if [[ "$port" =~ ^[0-9]+$ ]]; then
        printf '%s\n' "$port"
        return 0
      fi
    fi
  done
  return 1
}

hy2_temp_port_from_any() {
  local tag="$1"
  local meta cfg port
  meta="$(hy2_temp_meta_file "$tag")"
  if [[ -f "$meta" ]]; then
    port="$(hy2_meta_get "$meta" PORT 2>/dev/null || true)"
    if [[ "$port" =~ ^[0-9]+$ ]]; then
      printf '%s\n' "$port"
      return 0
    fi
  fi
  if port="$(hy2_temp_owner_port_from_aux "$tag" 2>/dev/null || true)"; then
    if [[ "$port" =~ ^[0-9]+$ ]]; then
      printf '%s\n' "$port"
      return 0
    fi
  fi
  cfg="$(hy2_temp_cfg_file "$tag")"
  if [[ -f "$cfg" ]]; then
    port="$(hy2_parse_port_from_cfg "$cfg" 2>/dev/null || true)"
    if [[ "$port" =~ ^[0-9]+$ ]]; then
      printf '%s\n' "$port"
      return 0
    fi
  fi
  return 1
}

hy2_safe_id() {
  local raw="$1"
  [[ ${#raw} -ge 1 && ${#raw} -le 80 && "$raw" =~ ^[A-Za-z0-9._-]+$ ]] \
    || hy2_die "非法 id/tag：${raw}；长度 1-80，仅允许字母、数字、点、下划线、连字符"
  printf '%s\n' "$raw"
}

hy2_temp_tag_from_id() {
  local raw_id="$1"
  printf 'hy2-temp-%s\n' "$raw_id"
}

hy2_is_valid_temp_tag() {
  local tag="${1:-}"
  [[ ${#tag} -ge 10 && ${#tag} -le 96 && "$tag" =~ ^hy2-temp-[A-Za-z0-9._-]+$ ]]
}

hy2_guess_owner_for_port() {
  local port="$1"
  local temp_meta temp_port tag main_port
  for temp_meta in "$HY2_TEMP_STATE_DIR"/*.env; do
    [[ -f "$temp_meta" ]] || continue
    temp_port="$(hy2_meta_get "$temp_meta" PORT 2>/dev/null || true)"
    if [[ "$temp_port" == "$port" ]]; then
      tag="$(hy2_meta_get "$temp_meta" TAG 2>/dev/null || true)"
      printf 'temp:%s\n' "$tag"
      return 0
    fi
  done
  main_port="$(hy2_main_port)"
  if [[ "$main_port" == "$port" ]]; then
    printf 'main:main\n'
    return 0
  fi
  printf 'manual:\n'
}

hy2_temp_meta_by_port() {
  local wanted="$1" meta port tag found=""
  [[ "$wanted" =~ ^[0-9]+$ ]] || hy2_die "端口必须为整数：${wanted}"
  for meta in "$HY2_TEMP_STATE_DIR"/*.env; do
    [[ -f "$meta" ]] || continue
    port="$(hy2_meta_get "$meta" PORT 2>/dev/null || true)"
    [[ "$port" == "$wanted" ]] || continue
    tag="$(hy2_meta_get "$meta" TAG 2>/dev/null || true)"
    hy2_is_valid_temp_tag "$tag" \
      || hy2_die "端口 ${wanted} 对应的临时节点 TAG 非法：${meta}"
    [[ "$tag" == "$(basename "$meta" .env)" ]] \
      || hy2_die "端口 ${wanted} 对应的临时节点 TAG 与文件名不一致：${meta}"
    [[ -z "$found" ]] \
      || hy2_die "多个临时节点占用同一端口 ${wanted}，拒绝修改管理状态"
    found="$meta"
  done
  if [[ -n "$found" ]]; then
    printf '%s\n' "$found"
  fi
}

hy2_collect_used_ports() {
  ss -lunH 2>/dev/null | awk '{print $4}' | sed -nE 's/.*:([0-9]+)$/\1/p'
  for meta in "$HY2_TEMP_STATE_DIR"/*.env "$HY2_QUOTA_STATE_DIR"/*.env "$HY2_IPLIMIT_STATE_DIR"/*.env; do
    [[ -f "$meta" ]] || continue
    hy2_meta_get "$meta" PORT || true
  done
  for cfg in "$HY2_TEMP_CFG_DIR"/*.yaml; do
    [[ -f "$cfg" ]] || continue
    hy2_parse_port_from_cfg "$cfg" || true
  done
  hy2_main_port || true
}

hy2_load_defaults() {
  [[ -f "$HY2_DEFAULTS_FILE" ]] || hy2_die "缺少 ${HY2_DEFAULTS_FILE}"
  [[ "$(stat -c %u "$HY2_DEFAULTS_FILE" 2>/dev/null || echo -1)" == "0" ]] \
    || hy2_die "${HY2_DEFAULTS_FILE} 必须属于 root"
  local defaults_mode
  defaults_mode="$(stat -c %a "$HY2_DEFAULTS_FILE" 2>/dev/null || echo 777)"
  [[ "$defaults_mode" =~ ^[0-7]{3,4}$ ]] && (( ((8#$defaults_mode) & 8#022) == 0 )) \
    || hy2_die "${HY2_DEFAULTS_FILE} 不能被 group/other 写入"
  # shellcheck disable=SC1090
  set -a
  . "$HY2_DEFAULTS_FILE"
  set +a
  : "${HY_DOMAIN:?缺少 HY_DOMAIN}"
  HY_DOMAIN="$(hy2_normalize_domain "$HY_DOMAIN")" || hy2_die "HY_DOMAIN 不是有效域名"
  if [[ -n "${HY_IPV6_DOMAIN:-}" ]]; then
    HY_IPV6_DOMAIN="$(hy2_normalize_domain "$HY_IPV6_DOMAIN")" || hy2_die "HY_IPV6_DOMAIN 不是有效域名"
  fi
  : "${HY_LISTEN:=0.0.0.0:443}"
  : "${MASQ_URL:?缺少 MASQ_URL}"
  : "${ENABLE_SALAMANDER:=0}"
  : "${SALAMANDER_PASSWORD:=}"
  : "${NODE_NAME:=HY2-MAIN}"
  : "${TEMP_PORT_START:=40000}"
  : "${TEMP_PORT_END:=50050}"
  : "${HYSTERIA_VERSION:=latest}"
  : "${HYSTERIA_UPDATE_POLICY:=install-only}"
  [[ "$ENABLE_SALAMANDER" == "0" || "$ENABLE_SALAMANDER" == "1" ]] \
    || hy2_die "ENABLE_SALAMANDER 只能是 0 或 1"
  [[ "$HYSTERIA_VERSION" == "latest" || "$HYSTERIA_VERSION" =~ ^v[0-9][A-Za-z0-9._-]*$ ]] \
    || hy2_die "HYSTERIA_VERSION 必须是 latest 或 vX.Y.Z"
  [[ "$HYSTERIA_UPDATE_POLICY" == "install-only" || "$HYSTERIA_UPDATE_POLICY" == "always" ]] \
    || hy2_die "HYSTERIA_UPDATE_POLICY 只能是 install-only 或 always"
}

hy2_main_cert_paths() {
  local domain="${1:?need domain}"
  printf '%s\n%s\n' "/etc/hysteria/certs/${domain}/fullchain.pem" "/etc/hysteria/certs/${domain}/privkey.pem"
}

hy2_build_url() {
  local auth="$1" domain="$2" port="$3" node_name="$4" enable_obfs="${5:-0}" obfs_pass="${6:-}" sni="${7:-$domain}"
  local auth_q sni_q node_q obfs_q url url_host
  auth_q="$(hy2_urlencode "$auth")"
  sni_q="$(hy2_urlencode "$sni")"
  node_q="$(hy2_urlencode "$node_name")"
  url_host="$(hy2_url_host "$domain")"
  url="hy2://${auth_q}@${url_host}:${port}/?sni=${sni_q}"
  if [[ "$enable_obfs" == "1" ]]; then
    obfs_q="$(hy2_urlencode "$obfs_pass")"
    url="${url}&obfs=salamander&obfs-password=${obfs_q}"
  fi
  url="${url}#${node_q}"
  printf '%s\n' "$url"
}

hy2_write_server_cfg() {
  local cfg="$1" listen_value="$2" password="$3" cert="$4" key="$5" masq_url="$6" enable_obfs="${7:-0}" obfs_pass="${8:-}" bind_device="${9:-}" outbound_mode="${10:-4}"
  local cert_q key_q pwd_q masq_q obfs_q listen_q bind_q tmp
  cert_q="$(hy2_yaml_quote "$cert")"
  key_q="$(hy2_yaml_quote "$key")"
  pwd_q="$(hy2_yaml_quote "$password")"
  masq_q="$(hy2_yaml_quote "$masq_url")"
  obfs_q="$(hy2_yaml_quote "$obfs_pass")"
  bind_q="$(hy2_yaml_quote "$bind_device")"
  if [[ "$listen_value" =~ ^[0-9]+$ ]]; then
    listen_value=":${listen_value}"
  fi
  listen_q="$(hy2_yaml_quote "$listen_value")"

  install -d -m 700 "$(dirname "$cfg")"
  tmp="$(mktemp "${cfg}.tmp.XXXXXX")"
  {
    printf 'listen: %s\n\n' "$listen_q"
    printf 'tls:\n'
    printf '  cert: %s\n' "$cert_q"
    printf '  key: %s\n\n' "$key_q"
    printf 'auth:\n'
    printf '  type: password\n'
    printf '  password: %s\n\n' "$pwd_q"
    if [[ "$enable_obfs" == "1" ]]; then
      printf 'obfs:\n'
      printf '  type: salamander\n'
      printf '  salamander:\n'
      printf '    password: %s\n\n' "$obfs_q"
    fi
    if [[ -n "$bind_device" ]]; then
      printf 'outbounds:\n'
      printf '  - name: nat\n'
      printf '    type: direct\n'
      printf '    direct:\n'
      printf '      mode: %s\n' "$outbound_mode"
      printf '      bindDevice: %s\n\n' "$bind_q"
    fi
    printf 'masquerade:\n'
    printf '  type: proxy\n'
    printf '  proxy:\n'
    printf '    url: %s\n' "$masq_q"
    printf '    rewriteHost: true\n\n'
    printf 'speedTest: false\n'
    printf 'disableUDP: false\n'
    printf 'udpIdleTimeout: 60s\n'
  } >"$tmp"
  chmod 600 "$tmp"
  mv -f "$tmp" "$cfg"
}

hy2_temp_unit_text() {
  local tag="$1" cfg="$2" landing="${3:-local}" wg_if="${4:-}"
  local requisite="" after_extra="" exec_pre=""
  if [[ "$landing" == "nat" ]]; then
    [[ -n "$wg_if" ]] || hy2_die "NAT 临时节点缺少 WG_IF"
    requisite="Requisite=wg-quick@${wg_if}.service"
    after_extra="wg-quick@${wg_if}.service"
    exec_pre="ExecStartPre=/usr/local/sbin/hy2_nat_preflight.sh ${tag}"
  fi
  cat <<UNIT
[Unit]
Description=Temporary Hysteria 2 ${tag} (${landing})
${requisite}
After=network-online.target nftables.service hy2-managed-restore.service ${after_extra}
Wants=network-online.target
ConditionPathExists=${cfg}
ConditionPathExists=$(hy2_temp_meta_file "$tag")
StartLimitIntervalSec=300
StartLimitBurst=10

[Service]
Type=simple
User=root
Group=root
${exec_pre}
ExecStart=/usr/local/sbin/hy2_run_temp.sh ${tag} ${cfg}
ExecStopPost=/usr/local/sbin/hy2_cleanup_one.sh ${tag} --from-stop-post
Restart=on-failure
RestartSec=3s
SuccessExitStatus=0 124 143
TimeoutStopSec=60
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectClock=true
ProtectHostname=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictNamespaces=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_NETLINK AF_UNIX
LockPersonality=true
SystemCallArchitectures=native
UMask=0077

[Install]
WantedBy=multi-user.target
UNIT
}

hy2_write_temp_unit() {
  local tag="$1" cfg="$2" landing="${3:-local}" wg_if="${4:-}" unit_file
  unit_file="$(hy2_temp_unit_file "$tag")"
  hy2_temp_unit_text "$tag" "$cfg" "$landing" "$wg_if" >"$unit_file"
  chmod 644 "$unit_file"
}

hy2_log() {
  local file="$1"
  shift
  install -d -m 755 "$HY2_LOG_DIR" >/dev/null 2>&1 || true
  printf '%s %s\n' "$(date '+%F %T %Z')" "$*" >>"${HY2_LOG_DIR}/${file}"
}
EOF
  chmod 644 "${HY2_LIB_DIR}/common.sh"
}

install_render_table() {
  cat >"${HY2_LIB_DIR}/render_table.py" <<'EOF'
#!/usr/bin/env python3
import os
import shutil
import sys
import unicodedata

SCHEMAS = {
    "hy2": [
        {"name": "NAME",  "min": 12, "ideal": 18, "max": 34, "align": "left",  "weight": 10},
        {"name": "STATE", "min":  6, "ideal":  7, "max": 10, "align": "left",  "weight":  2},
        {"name": "PORT",  "min":  5, "ideal":  5, "max":  5, "align": "right", "weight":  1},
        {"name": "LISN",  "min":  4, "ideal":  4, "max":  4, "align": "left",  "weight":  1},
        {"name": "FAM",   "min":  3, "ideal":  3, "max":  3, "align": "right", "weight":  1},
        {"name": "EXIT",  "min":  5, "ideal":  5, "max":  7, "align": "left",  "weight":  1},
        {"name": "QUOTA", "min":  6, "ideal":  8, "max": 10, "align": "left",  "weight":  1},
        {"name": "LIMIT", "min":  7, "ideal":  9, "max": 14, "align": "right", "weight":  1},
        {"name": "USED",  "min":  7, "ideal":  9, "max": 14, "align": "right", "weight":  1},
        {"name": "LEFT",  "min":  7, "ideal":  9, "max": 14, "align": "right", "weight":  1},
        {"name": "USE%",  "min":  6, "ideal":  6, "max":  6, "align": "right", "weight":  1},
        {"name": "TTL",   "min":  6, "ideal": 10, "max": 14, "align": "left",  "weight":  2},
        {"name": "EXPBJ", "min": 10, "ideal": 19, "max": 19, "align": "left",  "weight":  3},
        {"name": "IPLM",  "min":  4, "ideal":  4, "max":  6, "align": "right", "weight":  1},
        {"name": "IPACT", "min":  5, "ideal":  5, "max":  7, "align": "right", "weight":  1},
        {"name": "STKY",  "min":  4, "ideal":  6, "max":  8, "align": "right", "weight":  1},
    ],
    "pq": [
        {"name": "PORT",   "min":  5, "ideal":  5, "max":  5, "align": "right", "weight": 1},
        {"name": "OWNER",  "min": 10, "ideal": 20, "max": 40, "align": "left",  "weight": 8},
        {"name": "STATE",  "min":  6, "ideal":  8, "max": 10, "align": "left",  "weight": 2},
        {"name": "LIMIT",  "min":  7, "ideal":  9, "max": 14, "align": "right", "weight": 1},
        {"name": "USED",   "min":  7, "ideal":  9, "max": 14, "align": "right", "weight": 1},
        {"name": "LEFT",   "min":  7, "ideal":  9, "max": 14, "align": "right", "weight": 1},
        {"name": "USE%",   "min":  6, "ideal":  6, "max":  6, "align": "right", "weight": 1},
        {"name": "RESET",  "min":  5, "ideal":  5, "max": 10, "align": "left",  "weight": 1},
        {"name": "NEXTBJ", "min": 10, "ideal": 19, "max": 19, "align": "left",  "weight": 3},
    ],
}

def char_width(ch: str) -> int:
    if not ch or ch in "\n\r" or unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1

def text_width(text: str) -> int:
    return sum(char_width(ch) for ch in text)

def take_prefix(text: str, width: int):
    out = []
    used = 0
    idx = 0
    while idx < len(text):
        ch = text[idx]
        if ch == "\n":
            idx += 1
            break
        w = char_width(ch)
        if used + w > width:
            break
        out.append(ch)
        used += w
        idx += 1
    return "".join(out), text[idx:]

def split_point(text: str, width: int) -> int:
    prefix, _ = take_prefix(text, width)
    if len(prefix) == len(text):
        return len(text)
    for i in range(len(prefix) - 1, -1, -1):
        ch = prefix[i]
        prev = prefix[i - 1] if i > 0 else ""
        if ch.isspace():
            return i + 1
        if ch in "/_-:@":
            return i + 1
        if i > 0 and prev.isdigit() and ch.isalpha():
            return i
    return len(prefix)

def wrap_cell(text: str, width: int):
    text = "-" if text in (None, "") else str(text)
    text = text.replace("\r", "")
    lines = []
    for part in text.split("\n"):
        part = part.strip()
        if not part:
            lines.append("")
            continue
        while part:
            if text_width(part) <= width:
                lines.append(part)
                break
            cut = split_point(part, width)
            left = part[:cut].rstrip()
            part = part[cut:].lstrip()
            if not left:
                left, part = take_prefix(part, width)
            lines.append(left)
    return lines or ["-"]

def pad(text: str, width: int, align: str):
    text = "" if text is None else str(text)
    if text_width(text) > width:
        text = take_prefix(text, width)[0]
    spaces = " " * max(0, width - text_width(text))
    return spaces + text if align == "right" else text + spaces

def border(left: str, mid: str, right: str, widths):
    return left + mid.join("━" * w for w in widths) + right

def terminal_columns() -> int:
    env_cols = os.environ.get("COLUMNS", "").strip()
    if env_cols.isdigit() and int(env_cols) > 0:
        return int(env_cols)
    return shutil.get_terminal_size(fallback=(160, 24)).columns

def allocate_widths(schema):
    mins = [c["min"] for c in schema]
    ideals = [c["ideal"] for c in schema]
    maxs = [c["max"] for c in schema]
    weights = [max(1, int(c.get("weight", 1))) for c in schema]

    widths = ideals[:]
    available = max(sum(mins), terminal_columns() - (len(schema) + 1))
    current = sum(widths)

    if current > available:
        deficit = current - available
        order = sorted(range(len(schema)), key=lambda i: (weights[i], ideals[i] - mins[i]), reverse=True)
        changed = True
        while deficit > 0 and changed:
            changed = False
            for i in order:
                if deficit <= 0:
                    break
                if widths[i] > mins[i]:
                    widths[i] -= 1
                    deficit -= 1
                    changed = True
    elif current < available:
        extra = available - current
        order = sorted(range(len(schema)), key=lambda i: (weights[i], maxs[i] - ideals[i]), reverse=True)
        changed = True
        while extra > 0 and changed:
            changed = False
            for i in order:
                if extra <= 0:
                    break
                if widths[i] < maxs[i]:
                    widths[i] += 1
                    extra -= 1
                    changed = True

    return widths

def main():
    if len(sys.argv) != 2 or sys.argv[1] not in SCHEMAS:
        print("usage: render_table.py <hy2|pq>", file=sys.stderr)
        sys.exit(2)

    schema = SCHEMAS[sys.argv[1]]
    headers = [c["name"] for c in schema]
    aligns = [c["align"] for c in schema]
    widths = allocate_widths(schema)

    rows = []
    for raw in sys.stdin:
        raw = raw.rstrip("\n")
        if not raw:
            continue
        cols = raw.split("\t")
        if len(cols) < len(schema):
            cols += [""] * (len(schema) - len(cols))
        rows.append(cols[:len(schema)])

    if not rows:
        rows = [["-"] * len(schema)]

    print(border("┏", "┳", "┓", widths))
    print("┃" + "│".join(pad(h, w, "left") for h, w in zip(headers, widths)) + "┃")
    print(border("┣", "╋", "┫", widths))

    for idx, row in enumerate(rows):
        wrapped = [wrap_cell(col, width) for col, width in zip(row, widths)]
        height = max(len(parts) for parts in wrapped)
        for line_no in range(height):
            out = []
            for col_idx, parts in enumerate(wrapped):
                text = parts[line_no] if line_no < len(parts) else ""
                out.append(pad(text, widths[col_idx], aligns[col_idx]))
            print("┃" + "│".join(out) + "┃")
        if idx != len(rows) - 1:
            print(border("┣", "╋", "┫", widths))

    print(border("┗", "┻", "┛", widths))

if __name__ == "__main__":
    main()
EOF
  chmod 755 "${HY2_LIB_DIR}/render_table.py"
}

install_quota_lib() {
  cat >"${HY2_LIB_DIR}/quota-lib.sh" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail

# shellcheck disable=SC1091
source /usr/local/lib/hy2/common.sh

HY2_PQ_TABLE="hy2_pq"
HY2_PQ_INPUT_CHAIN="pq_input"
HY2_PQ_OUTPUT_CHAIN="pq_output"
HY2_PQ_LOCK_FILE="${HY2_LOCK_DIR}/quota.lock"

hy2_pq_lock() {
  if [[ "${HY2_PQ_LOCK_HELD:-0}" != "1" ]]; then
    hy2_acquire_lock_fd 9 "$HY2_PQ_LOCK_FILE" 20 "quota 锁繁忙"
    export HY2_PQ_LOCK_HELD=1
  fi
}

hy2_pq_unlock() {
  if [[ "${HY2_PQ_LOCK_HELD:-0}" == "1" ]]; then
    flock -u 9 >/dev/null 2>&1 || true
    { exec 9>&-; } 2>/dev/null || true
    unset HY2_PQ_LOCK_HELD
  fi
}

hy2_pq_counter_in() { printf 'hy2_pq_in_%s\n' "$1"; }
hy2_pq_counter_out() { printf 'hy2_pq_out_%s\n' "$1"; }
hy2_pq_quota_obj() { printf 'hy2_pq_q_%s\n' "$1"; }
hy2_pq_comment_count_in() { printf 'hy2-pq-count-in-%s\n' "$1"; }
hy2_pq_comment_count_out() { printf 'hy2-pq-count-out-%s\n' "$1"; }
hy2_pq_comment_drop_in() { printf 'hy2-pq-drop-in-%s\n' "$1"; }
hy2_pq_comment_drop_out() { printf 'hy2-pq-drop-out-%s\n' "$1"; }

hy2_pq_meta_owner_exists() {
  local meta="$1"
  local owner_tag owner_kind
  owner_tag="$(hy2_meta_get "$meta" OWNER_TAG 2>/dev/null || true)"
  owner_kind="$(hy2_meta_get "$meta" OWNER_KIND 2>/dev/null || true)"
  if [[ "$owner_kind" == "temp" && -n "$owner_tag" ]]; then
    [[ -f "$(hy2_temp_meta_file "$owner_tag")" ]] || return 1
  fi
  if [[ "$owner_kind" == "main" ]]; then
    [[ -f "$HY2_MAIN_STATE_FILE" ]] || return 1
  fi
  return 0
}

hy2_pq_ensure_base() {
  hy2_ensure_runtime_dirs || return 1
  command -v nft >/dev/null 2>&1 || hy2_die "未找到 nft 命令"
  if ! nft list table inet "$HY2_PQ_TABLE" >/dev/null 2>&1; then
    nft add table inet "$HY2_PQ_TABLE" || return 1
  fi
  if ! nft list chain inet "$HY2_PQ_TABLE" "$HY2_PQ_INPUT_CHAIN" >/dev/null 2>&1; then
    nft add chain inet "$HY2_PQ_TABLE" "$HY2_PQ_INPUT_CHAIN" \
      '{ type filter hook input priority 0; policy accept; }' || return 1
  fi
  if ! nft list chain inet "$HY2_PQ_TABLE" "$HY2_PQ_OUTPUT_CHAIN" >/dev/null 2>&1; then
    nft add chain inet "$HY2_PQ_TABLE" "$HY2_PQ_OUTPUT_CHAIN" \
      '{ type filter hook output priority 0; policy accept; }' || return 1
  fi
}

hy2_pq_delete_rules_with_comment() {
  local chain="$1" comment="$2"
  nft -a list chain inet "$HY2_PQ_TABLE" "$chain" 2>/dev/null \
    | awk -v c="comment \"${comment}\"" '$0 ~ c {print $NF}' \
    | sort -rn \
    | while read -r handle; do
        [[ -n "$handle" ]] || continue
        nft delete rule inet "$HY2_PQ_TABLE" "$chain" handle "$handle" >/dev/null 2>&1 || true
      done
}

hy2_pq_rule_comment_exists() {
  local chain="$1" comment="$2" rules
  rules="$(
    nft -a list chain inet "$HY2_PQ_TABLE" "$chain" 2>/dev/null || true
  )"
  [[ "$rules" == *"comment \"${comment}\""* ]]
}

hy2_pq_delete_port_rules() {
  local port="$1"
  hy2_pq_delete_rules_with_comment "$HY2_PQ_INPUT_CHAIN" "$(hy2_pq_comment_drop_in "$port")"
  hy2_pq_delete_rules_with_comment "$HY2_PQ_INPUT_CHAIN" "$(hy2_pq_comment_count_in "$port")"
  hy2_pq_delete_rules_with_comment "$HY2_PQ_OUTPUT_CHAIN" "$(hy2_pq_comment_drop_out "$port")"
  hy2_pq_delete_rules_with_comment "$HY2_PQ_OUTPUT_CHAIN" "$(hy2_pq_comment_count_out "$port")"
}

hy2_pq_delete_port_objects() {
  local port="$1"
  nft delete counter inet "$HY2_PQ_TABLE" "$(hy2_pq_counter_in "$port")" >/dev/null 2>&1 || true
  nft delete counter inet "$HY2_PQ_TABLE" "$(hy2_pq_counter_out "$port")" >/dev/null 2>&1 || true
  nft delete quota inet "$HY2_PQ_TABLE" "$(hy2_pq_quota_obj "$port")" >/dev/null 2>&1 || true
}

hy2_pq_append_atomic_deletes() {
  local batch="$1" port="$2" chain comment handle object kind name
  while IFS='|' read -r chain comment; do
    while IFS= read -r handle; do
      [[ "$handle" =~ ^[0-9]+$ ]] || continue
      printf 'delete rule inet %s %s handle %s\n' "$HY2_PQ_TABLE" "$chain" "$handle" >>"$batch"
    done < <(
      nft -a list chain inet "$HY2_PQ_TABLE" "$chain" 2>/dev/null \
        | awk -v c="comment \"${comment}\"" '$0 ~ c {print $NF}' | sort -rn
    )
  done <<EOF_COMMENTS
${HY2_PQ_INPUT_CHAIN}|$(hy2_pq_comment_drop_in "$port")
${HY2_PQ_INPUT_CHAIN}|$(hy2_pq_comment_count_in "$port")
${HY2_PQ_OUTPUT_CHAIN}|$(hy2_pq_comment_drop_out "$port")
${HY2_PQ_OUTPUT_CHAIN}|$(hy2_pq_comment_count_out "$port")
EOF_COMMENTS
  for object in \
    "counter|$(hy2_pq_counter_in "$port")" \
    "counter|$(hy2_pq_counter_out "$port")" \
    "quota|$(hy2_pq_quota_obj "$port")"
  do
    kind="${object%%|*}"
    name="${object#*|}"
    if nft list "$kind" inet "$HY2_PQ_TABLE" "$name" >/dev/null 2>&1; then
      printf 'delete %s inet %s %s\n' "$kind" "$HY2_PQ_TABLE" "$name" >>"$batch"
    fi
  done
}

hy2_pq_failsafe_block_port() {
  local port="$1" batch
  hy2_pq_ensure_base || return 1
  batch="$(mktemp "${HY2_LOCK_DIR}/quota-failsafe.XXXXXX")"
  hy2_pq_append_atomic_deletes "$batch" "$port"
  printf 'add rule inet %s %s udp dport %s drop comment "%s"\n' \
    "$HY2_PQ_TABLE" "$HY2_PQ_INPUT_CHAIN" "$port" "$(hy2_pq_comment_drop_in "$port")" >>"$batch"
  printf 'add rule inet %s %s udp sport %s drop comment "%s"\n' \
    "$HY2_PQ_TABLE" "$HY2_PQ_OUTPUT_CHAIN" "$port" "$(hy2_pq_comment_drop_out "$port")" >>"$batch"
  if ! nft -f "$batch"; then
    rm -f "$batch"
    return 1
  fi
  rm -f "$batch"
}

hy2_pq_rebuild_port() {
  local port="$1" remaining_bytes="$2" batch
  [[ "$port" =~ ^[0-9]+$ ]] || hy2_die "hy2_pq_rebuild_port: bad port ${port}"
  [[ "$remaining_bytes" =~ ^[0-9]+$ ]] || hy2_die "hy2_pq_rebuild_port: bad remaining ${remaining_bytes}"

  hy2_pq_lock
  hy2_pq_ensure_base || return 1
  batch="$(mktemp "${HY2_LOCK_DIR}/quota-rebuild.XXXXXX")"
  hy2_pq_append_atomic_deletes "$batch" "$port"

  if (( remaining_bytes > 0 )); then
    cat >>"$batch" <<EOF_RULES
add counter inet ${HY2_PQ_TABLE} $(hy2_pq_counter_in "$port")
add counter inet ${HY2_PQ_TABLE} $(hy2_pq_counter_out "$port")
add quota inet ${HY2_PQ_TABLE} $(hy2_pq_quota_obj "$port") { over ${remaining_bytes} bytes used 0 bytes }
add rule inet ${HY2_PQ_TABLE} ${HY2_PQ_INPUT_CHAIN} udp dport ${port} quota name "$(hy2_pq_quota_obj "$port")" drop comment "$(hy2_pq_comment_drop_in "$port")"
add rule inet ${HY2_PQ_TABLE} ${HY2_PQ_INPUT_CHAIN} udp dport ${port} counter name "$(hy2_pq_counter_in "$port")" comment "$(hy2_pq_comment_count_in "$port")"
add rule inet ${HY2_PQ_TABLE} ${HY2_PQ_OUTPUT_CHAIN} udp sport ${port} quota name "$(hy2_pq_quota_obj "$port")" drop comment "$(hy2_pq_comment_drop_out "$port")"
add rule inet ${HY2_PQ_TABLE} ${HY2_PQ_OUTPUT_CHAIN} udp sport ${port} counter name "$(hy2_pq_counter_out "$port")" comment "$(hy2_pq_comment_count_out "$port")"
EOF_RULES
  else
    cat >>"$batch" <<EOF_RULES
add rule inet ${HY2_PQ_TABLE} ${HY2_PQ_INPUT_CHAIN} udp dport ${port} drop comment "$(hy2_pq_comment_drop_in "$port")"
add rule inet ${HY2_PQ_TABLE} ${HY2_PQ_OUTPUT_CHAIN} udp sport ${port} drop comment "$(hy2_pq_comment_drop_out "$port")"
EOF_RULES
  fi
  if ! nft -f "$batch"; then
    rm -f "$batch"
    hy2_pq_failsafe_block_port "$port" || true
    return 1
  fi
  rm -f "$batch"
}

hy2_pq_counter_bytes() {
  local obj="$1" output value
  output="$(nft -n list counter inet "$HY2_PQ_TABLE" "$obj" 2>/dev/null)" || return 1
  value="$(
    awk '/bytes/ {
      for (i = 1; i <= NF; i++) {
        if ($i == "bytes") {
          gsub(/[^0-9]/, "", $(i+1))
          print $(i+1)
          exit
        }
      }
    }' <<<"$output"
  )"
  [[ "$value" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "$value"
}

hy2_pq_quota_used_bytes() {
  local obj="$1" output value
  output="$(nft -n list quota inet "$HY2_PQ_TABLE" "$obj" 2>/dev/null)" || return 1
  value="$(
    awk '/used/ {
      for (i = 1; i <= NF; i++) {
        if ($i == "used") {
          gsub(/[^0-9]/, "", $(i+1))
          print $(i+1)
          exit
        }
      }
    }' <<<"$output"
  )"
  # nft omits the used field while the consumed value is exactly zero.
  if [[ -z "$value" ]]; then
    printf '0\n'
    return 0
  fi
  [[ "$value" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "$value"
}

hy2_pq_live_used_bytes() {
  local port="$1"
  local quota_b in_b out_b counter_b meta remaining
  local quota_ok=0 counter_ok=0

  if quota_b="$(hy2_pq_quota_used_bytes "$(hy2_pq_quota_obj "$port")")"; then
    quota_ok=1
  fi
  if in_b="$(hy2_pq_counter_bytes "$(hy2_pq_counter_in "$port")")" \
    && out_b="$(hy2_pq_counter_bytes "$(hy2_pq_counter_out "$port")")"
  then
    counter_b=$((in_b + out_b))
    counter_ok=1
  fi

  if (( quota_ok == 1 && counter_ok == 1 )); then
    if (( counter_b > quota_b )); then
      printf '%s\n' "$counter_b"
    else
      printf '%s\n' "$quota_b"
    fi
    return 0
  fi
  if (( quota_ok == 1 )); then
    printf '%s\n' "$quota_b"
    return 0
  fi
  if (( counter_ok == 1 )); then
    printf '%s\n' "$counter_b"
    return 0
  fi

  meta="$(hy2_quota_meta_file "$port")"
  remaining="$(hy2_meta_get "$meta" LIMIT_BYTES 2>/dev/null || true)"
  if [[ "$remaining" == "0" ]] \
    && hy2_pq_rule_comment_exists "$HY2_PQ_INPUT_CHAIN" "$(hy2_pq_comment_drop_in "$port")" \
    && hy2_pq_rule_comment_exists "$HY2_PQ_OUTPUT_CHAIN" "$(hy2_pq_comment_drop_out "$port")"
  then
    printf '0\n'
    return 0
  fi

  return 1
}

hy2_pq_snapshot_unlocked() {
  local port="$1"
  local meta original saved live used left rebuild_pending state
  local input_rules output_rules

  meta="$(hy2_quota_meta_file "$port")"
  if [[ ! -f "$meta" ]]; then
    printf 'none|0|0|0\n'
    return 0
  fi

  original="$(hy2_meta_get "$meta" ORIGINAL_LIMIT_BYTES 2>/dev/null || true)"
  saved="$(hy2_meta_get "$meta" SAVED_USED_BYTES 2>/dev/null || true)"
  rebuild_pending="$(hy2_meta_get "$meta" REBUILD_PENDING 2>/dev/null || true)"
  if [[ ! "$original" =~ ^[0-9]+$ || ! "$saved" =~ ^[0-9]+$ ]]; then
    printf 'stale|0|0|0\n'
    return 0
  fi

  used="$saved"
  (( used > original )) && used="$original"
  left=$((original - used))
  (( left < 0 )) && left=0

  if [[ "$rebuild_pending" == "1" ]]; then
    printf 'stale|%s|%s|%s\n' "$original" "$used" "$left"
    return 0
  fi

  if ! live="$(hy2_pq_live_used_bytes "$port")"; then
    printf 'stale|%s|%s|%s\n' "$original" "$used" "$left"
    return 0
  fi
  used=$((saved + live))
  (( used > original )) && used="$original"
  left=$((original - used))
  (( left < 0 )) && left=0

  input_rules="$(nft -a list chain inet "$HY2_PQ_TABLE" "$HY2_PQ_INPUT_CHAIN" 2>/dev/null || true)"
  output_rules="$(nft -a list chain inet "$HY2_PQ_TABLE" "$HY2_PQ_OUTPUT_CHAIN" 2>/dev/null || true)"

  if (( left <= 0 )); then
    if [[ "$input_rules" == *"comment \"$(hy2_pq_comment_drop_in "$port")\""* ]] \
      && [[ "$output_rules" == *"comment \"$(hy2_pq_comment_drop_out "$port")\""* ]]
    then
      state='exhausted'
    else
      state='stale'
    fi
  elif nft list counter inet "$HY2_PQ_TABLE" "$(hy2_pq_counter_in "$port")" >/dev/null 2>&1 \
    && nft list counter inet "$HY2_PQ_TABLE" "$(hy2_pq_counter_out "$port")" >/dev/null 2>&1 \
    && nft list quota inet "$HY2_PQ_TABLE" "$(hy2_pq_quota_obj "$port")" >/dev/null 2>&1 \
    && [[ "$input_rules" == *"comment \"$(hy2_pq_comment_drop_in "$port")\""* ]] \
    && [[ "$input_rules" == *"comment \"$(hy2_pq_comment_count_in "$port")\""* ]] \
    && [[ "$output_rules" == *"comment \"$(hy2_pq_comment_drop_out "$port")\""* ]] \
    && [[ "$output_rules" == *"comment \"$(hy2_pq_comment_count_out "$port")\""* ]]
  then
    state='active'
  else
    state='stale'
  fi

  printf '%s|%s|%s|%s\n' "$state" "$original" "$used" "$left"
}

hy2_pq_snapshot() {
  local acquired=0 result
  if [[ "${HY2_PQ_LOCK_HELD:-0}" != "1" ]]; then
    hy2_pq_lock
    acquired=1
  fi
  result="$(hy2_pq_snapshot_unlocked "$1")"
  (( acquired == 0 )) || hy2_pq_unlock
  printf '%s\n' "$result"
}

hy2_pq_state() {
  local snapshot state
  snapshot="$(hy2_pq_snapshot "$1")"
  IFS='|' read -r state _ <<<"$snapshot"
  printf '%s\n' "$state"
}

hy2_pq_write_meta() {
  local port="$1" original="$2" saved="$3" remaining="$4" owner_kind="$5" owner_tag="$6" duration_seconds="$7" expire_epoch="$8" next_reset_epoch="$9" interval_seconds="${10}" created_epoch="${11}" last_reset_epoch="${12}" last_save_epoch="${13}" rebuild_pending="${14:-0}"
  hy2_write_meta "$(hy2_quota_meta_file "$port")" \
    "PORT=${port}" \
    "OWNER_KIND=${owner_kind}" \
    "OWNER_TAG=${owner_tag}" \
    "ORIGINAL_LIMIT_BYTES=${original}" \
    "SAVED_USED_BYTES=${saved}" \
    "LIMIT_BYTES=${remaining}" \
    "USED_BYTES=${saved}" \
    "LEFT_BYTES=${remaining}" \
    "RESET_INTERVAL_SECONDS=${interval_seconds}" \
    "NEXT_RESET_EPOCH=${next_reset_epoch}" \
    "DURATION_SECONDS=${duration_seconds}" \
    "EXPIRE_EPOCH=${expire_epoch}" \
    "CREATED_EPOCH=${created_epoch}" \
    "LAST_RESET_EPOCH=${last_reset_epoch}" \
    "LAST_SAVE_EPOCH=${last_save_epoch}" \
    "REBUILD_PENDING=${rebuild_pending}"
}

hy2_pq_add_managed_port() {
  local port="$1" original_bytes="$2" owner_kind="${3:-manual}" owner_tag="${4:-}" duration_seconds="${5:-0}" expire_epoch="${6:-0}"
  [[ "$port" =~ ^[0-9]+$ ]] || hy2_die "端口必须为整数"
  [[ "$original_bytes" =~ ^[0-9]+$ ]] || hy2_die "original_bytes 必须为整数"
  (( original_bytes > 0 )) || hy2_die "配额必须大于 0"

  hy2_pq_lock
  hy2_pq_ensure_base || return 1

  local created_epoch interval_seconds next_reset_epoch
  created_epoch="$(date +%s)"
  interval_seconds=0
  next_reset_epoch=0
  if [[ "$duration_seconds" =~ ^[0-9]+$ ]] && (( duration_seconds > 2592000 )); then
    interval_seconds=2592000
    next_reset_epoch=$((created_epoch + interval_seconds))
  fi

  (
    trap '' INT TERM HUP
    hy2_pq_write_meta "$port" "$original_bytes" 0 "$original_bytes" "$owner_kind" "$owner_tag" "${duration_seconds:-0}" "${expire_epoch:-0}" "$next_reset_epoch" "$interval_seconds" "$created_epoch" 0 "$created_epoch" 1 \
      && hy2_pq_rebuild_port "$port" "$original_bytes" \
      && hy2_meta_upsert "$(hy2_quota_meta_file "$port")" REBUILD_PENDING 0
  ) || return 1
}

hy2_pq_delete_managed_port() {
  local port="$1" batch
  [[ "$port" =~ ^[0-9]+$ ]] || return 0
  hy2_pq_lock
  if nft list table inet "$HY2_PQ_TABLE" >/dev/null 2>&1; then
    batch="$(mktemp "${HY2_LOCK_DIR}/quota-delete.XXXXXX")"
    hy2_pq_append_atomic_deletes "$batch" "$port"
    if ! nft -f "$batch"; then
      rm -f "$batch"
      return 1
    fi
    rm -f "$batch"
  fi
  rm -f "$(hy2_quota_meta_file "$port")"
}

hy2_pq_save_one() {
  local meta="$1"
  [[ -f "$meta" ]] || return 0
  hy2_pq_meta_owner_exists "$meta" || return 0

  local port original saved live new_saved left next_reset_epoch interval_seconds created_epoch last_reset_epoch owner_kind owner_tag duration_seconds expire_epoch rebuild_pending pending_remaining
  port="$(hy2_meta_get "$meta" PORT || true)"
  [[ "$port" =~ ^[0-9]+$ ]] || return 0
  rebuild_pending="$(hy2_meta_get "$meta" REBUILD_PENDING 2>/dev/null || true)"
  if [[ "$rebuild_pending" == "1" ]]; then
    pending_remaining="$(hy2_meta_get "$meta" LIMIT_BYTES 2>/dev/null || true)"
    [[ "$pending_remaining" =~ ^[0-9]+$ ]] || pending_remaining=0
    (
      trap '' INT TERM HUP
      hy2_pq_rebuild_port "$port" "$pending_remaining" \
        && hy2_meta_upsert "$meta" REBUILD_PENDING 0
    ) || return 1
    return 0
  fi
  original="$(hy2_meta_get "$meta" ORIGINAL_LIMIT_BYTES || true)"
  saved="$(hy2_meta_get "$meta" SAVED_USED_BYTES || true)"
  owner_kind="$(hy2_meta_get "$meta" OWNER_KIND || true)"
  owner_tag="$(hy2_meta_get "$meta" OWNER_TAG || true)"
  duration_seconds="$(hy2_meta_get "$meta" DURATION_SECONDS || true)"
  expire_epoch="$(hy2_meta_get "$meta" EXPIRE_EPOCH || true)"
  next_reset_epoch="$(hy2_meta_get "$meta" NEXT_RESET_EPOCH || true)"
  interval_seconds="$(hy2_meta_get "$meta" RESET_INTERVAL_SECONDS || true)"
  created_epoch="$(hy2_meta_get "$meta" CREATED_EPOCH || true)"
  last_reset_epoch="$(hy2_meta_get "$meta" LAST_RESET_EPOCH || true)"
  original="${original:-0}"
  saved="${saved:-0}"
  [[ "$original" =~ ^[0-9]+$ && "$saved" =~ ^[0-9]+$ ]] || return 1
  # Never persist an unreadable nft snapshot as zero usage.
  live="$(hy2_pq_live_used_bytes "$port")" || return 1
  new_saved=$((saved + live))
  if (( new_saved > original )); then
    new_saved="$original"
  fi
  left=$((original - new_saved))
  if (( left < 0 )); then
    left=0
  fi
  (
    trap '' INT TERM HUP
    hy2_pq_write_meta "$port" "$original" "$new_saved" "$left" "$owner_kind" "$owner_tag" "${duration_seconds:-0}" "${expire_epoch:-0}" "${next_reset_epoch:-0}" "${interval_seconds:-0}" "${created_epoch:-$(date +%s)}" "${last_reset_epoch:-0}" "$(date +%s)" 1 \
      && hy2_pq_rebuild_port "$port" "$left" \
      && hy2_meta_upsert "$meta" REBUILD_PENDING 0
  ) || return 1
  hy2_log pq.log "[save] port=${port} used=${new_saved} left=${left}"
}

hy2_pq_restore_one() {
  local meta="$1"
  [[ -f "$meta" ]] || return 0
  hy2_pq_meta_owner_exists "$meta" || return 0
  local port remaining
  port="$(hy2_meta_get "$meta" PORT || true)"
  remaining="$(hy2_meta_get "$meta" LIMIT_BYTES || true)"
  [[ "$port" =~ ^[0-9]+$ ]] || return 0
  [[ "$remaining" =~ ^[0-9]+$ ]] || remaining=0
  (
    trap '' INT TERM HUP
    hy2_pq_rebuild_port "$port" "$remaining" \
      && hy2_meta_upsert "$meta" REBUILD_PENDING 0
  ) || return 1
}

hy2_pq_reset_due_one() {
  local meta="$1"
  [[ -f "$meta" ]] || return 0
  hy2_pq_meta_owner_exists "$meta" || return 0

  local port original owner_kind owner_tag duration_seconds expire_epoch interval_seconds next_reset_epoch created_epoch now last_reset_epoch
  port="$(hy2_meta_get "$meta" PORT || true)"
  [[ "$port" =~ ^[0-9]+$ ]] || return 0
  original="$(hy2_meta_get "$meta" ORIGINAL_LIMIT_BYTES || true)"
  owner_kind="$(hy2_meta_get "$meta" OWNER_KIND || true)"
  owner_tag="$(hy2_meta_get "$meta" OWNER_TAG || true)"
  duration_seconds="$(hy2_meta_get "$meta" DURATION_SECONDS || true)"
  expire_epoch="$(hy2_meta_get "$meta" EXPIRE_EPOCH || true)"
  interval_seconds="$(hy2_meta_get "$meta" RESET_INTERVAL_SECONDS || true)"
  next_reset_epoch="$(hy2_meta_get "$meta" NEXT_RESET_EPOCH || true)"
  created_epoch="$(hy2_meta_get "$meta" CREATED_EPOCH || true)"
  last_reset_epoch="$(hy2_meta_get "$meta" LAST_RESET_EPOCH || true)"

  [[ "$original" =~ ^[0-9]+$ ]] && (( original > 0 )) || return 1
  [[ "$interval_seconds" =~ ^[0-9]+$ ]] || interval_seconds=0
  (( interval_seconds > 0 )) || return 0
  now="$(date +%s)"
  [[ "$next_reset_epoch" =~ ^[0-9]+$ ]] || next_reset_epoch=0
  (( next_reset_epoch > 0 )) || return 0
  if [[ "$expire_epoch" =~ ^[0-9]+$ ]] && (( expire_epoch > 0 && expire_epoch <= now )); then
    return 0
  fi
  (( now >= next_reset_epoch )) || return 0

  while (( next_reset_epoch <= now )); do
    next_reset_epoch=$((next_reset_epoch + interval_seconds))
  done

  (
    trap '' INT TERM HUP
    hy2_pq_write_meta "$port" "$original" 0 "$original" "$owner_kind" "$owner_tag" "${duration_seconds:-0}" "${expire_epoch:-0}" "$next_reset_epoch" "$interval_seconds" "${created_epoch:-$now}" "$now" "$now" 1 \
      && hy2_pq_rebuild_port "$port" "$original" \
      && hy2_meta_upsert "$meta" REBUILD_PENDING 0
  ) || return 1
  hy2_log pq.log "[reset] port=${port} reset_to=${original} next_reset=${next_reset_epoch}"
}
EOF
  chmod 644 "${HY2_LIB_DIR}/quota-lib.sh"
}

install_iplimit_lib() {
  cat >"${HY2_LIB_DIR}/iplimit-lib.sh" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail

# shellcheck disable=SC1091
source /usr/local/lib/hy2/common.sh

HY2_IL_TABLE="hy2_iplimit"
HY2_IL_INPUT_CHAIN="il_input"
HY2_IL_LOCK_FILE="${HY2_LOCK_DIR}/iplimit.lock"

hy2_il_lock() {
  if [[ "${HY2_IL_LOCK_HELD:-0}" != "1" ]]; then
    hy2_acquire_lock_fd 8 "$HY2_IL_LOCK_FILE" 20 "iplimit 锁繁忙"
    export HY2_IL_LOCK_HELD=1
  fi
}

hy2_il_unlock() {
  if [[ "${HY2_IL_LOCK_HELD:-0}" == "1" ]]; then
    flock -u 8 >/dev/null 2>&1 || true
    { exec 8>&-; } 2>/dev/null || true
    unset HY2_IL_LOCK_HELD
  fi
}

hy2_il_set_name() {
  local port="$1" ip_version="${2:-4}"
  if [[ "$ip_version" == "6" ]]; then
    printf 'hy2_il6_%s\n' "$port"
  else
    printf 'hy2_il4_%s\n' "$port"
  fi
}
hy2_il_comment_refresh() { printf 'hy2-il-refresh-%s\n' "$1"; }
hy2_il_comment_claim() { printf 'hy2-il-claim-%s\n' "$1"; }
hy2_il_comment_drop() { printf 'hy2-il-drop-%s\n' "$1"; }
hy2_il_comment_family() { printf 'hy2-il-family-%s\n' "$1"; }

hy2_il_meta_owner_exists() {
  local meta="$1" owner_tag owner_kind
  owner_tag="$(hy2_meta_get "$meta" OWNER_TAG 2>/dev/null || true)"
  owner_kind="$(hy2_meta_get "$meta" OWNER_KIND 2>/dev/null || true)"
  if [[ "$owner_kind" == "temp" && -n "$owner_tag" ]]; then
    [[ -f "$(hy2_temp_meta_file "$owner_tag")" ]] || return 1
  fi
  return 0
}

hy2_il_ensure_base() {
  hy2_ensure_runtime_dirs || return 1
  command -v nft >/dev/null 2>&1 || hy2_die "未找到 nft 命令"
  if ! nft list table inet "$HY2_IL_TABLE" >/dev/null 2>&1; then
    nft add table inet "$HY2_IL_TABLE" || return 1
  fi
  if ! nft list chain inet "$HY2_IL_TABLE" "$HY2_IL_INPUT_CHAIN" >/dev/null 2>&1; then
    nft add chain inet "$HY2_IL_TABLE" "$HY2_IL_INPUT_CHAIN" '{ type filter hook input priority -10; policy accept; }' || return 1
  fi
}

hy2_il_delete_rules_with_comment() {
  local comment="$1"
  nft -a list chain inet "$HY2_IL_TABLE" "$HY2_IL_INPUT_CHAIN" 2>/dev/null \
    | awk -v c="comment \"${comment}\"" '$0 ~ c {print $NF}' \
    | sort -rn \
    | while read -r handle; do
        [[ -n "$handle" ]] || continue
        nft delete rule inet "$HY2_IL_TABLE" "$HY2_IL_INPUT_CHAIN" handle "$handle" >/dev/null 2>&1 || true
      done
}

hy2_il_rule_comment_exists() {
  local comment="$1"
  local rules

  # Avoid `nft | grep -q` under pipefail: grep may exit early after a match,
  # causing nft to receive SIGPIPE and turning a real match into a false error.
  rules="$(
    nft -a list chain inet "$HY2_IL_TABLE" "$HY2_IL_INPUT_CHAIN" 2>/dev/null || true
  )"
  [[ "$rules" == *"comment \"${comment}\""* ]]
}

hy2_il_delete_port_rules() {
  local port="$1"
  hy2_il_delete_rules_with_comment "$(hy2_il_comment_refresh "$port")"
  hy2_il_delete_rules_with_comment "$(hy2_il_comment_claim "$port")"
  hy2_il_delete_rules_with_comment "$(hy2_il_comment_drop "$port")"
  hy2_il_delete_rules_with_comment "$(hy2_il_comment_family "$port")"
}

hy2_il_delete_port_sets() {
  local port="$1"
  nft delete set inet "$HY2_IL_TABLE" "$(hy2_il_set_name "$port" 4)" >/dev/null 2>&1 || true
  nft delete set inet "$HY2_IL_TABLE" "$(hy2_il_set_name "$port" 6)" >/dev/null 2>&1 || true
  nft delete set inet "$HY2_IL_TABLE" "hy2_il_${port}" >/dev/null 2>&1 || true
}

hy2_il_append_atomic_deletes() {
  local batch="$1" port="$2" comment handle set_name
  for comment in \
    "$(hy2_il_comment_refresh "$port")" \
    "$(hy2_il_comment_claim "$port")" \
    "$(hy2_il_comment_drop "$port")" \
    "$(hy2_il_comment_family "$port")"
  do
    while IFS= read -r handle; do
      [[ "$handle" =~ ^[0-9]+$ ]] || continue
      printf 'delete rule inet %s %s handle %s\n' "$HY2_IL_TABLE" "$HY2_IL_INPUT_CHAIN" "$handle" >>"$batch"
    done < <(
      nft -a list chain inet "$HY2_IL_TABLE" "$HY2_IL_INPUT_CHAIN" 2>/dev/null \
        | awk -v c="comment \"${comment}\"" '$0 ~ c {print $NF}' | sort -rn
    )
  done
  for set_name in "$(hy2_il_set_name "$port" 4)" "$(hy2_il_set_name "$port" 6)" "hy2_il_${port}"; do
    if nft list set inet "$HY2_IL_TABLE" "$set_name" >/dev/null 2>&1; then
      printf 'delete set inet %s %s\n' "$HY2_IL_TABLE" "$set_name" >>"$batch"
    fi
  done
}

hy2_il_failsafe_block_port() {
  local port="$1" batch
  hy2_il_ensure_base || return 1
  batch="$(mktemp "${HY2_LOCK_DIR}/iplimit-failsafe.XXXXXX")"
  hy2_il_append_atomic_deletes "$batch" "$port"
  printf 'add rule inet %s %s udp dport %s drop comment "%s"\n' \
    "$HY2_IL_TABLE" "$HY2_IL_INPUT_CHAIN" "$port" "$(hy2_il_comment_drop "$port")" >>"$batch"
  if ! nft -f "$batch"; then
    rm -f "$batch"
    return 1
  fi
  rm -f "$batch"
}

hy2_il_apply_family_guard() {
  local port="$1" ip_version="${2:-4}" batch handle
  [[ "$port" =~ ^[0-9]+$ ]] || hy2_die "hy2_il_apply_family_guard: bad port ${port}"
  [[ "$ip_version" == "4" || "$ip_version" == "6" ]] || hy2_die "IP_VERSION 只能是 4 或 6"
  hy2_il_lock
  hy2_il_ensure_base || return 1
  batch="$(mktemp "${HY2_LOCK_DIR}/iplimit-family.XXXXXX")"
  while IFS= read -r handle; do
    [[ "$handle" =~ ^[0-9]+$ ]] || continue
    printf 'delete rule inet %s %s handle %s\n' "$HY2_IL_TABLE" "$HY2_IL_INPUT_CHAIN" "$handle" >>"$batch"
  done < <(
    nft -a list chain inet "$HY2_IL_TABLE" "$HY2_IL_INPUT_CHAIN" 2>/dev/null \
      | awk -v c="comment \"$(hy2_il_comment_family "$port")\"" '$0 ~ c {print $NF}' | sort -rn
  )
  if [[ "$ip_version" == "6" ]]; then
    printf 'add rule inet %s %s meta nfproto ipv4 udp dport %s drop comment "%s"\n' \
      "$HY2_IL_TABLE" "$HY2_IL_INPUT_CHAIN" "$port" "$(hy2_il_comment_family "$port")" >>"$batch"
  else
    printf 'add rule inet %s %s meta nfproto ipv6 udp dport %s drop comment "%s"\n' \
      "$HY2_IL_TABLE" "$HY2_IL_INPUT_CHAIN" "$port" "$(hy2_il_comment_family "$port")" >>"$batch"
  fi
  if ! nft -f "$batch"; then
    rm -f "$batch"
    hy2_il_failsafe_block_port "$port" || true
    return 1
  fi
  rm -f "$batch"
}

hy2_il_family_guard_state_unlocked() {
  local port="$1" rules comment
  rules="$(
    nft -a list chain inet "$HY2_IL_TABLE" "$HY2_IL_INPUT_CHAIN" 2>/dev/null || true
  )"
  comment="$(hy2_il_comment_family "$port")"
  if [[ "$rules" == *"comment \"${comment}\""* ]]; then
    printf 'active\n'
  else
    printf 'stale\n'
  fi
}

hy2_il_family_guard_state() {
  local acquired=0 result
  if [[ "${HY2_IL_LOCK_HELD:-0}" != "1" ]]; then
    hy2_il_lock
    acquired=1
  fi
  result="$(hy2_il_family_guard_state_unlocked "$1")"
  (( acquired == 0 )) || hy2_il_unlock
  printf '%s\n' "$result"
}

hy2_il_rebuild_port() {
  local port="$1" ip_limit="$2" sticky_seconds="$3" ip_version="${4:-4}" batch
  [[ "$port" =~ ^[0-9]+$ ]] || hy2_die "hy2_il_rebuild_port: bad port ${port}"
  [[ "$ip_limit" =~ ^[0-9]+$ ]] && (( ip_limit > 0 )) || hy2_die "hy2_il_rebuild_port: bad limit ${ip_limit}"
  [[ "$sticky_seconds" =~ ^[0-9]+$ ]] && (( sticky_seconds > 0 )) || hy2_die "hy2_il_rebuild_port: bad sticky ${sticky_seconds}"
  [[ "$ip_version" == "4" || "$ip_version" == "6" ]] || hy2_die "hy2_il_rebuild_port: bad IP_VERSION ${ip_version}"

  hy2_il_lock
  hy2_il_ensure_base || return 1
  batch="$(mktemp "${HY2_LOCK_DIR}/iplimit-rebuild.XXXXXX")"
  hy2_il_append_atomic_deletes "$batch" "$port"

  if [[ "$ip_version" == "6" ]]; then
    cat >>"$batch" <<EOF_RULES
add rule inet ${HY2_IL_TABLE} ${HY2_IL_INPUT_CHAIN} meta nfproto ipv4 udp dport ${port} drop comment "$(hy2_il_comment_family "$port")"
add set inet ${HY2_IL_TABLE} $(hy2_il_set_name "$port" 6) { type ipv6_addr; size ${ip_limit}; flags timeout,dynamic; timeout ${sticky_seconds}s; }
add rule inet ${HY2_IL_TABLE} ${HY2_IL_INPUT_CHAIN} meta nfproto ipv6 udp dport ${port} ip6 saddr @$(hy2_il_set_name "$port" 6) update @$(hy2_il_set_name "$port" 6) { ip6 saddr timeout ${sticky_seconds}s } accept comment "$(hy2_il_comment_refresh "$port")"
add rule inet ${HY2_IL_TABLE} ${HY2_IL_INPUT_CHAIN} meta nfproto ipv6 udp dport ${port} add @$(hy2_il_set_name "$port" 6) { ip6 saddr timeout ${sticky_seconds}s } accept comment "$(hy2_il_comment_claim "$port")"
add rule inet ${HY2_IL_TABLE} ${HY2_IL_INPUT_CHAIN} meta nfproto ipv6 udp dport ${port} drop comment "$(hy2_il_comment_drop "$port")"
EOF_RULES
  else
    cat >>"$batch" <<EOF_RULES
add rule inet ${HY2_IL_TABLE} ${HY2_IL_INPUT_CHAIN} meta nfproto ipv6 udp dport ${port} drop comment "$(hy2_il_comment_family "$port")"
add set inet ${HY2_IL_TABLE} $(hy2_il_set_name "$port" 4) { type ipv4_addr; size ${ip_limit}; flags timeout,dynamic; timeout ${sticky_seconds}s; }
add rule inet ${HY2_IL_TABLE} ${HY2_IL_INPUT_CHAIN} meta nfproto ipv4 udp dport ${port} ip saddr @$(hy2_il_set_name "$port" 4) update @$(hy2_il_set_name "$port" 4) { ip saddr timeout ${sticky_seconds}s } accept comment "$(hy2_il_comment_refresh "$port")"
add rule inet ${HY2_IL_TABLE} ${HY2_IL_INPUT_CHAIN} meta nfproto ipv4 udp dport ${port} add @$(hy2_il_set_name "$port" 4) { ip saddr timeout ${sticky_seconds}s } accept comment "$(hy2_il_comment_claim "$port")"
add rule inet ${HY2_IL_TABLE} ${HY2_IL_INPUT_CHAIN} meta nfproto ipv4 udp dport ${port} drop comment "$(hy2_il_comment_drop "$port")"
EOF_RULES
  fi
  if ! nft -f "$batch"; then
    rm -f "$batch"
    hy2_il_failsafe_block_port "$port" || true
    return 1
  fi
  rm -f "$batch"
}

hy2_il_write_meta() {
  local port="$1" owner_kind="$2" owner_tag="$3" ip_limit="$4" sticky_seconds="$5" ip_version="${6:-4}"
  hy2_write_meta "$(hy2_iplimit_meta_file "$port")" \
    "PORT=${port}" \
    "OWNER_KIND=${owner_kind}" \
    "OWNER_TAG=${owner_tag}" \
    "IP_LIMIT=${ip_limit}" \
    "IP_STICKY_SECONDS=${sticky_seconds}" \
    "IP_VERSION=${ip_version}" \
    "SET_NAME=$(hy2_il_set_name "$port" "$ip_version")" \
    "CREATED_EPOCH=$(date +%s)"
}

hy2_il_add_managed_port() {
  local port="$1" ip_limit="$2" sticky_seconds="$3" owner_kind="${4:-temp}" owner_tag="${5:-}" ip_version="${6:-4}"
  [[ "$port" =~ ^[0-9]+$ ]] || hy2_die "端口必须为整数"
  [[ "$ip_limit" =~ ^[0-9]+$ ]] && (( ip_limit > 0 )) || hy2_die "IP_LIMIT 必须为正整数"
  [[ "$sticky_seconds" =~ ^[0-9]+$ ]] && (( sticky_seconds > 0 )) || hy2_die "IP_STICKY_SECONDS 必须为正整数"
  [[ "$ip_version" == "4" || "$ip_version" == "6" ]] || hy2_die "IP_VERSION 只能是 4 或 6"
  hy2_il_lock
  hy2_il_ensure_base || return 1
  # Keep metadata and the nftables rules as one interruption-safe commit.  A
  # caller still receives its pending signal after this short child finishes,
  # while the child and nft process ignore ordinary termination signals.
  (
    trap '' INT TERM HUP
    hy2_il_write_meta "$port" "$owner_kind" "$owner_tag" "$ip_limit" "$sticky_seconds" "$ip_version" \
      && hy2_il_rebuild_port "$port" "$ip_limit" "$sticky_seconds" "$ip_version"
  ) || return 1
}

hy2_il_delete_managed_port() {
  local port="$1" batch
  [[ "$port" =~ ^[0-9]+$ ]] || return 0
  hy2_il_lock
  if nft list table inet "$HY2_IL_TABLE" >/dev/null 2>&1; then
    batch="$(mktemp "${HY2_LOCK_DIR}/iplimit-delete.XXXXXX")"
    hy2_il_append_atomic_deletes "$batch" "$port"
    if ! nft -f "$batch"; then
      rm -f "$batch"
      return 1
    fi
    rm -f "$batch"
  fi
  rm -f "$(hy2_iplimit_meta_file "$port")"
}

# Atomically swap a port's per-source-IP limit for family-isolation-only in a
# single nft batch, so there is no window where both address families and an
# unlimited number of source IPs are momentarily accepted.  Only removes the
# iplimit meta file after the nft transaction commits.
hy2_il_delete_and_apply_family_guard() {
  local port="$1" ip_version="${2:-4}" batch
  [[ "$port" =~ ^[0-9]+$ ]] || hy2_die "hy2_il_delete_and_apply_family_guard: bad port ${port}"
  [[ "$ip_version" == "4" || "$ip_version" == "6" ]] || hy2_die "IP_VERSION 只能是 4 或 6"
  hy2_il_lock
  hy2_il_ensure_base || return 1
  batch="$(mktemp "${HY2_LOCK_DIR}/iplimit-swap.XXXXXX")"
  hy2_il_append_atomic_deletes "$batch" "$port"
  if [[ "$ip_version" == "6" ]]; then
    printf 'add rule inet %s %s meta nfproto ipv4 udp dport %s drop comment "%s"\n' \
      "$HY2_IL_TABLE" "$HY2_IL_INPUT_CHAIN" "$port" "$(hy2_il_comment_family "$port")" >>"$batch"
  else
    printf 'add rule inet %s %s meta nfproto ipv6 udp dport %s drop comment "%s"\n' \
      "$HY2_IL_TABLE" "$HY2_IL_INPUT_CHAIN" "$port" "$(hy2_il_comment_family "$port")" >>"$batch"
  fi
  if ! nft -f "$batch"; then
    rm -f "$batch"
    hy2_il_failsafe_block_port "$port" || true
    return 1
  fi
  rm -f "$batch"
  rm -f "$(hy2_iplimit_meta_file "$port")"
}

hy2_il_restore_one() {
  local meta="$1"
  [[ -f "$meta" ]] || return 0
  hy2_il_meta_owner_exists "$meta" || return 0
  local port ip_limit sticky_seconds ip_version
  port="$(hy2_meta_get "$meta" PORT || true)"
  ip_limit="$(hy2_meta_get "$meta" IP_LIMIT || true)"
  sticky_seconds="$(hy2_meta_get "$meta" IP_STICKY_SECONDS || true)"
  ip_version="$(hy2_meta_get "$meta" IP_VERSION 2>/dev/null || true)"
  ip_version="${ip_version:-4}"
  [[ "$port" =~ ^[0-9]+$ ]] || return 0
  [[ "$ip_limit" =~ ^[0-9]+$ ]] && (( ip_limit > 0 )) || return 0
  [[ "$sticky_seconds" =~ ^[0-9]+$ ]] && (( sticky_seconds > 0 )) || return 0
  [[ "$ip_version" == "4" || "$ip_version" == "6" ]] || ip_version=4
  hy2_il_rebuild_port "$port" "$ip_limit" "$sticky_seconds" "$ip_version" || return 1
}

hy2_il_active_ips() {
  local port="$1" ip_version="${2:-}" set_name
  if [[ -z "$ip_version" ]]; then
    ip_version="$(hy2_meta_get "$(hy2_iplimit_meta_file "$port")" IP_VERSION 2>/dev/null || true)"
    ip_version="${ip_version:-4}"
  fi
  set_name="$(hy2_il_set_name "$port" "$ip_version")"
  nft -j list set inet "$HY2_IL_TABLE" "$set_name" 2>/dev/null \
    | python3 -c '
import ipaddress
import json
import sys
version = int(sys.argv[1])
try:
    obj = json.load(sys.stdin)
except Exception:
    raise SystemExit(0)
values = []
def walk(x):
    if isinstance(x, dict):
        for k, v in x.items():
            if k == "val" and isinstance(v, str):
                try:
                    ip = ipaddress.ip_address(v)
                    if ip.version == version:
                        values.append(str(ip))
                except Exception:
                    pass
            walk(v)
    elif isinstance(x, list):
        for v in x:
            walk(v)
walk(obj)
print(" ".join(dict.fromkeys(values)))
' "$ip_version"
}

hy2_il_active_count() {
  local port="$1" ips
  ips="$(hy2_il_active_ips "$port" || true)"
  if [[ -z "$ips" ]]; then
    printf '0\n'
  else
    wc -w <<<"$ips" | tr -d ' '
  fi
}

hy2_il_state_unlocked() {
  local port="$1"
  local meta ip_version set_name rules comment

  meta="$(hy2_iplimit_meta_file "$port")"
  [[ -f "$meta" ]] || { printf 'none\n'; return 0; }

  ip_version="$(hy2_meta_get "$meta" IP_VERSION 2>/dev/null || true)"
  ip_version="${ip_version:-4}"
  if [[ "$ip_version" != "4" && "$ip_version" != "6" ]]; then
    printf 'stale\n'
    return 0
  fi

  set_name="$(hy2_il_set_name "$port" "$ip_version")"
  if ! nft list set inet "$HY2_IL_TABLE" "$set_name" >/dev/null 2>&1; then
    printf 'stale\n'
    return 0
  fi

  rules="$(
    nft -a list chain inet "$HY2_IL_TABLE" "$HY2_IL_INPUT_CHAIN" 2>/dev/null || true
  )"
  [[ -n "$rules" ]] || { printf 'stale\n'; return 0; }

  for comment in \
    "$(hy2_il_comment_refresh "$port")" \
    "$(hy2_il_comment_claim "$port")" \
    "$(hy2_il_comment_drop "$port")" \
    "$(hy2_il_comment_family "$port")"
  do
    if [[ "$rules" != *"comment \"${comment}\""* ]]; then
      printf 'stale\n'
      return 0
    fi
  done

  printf 'active\n'
}

hy2_il_state() {
  local acquired=0 result
  if [[ "${HY2_IL_LOCK_HELD:-0}" != "1" ]]; then
    hy2_il_lock
    acquired=1
  fi
  result="$(hy2_il_state_unlocked "$1")"
  (( acquired == 0 )) || hy2_il_unlock
  printf '%s\n' "$result"
}

EOF
  chmod 644 "${HY2_LIB_DIR}/iplimit-lib.sh"
}

install_main_script() {
  cat >"/root/onekey_hy2_main_tls.sh" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# shellcheck disable=SC1091
source /usr/local/lib/hy2/common.sh

TX_ACTIVE=0
TX_DIR=""
OLD_SERVICE_ACTIVE=0
OLD_SERVICE_ENABLED=""
OLD_TEMP_ACTIVE_UNITS=()
HY2_CORE_OLD_BIN_PATH=""
HY2_CORE_NEW_BIN_PATH=""
TX_TARGETS=(
  "$HY2_MAIN_CFG"
  /etc/systemd/system/hy2.service
  /etc/sysctl.d/99-hy2-bbr.conf
  /usr/local/bin/hysteria
  /usr/bin/hysteria
  "$HY2_RENEW_HOOK"
  "$HY2_MAIN_STATE_FILE"
  "$HY2_MAIN_PASSWORD_FILE"
  /root/hy2_main_url.txt
  /root/hy2_main_subscription_base64.txt
)

tx_key() {
  printf '%s' "$1" | sha256sum | awk '{print $1}'
}

begin_transaction() {
  local path key already=0
  HY2_CORE_OLD_BIN_PATH="$(command -v hysteria || true)"
  if [[ -n "$HY2_CORE_OLD_BIN_PATH" ]]; then
    for path in "${TX_TARGETS[@]}"; do
      [[ "$path" == "$HY2_CORE_OLD_BIN_PATH" ]] && already=1
    done
    (( already == 1 )) || TX_TARGETS+=("$HY2_CORE_OLD_BIN_PATH")
  fi
  TX_DIR="$(mktemp -d /var/tmp/hy2-main-transaction.XXXXXX)"
  for path in "${TX_TARGETS[@]}"; do
    key="$(tx_key "$path")"
    if [[ -e "$path" || -L "$path" ]]; then
      cp -a -- "$path" "${TX_DIR}/${key}"
      : >"${TX_DIR}/${key}.present"
    fi
  done
  OLD_SERVICE_ENABLED="$(systemctl is-enabled hy2.service 2>/dev/null || true)"
  systemctl is-active --quiet hy2.service 2>/dev/null && OLD_SERVICE_ACTIVE=1 || true
  mapfile -t OLD_TEMP_ACTIVE_UNITS < <(
    systemctl list-units --type=service --state=active --no-legend 'hy2-temp-*.service' 2>/dev/null       | awk '$1 ~ /^hy2-temp-[A-Za-z0-9._-]+[.]service$/ {print $1}'
  )
  TX_ACTIVE=1
}

rollback_transaction() {
  (( TX_ACTIVE == 1 )) || return 0
  TX_ACTIVE=0
  set +e
  trap '' INT TERM HUP
  systemctl stop hy2.service >/dev/null 2>&1 || true
  local path key
  for path in "${TX_TARGETS[@]}"; do
    key="$(tx_key "$path")"
    rm -f -- "$path"
    if [[ -f "${TX_DIR}/${key}.present" ]]; then
      install -d -m 755 "$(dirname "$path")"
      cp -a -- "${TX_DIR}/${key}" "$path"
    fi
  done
  if [[ -n "$HY2_CORE_NEW_BIN_PATH" ]]; then
    key="$(tx_key "$HY2_CORE_NEW_BIN_PATH")"
    if [[ ! -f "${TX_DIR}/${key}.present" ]]; then
      rm -f -- "$HY2_CORE_NEW_BIN_PATH"
    fi
  fi
  systemctl daemon-reload >/dev/null 2>&1 || true
  case "$OLD_SERVICE_ENABLED" in
    enabled) systemctl enable hy2.service >/dev/null 2>&1 || true ;;
    enabled-runtime) systemctl enable --runtime hy2.service >/dev/null 2>&1 || true ;;
    masked) systemctl mask hy2.service >/dev/null 2>&1 || true ;;
    masked-runtime) systemctl mask --runtime hy2.service >/dev/null 2>&1 || true ;;
    *) systemctl disable hy2.service >/dev/null 2>&1 || true ;;
  esac
  if (( OLD_SERVICE_ACTIVE == 1 )); then
    systemctl restart hy2.service >/dev/null 2>&1 || true
  fi
  local old_temp_unit
  for old_temp_unit in "${OLD_TEMP_ACTIVE_UNITS[@]}"; do
    timeout 60 systemctl restart "$old_temp_unit" >/dev/null 2>&1 || true
  done
  rm -rf -- "$TX_DIR"
}

on_error() {
  local rc=$?
  trap - ERR
  echo "❌ ${BASH_SOURCE[1]:-${BASH_SOURCE[0]}}:${BASH_LINENO[0]:-?}: ${BASH_COMMAND}" >&2
  exit "$rc"
}

on_exit() {
  local rc=$?
  trap - EXIT ERR
  rollback_transaction || true
  exit "$rc"
}
trap 'on_error' ERR
trap 'on_exit' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

commit_transaction() {
  TX_ACTIVE=0
  rm -rf -- "$TX_DIR"
  TX_DIR=""
}


secure_download_script() {
  local url="$1" destination="$2" expected="${3:-}" label="$4"
  local tmp actual policy
  policy="${REMOTE_SCRIPT_POLICY:-warn}"
  [[ "$policy" == "warn" || "$policy" == "require" ]] \
    || hy2_die "REMOTE_SCRIPT_POLICY 只能是 warn 或 require"
  [[ "$url" == https://* ]] || hy2_die "${label} URL 必须使用 HTTPS：${url}"
  if [[ -n "$expected" && ! "$expected" =~ ^[0-9A-Fa-f]{64}$ ]]; then
    hy2_die "${label} SHA-256 必须是 64 位十六进制"
  fi
  if [[ "$policy" == "require" && -z "$expected" ]]; then
    hy2_die "REMOTE_SCRIPT_POLICY=require，但没有设置 ${label} SHA-256"
  fi

  install -d -m 755 "$(dirname "$destination")"
  tmp="$(mktemp "${destination}.tmp.XXXXXX")"
  if ! curl --proto '=https' --tlsv1.2 -fsSL --retry 3 --retry-all-errors \
      --connect-timeout 10 --max-time 120 "$url" -o "$tmp"; then
    rm -f -- "$tmp"
    hy2_die "下载 ${label} 失败：${url}"
  fi
  [[ -s "$tmp" ]] || { rm -f -- "$tmp"; hy2_die "${label} 下载结果为空"; }
  (( $(wc -c <"$tmp") <= 1048576 )) \
    || { rm -f -- "$tmp"; hy2_die "${label} 异常大，拒绝执行"; }
  bash -n "$tmp" || { rm -f -- "$tmp"; hy2_die "${label} 语法检查失败"; }

  actual="$(sha256sum "$tmp" | awk '{print $1}')"
  if [[ -n "$expected" && "${actual,,}" != "${expected,,}" ]]; then
    rm -f -- "$tmp"
    hy2_die "${label} SHA-256 不匹配：期望 ${expected}，实际 ${actual}"
  fi
  if [[ -z "$expected" ]]; then
    echo "⚠️  ${label} 未配置固定 SHA-256；本次实际值：${actual}" >&2
  fi
  printf '%s  %s\n' "$actual" "$(basename "$destination")" >"${destination}.sha256"
  chmod 600 "${destination}.sha256"
  chmod 700 "$tmp"
  mv -f -- "$tmp" "$destination"
}

validate_local_hysteria_binary() {
  local binary="$1" mode owner
  [[ "$binary" == /* && -f "$binary" && -x "$binary" ]] \
    || hy2_die "HYSTERIA_LOCAL_BINARY 必须是可执行普通文件的绝对路径"
  owner="$(stat -c %u "$binary" 2>/dev/null || echo -1)"
  mode="$(stat -c %a "$binary" 2>/dev/null || echo 777)"
  [[ "$owner" == "0" ]] || hy2_die "本地 Hysteria 二进制必须属于 root"
  [[ "$mode" =~ ^[0-7]{3,4}$ ]] && (( ((8#$mode) & 8#022) == 0 )) \
    || hy2_die "本地 Hysteria 二进制不能被 group/other 写入"
  timeout 10 "$binary" version >/dev/null 2>&1 \
    || hy2_die "本地 Hysteria 二进制无法执行 version 命令"
}

hy2_collect_active_managed_units() {
  if systemctl is-active --quiet hy2.service 2>/dev/null; then
    printf 'hy2.service\n'
  fi
  systemctl list-units --type=service --state=active --no-legend 'hy2-temp-*.service' 2>/dev/null \
    | awk '$1 ~ /^hy2-temp-[A-Za-z0-9._-]+[.]service$/ {print $1}'
}

hy2_free_udp_port() {
  local family="${1:-4}"
  python3 - "$family" <<'PY'
import socket, sys
family = socket.AF_INET6 if sys.argv[1] == '6' else socket.AF_INET
host = '::1' if family == socket.AF_INET6 else '127.0.0.1'
s = socket.socket(family, socket.SOCK_DGRAM)
s.bind((host, 0))
print(s.getsockname()[1])
s.close()
PY
}

hy2_validate_config_with_binary() {
  local binary="$1" cfg="$2" family=4 host="127.0.0.1" port test_cfg log pid ok=0
  [[ -s "$cfg" ]] || return 0
  if sed -nE 's/^[[:space:]]*listen:[[:space:]]*(.+)$/\1/p' "$cfg" | head -n1 | grep -q '\[::\]'; then
    family=6
    host='[::1]'
  fi
  port="$(hy2_free_udp_port "$family")" || return 1
  test_cfg="$(mktemp /var/tmp/hy2-config-check.XXXXXX.yaml)"
  log="${test_cfg}.log"
  python3 - "$cfg" "$test_cfg" "$host" "$port" <<'PY'
from pathlib import Path
import re, sys
src, dst, host, port = sys.argv[1:]
lines = Path(src).read_text(encoding='utf-8').splitlines()
for i, line in enumerate(lines):
    if re.match(r'^\s*listen\s*:', line):
        lines[i] = f"listen: '{host}:{port}'"
        break
else:
    lines.insert(0, f"listen: '{host}:{port}'")
Path(dst).write_text('\n'.join(lines) + '\n', encoding='utf-8')
PY

  timeout --signal=TERM --kill-after=2 12 "$binary" server -c "$test_cfg" >"$log" 2>&1 &
  pid=$!
  local ss_dump
  for _ in $(seq 1 40); do
    if ! kill -0 "$pid" 2>/dev/null; then
      break
    fi
    # Snapshot ss output first: piping ss directly into `grep -q` under
    # pipefail can turn a real match into a false failure when grep exits on
    # the first hit and ss receives SIGPIPE.
    ss_dump="$(ss -H -l -u -n "-${family}" 2>/dev/null || true)"
    if [[ "$ss_dump" =~ :${port}([[:space:]]|$) ]]; then
      ok=1
      break
    fi
    sleep 0.2
  done
  kill -TERM "$pid" >/dev/null 2>&1 || true
  wait "$pid" >/dev/null 2>&1 || true
  if (( ok == 0 )); then
    echo "❌ 新 Hysteria 无法加载现有配置：${cfg}" >&2
    cat "$log" >&2 || true
    rm -f -- "$test_cfg" "$log"
    return 1
  fi
  rm -f -- "$test_cfg" "$log"
}

hy2_managed_unit_port() {
  local unit="$1" tag meta port
  if [[ "$unit" == "hy2.service" ]]; then
    hy2_main_port
    return
  fi
  tag="${unit%.service}"
  meta="$(hy2_temp_meta_file "$tag")"
  port="$(hy2_meta_get "$meta" PORT 2>/dev/null || true)"
  [[ "$port" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "$port"
}

hy2_restart_verify_managed_unit() {
  local unit="$1" port
  port="$(hy2_managed_unit_port "$unit")" || return 1
  timeout 60 systemctl restart "$unit" >/dev/null 2>&1 || return 1
  hy2_wait_unit_and_udp_port "$unit" "$port" 3 20
}

install_hysteria() {
  install -d -m 755 /usr/local/src/hy2-upstream
  local installer="/usr/local/src/hy2-upstream/get_hy2.sh"
  local version local_binary installed_bin installed_owner installed_mode
  local existing_bin force_update update_policy core_tx old_present=0 old_bin_path="" old_mode=755 new_bin=""
  local -a active_units=() configs=()
  local unit cfg

  version="${HYSTERIA_VERSION:-latest}"
  local_binary="${HYSTERIA_LOCAL_BINARY:-}"
  force_update="${HYSTERIA_FORCE_UPDATE:-0}"
  update_policy="${HYSTERIA_UPDATE_POLICY:-install-only}"
  existing_bin="$(command -v hysteria || true)"

  if [[ -x "$existing_bin" && "$force_update" != "1" && "$update_policy" == "install-only" && -z "$local_binary" ]]; then
    validate_local_hysteria_binary "$existing_bin"
    echo "ℹ️  已安装 Hysteria，按 install-only 策略复用：$(timeout 10 "$existing_bin" version 2>/dev/null | head -n1)"
    echo "ℹ️  显式升级：HYSTERIA_FORCE_UPDATE=1 HYSTERIA_VERSION=vX.Y.Z bash /root/onekey_hy2_main_tls.sh"
    systemctl disable --now hysteria-server.service >/dev/null 2>&1 || true
    return 0
  fi

  [[ "$force_update" == "0" || "$force_update" == "1" ]] \
    || hy2_die "HYSTERIA_FORCE_UPDATE 只能是 0 或 1"
  secure_download_script \
    "${HYSTERIA_INSTALLER_URL:-https://get.hy2.sh/}" \
    "$installer" \
    "${HYSTERIA_INSTALLER_SHA256:-}" \
    "Hysteria 官方安装器"
  if [[ -n "$local_binary" ]]; then
    validate_local_hysteria_binary "$local_binary"
  fi

  mapfile -t active_units < <(hy2_collect_active_managed_units)
  core_tx="$(mktemp -d /var/tmp/hy2-core-update.XXXXXX)"
  if [[ -x "$existing_bin" ]]; then
    validate_local_hysteria_binary "$existing_bin"
    old_present=1
    old_bin_path="$existing_bin"
    old_mode="$(stat -c %a "$existing_bin" 2>/dev/null || echo 755)"
    cp -aL -- "$existing_bin" "${core_tx}/hysteria.old"
  fi

  restore_core_update() {
    local restore_unit
    set +e
    if (( old_present == 1 )); then
      if [[ -n "$new_bin" && "$new_bin" != "$old_bin_path" ]]; then
        rm -f -- "$new_bin"
      fi
      install -m "$old_mode" "${core_tx}/hysteria.old" "$old_bin_path"
      hash -r
    elif [[ -n "$new_bin" ]]; then
      rm -f -- "$new_bin"
      hash -r
    fi
    systemctl daemon-reload >/dev/null 2>&1 || true
    for restore_unit in "${active_units[@]}"; do
      timeout 60 systemctl restart "$restore_unit" >/dev/null 2>&1 || true
    done
    rm -rf -- "$core_tx"
  }

  if [[ -n "$local_binary" ]]; then
    echo "⚙ 使用本地二进制安装 Hysteria 2：${local_binary}"
    if ! HYSTERIA_USER=root bash "$installer" --local "$local_binary"; then
      restore_core_update
      hy2_die "使用本地 Hysteria 二进制安装失败"
    fi
  else
    echo "⚙ 安装/升级 Hysteria 2（${version}）..."
    if [[ "$version" == "latest" ]]; then
      if ! HYSTERIA_USER=root bash "$installer"; then
        restore_core_update
        hy2_die "Hysteria 官方安装器执行失败"
      fi
    else
      if ! HYSTERIA_USER=root bash "$installer" --version "$version"; then
        restore_core_update
        hy2_die "Hysteria ${version} 安装失败"
      fi
    fi
  fi

  new_bin="$(command -v hysteria || true)"
  HY2_CORE_NEW_BIN_PATH="$new_bin"
  installed_bin="$new_bin"
  if [[ ! -x "$installed_bin" ]]; then
    restore_core_update
    hy2_die "未找到 hysteria 可执行文件"
  fi
  if ! timeout 10 "$installed_bin" version >/dev/null 2>&1; then
    restore_core_update
    hy2_die "安装后的 Hysteria 无法执行"
  fi
  installed_owner="$(stat -c %u "$installed_bin" 2>/dev/null || echo -1)"
  installed_mode="$(stat -c %a "$installed_bin" 2>/dev/null || echo 777)"
  if [[ "$installed_owner" != "0" ]] \
    || [[ ! "$installed_mode" =~ ^[0-7]{3,4}$ ]] \
    || (( ((8#$installed_mode) & 8#022) != 0 )); then
    restore_core_update
    hy2_die "安装后的 Hysteria 二进制所有权或权限不安全"
  fi

  # Supply-chain check on the actual core binary (not just the installer
  # script).  Enforce an operator-pinned SHA-256 when provided; require one
  # under REMOTE_SCRIPT_POLICY=require; otherwise record and warn.
  local core_sha expected_core_sha remote_policy
  expected_core_sha="${HYSTERIA_BINARY_SHA256:-}"
  remote_policy="${REMOTE_SCRIPT_POLICY:-warn}"
  if [[ -n "$expected_core_sha" && ! "$expected_core_sha" =~ ^[0-9A-Fa-f]{64}$ ]]; then
    restore_core_update
    hy2_die "HYSTERIA_BINARY_SHA256 必须是 64 位十六进制"
  fi
  core_sha="$(sha256sum "$installed_bin" | awk '{print $1}')"
  if [[ -n "$expected_core_sha" ]]; then
    if [[ "${core_sha,,}" != "${expected_core_sha,,}" ]]; then
      restore_core_update
      hy2_die "Hysteria 核心二进制 SHA-256 不匹配：期望 ${expected_core_sha}，实际 ${core_sha}"
    fi
    echo "✅ Hysteria 核心二进制 SHA-256 校验通过：${core_sha}"
  elif [[ "$remote_policy" == "require" ]]; then
    restore_core_update
    hy2_die "REMOTE_SCRIPT_POLICY=require，但未设置 HYSTERIA_BINARY_SHA256 以校验核心二进制"
  else
    echo "⚠️  未固定 HYSTERIA_BINARY_SHA256；本次核心二进制实际哈希：${core_sha}" >&2
  fi

  [[ -s "$HY2_MAIN_CFG" ]] && configs+=("$HY2_MAIN_CFG")
  while IFS= read -r cfg; do
    [[ -n "$cfg" ]] && configs+=("$cfg")
  done < <(find "$HY2_TEMP_CFG_DIR" -maxdepth 1 -type f -name 'hy2-temp-*.yaml' 2>/dev/null | LC_ALL=C sort)

  for cfg in "${configs[@]}"; do
    if ! hy2_validate_config_with_binary "$installed_bin" "$cfg"; then
      restore_core_update
      hy2_die "新 Hysteria 与现有配置不兼容，已恢复旧核心"
    fi
  done

  systemctl disable --now hysteria-server.service >/dev/null 2>&1 || true
  systemctl daemon-reload >/dev/null 2>&1 || true
  for unit in "${active_units[@]}"; do
    if ! hy2_restart_verify_managed_unit "$unit"; then
      restore_core_update
      hy2_die "升级后服务验证失败：${unit}；已恢复旧核心"
    fi
  done

  rm -rf -- "$core_tx"
  echo "✅ Hysteria 核心安装/升级完成；验证配置 ${#configs[@]} 个，重启活跃服务 ${#active_units[@]} 个"
}

ensure_acmesh_zerossl() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -o Acquire::Retries=3 >/dev/null 2>&1 || true
  apt-get install -y --no-install-recommends socat cron >/dev/null 2>&1
  systemctl enable --now cron >/dev/null 2>&1 || true

  local acme_home="/root/.acme.sh"
  local acme_sh="${acme_home}/acme.sh"
  local acme_installer="/usr/local/src/hy2-upstream/get_acme.sh"
  if [[ ! -x "$acme_sh" ]]; then
    secure_download_script \
      "${ACME_INSTALLER_URL:-https://raw.githubusercontent.com/acmesh-official/acme.sh/${ACME_SH_VERSION:-3.1.2}/acme.sh}" \
      "$acme_installer" \
      "${ACME_INSTALLER_SHA256:-}" \
      "acme.sh 官方安装器"
    sh "$acme_installer" --install -m "$ACME_EMAIL"
  fi
  [[ -x "$acme_sh" ]] || hy2_die "acme.sh 安装失败"

  "$acme_sh" --set-default-ca --server zerossl >/dev/null 2>&1 || true
  "$acme_sh" --register-account -m "$ACME_EMAIL" --server zerossl >/dev/null 2>&1 || true
}

issue_or_renew_cert_zerossl() {
  local domain="$1"
  local acme_home="/root/.acme.sh"
  local acme_sh="${acme_home}/acme.sh"
  local cert_dir="/etc/hysteria/certs/${domain}"

  [[ -x "$acme_sh" ]] || hy2_die "acme.sh 不可用"
  install -d -m 700 "$cert_dir"
  write_renew_hook

  "$acme_sh" --set-default-ca --server zerossl >/dev/null 2>&1 || true
  if ! "$acme_sh" --issue --server zerossl --standalone -d "$domain"; then
    if [[ ! -s "${acme_home}/${domain}_ecc/fullchain.cer" && ! -s "${acme_home}/${domain}/fullchain.cer" ]]; then
      hy2_die "ZeroSSL 签发失败：$domain"
    fi
  fi
  "$acme_sh" --install-cert -d "$domain"     --key-file "${cert_dir}/privkey.pem"     --fullchain-file "${cert_dir}/fullchain.pem"     --reloadcmd "$HY2_RENEW_HOOK"

  chmod 600 "${cert_dir}/privkey.pem" "${cert_dir}/fullchain.pem"
}

enable_bbr() {
  echo "=== 1. 启用 BBR ==="
  cat >/etc/sysctl.d/99-hy2-bbr.conf <<'SYS'
net.core.default_qdisc=fq
net.ipv4.tcp_congestion_control=bbr
SYS
  modprobe tcp_bbr 2>/dev/null || true
  sysctl -p /etc/sysctl.d/99-hy2-bbr.conf >/dev/null 2>&1 || true
  echo "当前拥塞控制: $(sysctl -n net.ipv4.tcp_congestion_control 2>/dev/null || echo unknown)"
}

write_main_unit() {
  local hy_bin="$1"
  cat >/etc/systemd/system/hy2.service <<UNIT
[Unit]
Description=Managed Hysteria 2 Main Service
After=network-online.target nftables.service hy2-managed-restore.service
Wants=network-online.target
ConditionPathExists=${HY2_MAIN_CFG}
StartLimitIntervalSec=300
StartLimitBurst=10

[Service]
Type=simple
User=root
Group=root
ExecStart=${hy_bin} server -c ${HY2_MAIN_CFG}
Restart=on-failure
RestartSec=3
LimitNOFILE=1000000
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ProtectClock=true
ProtectHostname=true
ProtectProc=invisible
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictNamespaces=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_NETLINK AF_UNIX
LockPersonality=true
SystemCallArchitectures=native
UMask=0077

[Install]
WantedBy=multi-user.target
UNIT
  chmod 644 /etc/systemd/system/hy2.service
}

write_renew_hook() {
  install -d -m 700 "$(dirname "${HY2_RENEW_HOOK}")"
  cat >"${HY2_RENEW_HOOK}" <<'HOOK'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# shellcheck disable=SC1091
source /usr/local/lib/hy2/common.sh

hy2_ensure_runtime_dirs
if [[ "${HY2_TEMP_LOCK_HELD:-0}" != "1" ]]; then
  hy2_acquire_lock_fd 7 "${HY2_LOCK_DIR}/temp.lock" 60 "temp 锁繁忙，无法安全重载证书"
  export HY2_TEMP_LOCK_HELD=1
fi

declare -a RENEW_BYPASS_FILES=()
renew_cleanup_bypass_files() {
  local path
  for path in "${RENEW_BYPASS_FILES[@]:-}"; do
    [[ -n "$path" ]] && rm -f -- "$path" "${path}.tmp.$$" 2>/dev/null || true
  done
}
trap renew_cleanup_bypass_files EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

renew_fail() {
  hy2_log acme.log "[renew-hook] ERROR: $*"
  printf '❌ HY2 certificate reload: %s\n' "$*" >&2
  return 1
}

validate_cert_pair() {
  local cert="$1" key="$2" cert_pub="" key_pub=""
  [[ -s "$cert" ]] || { renew_fail "certificate missing or empty: ${cert}"; return 1; }
  [[ -s "$key" ]] || { renew_fail "private key missing or empty: ${key}"; return 1; }
  openssl x509 -in "$cert" -noout -checkend 0 >/dev/null 2>&1 \
    || { renew_fail "renewed certificate is expired or unreadable: ${cert}"; return 1; }
  if ! cert_pub="$(openssl x509 -in "$cert" -pubkey -noout 2>/dev/null \
      | openssl pkey -pubin -outform DER 2>/dev/null \
      | sha256sum | awk '{print $1}')"; then
    renew_fail "cannot derive public key from certificate: ${cert}"
    return 1
  fi
  if ! key_pub="$(openssl pkey -in "$key" -pubout -outform DER 2>/dev/null \
      | sha256sum | awk '{print $1}')"; then
    renew_fail "cannot derive public key from private key: ${key}"
    return 1
  fi
  [[ -n "$cert_pub" && "$cert_pub" == "$key_pub" ]] \
    || { renew_fail "certificate and private key do not match"; return 1; }
}

restart_temp_preserve_state() {
  local svc="$1" tag meta expire port bypass tmp now
  tag="${svc%.service}"
  hy2_is_valid_temp_tag "$tag" || {
    renew_fail "refusing unsafe temporary service name: ${svc}"
    return 1
  }
  meta="$(hy2_temp_meta_file "$tag")"
  [[ -s "$meta" ]] || {
    renew_fail "temporary node state is missing; leaving running service untouched: ${tag}"
    return 1
  }
  expire="$(hy2_meta_get "$meta" EXPIRE_EPOCH 2>/dev/null || true)"
  [[ "$expire" =~ ^[0-9]+$ ]] || {
    renew_fail "temporary node has invalid EXPIRE_EPOCH; leaving running service untouched: ${tag}"
    return 1
  }
  now="$(date +%s)"
  if (( expire <= now )); then
    hy2_log acme.log "[renew-hook] cleaning expired active temp before reload: ${tag}"
    if ! FORCE=1 HY2_TEMP_LOCK_HELD=1 timeout --foreground 90 \
        /usr/local/sbin/hy2_cleanup_one.sh "$tag" >/dev/null 2>&1; then
      renew_fail "failed to clean expired temporary node: ${tag}"
      return 1
    fi
    return 0
  fi

  port="$(hy2_temp_port_from_any "$tag" 2>/dev/null || true)"
  [[ "$port" =~ ^[0-9]+$ ]] && (( port >= 1 && port <= 65535 )) || {
    renew_fail "temporary node has no valid UDP port; leaving it untouched: ${tag}"
    return 1
  }

  # The parent holds temp.lock while systemctl waits for ExecStopPost.  The
  # bypass lets the child skip inherited lock acquisition if expiry changes in
  # the tiny interval between our state check and the stop operation.
  bypass="${HY2_LOCK_DIR}/stoppost-bypass.${tag}"
  tmp="${bypass}.tmp.$$"
  printf '%s\n' "$$" >"$tmp"
  chmod 600 "$tmp"
  mv -f -- "$tmp" "$bypass"
  RENEW_BYPASS_FILES+=("$bypass")

  if ! timeout --foreground 90 systemctl restart "$svc" >/dev/null 2>&1; then
    rm -f -- "$bypass" "$tmp"
    renew_fail "restart failed for temporary node: ${tag}"
    # A failed restart can leave the unit inactive.  Try one recovery start,
    # but still return failure so acme.sh records the reload problem.
    timeout --foreground 60 systemctl start "$svc" >/dev/null 2>&1 || true
    return 1
  fi
  rm -f -- "$bypass" "$tmp"

  if ! hy2_wait_unit_and_udp_port "$svc" "$port" 2 12; then
    renew_fail "temporary node did not become stable after certificate reload: ${tag} UDP/${port}"
    return 1
  fi
  hy2_log acme.log "[renew-hook] temp reload OK: ${tag} UDP/${port}"
}

cert="$(hy2_meta_get "$HY2_MAIN_STATE_FILE" TLS_CERT 2>/dev/null || true)"
key="$(hy2_meta_get "$HY2_MAIN_STATE_FILE" TLS_KEY 2>/dev/null || true)"
validate_cert_pair "$cert" "$key" || exit 1

mapfile -t active_temp_services < <(
  systemctl list-units --type=service --state=active --no-legend 'hy2-temp-*.service' 2>/dev/null \
    | awk '$1 ~ /^hy2-temp-[A-Za-z0-9._-]+[.]service$/ {print $1}' \
    | LC_ALL=C sort -u
)

systemctl daemon-reload >/dev/null 2>&1 \
  || { renew_fail "systemctl daemon-reload failed"; exit 1; }

main_port="$(hy2_main_port 2>/dev/null || true)"
if ! timeout --foreground 90 systemctl restart hy2.service >/dev/null 2>&1; then
  renew_fail "hy2.service restart failed"
  exit 1
fi
if [[ "$main_port" =~ ^[0-9]+$ ]]; then
  hy2_wait_unit_and_udp_port hy2.service "$main_port" 2 12 \
    || { renew_fail "hy2.service did not become stable on UDP/${main_port}"; exit 1; }
elif ! systemctl is-active --quiet hy2.service 2>/dev/null; then
  renew_fail "hy2.service is not active after restart"
  exit 1
fi
hy2_log acme.log "[renew-hook] main reload OK: UDP/${main_port:-unknown}"

rc=0
for svc in "${active_temp_services[@]}"; do
  restart_temp_preserve_state "$svc" || rc=1
done

if (( rc != 0 )); then
  renew_fail "one or more temporary nodes failed certificate reload"
  exit 1
fi
hy2_log acme.log "[renew-hook] certificate reload completed successfully"
HOOK
  chmod 755 "${HY2_RENEW_HOOK}"
}

main() {
  local hysteria_version_explicit="${HYSTERIA_VERSION+x}"
  local hysteria_version_override="${HYSTERIA_VERSION:-}"
  local hysteria_policy_explicit="${HYSTERIA_UPDATE_POLICY+x}"
  local hysteria_policy_override="${HYSTERIA_UPDATE_POLICY:-}"
  local hysteria_force_explicit="${HYSTERIA_FORCE_UPDATE+x}"
  local hysteria_force_override="${HYSTERIA_FORCE_UPDATE:-}"
  local hysteria_local_explicit="${HYSTERIA_LOCAL_BINARY+x}"
  local hysteria_local_override="${HYSTERIA_LOCAL_BINARY:-}"
  hy2_require_root_supported_os
  hy2_ensure_runtime_dirs
  hy2_acquire_lock_fd 6 "${HY2_LOCK_DIR}/main-install.lock" 120 "另一个主节点安装任务正在运行"
  hy2_acquire_lock_fd 7 "${HY2_LOCK_DIR}/temp.lock" 120 "临时节点创建/清理任务仍在运行"
  export HY2_TEMP_LOCK_HELD=1
  hy2_load_defaults
  if [[ -n "$hysteria_version_explicit" ]]; then
    HYSTERIA_VERSION="$hysteria_version_override"
    HYSTERIA_FORCE_UPDATE=1
  fi
  if [[ -n "$hysteria_policy_explicit" ]]; then
    HYSTERIA_UPDATE_POLICY="$hysteria_policy_override"
  fi
  if [[ -n "$hysteria_force_explicit" ]]; then
    HYSTERIA_FORCE_UPDATE="$hysteria_force_override"
  fi
  if [[ -n "$hysteria_local_explicit" ]]; then
    HYSTERIA_LOCAL_BINARY="$hysteria_local_override"
    [[ -z "$HYSTERIA_LOCAL_BINARY" ]] || HYSTERIA_FORCE_UPDATE=1
  fi
  begin_transaction

  : "${ACME_EMAIL:?缺少 ACME_EMAIL}"
  : "${MASQ_URL:?缺少 MASQ_URL}"
  : "${HY_DOMAIN:?缺少 HY_DOMAIN}"
  : "${HY_LISTEN:=0.0.0.0:443}"
  : "${ENABLE_SALAMANDER:=0}"
  : "${SALAMANDER_PASSWORD:=}"
  : "${NODE_NAME:=HY2-MAIN}"
  : "${HYSTERIA_VERSION:=latest}"
  : "${HYSTERIA_UPDATE_POLICY:=install-only}"
  : "${HYSTERIA_FORCE_UPDATE:=0}"
  : "${HYSTERIA_INSTALLER_URL:=https://get.hy2.sh/}"
  : "${ACME_SH_VERSION:=3.1.2}"
  : "${ACME_INSTALLER_URL:=https://raw.githubusercontent.com/acmesh-official/acme.sh/${ACME_SH_VERSION}/acme.sh}"
  : "${HYSTERIA_INSTALLER_SHA256:=}"
  : "${ACME_INSTALLER_SHA256:=}"
  : "${REMOTE_SCRIPT_POLICY:=warn}"
  : "${HYSTERIA_LOCAL_BINARY:=}"
  : "${ROTATE_CREDENTIALS:=0}"

  if [[ "$ENABLE_SALAMANDER" == "1" && -z "$SALAMANDER_PASSWORD" ]]; then
    hy2_die "ENABLE_SALAMANDER=1 时必须设置 SALAMANDER_PASSWORD"
  fi
  [[ "$HYSTERIA_VERSION" == "latest" || "$HYSTERIA_VERSION" =~ ^v[0-9][A-Za-z0-9._-]*$ ]] \
    || hy2_die "HYSTERIA_VERSION 必须是 latest 或 vX.Y.Z"
  [[ "$HYSTERIA_UPDATE_POLICY" == "install-only" || "$HYSTERIA_UPDATE_POLICY" == "always" ]] \
    || hy2_die "HYSTERIA_UPDATE_POLICY 只能是 install-only 或 always"
  [[ "$HYSTERIA_FORCE_UPDATE" == "0" || "$HYSTERIA_FORCE_UPDATE" == "1" ]] \
    || hy2_die "HYSTERIA_FORCE_UPDATE 只能是 0 或 1"
  [[ "$ROTATE_CREDENTIALS" == "0" || "$ROTATE_CREDENTIALS" == "1" ]] \
    || hy2_die "ROTATE_CREDENTIALS 只能是 0 或 1"
  [[ "$REMOTE_SCRIPT_POLICY" == "warn" || "$REMOTE_SCRIPT_POLICY" == "require" ]] \
    || hy2_die "REMOTE_SCRIPT_POLICY 只能是 warn 或 require"

  mapfile -t public_ipv4s < <(hy2_get_public_ipv4_candidates)
  hy2_require_domain_points_here "$HY_DOMAIN" 4 "${public_ipv4s[@]}"

  enable_bbr

  echo
  echo "=== 2. 安装 Hysteria 2 ==="
  install_hysteria
  local hy_bin runtime_version
  hy_bin="$(command -v hysteria)"
  runtime_version="$(timeout 10 "$hy_bin" version 2>/dev/null | head -n1 | tr -d '\r' || true)"

  echo
  echo "=== 3. 申请 / 续期正式证书（ZeroSSL + acme.sh standalone） ==="
  ensure_acmesh_zerossl
  issue_or_renew_cert_zerossl "$HY_DOMAIN"

  local crt key main_port password_tmp
  mapfile -t certs < <(hy2_main_cert_paths "$HY_DOMAIN")
  crt="${certs[0]}"
  key="${certs[1]}"
  [[ -s "$crt" && -s "$key" ]] || hy2_die "证书文件不存在：$crt / $key"

  if [[ ! -s "$HY2_MAIN_PASSWORD_FILE" || "$ROTATE_CREDENTIALS" == "1" ]]; then
    password_tmp="$(mktemp "${HY2_MAIN_PASSWORD_FILE}.tmp.XXXXXX")"
    openssl rand -hex 16 >"$password_tmp"
    chmod 600 "$password_tmp"
    mv -f "$password_tmp" "$HY2_MAIN_PASSWORD_FILE"
  fi
  local main_password
  main_password="$(tr -d '\r\n' < "$HY2_MAIN_PASSWORD_FILE")"
  [[ -n "$main_password" ]] || hy2_die "主节点密码生成失败"

  main_port="$(hy2_parse_port_from_listen "$HY_LISTEN" || true)"
  [[ "$main_port" =~ ^[0-9]+$ ]] || hy2_die "HY_LISTEN 非法：$HY_LISTEN"
  (( main_port == 443 )) || hy2_die "主节点必须监听 443"
  [[ "$HY_LISTEN" == "0.0.0.0:443" ]] \
    || hy2_die "主节点必须固定为 IPv4 监听：HY_LISTEN=0.0.0.0:443"

  echo
  echo "=== 4. 写入主节点配置与 systemd ==="
  hy2_write_server_cfg "$HY2_MAIN_CFG" "$HY_LISTEN" "$main_password" "$crt" "$key" "$MASQ_URL" "$ENABLE_SALAMANDER" "$SALAMANDER_PASSWORD"
  write_main_unit "$hy_bin"

  hy2_write_meta "$HY2_MAIN_STATE_FILE" \
    "HY_DOMAIN=${HY_DOMAIN}" \
    "HY_IPV6_DOMAIN=${HY_IPV6_DOMAIN:-}" \
    "HY_LISTEN=${HY_LISTEN}" \
    "MAIN_PORT=${main_port}" \
    "ACME_EMAIL=${ACME_EMAIL}" \
    "ACME_SH_VERSION=${ACME_SH_VERSION}" \
    "MASQ_URL=${MASQ_URL}" \
    "ENABLE_SALAMANDER=${ENABLE_SALAMANDER}" \
    "SALAMANDER_PASSWORD=${SALAMANDER_PASSWORD}" \
    "NODE_NAME=${NODE_NAME}" \
    "HYSTERIA_VERSION=${HYSTERIA_VERSION}" \
    "HYSTERIA_UPDATE_POLICY=${HYSTERIA_UPDATE_POLICY}" \
    "HYSTERIA_RUNTIME_VERSION=${runtime_version}" \
    "TLS_CERT=${crt}" \
    "TLS_KEY=${key}" \
    "MAIN_PASSWORD_FILE=${HY2_MAIN_PASSWORD_FILE}" \
    "MAIN_PASSWORD=${main_password}" \
    "INSTALL_EPOCH=$(date +%s)"

  systemctl daemon-reload
  systemctl enable hy2.service >/dev/null 2>&1 || true
  systemctl restart hy2.service

  echo
  echo "=== 5. 启动并稳定性校验 ==="
  if ! hy2_wait_unit_and_udp_port hy2.service "$main_port" 3 12; then
    systemctl --no-pager --full status hy2.service >&2 || true
    journalctl -u hy2.service --no-pager -n 120 >&2 || true
    hy2_die "主节点启动失败或未通过稳定性校验"
  fi

  local url
  url="$(hy2_build_url "$main_password" "$HY_DOMAIN" "$main_port" "$NODE_NAME" "$ENABLE_SALAMANDER" "$SALAMANDER_PASSWORD" "$HY_DOMAIN")"
  printf '%s\n' "$url" >/root/hy2_main_url.txt
  printf '%s' "$url" | hy2_base64_one_line >/root/hy2_main_subscription_base64.txt
  chmod 600 /root/hy2_main_url.txt /root/hy2_main_subscription_base64.txt 2>/dev/null || true

  echo
  echo "================== 主节点信息 =================="
  cat /root/hy2_main_url.txt
  echo
  echo "Base64 订阅："
  cat /root/hy2_main_subscription_base64.txt
  echo
  echo "保存位置："
  echo "  /root/hy2_main_url.txt"
  echo "  /root/hy2_main_subscription_base64.txt"
  commit_transaction
  echo "✅ HY2 主节点安装完成"
}

main "$@"
EOF
  chmod 755 /root/onekey_hy2_main_tls.sh
}

install_quota_scripts() {
  cat >"${HY2_SBIN_DIR}/pq_add.sh" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
trap 'echo "❌ ${BASH_SOURCE[0]}:${LINENO}: ${BASH_COMMAND}" >&2' ERR

# shellcheck disable=SC1091
source /usr/local/lib/hy2/common.sh
# shellcheck disable=SC1091
source /usr/local/lib/hy2/quota-lib.sh

PORT="${1:-}"
GIB="${2:-}"
[[ "$PORT" =~ ^[0-9]+$ ]] && (( PORT >= 1 && PORT <= 65535 )) || hy2_die "用法: pq_add.sh <端口> <GiB>"
[[ -n "$GIB" ]] || hy2_die "用法: pq_add.sh <端口> <GiB>"
BYTES="$(hy2_parse_gib_to_bytes "$GIB")" || hy2_die "GiB 必须为正数"
hy2_ensure_runtime_dirs
hy2_acquire_lock_fd 7 "${HY2_LOCK_DIR}/temp.lock" 120 "temp 锁繁忙"
export HY2_TEMP_LOCK_HELD=1
TEMP_META="$(hy2_temp_meta_by_port "$PORT")"
TEMP_TAG=""
if [[ -n "$TEMP_META" ]]; then
  TEMP_TAG="$(hy2_meta_get "$TEMP_META" TAG)"
fi

(
  trap '' INT TERM HUP
  if [[ -n "$TEMP_META" ]]; then
    # Manual changes stay bound to the temp node for cleanup, but do not
    # automatically enable the 30-day reset.
    hy2_pq_add_managed_port "$PORT" "$BYTES" temp "$TEMP_TAG" 0 0
    hy2_meta_upsert "$TEMP_META" PQ_LIMIT_BYTES "$BYTES"
    hy2_meta_upsert "$TEMP_META" PQ_GIB "$GIB"
  else
    hy2_pq_add_managed_port "$PORT" "$BYTES" manual ""
  fi
)
if [[ -n "$TEMP_TAG" ]]; then
  echo "✅ 已为临时节点 ${TEMP_TAG}（端口 ${PORT}）手工设置总配额 $(hy2_human_bytes "$BYTES")；不启用 30 天自动重置"
else
  echo "✅ 已为端口 ${PORT} 设置 HY2 UDP 总配额 $(hy2_human_bytes "$BYTES")"
fi
EOF
  chmod 755 "${HY2_SBIN_DIR}/pq_add.sh"

  cat >"${HY2_SBIN_DIR}/pq_del.sh" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
trap 'echo "❌ ${BASH_SOURCE[0]}:${LINENO}: ${BASH_COMMAND}" >&2' ERR

# shellcheck disable=SC1091
source /usr/local/lib/hy2/common.sh
# shellcheck disable=SC1091
source /usr/local/lib/hy2/quota-lib.sh

PORT="${1:-}"
[[ "$PORT" =~ ^[0-9]+$ ]] && (( PORT >= 1 && PORT <= 65535 )) || hy2_die "用法: pq_del.sh <端口>"
hy2_ensure_runtime_dirs
hy2_acquire_lock_fd 7 "${HY2_LOCK_DIR}/temp.lock" 120 "temp 锁繁忙"
export HY2_TEMP_LOCK_HELD=1
TEMP_META="$(hy2_temp_meta_by_port "$PORT")"
TEMP_TAG=""
OLD_PQ_GIB=""
OLD_PQ_LIMIT_BYTES=""
if [[ -n "$TEMP_META" ]]; then
  TEMP_TAG="$(hy2_meta_get "$TEMP_META" TAG)"
  OLD_PQ_GIB="$(hy2_meta_get "$TEMP_META" PQ_GIB 2>/dev/null || true)"
  OLD_PQ_LIMIT_BYTES="$(hy2_meta_get "$TEMP_META" PQ_LIMIT_BYTES 2>/dev/null || true)"
fi

(
  trap '' INT TERM HUP
  if [[ -n "$TEMP_META" ]]; then
    hy2_meta_upsert "$TEMP_META" PQ_LIMIT_BYTES ""
    hy2_meta_upsert "$TEMP_META" PQ_GIB ""
    if ! hy2_pq_delete_managed_port "$PORT"; then
      hy2_meta_upsert "$TEMP_META" PQ_LIMIT_BYTES "$OLD_PQ_LIMIT_BYTES" || true
      hy2_meta_upsert "$TEMP_META" PQ_GIB "$OLD_PQ_GIB" || true
      exit 1
    fi
  else
    hy2_pq_delete_managed_port "$PORT"
  fi
)
if [[ -n "$TEMP_TAG" ]]; then
  echo "✅ 已删除临时节点 ${TEMP_TAG}（端口 ${PORT}）的配额管理"
else
  echo "✅ 已删除端口 ${PORT} 的配额管理"
fi
EOF
  chmod 755 "${HY2_SBIN_DIR}/pq_del.sh"

  cat >"${HY2_SBIN_DIR}/pq_save_state.sh" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
trap 'echo "❌ ${BASH_SOURCE[0]}:${LINENO}: ${BASH_COMMAND}" >&2' ERR

# shellcheck disable=SC1091
source /usr/local/lib/hy2/common.sh
# shellcheck disable=SC1091
source /usr/local/lib/hy2/quota-lib.sh

hy2_ensure_runtime_dirs
hy2_pq_lock
rc=0
for meta in "$HY2_QUOTA_STATE_DIR"/*.env; do
  [[ -f "$meta" ]] || continue
  hy2_pq_save_one "$meta" || rc=1
done
exit "$rc"
EOF
  chmod 755 "${HY2_SBIN_DIR}/pq_save_state.sh"

  cat >"${HY2_SBIN_DIR}/pq_restore_all.sh" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
trap 'echo "❌ ${BASH_SOURCE[0]}:${LINENO}: ${BASH_COMMAND}" >&2' ERR

# shellcheck disable=SC1091
source /usr/local/lib/hy2/common.sh
# shellcheck disable=SC1091
source /usr/local/lib/hy2/quota-lib.sh

hy2_ensure_runtime_dirs
hy2_pq_lock
rc=0
for meta in "$HY2_QUOTA_STATE_DIR"/*.env; do
  [[ -f "$meta" ]] || continue
  hy2_pq_restore_one "$meta" || rc=1
done
exit "$rc"
EOF
  chmod 755 "${HY2_SBIN_DIR}/pq_restore_all.sh"

  cat >"${HY2_SBIN_DIR}/pq_reset_due.sh" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
trap 'echo "❌ ${BASH_SOURCE[0]}:${LINENO}: ${BASH_COMMAND}" >&2' ERR

# shellcheck disable=SC1091
source /usr/local/lib/hy2/common.sh
# shellcheck disable=SC1091
source /usr/local/lib/hy2/quota-lib.sh

hy2_ensure_runtime_dirs
hy2_pq_lock
rc=0
for meta in "$HY2_QUOTA_STATE_DIR"/*.env; do
  [[ -f "$meta" ]] || continue
  hy2_pq_reset_due_one "$meta" || rc=1
done
exit "$rc"
EOF
  chmod 755 "${HY2_SBIN_DIR}/pq_reset_due.sh"

  cat >"${HY2_SBIN_DIR}/pq_audit.sh" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
trap 'echo "❌ ${BASH_SOURCE[0]}:${LINENO}: ${BASH_COMMAND}" >&2' ERR

# shellcheck disable=SC1091
source /usr/local/lib/hy2/common.sh
# shellcheck disable=SC1091
source /usr/local/lib/hy2/quota-lib.sh

FILTER_PORT="${1:-}"
if [[ -n "$FILTER_PORT" && ! "$FILTER_PORT" =~ ^[0-9]+$ ]]; then
  hy2_die "用法: pq_audit.sh [port]"
fi

TMP_ROWS="$(mktemp)"
trap 'rm -f "$TMP_ROWS"' EXIT

hy2_ensure_runtime_dirs
hy2_pq_lock

for meta in "$HY2_QUOTA_STATE_DIR"/*.env; do
  [[ -f "$meta" ]] || continue
  PORT="$(hy2_meta_get "$meta" PORT || true)"
  [[ "$PORT" =~ ^[0-9]+$ ]] || continue
  if [[ -n "$FILTER_PORT" && "$PORT" != "$FILTER_PORT" ]]; then
    continue
  fi
  OWNER_KIND="$(hy2_meta_get "$meta" OWNER_KIND || true)"
  OWNER_TAG="$(hy2_meta_get "$meta" OWNER_TAG || true)"
  NEXT_RESET_EPOCH="$(hy2_meta_get "$meta" NEXT_RESET_EPOCH || true)"
  RESET_INTERVAL_SECONDS="$(hy2_meta_get "$meta" RESET_INTERVAL_SECONDS || true)"
  SNAPSHOT="$(hy2_pq_snapshot "$PORT")"
  IFS='|' read -r STATE ORIGINAL USED LEFT <<<"$SNAPSHOT"
  OWNER="${OWNER_KIND:-manual}"
  if [[ -n "$OWNER_TAG" ]]; then
    OWNER="${OWNER_KIND:-manual}:${OWNER_TAG}"
  fi
  if [[ "$RESET_INTERVAL_SECONDS" =~ ^[0-9]+$ ]] && (( RESET_INTERVAL_SECONDS > 0 )); then
    RESET="30d"
    NEXT_RESET_BJ="$(hy2_beijing_time "$NEXT_RESET_EPOCH")"
  else
    RESET="-"
    NEXT_RESET_BJ="-"
  fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$PORT" \
    "$OWNER" \
    "$STATE" \
    "$(hy2_human_bytes "$ORIGINAL")" \
    "$(hy2_human_bytes "$USED")" \
    "$(hy2_human_bytes "$LEFT")" \
    "$(hy2_pct_text "$USED" "$ORIGINAL")" \
    "$RESET" \
    "$NEXT_RESET_BJ" >>"$TMP_ROWS"
done

sort -t $'\t' -k1,1n "$TMP_ROWS" | /usr/local/lib/hy2/render_table.py pq
EOF
  chmod 755 "${HY2_SBIN_DIR}/pq_audit.sh"
}

install_iplimit_scripts() {
  cat >"${HY2_SBIN_DIR}/ip_set.sh" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
trap 'echo "❌ ${BASH_SOURCE[0]}:${LINENO}: ${BASH_COMMAND}" >&2' ERR

# shellcheck disable=SC1091
source /usr/local/lib/hy2/common.sh
# shellcheck disable=SC1091
source /usr/local/lib/hy2/iplimit-lib.sh

PORT="${1:-}"
IP_LIMIT="${2:-}"
STICKY_SECONDS="${3:-}"

[[ "$PORT" =~ ^[0-9]+$ ]] && (( PORT >= 1 && PORT <= 65535 )) || hy2_die "用法: ip_set.sh <port> <limit> [sticky_seconds]"
[[ "$IP_LIMIT" =~ ^[0-9]+$ ]] && (( IP_LIMIT > 0 )) || hy2_die "limit 必须是正整数"

hy2_ensure_runtime_dirs
hy2_acquire_lock_fd 7 "${HY2_LOCK_DIR}/temp.lock" 120 "temp 锁繁忙"
export HY2_TEMP_LOCK_HELD=1
META="$(hy2_iplimit_meta_file "$PORT")"
OWNER_KIND="manual"
OWNER_TAG=""
IP_VERSION=""

if [[ -f "$META" ]]; then
  IP_VERSION="$(hy2_meta_get "$META" IP_VERSION 2>/dev/null || true)"
  if [[ -z "$STICKY_SECONDS" ]]; then
    STICKY_SECONDS="$(hy2_meta_get "$META" IP_STICKY_SECONDS || true)"
  fi
fi

TEMP_META="$(hy2_temp_meta_by_port "$PORT" 2>/dev/null || true)"
if [[ -n "$TEMP_META" ]]; then
  OWNER_KIND="temp"
  OWNER_TAG="$(hy2_meta_get "$TEMP_META" TAG)"
  IP_VERSION="$(hy2_meta_get "$TEMP_META" IP_VERSION 2>/dev/null || true)"
fi

STICKY_SECONDS="${STICKY_SECONDS:-120}"
IP_VERSION="${IP_VERSION:-4}"
[[ "$STICKY_SECONDS" =~ ^[0-9]+$ ]] && (( STICKY_SECONDS > 0 )) || hy2_die "sticky_seconds 必须是正整数"
[[ "$IP_VERSION" == "4" || "$IP_VERSION" == "6" ]] || hy2_die "IP_VERSION 只能是 4 或 6"
[[ -n "$OWNER_KIND" ]] || OWNER_KIND="manual"

(
  trap '' INT TERM HUP
  hy2_il_add_managed_port "$PORT" "$IP_LIMIT" "$STICKY_SECONDS" "$OWNER_KIND" "$OWNER_TAG" "$IP_VERSION"
  if [[ -n "$TEMP_META" ]]; then
    hy2_meta_upsert "$TEMP_META" IP_LIMIT "$IP_LIMIT"
    hy2_meta_upsert "$TEMP_META" IP_STICKY_SECONDS "$STICKY_SECONDS"
  fi
)
echo "✅ 已将端口 ${PORT} 的 source-IP 限制设为 ${IP_LIMIT}（STICKY=${STICKY_SECONDS}s）"
EOF
  chmod 755 "${HY2_SBIN_DIR}/ip_set.sh"

  cat >"${HY2_SBIN_DIR}/ip_del.sh" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
trap 'echo "❌ ${BASH_SOURCE[0]}:${LINENO}: ${BASH_COMMAND}" >&2' ERR

# shellcheck disable=SC1091
source /usr/local/lib/hy2/common.sh
# shellcheck disable=SC1091
source /usr/local/lib/hy2/iplimit-lib.sh

PORT="${1:-}"
[[ "$PORT" =~ ^[0-9]+$ ]] && (( PORT >= 1 && PORT <= 65535 )) || hy2_die "用法: ip_del.sh <port>"

hy2_ensure_runtime_dirs
hy2_acquire_lock_fd 7 "${HY2_LOCK_DIR}/temp.lock" 120 "temp 锁繁忙"
export HY2_TEMP_LOCK_HELD=1
TEMP_META="$(hy2_temp_meta_by_port "$PORT" 2>/dev/null || true)"
IP_VERSION=""
OLD_IP_LIMIT=""
if [[ -n "$TEMP_META" ]]; then
  IP_VERSION="$(hy2_meta_get "$TEMP_META" IP_VERSION 2>/dev/null || true)"
  IP_VERSION="${IP_VERSION:-4}"
  [[ "$IP_VERSION" == "4" || "$IP_VERSION" == "6" ]] || hy2_die "临时节点 IP_VERSION 非法：${TEMP_META}"
  OLD_IP_LIMIT="$(hy2_meta_get "$TEMP_META" IP_LIMIT 2>/dev/null || true)"
  OLD_IP_LIMIT="${OLD_IP_LIMIT:-0}"
  [[ "$OLD_IP_LIMIT" =~ ^[0-9]+$ ]] || hy2_die "临时节点 IP_LIMIT 非法：${TEMP_META}"
fi

(
  trap '' INT TERM HUP
  if [[ -n "$TEMP_META" ]]; then
    hy2_meta_upsert "$TEMP_META" IP_LIMIT 0
    # Single atomic nft batch: drop the per-IP limit but keep protocol-family
    # isolation, with no fail-open gap between the two.  On failure the iplimit
    # meta file is left in place so the watchdog can rebuild it.
    if ! hy2_il_delete_and_apply_family_guard "$PORT" "$IP_VERSION"; then
      hy2_meta_upsert "$TEMP_META" IP_LIMIT "$OLD_IP_LIMIT" || true
      exit 1
    fi
  else
    hy2_il_delete_managed_port "$PORT"
  fi
)
echo "✅ 已删除端口 ${PORT} 的 source-IP 数量限制；临时节点的协议族隔离仍保留"
EOF
  chmod 755 "${HY2_SBIN_DIR}/ip_del.sh"

  cat >"${HY2_SBIN_DIR}/iplimit_restore_all.sh" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
trap 'echo "❌ ${BASH_SOURCE[0]}:${LINENO}: ${BASH_COMMAND}" >&2' ERR

# shellcheck disable=SC1091
source /usr/local/lib/hy2/common.sh
# shellcheck disable=SC1091
source /usr/local/lib/hy2/iplimit-lib.sh

hy2_ensure_runtime_dirs
hy2_il_lock
rc=0
now="$(date +%s)"

for meta in "$HY2_TEMP_STATE_DIR"/*.env; do
  [[ -f "$meta" ]] || continue
  port="$(hy2_meta_get "$meta" PORT 2>/dev/null || true)"
  ip_version="$(hy2_meta_get "$meta" IP_VERSION 2>/dev/null || true)"
  expire_epoch="$(hy2_meta_get "$meta" EXPIRE_EPOCH 2>/dev/null || true)"
  ip_version="${ip_version:-4}"
  [[ "$port" =~ ^[0-9]+$ ]] || continue
  [[ "$ip_version" == "4" || "$ip_version" == "6" ]] || ip_version=4
  if [[ "$expire_epoch" =~ ^[0-9]+$ ]] && (( expire_epoch <= now )); then
    continue
  fi
  hy2_il_apply_family_guard "$port" "$ip_version" || rc=1
done

for meta in "$HY2_IPLIMIT_STATE_DIR"/*.env; do
  [[ -f "$meta" ]] || continue
  hy2_il_restore_one "$meta" || rc=1
done
exit "$rc"
EOF
  chmod 755 "${HY2_SBIN_DIR}/iplimit_restore_all.sh"
}

install_hy2_management_scripts() {
  cat >"${HY2_SBIN_DIR}/hy2_run_temp.sh" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
trap 'echo "❌ ${BASH_SOURCE[0]}:${LINENO}: ${BASH_COMMAND}" >&2' ERR

# shellcheck disable=SC1091
source /usr/local/lib/hy2/common.sh
# shellcheck disable=SC1091
source /usr/local/lib/hy2/quota-lib.sh
# shellcheck disable=SC1091
source /usr/local/lib/hy2/iplimit-lib.sh

TAG="${1:?need TAG}"
CFG="${2:?need CONFIG}"
hy2_is_valid_temp_tag "$TAG" || hy2_die "非法临时节点 TAG：${TAG}"
META="$(hy2_temp_meta_file "$TAG")"
HY_BIN="$(command -v hysteria || true)"

[[ -x "$HY_BIN" ]] || hy2_die "未找到 hysteria 可执行文件"
[[ -f "$CFG" ]] || hy2_die "配置不存在：${CFG}"
[[ -f "$META" ]] || hy2_die "meta 不存在：${META}"

# 临时监听只有在配额、IP_LIMIT 或协议族隔离完整时才允许启动。
PORT="$(hy2_meta_get "$META" PORT 2>/dev/null || true)"
PQ_LIMIT_BYTES="$(hy2_meta_get "$META" PQ_LIMIT_BYTES 2>/dev/null || true)"
IP_LIMIT="$(hy2_meta_get "$META" IP_LIMIT 2>/dev/null || true)"
IP_VERSION="$(hy2_meta_get "$META" IP_VERSION 2>/dev/null || true)"
IP_LIMIT="${IP_LIMIT:-0}"
IP_VERSION="${IP_VERSION:-4}"
[[ "$PORT" =~ ^[0-9]+$ && "$IP_LIMIT" =~ ^[0-9]+$ ]] || hy2_die "临时节点防护元数据非法：${META}"
[[ "$IP_VERSION" == "4" || "$IP_VERSION" == "6" ]] || hy2_die "临时节点 IP_VERSION 非法：${META}"
if [[ -n "$PQ_LIMIT_BYTES" ]]; then
  [[ "$PQ_LIMIT_BYTES" =~ ^[0-9]+$ ]] || hy2_die "临时节点配额元数据非法：${META}"
  case "$(hy2_pq_state "$PORT")" in
    active|exhausted) ;;
    *) hy2_die "端口 ${PORT} 的配额防护未就绪，等待 watchdog 修复" ;;
  esac
fi
if (( IP_LIMIT > 0 )); then
  [[ "$(hy2_il_state "$PORT")" == "active" ]] \
    || hy2_die "端口 ${PORT} 的 IP_LIMIT 防护未就绪，等待 watchdog 修复"
else
  [[ "$(hy2_il_family_guard_state "$PORT")" == "active" ]] \
    || hy2_die "端口 ${PORT} 的协议族隔离未就绪，等待 watchdog 修复"
fi

EXPIRE_EPOCH="$(hy2_meta_get "$META" EXPIRE_EPOCH || true)"
[[ "$EXPIRE_EPOCH" =~ ^[0-9]+$ ]] || hy2_die "meta 中 EXPIRE_EPOCH 非法"

NOW="$(date +%s)"
REMAIN=$((EXPIRE_EPOCH - NOW))
if (( REMAIN <= 0 )); then
  # 交给本 unit 的 ExecStopPost 清理，避免 ExecStart 内停止自身 unit。
  exit 0
fi

exec timeout --foreground "$REMAIN" "$HY_BIN" server -c "$CFG"
EOF
  chmod 755 "${HY2_SBIN_DIR}/hy2_run_temp.sh"

  cat >"${HY2_SBIN_DIR}/hy2_cleanup_one.sh" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
trap 'echo "❌ ${BASH_SOURCE[0]}:${LINENO}: ${BASH_COMMAND}" >&2' ERR

# shellcheck disable=SC1091
source /usr/local/lib/hy2/common.sh
# shellcheck disable=SC1091
source /usr/local/lib/hy2/quota-lib.sh
# shellcheck disable=SC1091
source /usr/local/lib/hy2/iplimit-lib.sh

TAG="${1:?need TAG}"
MODE="${2:-}"
FORCE="${FORCE:-0}"
hy2_is_valid_temp_tag "$TAG" || hy2_die "非法临时节点 TAG：${TAG}"
META="$(hy2_temp_meta_file "$TAG")"
CFG="$(hy2_temp_cfg_file "$TAG")"
UNIT_FILE="$(hy2_temp_unit_file "$TAG")"
URL_FILE="$(hy2_temp_url_file "$TAG")"
UNIT_NAME="${TAG}.service"
FROM_STOP_POST=0
[[ "$MODE" == "--from-stop-post" ]] && FROM_STOP_POST=1
STOPPOST_BYPASS_FILE="${HY2_LOCK_DIR}/stoppost-bypass.${TAG}"
SKIP_LOCK=0

hy2_ensure_runtime_dirs

# Read metadata first (plain file reads need no lock) so the common
# "not expired -> preserve" decision is made BEFORE any lock is taken.  This
# is essential for the ExecStopPost path: a parent (watchdog / cert-renew hook
# / set_peer) may hold temp.lock (fd7) or the WG state lock (fd5) while it
# `systemctl restart`s the unit; blocking on those locks here would stall the
# stop until the flock timeout and get this ExecStopPost killed mid-run.
PORT="$(hy2_temp_port_from_any "$TAG" 2>/dev/null || true)"
LANDING="$(hy2_meta_get "$META" LANDING 2>/dev/null || true)"
WG_IF="$(hy2_meta_get "$META" WG_IF 2>/dev/null || true)"
TABLE_ID="$(hy2_meta_get "$META" TABLE_ID 2>/dev/null || true)"
OIF_RULE_PRIORITY="$(hy2_meta_get "$META" OIF_RULE_PRIORITY 2>/dev/null || true)"

if [[ "$FORCE" != "1" && -f "$META" ]]; then
  EXPIRE_EPOCH="$(hy2_meta_get "$META" EXPIRE_EPOCH || true)"
  if [[ "$EXPIRE_EPOCH" =~ ^[0-9]+$ ]]; then
    NOW="$(date +%s)"
    if (( EXPIRE_EPOCH > NOW )); then
      exit 0
    fi
  elif (( FROM_STOP_POST == 1 )); then
    # Corrupt/missing EXPIRE reached via ExecStopPost (e.g. a watchdog
    # restart of a live node): preserve rather than silently destroy.  The GC
    # timer force-cleans genuinely corrupt nodes on its own path.
    exit 0
  fi
fi

if (( FROM_STOP_POST == 1 )) && [[ -f "$STOPPOST_BYPASS_FILE" ]]; then
  BYPASS_PID="$(cat "$STOPPOST_BYPASS_FILE" 2>/dev/null || true)"
  if [[ "$BYPASS_PID" =~ ^[0-9]+$ ]] && kill -0 "$BYPASS_PID" 2>/dev/null; then
    SKIP_LOCK=1
  else
    rm -f "$STOPPOST_BYPASS_FILE"
  fi
fi

if [[ "${HY2_TEMP_LOCK_HELD:-0}" != "1" && "$SKIP_LOCK" != "1" ]]; then
  hy2_acquire_lock_fd 7 "${HY2_LOCK_DIR}/temp.lock" 20 "temp 锁繁忙"
  export HY2_TEMP_LOCK_HELD=1
fi

# The WG state lock (fd5) is held by the parent when SKIP_LOCK is set (the
# parent wrote the stop-post bypass before it called `systemctl stop`), so the
# ExecStopPost child must not try to re-acquire it.
if [[ "$LANDING" == "nat" && "${HY2_WG_STATE_LOCK_HELD:-0}" != "1" && "$SKIP_LOCK" != "1" ]]; then
  install -d -m 755 /run/hy2-wg
  exec 5>/run/hy2-wg/temp.lock
  flock -w 120 5 || hy2_die "项目 WG-NAT 管理任务仍在运行"
  export HY2_WG_STATE_LOCK_HELD=1
fi

if (( FROM_STOP_POST == 1 )) && [[ "$FORCE" != "1" ]] && [[ "$PORT" =~ ^[0-9]+$ ]]; then
  hy2_pq_save_one "$(hy2_quota_meta_file "$PORT")" >/dev/null 2>&1 || true
fi

if (( FROM_STOP_POST == 0 )); then
  if systemctl is-active --quiet "$UNIT_NAME" 2>/dev/null; then
    printf '%s\n' "$$" >"$STOPPOST_BYPASS_FILE"
    timeout 15 systemctl stop "$UNIT_NAME" >/dev/null 2>&1 || systemctl kill "$UNIT_NAME" >/dev/null 2>&1 || true
    rm -f "$STOPPOST_BYPASS_FILE"
  fi
  if [[ "$PORT" =~ ^[0-9]+$ ]]; then
    hy2_pq_save_one "$(hy2_quota_meta_file "$PORT")" >/dev/null 2>&1 || true
  fi
fi

systemctl disable "$UNIT_NAME" >/dev/null 2>&1 || true
systemctl reset-failed "$UNIT_NAME" >/dev/null 2>&1 || true

if [[ "$PORT" =~ ^[0-9]+$ ]]; then
  HY2_PQ_LOCK_HELD=0 hy2_pq_delete_managed_port "$PORT" || true
  HY2_IL_LOCK_HELD=0 hy2_il_delete_managed_port "$PORT" || true
fi

rm -f "$STOPPOST_BYPASS_FILE"
rm -f "$CFG" "$META" "$UNIT_FILE" "$URL_FILE"
if [[ "$LANDING" == "nat" && -n "$WG_IF" && "$TABLE_ID" =~ ^[0-9]+$ \
      && "$OIF_RULE_PRIORITY" =~ ^[0-9]+$ ]]; then
  KEEP_OIF_RULE=0
  for other_meta in "$HY2_TEMP_STATE_DIR"/*.env; do
    [[ -f "$other_meta" ]] || continue
    [[ "$(hy2_meta_get "$other_meta" LANDING 2>/dev/null || true)" == "nat" ]] || continue
    [[ "$(hy2_meta_get "$other_meta" WG_IF 2>/dev/null || true)" == "$WG_IF" ]] || continue
    [[ "$(hy2_meta_get "$other_meta" TABLE_ID 2>/dev/null || true)" == "$TABLE_ID" ]] || continue
    [[ "$(hy2_meta_get "$other_meta" OIF_RULE_PRIORITY 2>/dev/null || true)" == "$OIF_RULE_PRIORITY" ]] || continue
    KEEP_OIF_RULE=1
    break
  done
  if (( KEEP_OIF_RULE == 0 )); then
    while ip -4 rule del priority "$OIF_RULE_PRIORITY" oif "$WG_IF" lookup "$TABLE_ID" >/dev/null 2>&1; do :; done
  fi
fi
systemctl daemon-reload >/dev/null 2>&1 || true
/usr/local/sbin/hy2_temp_sub.sh >/dev/null 2>&1 || true
hy2_log gc.log "[cleanup] tag=${TAG} port=${PORT:-unknown} mode=${MODE:-normal} force=${FORCE}"
echo "✅ 已清理临时节点：${TAG}"

EOF
  chmod 755 "${HY2_SBIN_DIR}/hy2_cleanup_one.sh"

  cat >"${HY2_SBIN_DIR}/hy2_clear_all.sh" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
trap 'echo "❌ ${BASH_SOURCE[0]}:${LINENO}: ${BASH_COMMAND}" >&2' ERR
shopt -s nullglob

# shellcheck disable=SC1091
source /usr/local/lib/hy2/common.sh

hy2_ensure_runtime_dirs
hy2_acquire_lock_fd 7 "${HY2_LOCK_DIR}/temp.lock" 20 "temp 锁繁忙"
export HY2_TEMP_LOCK_HELD=1

mapfile -t TAGS < <(hy2_collect_temp_tags)
if (( ${#TAGS[@]} == 0 )); then
  echo "当前没有任何临时 HY2 节点。"
  exit 0
fi

for tag in "${TAGS[@]}"; do
  [[ -n "$tag" ]] || continue
  FORCE=1 HY2_TEMP_LOCK_HELD=1 /usr/local/sbin/hy2_cleanup_one.sh "$tag" || true
done

echo "✅ 所有临时 HY2 节点已清理。"
EOF
  chmod 755 "${HY2_SBIN_DIR}/hy2_clear_all.sh"

  cat >"${HY2_SBIN_DIR}/hy2_gc.sh" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
trap 'echo "❌ ${BASH_SOURCE[0]}:${LINENO}: ${BASH_COMMAND}" >&2' ERR

# shellcheck disable=SC1091
source /usr/local/lib/hy2/common.sh

hy2_ensure_runtime_dirs
if [[ "${HY2_TEMP_LOCK_HELD:-0}" != "1" ]]; then
  if ! hy2_try_lock_fd 7 "${HY2_LOCK_DIR}/temp.lock"; then
    exit 0
  fi
  export HY2_TEMP_LOCK_HELD=1
fi

NOW="$(date +%s)"
mapfile -t TAGS < <(hy2_collect_temp_tags)

for tag in "${TAGS[@]}"; do
  [[ -n "$tag" ]] || continue
  meta="$(hy2_temp_meta_file "$tag")"
  cfg="$(hy2_temp_cfg_file "$tag")"
  unit="$(hy2_temp_unit_file "$tag")"

  if [[ ! -f "$meta" ]]; then
    FORCE=1 HY2_TEMP_LOCK_HELD=1 /usr/local/sbin/hy2_cleanup_one.sh "$tag" >/dev/null 2>&1 || true
    continue
  fi

  expire_epoch="$(hy2_meta_get "$meta" EXPIRE_EPOCH || true)"
  if [[ ! "$expire_epoch" =~ ^[0-9]+$ ]]; then
    FORCE=1 HY2_TEMP_LOCK_HELD=1 /usr/local/sbin/hy2_cleanup_one.sh "$tag" >/dev/null 2>&1 || true
    continue
  fi

  if (( expire_epoch <= NOW )); then
    HY2_TEMP_LOCK_HELD=1 /usr/local/sbin/hy2_cleanup_one.sh "$tag" >/dev/null 2>&1 || true
    continue
  fi

  if [[ ! -f "$cfg" || ! -f "$unit" ]]; then
    if ! systemctl is-active --quiet "${tag}.service" 2>/dev/null; then
      FORCE=1 HY2_TEMP_LOCK_HELD=1 /usr/local/sbin/hy2_cleanup_one.sh "$tag" >/dev/null 2>&1 || true
    fi
  fi
done
EOF
  chmod 755 "${HY2_SBIN_DIR}/hy2_gc.sh"

  cat >"${HY2_SBIN_DIR}/hy2_restore_all.sh" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
trap 'echo "❌ ${BASH_SOURCE[0]}:${LINENO}: ${BASH_COMMAND}" >&2' ERR

# shellcheck disable=SC1091
source /usr/local/lib/hy2/common.sh

hy2_ensure_runtime_dirs

rc=0

/usr/local/sbin/hy2_gc.sh || rc=1
/usr/local/sbin/pq_restore_all.sh || rc=1
/usr/local/sbin/iplimit_restore_all.sh || rc=1

systemctl daemon-reload >/dev/null 2>&1 || true

exit "$rc"
EOF
  chmod 755 "${HY2_SBIN_DIR}/hy2_restore_all.sh"

  cat >"${HY2_SBIN_DIR}/hy2_audit.sh" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
trap 'echo "❌ ${BASH_SOURCE[0]}:${LINENO}: ${BASH_COMMAND}" >&2' ERR

# shellcheck disable=SC1091
source /usr/local/lib/hy2/common.sh
# shellcheck disable=SC1091
source /usr/local/lib/hy2/quota-lib.sh
# shellcheck disable=SC1091
source /usr/local/lib/hy2/iplimit-lib.sh

FILTER_TAG=""
if [[ "${1:-}" == "--tag" ]]; then
  FILTER_TAG="${2:-}"
elif [[ -n "${1:-}" ]]; then
  FILTER_TAG="${1:-}"
fi

hy2_ensure_runtime_dirs
hy2_pq_lock

quota_summary() {
  local port="$1"
  local snapshot state original used left
  snapshot="$(hy2_pq_snapshot "$port")"
  IFS='|' read -r state original used left <<<"$snapshot"
  if [[ "$state" == "none" ]]; then
    printf 'none|-|-|-|-\n'
    return 0
  fi
  printf '%s|%s|%s|%s|%s\n' \
    "$state" \
    "$(hy2_human_bytes "$original")" \
    "$(hy2_human_bytes "$used")" \
    "$(hy2_human_bytes "$left")" \
    "$(hy2_pct_text "$used" "$original")"
}

ip_summary() {
  local port="$1"
  local meta ip_limit sticky active_count
  meta="$(hy2_iplimit_meta_file "$port")"
  if [[ ! -f "$meta" ]]; then
    printf '%s\n' '-|-|-'
    return 0
  fi
  ip_limit="$(hy2_meta_get "$meta" IP_LIMIT || true)"
  sticky="$(hy2_meta_get "$meta" IP_STICKY_SECONDS || true)"
  active_count="$(hy2_il_active_count "$port" || true)"
  printf '%s|%s|%s\n' "${ip_limit:-0}" "${active_count:-0}" "${sticky:-0}"
}

TMP_ROWS="$(mktemp)"
trap 'rm -f "$TMP_ROWS"' EXIT
FOUND=0

main_port="$(hy2_main_port)"
if [[ -z "$FILTER_TAG" ]]; then
  main_state="$(hy2_unit_state hy2.service)"
  main_lisn="no"
  if [[ "$main_state" == "active" ]] && hy2_port_is_listening_udp "$main_port"; then
    main_lisn="yes"
  fi
  IFS='|' read -r qstate limit used left pct <<<"$(quota_summary "$main_port")"
  IFS='|' read -r ip_limit ip_active sticky <<<"$(ip_summary "$main_port")"
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "hy2.service" \
    "$main_state" \
    "$main_port" \
    "$main_lisn" \
    "4" \
    "local" \
    "$qstate" \
    "$limit" \
    "$used" \
    "$left" \
    "$pct" \
    "-" \
    "-" \
    "${ip_limit:-0}" \
    "${ip_active:-0}" \
    "${sticky:-0}" >>"$TMP_ROWS"
fi

mapfile -t TAGS < <(hy2_collect_temp_tags)
for tag in "${TAGS[@]}"; do
  [[ -n "$tag" ]] || continue
  if [[ -n "$FILTER_TAG" && "$tag" != "$FILTER_TAG" ]]; then
    continue
  fi
  FOUND=1
  meta="$(hy2_temp_meta_file "$tag")"
  port="$(hy2_temp_port_from_any "$tag" 2>/dev/null || true)"
  expire_epoch="$(hy2_meta_get "$meta" EXPIRE_EPOCH 2>/dev/null || true)"
  ip_version="$(hy2_meta_get "$meta" IP_VERSION 2>/dev/null || true)"
  landing="$(hy2_meta_get "$meta" LANDING 2>/dev/null || true)"
  ip_version="${ip_version:-4}"
  landing="${landing:-local}"
  [[ "$port" =~ ^[0-9]+$ ]] || port="-"
  state="$(hy2_unit_state "${tag}.service")"
  lisn="no"
  if [[ "$port" =~ ^[0-9]+$ ]] && [[ "$state" == "active" ]] && hy2_port_is_listening_udp "$port"; then
    lisn="yes"
  fi
  IFS='|' read -r qstate limit used left pct <<<"$(quota_summary "$port")"
  IFS='|' read -r ip_limit ip_active sticky <<<"$(ip_summary "$port")"
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${tag}.service" \
    "$state" \
    "$port" \
    "$lisn" \
    "$ip_version" \
    "$landing" \
    "$qstate" \
    "$limit" \
    "$used" \
    "$left" \
    "$pct" \
    "$(hy2_ttl_human "$expire_epoch")" \
    "$(hy2_beijing_time "$expire_epoch")" \
    "${ip_limit:-0}" \
    "${ip_active:-0}" \
    "${sticky:-0}" >>"$TMP_ROWS"
done

if [[ -n "$FILTER_TAG" && "$FOUND" -eq 0 ]]; then
  exit 1
fi

sort -t $'\t' -k3,3n "$TMP_ROWS" | /usr/local/lib/hy2/render_table.py hy2
EOF
  chmod 755 "${HY2_SBIN_DIR}/hy2_audit.sh"

  cat >"${HY2_SBIN_DIR}/hy2_doctor.sh" <<'EOF'
#!/usr/bin/env bash
set -u

# shellcheck disable=SC1091
source /usr/local/lib/hy2/common.sh

STRICT=0
[[ "${1:-}" == "--strict" ]] && STRICT=1
ERRORS=0
WARNINGS=0
TMP_VERSION="$(mktemp /tmp/hy2-doctor-version.XXXXXX)"
TMP_WG="$(mktemp /tmp/hy2-doctor-wg.XXXXXX)"
trap 'rm -f -- "$TMP_VERSION" "$TMP_WG"' EXIT

ok()   { printf '✅ %s\n' "$*"; }
warn() { printf '⚠️  %s\n' "$*" >&2; WARNINGS=$((WARNINGS + 1)); }
bad()  { printf '❌ %s\n' "$*" >&2; ERRORS=$((ERRORS + 1)); }

check_secure_file() {
  local path="$1" label="$2" max_public_read="${3:-1}" owner mode mode_num
  if [[ ! -e "$path" ]]; then
    warn "${label} 不存在：${path}"
    return 0
  fi
  owner="$(stat -c %u "$path" 2>/dev/null || echo -1)"
  mode="$(stat -c %a "$path" 2>/dev/null || echo 777)"
  [[ "$owner" == "0" ]] || bad "${label} 必须属于 root：${path}"
  if [[ "$mode" =~ ^[0-7]{3,4}$ ]]; then
    mode_num=$((8#$mode))
    (( (mode_num & 8#022) == 0 )) || bad "${label} 可被 group/other 写入：${path} (${mode})"
    if [[ "$max_public_read" == "0" ]]; then
      (( (mode_num & 8#077) == 0 )) || bad "${label} 权限应为 600/700：${path} (${mode})"
    fi
  else
    bad "无法读取 ${label} 权限：${path}"
  fi
}

check_script_syntax() {
  local path="$1"
  [[ -f "$path" ]] || { bad "缺少管理脚本：${path}"; return; }
  bash -n "$path" >/dev/null 2>&1 && ok "语法正常：${path}" || bad "Shell 语法错误：${path}"
}

check_state_schema() {
  local path="$1" label="$2" schema manager
  [[ -f "$path" ]] || return 0
  schema="$(hy2_meta_get "$path" STATE_SCHEMA 2>/dev/null || true)"
  manager="$(hy2_meta_get "$path" MANAGER_VERSION 2>/dev/null || true)"
  [[ "$schema" == "${HY2_STATE_SCHEMA:-1}" ]] \
    && ok "状态格式正常：${label} schema=${schema}" \
    || bad "状态格式缺失或不匹配：${path}（当前=${schema:-missing}，期望=${HY2_STATE_SCHEMA:-1}）"
  [[ -n "$manager" ]] || bad "状态缺少 MANAGER_VERSION：${path}"
}

printf 'HY2 five-file edition v%s doctor\n' "${HY2_BUNDLE_VERSION:-unknown}"
printf '%s\n' '--------------------------------------------------'

[[ ${EUID:-1} -eq 0 ]] || warn "建议使用 root 运行，以便检查 systemd、nftables 和 WireGuard"

check_secure_file /etc/default/hy2-main "主配置" 0
check_secure_file /var/lib/hy2/main/password "主节点密码" 0
check_secure_file /root/hy2_main_url.txt "主节点链接" 0
check_secure_file /etc/wireguard/wg-nat.key "WireGuard 私钥" 0

if command -v hysteria >/dev/null 2>&1; then
  HY_BIN="$(command -v hysteria)"
  if timeout 10 "$HY_BIN" version >"$TMP_VERSION" 2>/dev/null; then
    ok "Hysteria 可执行：$(head -n1 "$TMP_VERSION")"
  else
    bad "Hysteria version 命令失败：${HY_BIN}"
  fi
  check_secure_file "$HY_BIN" "Hysteria 二进制" 1
  expected_core_sha=""
  if [[ -r "$HY2_DEFAULTS_FILE" ]]; then
    expected_core_sha="$(sed -nE 's/^[[:space:]]*HYSTERIA_BINARY_SHA256[[:space:]]*=[[:space:]]*//p' "$HY2_DEFAULTS_FILE" 2>/dev/null | head -n1)"
    expected_core_sha="${expected_core_sha%\"}"
    expected_core_sha="${expected_core_sha#\"}"
    expected_core_sha="${expected_core_sha%\'}"
    expected_core_sha="${expected_core_sha#\'}"
    expected_core_sha="${expected_core_sha//[[:space:]]/}"
  fi
  if [[ -n "$expected_core_sha" ]]; then
    if [[ ! "$expected_core_sha" =~ ^[A-Fa-f0-9]{64}$ ]]; then
      bad "HYSTERIA_BINARY_SHA256 格式非法"
    else
      actual_core_sha="$(sha256sum "$HY_BIN" 2>/dev/null | awk '{print $1}')"
      if [[ "${actual_core_sha,,}" == "${expected_core_sha,,}" ]]; then
        ok "Hysteria 二进制 SHA-256 与固定值一致"
      else
        bad "Hysteria 二进制 SHA-256 不匹配（expected=${expected_core_sha,,}, actual=${actual_core_sha:-unreadable}）"
      fi
    fi
  else
    warn "未设置 HYSTERIA_BINARY_SHA256，doctor 无法持续验证核心完整性"
  fi
else
  bad "未安装 Hysteria"
fi

MAIN_CONFIGURED=0
if [[ -s "$HY2_MAIN_CFG" && -s "$HY2_MAIN_STATE_FILE" ]]; then
  MAIN_CONFIGURED=1
  grep -qE '^[[:space:]]*listen:' "$HY2_MAIN_CFG" || bad "主配置缺少 listen"
  grep -qE '^[[:space:]]*tls:' "$HY2_MAIN_CFG" || bad "主配置缺少 tls"
  grep -qE '^[[:space:]]*auth:' "$HY2_MAIN_CFG" || bad "主配置缺少 auth"
  main_port="$(hy2_main_port 2>/dev/null || true)"
  if systemctl is-active --quiet hy2.service 2>/dev/null; then
    ok "hy2.service 正在运行"
  else
    bad "hy2.service 未运行"
  fi
  if [[ "$main_port" =~ ^[0-9]+$ ]] && hy2_port_is_listening_udp "$main_port"; then
    ok "主节点 UDP/${main_port} 正在监听"
  else
    bad "主节点 UDP 端口未监听"
  fi
  cert="$(hy2_meta_get "$HY2_MAIN_STATE_FILE" TLS_CERT 2>/dev/null || true)"
  key="$(hy2_meta_get "$HY2_MAIN_STATE_FILE" TLS_KEY 2>/dev/null || true)"
  if [[ -s "$cert" && -s "$key" ]]; then
    check_secure_file "$key" "TLS 私钥" 0
    if openssl x509 -in "$cert" -noout -checkend 604800 >/dev/null 2>&1; then
      ok "TLS 证书至少还有 7 天有效期"
    else
      bad "TLS 证书将在 7 天内过期或无法读取：${cert}"
    fi
  else
    bad "主节点证书或私钥缺失"
  fi
else
  (( STRICT == 0 )) && warn "主节点尚未部署；运行 /root/onekey_hy2_main_tls.sh" || bad "严格模式：主节点尚未部署"
fi

check_state_schema "$HY2_MAIN_STATE_FILE" "主节点"
for state_file in "$HY2_TEMP_STATE_DIR"/*.env "$HY2_QUOTA_STATE_DIR"/*.env "$HY2_IPLIMIT_STATE_DIR"/*.env; do
  [[ -f "$state_file" ]] || continue
  check_state_schema "$state_file" "$(basename "$state_file")"
done

for timer in hy2-gc.timer pq-save.timer pq-reset.timer hy2-managed-watchdog.timer; do
  systemctl is-enabled --quiet "$timer" 2>/dev/null && ok "已启用：${timer}" || bad "未启用：${timer}"
  systemctl is-active --quiet "$timer" 2>/dev/null && ok "正在运行：${timer}" || warn "未运行：${timer}"
done

for script in \
  /usr/local/sbin/hy2_mktemp.sh \
  /usr/local/sbin/hy2_cleanup_one.sh \
  /usr/local/sbin/hy2_restore_all.sh \
  /usr/local/sbin/hy2_audit.sh \
  /usr/local/sbin/hy2_temp_sub.sh \
  /usr/local/sbin/hy2_managed_watchdog.sh; do
  check_script_syntax "$script"
done

TEMP_TOTAL=0
TEMP_BAD=0
while IFS= read -r tag; do
  [[ -n "$tag" ]] || continue
  TEMP_TOTAL=$((TEMP_TOTAL + 1))
  meta="$(hy2_temp_meta_file "$tag")"
  port="$(hy2_temp_port_from_any "$tag" 2>/dev/null || true)"
  if [[ ! -s "$meta" || ! "$port" =~ ^[0-9]+$ ]]; then
    bad "临时节点状态不完整：${tag}"
    TEMP_BAD=$((TEMP_BAD + 1))
    continue
  fi
  if systemctl is-active --quiet "${tag}.service" 2>/dev/null && hy2_port_is_listening_udp "$port"; then
    :
  else
    expire="$(hy2_meta_get "$meta" EXPIRE_EPOCH 2>/dev/null || true)"
    if [[ "$expire" =~ ^[0-9]+$ ]] && (( expire > $(date +%s) )); then
      bad "未到期临时节点未正常运行：${tag} UDP/${port}"
      TEMP_BAD=$((TEMP_BAD + 1))
    fi
  fi
done < <(hy2_collect_temp_tags 2>/dev/null || true)
(( TEMP_TOTAL == 0 )) && ok "当前没有临时节点" || ok "检查临时节点：${TEMP_TOTAL} 个，异常 ${TEMP_BAD} 个"

if [[ -f /etc/wireguard/wg-nat.env ]]; then
  if [[ -x /usr/local/sbin/wg_nat_healthcheck.sh ]]; then
    if /usr/local/sbin/wg_nat_healthcheck.sh >"$TMP_WG" 2>&1; then
      ok "WG-NAT 出口正常：$(tail -n1 "$TMP_WG")"
    else
      bad "WG-NAT 健康检查失败：$(tail -n3 "$TMP_WG" | tr '\n' ' ')"
    fi
  else
    bad "存在 wg-nat 状态，但缺少 wg_nat_healthcheck.sh"
  fi
fi

printf '%s\n' '--------------------------------------------------'
printf '结果：%d 个错误，%d 个警告\n' "$ERRORS" "$WARNINGS"
(( ERRORS == 0 ))
EOF
  chmod 755 "${HY2_SBIN_DIR}/hy2_doctor.sh"

  cat >"${HY2_SBIN_DIR}/hy2_mktemp.sh" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
CURRENT_ATTEMPT_ACTIVE=0

rollback_current() {
  CURRENT_ATTEMPT_ACTIVE=0
  hy2_il_unlock
  hy2_pq_unlock
  if ! FORCE=1 HY2_TEMP_LOCK_HELD=1 /usr/local/sbin/hy2_cleanup_one.sh "$TAG" >/dev/null 2>&1; then
    echo "❌ ${TAG} 回滚未完成；停止重试并保留状态供 watchdog/GC 继续清理" >&2
    return 1
  fi
}

on_error() {
  local rc=$?
  trap - ERR
  echo "❌ ${BASH_SOURCE[1]:-${BASH_SOURCE[0]}}:${BASH_LINENO[0]:-?}: ${BASH_COMMAND}" >&2
  exit "$rc"
}

on_exit() {
  local rc=$?
  trap - EXIT ERR
  trap '' INT TERM HUP
  if (( CURRENT_ATTEMPT_ACTIVE == 1 )); then
    rollback_current || true
  fi
  exit "$rc"
}
trap 'on_error' ERR
trap 'on_exit' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

# shellcheck disable=SC1091
source /usr/local/lib/hy2/common.sh
# shellcheck disable=SC1091
source /usr/local/lib/hy2/quota-lib.sh
# shellcheck disable=SC1091
source /usr/local/lib/hy2/iplimit-lib.sh

hy2_require_root_supported_os
hy2_ensure_runtime_dirs

: "${D:?请用 D=秒 调用，例如：id=tmp4 IP_VERSION=4 IP_LIMIT=1 PQ_GIB=1 D=1200 hy2_mktemp.sh}"
DURATION="$D"
PQ_GIB="${PQ_GIB:-}"
IP_LIMIT="${IP_LIMIT:-0}"
IP_STICKY_SECONDS="${IP_STICKY_SECONDS:-120}"
IP_VERSION="${IP_VERSION:-4}"
MAX_START_RETRIES="${MAX_START_RETRIES:-12}"
SERVER_ADDR="${SERVER_ADDR:-}"
LANDING="${LANDING:-local}"
WG_IF="${WG_IF:-wg-nat}"
HANDSHAKE_MAX="${HANDSHAKE_MAX:-180}"
SKIP_HEALTHCHECK="${SKIP_HEALTHCHECK:-0}"

[[ "$DURATION" =~ ^[0-9]+$ && ${#DURATION} -le 10 ]] || hy2_die "D 必须是正整数秒"
DURATION=$((10#$DURATION))
(( DURATION > 0 && DURATION <= 2147483647 )) || hy2_die "D 必须在 1-2147483647 秒"
[[ "$IP_VERSION" == "4" || "$IP_VERSION" == "6" ]] || hy2_die "IP_VERSION 只能是 4 或 6"
[[ "$MAX_START_RETRIES" =~ ^[0-9]+$ && ${#MAX_START_RETRIES} -le 3 ]] || hy2_die "MAX_START_RETRIES 必须是 1-100"
MAX_START_RETRIES=$((10#$MAX_START_RETRIES))
(( MAX_START_RETRIES >= 1 && MAX_START_RETRIES <= 100 )) || hy2_die "MAX_START_RETRIES 必须是 1-100"
[[ "$IP_LIMIT" =~ ^[0-9]+$ && ${#IP_LIMIT} -le 5 ]] || hy2_die "IP_LIMIT 必须是 0-65535"
IP_LIMIT=$((10#$IP_LIMIT))
(( IP_LIMIT <= 65535 )) || hy2_die "IP_LIMIT 必须是 0-65535"
[[ "$IP_STICKY_SECONDS" =~ ^[0-9]+$ && ${#IP_STICKY_SECONDS} -le 10 ]] || hy2_die "IP_STICKY_SECONDS 必须是正整数"
IP_STICKY_SECONDS=$((10#$IP_STICKY_SECONDS))
(( IP_STICKY_SECONDS > 0 && IP_STICKY_SECONDS <= 2147483647 )) || hy2_die "IP_STICKY_SECONDS 必须在 1-2147483647"
[[ -z "$PQ_GIB" || "$PQ_GIB" =~ ^[0-9]+([.][0-9]+)?$ ]] || hy2_die "PQ_GIB 必须是正数"
[[ "$LANDING" == "local" || "$LANDING" == "nat" ]] || hy2_die "LANDING 只能是 local 或 nat"
[[ "$SKIP_HEALTHCHECK" == "0" || "$SKIP_HEALTHCHECK" == "1" ]] || hy2_die "SKIP_HEALTHCHECK 只能是 0 或 1"

if [[ "${HY2_TEMP_LOCK_HELD:-0}" != "1" ]]; then
  hy2_acquire_lock_fd 7 "${HY2_LOCK_DIR}/temp.lock" 120 "temp 锁繁忙"
  export HY2_TEMP_LOCK_HELD=1
fi

hy2_load_defaults
[[ "$ENABLE_SALAMANDER" == "0" || -n "$SALAMANDER_PASSWORD" ]] || hy2_die "启用 Salamander 时必须设置 SALAMANDER_PASSWORD"

BIND_DEVICE=""
MARK=""
TABLE_ID=""
RULE_PRIORITY=""
OIF_RULE_PRIORITY="${OIF_RULE_PRIORITY:-}"
if [[ "$LANDING" == "nat" ]]; then
  install -d -m 755 /run/hy2-wg
  exec 5>/run/hy2-wg/temp.lock
  flock -w 120 5 || hy2_die "项目 WG-NAT 管理任务仍在运行"
  export HY2_WG_STATE_LOCK_HELD=1
  [[ -n "$WG_IF" && ${#WG_IF} -le 15 && "$WG_IF" =~ ^[A-Za-z0-9_.-]+$ \
     && "$WG_IF" != "." && "$WG_IF" != ".." ]] || hy2_die "WG_IF 非法：${WG_IF}"
  [[ "$HANDSHAKE_MAX" =~ ^[0-9]+$ ]] && (( HANDSHAKE_MAX >= 1 && HANDSHAKE_MAX <= 86400 )) \
    || hy2_die "HANDSHAKE_MAX 必须在 1-86400 秒"
  WG_STATE_FILE="/etc/wireguard/${WG_IF}.env"
  [[ -f "$WG_STATE_FILE" ]] || hy2_die "缺少 ${WG_STATE_FILE}；请先运行项目中的 vpswg.sh"
  [[ "$(stat -c %u "$WG_STATE_FILE" 2>/dev/null || echo -1)" == "0" ]] \
    || hy2_die "${WG_STATE_FILE} 必须属于 root"
  WG_STATE_MODE="$(stat -c %a "$WG_STATE_FILE" 2>/dev/null || echo 777)"
  [[ "$WG_STATE_MODE" =~ ^[0-7]{3,4}$ ]] && (( ((8#$WG_STATE_MODE) & 8#022) == 0 )) \
    || hy2_die "${WG_STATE_FILE} 不能被 group/other 写入"
  MARK="$(sed -n 's/^MARK=//p' "$WG_STATE_FILE" | head -n1)"
  TABLE_ID="$(sed -n 's/^TABLE_ID=//p' "$WG_STATE_FILE" | head -n1)"
  RULE_PRIORITY="$(sed -n 's/^RULE_PRIORITY=//p' "$WG_STATE_FILE" | head -n1)"
  [[ "$MARK" =~ ^[0-9]+$ && "$TABLE_ID" =~ ^[0-9]+$ && "$RULE_PRIORITY" =~ ^[0-9]+$ ]] \
    || hy2_die "${WG_STATE_FILE} 缺少有效的 MARK/TABLE_ID/RULE_PRIORITY"
  OIF_RULE_PRIORITY="${OIF_RULE_PRIORITY:-$((RULE_PRIORITY - 1))}"
  [[ "$OIF_RULE_PRIORITY" =~ ^[0-9]+$ ]] && (( OIF_RULE_PRIORITY >= 1 && OIF_RULE_PRIORITY <= 32765 )) \
    || hy2_die "OIF_RULE_PRIORITY 必须在 1-32765"
  [[ -x /usr/local/sbin/wg_nat_guard.sh && -x /usr/local/sbin/wg_nat_healthcheck.sh ]] \
    || hy2_die "WireGuard NAT 组件不完整；请先运行项目中的 vpswg.sh"
  if [[ "$SKIP_HEALTHCHECK" == "1" ]]; then
    echo "⚠️  已按 SKIP_HEALTHCHECK=1 跳过 WireGuard NAT 出口检查" >&2
  else
    HANDSHAKE_MAX="$HANDSHAKE_MAX" WG_IF="$WG_IF" MARK="$MARK" TABLE_ID="$TABLE_ID" \
      RULE_PRIORITY="$RULE_PRIORITY" /usr/local/sbin/wg_nat_healthcheck.sh \
      || hy2_die "wg-nat 出口不可用"
  fi
  systemctl start "wg-nat-guard@${WG_IF}.service" >/dev/null
  /usr/local/sbin/wg_nat_guard.sh "$WG_IF"
  BIND_DEVICE="$WG_IF"
fi

PORT_START="${PORT_START:-$TEMP_PORT_START}"
PORT_END="${PORT_END:-$TEMP_PORT_END}"
[[ "$PORT_START" =~ ^[0-9]+$ && "$PORT_END" =~ ^[0-9]+$ ]] \
  && (( PORT_START >= 1 && PORT_END <= 65535 && PORT_START <= PORT_END )) || hy2_die "PORT_START/PORT_END 无效"

PUBLISHED_DOMAIN="$HY_DOMAIN"
LISTEN_VALUE=""
if [[ "$IP_VERSION" == "6" ]]; then
  [[ -n "${HY_IPV6_DOMAIN:-}" ]] || hy2_die "IP_VERSION=6 时必须在 ${HY2_DEFAULTS_FILE} 设置 HY_IPV6_DOMAIN"
  mapfile -t SERVER_IPS < <(hy2_get_public_ipv6_candidates)
  hy2_require_domain_points_here "$HY_IPV6_DOMAIN" 6 "${SERVER_IPS[@]}"
  PUBLISHED_DOMAIN="$HY_IPV6_DOMAIN"
else
  mapfile -t SERVER_IPS < <(hy2_get_public_ipv4_candidates)
  hy2_require_domain_points_here "$HY_DOMAIN" 4 "${SERVER_IPS[@]}"
fi
SERVER_ADDR="${SERVER_ADDR:-$PUBLISHED_DOMAIN}"
hy2_validate_server_addr "$SERVER_ADDR" "$IP_VERSION" \
  || hy2_die "SERVER_ADDR=${SERVER_ADDR} 没有可用的 IPv${IP_VERSION} 地址"

mapfile -t certs < <(hy2_main_cert_paths "$HY_DOMAIN")
CRT="${certs[0]}"
KEY="${certs[1]}"
[[ -s "$CRT" && -s "$KEY" ]] || hy2_die "缺少正式证书，请先执行：bash /root/onekey_hy2_main_tls.sh"
[[ -x "$(command -v hysteria || true)" ]] || hy2_die "未找到 hysteria 可执行文件"

RAW_ID="${id:-$(date +%Y%m%d%H%M%S)-$(openssl rand -hex 2)}"
SAFE_ID="$(hy2_safe_id "$RAW_ID")"
TAG="$(hy2_temp_tag_from_id "$SAFE_ID")"
META="$(hy2_temp_meta_file "$TAG")"
CFG="$(hy2_temp_cfg_file "$TAG")"
UNIT_FILE="$(hy2_temp_unit_file "$TAG")"
URL_FILE="$(hy2_temp_url_file "$TAG")"
UNIT_NAME="${TAG}.service"

if [[ -f "$META" ]]; then
  EXIST_EXPIRE="$(hy2_meta_get "$META" EXPIRE_EPOCH 2>/dev/null || true)"
  if [[ "$EXIST_EXPIRE" =~ ^[0-9]+$ ]] && (( EXIST_EXPIRE <= $(date +%s) )); then
    FORCE=1 HY2_TEMP_LOCK_HELD=1 /usr/local/sbin/hy2_cleanup_one.sh "$TAG" >/dev/null 2>&1 \
      || hy2_die "旧的过期节点 ${TAG} 清理未完成"
  else
    hy2_die "临时节点 ${TAG} 已存在"
  fi
fi

PQ_LIMIT_BYTES=""
if [[ -n "$PQ_GIB" ]]; then
  PQ_LIMIT_BYTES="$(hy2_parse_gib_to_bytes "$PQ_GIB")" || hy2_die "PQ_GIB 必须是正数"
  [[ "$PQ_LIMIT_BYTES" =~ ^[0-9]+$ ]] && (( PQ_LIMIT_BYTES > 0 )) || hy2_die "PQ_GIB 转换失败"
fi

CREATE_EPOCH="$(date +%s)"
EXPIRE_EPOCH=$((CREATE_EPOCH + DURATION))
MAIN_PORT="$(hy2_main_port)"

validate_full_state() {
  local meta="$1" port="$2"
  [[ -f "$meta" && -f "$CFG" && -f "$UNIT_FILE" && -s "$URL_FILE" ]] || return 1
  [[ -f /root/hy2_temp_subscription.txt ]] || return 1
  grep -Fxq -- "$HY2_URL" /root/hy2_temp_subscription.txt || return 1
  [[ "$(hy2_meta_get "$meta" IP_VERSION 2>/dev/null || true)" == "$IP_VERSION" ]] || return 1
  [[ "$(hy2_meta_get "$meta" SERVER_ADDR 2>/dev/null || true)" == "$SERVER_ADDR" ]] || return 1
  [[ "$(hy2_meta_get "$meta" LANDING 2>/dev/null || true)" == "$LANDING" ]] || return 1
  systemctl is-active --quiet "$UNIT_NAME" || return 1
  hy2_port_is_listening_udp "$port" || return 1
  if [[ -n "$PQ_LIMIT_BYTES" ]]; then
    [[ -f "$(hy2_quota_meta_file "$port")" ]] || return 1
    [[ "$(hy2_pq_state "$port")" == "active" ]] || return 1
  fi
  if (( IP_LIMIT > 0 )); then
    local imeta
    imeta="$(hy2_iplimit_meta_file "$port")"
    [[ -f "$imeta" && "$(hy2_meta_get "$imeta" IP_VERSION 2>/dev/null || true)" == "$IP_VERSION" ]] || return 1
    [[ "$(hy2_il_state "$port")" == "active" ]] || return 1
  else
    [[ "$(hy2_il_family_guard_state "$port")" == "active" ]] || return 1
  fi
  if [[ "$LANDING" == "nat" ]]; then
    systemctl is-active --quiet "wg-quick@${WG_IF}.service" || return 1
    route_result="$(ip -4 route get 1.1.1.1 oif "$WG_IF" 2>/dev/null || true)"
    [[ "$route_result" == *" dev ${WG_IF} "* || "$route_result" == *" dev ${WG_IF}" ]] || return 1
  fi
  /usr/local/sbin/hy2_audit.sh --tag "$TAG" >/dev/null 2>&1
}

declare -A FAILED_PORTS=()
ATTEMPT=0
while (( ATTEMPT < MAX_START_RETRIES )); do
  ATTEMPT=$((ATTEMPT + 1))
  mapfile -t USED_PORTS < <(hy2_collect_used_ports | awk '/^[0-9]+$/ {print}' | sort -n -u)
  declare -A USED=()
  for p in "${USED_PORTS[@]}"; do USED["$p"]=1; done
  for p in "${!FAILED_PORTS[@]}"; do USED["$p"]=1; done

  PORT=""
  for ((CANDIDATE=PORT_START; CANDIDATE<=PORT_END; CANDIDATE++)); do
    if [[ -z "${USED[$CANDIDATE]+x}" ]]; then
      PORT="$CANDIDATE"
      break
    fi
  done
  [[ -n "$PORT" ]] || hy2_die "在 ${PORT_START}-${PORT_END} 范围内没有空闲端口"
  CURRENT_ATTEMPT_ACTIVE=1

  PASSWORD="$(openssl rand -hex 16)"
  if [[ "$IP_VERSION" == "6" ]]; then
    LISTEN_VALUE="[::]:${PORT}"
  else
    LISTEN_VALUE="0.0.0.0:${PORT}"
  fi
  hy2_write_server_cfg "$CFG" "$LISTEN_VALUE" "$PASSWORD" "$CRT" "$KEY" "$MASQ_URL" "$ENABLE_SALAMANDER" "$SALAMANDER_PASSWORD" "$BIND_DEVICE" 4
  HY2_URL="$(hy2_build_url "$PASSWORD" "$SERVER_ADDR" "$PORT" "$TAG" "$ENABLE_SALAMANDER" "$SALAMANDER_PASSWORD" "$HY_DOMAIN")"
  URL_TMP="$(mktemp "${URL_FILE}.tmp.XXXXXX")"
  printf '%s\n' "$HY2_URL" >"$URL_TMP"
  chmod 600 "$URL_TMP"
  mv -f "$URL_TMP" "$URL_FILE"

  META_LINES=( \
    "TAG=${TAG}" "ID=${SAFE_ID}" "PORT=${PORT}" \
    "HY_DOMAIN=${HY_DOMAIN}" "PUBLIC_DOMAIN=${PUBLISHED_DOMAIN}" "SERVER_ADDR=${SERVER_ADDR}" \
    "IP_VERSION=${IP_VERSION}" "LISTEN_ADDR=${LISTEN_VALUE}" "PASSWORD=${PASSWORD}" \
    "CREATE_EPOCH=${CREATE_EPOCH}" "EXPIRE_EPOCH=${EXPIRE_EPOCH}" "DURATION_SECONDS=${DURATION}" \
    "MASQ_URL=${MASQ_URL}" "ENABLE_SALAMANDER=${ENABLE_SALAMANDER}" \
    "SALAMANDER_PASSWORD=${SALAMANDER_PASSWORD}" "PQ_GIB=${PQ_GIB}" \
    "PQ_LIMIT_BYTES=${PQ_LIMIT_BYTES}" "IP_LIMIT=${IP_LIMIT}" \
    "IP_STICKY_SECONDS=${IP_STICKY_SECONDS}" "LANDING=${LANDING}" )
  if [[ "$LANDING" == "nat" ]]; then
    META_LINES+=( \
      "WG_IF=${WG_IF}" "MARK=${MARK}" "TABLE_ID=${TABLE_ID}" \
      "RULE_PRIORITY=${RULE_PRIORITY}" "OIF_RULE_PRIORITY=${OIF_RULE_PRIORITY}" \
      "HANDSHAKE_MAX=${HANDSHAKE_MAX}" )
  fi
  hy2_write_meta "$META" "${META_LINES[@]}"

  hy2_write_temp_unit "$TAG" "$CFG" "$LANDING" "$WG_IF"
  if command -v systemd-analyze >/dev/null 2>&1 \
    && ! systemd-analyze verify "$UNIT_FILE" >/dev/null 2>&1
  then
    rollback_current || hy2_die "回滚失败"
    FAILED_PORTS["$PORT"]=1
    continue
  fi

  if [[ -n "$PQ_LIMIT_BYTES" ]] \
    && ! hy2_pq_add_managed_port "$PORT" "$PQ_LIMIT_BYTES" temp "$TAG" "$DURATION" "$EXPIRE_EPOCH"
  then
    rollback_current || hy2_die "回滚失败"
    FAILED_PORTS["$PORT"]=1
    continue
  fi

  if (( IP_LIMIT > 0 )); then
    if ! hy2_il_add_managed_port "$PORT" "$IP_LIMIT" "$IP_STICKY_SECONDS" temp "$TAG" "$IP_VERSION"; then
      rollback_current || hy2_die "回滚失败"
      FAILED_PORTS["$PORT"]=1
      continue
    fi
  elif ! hy2_il_apply_family_guard "$PORT" "$IP_VERSION"; then
    rollback_current || hy2_die "回滚失败"
    FAILED_PORTS["$PORT"]=1
    continue
  fi

  hy2_il_unlock
  hy2_pq_unlock
  if [[ "${HY2_WG_STATE_LOCK_HELD:-0}" == "1" ]]; then
    flock -u 5 >/dev/null 2>&1 || true
    { exec 5>&-; } 2>/dev/null || true
    unset HY2_WG_STATE_LOCK_HELD
  fi
  systemctl daemon-reload
  systemctl enable "$UNIT_NAME" >/dev/null
  if ! systemctl start "$UNIT_NAME" \
    || ! hy2_wait_unit_and_udp_port "$UNIT_NAME" "$PORT" 3 12
  then
    rollback_current || hy2_die "回滚失败"
    FAILED_PORTS["$PORT"]=1
    continue
  fi
  if ! /usr/local/sbin/hy2_temp_sub.sh >/dev/null 2>&1; then
    rollback_current || hy2_die "回滚失败"
    FAILED_PORTS["$PORT"]=1
    continue
  fi

  if ! validate_full_state "$META" "$PORT"; then
    echo "❌ ${TAG} 最终状态校验失败" >&2
    /usr/local/sbin/hy2_audit.sh --tag "$TAG" >&2 || true
    journalctl -u "$UNIT_NAME" -n 80 --no-pager >&2 || true
    rollback_current || hy2_die "回滚失败"
    FAILED_PORTS["$PORT"]=1
    continue
  fi

  if [[ "$LANDING" == "nat" ]]; then
    echo "✅ WG-NAT 落地临时节点创建成功"
  else
    echo "✅ 临时节点创建成功"
  fi
  echo "TAG: ${TAG}"
  echo "PORT: ${PORT}"
  echo "IP_VERSION: ${IP_VERSION}"
  echo "SERVER_ADDR: ${SERVER_ADDR}"
  echo "LANDING: ${LANDING}"
  if [[ "$LANDING" == "nat" ]]; then
    echo "WG_IF: ${WG_IF}"
    echo "TABLE_ID: ${TABLE_ID}"
    echo "OIF_RULE_PRIORITY: ${OIF_RULE_PRIORITY}"
  fi
  echo "TTL: $(hy2_ttl_human "$EXPIRE_EPOCH")"
  echo "到期(北京时间): $(hy2_beijing_time "$EXPIRE_EPOCH")"
  [[ -n "$PQ_LIMIT_BYTES" ]] && echo "配额: $(hy2_human_bytes "$PQ_LIMIT_BYTES")"
  (( IP_LIMIT > 0 )) && echo "IP_LIMIT: ${IP_LIMIT} / sticky ${IP_STICKY_SECONDS}s"
  echo "URL: ${HY2_URL}"
  CURRENT_ATTEMPT_ACTIVE=0
  exit 0
done

hy2_die "临时节点创建失败，已回滚（尝试次数：${MAX_START_RETRIES}）"
EOF
  chmod 755 "${HY2_SBIN_DIR}/hy2_mktemp.sh"

  cat >"${HY2_SBIN_DIR}/hy2_temp_sub.sh" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
trap 'echo "❌ ${BASH_SOURCE[0]}:${LINENO}: ${BASH_COMMAND}" >&2' ERR
umask 077

# shellcheck disable=SC1091
source /usr/local/lib/hy2/common.sh

OUT_RAW="/root/hy2_temp_subscription.txt"
OUT_B64="/root/hy2_temp_subscription_base64.txt"
TMP="$(mktemp)"
RAW_TMP="$(mktemp /root/.hy2_temp_subscription.txt.XXXXXX)"
B64_TMP="$(mktemp /root/.hy2_temp_subscription_base64.txt.XXXXXX)"
trap 'rm -f "$TMP" "$RAW_TMP" "$B64_TMP"' EXIT

hy2_ensure_runtime_dirs
if [[ "${HY2_TEMP_LOCK_HELD:-0}" != "1" ]]; then
  hy2_acquire_lock_fd 7 "${HY2_LOCK_DIR}/temp.lock" 120 "temp 锁繁忙"
  export HY2_TEMP_LOCK_HELD=1
fi

NOW="$(date +%s)"
for meta in "$HY2_TEMP_STATE_DIR"/*.env; do
  [[ -f "$meta" ]] || continue
  tag="$(hy2_meta_get "$meta" TAG 2>/dev/null || true)"
  exp="$(hy2_meta_get "$meta" EXPIRE_EPOCH 2>/dev/null || true)"
  port="$(hy2_meta_get "$meta" PORT 2>/dev/null || true)"
  hy2_is_valid_temp_tag "$tag" || continue
  [[ "$tag" == "$(basename "$meta" .env)" ]] || continue
  [[ "$exp" =~ ^[0-9]+$ && "$port" =~ ^[0-9]+$ ]] || continue
  (( exp > NOW )) || continue
  systemctl is-active --quiet "${tag}.service" || continue
  hy2_port_is_listening_udp "$port" || continue
  url_file="$(hy2_temp_url_file "$tag")"
  [[ -s "$url_file" ]] || continue
  sed -n '1p' "$url_file" >>"$TMP"
done

sort -u "$TMP" >"$RAW_TMP"
hy2_base64_one_line <"$RAW_TMP" >"$B64_TMP"
chmod 600 "$RAW_TMP" "$B64_TMP"
mv -f "$RAW_TMP" "$OUT_RAW"
mv -f "$B64_TMP" "$OUT_B64"
printf 'RAW: %s\nBASE64: %s\n' "$OUT_RAW" "$OUT_B64"
EOF
  chmod 755 "${HY2_SBIN_DIR}/hy2_temp_sub.sh"

  cat >"${HY2_SBIN_DIR}/hy2_managed_watchdog.sh" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
trap 'echo "❌ ${BASH_SOURCE[0]}:${LINENO}: ${BASH_COMMAND}" >&2' ERR

# shellcheck disable=SC1091
source /usr/local/lib/hy2/common.sh
# shellcheck disable=SC1091
source /usr/local/lib/hy2/quota-lib.sh
# shellcheck disable=SC1091
source /usr/local/lib/hy2/iplimit-lib.sh

hy2_require_root_supported_os
hy2_ensure_runtime_dirs
if ! hy2_try_lock_fd 7 "${HY2_LOCK_DIR}/temp.lock"; then
  exit 0
fi
export HY2_TEMP_LOCK_HELD=1
rc=0
now="$(date +%s)"

stop_temp_listener_failclosed() {
  local tag="$1" bypass="${HY2_LOCK_DIR}/stoppost-bypass.${tag}"
  hy2_is_valid_temp_tag "$tag" || return 1
  printf '%s\n' "$$" >"$bypass"
  if ! timeout 20 systemctl stop "${tag}.service" >/dev/null 2>&1; then
    systemctl kill --kill-who=all --signal=KILL "${tag}.service" >/dev/null 2>&1 || true
  fi
  rm -f "$bypass"
}

for meta in "$HY2_QUOTA_STATE_DIR"/*.env; do
  [[ -f "$meta" ]] || continue
  port="$(hy2_meta_get "$meta" PORT 2>/dev/null || true)"
  [[ "$port" =~ ^[0-9]+$ ]] || continue
  if [[ "$(hy2_pq_state "$port")" == "stale" ]]; then
    echo "⚠️  修复 quota:${port}" >&2
    # Hold the quota lock across the whole repair so a concurrent pq-save
    # cannot read/rebuild the same counters mid-repair (double-count).  If the
    # nft objects were fully wiped, live usage is unreadable and save_one
    # bails; fall back to a deterministic rebuild from the persisted remaining
    # bytes so the port fail-closes instead of silently losing enforcement.
    hy2_pq_lock
    if ! hy2_pq_save_one "$meta"; then
      hy2_pq_restore_one "$meta" || rc=1
    fi
    hy2_pq_unlock
  fi
done

for meta in "$HY2_IPLIMIT_STATE_DIR"/*.env; do
  [[ -f "$meta" ]] || continue
  port="$(hy2_meta_get "$meta" PORT 2>/dev/null || true)"
  [[ "$port" =~ ^[0-9]+$ ]] || continue
  if [[ "$(hy2_il_state "$port")" == "stale" ]]; then
    echo "⚠️  修复 iplimit:${port}" >&2
    hy2_il_lock
    hy2_il_restore_one "$meta" || rc=1
    hy2_il_unlock
  fi
done

for meta in "$HY2_TEMP_STATE_DIR"/*.env; do
  [[ -f "$meta" ]] || continue
  tag="$(hy2_meta_get "$meta" TAG 2>/dev/null || true)"
  file_tag="$(basename "$meta" .env)"
  port="$(hy2_meta_get "$meta" PORT 2>/dev/null || true)"
  expire="$(hy2_meta_get "$meta" EXPIRE_EPOCH 2>/dev/null || true)"
  ip_version="$(hy2_meta_get "$meta" IP_VERSION 2>/dev/null || true)"
  ip_limit="$(hy2_meta_get "$meta" IP_LIMIT 2>/dev/null || true)"
  pq_limit="$(hy2_meta_get "$meta" PQ_LIMIT_BYTES 2>/dev/null || true)"
  ip_version="${ip_version:-4}"
  ip_limit="${ip_limit:-0}"

  if ! hy2_is_valid_temp_tag "$tag" || [[ "$tag" != "$file_tag" ]]; then
    echo "❌ 临时节点元数据 TAG 非法或与文件名不一致：${meta}" >&2
    hy2_is_valid_temp_tag "$file_tag" && stop_temp_listener_failclosed "$file_tag" || true
    rc=1
    continue
  fi
  if [[ ! "$port" =~ ^[0-9]+$ || ! "$expire" =~ ^[0-9]+$ \
        || ( "$ip_version" != "4" && "$ip_version" != "6" ) \
        || ! "$ip_limit" =~ ^[0-9]+$ || ${#ip_limit} -gt 5 ]]; then
    echo "❌ 临时节点 ${tag} 的防护元数据非法，停止监听并保留状态" >&2
    stop_temp_listener_failclosed "$tag" || true
    rc=1
    continue
  fi
  if (( expire <= now )); then
    FORCE=1 HY2_TEMP_LOCK_HELD=1 /usr/local/sbin/hy2_cleanup_one.sh "$tag" || rc=1
    continue
  fi

  if (( ip_limit == 0 )) && [[ "$(hy2_il_family_guard_state "$port")" == "stale" ]]; then
    echo "⚠️  修复 family:${port}" >&2
    hy2_il_apply_family_guard "$port" "$ip_version" || rc=1
    hy2_il_unlock
  fi

  protection_ready=1
  if [[ -n "$pq_limit" ]]; then
    if [[ ! "$pq_limit" =~ ^[0-9]+$ || ${#pq_limit} -gt 19 ]] || (( pq_limit <= 0 )); then
      protection_ready=0
    else
      case "$(hy2_pq_state "$port")" in
        active|exhausted) ;;
        *) protection_ready=0 ;;
      esac
    fi
  fi
  if (( ip_limit > 0 )); then
    [[ "$(hy2_il_state "$port")" == "active" ]] || protection_ready=0
  else
    [[ "$(hy2_il_family_guard_state "$port")" == "active" ]] || protection_ready=0
  fi
  if (( protection_ready == 0 )); then
    echo "❌ 临时节点 ${tag} 的 nftables 防护未就绪，停止监听并等待修复" >&2
    stop_temp_listener_failclosed "$tag" || true
    rc=1
    continue
  fi

  node_healthy=0
  if systemctl is-active --quiet "${tag}.service" && hy2_port_is_listening_udp "$port"; then
    node_healthy=1
  fi
  landing="$(hy2_meta_get "$meta" LANDING 2>/dev/null || true)"
  if (( node_healthy == 1 )) && [[ "$landing" == "nat" ]]; then
    wg_if="$(hy2_meta_get "$meta" WG_IF 2>/dev/null || true)"
    table_id="$(hy2_meta_get "$meta" TABLE_ID 2>/dev/null || true)"
    oif_priority="$(hy2_meta_get "$meta" OIF_RULE_PRIORITY 2>/dev/null || true)"
    handshake_max="$(hy2_meta_get "$meta" HANDSHAKE_MAX 2>/dev/null || true)"
    handshake_max="${handshake_max:-180}"
    [[ -n "$wg_if" && "$table_id" =~ ^[0-9]+$ && "$oif_priority" =~ ^[0-9]+$ \
       && "$handshake_max" =~ ^[0-9]+$ ]] || node_healthy=0
    if (( node_healthy == 1 )); then
      systemctl is-active --quiet "wg-quick@${wg_if}.service" || node_healthy=0
      route_result="$(ip -4 route get 1.1.1.1 oif "$wg_if" 2>/dev/null || true)"
      [[ "$route_result" == *" dev ${wg_if} "* || "$route_result" == *" dev ${wg_if}" ]] || node_healthy=0
      hs="$(wg show "$wg_if" latest-handshakes 2>/dev/null | awk 'NF>=2{print $2}' | sort -nr | head -n1 || true)"
      [[ "$hs" =~ ^[0-9]+$ ]] && (( hs > 0 && now - hs <= handshake_max )) || node_healthy=0
    fi
  fi

  if (( node_healthy == 1 )); then
    [[ "$(hy2_meta_get "$meta" WATCHDOG_FAILURES 2>/dev/null || true)" == "0" ]] \
      || hy2_meta_upsert "$meta" WATCHDOG_FAILURES 0
    continue
  fi

  echo "⚠️  临时节点 ${tag} 未运行或出口异常，尝试恢复" >&2
  systemctl reset-failed "${tag}.service" >/dev/null 2>&1 || true
  if systemctl restart "${tag}.service" >/dev/null 2>&1 \
    && hy2_wait_unit_and_udp_port "${tag}.service" "$port" 2 8
  then
    hy2_meta_upsert "$meta" WATCHDOG_FAILURES 0
    hy2_meta_upsert "$meta" WATCHDOG_LAST_FAILURE_EPOCH 0
    continue
  fi

  failures="$(hy2_meta_get "$meta" WATCHDOG_FAILURES 2>/dev/null || true)"
  [[ "$failures" =~ ^[0-9]+$ ]] || failures=0
  failures=$((failures + 1))
  hy2_meta_upsert "$meta" WATCHDOG_FAILURES "$failures"
  hy2_meta_upsert "$meta" WATCHDOG_LAST_FAILURE_EPOCH "$now"
  if (( failures >= 3 )); then
    echo "❌ 临时节点 ${tag} 连续恢复失败 ${failures} 次，执行清理" >&2
    FORCE=1 HY2_TEMP_LOCK_HELD=1 /usr/local/sbin/hy2_cleanup_one.sh "$tag" || rc=1
  else
    rc=1
  fi
done

HY2_TEMP_LOCK_HELD=1 /usr/local/sbin/hy2_temp_sub.sh >/dev/null 2>&1 || rc=1
exit "$rc"
EOF
  chmod 755 "${HY2_SBIN_DIR}/hy2_managed_watchdog.sh"
}

install_root_helper() {
  cat >"/root/hy2_temp_audit_all.sh" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
trap 'echo "❌ ${BASH_SOURCE[0]}:${LINENO}: ${BASH_COMMAND}" >&2' ERR

echo "=== HY2 统一审计 ==="
/usr/local/sbin/hy2_audit.sh "${@:-}"
echo
echo "=== 配额审计 ==="
/usr/local/sbin/pq_audit.sh
EOF
  chmod 755 /root/hy2_temp_audit_all.sh
}

install_systemd_units() {
  cat >/etc/systemd/system/hy2-managed-restore.service <<'EOF'
[Unit]
Description=Restore managed HY2 quota / IP-limit / temp state
After=local-fs.target systemd-tmpfiles-setup.service nftables.service
Before=multi-user.target hy2.service
ConditionPathIsDirectory=/var/lib/hy2

[Service]
Type=oneshot
ExecStartPre=/bin/mkdir -p /run/hy2
ExecStartPre=/usr/bin/systemd-tmpfiles --create /etc/tmpfiles.d/hy2.conf
ExecStart=/usr/local/sbin/hy2_restore_all.sh

[Install]
WantedBy=multi-user.target
EOF
  chmod 644 /etc/systemd/system/hy2-managed-restore.service

  cat >/etc/systemd/system/hy2-managed-watchdog.service <<'EOF'
[Unit]
Description=Repair managed HY2 listeners and fail-closed protections
After=network-online.target hy2-managed-restore.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/hy2_managed_watchdog.sh
TimeoutStartSec=300
EOF
  chmod 644 /etc/systemd/system/hy2-managed-watchdog.service

  cat >/etc/systemd/system/hy2-managed-watchdog.timer <<'EOF'
[Unit]
Description=Run HY2 managed watchdog regularly

[Timer]
OnBootSec=90s
OnUnitActiveSec=1min
AccuracySec=15s
RandomizedDelaySec=10s

[Install]
WantedBy=timers.target
EOF
  chmod 644 /etc/systemd/system/hy2-managed-watchdog.timer

  cat >/etc/systemd/system/hy2-managed-shutdown-save.service <<'EOF'
[Unit]
Description=Save managed HY2 quota usage before shutdown/reboot
After=local-fs.target hy2.service
Before=shutdown.target

[Service]
# RemainAfterExit + ExecStop is the reliable "run once on shutdown" pattern:
# the unit is started (a no-op) at boot and its ExecStop fires while the
# hysteria units and their nft counters are still present, so usage is
# persisted instead of racing the teardown.
Type=oneshot
RemainAfterExit=yes
ExecStartPre=/bin/mkdir -p /run/hy2
ExecStart=/bin/true
ExecStop=/usr/local/sbin/pq_save_state.sh
TimeoutStopSec=120

[Install]
WantedBy=multi-user.target
EOF
  chmod 644 /etc/systemd/system/hy2-managed-shutdown-save.service

  cat >/etc/systemd/system/hy2-gc.service <<'EOF'
[Unit]
Description=GC expired temporary HY2 nodes
After=local-fs.target network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/hy2_gc.sh
EOF
  chmod 644 /etc/systemd/system/hy2-gc.service

  cat >/etc/systemd/system/hy2-gc.timer <<'EOF'
[Unit]
Description=Run HY2 temp GC regularly

[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
AccuracySec=15s
RandomizedDelaySec=20s

[Install]
WantedBy=timers.target
EOF
  chmod 644 /etc/systemd/system/hy2-gc.timer

  cat >/etc/systemd/system/pq-save.service <<'EOF'
[Unit]
Description=Persist managed HY2 quota usage
After=local-fs.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/pq_save_state.sh
EOF
  chmod 644 /etc/systemd/system/pq-save.service

  cat >/etc/systemd/system/pq-save.timer <<'EOF'
[Unit]
Description=Run HY2 quota save every 5 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
EOF
  chmod 644 /etc/systemd/system/pq-save.timer

  cat >/etc/systemd/system/pq-reset.service <<'EOF'
[Unit]
Description=Reset due HY2 quota windows

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/pq_reset_due.sh
EOF
  chmod 644 /etc/systemd/system/pq-reset.service

  cat >/etc/systemd/system/pq-reset.timer <<'EOF'
[Unit]
Description=Check due HY2 quota resets

[Timer]
OnBootSec=15min
OnUnitActiveSec=1h

[Install]
WantedBy=timers.target
EOF
  chmod 644 /etc/systemd/system/pq-reset.timer

}

install_logrotate_rules() {
  cat >"$HY2_LOGROTATE" <<'EOF'
/var/log/hy2/*.log {
    daily
    rotate 7
    maxage 7
    missingok
    notifempty
    compress
    delaycompress
    dateext
    create 0640 root adm
}
EOF
  chmod 644 "$HY2_LOGROTATE"
}

enable_units() {
  systemctl daemon-reload
  systemctl enable hy2-managed-restore.service >/dev/null
  systemctl enable hy2-managed-shutdown-save.service >/dev/null
  systemctl enable hy2-gc.timer pq-save.timer pq-reset.timer hy2-managed-watchdog.timer >/dev/null
  /usr/local/sbin/hy2_restore_all.sh
  /usr/local/sbin/hy2_temp_sub.sh >/dev/null
  systemctl start hy2-gc.timer pq-save.timer pq-reset.timer hy2-managed-watchdog.timer
}

validate_generated_files() {
  local script
  for script in \
    "${HY2_LIB_DIR}/common.sh" \
    "${HY2_LIB_DIR}/quota-lib.sh" \
    "${HY2_LIB_DIR}/iplimit-lib.sh" \
    "${HY2_SBIN_DIR}/pq_add.sh" "${HY2_SBIN_DIR}/pq_del.sh" \
    "${HY2_SBIN_DIR}/pq_audit.sh" "${HY2_SBIN_DIR}/pq_save_state.sh" \
    "${HY2_SBIN_DIR}/pq_restore_all.sh" "${HY2_SBIN_DIR}/pq_reset_due.sh" \
    "${HY2_SBIN_DIR}/ip_set.sh" "${HY2_SBIN_DIR}/ip_del.sh" \
    "${HY2_SBIN_DIR}/iplimit_restore_all.sh" \
    "${HY2_SBIN_DIR}/hy2_run_temp.sh" "${HY2_SBIN_DIR}/hy2_cleanup_one.sh" \
    "${HY2_SBIN_DIR}/hy2_clear_all.sh" "${HY2_SBIN_DIR}/hy2_gc.sh" \
    "${HY2_SBIN_DIR}/hy2_restore_all.sh" "${HY2_SBIN_DIR}/hy2_audit.sh" \
    "${HY2_SBIN_DIR}/hy2_mktemp.sh" "${HY2_SBIN_DIR}/hy2_temp_sub.sh" \
    "${HY2_SBIN_DIR}/hy2_managed_watchdog.sh" "${HY2_SBIN_DIR}/hy2_doctor.sh" \
    /root/onekey_hy2_main_tls.sh /root/hy2_temp_audit_all.sh
  do
    bash -n "$script" || die "生成脚本语法检查失败：${script}"
  done
  python3 - "${HY2_LIB_DIR}/render_table.py" <<'PY'
import pathlib
import sys
path = pathlib.Path(sys.argv[1])
compile(path.read_text(encoding="utf-8"), str(path), "exec")
PY
  if command -v systemd-analyze >/dev/null 2>&1; then
    systemd-analyze verify \
      /etc/systemd/system/hy2-managed-restore.service \
      /etc/systemd/system/hy2-managed-watchdog.service \
      /etc/systemd/system/hy2-managed-watchdog.timer \
      /etc/systemd/system/hy2-managed-shutdown-save.service \
      /etc/systemd/system/hy2-gc.service /etc/systemd/system/hy2-gc.timer \
      /etc/systemd/system/pq-save.service /etc/systemd/system/pq-save.timer \
      /etc/systemd/system/pq-reset.service /etc/systemd/system/pq-reset.timer >/dev/null
  fi
}

main() {
  case "${1:-install}" in
    version|--version|-V)
      echo "HY2 five-file edition ${HY2_BUNDLE_VERSION} (${HY2_BUNDLE_DATE}), state-schema=${HY2_STATE_SCHEMA}"
      return 0
      ;;
    doctor|check)
      [[ -x /usr/local/sbin/hy2_doctor.sh ]] \
        || die "尚未安装 hy2_doctor.sh；请先运行 bash hy2.sh"
      exec /usr/local/sbin/hy2_doctor.sh "${@:2}"
      ;;
    install|"") ;;
    *) die "未知命令：${1}（支持 install、doctor、version）" ;;
  esac
  check_supported_os
  need_basic_tools
  acquire_install_locks
  begin_install_transaction
  install_dirs
  install_env_template
  install_tmpfiles
  install_common_lib
  install_render_table
  install_quota_lib
  install_iplimit_lib
  install_main_script
  install_quota_scripts
  install_iplimit_scripts
  install_hy2_management_scripts
  install_root_helper
  install_systemd_units
  install_logrotate_rules
  validate_generated_files
  enable_units
  commit_install_transaction

  cat <<'DONE'
==================================================
✅ HY2 受管系统安装完成（Debian 11+ / Ubuntu 20.04+）

已生成：
- /etc/default/hy2-main
- /root/onekey_hy2_main_tls.sh
- /root/hy2_temp_audit_all.sh

主库：
- /usr/local/lib/hy2/common.sh
- /usr/local/lib/hy2/quota-lib.sh
- /usr/local/lib/hy2/iplimit-lib.sh
- /usr/local/lib/hy2/render_table.py

管理脚本：
- /usr/local/sbin/hy2_mktemp.sh
- /usr/local/sbin/hy2_cleanup_one.sh
- /usr/local/sbin/hy2_clear_all.sh
- /usr/local/sbin/hy2_gc.sh
- /usr/local/sbin/hy2_run_temp.sh
- /usr/local/sbin/hy2_restore_all.sh
- /usr/local/sbin/hy2_audit.sh
- /usr/local/sbin/hy2_temp_sub.sh
- /usr/local/sbin/hy2_managed_watchdog.sh
- /usr/local/sbin/hy2_doctor.sh

配额与来源 IP 限制：
- /usr/local/sbin/pq_add.sh
- /usr/local/sbin/pq_del.sh
- /usr/local/sbin/pq_audit.sh
- /usr/local/sbin/pq_save_state.sh
- /usr/local/sbin/pq_restore_all.sh
- /usr/local/sbin/pq_reset_due.sh
- /usr/local/sbin/ip_set.sh
- /usr/local/sbin/ip_del.sh
- /usr/local/sbin/iplimit_restore_all.sh

下一步：
1) 编辑主配置：
   nano /etc/default/hy2-main

2) 部署主节点：
   bash /root/onekey_hy2_main_tls.sh

3) 创建临时节点：
   id=tmp4 IP_VERSION=4 IP_LIMIT=1 PQ_GIB=1 D=1200 hy2_mktemp.sh
   id=tmp6 IP_VERSION=6 IP_LIMIT=1 PQ_GIB=1 D=1200 hy2_mktemp.sh

4) WG-NAT 临时节点（先运行项目中的 vpswg.sh，再运行 natjichang.sh）：
   id=nat4 IP_VERSION=4 IP_LIMIT=1 PQ_GIB=1 D=1200 hy2_mktemp_nat.sh
   id=nat6 IP_VERSION=6 IP_LIMIT=1 PQ_GIB=1 D=1200 hy2_mktemp_nat.sh

5) 体检、审计及订阅：
   hy2_doctor.sh --strict
   hy2_audit.sh
   pq_audit.sh
   hy2_temp_sub.sh
   /root/hy2_temp_audit_all.sh

6) 清理：
   hy2_clear_all.sh
   FORCE=1 hy2_cleanup_one.sh hy2-temp-xxxx
==================================================
DONE
}

main "$@"
