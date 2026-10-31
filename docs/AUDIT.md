# 代码审查与验证边界（2026-10-31）

审查基线：ceshi `ddc338d83a1987ea6a186258ee006b35de95eb6b` 的全部四个安装器、README、唯一 Python 测试及 release workflow；参考 jieshu `9db23a3551ed52daafefcb16071b7a705fd4515d` 的正式版说明、相关管理器、CI 和回归测试。jieshu 只读，未修改。

## P0 网络安全

| 问题 | 判定和处理 | 验证/限制 |
|---|---|---|
| NAT FORWARD 越权 | 原版已拒绝 WG→非 WAN 接口和 Peer 互访，但 WAN 也可路由私网/云元数据；补充目的地址拒绝，增加 WG INPUT 拒绝 | CI 真实 WG 隧道中公网可达、元数据与出口机内网不可达 |
| NAT 修复时清空链 | 原版逐条 `-F/-A`，可能落入 ACCEPT 基链；改为 `iptables-restore --noflush` 的完整 filter 事务 | 只修改专属链/跳转，不清空宿主机其他规则；filter 与 NAT 分属两个事务，NAT 更新失败仅影响可用性 |
| WG 删除后的真实出口回落 | 保留 bindDevice、IPv4-only direct、prohibit 与 OUTPUT mark guard | CI 删除 WG 后 marked ping 失败；同时删除规则后新建 bind-device socket 失败。尚非 HY2 客户端端到端证明 |
| 同 tuple 多接口 | 原优先级检查允许相同 mark/table 被另一接口使用；新增其他 state 文件占用检查 | 全局 WG 状态锁串行化扫描和写入；任意第三方手工添加路由仍不能据此保证无冲突 |
| DNS/masquerade 路径 | **未闭环，生产阻塞**。direct 的 bindDevice 不能作为整个进程的网络隔离证明；未配置 resolver 时使用系统 DNS，proxy masquerade 也需抓包确认 | [官方配置说明](https://v2.hysteria.network/docs/advanced/Full-Server-Config/) 将 direct、resolver、masquerade 分开配置。尚未证明所有域名请求/伪装请求经 NAT，不能声称零 DNS 泄漏 |
| nft 被特权进程清空 | **仍有检测窗口，生产阻塞**。原 Watchdog 是分钟级 timer；现检测到失效先停相关临时监听再修复 | 不能保证 flush 后到检测前没有短暂无限制访问。禁止把 timer 当作即时的安全边界；需设计独立持久执行层并完成压力故障验收 |
| IPv6 | HY2 保留族隔离，NAT 仅 IPv4 出口且 WG AllowedIPs 为 IPv4；CI 编译真实 IPv6 动态集合规则 | 完整 IPv6 入站、v4-mapped 绕过、DNS AAAA 及客户端兼容仍待双栈 VPS 验收 |

NAT INPUT 限制有意禁止 WG Peer 访问 NAT 主机的 SSH、DNS、ping 等服务。管理须走独立管理网络。特殊网络需求不能通过删除默认拒绝规则解决，应另行设计显式授权。

## P1 配额与临时节点

| 问题 | 已实施处理 | 验证/剩余风险 |
|---|---|---|
| 保存时采样到重建之间丢字节 | 正常保存只写检查点，不重建 nft 对象 | 可执行生成函数测试断言 baseline 不变且不调用 rebuild；CI 真实 nft 验证双向共用一个 quota |
| 重复 restore 退还未保存流量 | 完整 active/exhausted 对象不重建；丢规则时尽量保存可读 live usage；完全丢对象按检查点恢复 | 覆盖旧状态无 checkpoint、新状态、缺失 live、active、exhausted；异常断电仍可丢失最近 5 分钟未保存量 |
| 保存/重置/恢复锁竞争 | 每个单项入口在读元数据之前获取 quota 锁 | Linux 真实 flock 冲突测试；长时间并发创建/删除/重置仍需真机压力测试 |
| 重复 IP restore 清空槽位 | active set 不重建 | 执行真实生成函数；内核测试加载 IPv4/IPv6 集合。跨来源/到期回收仍需真机验收 |
| 停止 helper 变量未定义 | `local tag=... bypass=...${tag}` 拆开赋值，避免 nounset 阻断停止 | ShellCheck SC2318 + 执行 helper 时 unset 全局 tag 的回归 |
| 删除失败吞错误 | 保留原单节点删除失败留元数据机制；clear-all 返回失败，不虚报全部成功 | 模拟失败检查退出码；仍需 systemd ExecStopPost 与到期并发覆盖 |
| 计费口径 | 双向 quota 按经过服务 UDP 端口的 IP 包字节计数，共用同一个对象；不是应用净载荷 | 内核固定 100-byte UDP 报文每方向 128 bytes；drop 规则先于 accepted counter，配额耗尽后 counter 不再增加。多核高并发极限边界未测 |

## P2 安装、供应链、运维

- 首次 acme install-cert 会先执行 reload hook，再由主部署写 main.env。原 hook 遇空状态失败；现仅在无主配置、无主状态、服务不活跃时跳过重启，部署随后检查证书有效性、域名和密钥。已有配置缺失状态仍失败。
- 真机反馈另确认首次安装客户端时的来源路径缺陷：单脚本被命名为 get_acme.sh，且不在来源目录执行，而上游 install 会复制当前目录的 acme.sh。rc.2 修正 basename/cwd/显式 home，并新增真正上游脚本的隔离安装实验；该实验不进行 CA 签发。
- shutdown-save 以前只 enable，首次重启前没有 active 的 RemainAfterExit 服务；改为 enable --now。仍需真实 reboot 证明服务停止顺序和最终计数。
- common.sh 会开启 errexit，导致 doctor 首项错误时退出；doctor 显式关闭 errexit，修正密码路径。natjichang check 现在传播 doctor 错误。
- 本地核心先校验 SHA-256 再执行，require 策略还要求 `HYSTERIA_LOCAL_BINARY`，避免上游安装器在本项目验证前执行远程核心。默认 warn 为兼容保留，**不提供严格供应链保证**；对现有二进制的哈希需与目标版本匹配。
- 安装器下载限制 HTTPS 重定向；仍需操作者提供从可信渠道获取的 SHA-256。自动记录下载结果的 hash 不是独立真实性验证。
- 初次审查时 GitHub 仓库为 private，后续用户真机反馈时已公开；提供匿名 raw 和 `gh auth login` + 固定 ref 下载方案。分支未合并前，main 下载不会带上候选修复；本次未改变仓库可见性。
- 无授权 VPS/域名/客户端，未执行发行版完整 apt 安装、ACME 公网签发/续期、核心真实升级失败回滚、重启恢复。生成器隔离目录执行及回滚函数测试不能代替这些测试。

## P3 性能与 jieshu 差距

正常配额保存不再删除/重建每端口 nft 对象；这是可执行测试验证的命令路径变化，**未提供 CPU 百分比或吞吐收益**。Watchdog 的批量状态快照、快照一致性验证和大量节点基准尚未实现；新增失效检测预检查会增加健康巡检开销，需先测量再优化。

jieshu 正式版有批量 quota/watchdog 查询、更多行为测试、真机重启探针、NAT Peer 主动探测、维护窗口说明。ceshi 现补充了 CI、实际生成器校验、真实内核 WG/NAT/配额实验、版本与发布门槛，但尚缺完整 HY2 协议客户端验收、同等巡检批量化、长期稳定性及 DNS/全规则清空的安全闭环。portbw/SOCKS5 属于独立可选产品，不是 HY2 核心缺失功能；没有复制 VLESS/Reality 实现。

结论：可以提交候选 PR 供审查和隔离测试，**不建议作为生产正式版合并 main**。完成剩余 P0 与 [真机验收](VPS_ACCEPTANCE.md) 后再作发布决定。
