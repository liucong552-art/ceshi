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

<summary>HY2 每日流量：现有数据与尚未实现的历史记录</summary>

当前没有与 `jieshu/traffic.py` 对应的 HY2 按自然日记录、30 天历史或 JSON 导出。`pq_audit.sh` 是累计配额，不是每天用量。

```bash

pq_audit.sh

hy2_audit.sh

```

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

### HY2 访问目标、共享嫌疑审计

`hy2_audit.sh` 只显示节点状态、配额、来源 IP 槽位；没有与 `jieshu/vless-abuse` 等效的目标域名日志分析器，不要运行虚构的 `hy2-abuse`。

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

<a id="portbw"></a>
### 独立端口限速 `portbw`（可选，直接引入自 `jieshu`）

`portbw` 与节点类型无关，在实际运行 HY2 的 **VPS 公网物理网卡**上，按服务器监听端口对 **TCP+UDP 合计**流量限制速率。对 NAT 临时节点也是限制 VPS 侧入站/出站 UDP 40020，不需要在 NAT 机对 WG 封装端口 51820 限速。**Mbps 是兆比特每秒，和配额 MiB/GiB 不是同一种单位。**

```bash
# 示例：UDP 40020 上传 10 Mbps、下载 20 Mbps；接口示例为 eth0
portbw set 40020 10 20
portbw show 40020
portbw report
portbw audit

# 仅修改上传/下载；0 表示本方向不限速
portbw up 40020 15
portbw down 40020 0

# 自动模式：基础上/下 40 Mbps，100% 持续 45 秒后降为 18 Mbps
# 至少保护 120 秒，恢复后冷却 0 秒
portbw auto set 40020 --up 40 --down 40 --trigger 100 --after 45s --auto-up 18 --auto-down 18 --hold 120s --cooldown 0s
portbw auto status 40020
portbw auto off 40020

# 删除限速规则，不删除 HY2 节点
portbw del 40020
```

**使用边界：**

- 必须先按[安装段落](#portbw-install)单独安装；`hy2.sh` 不会自动安装、升级或调用 `portbw`。
- `portbw` 拥有独立的 `inet pbw_policy` 表及 `tc` 规则，不会修改 `hy2_pq` 或 `hy2_iplimit` 表。与 HY2 组合后的实际限速、配额交互和重启恢复**仍需真机实测**，本次仅复制上游源码及静态测试。
- **端口规则与节点生命周期独立**：HY2 到期/删除节点不会自动删除 `portbw` 限速；端口以后重用时仍可能被旧限速约束。清理旧端口时应明确执行 `portbw del <端口>`。
- 下载限速和自动降速需要默认的 `tc` 模式；**不要选择 `--nft-only`** 来实现双向限速。安装器不会覆盖已有未知 `portbw` 或其他 tc 规则，检查失败时应先诊断，不能强制清空系统 qdisc。

<a id="troubleshooting"></a>
## 四、故障排查

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
## 五、删除与清理

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

<a id="install"></a>
## 六、安装与更新

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

<a id="portbw-install"></a>
### 可选组件：安装通用 `portbw`（直接来自 `jieshu`）

`ceshi` 原有**五个核心文件不变**，另提供两个独立的可选文件：`portbw.py` 和 `portbw-install.sh`。它们与 `jieshu/main` 对应版本**逐字相同**，安装器内的 `EXPECTED_SHA` 已与脚本原文计算值核对。只在真正需要端口限速的 **HY2 VPS** 安装；目前没有在本次服务器上对限速执行过真实流量测试。

首次安装时，在 HY2 VPS 运行（示例公网网卡为 `eth0`，请按真实网卡调整）：

```bash
apt-get update && apt-get install -y --no-install-recommends python3 iproute2 nftables
curl -fL --retry 3 https://raw.githubusercontent.com/liucong552-art/ceshi/main/portbw.py -o /root/portbw.py
curl -fL --retry 3 https://raw.githubusercontent.com/liucong552-art/ceshi/main/portbw-install.sh -o /root/portbw-install.sh
chmod 700 /root/portbw.py /root/portbw-install.sh
bash -n /root/portbw-install.sh
bash /root/portbw-install.sh check --iface eth0
bash /root/portbw-install.sh install --iface eth0
bash /root/portbw-install.sh verify
portbw report
```

**先 `check` 再 `install`**：若检查失败说明有环境、依赖、已有端口规则、`tc` 或安装记录冲突；不要用清表、覆盖现有安装等方式绕过。上面 `install` 是修改系统的操作，会安装独立的 `portbw` 服务和限速状态管理。

已经存在此系列的托管安装，**不要再次运行首次安装**。可以先使用 `bash /root/portbw-install.sh preflight-upgrade --iface eth0`，通过后按照工具说明执行 `upgrade`。升级脚本有完整 SHA 与托管记录校验及回滚，不能直接替换现有运行中的 `portbw.py`。

`portbw` 的日常 [设置、自动降速和清理](#portbw) 在上文，安装后以 `portbw audit` 校验内核规则；如不需要限速，不必安装该可选模块，也不会增加常驻进程。

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

<a id="wg-nat"></a>
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

<a id="acceptance"></a>
## 七、Debian 13 真机验收与 `jieshu` 功能对标

[**完整实测记录：2026-11-04 Debian 13**](tests/REAL_MACHINE_ACCEPTANCE_2026-11-04.md)详细列出每个步骤、通过依据和未测试项。实测包括：普通临时节点及双向配额、来源 IP 上限、到期清理；一次真正发现的**首次关机配额持久化问题**（已通过 [PR #3](https://github.com/liucong552-art/ceshi/pull/3) 修复）；WG-NAT 客户端真实出口、断线阻断、恢复及整机重启后配额保留。

收尾时 `hy2_doctor.sh --strict` 为 **0 错误、1 警告**；警告是未设置 Hysteria 二进制 SHA256 校验。NAT 节点刚启动时允许因 WG 握手未就绪而安全拒绝监听，随后自动重试成功；不应把这一启动时序当作持续故障。

<a id="feature-gap"></a>
### 仍缺少哪些 `jieshu` 管理能力？

| 功能 | `ceshi` 当前状态 | 推荐处理 |
|---|---|---|
| Hysteria 2 主/临时节点、配额、IP 限制、到期管理 | 已有；主要路径已实测 | 维持稳定 |
| WG-NAT 出口、防泄漏与 VPS 重启恢复 | 已有；本次实测 | 不盲目覆盖 WG 脚本 |
| 按端口每日流量历史统计与导出 | **未实现** | P1：按 HY2 UDP 双向计数与重启持久化设计 |
| 静态端口限速、持续流量高峰自动降速（`portbw`） | **可选模块已原样收录**，不随 HY2 自动安装，尚未在 HY2 VPS 验证实际吞吐 | 下一步：按上游安装器预检、真机测试 UDP 上传/下载、配额和重启恢复 |
| 端口暂停、来源 IP/CIDR 黑名单 | **未实现**；当前只有 IP 槽位上限 | P2：先设计防火墙规则归属与恢复 |
| 访问目标、共享嫌疑、异常连接审计 | **未实现** | P2：只能使用 HY2 实际可采集的数据，不能假定有 VLESS 日志字段 |
| NAT 端 `probe-once` / `probe-enable` / `probe-disable` / `probe-status` | `ceshi/nat.sh` 暂无对应命令 | P2：按多 Peer、fail-close、安全场景单独评估 |
| SOCKS5 账号、VLESS Reality 特定管理 | 不属于 HY2 五文件版 | 不应直接照搬 |

`vpswg.sh` 也已是对 `jieshu/vpswg.sh` 的 HY2 适配版：大部分代码相同，但临时节点目录、锁和旧标记切换时的 fail-closed 保护不能直接替换回 VLESS 版本。本轮已在 jieshu 的 VPS WG 配置与 HY2 节点组合下完成真实出口、断线阻断和 VPS 重启恢复；不代表所有版本混用都受支持。

对标目标是达到类似的管理体验和验证标准，不承诺把其他协议模块直接塞进 HY2。新增功能都应独立 PR、CI 和指定真机测试。

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

## 八、适用边界与安全

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
