# 变更记录

## 3.3.2-rc.1 — 2026-10-31（候选版，禁止视为生产认证）

基于 ceshi/main `ddc338d83a1987ea6a186258ee006b35de95eb6b`。只读参考 jieshu/main `9db23a3551ed52daafefcb16071b7a705fd4515d`，未复制 VLESS 协议实现。

- NAT 机在完整 filter 事务内更新专属链，拒绝 Peer 访问出口机本身及 WAN 可达的私网、链路本地、云元数据等地址。
- 配额保存使用累计检查点，避免“采样后清零”丢失流量；重复 restore 保留完整计数器和动态 IP 槽位。
- 首次证书安装允许尚未创建主状态的 reload hook；随后部署验证证书有效期、域名及密钥匹配。
- 启动关机保存服务；修复 Watchdog 停止函数的 local 变量提前引用、doctor 提前退出、批量清理虚报成功、natjichang check 吞掉错误。
- 校验本地核心哈希后才执行 version。严格供应链策略要求预下载的本地核心及 SHA-256；HTTPS 重定向限制为 HTTPS。
- WG 管理拒绝其他接口占用相同 mark/table/priority；握手检查拒绝未来时间戳。
- Watchdog 检测到临时节点防护缺失时先停止监听再修复。
- 两版 Ubuntu CI、嵌套 Bash/Python 检查、ShellCheck、实际生成 systemd 文件校验、隔离 namespace 内真实 WireGuard/nft/iptables 测试。

兼容：原四入口和参数保留，状态 schema 仍为 1，新增可选 `CHECKPOINT_USED_BYTES`。旧 schema 1 状态可读取；降级必须按 [发布与回滚](docs/RELEASE.md) 操作，不能直接用旧管理器覆盖新状态。NAT Peer 访问私网/出口机的行为有意收紧；本项目只提供公网 IPv4 出口。

剩余发布阻塞项及验收范围见 [审查报告](docs/AUDIT.md)、[真机验收](docs/VPS_ACCEPTANCE.md)。
