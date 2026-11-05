# jieshu ↔ ceshi（HY2）通用组件复用审计

**审计日期：2026-11-05** · 仓库：[\`jieshu\`](https://github.com/liucong552-art/jieshu) 与 [\`ceshi\`](https://github.com/liucong552-art/ceshi) 的 \`main\` · 仅静态对比 GitHub 文件和既有验收记录，**不在 VPS 修改任何运行配置**。

## 结论与验收策略

- **应直接复用**：\`portbw.py\`、\`portbw-install.sh\` 两文件在两个仓库的 Git Blob SHA **完全相同**。\`jieshu\` README 的「通用 portbw（2026-10-18）」明确记载，同一源码曾在 Helan 验证上传、下载触发与恢复；\`ceshi\` 的 \`test_portbw_integration.py\` 又检查同一 SHA256 固定源码、TCP/UDP、单独 nft \`pbw_policy\`、无 VLESS/HY2 状态路径依赖。**不需要把限速内核算法当作 HY2 新功能重建/重测**；先前要求再做一遍高流量触发，属于不必要的常规验收重复。若需证明特定已部署节点的端到端 UDP 吞吐，可选择额外做一次受控集成测试，但它不是重复发布 portbw 的先决条件。
- **应共用现有 NAT 实例**：两个仓库的 \`nat.sh\` 默认 \`wg-exit\`、UDP \`51820\`、\`10.66.66.2/24\`、IPv4 FORWARD 与 MASQUERADE；WG 出口处理 IP 数据包，不依赖最上层是 VLESS 还是 Hysteria2。NAT 机既已在 \`jieshu\` 项目验收，HY2 VPS 又已独立验证实际 WG-NAT 出口和断线保护，**不需要重新部署 HY2 \`nat.sh\`，不需要再对共享 NAT 执行原有验收**。NAT 端仍须为每台 VPS 保持单独 WG Peer、公钥与 \`AllowedIPs\` 映射。
- **有条件共用逻辑/保留适配**：\`vpswg.sh\` 的 WG 转发与故障保护、\`traffic.py\` 的每日统计、\`quota-lib.sh\` 和 \`iplimit-lib.sh\` 的状态机属于大量继承/移植；但 HY2 的临时节点路径、UDP 规则、密码身份、服务名、锁和元数据不同。这些**不得直接用 VLESS 文件覆盖**。HY2 已完成相应集成与真机回归，后续不必再反复验证原始算法。
- **必须协议专属**：Hysteria 二进制、TLS/密码/QUIC 配置、HY2 节点创建与生存期、UDP/IP_LIMIT nft 规则、HY2 Watchdog 与防泄漏集成。仅复用设计与算法，保留 HY2 命名空间。现有 Watchdog 真机真实停止/自动恢复已通过。

## 逐文件、逐模块证据

| 源头 | jieshu Git Blob | ceshi Git Blob / 代码关系 | 本次判定 |
|---|---|---|---|
| \`portbw.py\` | \`3233153d3aa04b3baf577fa00d02e17884793203\` | **相同 SHA** | 完全同一源码，通用；既有算法测试可继承 |
| \`portbw-install.sh\` | \`82f16ec0abb0f057d908efd2a5725a7d008e009e\` | **相同 SHA** | 完全同一安装器，勿再开发第二套 |
| \`tests/test_portbw_auto_sustained_window.py\` | \`f67f4fbfcc072c0f3cb039f2f3030b82fea5fce7\` | **相同 SHA** | 两仓库重复 CI 单元测试；不需要用户重复真机操作 |
| \`tests/test_portbw_cpu_candidate.py\` | \`266e61e92f71ff5eb7194fbd45b16845de80a313\` | **相同 SHA** | 同上 |
| \`nat.sh\` | \`993b0e0d0c34e03303ec4bbc2c44246432bc2ccd\` | \`741a96b86f2300ab55964dcdf930f1ba9aefe62a\`；按多重集合相同行约 **96%** | NAT 核心通用，但复制版有功能/安全行为偏差；现有 NAT 必须继续用 jieshu 管理器 |
| \`vpswg.sh\` | \`a7353700a581d6699a24f6b2929da0e20bd93611\` | \`809484d58f51ecbc03f062178686506e05ce9a75\`；按多重集合相同行约 **99%** | WG 通用，VPS 应用元数据适配独立；当前正常实例不要覆盖 |
| \`traffic.py\` | \`5b68393eb7dc49f4b44c87a137709c47f7749265\` | \`59fb02dda6486fb04fcab1108e429481e8be93a3\`；按多重集合相同行约 **91%** | 采集/时间窗口同源，但须 TCP → UDP、UUID → HY2 密码/主节点身份适配 |
| \`natjichang.sh\` | \`b1d0577486e61a19fb67f2ed2e79a3bc94d90cc9\` | \`00fba6dce0b22b839da1d14816ecd43c06ef07f7\` | 这是 VPS 侧的 VLESS/HY2 临时 NAT 节点**入口创建器**，非 NAT 机出口管理器；协议适配必要 |

\`vless.sh\` 和 \`hy2.sh\` 内嵌生成的 \`quota-lib.sh\` 与 \`iplimit-lib.sh\`，按函数/项目命名空间粗略标准化后，相同代码行约分别占最短版本 **91% / 94%**。这仅是移植关系线索，**不是函数完全等价的证明**：VLESS nft 用 TCP，HY2 nft 必须使用 UDP；配额、IP 槽位、元数据和看门狗仍须在各自服务协议上做最低限度的集成验收，而这一工作此前已进行。

## HY2 从 jieshu 复制代码后发生的值得警惕的改动

以下是**仓库当前代码的真实差异**，不能据此直接断定改动者是谁；也不代表每一项改动都有 bug。

1. **\`nat.sh\` 重复维护、且不只是改名：** jieshu 用 \`vr_wg_peer_probe.sh\` + \`vr-wg-*\` timer；HY2 改成 \`hy2_wg_peer_probe.sh\` + \`hy2-wg-*\`。如果在已使用 jieshu 的 NAT 出口机上叠加 HY2 管理器，会引入第二套命名空间、混淆探测任务与状态，甚至因为 \`init/add\` 重启 WG 影响现有 Peer。上轮已修正可选检查脚本，使其识别当前实现且默认只读。
2. **\`nat.sh\` 功能行为分叉：** jieshu 的依赖安装包含 \`iputils-ping\`，HY2 的同一安装依赖表去掉了它，但 Peer 探测仍依赖 \`ping\`；HY2 将两个 Peer 解析到相同 IPv4 时的处理从 \`fail\` 改为 \`warn\`；HY2 给 WG FORWARD 专有链额外添加了两个兜底 \`DROP\`；HY2 删除了 jieshu 对旧版五参数 \`nat.sh add\` 的兼容分支。这些不是 Hysteria2 协议所必需的区别，**不能为更新 HY2 而覆盖已验收的 jieshu NAT**。若将来要统一，应逐项评估并向公共上游合并适当修复；目前不动已运行机器。
3. **\`vpswg.sh\` 存在真差异：** HY2 将 VLESS 临时元数据目录 \`/var/lib/vless-reality/temp\`、锁 \`/run/vless-reality/temp.lock\` 改为 HY2 目录/锁；另加标记规则切换时旧 mark 的 OUTPUT fail-closed 防护和 IP 整数字面量十进制处理。这是 HY2 集成/安全强化，不宜把文件盲目换回 jieshu 版，也无须重装已通过的 jieshu WG。
4. **\`traffic.py\` 的协议适配不可删：** 现成统计框架、时区/保留规则是通用的，但 Hysteria2 端口是 UDP，VLESS 是 TCP；HY2 认证身份是密码而非 VLESS UUID。原样替换会错计或归属错误。
5. **配额/IP_LIMIT/Watchdog 不是“同一份可执行文件”：** 共用了状态/锁/原子恢复的设计，但 nft 协议、主服务、文件目录与计数器对象不同，不能盲目复用 VLESS 命令覆盖 HY2。HY2 已有真机证据支持主要路径，无须将通用状态机按功能再测一遍。

## 另一个未在此前明确提示的部署冲突

两项目分别在各自主脚本中写入同名全局路径：

- \`/etc/systemd/system/pq-save.service\`、\`pq-save.timer\`、\`pq-reset.service\`、\`pq-reset.timer\`；
- \`/usr/local/sbin/pq_add.sh\`、\`pq_del.sh\`、\`pq_audit.sh\`、\`pq_save_state.sh\`、\`pq_reset_due.sh\`、\`pq_restore_all.sh\`、\`ip_set.sh\`、\`ip_del.sh\`、\`iplimit_restore_all.sh\`。

**因此 VLESS 与 HY2 两套“完整安装器”不应在一台 VPS 上不加命名空间隔离地共同安装**；会发生 unit 或命令路径覆盖、配额服务指向错误应用等风险。当前 \`helan\` 与 \`MEGABOX\` 分机部署，没有已知由此产生的现场冲突。共享 NAT 出口则是**另一台机上的公共 WireGuard 服务**，属于被支持的共用模式，与同机双安装器不是一回事。

## 测试结果正确归属与剩余工作

| 类别 | 证据来源与应计入状态 |
|---|---|
| portbw 本体、上/下行限速算法、自动触发/恢复 | jieshu \`README.md\`「通用 portbw」有 Helan 真机历史说明；两个项目代码和两份核心算法测试 SHA 相同，**复用已有算法验收，不再强制 HY2 再跑等价高负载压测**。但不能据此声称“某个 HY2 链接的端到端高负载”已独立测试 |
| portbw 在 HY2 机器上的安装、策略、核查与清理 | ceshi \`--maintenance\` 一键批次已实际执行，\`PASS portbw automatic-policy idle lifecycle\`，只需现有结果 |
| jieshu NAT 出口管理与 Peer 探测 | 用户确认在 jieshu 已验收；已有 HY2 WG-NAT 真机出口和阻断恢复证据；**外部依赖复用，不重装、不再把 NAT Peer 作为 HY2 新必测** |
| HY2 UDP 配额、IP_LIMIT、临时节点、端口复用、真实端口防护 | 必须在 HY2 本体测，已有 35 项批量通过及真实客户端测试（不重复计数为独立功能） |
| HY2 Watchdog 真正停止/恢复 | \`--recovery\` 6 PASS、0 FAIL，自动清理 PASS；不再作为待执行项目 |
| IPv6、实际 72 小时故障宽限、30 天按时执行、证书轮换与异常断电 | **条件性或长周期专项**，不能继承现有通用库验收，也不该拖延常规功能结论 |

**纠正此前多余的工作：** 将 NAT 机再做一套 HY2 Peer 验收，以及把完全相同的 \`portbw\` 当作 HY2 独立算法要求重新高负载压测，都是重复规划。以后采用「**同一源码 SHA + 共享测试证据 + 目标主机一次集成验证**」作为复用标准；不是每个仓库、每个上层协议都从零重做算法测试。

## 后续代码治理（这次不改运行逻辑）

1. 将 \`jieshu\` 已验收的 NAT 出口服务视为现有共享基础设施；HY2 README 在已有 NAT 的场景不应执行 \`ceshi/nat.sh init\`，只需确认已存在独立 Peer 与 HY2 WG 路由。
2. \`portbw\` 保持一份经过验收的上游源码与安装器，两个仓库引用/复制发布时用 SHA 对齐。\`ceshi/tests/test_portbw_integration.py\` 已对通用包做 SHA256 与 UDP/命名空间隔离检查，不需再复制更多人工测试。
3. \`vpswg.sh\`、\`traffic.py\`、\`quota-lib.sh\`、\`iplimit-lib.sh\` 仅在非破坏性维护窗口规划「共享逻辑 + 协议 adapter」的长期重构；现在核心真机已通过，**不要为了代码看起来更统一而动现场的已验收实现**。
4. 若未来希望将 HY2 和 VLESS 部署在同一 VPS，先彻底改名配额系统 unit/命令入口及锁，明确共享 \`portbw\` 单实例；这是单独的架构工作，不能假设目前已支持。
