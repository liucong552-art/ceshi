# 变更记录

## 3.3.2-rc.2 — 2026-10-31（候选版）

- 修复真机首次安装 acme.sh 的 `cannot copy acme.sh`：下载保留上游要求的 basename，子 shell 进入来源目录后执行安装，明确 `--home`，使用 `--no-profile` 避免改写登录配置。
- 新增三项生成函数回归：无关工作目录下首次安装、重复安装不覆盖账户、失败返回非零并保留账户。
- 新增真正上游 3.1.2 脚本的安装实验：固定 SHA-256 后执行，在临时目录复现原 cp 失败，验证修复后安装/版本/重复执行/续期 cron 写入；CA 调用与系统 cron 均隔离。
- 增加 Debian 13 容器验证该安装路径。公网签发、续期和 HY2 客户端连通性仍需真机验收；本次未连接用户 VPS、未合并 main。

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
