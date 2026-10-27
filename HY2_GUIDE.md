# HY2 / WG-NAT / SOCKS5 安装与使用手册

本版将 `jieshu` 的节点管理和配套工具对齐到 Hysteria 2，版本 `4.0.0`。节点链接、认证、配置和计费均使用 HY2；独立 SOCKS5 仍使用 3proxy。

当前验收分支：`feature/hy2-jieshu-parity`。以下下载命令固定使用该分支，真机验收后再合并 `main`。原版 v3.3.1 的说明保留在 [README.md](README.md)。

| 文件 | 执行位置 | 用途 |
|---|---|---|
| `hy2.sh` | HY2 VPS | 主节点、临时节点、配额、IP 限制、订阅、看门狗 |
| `vpswg.sh` | HY2 VPS | WG-NAT 隧道、路由与防泄漏规则 |
| `natjichang.sh` | HY2 VPS | WG-NAT 临时节点入口与启动检查 |
| `nat.sh` | NAT 出口机 | 多 Peer 出口管理和独立 Peer 探测 |
| `traffic.py` | HY2 VPS | 可选：每日流量记录 |
| `portbw-install.sh`、`portbw.py` | 要限速的主机 | 可选：静态、自动端口限速 |
| `socks5-install.sh`、`socks5.py`、`socks5-traffic.py`、`socks5-audit.py` | SOCKS5 主机 | 可选：独立账号、配额、IP 限制和访问审计 |

## 一、准备与安装主节点

使用全新 Debian 11+ / Ubuntu 20.04+、root、systemd。HY2 使用 **UDP**：放行主节点 UDP `443`（或自定义端口）和临时节点 UDP `40000-50050`。正式证书签发还需要域名指向 VPS、TCP `80` 可达。普通 Cloudflare 橙云 HTTP 代理不能承载本项目的原生 HY2，域名使用灰云。

HY2 VPS：

```bash
apt-get update && apt-get install -y curl ca-certificates && curl -fsSL 'https://raw.githubusercontent.com/liucong552-art/ceshi/feature/hy2-jieshu-parity/hy2.sh' -o /root/hy2.sh && bash /root/hy2.sh
```

填写配置：

```bash
nano /etc/default/hy2-main
```

```ini
HY_DOMAIN=hy2.example.com
HY_IPV6_DOMAIN=
HY_LISTEN=0.0.0.0:443
ACME_EMAIL=admin@example.com
MASQ_URL=https://www.apple.com/
NODE_NAME=HY2-MAIN
ENABLE_SALAMANDER=0
HYSTERIA_VERSION=latest
HYSTERIA_UPDATE_POLICY=install-only
```

`HY_DOMAIN` 的 A 记录指向本机。需要 IPv6 入站临时节点时，再填写 `HY_IPV6_DOMAIN` 并配置 AAAA 记录。`HY_LISTEN` 保持 IPv4，端口可改为 1–65535 内未被临时节点使用的端口；修改后同步调整防火墙和客户端。

```bash
bash /root/onekey_hy2_main_tls.sh
hy2_doctor.sh --strict
cat /root/hy2_main_url.txt
cat /root/hy2_main_subscription_base64.txt
```

重复部署保留主节点密码。显式重置密码或升级核心：

```bash
ROTATE_CREDENTIALS=1 bash /root/onekey_hy2_main_tls.sh
HYSTERIA_FORCE_UPDATE=1 HYSTERIA_VERSION=v2.13.0 bash /root/onekey_hy2_main_tls.sh
```

重置密码会使原链接失效。普通重复部署不自动升级核心。可在配置中设置 `HYSTERIA_BINARY_SHA256` 固定核心哈希。

## 二、普通和 WG-NAT 临时节点

两种创建命令参数相同：普通节点用 `hy2_mktemp.sh`，WG-NAT 节点用 `hy2_mktemp_nat.sh`。WG-NAT 需先完成第五节。`D` 为有效秒数，`PQ_GIB` 为 UDP 入站端口上传与下载合计的流量配额，`IP_LIMIT` 为活跃来源 IP 数。

```bash
# 30 分钟、最多 3 个 IP、双向 1 GiB
MINUTES=30; IP_VERSION=4 IP_LIMIT=3 PQ_GIB=1 D=$((MINUTES*60)) hy2_mktemp.sh
MINUTES=30; IP_VERSION=4 IP_LIMIT=3 PQ_GIB=1 D=$((MINUTES*60)) hy2_mktemp_nat.sh

# 2 小时、最多 3 个 IP、双向 1 GiB
HOURS=2; IP_VERSION=4 IP_LIMIT=3 PQ_GIB=1 D=$((HOURS*3600)) hy2_mktemp.sh
HOURS=2; IP_VERSION=4 IP_LIMIT=3 PQ_GIB=1 D=$((HOURS*3600)) hy2_mktemp_nat.sh

# 7 天、最多 3 个 IP、双向 50 GiB
DAYS=7; IP_VERSION=4 IP_LIMIT=3 PQ_GIB=50 D=$((DAYS*86400)) hy2_mktemp.sh
DAYS=7; IP_VERSION=4 IP_LIMIT=3 PQ_GIB=50 D=$((DAYS*86400)) hy2_mktemp_nat.sh

# 固定端口 40001、1 小时
HOURS=1; IP_VERSION=4 PORT_START=40001 PORT_END=40001 IP_LIMIT=3 PQ_GIB=1 D=$((HOURS*3600)) hy2_mktemp.sh
HOURS=1; IP_VERSION=4 PORT_START=40001 PORT_END=40001 IP_LIMIT=3 PQ_GIB=1 D=$((HOURS*3600)) hy2_mktemp_nat.sh

# 可选自定义名称
id=user01 IP_VERSION=4 IP_LIMIT=3 PQ_GIB=50 D=604800 hy2_mktemp.sh
```

IPv6 入站将 `IP_VERSION=4` 改成 `6`；WG-NAT 出口仍是 NAT 机 IPv4。不限 IP 用 `IP_LIMIT=0`，不限流量则省略 `PQ_GIB`。即使不限 IP，地址族隔离仍启用。IP 限制按活跃源地址计算，QUIC 迁移至新源 IP 也会参与计数。

## 三、查询、删除与访问日志

```bash
hy2_audit.sh
hy2_doctor.sh --strict
hy2_temp_sub.sh
```

按端口查询主节点或临时节点链接：

```bash
PORT=40001 bash -c 'set -euo pipefail; source /usr/local/lib/hy2/common.sh; if [[ "$PORT" == "$(hy2_main_port)" ]]; then cat /root/hy2_main_url.txt; else M=$(hy2_temp_meta_by_port "$PORT"); [[ -n "$M" ]] || { echo "未找到该端口" >&2; exit 1; }; cat "${M%.env}.url"; fi'
```

按端口删除临时节点，不删除主节点：

```bash
PORT=40001 bash -c 'set -euo pipefail; source /usr/local/lib/hy2/common.sh; M=$(hy2_temp_meta_by_port "$PORT"); [[ -n "$M" ]] || { echo "未找到临时节点" >&2; exit 1; }; TAG=${M##*/}; FORCE=1 hy2_cleanup_one.sh "${TAG%.env}"'
```

清空所有临时节点：

```bash
hy2_clear_all.sh
```

主节点、临时节点均开启 HY2 debug 访问日志，包含来源和 TCP/UDP 请求目标，写入各自 systemd journal。以下查询默认北京时间：

```bash
# 主节点
TZ=Asia/Shanghai journalctl -u hy2.service --since '24 hours ago' --no-pager -o short-iso

# 某个临时节点的访问日志，修改端口即可
PORT=40001 bash -c 'set -euo pipefail; source /usr/local/lib/hy2/common.sh; M=$(hy2_temp_meta_by_port "$PORT"); [[ -n "$M" ]] || { echo "未找到临时节点" >&2; exit 1; }; TAG=${M##*/}; TZ=Asia/Shanghai journalctl -u "${TAG%.env}.service" --since "24 hours ago" --no-pager -o short-iso'
```

历史保存期限取决于本机 journald 的容量与轮替设置。日志可显示客户端请求的域名或 IP；不会把只以 IP 发起的请求自动还原成网站域名。

## 四、配额、IP 限制和每日流量

```bash
pq_add.sh 40001 50      # 设置双向总流量 50 GiB
pq_audit.sh
pq_del.sh 40001         # 仅删除配额
ip_set.sh 40001 3 600   # 最多 3 个活跃 IP，槽位保持 600 秒
ip_del.sh 40001         # 仅删除 IP 数量限制，保留地址族隔离
```

按端口查看活跃来源 IP：

```bash
PORT=40001 bash -c 'set -euo pipefail; source /usr/local/lib/hy2/common.sh; source /usr/local/lib/hy2/iplimit-lib.sh; hy2_il_active_ips "$PORT"'
```

配额按 HY2 的外层 UDP 数据包计数，包含 QUIC/TLS 协议开销和重传；即使代理内部是 TCP 请求，也计入此 UDP 端口。主节点和超过 30 天的临时节点保留原有配额重置逻辑，以 `pq_audit.sh` 中的时间为准。

可选的每日流量独立于配额，也支持无配额节点。安装后才开始记录，北京时间分日，保留今天和前 29 天；端口复用或更换密码不继承旧用户记录，到期或删除节点后下一次采集清理对应历史。它不重启 HY2，不修改配额。

```bash
curl -fsSL 'https://raw.githubusercontent.com/liucong552-art/ceshi/feature/hy2-jieshu-parity/traffic.py' -o /root/hy2_traffic.py && python3 /root/hy2_traffic.py --install
hy2_traffic
hy2_traffic 40001
hy2_traffic main
hy2_traffic user01
hy2_traffic 40001 --json
```

`UP`、`DOWN`、`TOTAL` 是当天流量；指定端口、ID 或 `main` 才展开历史。独立统计表位于 `inet hy2_daily_traffic`，数据位于 `/var/lib/hy2/traffic/daily.json`。跨午夜流量按采样时间归日，断电可能丢失尚未保存的增量。

## 五、WG-NAT 配置

NAT 出口机执行：

```bash
curl -fsSL 'https://raw.githubusercontent.com/liucong552-art/ceshi/feature/hy2-jieshu-parity/nat.sh' -o /root/nat.sh && bash /root/nat.sh init
```

无法识别公网网卡时，显式使用 `WAN_IF=eth0 bash /root/nat.sh init`。HY2 VPS 执行：

```bash
curl -fsSL 'https://raw.githubusercontent.com/liucong552-art/ceshi/feature/hy2-jieshu-parity/vpswg.sh' -o /root/vpswg.sh && bash /root/vpswg.sh
cat /etc/wireguard/wg-nat.pub
```

回到 NAT 机添加 VPS：

```bash
bash /root/nat.sh add hy2 hy2.example.com '替换成VPS的WireGuard公钥'
```

原样执行 NAT 机输出的回填命令，例如在 HY2 VPS：

```bash
/usr/local/sbin/wg_nat_set_peer.sh '替换成NAT机的WireGuard公钥' '10.66.66.1/32'
/usr/local/sbin/wg_nat_healthcheck.sh
```

出现 `OK EXIT_IP=...` 后，在 HY2 VPS 安装创建入口：

```bash
curl -fsSL 'https://raw.githubusercontent.com/liucong552-art/ceshi/feature/hy2-jieshu-parity/natjichang.sh' -o /root/natjichang.sh && bash /root/natjichang.sh && bash /root/natjichang.sh check
```

之后使用第二节的 `hy2_mktemp_nat.sh` 命令。不要跳过健康检查。此实现通过 `direct.bindDevice` 和 OIF 策略路由绑定 WG 出口；看门狗使用设备路由检查，不能拿 Xray 的 fwmark 检查直接替代。

NAT 机常用操作：

```bash
bash /root/nat.sh list
bash /root/nat.sh status
bash /root/nat.sh del hy2
bash /root/nat.sh probe-status hy2
bash /root/nat.sh probe-once hy2
bash /root/nat.sh probe-enable hy2
bash /root/nat.sh probe-disable hy2
```

新增或更新 Peer 后自动启用该 Peer 的数据探测；旧 Peer 可单独启用。探测开关不重启 WG，不影响其他 Peer。`init/add/del` 涉及 WG 配置变更，只在测试机或维护窗口执行。

## 六、故障保护、更新与验收

V4 批量查询只复用一轮内、短时间内已验证的健康状态；缺失、损坏、变化或中断提交均回到原有即时查询。修复后使缓存失效，不跨看门狗运行保存内核状态。不降低看门狗、GC、配额保存或重置频率，不放宽配额和 IP 限制。

节点真实恢复失败 3 次后，开始持久的 72 小时宽限期。期间保留节点和原到期时间，继续恢复；保护缺失或 WG 不健康时关闭监听。只有确认防护和实际出口均健康才清除故障计时；72 小时后仍失败才清理，冷启动前 300 秒暂缓故障删除。正常到期仍按原到期时间清理。

```bash
bash /root/hy2.sh version
hy2_restore_all.sh
systemctl start hy2-managed-watchdog.service
```

更新管理脚本使用第一节下载命令，不必重新创建节点。WG 脚本和可选组件独立更新。当前版本仍面向全新 HY2 环境；不要在现有 VLESS 主机上直接叠加安装，两套安装器使用了同名的 `pq_*`、`ip_*` 命令和部分 systemd 单元。

已完成的本地验证和未完成项目见 [tests/VALIDATION.md](tests/VALIDATION.md)。正式使用前，在全新 HY2 VPS 与 NAT 测试机确认：客户端实际连接、IPv4/IPv6 隔离、超额拒绝、IP 槽位、到期清理、WG 断开不回落、nft 表丢失后的恢复、两机重启恢复、证书续期。这里不把 VLESS 的旧真机测量当作 HY2 验收结果。

## 七、可选：静态和自动端口限速（portbw）

此组件与 HY2 节点管理、流量统计、WG-NAT 相互独立：**按本机监听端口限速，不绑定节点配置**。TCP 和 UDP 使用同一个端口时，二者在**上传方向合计使用一个额度**，在**下载方向合计使用另一个额度**；IPv4 和 IPv6 也共用各自方向的同一额度，不是每种协议分别限速。实际速度可能因协议开销、丢包和瞬时突发略有波动。

#### 一键查询全部端口限速

```bash
portbw report           # 一键查看所有静态、自动限速端口（中文单表格，每个端口一行）
```

查看当前上传/下载、正常速度、自动降速值、触发阈值、持续时间、滚动保护、冷却时间及规则状态，单位为 **Mbps**。静态端口不适用的参数显示 `-`；终端较窄时优先保留限速参数。此命令**只读**，不会改变限速或保护计时。安装 `portbw` 后即可使用。

#### 安装 / 更新 portbw（需要同时下载两个文件）

**干净服务器首次安装（正式版）：** 在要限速的 Debian/Ubuntu 服务器上以 **root** 逐行执行以下完整命令。安装器不自动下载配套 Python 文件，也不自动执行 apt，因此下面的依赖安装、两次下载、只读预检和实际安装都要保留；**不要停在 curl 或 check 那一步**。

```bash
apt-get update && apt-get install -y curl ca-certificates python3 iproute2 nftables util-linux coreutils procps
curl -fsSL 'https://raw.githubusercontent.com/liucong552-art/ceshi/feature/hy2-jieshu-parity/portbw-install.sh' -o /root/portbw-install.sh
curl -fsSL 'https://raw.githubusercontent.com/liucong552-art/ceshi/feature/hy2-jieshu-parity/portbw.py' -o /root/portbw.py
bash /root/portbw-install.sh check
bash /root/portbw-install.sh install
bash /root/portbw-install.sh verify
portbw report
portbw audit
```

`check` 是只读预检，必须出现 `CHECK PASS`；`install` **才执行实际安装**，须出现 `INSTALL PASS` 和 `VERIFY PASS`；完成后 `command -v portbw` 应显示 `/usr/local/sbin/portbw`，Watchdog 应为 `active`。**如果预检失败，先排查，不要强行安装。** 如需手动指定物理网卡，可先运行 `ip -4 route get 1.1.1.1` 查看，再在 `check` 和 `install` 后分别加 `--iface eth0`（`eth0` 替换为实际的非隧道物理网卡）。首次安装不创建端口规则，请使用后面的 `portbw set` 或 `portbw auto set` 明确设置端口。

**升级已由本系列通用安装器管理的已知 v1/v2/候选版：** 先按上述两条 `curl` 重新下载同一版安装器和源码，先记录当前的 `portbw report` 与 `portbw audit`，保存可恢复的 VPS 快照，再在维护窗口执行（不要重新执行首次安装 `install`）：

```bash
bash /root/portbw-install.sh preflight-upgrade
bash /root/portbw-install.sh upgrade
bash /root/portbw-install.sh verify
portbw report
portbw audit
```

首次安装的 `check` 要求干净环境，已有受管理安装要用 **`preflight-upgrade`**。升级工具只接受有 `/var/lib/portbw-resident-release/current.env` 管理记录，且已安装源码 SHA 与记录一致的受支持旧版；未知来源的旧版即使已有端口配置也**不允许直接覆盖或伪造管理记录**。安装器会持久保存备份，但不能取代 VPS 快照。不要执行 `nft flush ruleset`、`tc qdisc del ... root`，不要停止其他业务的 nft/tc 系统规则。
#### 静态限速：设置、修改、取消

```bash
portbw set 40001 10 20  # 40001：TCP+UDP 合计上传 10 Mbps / 合计下载 20 Mbps
portbw up 40001 15      # 上传上限改为 15 Mbps
portbw down 40001 30    # 下载上限改为 30 Mbps（直接限速，不是触发阈值）
portbw down 40001 0     # 仅关闭下载限速；上传额度不变
portbw show 40001       # 查看单个端口的详细配置和内核状态
portbw audit            # 专门审计所有限速规则的 nft/tc 生效情况
portbw del 40001        # 仅取消端口限速，不删除 HY2 节点
```

#### 自动限速：双向独立触发与恢复（2026-10-18 常驻版）

以下以端口 `40001` 为例：平时上下行各 30 Mbps；达到本方向上限的 100% 并持续 5 秒后，该方向降至 15 Mbps；滚动保护至少 120 秒（2 分钟），持续高负载会续期；恢复后无冷却。**下列 Bash 数组含中文注释，整段复制即可执行**（不是把中文括号插在命令参数之间）。

```bash
args=(
  40001              # 本机监听端口：40001（HY2 可按实际情况改为 443）
  --up 30            # 正常上传上限：30 Mbps
  --down 30          # 正常下载上限：30 Mbps
  --trigger 100      # 触发阈值：基础速度的 100%，即上传或下载达到 30 Mbps
  --after 5s         # 达到对应方向阈值并持续 5 秒后，开始降速
  --auto-up 15       # 上传方向触发后：上传降为 15 Mbps
  --auto-down 15     # 下载方向触发后：下载降为 15 Mbps
  --hold 120s        # 滚动保护至少 120 秒，即 2 分钟；持续高负载会续期
  --cooldown 0s      # 恢复基础速度后零冷却，立即允许再次检测违规
)

portbw auto set "${args[@]}"  # 应用自动限速；上传和下载分别监测、分别触发
portbw auto status 40001     # 查看自动限速、采样和滚动保护状态
```

需要**手动停止自动限速**时，另行执行（不要与上面的设置命令一起执行）：

```bash
portbw auto off 40001        # 关闭自动模式，恢复基础上传/下载限速
```

`--trigger 100` 表示**基础速度的 100%**，不是整台服务器总带宽的 100%，也不是 100 Mbps。新版对接近阈值的连续样本使用 85% **连续性判定下界**，但最终持续窗口仍要求达到用户设定的 100% 整体触发门槛，**并非降到 85% 即触发**；不满足条件不会强制降速。`--after 5s` 是在符合连续和窗口条件后触发。`--hold 120s` 是**至少 2 分钟**，持续接近降速上限或持续超限丢包时可滚动续期；`--cooldown 0s` 取消恢复后的冷却，再次降速仍需重新满足触发条件。

自动模式中只有 `--cooldown` 可以省略（省略默认 `60s`，**零冷却必须明确写 `--cooldown 0s`**），其他参数均为必填；上下行基础速度必须分别大于降速值，且均大于 0。`--trigger` 必须填写，单位为百分比（可填 100），不是 Mbps。`portbw down 40001 30` 只会直接改成**静态下载限速 30 Mbps**，不会设置自动触发门槛，且手动修改会关闭该端口的自动模式。

`0` 表示对应方向**不限速**；两个方向都无需限速时用 `portbw del <端口>`。限速覆盖本机进入/离开的 TCP、UDP 流量，不覆盖任意 WireGuard/NAT `FORWARD` 转发流量；上传由 nftables + tc 执行限速，下载使用 tc egress 的单个共享 policer，nftables 负责计数（避免 OUTPUT 超限导致 UDP 程序 `Operation not permitted`）。`--nft-only` 不支持下载限速，请使用默认的 nft+tc 模式以获得完整双向限速。

#### 查询自动限速日志（默认北京时间）

以下查询命令**默认按北京时间（UTC+8）输出**，无需修改 VPS 的系统时区。命令只读取已有 systemd 日志，不会改变限速设置。日志时间与服务器本身使用 UTC 还是北京时间无关。

查看最近24小时**所有端口**的自动降速、恢复基础速度和故障记录：

```bash
TZ=Asia/Shanghai journalctl -u portbw-watch.service \
  --since '24 hours ago' --no-pager -o short-iso \
  | grep -E '自动降速|恢复基础速度|失败|Error|Traceback' \
  || echo '最近24小时没有匹配的降速、恢复或报错日志'
```

查看最近24小时**指定端口**的自动降速与恢复记录（修改 `PORT=40002` 即可）：

```bash
PORT=40002
TZ=Asia/Shanghai journalctl -u portbw-watch.service \
  --since '24 hours ago' --no-pager -o short-iso \
  | grep -E "${PORT} (up|down): (自动降速|恢复基础速度)" \
  || echo "最近24小时端口 ${PORT} 没有降速或恢复记录"
```

查看**指定端口的全部尚存历史记录**，不局限于最近24小时：

```bash
PORT=40002
TZ=Asia/Shanghai journalctl -u portbw-watch.service \
  --no-pager -o short-iso \
  | grep -E "${PORT} (up|down): (自动降速|恢复基础速度)" \
  || echo "端口 ${PORT} 没有尚存的降速或恢复历史记录"
```

其中 `up` 表示上传方向，`down` 表示下载方向。日志可能包含参数修改**之前**的历史记录；例如旧版显示 `自动降速 15 Mbps / 恢复基础速度 30 Mbps`，不代表现在仍使用旧限速。**当前配置请以 `portbw report` 和 `portbw audit` 为准。** 没有触发日志，也不代表基础限速失效；历史保存时长受 `systemd-journald` 的自动轮替、磁盘容量和保留策略影响。

**其他旧仓库/无管理记录版本的迁移（不能直接 `install`/`upgrade`）：** 特别是 `zuizhongheji` 等独立旧版及使用旧 tc flower 槽位的机器，先记录每个端口的**基础上/下行、降速上/下行、触发百分比、持续秒数、滚动保护、冷却**，执行 `portbw report` 与 `portbw audit` 并制作 VPS 快照。只可在维护窗口按照确切旧版校验过的迁移工具或逐端口安全删除方法移除旧限速，再安装此通用版并重新添加规则；在删除到重建期间可能暂时失去限速保护。不要把普通安装器用于未知来源旧版本，更不要手动清空整个 nftables ruleset、网卡 root qdisc 或其他业务规则。



## 八、可选：独立 SOCKS5 代理（3proxy）

在**单独的家宽主机**上用 `root` 安装，与 HY2 VPS 分开。Debian 12 已完成真机验收；Debian 13 和 Ubuntu 22.04/24.04 LTS 在代码依赖上可能兼容，但尚未完成真机验收。

### 1. 安装 / 更新

在家宽主机执行（安装新版不会主动清空已有账号；更新时可能短暂影响连接）：

```bash
apt-get update && apt-get install -y curl ca-certificates
AUTO_DEPS=1 bash <(curl -fsSL 'https://raw.githubusercontent.com/liucong552-art/ceshi/feature/hy2-jieshu-parity/socks5-install.sh') install
```

主机位于路由器 NAT 后面时，可以在 `install` 后加 `--host proxy.example.com`（换成自己的公网域名/IP）。必要时加 `--wan-if eth0` 指定物理出口网卡。

### 2. 创建临时账号（最后一行直接输出 SOCKS5 连接链接）

命令直接修改数字：`IP_LIMIT`=最多来源 IP 数量；`IP_STICKY_SECONDS`=来源 IP 闲置占位时长（秒）；`PQ_GIB`=上传+下载合计总配额（GiB）。持续活动的 IP 会刷新占位时间，超时后释放名额，不是强制断开已建立连接。

#### 按分钟

创建有效期 **30 分钟**、最多 **1 个来源 IP**、**IP 占位 60 秒（1 分钟）**、总配额 **1 GiB** 的账号：

```bash
MINUTES=30; IP_LIMIT=1 IP_STICKY_SECONDS=60 PQ_GIB=1 D=$((MINUTES*60)) socks5 add
```

#### 按小时

创建有效期 **2 小时**、最多 **2 个来源 IP**、**IP 占位 300 秒（5 分钟）**、总配额 **5 GiB** 的账号：

```bash
HOURS=2; IP_LIMIT=2 IP_STICKY_SECONDS=300 PQ_GIB=5 D=$((HOURS*3600)) socks5 add
```

#### 按天

创建有效期 **7 天**、最多 **3 个来源 IP**、**IP 占位 600 秒（10 分钟）**、总配额 **50 GiB** 的账号：

```bash
DAYS=7; IP_LIMIT=3 IP_STICKY_SECONDS=600 PQ_GIB=50 D=$((DAYS*86400)) socks5 add
```

#### 指定固定端口

创建有效期 **1 小时**、最多 **1 个来源 IP**、**IP 占位 60 秒**、总配额 **1 GiB**、监听端口 `41004` 的账号：

```bash
HOURS=1; IP_LIMIT=1 IP_STICKY_SECONDS=60 PQ_GIB=1 PORT=41004 D=$((HOURS*3600)) socks5 add
```

省略 `PORT` 自动选择本机空闲端口；`IP_LIMIT=0` 不限制来源 IP；删除 `PQ_GIB=...` 表示不限总配额。创建时可选 `id=myproxy` 自定义内部 ID，但**日常命令直接使用端口即可，不用记 ID**。

家宽主机在路由器后面时，需要把公网 **TCP** 端口映射到本机 SOCKS5 监听端口。若外部端口与本地端口不同，例如外部 `51004` 映射到本机 `41004`：

```bash
HOURS=1; IP_LIMIT=1 IP_STICKY_SECONDS=60 PORT=41004 D=$((HOURS*3600)) socks5 add --host proxy.example.com --public-port 51004
```

**重要：下面管理命令使用的是本机监听端口 `41004`，不是路由器映射的外部端口 `51004`。**

### 3. 按端口查看账号、剩余流量和连接链接

日常查看使用 `socks5 list` 即可，一张表看清端口、流量余额、IP 占用、剩余有效期和防护状态。

```bash
socks5 list                         # 查看全部账号的对齐表格（含实时剩余流量及 IP 占用）
socks5 quota 41004                  # 查看端口 41004 的总配额、已用、剩余及精确字节数
socks5 link 41004                   # 获取端口 41004 的完整 socks5:// 连接链接
socks5 show 41004 --credentials     # 查看账号详细信息、用户名和密码（JSON）
socks5 status                       # 查看出口网卡、路由与代理服务状态
socks5 watch                        # 手动检查并恢复防护规则或异常服务
```

表格说明：`LIMIT`=总配额，`USED`=已用，**`LEFT`=实时剩余额度**，`USE%`=使用比例，`TTL`=剩余有效期，`EXPIRE(BJ)`=北京时间到期日期，`IP占用`=当前有效占位 IP 数/允许上限（例如 **2/3** 表示最多允许 3 个来源 IP，目前占用了 2 个名额），`STICKY`=IP 停止活动后的占位释放秒数。若实时 nftables 计数无法读取，`LEFT` 会显示 **未知**，不会把缓存值误当实时余额。**IP占用统计的是仍在占位期内的不同来源 IP，不是实时建立的 TCP 连接数；读取失败显示 ?/上限，而不是错误显示为零。** 窄终端会隐藏部分列，精确字节数请用 `socks5 quota 41004`。

`socks5-traffic` 每日统计**不等于**总流量配额；剩余可用配额以 `socks5 quota` 查询为准。

### 4. 按端口修改、删除账号

```bash
socks5 ip-show 41004                # 查看当前占位数量、具体来源 IP 和剩余占位时间
socks5 ip-set 41004 2 60            # 最多 2 个来源 IP，闲置占位 60 秒（重置已有占位记录）
socks5 ip-del 41004                 # 取消来源 IP 数量限制
socks5 pq-set 41004 5 --confirm-reset  # 重新设置 5 GiB 总配额，已用量从零开始
socks5 pq-del 41004                 # 取消总配额（不限流量）
socks5 del 41004                    # 删除本机端口 41004 对应的 SOCKS5 账号
```

为兼容旧版，也可以继续输入创建时返回的账号 ID。

### 5. 可选：每日流量历史（最近 30 天）

```bash
socks5-traffic --install            # 首次开启或更新每日流量采集
socks5-traffic                      # 查看所有端口今天的上传/下载用量
socks5-traffic 41004                # 查看端口 41004 最近 30 天的每日用量
```

每日统计按约每分钟采样的增量记录；不能把每日流量直接当作实时剩余配额。

### 6. 可选：SOCKS5 端口限速

SOCKS5 与 HY2 使用**同一套独立 `portbw` 限速方法**，无需重复设置命令。请在**实际运行 SOCKS5 的家宽主机**按照前文“可选：TCP+UDP 同端口共享限速（portbw）”安装并设置；把示例里的 `40001` 改成 SOCKS5 的**本机监听端口**（如 `41004`），即可使用相同的静态限速或 **30→15 Mbps、100% 持续 5 秒、滚动保护 120 秒、零冷却**的自动限速。查询所有端口时，同样使用前文的 `portbw report`。

**注意：** 如果路由器把公网端口 `51004` 映射到家宽主机的 `41004`，限速应设置在本机端口 **`41004`**，不是公网端口 `51004`。SOCKS5 仅支持 TCP CONNECT，不支持 UDP ASSOCIATE；`portbw` 独立按本机端口管理 TCP+UDP。SOCKS5 账号创建、到期、删除均不会自动增删 `portbw` 规则；升级 `portbw` 也不会修改 SOCKS5 账号、配额和服务。已安装非本系列管理的旧版 `portbw` 的主机先参考前文“其他旧仓库/无管理记录版本的迁移”安全迁移；已有受管理版本按 `preflight-upgrade` / `upgrade` 操作。

### 7. SOCKS5 访问日志审计（按本机端口查询）

本功能在 3proxy 连接结束并记账时写入各端口独立的 systemd journal。**原有四项报表保持不变**；新增两个只读查询，不会修改代理、连接限制、流量配额、IP 防护、portbw 限速或 HY2/WG 配置。

```bash
# 原有四项：完整主机域名 Top 20、来源 IP 历史 Top 20、同一分钟不同来源 IP、当前 TCP 连接
socks5 access 40002 7

# 新增：列出全部成功连接的原始主机域名及次数，不限制为前 20 名
socks5 access-domains 40002 7

# 新增：所有非零错误码的汇总次数，以及最近 50 条错误的时间/IP/目标明细
socks5 access-errors 40002 7

# 只改变错误明细显示数量；错误码统计依然覆盖全部查询时段
socks5 access-errors 40002 7 --limit 200

# 显示全部非零错误连接明细（高流量端口可能输出很长）
socks5 access-errors 40002 7 --all

# 原有的服务与防护审计命令依然可用，不等于访问日志审计
socks5 audit 40002
```

上面 `40002` 是 **SOCKS5 本机监听端口**，不是路由器映射的公网端口；`7` 是过去 7 天，可设置为 **1～90 天**。日志时间均按**北京时间**显示。原四项只对成功连接（3proxy 返回码 `00000`）计数；域名查询只对客户端明确使用主机域名的成功代理连接计数，不合并 CNN、Google 等子域名，也不自动排除 CDN/广告域名。错误详情独立报告，不能仅凭错误码或域名推断租客恶意行为或侵权行为。

只有日志开启之后、仍保存在 journald 中且已经记录的连接才能查到；未保存的历史无法补查。3proxy 日志通常在连接结束时出现，因此记录次数不是打开网页的次数；直接通过 IP 发起的连接无法反推出域名。

**更新现有 SOCKS5 管理器（建议先做 VPS 快照；升级期间已有 3proxy 实例可能短暂重启）：**

```bash
AUTO_DEPS=1 bash <(curl -fsSL 'https://raw.githubusercontent.com/liucong552-art/ceshi/feature/hy2-jieshu-parity/socks5-install.sh') update
```

初次部署请使用本节“1. 安装 / 更新”的 `install` 命令。日志采集使用每个 SOCKS5 实例自己的 journal，不改变其他端口的记录；`socks5-traffic` 每日统计和 `portbw` 限速按原章节独立管理。


## 九、临时封禁 / 解封单个 HY2 端口（删除后无残留）

在**运行该 HY2 节点的服务器**（例如 `HY2 VPS`）以 root 身份执行。**下面三条命令互相独立，每次只改各自的 `PORT=40022` 数字即可。** 不需要安装脚本，也不创建配置文件。

本功能同时封禁**外部 IPv4 / IPv6 的 TCP / UDP 入站**（四种组合）；`lo` 回环流量不拦截。采用每个端口独立的 `inet pause_hy2_端口` 临时表，不停止或重启 Hysteria 2、WG、systemd，不修改 UUID、配额、IP_LIMIT、已有流量统计或其他端口的规则。**仅处理发往本机的 INPUT 流量**，不处理 FORWARD 转发端口或从本机主动发起的出站连接。

### 1. 临时封禁（只改 PORT）

```bash
(
PORT=40022
[[ "$PORT" =~ ^[1-9][0-9]{0,4}$ ]] && (( PORT <= 65535 )) || { echo "❌ 端口无效"; exit 1; }
T="pause_hy2_$PORT"
if ! nft list table inet "$T" >/dev/null 2>&1; then
  nft -f - >/dev/null 2>&1 <<EOF
add table inet $T
add chain inet $T input { type filter hook input priority -20; policy accept; }
add rule inet $T input iifname != "lo" tcp dport $PORT reject with tcp reset
add rule inet $T input iifname != "lo" udp dport $PORT drop
EOF
fi
R=$(nft list chain inet "$T" input 2>/dev/null) || R=""
if grep -Fq "iifname != \"lo\" tcp dport $PORT reject with tcp reset" <<<"$R" &&
   grep -Fq "iifname != \"lo\" udp dport $PORT drop" <<<"$R"; then
  echo "✅ $PORT 已临时封禁（IPv4 + IPv6 + TCP + UDP）"
else
  echo "❌ $PORT 封禁失败或存在旧版规则；如存在旧版临时表，请先执行第 2 条解封，再重试"
  exit 1
fi
)
```

### 2. 解除封禁（只改 PORT，删除本功能的整张表）

```bash
(
PORT=40022
[[ "$PORT" =~ ^[1-9][0-9]{0,4}$ ]] && (( PORT <= 65535 )) || { echo "❌ 端口无效"; exit 1; }
T="pause_hy2_$PORT"
if nft list table inet "$T" >/dev/null 2>&1; then
  nft delete table inet "$T" >/dev/null 2>&1
fi
if nft list table inet "$T" >/dev/null 2>&1; then
  echo "❌ $PORT 解封失败，临时封禁表仍存在"
  exit 1
else
  echo "✅ $PORT 已解封，临时表、链、规则均已删除"
fi
)
```

### 3. 查询封禁状态（只改 PORT，只输出中文结果）

```bash
(
PORT=40022
[[ "$PORT" =~ ^[1-9][0-9]{0,4}$ ]] && (( PORT <= 65535 )) || { echo "❌ 端口无效"; exit 1; }
T="pause_hy2_$PORT"
R=$(nft list chain inet "$T" input 2>/dev/null) || R=""
if grep -Fq "iifname != \"lo\" tcp dport $PORT reject with tcp reset" <<<"$R" &&
   grep -Fq "iifname != \"lo\" udp dport $PORT drop" <<<"$R"; then
  echo "✅ $PORT 已封禁（IPv4 + IPv6 + TCP + UDP）"
else
  echo "❌ $PORT 未完整封禁（或没有临时封禁规则）"
fi
)
```

**无残留说明：** 解除封禁会删除这个端口专属的 nftables 表以及其中全部链和规则，不产生本功能的配置文件、定时任务或后台进程。命令本身仍可能留在 Bash history；已有 TCP 连接在内核中的状态、系统日志不会因此自动清除。

**注意：** 本功能只检查其专属临时规则，不能替代外部网络连通性测试。解除后若还有其他防火墙规则，端口仍可能无法访问；封禁时服务显示 `active`、端口显示 `LISTEN` 是正常的。临时规则不设到期时间，通常在重启或防火墙规则重载后失效。若端口存在之前创建的旧版 IPv4-only 临时表，第 1 条不会擅自覆盖；先用第 2 条删除旧表，再执行第 1 条重新封禁即可。

## 十、指定端口按来源 IP 永久拉黑 / 解封（支持批量）

在**需要封禁的 Debian 12 服务器**上以 root 身份执行。**不需要首次初始化或安装**。每次只改命令最前面的 `P=40001`（端口）和 `IPS="..."`（来源 IPv4，多 IP 用空格分隔）。只封一个 IP 时也使用同一命令，例如 `IPS="1.2.3.4"`。依赖系统现有 `nft`、`python3`、`flock` 和 `systemctl`。

独立使用 `inet hy2_ipban` 防火墙表，针对指定**来源 IPv4 + 目标端口**在 PREROUTING 阶段丢弃 TCP / UDP 数据包（在 DNAT 前匹配）。**它是服务器级别的规则，不是按 GitHub 仓库或 HY2 用户隔离的规则**，同一服务器上其他服务只要匹配此 IP/端口也会被拦截。IPv6 不受此规则影响。操作前请确认目标端口不是远程 SSH 管理端口。

### 1. 永久拉黑（单 IP / 批量，一行命令）

```bash
P=40001 IPS="1.2.3.4 5.6.7.8 9.10.11.12" bash -c 'set -euo pipefail; (( EUID == 0 )) || { echo "需 root 权限"; exit 1; }; [[ $P =~ ^[1-9][0-9]{0,4}$ ]] && (( P <= 65535 )) || { echo "端口无效"; exit 1; }; read -r -a A <<< "$IPS"; (( ${#A[@]} > 0 )) || { echo "IP 列表为空"; exit 1; }; python3 -c "import ipaddress,sys; [ipaddress.IPv4Address(x) for x in sys.argv[1:]]" "${A[@]}"; exec 9>/run/hy2-ipban.lock; flock -x 9; if ! nft list table inet hy2_ipban >/dev/null 2>&1; then printf "%s\n" "add table inet hy2_ipban" "add chain inet hy2_ipban pre { type filter hook prerouting priority -150; policy accept; }" "add set inet hy2_ipban banned { type ipv4_addr . inet_service; }" "add rule inet hy2_ipban pre meta l4proto { tcp, udp } ip saddr . th dport @banned drop" | nft -f -; fi; for IP in "${A[@]}"; do nft get element inet hy2_ipban banned "{ $IP . $P }" >/dev/null 2>&1 || nft add element inet hy2_ipban banned "{ $IP . $P }"; done; umask 077; nft list table inet hy2_ipban >/etc/hy2-ipban.nft.tmp; mv -f /etc/hy2-ipban.nft.tmp /etc/hy2-ipban.nft; printf "%s\n" "[Unit]" "Description=HY2 IP port blacklist" "After=nftables.service" "Before=network.target" "[Service]" "Type=oneshot" "ExecStart=/usr/sbin/nft -f /etc/hy2-ipban.nft" "[Install]" "WantedBy=multi-user.target" >/etc/systemd/system/hy2-ipban.service; systemctl daemon-reload; systemctl enable hy2-ipban.service >/dev/null; echo "已封锁端口 $P：${A[*]}"'
```

重复运行可以追加新 IP，不会主动清除已有黑名单；重复添加已有 IP 不产生第二条记录。可对其他端口再次执行。通过 `/etc/hy2-ipban.nft` 保存当前规则，并启用 `hy2-ipban.service`，在正常系统重启后尝试恢复。

### 2. 解封（单 IP / 批量，一行命令，最后一条解封时清理）

```bash
P=40001 IPS="1.2.3.4 5.6.7.8" bash -c 'set -euo pipefail; (( EUID == 0 )) || { echo "需 root 权限"; exit 1; }; [[ $P =~ ^[1-9][0-9]{0,4}$ ]] && (( P <= 65535 )) || { echo "端口无效"; exit 1; }; read -r -a A <<< "$IPS"; (( ${#A[@]} > 0 )) || { echo "IP 列表为空"; exit 1; }; python3 -c "import ipaddress,sys; [ipaddress.IPv4Address(x) for x in sys.argv[1:]]" "${A[@]}"; exec 9>/run/hy2-ipban.lock; flock -x 9; if nft list table inet hy2_ipban >/dev/null 2>&1; then for IP in "${A[@]}"; do if nft get element inet hy2_ipban banned "{ $IP . $P }" >/dev/null 2>&1; then nft delete element inet hy2_ipban banned "{ $IP . $P }"; fi; done; EMPTY=$(nft -j list set inet hy2_ipban banned | python3 -c "import json,sys; a=[x[\"set\"] for x in json.load(sys.stdin)[\"nftables\"] if \"set\" in x]; assert len(a)==1; print(\"yes\" if not a[0].get(\"elem\") else \"no\")"); else EMPTY=yes; fi; if [[ $EMPTY == yes ]]; then systemctl disable hy2-ipban.service >/dev/null 2>&1 || true; if nft list table inet hy2_ipban >/dev/null 2>&1; then nft delete table inet hy2_ipban; fi; rm -f /etc/hy2-ipban.nft /etc/hy2-ipban.nft.tmp /etc/systemd/system/hy2-ipban.service /etc/systemd/system/multi-user.target.wants/hy2-ipban.service; systemctl daemon-reload; rm -f /run/hy2-ipban.lock; echo "已解封；黑名单为空，已清理本模块专属规则与文件"; else nft list table inet hy2_ipban >/etc/hy2-ipban.nft.tmp; mv -f /etc/hy2-ipban.nft.tmp /etc/hy2-ipban.nft; echo "已解封指定 IP；其余黑名单保留"; fi'
```

**解封清理：** 只解除指定的来源 IP + 端口组合；其他封禁保留。全部解封后，会删除本功能的 nftables 表/链/集合/规则、开机恢复服务、启用链接、持久化配置及锁文件。不会删除系统日志、Bash 历史，也不会主动清除已有 TCP / conntrack 连接状态。**不要并发执行封禁和解封命令**，以避免最后一次清理锁文件时发生竞争。

**与仓库及现有功能的关系：** 这两条命令不会自动修改任何 GitHub 仓库，也不会直接改动 HY2、SOCKS5、WG-NAT 的 UUID、配额或 IP_LIMIT 数据。但防火墙规则作用于整台执行命令的服务器，可能影响同一端口上的其他应用或 NAT 转发流量。本 手册第九节的「临时封禁整个端口」与此处的 IP 黑名单是两套独立规则；即使解封了 IP，如果那个端口还有临时封禁或其他防火墙策略，仍可能无法连接。

**验证说明：** 命令已检查 Bash 语法，未在生产服务器上实际执行；系统重启、防火墙管理程序重载、其他服务清空 nftables 规则时，应以实时 `nft list table inet hy2_ipban` 和 `systemctl status hy2-ipban.service` 的结果为准。
