# Debian/Ubuntu 真机验收（未执行的发布门槛）

仅使用两台可重建、无现有客户业务的 VPS（HY2 VPS + NAT 机），以及至少三个不同公网来源的 HY2 客户端。需要两个域名 A/AAAA、TCP/80、UDP/443/临时端口/WG 端口、防火墙控制台和提供商快照。不得在已有生产服务器执行故障注入。所有“通过”必须由操作者实际记录。

建议矩阵：Debian 11/12/13、Ubuntu 20.04/22.04/24.04，各选用发行版内核和 systemd；超出矩阵的系统单独验收。当前 CI 为 Ubuntu 22.04/24.04，未测试的系统不能自动标为通过。

## 0. 固定候选提交、取证与快照

安装命令见 README；PR 未合并前将下载 ref 设为 `codex/hy2-hardening-20261031`，解析为同一个 commit SHA 后下载四文件。不要误测 main 的旧版本。

```bash
uname -a
cat /etc/os-release
systemctl --version | head -n1
date -Is
```

建立两台机器的快照。按 RELEASE.md 保存受限备份；日志不应含私钥、密码、Token 或完整订阅。先在隔离机器确认快照可恢复。

## 1. 全新安装和重复安装

HY2 VPS：`bash /root/hy2.sh`，编辑 `/etc/default/hy2-main` 的 HY_DOMAIN/ACME_EMAIL，确认 DNS，运行 `bash /root/onekey_hy2_main_tls.sh`。

```bash
hy2_doctor.sh --strict
systemctl is-active hy2.service hy2-managed-shutdown-save.service
systemctl list-timers 'hy2-*' 'pq-*'
stat -c '%a %U %n' /etc/default/hy2-main /var/lib/hy2/main/main.password
openssl x509 -in /etc/hysteria/certs/你的域名/fullchain.pem -noout -dates -checkhost 你的域名
```

导入 `/root/hy2_main_url.txt` 到客户端，关闭其他代理，确认实际 HTTPS/TCP 和 UDP 请求可用，出口为 HY2 VPS。记录延迟和连续吞吐，不只检查服务 active。

再次执行同一个 hy2.sh 和主部署；检查密码文件 SHA-256、现有临时 ID/端口/密码/到期时间、WG Peer 未变。核心在 install-only 下不得偷偷升级。hash 结果仅留本机，勿提交秘密原文。

## 2. 普通 IPv4/IPv6 临时节点、到期与订阅

```bash
id=accept4 PORT_START=40000 PORT_END=40000 IP_VERSION=4 IP_LIMIT=1 PQ_GIB=0.01 D=300 hy2_mktemp.sh
id=accept6 PORT_START=40001 PORT_END=40001 IP_VERSION=6 IP_LIMIT=1 PQ_GIB=0.01 D=300 hy2_mktemp.sh
hy2_audit.sh
hy2_temp_sub.sh
```

分别从 IPv4/IPv6 客户端连接，验证不能借另一族地址进入同一端口；尤其用 IPv4-mapped 地址尝试 IPv6 监听。检查 UDP 实际收发和证书 SNI。等待 5 分钟，确认服务/配置/配额/IP set/meta/订阅均清理；重复清理不得删除其他节点。

删除测试另创建 `id=delete4 ... D=3600`，执行 `FORCE=1 hy2_cleanup_one.sh hy2-temp-delete4`，检查无遗留规则/对象。制造 nft 删除失败时，清理必须非零、保留状态；修复故障后可重复清理。不要在生产机器制造删除失败。

## 3. 流量配额、来源 IP 与持久化

在测试节点设置约 10 MiB：`pq_add.sh 40000 0.01`。分别进行上传和下载，用 `nft list quota inet hy2_pq hy2_pq_q_40000` 与 `pq_audit.sh` 观察总量。达到阈值后新旧客户端均不能继续实际转发。计数含 IP/UDP/QUIC 开销，不能与应用净载荷完全相等。

```bash
pq_save_state.sh
pq_restore_all.sh
pq_save_state.sh
pq_restore_all.sh
pq_audit.sh
```

连续保存/恢复不能退还已用配额，也不能重复累计同一 generation 的计数。检查 `CHECKPOINT_USED_BYTES` 随保存增长，baseline 不因正常 save 改变。

`ip_set.sh 40000 1 30`：来源 A 持续使用时，来源 B/C 必须无法新接入；停止 A 并等满 sticky 后 B 可用。更换源端口仍算同一 IP。并发 B/C 抢最后一个槽位不能同时成功。重复 iplimit_restore_all 不得踢出现有槽位。IPv6 再做一次。

定时重置测试：创建有效期超过 30 天的测试节点，备份配额 meta，在维护窗口将 `NEXT_RESET_EPOCH` 调整到当前之前；运行 `pq_reset_due.sh`，确认只重置到期窗口、再次运行不重复重置，并发 save 不破坏总量。修改后恢复测试状态，不采用长期等待的结果冒充已测。

## 4. NAT 多 Peer 与实际出口

在 NAT 机执行 `bash /root/nat.sh init`；HY2 VPS 执行 `bash /root/vpswg.sh`。按 README 在 NAT 机添加两台测试 VPS 的公钥，各回填地址。

NAT 机：同名 add 保留地址；更新 Endpoint/公钥；不同 Peer 不得复用相同公钥/WG 地址；del 一个 Peer 后另一个仍可恢复使用。init/add/del 会重启 WG，记录中断时间。并发 add/del 应串行且无重复地址。

HY2 VPS：运行 `wg_nat_healthcheck.sh`，再安装 natjichang.sh：

```bash
id=nat4 IP_VERSION=4 IP_LIMIT=1 PQ_GIB=1 D=3600 hy2_mktemp_nat.sh
id=nat6 IP_VERSION=6 IP_LIMIT=1 PQ_GIB=1 D=3600 hy2_mktemp_nat.sh
```

两个客户端通过 HY2 发起实际 HTTPS 和 UDP 请求，公网出口都应等于 NAT 机。**wg_nat_healthcheck 的出口测试不能替代客户端穿过 HY2 的出口测试。** 尝试访问 NAT 主机 WG 地址、另一个 Peer、RFC1918 与 169.254.169.254，均须拒绝。

## 5. WG 断线、路由和防火墙丢失

先在 HY2 VPS 公网网卡抓包，并在客户端连续请求一个你控制的、记录来源 IP 的 HTTPS/UDP 服务。不要用订阅“仍可导入”判定无泄漏。

```bash
systemctl stop wg-quick@wg-nat.service
systemctl start hy2-managed-watchdog.service
hy2_audit.sh
ip -4 rule show
ip -4 route show table 100
```

NAT 节点必须断流；受控目标不得看到 VPS IP。再分别测试删除 WG 默认路由、删除策略 rule、删除自有 OUTPUT guard、以及测试快照上同时删除这些保护，观察 bound socket 是否失败。每项后恢复快照并重新核对状态，不使用一段通用 flush 命令误伤宿主机其他规则。

DNS 必测：客户端向 HY2 请求随机、未缓存的域名，在 VPS 公网网卡抓 UDP/TCP 53、DoT/DoH 和 masquerade HTTP 请求。记录是否有本机解析/伪装出网。当前版本尚未闭环此项，出现 DNS/masquerade 公网流量时应判定相应隐私要求不满足。

## 6. nft 缺失和安全恢复

仅在可恢复快照的测试 VPS 上，持续客户端流量，删除单个 HY2 配额/IP 规则，再删除本项目 `inet hy2_pq` 或 `inet hy2_iplimit` 表。记录删除时刻、继续放行量、检测时刻、监听停止时刻和恢复时刻。

手动运行 `systemctl start hy2-managed-watchdog.service` 应先停止失去保护的临时节点，再修复并尝试恢复。手动运行 `hy2_restore_all.sh` 应保持完整的其他节点状态。必须承认删除后到检测前的窗口；当前方案不满足“任意特权 flush 后零窗口无限制访问”的发布要求。

## 7. 证书续期、重启和升级失败

已连接主节点及临时节点时，先运行 `/usr/local/lib/hy2/acme-reload.sh` 验证钩子路径。真正续期需在测试域名和 CA 允许的限额内运行 acme.sh `--renew -d 域名 --force`，核验证书序列号/有效期改变、原密码和到期时间不变。不要频繁强制续期耗尽 CA 限额。

创建 1 小时普通/NAT 节点，消耗部分配额并 `pq_save_state.sh`，记录匿名化审计结果，重启两台 VPS。等待 WG 握手和 timer 就绪，运行 strict doctor、审计及实际客户端请求；配额不得恢复为满额，过期节点不能复活。

升级前快照。使用已核验的不同版本本地核心及对应哈希执行升级，确认全部活动服务配置兼容、监听稳定。再用错误哈希、损坏二进制、无法启动服务分别注入失败；旧核心/配置/活动服务应恢复，敏感文件仍为 600/700。检查临时 transaction 目录没有秘密残留。这里的升级/回滚结果仍需实际运行，不能由单元测试推定。

## 8. 并发、锁与最终记录

使用不同 ID 并行创建 10 个测试节点，随后并发 audit、save、reset、cleanup 和 watchdog；确认无重复端口、死锁、孤儿规则。测试服务异常退出时的 ExecStopPost，特别是恰好过期和父进程持 temp/WG 锁的情况。保存脱敏日志并核对所有命令退出码。

记录清单：系统/内核/systemd/核心/提交版本、每项 PASS/FAIL/未测、入口机器、异常时日志、实际出口 IP、丢规则时间窗口、配额误差、重启恢复时长、CPU/IO 测量时长。只有所有必测项完成且 P0 闭环后，才考虑正式发布。
