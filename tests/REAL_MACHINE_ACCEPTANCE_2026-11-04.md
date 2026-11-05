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
- 每日历史流量、访问与滥用审计、SOCKS5 管理等未纳入首次 HY2 真机实测；2026-11-04 初次验收时，`portbw` 也没有安装或开展限速实测。后续虽可选收录上游 `portbw` 原版源码，但不应追认本次已验证限速。

## 代码对标说明与后续优先级

| 优先级 | `jieshu` 中已有的能力 | `ceshi` 的差距 / 处理策略 |
|---|---|---|
| P1 | 按端口的每日历史流量、报表 | 后续已经从 jieshu 移植 `traffic.py`（HY2 UDP 双向、30 天历史、JSON、systemd 每分钟/关机保存）；**未在原真机轮次完成新增组件验收** |
| P1 | 静态端口限速、持续高负载自动降速恢复（`portbw`） | `jieshu` 通用程序已原样收录；**静态 10/10 Mbps 的 HY2-NAT 配置、内核审计及客户端测速已通过**，自动限速触发和恢复仍待实测 |
| P2 | 按端口暂停、来源 IP/CIDR 黑名单、访问/共享嫌疑审计 | 后续新增 `hy2-guard.py`（受管理端口暂停及 IPv4/IPv6 CIDR 封禁），以及 `hy2-abuse.py`（IP 槽位与错误事件、可选仅当前 TCP 目标）；**没有假造历史 UDP 目标**，待实机测试 |
| P2 | NAT 机 `probe-once` / `probe-enable` / `probe-disable` / `probe-status` | 后续已从 jieshu 移植到 `ceshi/nat.sh`，仍**未开展 NAT 机探测真机验收**；首次 HY2 真机验收不包含 probe |
| 不直接照搬 | SOCKS5 账号体系、VLESS Reality 专用字段 | 协议不同，作为独立产品/模块而非 HY2 五文件版必要功能 |

以上为**源码能力对照**，不是上述缺失模块的通过性测试。未来每项代码变更应独立 PR + CI + 指定场景真机验证，避免破坏已经确认的 NAT 防泄漏和配额持久化。

## 当前仍需注意

- `hy2_doctor.sh --strict` 唯一持续警告：未配置 `HYSTERIA_BINARY_SHA256`，属于无法持续校验核心哈希，**不是运行故障**。
- 本轮 UDP/40020 是 **60 分钟有效期**的临时测试节点；测试时的状态不会无限保留。
- 终端原始记录曾出现完整 HY2 节点链接/密码。正式使用前应轮换已暴露凭据；**不要将原始日志、私钥或节点订阅提交到仓库**。
- 验收仅覆盖被明确执行的场景，不能推导“所有 Debian/Ubuntu 版本和所有出口方案已经通过”。

## 补充实测：原版 portbw 与 HY2-NAT 组合（2026-11-04）

本节是在上方首次 HY2/WG 验收**之后**补做的限速验证，不能倒推为首次验收时已经测试。

- `portbw.py` 与 `portbw-install.sh` 均由 [`jieshu`](https://github.com/liucong552-art/jieshu) 原样导入，已合并在 [`ceshi` PR #5](https://github.com/liucong552-art/ceshi/pull/5)；上游程序 SHA256 为 `66a79fd8ff9f4b00c43a62ef3530251cd6934301bfc5412d497b47d94a3f70d1`，安装器的 `EXPECTED_SHA` 与之匹配。
- Debian 13 机器在 `eth0` 上通过安装器 `check`、`install` 和 `verify`；安装器报告 `CHECK PASS`、`INSTALL PASS`、`VERIFY PASS`，`tc` 与 nftables 双层模式正常，常驻监控 Watchdog 就绪。
- 对 HY2-NAT 临时节点 UDP/40020 执行 `portbw set 40020 10 10`，`portbw report` 显示静态 10/10 Mbps、监听正常、防火墙正常、流控正常；`portbw audit` 显示 `NFT=ok`、`TC=ok`、`STATE=OK`。
- 同时 `pq_audit.sh` 显示 500 MiB 原有配额保持正常；操作人员通过 Windows v2rayN 报告**静态限速测速通过**（未提供可供校对的客户端精确测速数值，故不写入推测值）。
- 测试后执行 `portbw del 40020`，结果“已手动取消”；`portbw audit` 输出空策略列表。
- **未验证**自动模式的 40→18 Mbps、持续 45 秒触发、保护/恢复/冷却、异常断电后的限速恢复，也没有做多端口高并发的吞吐基准。

### WG 来源与覆盖风险说明

- 本次 VPS 实际运行过 `jieshu/vpswg.sh`，且 HY2-NAT 出口、防泄漏与 VPS 正常重启均通过；因此**VLESS 项目来源的 WG 组件在这个环境确实与 HY2 协同运行成功**。
- 源码对比：`jieshu/vpswg.sh` 共 1613 行，`ceshi/vpswg.sh` 共 1641 行，最长公共子序列为 1602 行。`ceshi` 已把临时节点扫描目录、状态锁适配为 HY2，并保留旧 `fwmark` 切换期间额外的禁止直连保护。
- 因此“实测能运行”和“可以无差别将 `jieshu` 原版覆盖掉 `ceshi` 的适配脚本”并非一回事。没有必要为追求字节一致覆盖现有正常运行的 WG，更不能误称 `jieshu/vpswg.sh` 无法在 HY2 运行。

## 补充开发：HY2 独立审计工具（本轮尚未真机运行）

后续单独移植并提交了 `hy2-abuse.py`：根据 HY2 端口管理元数据检索当前来源 IP 槽位，以及 journalctl 警告/错误分类；在**用户明确启用且仅监听回环地址**的 Hysteria Traffic Stats API 下，可以只读查询**当前 TCP 流目标**。不输出节点密码、不自动封禁、不保存日志原文。`DAYS` 不产生 API 历史目标记录，不能将此功能误称为 `jieshu/vless-abuse` 所有日志维度的等效实现。GitHub CI 是程序验证，**实际 HY2 服务端口和可选 Stats API 的联通仍需 VPS 实测**。

## 补充开发：HY2 Watchdog 持久 72 小时恢复宽限（待真机故障验收）

原 `hy2_managed_watchdog.sh` 连续第三次重启失败即清理节点，可能在 WG 暂时中断时提前删除临时凭据。后续对标 `jieshu` 新增 `WATCHDOG_GRACE_START_EPOCH`、第三次失败启动 72h 持久宽限、实际健康验证后清除计时、宽限期内保留元数据、防护异常时停止监听和终局清理前 300 秒冷启动稳定时间。新增代码已在 `hy2.sh` 的生成脚本内，GitHub 模拟测试覆盖三次失败、跨重启保留与健康清零。**本次 Debian 13 正常运营测试发生在此功能编写之前，没有证明真机长期故障场景已经通过。**

## 对标开发完成后的代码/真机区别

在本次 HY2 核心实测完成之后，仓库又合并了 [PR #7](https://github.com/liucong552-art/ceshi/pull/7) NAT Peer 探测、[PR #8](https://github.com/liucong552-art/ceshi/pull/8) 端口暂停/CIDR 黑名单、[PR #9](https://github.com/liucong552-art/ceshi/pull/9) HY2 每日流量、[PR #10](https://github.com/liucong552-art/ceshi/pull/10) 30 天保周期手动重设、[PR #11](https://github.com/liucong552-art/ceshi/pull/11) HY2 只读异常审计、[PR #12](https://github.com/liucong552-art/ceshi/pull/12) Watchdog 持久 72 小时宽限。它们均已通过仓库 CI，**但新模块必须在真实目标 VPS/NAT 机做后续验收才能宣称部署链路通过**。这份报告保留早先实测日期和证据，不把新的 CI 合并追溯当成旧的真机已测。

## 2026-11-05 补充真机验收：每日流量及只读审计初装

运行环境：原 Debian 13 VPS（helan），已有 HY2 UDP/443 主节点和 WG-NAT 出口；本轮没有临时节点。本节仅记录运营者提供的终端输出。

| 检查项 | 观测结果 | 结论 |
|---|---|---|
| `traffic.py --install` | 输出“每日流量记录已安装” | 安装动作通过 |
| `hy2_traffic` 和 `hy2_traffic main` | 今日北京时间 2026-11-05，主节点 UDP/443，UP/DOWN/TOTAL 均为 0.00B | 基础查询通过；**尚未产生客户端流量，不能证明双向计数准确** |
| `hy2-abuse.py 443 --days 1` | 运行成功；IP 槽位 not-configured-or-unavailable；最近 1 天警告/错误 0；未配置可选 loopback Stats API | CLI 基础只读查询通过；**不代表验证了 IP_LIMIT 开启时的槽位或实时目标** |
| systemd | `hy2.service`、`hy2-traffic.timer`、`hy2-traffic-shutdown.service` 均返回 active | 服务状态通过 |
| `hy2_doctor.sh --strict` | 0 错误、1 警告；仅提醒未设置 HYSTERIA_BINARY_SHA256；当前没有临时节点 | 现有 HY2 管理器总体健康 |
| WG-NAT 诊断 | `OK EXIT_IP=（出口检测结果一致；具体 IP 已脱敏）` | 出口检查通过；**此次未通过新临时节点客户端测速** |

**下一项关键验收已在下文完成：** 创建短期带配额 HY2-NAT 节点，产生双向流量，并核对 `hy2_traffic` 与 `pq_audit.sh`；剩余跨重启、跨日及到期清理仍待单独验证。

## 2026-11-05 新增真机验收：HY2 UDP 每日双向统计与配额一致性

本次追加测试继续使用 Debian 13 VPS，执行 `hy2_traffic --collect` 后，以 `hy2_mktemp_nat.sh` 创建仅供验收使用的 `hy2-temp-trafficcheck`，IP_VERSION=4、IP_LIMIT=1、PQ_GIB=0.48828125、D=1800。系统自动分配 UDP/40000；**该节点含密码的完整 URL 不收录本记录**。

| 检查 | 原始终端观测 | 判断 |
|---|---|---|
| NAT 节点创建 | `WG-NAT 落地临时节点创建成功`，`LANDING=nat`，`WG_IF=wg-nat`，TTL 约 30 分钟、500.00MiB | 成功 |
| WG 路由及出口 | `fwmark=2333` 指向 table 100、WG 握手新鲜、`wg_nat_healthcheck.sh` 显示 `OK EXIT_IP=`，出口地址已脱敏 | 服务端 NAT 出口健康 |
| 当日首次统计 | 创建后 `hy2_traffic hy2-temp-trafficcheck` 显示 UP=DOWN=0.00B | 正常建立初始基线 |
| 真实流量后的每日统计 | UP=967.28KiB、DOWN=71.42MiB、TOTAL=72.36MiB | **真实 UDP 流量计数通过** |
| `hy2_traffic 40000 --json` | `upload=990493`、`download=74886885`，合计 **75,877,378 字节**（约 **72.36MiB**）；采样时间北京时间 08:09:35 | JSON 与人类可读输出一致 |
| 原有 `pq_audit.sh 40000` | LIMIT=500.00MiB，USED=72.36MiB，LEFT=427.64MiB，USE=14.5%，STATE=active | **与新每日统计一致，未破坏现有配额** |
| `hy2-traffic.timer` | `active` | 定时器仍正常 |

**到期时间**：该临时节点记录为北京时间 **2026-11-05 08:38:28**，不可在之后假定该 URL 仍有效。

**结果**：本次实际有双向 UDP 字节增量；`hy2_traffic`、JSON 及独立配额审计数值吻合，故该模块“真机真实流量计数 + 配额共存”可以标为通过。**正常 VPS 重启后保留计数在下一节已验证通过**；仍未直接验证跨日归属、异常断电、30 日过期裁剪或多个节点同时产生大流量。本轮服务端 WG 出口检查通过；日志未包含新的 v2rayN 出口截图，不能将其作为新的客户端出口截图证据。

## 2026-11-05 补充真机验收：HY2 每日统计正常重启持久化

基于 `hy2-temp-trafficcheck`（UDP/40000，原 TTL 截止北京时间 2026-11-05 08:38:28）。操作人员先确认剩余 TTL 为 **1385 秒**、保存重启前 `hy2_traffic 40000 --json` 至 root 私有文件，获得 `READY_FOR_REBOOT` 后执行 VPS 正常 `reboot` 并重新通过 SSH 登录。未公开原始 HY2 链接、密码或 WG 私钥。

| 验收指标 | 重启前（字节） | 重启后（字节） | 结论 |
|---|---:|---:|---|
| `upload` | 990493 | 990493 | 一致 |
| `download` | 74887317 | 74887317 | 一致 |
| TOTAL | **75877810** | **75877810** | **0 字节差值** |
| `PERSISTENCE_CHECK` | — | `PASS` | 通过 |
| 测试节点元数据 | 存在 | `NODE_METADATA_EXISTS=True` | 保留 |
| `hy2.service` | active | active | 正常 |
| `hy2-traffic.timer` | active | active | 正常 |
| `hy2-traffic-shutdown.service` | active | active | 正常 |

重启后的 `journalctl -u hy2-traffic.service -b -n 12` 记录三次成功采集：**00:16:00、00:16:22、00:17:00（服务器当地日志时区）**，每次均正常完成。此次被比较的是**重启之前再次采集的新基线**，因此下载为 74,887,317 字节（比上一轮 JSON 样本 74,886,885 字节多 432 字节），而不是错误地将两个不同时刻样本强行比较。

**正式结论：HY2 每日流量正常重启后的累计保留、不归零、不重复累计，真机验收通过。** 由于重启后的记录没有再次产生新的客户端大量传输，此项仅证明已保存的累计数据跨重启完好，并不替代重启后新增流量持续计数、跨午夜归属、突然断电或端口到期裁剪的专项测试。

## 2026-11-05 补充真机验收：HY2 单 IP 黑名单与受控解封

本次使用 **Debian 13 测试机 `helan`** 的 `hy2-temp-guardcheck`，HY2 WG-NAT UDP/40001，500MiB 配额、IPv4 来源 IP 槽位上限 1、原始有效期两小时。与生产机 `MEGABOX` 区分后才进行修改。**不记录真实客户端 IP、HY2 链接或节点密码。**

| 测试 | 真机输出或客户端观测 | 结论 |
|---|---|---|
| 服务器身份 | `hostname -s` 返回 `helan` | 通过 |
| 节点有效性 | 封禁测试前端口 40001，剩余有效期大于 6000 秒 | 通过 |
| 来源 IP 识别 | `hy2_il_active_ips 40001 4` 在有连接后返回唯一的 IPv4 来源地址 | 通过 |
| 测试前清洁状态 | `PAUSED: -`、`BANS: -`、`GUARD AUDIT OK: 0 paused, 0 bans` | 通过 |
| 安全解封兜底 | `systemd-run --on-active=300s` 返回已创建定时器 | 定时器建立通过；自动触发本身未单独验证 |
| 单 IP 封禁 | `GUARD BAN OK: 40001`、`GUARD AUDIT OK: 0 paused, 1 bans` | 通过 |
| v2rayN 实际封禁 | 操作人员确认使用原测试节点时无法联网 | **通过** |
| 手工解除封禁 | `GUARD UNBAN OK: 40001`、`PAUSED: -`、`BANS: -`、`GUARD AUDIT OK: 0 paused, 0 bans` | **通过** |
| v2rayN 联网恢复 | 操作人员确认解除后可重新联网 | **通过** |
| 服务完整性 | `hy2.service`、`hy2-temp-guardcheck.service`、`wg-quick@wg-nat.service` 全部 `active` | 通过 |

**结论：** HY2 单个 IPv4 来源地址的黑名单封禁/解除、独立 nft 规则与原有 HY2/WG 同时运行，真实 VPS + 客户端验收通过，最终无测试封禁残留。这不同于 `IP_LIMIT` 的最大来源 IP 槽位限制，二者经过共存验收。

**下一项：** CIDR `/24` 对真实客户端的效果已在下节验证；独立 nft 表的重启后持久恢复仍未实测。该项宜使用文档保留网段 `198.51.100.0/24`，仅针对受管理且未过期的测试端口，避免将真实客户端的封禁规则带入重启。生产机 `MEGABOX` 不参与。


## 2026-11-05 补充真机验收：HY2 来源 CIDR /24 网段黑名单

继续在 Debian 13 `helan` 的 `hy2-temp-guardcheck`（UDP/40001）进行测试。测试前验证了 hostname、节点元数据归属、NAT 模式、未到期、`hy2-guard audit` 无错误，且 `PAUSED: -` / `BANS: -`；从唯一来源 IPv4 推导所属 `/24` 网段。**不收录公网客户端 IP 或节点密码。**

| 验收检查 | 终端输出与人工客户端结果 | 判断 |
|---|---|---|
| 自动恢复保护 | `systemd-run --on-active=180s` 成功创建针对测试 CIDR 的临时解封 timer | 自动解封任务建立通过；定时触发本身未单独验证 |
| `/24` 封禁规则 | `GUARD BAN OK: 40001`、`GUARD AUDIT OK: 0 paused, 1 bans`、`CIDR24_BAN_STARTED` | **通过** |
| 真实客户端阻断 | 操作人员使用同一 v2rayN `guardcheck` 节点确认封禁期间无法联网 | **通过** |
| 解除 `/24` 规则 | `GUARD UNBAN OK: 40001`、`PAUSED: -`、`BANS: -`、`GUARD AUDIT OK: 0 paused, 0 bans` | **通过** |
| 客户端恢复 | 操作人员确认 v2rayN 解除后恢复正常联网 | **通过** |
| 主服务及 WG | `hy2.service` 和 `wg-quick@wg-nat.service` 均返回 `active` | 通过 |

**结论：** IPv4 CIDR `/24` 网段封禁与恢复的真机/客户端验证通过；端口暂停、单 IP `/32` 和 CIDR `/24` 三种封禁均有客户端行为证据。最终手工规则为零，没有残余封禁。**独立 nft 表正常重启后规则自动恢复已在下一节通过真机验收**。

## 2026-11-05 补充真机验收：HY2 独立 nft 防火墙跨重启持久化

**目标**：在 Debian 13 `helan` 测试机上，确认独立 `hy2_manual_guard` 防火墙表中的规则经正常 VPS 重启、systemd 服务恢复后仍存在；使用 RFC 5737 文档测试网段 `198.51.100.0/24`，避免封禁真实公网客户端。目标为有效期大于一小时的 `hy2-temp-guardcheck`，UDP/40001。**未对 `MEGABOX` 生产机执行本次操作。**

| 操作或检查 | 原始真机证据 | 结论 |
|---|---|---|
| 前置保护 | `hostname -s=helan`；校验测试节点 TAG/PORT/LANDING、剩余 TTL `>=3600s`，HY2 临时节点及守护服务运行，防火墙初始 `0 paused, 0 bans` | 通过 |
| 写入持久黑名单 | `hy2-guard ban 40001 198.51.100.0/24`；`GUARD BAN OK: 40001` | 通过 |
| 重启前状态 | `BAN 40001: 198.51.100.0/24`，`GUARD AUDIT OK: 0 paused, 1 bans`，`REBOOT_TEST_READY` | 通过 |
| VPS 重启及重新登录 | 实际执行 `reboot`，连接断开后通过 SSH 成功再次登录同一 `helan` | 通过 |
| 重启后的服务 | `hy2-manual-guard.service`、`hy2.service` 和 `wg-quick@wg-nat.service` 均 `active` | 通过 |
| 重启后恢复状态 | `BAN 40001: 198.51.100.0/24`、`GUARD AUDIT OK: 0 paused, 1 bans`；实地 `nft -a list table inet hy2_manual_guard` 包含同一规则注释 | **通过** |
| 脚本判定 | **`GUARD_REBOOT_PERSISTENCE=PASS`** | **通过** |
| 最终清理 | `GUARD UNBAN OK: 40001`，`GUARD AUDIT OK: 0 paused, 0 bans`，`PAUSED: -`，`BANS: -` | **清洁** |

**正式结论：** `hy2-guard` 的独立 nft 防火墙规则经正常重启可持久恢复；单 IP、CIDR 网段及端口暂停的真实 v2rayN 行为验证均已通过。测试完成后的持久状态没有遗留封禁。此轮没有让真实客户端处于重启时的封禁状态，不能据此声称已验证客户端在重启瞬间的封禁连续性；也未测试宿主机异常断电。


## 2026-11-05 补充真机验收：HY2 保留 30 天锚点的配额重设

在 Debian 13 **测试机 `helan`** 使用独立 NAT 临时节点 `hy2-temp-cyclecheck`（UDP/40000，原有效期 31 天），先以 `PQ_GIB=0.02` 创建，启用原有 30 天自动重置周期，再使用 v2rayN 产生实际流量后，调用 `bash /root/pq_keepcycle.sh 40000 0.04`。已建立 45 分钟的临时 systemd 清理定时器；真实节点链接、密码及服务器地址均不写入公开记录。

| 验收数据 | 重设前 | 重设后 | 结论 |
|---|---|---|---|
| `pq_audit.sh` 总配额 | 20.48MiB | **40.96MiB** | 生效 |
| `pq_audit.sh` 已用流量 | 41.07KiB | **0.00B** | 清零 |
| `pq_audit.sh` 剩余额度 | 20.44MiB | **40.96MiB** | 正常 |
| `RESET_INTERVAL_SECONDS` | `2592000` | `2592000` | **30 天周期保留** |
| `NEXT_RESET_EPOCH` | `1796433625` | `1796433625` | **锚点完全不变** |
| 北京时间下次自动重置 | 2026-12-05 09:20:25 | 2026-12-05 09:20:25 | **日期不变** |
| `ORIGINAL_LIMIT_BYTES` | `21474836` | `42949672` | 新配额正确 |
| `SAVED_USED_BYTES` | — | `0` | 元数据清零 |
| 校验脚本 | — | `KEEP_CYCLE_METADATA_TEST=PASS` | **通过** |
| `hy2.service` / `wg-quick@wg-nat.service` | — | `active` / `active` | 原服务完好 |

脚本自动在 root 私有目录备份原配额与节点元数据，日志显示执行成功、nft 重建后配额审计与新额度一致。

**结论：** 对现有有效期 31 天、已产生真实流量的 HY2-NAT 临时节点，`pq_keepcycle.sh` 重设总额度、清零配额已用量、保留原 30 天下次自动重置锚点，**VPS 真机通过**。本轮**未等待真实 30 天到期**，因此下一周期自动触发的事件仍需时间跨度测试。另需独立核对专用测试节点最终删除/临时 timer 停止，不应凭原有 45 分钟 transient timer 宣称完成了清理。

## 2026-11-05 补充真机验收：一键批量回归测试（PR #21）

在 Debian 13 `helan` 测试 VPS 上，操作人员从仓库 `main` 下载 `tests/hy2_batch_acceptance.py`，完成 `py_compile` 后仅执行一次 `python3 /root/hy2_batch_acceptance.py --live`。终端日志显示 **12 项 PASS、0 FAIL、0 SKIP**，最后为 `FINAL_RESULT=PASS`、`CLEANUP=PASS`，脱敏汇总文件生成在本机 root 私有目录 `/root/hy2-batch-accept-<随机标识>/report.json`。该 JSON 的文件内容本次未上传，故此项依据是操作人员提供的完整终端标准输出，不宣称已独立审阅 JSON 本体。

| 自动回归检查 | 真实 VPS 终端证据 | 结论 |
|---|---|---|
| 前置保护：主机、依赖、所有权、guard、内存、磁盘 | `PASS host/dependencies/owner/guard/disk/memory` | PASS |
| HY2 Doctor | `PASS HY2 doctor` | PASS |
| 已管理 systemd timers | `PASS managed timers` | PASS |
| 北京时间主节点流量 JSON | `PASS daily traffic main JSON` | PASS |
| WireGuard 策略路由 | `PASS WireGuard marked route` | PASS |
| 异常连接只读审计 | `PASS read-only abuse audit` | PASS |
| 预先启用安全清理机制 | `SAFEGUARD=30_MIN_PERSISTENT_TIMER` | 已启用跨重启安全计时器 |
| 随机隔离 31 天 HY2-NAT 临时节点 | `PASS isolated 31-day HY2-NAT node create` | PASS |
| 新节点每日流量 JSON | `PASS test-node daily traffic JSON` | PASS |
| 端口暂停/恢复、文档 IP /32、CIDR /24 与 guard 规则 restore | `PASS temporary-node guard pause/resume + /32 + /24 + restore` | PASS |
| 配额 0.02 → 0.04 GiB，保留 30 天锚点 | `PASS quota reset 0.02 to 0.04 GiB retaining 30-day anchor` | PASS |
| nft 配额与原有 HY2/WG 状态 | `PASS nft quota and original service health` | PASS |
| 强制清理临时测试节点 | `PASS forced temporary-node cleanup` | PASS |

**结论：** 首次真正实现并在 `helan` 一键执行了该批量回归脚本，正常结束后自动完成测试节点回收；无需每项分别执行测试命令。脚本没有重新实测 v2rayN 的封禁/恢复行为、真实客户端数据面测速、VPS 自动重启或 30 天到期重置；这些状态应以此前单独真机记录或后续专项验收为准。因为未读取 `report.json` 本体，也未独立检查执行后的 systemd 定时器列表，不能据本条日志进一步证明所有残余 root 私有文件和计时器都已完全回收。公开验收报告不记录随机运行标识、公网 IP、HY2 链接、密码或 WG 密钥。


## 2026-11-05 补充真机验收：第二轮一键维护（自然到期、限速生命周期）

操作人员在 Debian 13 **`helan`** 测试 VPS 上从 `main` 拉取 `tests/hy2_batch_acceptance.py`，执行 `--maintenance`。终端回报 `TEST_COUNTS=PASS:9 FAIL:0 SKIP:1`，`FINAL_RESULT=PASS`、`CLEANUP=PASS`。结果依据为实际命令标准输出；本轮未收到 root 私有 `report.json` 本体。

| 主要验收内容 | 终端输出 | 结论 |
|---|---|---|
| 运行环境、权限、依赖、guard 审计、服务状态 | `PASS host, dependencies, guard and service preflight` | PASS |
| HY2 Doctor 和已管理 timers | `PASS HY2 doctor`, `PASS managed timers` | PASS |
| WG 标记流量路由 | `PASS WG marked exit route` | PASS |
| 隔离 180 秒 HY2-NAT 测试节点 | `PASS isolated 180s HY2-NAT node creation` | PASS |
| Watchdog 正常巡检 | **`SKIP healthy watchdog scan`**：检测到其他临时节点，为避免共享巡检可能改动它们而跳过 | **未验收，非失败** |
| portbw 自动限速策略生命周期（空闲态） | `PASS portbw automatic-policy idle lifecycle` | PASS |
| 节点自然时间到期、自动清理 | `WAITING_FOR_NAT_TEST_NODE_NATURAL_EXPIRY` → `PASS real TTL expiry and automatic cleanup` | **PASS** |
| 到期后防火墙/主服务 | `PASS guard/primary services after expiry` | PASS |
| 兜底安全清理验证 | `PASS safety cleanup verified` | PASS |

**结论：** 有真实 TTL 等待与自动清理证据，且本轮 portbw 自动模式的设置、只读检查、关闭和撤销获得成功；这**不是**大流量触发降速与恢复的性能验收。Watchdog 扫描明确 **SKIP**，更未故意注入错误或等待 72 小时故障宽限。下一轮批量验证多节点共存、配额与 IP_LIMIT 规则归属隔离及端口复用。脚本启动了跨重启有效的 30 分钟兜底 timer，但本轮未触发该后备恢复路径。公开文档不含测试连接凭据或原始运行目录的随机标识。

## 2026-11-05 第三轮自动化首次运行：测试器周期预期错误（未通过，清理通过）

操作人员从 `main` 运行 `python3 /root/hy2_batch_acceptance.py --edges`。终端输出：

- 前置状态、HY2 Doctor、托管 timers、WG 路由通过；分别创建了 A/B 两个独立 HY2-NAT 测试节点，且三枚持久安全清理定时器已提前建立
- `FAIL dual-node protection and quota independence - 31d node did not receive the 30d quota cycle`
- `STOP dual-node protections are not isolated` 是上一项检查失败后的停止提示，**并非证明两个节点相互干扰**
- `PASS safe cleanup C`、`PASS safe cleanup B`、`PASS safe cleanup A`；`TEST_COUNTS=PASS:9 FAIL:2 SKIP:0`、`FINAL_RESULT=FAIL`、`CLEANUP=PASS`

**定位：** `--edges` 实际创建的测试节点 `D=1200`（20 分钟），该节点应该保留 `RESET_INTERVAL_SECONDS=0` 和 `NEXT_RESET_EPOCH=0`，但复用了只适合 `D>2592000`（超过 30 天）的测试断言，错误要求 20 分钟节点具有 30 天周期。这是 **`tests/hy2_batch_acceptance.py` 验收器断言错误**，不是用户配置或 HY2 已证实的配额错误。该次测试在 A 配额重设及 C 端口复用前终止，因此**双节点配额修改隔离、端口重用本次尚未验收**。

**修正与回归：** 修复测试函数依据真实 `DURATION_SECONDS` 判断预期：`D>2592000` 必须保留原 30 天锚点；`D<=2592000` 必须保留“无周期”状态，两者都应验证配额改为 0.04 GiB、已用清零、新 nft 配额生效、节点元数据同步。为 20 分钟与 31 天两种节点添加模拟真实元数据的单元回归。**原始第三轮结论仍为 FAIL，待修订版在 `helan` 再实测。**


## 2026-11-05 第三轮一键边界回归：修复后实机全部通过

第三轮第一次因测试器错误地把 1200 秒节点当成 31 天节点要求 30 天重置周期而 `FINAL_RESULT=FAIL`、`CLEANUP=PASS`，已通过 PR #25 修复并增加两类 TTL 的 CI 防回归。

操作人员从当前仓库 `main` 重新下载测试器，在 Debian 13 **`helan`** 执行 `python3 /root/hy2_batch_acceptance.py --edges`，终端结果如下：

| 验收阶段 | 终端证据 | 判定 |
|---|---|---|
| 前置主机、Doctor、systemd timers、WG 路由 | 四项 `PASS` | 通过 |
| 测试资源保护 | `SAFEGUARD=3_PERSISTENT_30_MIN_TIMERS` | 三个跨重启定时器预先创建 |
| A/B 两个并行 HY2-NAT 节点 | `PASS first isolated HY2-NAT node created`、`PASS second concurrent HY2-NAT node created` | 通过 |
| 两节点配额/IP_LIMIT 规则隔离 | `PASS dual-node protection and quota independence` | 通过 |
| 删除 A，B 仍运行 | `PASS delete A while B remains active` | 通过 |
| A 端口及元数据/nft 释放，B 保持原配额/IP | `PASS A port released and B quota/IP intact` | 通过 |
| C 复用原 A 端口、不继承配额/IP | `PASS recreate C on A former port without stale quota/IP` | 通过 |
| C 运行时 B 仍健康 | `PASS B stays healthy after port reuse` | 通过 |
| C/B/A 各自安全清理 | `PASS safe cleanup C`、`PASS safe cleanup B`、`PASS safe cleanup A` | 通过 |

**实际汇总：`TEST_COUNTS=PASS:14 FAIL:0 SKIP:0`、`FINAL_RESULT=PASS`、`CLEANUP=PASS`。** 本轮从实际 Linux VPS 终端输出得出；root 私有 `report.json` 内容本次未单独上传。这是服务端两节点并发、状态及规则隔离、端口复用的真机验收；不宣称来自两个独立真实 v2rayN 客户端的同步压测。

### 当前常规发布/长期专项边界

截至上述第三轮通过，核心 HY2/WG、真实客户端暂停和 CIDR 规则、持久化、单节点配额清零/保留 30 天锚点、真实短期到期清理、自动限速配置/撤销、双节点配额/IP 隔离和端口复用的主路径已有证据。**仍不能宣称完整生产场景全部测试完。** 未完成项目集中为：Watchdog 在故障注入后的修复和 72 小时宽限行为；真实持续流量触发 portbw 自动降速及恢复；NAT 出口机 Peer 探测/重启（需到 NAT 机器执行）；IPv6 的真实服务和客户端（取决于 IPv6 与 AAAA）；以及真实 30 天自动重置、证书续期、异常断电/底层 nft 全表失效等单独专项。与既有通过事项不同，这些不是复制旧的第三轮脚本就能完成，必须有专门条件和隔离策略。

新增只读 `tests/hy2_release_gate.py` 可从本地脱敏 `report.json` 自动汇总三个既有批次是否都存在成功且清理完成的证据，实时检查 HY2/WG/guard/portbw、IPv6 环境前提，并将这些未测项目**明确标为未测**；它不能替代额外的实机故障/压测，也不改变机器状态。


## 2026-11-05 真机只读发布门禁：常规三轮成功且测试节点零残留

操作人员在 Debian 13 `helan` 执行仓库 `tests/hy2_release_gate.py`。控制台证据如下（不收录机器公网地址或测试凭据）：

```text
ROUTINE_GATE=PASS
LIVE=PASS pass:12 skip:0
MAINTENANCE=PASS pass:9 skip:1
EDGES=PASS pass:14 skip:0
OTHER_TEMP_NODES=0
IPV6_PREFLIGHT=SKIP
OUTSTANDING_ISOLATED_TESTS=3 plus IPv6-if-ready and long-horizon
READ_ONLY=YES
```

**结论：** 三个独立批次的本地成功/清理报告及当前机器健康审计满足 **常规功能验收 PASS**，批次 PASS 计数合计 **35 次**（含重复健康检查，不应称为 35 个完全不同功能）。临时节点检查为 **0 个**；此前维护批次的 Watchdog 健康巡检明确 `SKIP`，未被当作成功。IPv6 网络前提未满足，跳过真实 IPv6 客户端验收。本门禁不代表高负载真实限速、NAT 出口机 Peer 探测或 72 小时故障宽限已通过。原始 `/root/hy2-release-gate-report.json` 保留在操作者私有主机，以上结论依据粘贴的控制台输出。

**待专项实测：** (1) Watchdog 受控停止并自动恢复一枚测试节点；(2) `portbw` 持续实际流量触发降速与恢复；(3) 仅在 NAT 出口机执行 Peer 探测及状态检查。长周期和破坏性专项需单独隔离。
