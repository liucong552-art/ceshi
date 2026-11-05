# HY2 / WG-NAT / 端口限速

命令使用 **root + Bash**，示例端口换成实际端口。长命令和低频操作点击展开，所有内容都在本 README 内。

[查询](#queries) · [新建节点](#create) · [修改与重置](#modify) · [排查](#trouble-guide) · [封禁与删除](#remove) · [安装与更新](#install)

> 按 [jieshu README](https://github.com/liucong552-art/jieshu/blob/main/README.md) 的六章节顺序、查询入口、折叠方式组织，但内容替换为能实际运行的 HY2 命令。未实现的功能明确标注。

<a id="queries"></a>
## 一、查询与检查

| 想查什么 | 在哪台机器 | 命令 |
|---|---|---|
| 主节点、临时节点、到期、配额、IP 占用 | HY2 VPS | `hy2_audit.sh` |
| 某端口配额归属、已用量、剩余量、重置周期 | HY2 VPS | `pq_audit.sh` |
| 端口监听、TLS、WG 出口和管理组件 | HY2 VPS | `hy2_doctor.sh --strict` |
| 指定临时节点状态 | HY2 VPS | `hy2_audit.sh --tag hy2-temp-节点名称` |
| 刷新临时节点订阅 | HY2 VPS | `hy2_temp_sub.sh` |
| WG-NAT 出口与 fail-closed 检查 | HY2 VPS | `/usr/local/sbin/wg_nat_healthcheck.sh` |
| Peer 列表 / NAT 出口机状态 | NAT 机 | `bash /root/nat.sh list` / `bash /root/nat.sh status` |
| 异常服务日志 | HY2 VPS | [故障排查](#troubleshooting) |
| 独立端口限速与自动降速（可选） | HY2 VPS | [安装与管理 portbw](#portbw) / `portbw report` |\n| 每日流量、黑名单等是否支持 | — | [功能差距](#feature-gap) |

`hy2_audit.sh` 和 `pq_audit.sh` 对同一端口的配额使用同一底层计数快照，不是两套不同计费。`hy2_temp_sub.sh` 输出的订阅文件包含节点凭据。

<details><summary>展开：已有的审计、主节点和临时节点订阅命令</summary>

#### 查看全部节点

```bash
hy2_audit.sh
```

#### 综合健康检查

```bash
hy2_doctor.sh --strict
```

也可以执行：

```bash
bash /root/hy2.sh doctor
bash /root/hy2.sh version
```

#### 重新生成临时节点订阅

```bash
hy2_temp_sub.sh
```

单节点链接保存在 `/var/lib/hy2/temp/<完整TAG>.url`，主节点链接在 `/root/hy2_main_url.txt`。这些链接含密码，请勿粘贴到公开日志或 GitHub。

</details>

<a id="advanced-queries"></a>

<details>

<summary>补充查询：只在总览未提供所需信息时使用</summary>

| 查询 | 命令 | 说明 |
|---|---|---|
| 所有受管理配额 | `pq_audit.sh` | 显示配额归属及周期 |
| 单节点 | `hy2_audit.sh --tag hy2-temp-节点名称` | 到期与状态 |
| 单端口限速 | `portbw show 40020` | 独立的 nft/tc 状态 |
| 动态限速采样进度 | `portbw auto status 40020` | 触发、保护与恢复 |
| 限速自检 | `portbw audit` | 异常返回非零 |
| NAT 出口 | `/usr/local/sbin/wg_nat_healthcheck.sh` | WG 握手与出口 |

累计总流量配额和 Mbps 带宽是不同概念。

</details>

<a id="node-link"></a>

<details>

<summary>按端口查询 HY2 节点链接</summary>

修改第一行端口。输出包含密码，不要提交到 GitHub。

```bash

PORT=40020

if [[ "$PORT" == 443 ]]; then

  cat /root/hy2_main_url.txt

else

  mapfile -t m < <(grep -l "^PORT=$PORT$" /var/lib/hy2/temp/*.env 2>/dev/null)

  if (( ${#m[@]} == 1 )); then

    cat "${m[0]%.env}.url"

  else

    echo "未找到唯一端口对应的节点，请检查 hy2_audit.sh"

  fi

fi

```

</details>

<a id="active-ips"></a>

<details>

<summary>查看来源 IP 占位与 UDP 监听</summary>

`IP_LIMIT` 为活跃来源 IP 槽位，不是实际人数；HY2 用 UDP，不照搬 VLESS 的 TCP established 查询。

```bash

PORT=40020

source /usr/local/lib/hy2/iplimit-lib.sh

hy2_il_active_ips "$PORT"

ss -lunp | grep ":$PORT " || true

```

</details>

<a id="daily-traffic"></a>

<details>
<summary>HY2 每日流量：今天、单端口最近 30 天、历史 JSON 导出</summary>

**已实现独立可选组件**：从 `jieshu/traffic.py` 移植到 HY2 的 UDP 计数器、密码身份及主节点 `MAIN_PORT`。按北京时间自然日保存最近 30 天，`UP` 用户上传、`DOWN` 用户下载、`TOTAL` 当日合计；这是**独立的每日历史**，与 `pq_audit.sh` 的双向总配额互不冲突。必须先按 [6.3 安装每日流量](#daily-install)，安装前的历史流量无法补记。

```bash
# 所有当前节点今天的上传、下载
hy2_traffic

# 指定临时节点最近 30 天
hy2_traffic 40020

# 主节点最近 30 天
hy2_traffic main

# 按临时节点 ID 或完整 TAG
hy2_traffic nat4
hy2_traffic hy2-temp-nat4

# 导出指定端口仍保留的每日历史 JSON（字节）
hy2_traffic 40020 --json
```

- 数据文件 `/var/lib/hy2/traffic/daily.json`，节点密码不保存到历史文件，只保存不可逆身份摘要；同名重建/端口复用、主节点密码轮换不会继承旧用户历史。
- 每分钟定时采集；查询时也执行采集。自动删除/到期节点的历史会在下次成功采集时清理，保留 30 个北京时间自然日。
- 采用**独立** `inet hy2_daily_traffic` nft 表，只统计该节点 UDP 端口双向字节，不修改 `hy2_pq` 或 `hy2_iplimit`。跨午夜分钟采样、断电尚未保存的尾部流量有少量归属/丢失风险，不能把它作为毫秒级结算系统。
- 与 `jieshu` 一样配有定时采集和关机 `ExecStop` 保存服务；**部署到 HY2 VPS 后的实际读写、重启和端口复用仍需要真机测试**，CI 只能证明程序结构和模拟计数逻辑。

</details>

<a id="speed-logs"></a>

<details>
<summary>自动限速日志：全部端口、单端口、全部尚存历史</summary>

以下依次查询：最近 24 小时全部端口、最近 24 小时指定端口、指定端口全部尚存历史。北京时间输出；旧日志不代表当前配置，当前值以 `portbw report` 为准。

up / down 分别为上传 / 下载；没有自动降速日志不代表基础限速失效。可查历史受 journald 轮替、容量和保留策略限制。

```bash
TZ=Asia/Shanghai journalctl -u portbw-watch.service \
  --since '24 hours ago' --no-pager -o short-iso \
  | grep -E '自动降速|恢复基础速度|失败|Error|Traceback' \
  || echo '最近24小时没有匹配的降速、恢复或报错日志'
```

```bash
PORT=40002
TZ=Asia/Shanghai journalctl -u portbw-watch.service \
  --since '24 hours ago' --no-pager -o short-iso \
  | grep -E "${PORT} (up|down): (自动降速|恢复基础速度)" \
  || echo "最近24小时端口 ${PORT} 没有降速或恢复记录"
```

```bash
PORT=40002
TZ=Asia/Shanghai journalctl -u portbw-watch.service \
  --no-pager -o short-iso \
  | grep -E "${PORT} (up|down): (自动降速|恢复基础速度)" \
  || echo "端口 ${PORT} 没有尚存的降速或恢复历史记录"
```

</details>



<a id="hy2-audit"></a>

### HY2 来源 IP、连接错误与访问目标审计

**现已提供可执行的只读工具 `hy2-abuse.py`**，专门针对 HY2，而不是把 `vless-abuse` 改名。直接从受管理的 HY2 临时节点/IP 槽位，以及该节点的 `journalctl` 提取已有证据：

```bash
PORT=40020 DAYS=7 hy2-abuse
hy2-abuse 40020 --days 7 --json
```

- **当前来源 IP**：从 HY2 nftables `hy2_iplimit` 集合读取活跃槽位。若 `IP_LIMIT=0`，没有此类占位记录，**不能**据此认定“没有用户”。
- **异常事件**：从该 HY2 systemd 服务最多扫描最近 3000 条 journald 消息，按认证、握手、QUIC、超时、网络等类型统计警告/错误；不会在报告中输出原始错误字符串或节点密码。日志被轮替、事件未记录时无法补查。
- **实时访问目标**：如果手动配置了 Hysteria 2 **Traffic Stats API** 且仅绑定回环地址，可额外查询**当前 TCP 流**的目标地址、当前在线实例数（见安装说明）。**UDP 目标和历史目标列表不由该 API 提供**，不能虚构。
- **只读**：不触发自动封禁，也不更改配额、WG、nft 规则。IP 槽位/在线实例数不等于真实人数，出现异常事件也不等于已经确定滥用。

<a id="subscription"></a>

<details>

<summary>导出有效临时节点订阅（生成文件）</summary>

只导出有效且运行的临时节点到 `/root/hy2_temp_subscription.txt` 和 Base64 文件，不包含主节点。Base64 不是加密。

```bash

hy2_temp_sub.sh

```

</details>

<a id="create"></a>

## 二、新建节点

<a id="temp-create"></a>

### HY2 临时节点

在 **HY2 VPS** 执行。下面两种命令二选一：示例有效期 7 天，总配额 50 GiB，最多 3 个活跃来源 IP；WG-NAT 命令需先完成[WG-NAT 部署](#wg-nat)。

普通临时节点：

```bash
DAYS=7; IP_VERSION=4 IP_LIMIT=3 PQ_GIB=50 D=$((DAYS*24*60*60)) hy2_mktemp.sh
```

WG-NAT 临时节点：

```bash
DAYS=7; IP_VERSION=4 IP_LIMIT=3 PQ_GIB=50 D=$((DAYS*24*60*60)) hy2_mktemp_nat.sh
```

<details>
<summary>其他创建方式：分钟、小时、固定端口、IPv6</summary>

### 按分钟

普通临时节点：

```bash
MINUTES=30; IP_VERSION=4 IP_LIMIT=3 PQ_GIB=1 D=$((MINUTES*60)) hy2_mktemp.sh
```

WG-NAT 临时节点：

```bash
MINUTES=30; IP_VERSION=4 IP_LIMIT=3 PQ_GIB=1 D=$((MINUTES*60)) hy2_mktemp_nat.sh
```

### 按小时

普通临时节点：

```bash
HOURS=2; IP_VERSION=4 IP_LIMIT=3 PQ_GIB=1 D=$((HOURS*60*60)) hy2_mktemp.sh
```

WG-NAT 临时节点：

```bash
HOURS=2; IP_VERSION=4 IP_LIMIT=3 PQ_GIB=1 D=$((HOURS*60*60)) hy2_mktemp_nat.sh
```

### 固定端口

下面示例固定使用端口 `40000`，有效期为 1 小时。

普通临时节点：

```bash
HOURS=1; IP_VERSION=4 PORT_START=40000 PORT_END=40000 IP_LIMIT=3 PQ_GIB=1 D=$((HOURS*60*60)) hy2_mktemp.sh
```

WG-NAT 临时节点：

```bash
HOURS=1; IP_VERSION=4 PORT_START=40000 PORT_END=40000 IP_LIMIT=3 PQ_GIB=1 D=$((HOURS*60*60)) hy2_mktemp_nat.sh
```

固定端口时，`PORT_START` 和 `PORT_END` 必须设置为同一个端口。

### 创建 IPv6 入站节点

IPv6 只需改一个参数，按下面两步操作：

1. 在 `/etc/default/hy2-main` 中填写 `HY_IPV6_DOMAIN`，并确保该域名的 AAAA 记录指向当前 HY2 VPS。
2. 将上面任意创建命令中的 `IP_VERSION=4` 改成 `IP_VERSION=6`，其他参数不用改。

例如，创建 IPv6 临时节点：

```bash
HOURS=2; IP_VERSION=6 IP_LIMIT=3 PQ_GIB=1 D=$((HOURS*60*60)) hy2_mktemp.sh
```

WG-NAT 同样只需把 `IP_VERSION=4` 改成 `IP_VERSION=6`；客户端通过 IPv6 连接 HY2 VPS，出站仍是 NAT 机的公网 IPv4。

常用调整：

- 不限制来源 IP：改为 `IP_LIMIT=0`
- 不限制流量：删除 `PQ_GIB=...`
- 修改固定端口：同时修改 `PORT_START` 和 `PORT_END`

创建成功后会直接输出端口、到期时间和节点链接。

</details>

<details>
<summary>创建参数、节点类型与自动重置规则</summary>

普通节点用 VPS 出口，WG-NAT 节点用 NAT 出口；`id` 可省略或填写英文 / 数字 / 点 / 下划线 / 连字符。

| 参数 | 含义 |
|---|---|
| `D` | 有效期秒数，由示例前面的分钟 / 小时 / 天数计算 |
| `PQ_GIB` | 上传+下载合计配额；不限量时省略，不能填 0 |
| `IP_LIMIT` | 活跃来源 IP 槽位数；0 为不限数量 |
| `IP_STICKY_SECONDS` | 闲置占位秒数，默认 120；持续访问会刷新 |
| `PORT_START` / `PORT_END` | 固定端口时两项填相同端口 |
| `IP_VERSION` | 入站协议族 4 / 6 |

创建时有配额且有效期严格超过 30 天，才每 30×24 小时自动重置；不是每月 1 日，重置也不延长有效期。

</details>

<a id="socks5-create"></a>

<details>
<summary>其他协议：SOCKS5 不属于 HY2</summary>

本项目不包含 SOCKS5 账号管理，不提供 `socks5 add` 等命令。

</details>

<a id="modify"></a>
## 三、修改与重置

<a id="quota-ip"></a>

### 配额与来源 IP

**重要：`pq_add.sh` 是重新设置整份配额，不是追加流量。** 修改已有临时节点会将已用量清零，不延长节点 TTL，也不启用 30 天自动重置。修改后立即执行 `pq_audit.sh` 核对。

#### 设置端口双向总流量

```bash
pq_add.sh 40000 50
```

表示 UDP `40000` 的上行与下行合计最多 `50 GiB`。

#### 查看全部配额

```bash
pq_audit.sh
```

#### 删除端口配额

```bash
pq_del.sh 40000
```

#### 设置活跃来源 IP 数量

```bash
ip_set.sh 40000 2 300
```

表示最多允许 2 个活跃来源 IP，每个槽位保持 300 秒。

#### 删除 IP 数量限制

```bash
ip_del.sh 40000
```

删除 IP 数量限制后，地址族隔离仍然保留。

临时节点到期或被删除时，其配额和 IP 限制也会一起清理。

### 准确输入 MiB 配额

`PQ_GIB` 与 `pq_add.sh` 的流量参数均为 GiB；**1 GiB = 1024 MiB**。

| 想要的额度 | GiB 参数 | 示例 |
|---|---|---|
| 50 MiB | `0.048828125` | `pq_add.sh 40020 0.048828125` |
| 100 MiB | `0.09765625` | `pq_add.sh 40020 0.09765625` |
| 500 MiB | `0.48828125` | `pq_add.sh 40020 0.48828125` |

`pq_del.sh` 与 `ip_del.sh` 仅取消对应限制，不删除临时节点；删除前请确认端口对应的节点身份。

<a id="quota-keep-cycle"></a>

### 手动重置流量并保留原自动重置日期

**已实现 `pq_keepcycle.sh`**：直接使用 HY2 现有的配额库、临时节点/配额双锁、nft 原子重建和元数据所有者检查。对**已有配额且尚未到期的临时节点**，将总配额重新设定、已用量清零，**保留原 30 天周期日期**；若有效期严格超过 30 天、但此前 `pq_add.sh` 清除了周期，可按节点创建时间恢复应有的周期锚点。短期节点不会新增周期。

**2026-11-05 真机验收通过**：独立 31 天 HY2-NAT 临时节点已用 41.07KiB 时调用 `pq_keepcycle.sh`，配额 20.48MiB → 40.96MiB、已用清零、原 30 天重置时间（北京时间 2026-12-05 09:20:25）不变，`KEEP_CYCLE_METADATA_TEST=PASS`，主 HY2/WG 正常。实际跨 30 天自动触发仍未验证。

示例：把 UDP 40020 的配额重置为 **500 MiB**，保留或按创建日恢复原周期：

```bash
pq_audit.sh 40020
pq_keepcycle.sh 40020 0.48828125
pq_audit.sh 40020
```

如果你还没有重新运行新版 `hy2.sh` 管理器，也可**只下载独立命令**，避免重装 WG 或修改运行中的 HY2：

```bash
curl -fL https://raw.githubusercontent.com/liucong552-art/ceshi/main/pq_keepcycle.sh -o /root/pq_keepcycle.sh
bash -n /root/pq_keepcycle.sh
bash /root/pq_keepcycle.sh 40020 0.48828125
```

执行会在 `/root/pq-keepcycle-端口-随机字符/` 下备份节点和配额元数据（含敏感信息，保持 root 私有），然后更新配额和 nft 规则。与 `pq_add.sh` 的关键区别是**不随手删除原自动重置日期**。必须确认端口正确，且确定需要将已用量清零；该命令不是增加余额。

**测试边界**：源码语法、安装器生成的一致性、元数据锁/归属检查和重置计算由 CI 覆盖；仍需在有 30 天以上有效期的**专用测试节点**上验证实际计数、保留日期和重启持久化，不能在当前生产节点上盲目重置。

<a id="quota-reset-time"></a>

<details>

<summary>查询端口下次自动重置时间及倒计时（只读）</summary>

只需修改端口；此查询读取 `/var/lib/hy2/quota`，不会修改内核配额。

```bash

PORT=40020

META="/var/lib/hy2/quota/$PORT.env"

if [[ -f "$META" ]]; then

  NEXT=$(awk -F= '$1=="NEXT_RESET_EPOCH"{print $2; exit}' "$META")

  INT=$(awk -F= '$1=="RESET_INTERVAL_SECONDS"{print $2; exit}' "$META")

  if [[ "$NEXT" =~ ^[0-9]+$ && "$INT" =~ ^[0-9]+$ ]] && (( NEXT>0 && INT>0 )); then

    TZ=Asia/Shanghai date -d "@$NEXT" "+下次计划重置（北京时间）：%F %T"

    NOW=$(date +%s)

    (( NEXT>NOW )) && echo "剩余 $((NEXT-NOW)) 秒" || echo "已到预定时间，请检查 pq-reset.timer"

  else

    echo "该端口没有启用周期重置"

  fi

else

  echo "没有该端口配额配置"

fi

```

`pq-reset.timer` 开机约 15 分钟后首次检查，以后通常每小时检查一次。

```bash

systemctl list-timers --all pq-reset.timer

journalctl -u pq-reset.service -n 50 --no-pager

```

</details>

<a id="portbw"></a>

### 自动限速标准：40→18 Mbps，持续 45 秒触发

在实际需要限速的服务器执行；示例端口为 `40000`。整行复制即可：

```bash
portbw auto set 40000 --up 40 --down 40 --trigger 100 --after 45s --auto-up 18 --auto-down 18 --hold 120s --cooldown 0s
```

<details>
<summary>自动限速参数、恢复规则与关闭自动模式</summary>

上传 / 下载分别触发。100% 指本方向 40 Mbps；85% 只用于采样连续性判断，整段窗口仍需达标。保护至少 120 秒，高负载会续期；恢复后零冷却，再次触发仍需重新满足 45 秒条件。手动 set/up/down 会关闭自动模式，删除节点不会自动删除独立限速规则。仅覆盖本机端口，不覆盖任意 WG/NAT 转发；完整双向限速使用默认 nft+tc 模式。

同一端口的 TCP/UDP、IPv4/IPv6 共用上传额度，并共用另一份下载额度，不是每种协议各分一份。静态限速中 `0` 表示该方向不限速。`--nft-only` 不支持下载限速。

实际吞吐可能受协议开销、丢包和瞬时突发影响，不能要求客户端每一瞬间都精确等于配置值。

自动模式只有 `--cooldown` 可省略，默认 `60s`；零冷却必须明确填 `--cooldown 0s`，其他自动参数均必填。两方向均须满足 `0 < 降速值 < 基础速度`；`--trigger` 的单位为百分比，不是 Mbps。

设置后用 `portbw report` 核对；需要采样进度或内核故障检查时，见[补充查询](#advanced-queries)。

```bash
portbw auto off 40000  # 关闭自动模式，恢复本端口的基础上传 / 下载限速
```

</details>

<details>
<summary>静态限速：设置、修改、取消</summary>

**40001：TCP+UDP 合计上传 10 Mbps / 合计下载 20 Mbps：**

```bash
portbw set 40001 10 20  # 40001：TCP+UDP 合计上传 10 Mbps / 合计下载 20 Mbps
```

**上传上限改为 15 Mbps：**

```bash
portbw up 40001 15      # 上传上限改为 15 Mbps
```

**下载上限改为 30 Mbps（直接限速，不是触发阈值）：**

```bash
portbw down 40001 30    # 下载上限改为 30 Mbps（直接限速，不是触发阈值）
```

**仅关闭下载限速；上传额度不变：**

```bash
portbw down 40001 0     # 仅关闭下载限速；上传额度不变
```

**仅取消端口限速，不删除 HY2 节点：**

```bash
portbw del 40001        # 仅取消端口限速，不删除 HY2 节点
```

</details>



**HY2-NAT 真机测速（2026-11-04）：** `portbw set 40020 10 10`、`portbw report`、`portbw audit` 均正常，用户报告 v2rayN 测速通过，最后 `portbw del 40020` 正常取消。**自动 40→18 Mbps 的持续触发和恢复尚未单独验证。**

**注意：** 独立限速不会因 HY2 临时节点到期/删除而自动移除；复用端口前要用 `portbw report` 检查。对 WG-NAT 节点按 HY2 VPS 的 UDP 40020 限速，不是 NAT 出口机的 WG 51820。

<a id="troubleshooting"></a>
## 四、故障排查

<a id="trouble-guide"></a>

### 从症状出发

| 现象 | 先看什么 | 处理边界 |
|---|---|---|
| HY2 主节点 active 但客户端不通 | `hy2_doctor.sh --strict`、UDP 443、DNS A 记录、客户端 SNI | 确认云安全组/防火墙，勿反复重装 |
| 某临时节点突然连不上 | `hy2_audit.sh` + `pq_audit.sh` | 检查到期、配额耗尽或 IP 槽位占满 |
| NAT 节点无法创建 | `wg_nat_healthcheck.sh` / `wg show wg-nat` | 先检查 WG 握手、路由、NAT Peer |
| WG 断开导致 NAT 节点无法联网 | `ip -4 route show table 100` | 若仍有 `prohibit default` 是预期 fail-close，不可改走 VPS |
| 刚重启时 NAT 节点短暂启动失败 | `journalctl -b -u hy2-temp-对应TAG.service` | WG 首次握手可能延后，观察是否自动重试成功 |
| 首次整机重启配额变成 0 | `systemctl is-active hy2-managed-shutdown-save.service` | 初装启动缺陷已由 [PR #3](https://github.com/liucong552-art/ceshi/pull/3) 修复 |
| doctor 警告缺少 `HYSTERIA_BINARY_SHA256` | `hy2_doctor.sh --strict` | 未配置持续核心校验，不是连接故障 |
| 到期节点端口未释放 | `journalctl -u hy2-gc.service -n 50 --no-pager` | 查 GC timer 和防火墙状态，不要盲目覆盖端口 |
| HY2 节点限速异常或删节点后端口仍被限速 | `portbw report`、`portbw audit`、`portbw show 40020` | `portbw` 独立于节点生命周期；仅确认归属后执行 `portbw del 40020` |

### 常用只读检查

```bash
systemctl is-active hy2.service wg-quick@wg-nat.service
systemctl list-timers --all hy2-gc.timer pq-save.timer hy2-managed-watchdog.timer
journalctl -u hy2.service -n 50 --no-pager
journalctl -u hy2-managed-watchdog.service -n 50 --no-pager
journalctl -u wg-quick@wg-nat.service -n 50 --no-pager
```

`hy2_restore_all.sh` 或启动 watchdog 会修改规则与服务状态，**不是纯查询**；优先收集状态与日志后再进行恢复。

- 主节点和临时节点启动前会检查防护规则
- 配额或 IP 限制丢失时，watchdog 尝试恢复
- 无法恢复时停止对应监听，保持 fail-close
- WG-NAT 故障时，策略路由 `prohibit` 和 OUTPUT REJECT 防止出口泄漏
- 临时节点创建采用事务式清理，失败不会保留半套状态
- Hysteria 核心升级失败会恢复旧二进制与原活跃服务
- 证书续期钩子会验证证书有效期及公私钥匹配
- 已到期节点不会在证书续期时被错误重启
- 配置了 `HYSTERIA_BINARY_SHA256` 时，doctor 会检查当前核心哈希

手动触发全部保护恢复：

```bash
/usr/local/sbin/hy2_restore_all.sh
```

手动触发 watchdog：

```bash
systemctl start hy2-managed-watchdog.service
```

<a id="remove"></a>
## 五、暂停、封禁与删除

<a id="port-block"></a>

**已实现**：`hy2-guard.py` 提供真正可持久化的整端口暂停 / 恢复与来源 IP/CIDR 黑名单，规则在独立 `inet hy2_manual_guard` 表中。不会修改 `hy2_pq`、`hy2_iplimit`、WireGuard、Hysteria 服务或其他端口；修改只允许作用于已登记的 HY2 主节点和临时节点，不允许误封 SSH 或其他服务端口。初次使用先完成下面的安装。

### 初次安装（HY2 VPS 执行）

```bash
curl -fL --retry 3 https://raw.githubusercontent.com/liucong552-art/ceshi/main/hy2-guard.py -o /root/hy2-guard.py
python3 -m py_compile /root/hy2-guard.py
python3 /root/hy2-guard.py install
hy2-guard audit
```

安装生成 `/usr/local/sbin/hy2-guard` 和 `hy2-manual-guard.service`；重启后由 systemd 恢复持久规则。依赖 `nftables`、`python3`、`systemd`。只对 `inet hy2_manual_guard` 表进行原子事务，**不会执行 `nft flush ruleset`**。对于真正的恢复和安全边界，仍需在目标 VPS 上用单独测试端口做一次真机验证。

<a id="pause-port"></a>

<details>
<summary>端口暂停：封禁、解封、状态查询</summary>

以下操作不会删除节点、重置配额或停止 HY2 服务；暂停时外部 IPv4/IPv6 的 TCP/UDP 入站会被丢弃。示例 UDP/40020，先确认这是需要管理的 HY2 节点：

```bash
hy2_audit.sh
hy2-guard pause 40020
hy2-guard status
hy2-guard audit

# 恢复此端口的外部连接
hy2-guard resume 40020
hy2-guard audit
```

恢复后如还有独立配额、IP 限制、黑名单或 portbw 限速，它们继续生效。守护进程不会自行撤销手工暂停。

</details>

<a id="source-blacklist"></a>

<details>
<summary>来源 IP / CIDR 黑名单：单 IP、网段、批量逐条管理</summary>

支持 IPv4 和 IPv6，输入 CIDR 时必须填写规范网络地址（例如 `203.0.113.0/24`；不是 `203.0.113.123/24`）。以下都只影响指定 HY2 端口：

```bash
# 单独拉黑一个 IPv4 来源
hy2-guard ban 40020 198.51.100.8

# 拉黑整个来源网段
hy2-guard ban 40020 203.0.113.0/24
hy2-guard ban 40020 2001:db8::/32

# 解除其中一个网段，不影响其他规则
hy2-guard unban 40020 203.0.113.0/24

# 清空仅此端口的黑名单，不会取消整端口暂停
hy2-guard clear 40020
```

**区别**：`IP_LIMIT=1` 是最多同时允许一个活跃来源 IP；`hy2-guard ban` 则直接封禁具体来源地址或网段，它们各自独立，互不替代。手工黑名单支持持久化，删除临时节点不自动清除手工规则。

<a id="blacklist-report"></a>

### 查询所有受管理暂停与黑名单

```bash
hy2-guard status
hy2-guard audit
systemctl is-enabled hy2-manual-guard.service
systemctl is-active hy2-manual-guard.service
```

如果因外部清空表导致 `audit` 失败，在确认没有其他管理器冲突后，可运行 `hy2-guard restore` 恢复独立表。恢复只使用本模块的状态文件，不读取任意 shell 配置。

</details>

<a id="node-manage"></a>

<details>

<summary>删除指定 HY2 临时节点 / 清空全部临时节点</summary>

删除临时节点会使对应链接失效并清理其配额/IP 规则；不会删除 HY2 主节点或 WireGuard。清理前先运行 `hy2_audit.sh` 确认完整 TAG。

#### 强制删除一个临时节点

先运行 `hy2_audit.sh` 找到完整 TAG，然后执行：

```bash
FORCE=1 hy2_cleanup_one.sh hy2-temp-节点名称
```

普通临时节点与 WG-NAT 临时节点使用同一清理命令。

#### 清空全部临时节点

```bash
hy2_clear_all.sh
```

</details>

<a id="install"></a>

## 六、安装与更新

按组件展开。WG-NAT 保留两端完整安装顺序，portbw 独立安装和更新。

<details>
<summary>环境要求、脚本运行位置与首次安装</summary>

### 运行环境和网络准备

- 全新 Debian 11+ 或 Ubuntu 20.04+
- `root` 用户，且 PID 1 为 systemd
- 一个 A 记录指向 HY2 VPS 公网 IPv4 的域名
- 创建 IPv6 临时节点时，再准备一个 AAAA 记录指向该 VPS 的域名
- TCP `80` 可从公网访问，用于首次签发证书
- 放行主节点 UDP `443`
- 放行临时节点 UDP 端口范围，默认 `40000-50050`
- 使用 WG-NAT 时，NAT 机与 HY2 VPS 都要放行 UDP `51820`
- 域名如使用 Cloudflare，必须保持 DNS only（灰云）

### 从 `main` 下载与最小安装测试

与 `jieshu` 类似，`ceshi/main` 提供可直接复制到全新 Debian 11+ / Ubuntu 20.04+ VPS 的安装命令。下载后先运行 `bash -n`；只有下载及语法检查成功才执行。**HY2 VPS 与 NAT 出口机分别操作，请勿混用。** 建议先给测试机建立 VPS 快照。

#### 1. HY2 VPS：安装管理器

```bash
apt-get update && apt-get install -y curl ca-certificates
curl -fL --retry 3 https://raw.githubusercontent.com/liucong552-art/ceshi/main/hy2.sh -o /root/hy2.sh && chmod 700 /root/hy2.sh && bash -n /root/hy2.sh && bash /root/hy2.sh
```

首次安装只部署管理模块，**不会自动生成一个可用的主节点**。必须修改域名及邮箱、签发证书并部署主节点：

```bash
nano /etc/default/hy2-main
# 填写 HY_DOMAIN、ACME_EMAIL；保证 A 记录指向本机、TCP/80 可访问
bash /root/onekey_hy2_main_tls.sh
hy2_doctor.sh --strict
```

主节点 UDP/443 和临时节点 UDP 端口需要在服务商防火墙中放行。查看主节点链接：`cat /root/hy2_main_url.txt`。

#### 2. NAT 出口机：安装 WireGuard NAT

```bash
apt-get update && apt-get install -y curl ca-certificates
curl -fL --retry 3 https://raw.githubusercontent.com/liucong552-art/ceshi/main/nat.sh -o /root/nat.sh && chmod 700 /root/nat.sh && bash -n /root/nat.sh && bash /root/nat.sh init
```

#### 3. HY2 VPS：配置 WG-NAT（可选）

```bash
curl -fL --retry 3 https://raw.githubusercontent.com/liucong552-art/ceshi/main/vpswg.sh -o /root/vpswg.sh && chmod 700 /root/vpswg.sh && bash -n /root/vpswg.sh && bash /root/vpswg.sh
```

先按下方“部署 WG-NAT”章节，在 NAT 机执行 `nat.sh add`，回 HY2 VPS 执行 NAT 机输出的 `wg_nat_set_peer.sh` 命令，并确保 `wg_nat_healthcheck.sh` 成功。最后：

```bash
curl -fL --retry 3 https://raw.githubusercontent.com/liucong552-art/ceshi/main/natjichang.sh -o /root/natjichang.sh && chmod 700 /root/natjichang.sh && bash -n /root/natjichang.sh && bash /root/natjichang.sh
bash /root/natjichang.sh check
```

#### 4. 最小真机测试（HY2 VPS）

```bash
bash /root/hy2.sh version
hy2_doctor.sh --strict
id=release-test IP_VERSION=4 IP_LIMIT=1 PQ_GIB=1 D=300 hy2_mktemp.sh
hy2_audit.sh
FORCE=1 hy2_cleanup_one.sh hy2-temp-release-test
```

以上命令需要先完成主节点部署。额外按文末验收清单验证 WG-NAT 出口、断线防泄漏、重启恢复、IPv6 和流量/IP 限制。CI 仅能自动验证源代码语法与静态约束，不等于真实服务器验收合格。

**注意：** `nat.sh init/add/del` 会重启 WG 接口/调整防火墙；不要直接在正在给客户提供出口的机器上试验。

</details>

<a id="hy2-install"></a>

<details>
<summary>6.1 HY2 主节点：安装 → 配置 → 创建主节点 → 检查</summary>

### 五个项目文件

将以下五个文件放在同一目录：

```text
hy2.sh
vpswg.sh
nat.sh
natjichang.sh
README.md
```

设置脚本权限：

```bash
chmod 700 hy2.sh vpswg.sh nat.sh natjichang.sh
```

上传到自己的 GitHub 仓库后，也可以使用 Raw 地址下载。示例：

```bash
RAW_BASE='https://raw.githubusercontent.com/<owner>/<repo>/refs/heads/main'
curl -fsSL "$RAW_BASE/hy2.sh" -o /root/hy2.sh
curl -fsSL "$RAW_BASE/vpswg.sh" -o /root/vpswg.sh
curl -fsSL "$RAW_BASE/nat.sh" -o /root/nat.sh
curl -fsSL "$RAW_BASE/natjichang.sh" -o /root/natjichang.sh
chmod 700 /root/hy2.sh /root/vpswg.sh /root/nat.sh /root/natjichang.sh
```

将 `<owner>/<repo>` 替换成实际仓库地址。

### HY2 管理器、主节点、证书及核心升级

#### 1. 安装管理系统

在 **HY2 VPS** 执行：

```bash
bash /root/hy2.sh
```

脚本会创建：

- `/etc/default/hy2-main`
- `/root/onekey_hy2_main_tls.sh`
- `/usr/local/sbin/hy2_mktemp.sh`
- 配额、IP 限制、审计、清理、恢复和 watchdog 工具
- 对应的 systemd service 与 timer

#### 2. 修改配置

```bash
nano /etc/default/hy2-main
```

最少需要填写：

```ini
HY_DOMAIN=hy2.example.com
ACME_EMAIL=admin@example.com
MASQ_URL=https://www.apple.com/
```

创建 IPv6 临时节点时再填写：

```ini
HY_IPV6_DOMAIN=hy2-v6.example.com
```

主要默认参数：

```ini
HY_LISTEN=0.0.0.0:443
TEMP_PORT_START=40000
TEMP_PORT_END=50050
ENABLE_SALAMANDER=0
HYSTERIA_VERSION=latest
HYSTERIA_UPDATE_POLICY=install-only
```

说明：

- 主节点固定监听 IPv4 UDP `443`
- 普通 IPv4 临时节点使用 `HY_DOMAIN`
- IPv6 临时节点使用 `HY_IPV6_DOMAIN`
- `install-only` 表示首次安装核心，之后重复执行部署不会偷偷升级 Hysteria
- 默认使用正式证书，不使用自签证书或跳过证书验证

#### 3. 创建或更新主节点

```bash
bash /root/onekey_hy2_main_tls.sh
```

严格检查：

```bash
hy2_doctor.sh --strict
```

主节点链接：

```bash
cat /root/hy2_main_url.txt
```

Base64 订阅：

```bash
cat /root/hy2_main_subscription_base64.txt
```

链接默认为 v2rayN 可导入的 `hy2://` 格式。

#### 4. 显式升级 Hysteria 核心

普通重装不会自动升级核心。需要升级时明确执行：

```bash
HYSTERIA_FORCE_UPDATE=1 HYSTERIA_VERSION=vX.Y.Z \
  bash /root/onekey_hy2_main_tls.sh
```

升级到官方当前稳定版：

```bash
HYSTERIA_FORCE_UPDATE=1 HYSTERIA_VERSION=latest \
  bash /root/onekey_hy2_main_tls.sh
```

升级过程会：

- 备份旧核心
- 使用候选核心加载主节点及全部临时节点配置
- 记录升级前活跃服务
- 重启并验证服务状态与 UDP 监听
- 任一步失败时恢复旧核心和原有服务

生产使用建议固定明确版本，并按需配置 `HYSTERIA_BINARY_SHA256`。

</details>

<a id="wg-nat"></a>

<details>
<summary>6.2 WG-NAT：完整五步部署、NAT 管理和 Peer 探测</summary>
### WG-NAT 完整部署

WG-NAT 让 HY2 VPS 上指定的临时节点通过另一台机器的公网 IPv4 出口访问互联网。

#### 1. 初始化 NAT 出口机

把 `nat.sh` 放到 **NAT 出口机** 的 `/root/nat.sh`，执行：

```bash
chmod 700 /root/nat.sh
bash /root/nat.sh version
bash /root/nat.sh init
```

无法自动识别公网网卡时：

```bash
WAN_IF=eth0 bash /root/nat.sh init
```

#### 2. 配置 HY2 VPS

在 **HY2 VPS** 执行：

```bash
chmod 700 /root/vpswg.sh
bash /root/vpswg.sh
```

脚本会输出 VPS WireGuard 公钥。以后查询：

```bash
cat /etc/wireguard/wg-nat.pub
```

#### 3. 在 NAT 机添加 VPS

在 **NAT 出口机** 执行：

```bash
bash /root/nat.sh add hy2 hy2.example.com '这里替换成VPS的WireGuard公钥'
```

三个参数分别是：

- `hy2`：Peer 名称，例如 `vps1`、`hk1`
- `hy2.example.com`：HY2 VPS 的公网 IPv4 域名或 IPv4 地址
- 第三个参数：VPS 的 WireGuard 公钥

NAT 机会自动分配 WG 地址，并打印 VPS 回填命令。

同名再次执行 `add` 会保留原 WG 地址，只更新 Endpoint 或公钥。

#### 4. 回到 VPS 执行回填命令

原样执行 NAT 机输出的命令。例如：

```bash
/usr/local/sbin/wg_nat_set_peer.sh 'NAT机WireGuard公钥' '10.66.66.1/32'
```

然后检查出口：

```bash
/usr/local/sbin/wg_nat_healthcheck.sh
```

出现下面结果后再继续：

```text
OK EXIT_IP=x.x.x.x
```

#### 5. 安装 WG-NAT 临时节点工具

在 **HY2 VPS** 执行：

```bash
chmod 700 /root/natjichang.sh
bash /root/natjichang.sh
bash /root/natjichang.sh check
```

### NAT 出口机日常命令

```bash
# 查看 Peer
bash /root/nat.sh list

# 查看整体状态
bash /root/nat.sh status

# 更新同名 Peer
bash /root/nat.sh add hy2 hy2.example.com '新的VPS公钥'

# 删除 Peer
bash /root/nat.sh del hy2

# 查看版本
bash /root/nat.sh version
```

> **注意组件版本**：本轮真机的 VPS WireGuard 配置曾使用 `jieshu/vpswg.sh`，HY2 和 NAT 节点来自 `ceshi`；两个仓库的 WG 脚本版本存在差异。本次兼容成功不等于任意版本都能混装。以后从新机器安装尽量使用 `ceshi` 同一版本的四个脚本，**不要为了对标覆盖当前已正常工作的 WG 接口配置**。

### 关于复用 `jieshu` 的 VPS WireGuard

这台 Debian 13 测试机实际使用过 `jieshu/vpswg.sh`，并完成 HY2 NAT 出口、断线防泄漏与 VPS 重启恢复测试。**它在当前环境能正常运行。** 但 `ceshi/vpswg.sh` 已将临时节点目录和管理锁替换成 HY2 路径，并增加旧 fwmark 切换时的保护；无差别覆盖会失去这些适配，不建议为追求代码看起来相同而重装现有 WG。

### NAT 机单 Peer 主动探测

**已移植到 `ceshi/nat.sh`**：与 `jieshu` 一样，针对**单个 Peer**发送普通 WireGuard 数据探测包，不重启 `wg-quick`、不更改密钥、Endpoint、路由或其他 Peer。以下命令均在 **NAT 出口机**执行，先用 `list` 确认名称与公钥、AllowedIPs 对应关系：

```bash
bash /root/nat.sh list
bash /root/nat.sh probe-once hy2
bash /root/nat.sh probe-status hy2
bash /root/nat.sh probe-enable hy2
bash /root/nat.sh probe-disable hy2
```

- `probe-once` 发送一次普通数据探测，日志 `WG_PROBE_SENT` 表示尝试发包，**不保证对方已收到或立刻握手成功**，还需 `wg show wg-exit latest-handshakes` 核验。
- `probe-enable` 创建/启用该 Peer 的 systemd timer，通常 10 秒发一次，**不是每 10 秒必然加密重握手**；`probe-disable` 只停该 Peer 的 timer。
- `nat.sh add` 在完成 Peer 提交后尝试自动开启探测；缺少 `ping` 时只警告，不回滚已可用的 WG。旧 Peer 需手动 `probe-enable`，`del` 成功后尝试停止其探测。
- 该改动尚未在 NAT 出口机进行新的 Peer 探测真机验收；不要为取得这一功能就对已有 WG 运行 `nat.sh init/add`。先在 NAT 机**只更新管理脚本文件**、核对 Bash 语法与 Peer 对应关系，再运行 `probe-once` / `probe-status`。


</details>

<a id="daily-install"></a>

<details>
<summary>6.3 HY2 每日流量：独立安装 / 更新</summary>

在已安装 HY2 管理器的 **HY2 VPS** 上使用 root。首次安装和后续更新使用同一命令，安装后**无需重启 Hysteria 或重新创建节点**：

```bash
curl -fL --retry 3 https://raw.githubusercontent.com/liucong552-art/ceshi/main/traffic.py -o /root/hy2_traffic.py
python3 -m py_compile /root/hy2_traffic.py
python3 /root/hy2_traffic.py --install
```

安装器将创建 `/usr/local/sbin/hy2_traffic`、`hy2-traffic.timer`、`hy2-traffic.service` 和 `hy2-traffic-shutdown.service`。核对状态和历史：

```bash
systemctl is-active hy2-traffic.timer hy2-traffic-shutdown.service
systemctl list-timers --all hy2-traffic.timer
hy2_traffic
hy2_traffic main
hy2_traffic 40020 --json
```

`hy2-traffic.service` 是定时运行的 oneshot，完成后显示 inactive 属正常情况。`hy2-traffic-shutdown.service` 的 `RemainAfterExit=yes` 使正常关机时能够执行最后一次采集。定时器与关机服务由独立安装器启用；它们不修改原有 `pq-save.timer` 或 `hy2-managed-shutdown-save.service`。

</details>

<a id="portbw-install"></a>

<details>
<summary>6.4 portbw：完整首次安装、受管理版本升级与旧版本迁移</summary>

### 安装 / 更新 portbw（需要同时下载两个文件）

**运行位置：需要限速的服务器。首次安装和升级分开操作。**

1. 仅限没有旧 portbw 的新安装环境，先安装依赖（修改系统）：

```bash
apt-get update && apt-get install -y curl ca-certificates python3 iproute2 nftables util-linux coreutils procps
```

依赖安装成功后，下载两个配套文件；以下命令仅下载，不安装、不创建限速策略：

```bash
curl -fsSL 'https://raw.githubusercontent.com/liucong552-art/ceshi/main/portbw-install.sh' -o /root/portbw-install.sh && \
curl -fsSL 'https://raw.githubusercontent.com/liucong552-art/ceshi/main/portbw.py' -o /root/portbw.py
```

两次下载均成功后再继续。已有 portbw 的主机应看下面的升级说明，不要执行新安装流程。

2. 预检环境和配套源码（不安装 portbw）：

```bash
bash /root/portbw-install.sh check
```

**只在出现 `CHECK PASS` 后继续。** 若提示已有安装，不要删旧文件来强行通过，改看下面的升级 / 迁移说明。

3. 执行实际安装（修改系统）：

```bash
bash /root/portbw-install.sh install
```

应出现 `INSTALL PASS` 和 `VERIFY PASS`；仅下载文件、语法检查或运行 `check` 都不会完成安装。首次安装不会自动创建端口限速策略。

4. 核对安装结果：

```bash
command -v portbw
bash /root/portbw-install.sh verify
systemctl is-active portbw-watch.service
portbw report
portbw audit
```

命令路径应为 `/usr/local/sbin/portbw`，Watchdog 通常为 `active`；尚未设置端口时列表为空属于正常情况。核对通过后，选择后面的静态设置或自动设置。

如预检要求指定物理网卡，先用 `ip -4 route get 1.1.1.1` 查看，再在 `check` 和 `install` 后分别加 `--iface eth0`，把 `eth0` 换成实际物理网卡名；不要根据示例猜网卡。

<details>
<summary>已有受管理版本：升级方法</summary>

**升级已由本系列通用安装器管理的已知 v1/v2/候选版：** 先按上方两条下载命令重新下载同一版安装器和源码，先记录当前的 `portbw report` 与 `portbw audit`，保存可恢复的 VPS 快照，再在维护窗口执行（不要重新执行首次安装 `install`）：

下面三步用 `&&` 串联，前一步失败时不会继续下一步；预检失败时先处理原因，不能跳过：

```bash
bash /root/portbw-install.sh preflight-upgrade && \
bash /root/portbw-install.sh upgrade && \
bash /root/portbw-install.sh verify
```

全部成功后核对：

```bash
portbw report
portbw audit
```

首次安装的 `check` 要求干净环境，已有受管理安装要用 **`preflight-upgrade`**。升级工具只接受有 `/var/lib/portbw-resident-release/current.env` 管理记录，且已安装源码 SHA 与记录一致的受支持旧版；未知来源的旧版即使已有端口配置也**不允许直接覆盖或伪造管理记录**。安装器会持久保存备份，但不能取代 VPS 快照。不要执行 `nft flush ruleset`、`tc qdisc del ... root`，不要停止其他业务的 nft/tc 系统规则。

</details>

<details>
<summary>其他旧仓库 / 无管理记录版本：迁移说明</summary>

**其他旧仓库/无管理记录版本的迁移（不能直接 `install`/`upgrade`）：** 特别是 `zuizhongheji` 等独立旧版及使用旧 tc flower 槽位的机器，先记录每个端口的**基础上/下行、降速上/下行、触发百分比、持续秒数、滚动保护、冷却**，执行 `portbw report` 与 `portbw audit` 并制作 VPS 快照。只可在维护窗口按照确切旧版校验过的迁移工具或逐端口安全删除方法移除旧限速，再安装此通用版并重新添加规则；在删除到重建期间可能暂时失去限速保护。不要把普通安装器用于未知来源旧版本，更不要手动清空整个 nftables ruleset、网卡 root qdisc 或其他业务规则。

</details>

</details>



<a id="socks5"></a>

<details>

<summary>6.5 SOCKS5：独立协议，不属于 HY2 五文件版</summary>

本项目不安装 SOCKS5 账号管理。

</details>

<a id="maintenance"></a>

<details>

<summary>6.6 HY2 / WG-NAT / NAT / portbw 的更新方法</summary>

更新 `hy2.sh` 只部署 HY2 管理组件，不会自动更新 VPS 侧 WG、NAT 机的 `nat.sh` 或独立限速 `portbw`。GitHub `main` 更新不会自动修改现有 VPS 文件。重要升级前建议创建可恢复快照，并在维护窗口逐项核对。

已由本系列托管安装的 `portbw` 应执行 `preflight-upgrade → upgrade → verify`，**不能再按首次安装的 `install` 覆盖**。WG 正常运行时不要为对标反复执行 `nat.sh init/add`。

</details>

<a id="abuse-install"></a>

<details>
<summary>6.7 HY2 访问与异常审计：独立安装和实时 Stats API（可选）</summary>

在 HY2 VPS 以 root 安装只读命令；**不会重启 Hysteria、修改服务配置或启动后台采集器**：

```bash
curl -fL --retry 3 https://raw.githubusercontent.com/liucong552-art/ceshi/main/hy2-abuse.py -o /root/hy2-abuse.py
python3 -m py_compile /root/hy2-abuse.py
install -m 755 /root/hy2-abuse.py /usr/local/sbin/hy2-abuse

PORT=40020 DAYS=7 hy2-abuse
hy2-abuse 40020 --days 7 --json
```

可选：**只有你已在 Hysteria 配置里自行启用 `trafficStats`、并仅监听本机回环地址**时，才能额外读取当前 TCP 连接目标：

```bash
# secret 文件路径为示例，务必 600 权限且由 root 保管
hy2-abuse 40020 --days 7 \
  --stats-url http://127.0.0.1:9999 \
  --secret-file /root/hy2-stats-secret
```

本程序不会代你创建 API 服务或暴露公网端口，也不保存 API 密码。只允许字面量 `127.0.0.1` 或 `[::1]`，拒绝远程地址、HTTP 跳转和代理转发。使用 `--secret-file` 前，需确保该文件与配置中的 API secret 一致且为 root 私有。

`/online` 和 `/dump/streams` 返回的是**当前**在线与 TCP 流，不是 7 天的访问域名历史；`DAYS` 只作用于 journald 异常事件。不要把这个工具的汇总当作自动处罚依据。

</details>

<a id="backup-recovery"></a>

<details>

<summary>备份位置与回退方法</summary>

操作前建立可恢复的整机快照。GitHub ZIP 不包含运行中的节点、内核规则和流量状态。

| 组件 | 默认位置 | 说明 |
|---|---|---|
| HY2 主节点及证书 | `/etc/default/hy2-main`、`/etc/hysteria/` | 包含密钥 |
| HY2 配额/IP/节点状态 | `/var/lib/hy2/` | 包含节点密码 |
| WireGuard | `/etc/wireguard/` | 两端分别备份，包含私钥 |
| portbw | `/etc/portbw/`、`/var/lib/portbw/`、`/var/lib/portbw-resident-release/` | 独立的限速配置和备份 |

<details>

<summary>受管理 portbw 专用回滚</summary>

```bash

bash /root/portbw-install.sh rollback

```

仅适用于当前安装器认可的托管记录和有效备份；回滚后仍需执行 `portbw audit` 和客户端连接测试。

</details>

</details>

<a id="release-notes"></a>

<details>

<summary>版本说明与看门狗运行规则</summary>

2026-11-04 Debian 13 真机已验证 HY2 主/临时节点、双向配额、来源 IP 限制、到期清理、WG-NAT 出口、断线防泄漏、VPS 重启恢复和 `portbw` 静态 10/10 Mbps。**动态 40→18 Mbps 的持续触发、保护、恢复及其他未测场景不在本次通过范围。**

首次安装后配额重启归零问题由 [PR #3](https://github.com/liucong552-art/ceshi/pull/3) 修复；通用 portbw 与 jieshu 源码一致，由 [PR #5](https://github.com/liucong552-art/ceshi/pull/5) 原样引入。

### 看门狗与生产运行规则

- **HY2 临时节点 Watchdog 72 小时宽限：** 第三次连续恢复失败后，才开始一次持久化的 72 小时宽限期。系统重启/手工重启服务不重置 `WATCHDOG_GRACE_START_EPOCH`；只有服务、端口监听、全部配额/IP 防护，且 NAT 节点的 WG 路由/握手均被重新核实为健康，才清空失败次数和宽限计时。
- **宽限期间仍然 fail-closed：** 若缺失 nft 防护，停掉监听并保留节点；若 WG 无有效握手，恢复失败的 NAT 节点不允许继续监听，不能改走 VPS 公网。
- **到期处理：** 自然 TTL 到期按原规则正常删除；72 小时宽限结束后，只有再次恢复失败且本次启动已稳定至少 300 秒，才执行最终节点清理。这与 `jieshu` 的持久宽限理念一致，但**尚需在专用临时节点上做跨重启/超过 72h 或模拟时钟的验收**，GitHub CI 不等于真实故障链路。
- `portbw` 独立于 HY2 临时节点生命周期；删除节点不自动删限速。
- `nat.sh init/add/del` 可能重启 NAT 机 WG，影响其他 Peer。

</details>

<a id="acceptance"></a>
### Debian 13 真机验收与 `jieshu` 功能对标

[**完整实测记录：2026-11-04 Debian 13**](tests/REAL_MACHINE_ACCEPTANCE_2026-11-04.md)详细列出每个步骤、通过依据和未测试项。实测包括：普通临时节点及双向配额、来源 IP 上限、到期清理；一次真正发现的**首次关机配额持久化问题**（已通过 [PR #3](https://github.com/liucong552-art/ceshi/pull/3) 修复）；WG-NAT 客户端真实出口、断线阻断、恢复及整机重启后配额保留。

收尾时 `hy2_doctor.sh --strict` 为 **0 错误、1 警告**；警告是未设置 Hysteria 二进制 SHA256 校验。NAT 节点刚启动时允许因 WG 握手未就绪而安全拒绝监听，随后自动重试成功；不应把这一启动时序当作持续故障。

<a id="feature-gap"></a>
### 仍缺少哪些 `jieshu` 管理能力？

| 功能 | `ceshi` 当前状态 | 推荐处理 |
|---|---|---|
| Hysteria 2 主/临时节点、配额、IP 限制、到期管理 | 已有；主要路径已实测 | 维持稳定 |
| WG-NAT 出口、防泄漏与 VPS 重启恢复 | 已有；本次实测 | 不盲目覆盖 WG 脚本 |
| 按端口每日流量历史统计与导出 | **已移植并真机验证**：HY2 UDP 双向字节、原配额一致、正常重启前后保留且 0 字节重复计数 | 剩余：跨北京时间午夜、突然断电、30 天裁剪及端口复用 |
| 静态端口限速、持续流量高峰自动降速（`portbw`） | 原版模块已收录；**静态 10/10 Mbps 真机测速通过**，自动触发/恢复尚未测试 | 下一步单独验收自动模式与恢复 |
| 端口暂停、来源 IP/CIDR 黑名单 | **真机通过**：端口暂停/恢复、来源 IP `/32` 和 CIDR `/24` 封禁/解封；独立 nft 表正常重启持久化，HY2/WG 均正常 | 尚待补充异常断电、过期端口生命周期等边界测试 |
| 访问目标、共享嫌疑、异常连接审计 | **已新增 `hy2-abuse.py` 只读审计**：来源 IP 占位、最近警告/错误、可选 Stats API 当前 TCP 目标 | 尚需 HY2 VPS 读取真实日志与可选 API 的真机验收；不宣称提供历史 UDP 目标 |
| NAT 端 `probe-once` / `probe-enable` / `probe-disable` / `probe-status` | **已移植到 `nat.sh`**，CLI/按 Peer timer/自动 enable 与删除停用已实现 | 待 NAT 机真机验证：不打断其他 Peer、恢复连接、重启后 timer |
| SOCKS5 账号、VLESS Reality 特定管理 | 不属于 HY2 五文件版 | 不应直接照搬 |

`vpswg.sh` 也已是对 `jieshu/vpswg.sh` 的 HY2 适配版：大部分代码相同，但临时节点目录、锁和旧标记切换时的 fail-closed 保护不能直接替换回 VLESS 版本。本轮已在 jieshu 的 VPS WG 配置与 HY2 节点组合下完成真实出口、断线阻断和 VPS 重启恢复；不代表所有版本混用都受支持。

对标目标是达到类似的管理体验和验证标准，不承诺把其他协议模块直接塞进 HY2。新增功能都应独立 PR、CI 和指定真机测试。

### 一键批量真机回归（仅 `helan` 测试机）

以前每项单独复制命令的验收方式已新增自动化：`tests/hy2_batch_acceptance.py`。在 **Debian 13 测试机 `helan`**、root 下运行一次，即可自动完成既有服务、doctor、systemd timers、北京时间流量 JSON、WG 打标路由、只读异常审计，以及一枚**新生成、独立、随机名**的 NAT 临时节点的端口 pause/resume、文档 IP `/32` 与 CIDR `/24` 封禁/解封、nft guard `restore`、30 天周期配额保持测试与资源清理。源 IP 封禁只使用 `198.51.100.7/32` 和 `203.0.113.0/24` 文档保留网段，不封真实客户端。普通业务节点不作变更。

```bash
curl -fsSL --retry 3 \
  https://raw.githubusercontent.com/liucong552-art/ceshi/main/tests/hy2_batch_acceptance.py \
  -o /root/hy2_batch_acceptance.py && \
python3 -m py_compile /root/hy2_batch_acceptance.py && \
python3 /root/hy2_batch_acceptance.py --live
```

完成后终端打印各项 `PASS` / `FAIL` / `SKIP`、`FINAL_RESULT`、`CLEANUP` 和本次 `/root/hy2-batch-accept-*/report.json` 的准确路径。**仅分享该脱敏 JSON 报告**，不要上传测试目录内的 `*.raw.log`：创建日志包含测试节点密码和 HY2 链接。测试器退出时自动清理节点和专用 nft 规则；同时在运行前先安装**跨重启仍有效、30 分钟后执行一次**的 systemd cleanup 安全计时器。正常清理成功时连同计时器一并移除；遇到清理错误时保留计时器兜底，终端会提示。根目录下仅留脱敏报告和 root 私有原始审计日志。

不带 `--live` 时仅执行只读自检，不会创建节点：`python3 /root/hy2_batch_acceptance.py`。脚本明确拒绝在非 `helan` 的机器上执行，且从未自动重启 VPS、NAT 出口机或 WireGuard。自动化规则检查**不能替代**前面已完成的 v2rayN 真实断网/恢复证据；异常断电、真实 30 天事件、IPv6 与跨机故障注入仍需单独隔离验收。本脚本 GitHub Actions 单元测试通过并不等于实际 VPS 验收，须以脚本在真实 `helan` 的 `report.json` 为准。

**2026-11-05 首次批量真机执行通过**：`helan` 控制台记录 12 项 PASS、0 FAIL，包含独立测试 NAT 节点创建、文档源网段封禁/解封、保留 30 天锚点的配额重设及自动删除；末尾 `FINAL_RESULT=PASS`、`CLEANUP=PASS`。此结论依据操作者控制台记录，未另行收取 `report.json` 本体，也不等同于客户端断网测试或重启故障注入。


### 第二轮一键自动维护验收（`--maintenance`）

和第一轮 `--live` 不同，第二轮集中检验 **节点真实到期清理、Watchdog 健康巡检与 `portbw` 自动限速策略的空闲态配置/审计/撤销**，而不是重复已经验收过的暂停、配额重设。

在 **仅允许 `helan` 的测试机**用下面一条命令运行：

```bash
curl -fsSL --retry 3 \
  https://raw.githubusercontent.com/liucong552-art/ceshi/main/tests/hy2_batch_acceptance.py \
  -o /root/hy2_batch_acceptance.py && \
python3 -m py_compile /root/hy2_batch_acceptance.py && \
python3 /root/hy2_batch_acceptance.py --maintenance
```

此模式先完成主服务、doctor、WG、guard 和 timer 预检，再建立 **180 秒有效期**的随机 HY2-NAT 临时节点，检查仅健康状态下的 Watchdog 巡检、尝试对其配置 `portbw auto` 基础 5/5 Mbps 与降速 2/2 Mbps 并做 audit/status/off/del，最后**等待真实 TTL 到期、服务和配额/IP 限制元数据自然清理**。所有创建动作前均安装 30 分钟后的一次性、跨重启持久安全清理 timer；失败或退出时立即强制清理本次拥有的资源。一次自然到期通常需等待约 3 分钟；若没能自然清理则记为 FAIL，不会将强制清理冒充通过。此模式绝不主动停止主服务或 WireGuard、不改生产机。

**测试范围界限：** Watchdog 只在没有其他临时节点时主动跑健康巡检，有其他节点会 SKIP，以免触碰其他测试或业务；故障恢复/连续三次失败后 72 小时宽限的真实故障注入**不在这轮**。`portbw` 仅在已安装、tc 模式启用、监控服务运行、且无其他受管端口策略的安全前提下执行；否则 SKIP，不会为了测试擅自安装或升级限速器。空闲态自动策略正常不等于**持续高流量真实触发降速与恢复已通过**；后者需单独受控压测。输出的 `report.json` 有每项 PASS/FAIL/SKIP 和清理状态，只分享脱敏 JSON，绝不分享 `*.raw.log` 或 HY2 节点链接。

### 仍需后续实测的场景

下面是完整参考验收清单，**并非 12 项都已完成**；IPv6、30 天周期、NAT 机本身重启、真实续证、核心升级回滚、异常断电及 nftables 全表冲刷均未做完。

建议先在一台全新 HY2 VPS 和一台全新 NAT VPS 测试：

1. `hy2_doctor.sh --strict` 无错误
2. 主节点能导入 v2rayN 并连接
3. 主节点出口 IP 是 HY2 VPS
4. 普通临时节点能连接并按时间自动删除
5. 达到 `PQ_GIB` 后端口停止转发
6. 超出 `IP_LIMIT` 后新来源 IP 被拒绝
7. IPv6 临时节点可连接，IPv4/IPv6 不串入
8. WG-NAT 临时节点出口 IP 是 NAT 机
9. 停止 `wg-quick@wg-nat` 后，NAT 节点断流且不泄漏 VPS 本机出口
10. 清空 HY2 nftables 表后，watchdog 能恢复或停止节点
11. HY2 VPS 与 NAT 机重启后，Peer、路由、配额和临时节点状态恢复
12. 模拟证书续期后，主节点和未到期临时节点正常重载

### 适用边界与安全

- 只面向全新机器
- 不迁移旧 Hysteria、旧 WireGuard 或旧 nftables 状态
- 主节点固定为 IPv4 UDP `443`
- 不提供一键卸载命令
- 不应与其他会频繁重建相同 nftables、iptables 或策略路由的管理脚本混用
- 在真实生产使用前，必须完成上一节的真机验收

仅在自己拥有或已获明确授权的服务器和网络上使用，并遵守当地法律和服务商条款。

- 本次只验证 Debian 13 的指定服务器与客户端条件，不代表所有系统和网络已通过。
- 任何 `hy2://` 链接及 `/etc/wireguard` 密钥均视为敏感凭据，不应放入 GitHub、公开问题单或原始验收日志；公开记录采用匿名摘要。
- 临时节点到期自动回收。此次 UDP/40020 是限时测试节点，正式服务应重新生成节点及凭据并设定实际 TTL。

---

`ceshi` README 列出核心 HY2 命令与可选独立 `portbw` 模块；未实现的 `jieshu` 功能不会伪装为已安装命令。
