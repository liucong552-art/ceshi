# 发布、兼容与回滚

版本来源是 `VERSION`，四入口和嵌入 common.sh 的版本须一致。3.3.2-rc.1 为候选版；本次不会创建正式 release、tag 或自动合并 main。

发布门槛：

1. PR 最新提交的两个 Ubuntu 静态/生成器 job 和 kernel-lab job 全部通过。
2. 逐项完成 [Debian/Ubuntu 验收](VPS_ACCEPTANCE.md)，保存脱敏结果、发行版、内核、核心版本、Git 提交和计数记录。
3. 解决审查报告中 DNS/masquerade 与特权 nft flush 检测窗口两个 P0 阻塞项；确认所需的生产安全边界。
4. 在测试机演练升级和快照回滚；维护者审查差异，用户明确确认后才可合并。
5. 从已验证 commit 生成 release 包、`SHA256SUMS` 和变更说明。sha256sum 是完整性检查，发布者身份仍取决于可信 GitHub ref/签名渠道。

## 新旧状态

旧 schema 1 无 `CHECKPOINT_USED_BYTES` 时，以 `SAVED_USED_BYTES` 为检查点；ID、端口、密码、到期时间及策略 tuple 不变。新管理器仍写 schema 1，增加可选检查点字段。**旧管理器不了解检查点：不能在新状态目录上直接降级**，否则旧 restore 可能退还未折算的用量。

推荐回滚方法：安装前创建 VPS 提供商快照并导出加密/受限权限的备份。在完全隔离的测试机上验证快照恢复，再用于维护窗口。恢复快照会回到快照时的节点和计费状态，维护窗口后新增节点和流量需单独核对。

HY2 VPS 安装前，以 root 在维护窗口执行：

```bash
umask 077
backup=/root/hy2-before-$(date +%Y%m%d-%H%M%S).tar.gz
paths=()
for p in etc/default/hy2-main etc/hysteria var/lib/hy2 usr/local/lib/hy2 usr/local/sbin etc/systemd/system root/.acme.sh; do
  [ ! -e "/$p" ] || paths+=("$p")
done
tar -C / -czf "$backup" "${paths[@]}"
chmod 600 "$backup"
```

此备份包含密码、TLS/WG 私钥（若另备份 WireGuard）、订阅及 ACME 账户，不能上传 PR/Issue。它包含部分共享系统目录；不要在在线机器上直接全量解压覆盖。优先用提供商快照恢复；按文件恢复须先停止全部相关服务并审阅备份内容。

NAT 机另行备份 `/etc/wireguard`、专属 firewall helper/service/timer、sysctl 文件及 `iptables-save`。NAT init/add/del 会重启整条 WG 接口，不承诺 Peer 无中断热更新。

安装器内部事务用于当前操作失败时恢复已有文件和启用状态，不能恢复 apt 已安装的软件包、远程 CA 账户、外部安装器的所有副作用或强制断电。勿把事务回滚等同于完整服务器快照。

## 严格供应链配置

先选择官方 release 的核心版本和对应架构，在可信环境核验 hash。上传 root 所有、不可被 group/other 写入的可执行文件，再在 `/etc/default/hy2-main` 配置：

```ini
REMOTE_SCRIPT_POLICY=require
HYSTERIA_VERSION=vX.Y.Z
HYSTERIA_LOCAL_BINARY=/root/verified-hysteria
HYSTERIA_BINARY_SHA256=替换为已核验核心的64位SHA256
HYSTERIA_INSTALLER_SHA256=替换为已核验安装器的64位SHA256
ACME_INSTALLER_SHA256=替换为固定acme版本的64位SHA256
```

继续通过原命令 `HYSTERIA_FORCE_UPDATE=1 bash /root/onekey_hy2_main_tls.sh` 执行。require 只允许本地核心是有意的安全收紧；回到兼容的 warn 策略会重新接受远程安装器的供应链风险，不应作为生产故障绕过方案。

## 删除与恢复

单节点：`FORCE=1 hy2_cleanup_one.sh hy2-temp-ID`；全部临时节点：`hy2_clear_all.sh`。失败会返回非零并保留需要重试的状态，检查日志、修复 nft 后再运行。勿手工删除状态文件释放仍被旧规则占用的端口。

项目仍无全量自动卸载器。需要移除完整系统时先导出受限备份，在测试机恢复干净快照/重装系统。手工停用服务时不要顺手删除 WG fail-close 规则或混用 `nft flush ruleset`；先停止全部 NAT 临时服务、确认 UDP 监听消失，再清理本项目资源。
