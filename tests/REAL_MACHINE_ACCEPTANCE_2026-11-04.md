# HY2 五文件版：Debian 13 真机验收记录（2026-11-04）

> 仓库：[`liucong552-art/ceshi`](https://github.com/liucong552-art/ceshi)；对标项目：[`liucong552-art/jieshu`](https://github.com/liucong552-art/jieshu)。本记录只描述实际进行的 HY2/WG-NAT 测试。**GitHub Actions 静态检查不能替代真实服务器验收。**
>
> **安全与隐私**：不收录 VPS/NAT 公网 IP、生产域名、节点 `hy2://` 链接、密码、WireGuard 私钥或原始终端日志。

## 测试环境与边界

- VPS：Debian GNU/Linux 13 (trixie)、systemd、x86_64、Linux 6.12.88 系列内核；约 1.1 GiB RAM。
- 客户端：Windows v2rayN，通过实际代理网页访问核对出口行为。
- HY2 管理器：`HY2 five-file edition 3.3.1`；服务端 Hysteria 2；主节点 IPv4 UDP/443。
- WireGuard：`wg-nat`，VPS 到独立 NAT 出口机；策略路由 `fwmark=2333 → table 100`，故障兜底为 `prohibit default`。
- 测试均发生于 **2026-11-04**。以下性能与连接结果仅代表这组主机、系统和网络条件，不是通用性能保证。
- NAT 机器配置由操作人员自行完成；本轮**未重启 NAT 出口机本身**。

## 已通过：有真机观察依据

| 模块 | 场景 / 验收信号 | 结果 |
|---|---|---|
| HY2 管理器 | 安装返回 `MANAGER_EXIT_CODE=0`；生成管理命令及 systemd timer | 通过 |
| 主节点 | 域名 A 记录指向 VPS 后部署 `MAIN_EXIT_CODE=0`；`hy2.service=active`，UDP/443 监听，正式 TLS 证书有效 | 通过（服务端） |
| 普通临时节点 | IPv4 UDP/40000 等临时节点创建成功、可在 v2rayN 使用 | 通过 |
| 总流量配额 | `PQ_GIB=0.05` 对应 **51.20 MiB**，耗尽后 nftables UDP 双向阻断；客户端无法继续连接 | 通过 |
| 流量计数 | UDP 双向 nftables 原始 counter 合计约 **108.75 MiB**，与 `pq_audit.sh` 一致 | 通过（未做独立网络字节校准） |
| 来源 IP 限制 | `IP_LIMIT=1`、`IP_STICKY_SECONDS=600`；第二个独立来源 IP 被阻断 | 通过 |
| 到期回收 | 90 秒 TTL 节点到期后元数据清除，UDP/40001 端口释放 | 通过 |
| 单临时服务重启 | 单个普通临时节点重启 `RESTART_EXIT=0`，无新错误 | 通过 |
| 正常整机重启后配额 | 修复后：重启前 **110.83 MiB**，重启后仍 **110.83 MiB**；关机保存服务正常执行 | 通过 |
| HY2 → WG-NAT | IPv4 入站 UDP/40020，`bindDevice: wg-nat`；客户端通过 NAT 机公网 IPv4 出口访问网页 | 通过 |
| WG fail-closed | VPS 侧暂时停止 `wg-quick@wg-nat` 时，table 100 保留 `prohibit default`，标记路由拒绝；v2rayN 使用 NAT 节点无法联网 | 通过（所测场景） |
| WG 恢复 | WireGuard 恢复后客户端出口仍为同一 NAT IPv4，没有切回 VPS 公网出口 | 通过 |
| VPS 整机重启恢复 NAT 节点 | `hy2.service`、`wg-quick@wg-nat.service`、`hy2-temp-nat-live4.service` 自动恢复；v2rayN UDP/40020 能联网 | 通过 |
| NAT 节点配额重启持久化 | 重启前 **85.45 MiB**，重启后 **85.47 MiB**（正常新增少量流量），总限额 500 MiB 不变 | 通过 |
| 临时节点清理 | 普通实验节点清理后，仅主节点和 NAT 试验节点留存；`hy2_doctor.sh --strict` **0 错误、1 警告** | 通过 |
| 资源 | 收尾时主/临时 Hysteria 各约 28–29 MiB RSS、可用内存约 864 MiB、swap 使用 0 B | 通过（观察值） |

## 实际发现并修复的缺陷

**首次安装后关机保存服务只 `enable`、未 `start`。**

- 修复前首次整机重启：UDP/40010 在 500 MiB 配额下已用约 **108.75 MiB**，重启后显示 **0 B**。
- 根因：`hy2-managed-shutdown-save.service` 的 `Type=oneshot` / `RemainAfterExit=yes` 需要首次安装即进入 active 状态，关机时才能执行 `ExecStop=/usr/local/sbin/pq_save_state.sh`；五分钟的 `pq-save.timer` 无法保证第一次重启前已经保存。
- 最小修复：`enable_units()` 增加 `systemctl start hy2-managed-shutdown-save.service`，不新增常驻循环。
- 仓库变更：[`ceshi` PR #3](https://github.com/liucong552-art/ceshi/pull/3)，已合并到 `main`（`893bb2dc`），含回归测试。
- 修复后真机重启：**110.83 MiB → 110.83 MiB**。证明正常关机重启路径的配额保留；**不意味着突发断电时绝不丢失近期计数**。

## 非缺陷的启动时序

VPS 重启后，NAT 节点在 WG 首次握手尚未就绪时，`hy2_nat_preflight.sh` 于 **08:15:15**、**08:15:18 UTC** 两次拒绝启动；**08:15:22 UTC** 自动重试成功，UDP/40020 监听；**08:15:33 UTC** 记录到客户端连接。说明防护先于对外监听生效。本轮不据此判断存在需要修复的脚本问题。

## 尚未实测，不能标记为通过

- IPv6 入站普通节点、IPv6 入站 + NAT IPv4 出口、地址族隔离的外部客户端验证（此 VPS 无公网 IPv6）。
- 有效期超过 30 天时的周期自动配额重置、手工重置后的周期管理。
- NAT **出口机自身**的断电/重启、WG Peer 变更/重复部署、长期运行稳定性。
- 突然断电、强制杀进程、完全清空 nftables 规则后的 fail-close 与 watchdog 恢复。
- ACME 证书真实续期、证书重载、Hysteria 核心升级及失败回滚。
- UDP/40020 NAT 节点的**独立第二来源 IP**上限测试（本次已配置 `IP_LIMIT=1`，但未单独再次验证）。
- 每日历史流量、访问与滥用审计、SOCKS5 管理等未纳入本次 HY2 实测；2026-11-04 初次验收时，`portbw` 也没有安装或开展限速实测。后续虽可选收录上游 `portbw` 原版源码，但不应追认本次已验证限速。

## 代码对标说明与后续优先级

| 优先级 | `jieshu` 中已有的能力 | `ceshi` 的差距 / 处理策略 |
|---|---|---|
| P1 | 按端口的每日历史流量、报表 | 当前无独立模块；需要 HY2 UDP 双向计数、持久化和自然日边界设计，另开实现与实测 |
| P1 | 静态端口限速、持续高负载自动降速恢复（`portbw`） | 后续已将 `jieshu` 的通用 `portbw` 作为独立可选文件原样收录；当前 HY2 UDP 与配额交互未真机测通 |
| P2 | 按端口暂停、来源 IP/CIDR 黑名单、访问/共享嫌疑审计 | 当前只有 IP 槽位上限；可先设计操作/恢复语义，严禁混同客户端流量和目标域名日志 |
| P2 | NAT 机 `probe-once` / `probe-enable` / `probe-disable` / `probe-status` | `ceshi/nat.sh` 尚无对应管理入口；须评估是否影响现有 Peer 和 fail-close，再单独移植验证 |
| 不直接照搬 | SOCKS5 账号体系、VLESS Reality 专用字段 | 协议不同，作为独立产品/模块而非 HY2 五文件版必要功能 |

以上为**源码能力对照**，不是上述缺失模块的通过性测试。未来每项代码变更应独立 PR + CI + 指定场景真机验证，避免破坏已经确认的 NAT 防泄漏和配额持久化。

## 当前仍需注意

- `hy2_doctor.sh --strict` 唯一持续警告：未配置 `HYSTERIA_BINARY_SHA256`，属于无法持续校验核心哈希，**不是运行故障**。
- 本轮 UDP/40020 是 **60 分钟有效期**的临时测试节点；测试时的状态不会无限保留。
- 终端原始记录曾出现完整 HY2 节点链接/密码。正式使用前应轮换已暴露凭据；**不要将原始日志、私钥或节点订阅提交到仓库**。
- 验收仅覆盖被明确执行的场景，不能推导“所有 Debian/Ubuntu 版本和所有出口方案已经通过”。
