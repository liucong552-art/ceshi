# HY2 v4 验证范围（更新于 2026-10-29）

对齐基准：`liucong552-art/jieshu` 的 `befe4f1efde04d87615e53358441b510f7954547`。
HY2 基础：`liucong552-art/ceshi` 的 `1ac939194fbdffdbe73e2e3d34a0bdaab2a92202`。

## 已完成

- 所有顶层 Shell/Python 文件，以及安装器内 37 个生成的 Shell/Python 载荷语法检查。
- 55 项本地回归：HY2 真实字段与 UDP 统计、配额中断提交不重复扣费、配额读取失败不记成 0、清理阶段的配额锁、双向共享配额、元数据按字面读取、URL 编码、批量查询异常回退、IP 防护缺失不命中快路径、握手时钟、只读 WG 防护检查、72 小时宽限与重启稳定期、统计幂等与端口复用、30 天清理，以及沿用组件的限速和 SOCKS5 回归。另验证端口限速源码哈希与 45 段手册 Bash 命令语法。
- 官方 Hysteria `v2.13.0` Linux amd64 核心的 SHA-256 校验：`907ba8c9693edb104b20582681fb7dc15639d5b64a9cbb616a7b539190a86691`。
- 使用实际 `hy2_write_server_cfg` 生成配置，在回环网络验证普通模式和 Salamander 模式：TLS 证书验证、TCP 数据传输、UDP 数据传输、访问日志、错误密码拒绝。测试专用证书由临时 CA/自签信任根提供给客户端，不设置 `insecure`；生产签发方式不变。
- 测试不安装系统服务、不改防火墙、不连接用户的 VPS。测试进程结束后移除临时文件。

执行命令：

```bash
python3 -B tests/check_bundle.py
python3 -B -m unittest discover -s tests -v
python3 -B tests/hy2_loopback.py /path/to/verified/hysteria
```

## 仍需真机验收

- 其他受支持 Debian/Ubuntu 版本的完整安装、实际证书续期、核心升级失败回滚。Helan Debian 13 的正式安装、ZeroSSL 首次签发、重载与重启已通过，见第三轮。
- 公网 IPv6 入站、真实流量下的端口自动限速。内核配额/IP 防护的隔离真机测试已通过，见第二轮记录。
- 真实 WG-NAT 出口、WG 中断与防泄漏、两台机器冷启动/重启恢复、NAT Peer 探测与多 Peer 并存。
- 生产规模下的 CPU 测量与一小时平均值。VLESS 的旧数据不能当成 HY2 的实测收益。
- 临时封禁、永久 IP 黑名单、开机恢复和最后一条解封后的清理；命令仅做 Bash 语法检查。

当前执行环境禁止 netlink 路由访问，`bindDevice=lo` 的实核测试在读取接口时报告 `operation not permitted`，因此不能在这里证明真实 WG 绑定。具备相应网络权限的测试机可补跑：

```bash
python3 -B tests/hy2_loopback.py /path/to/verified/hysteria --bind-device
```

这只额外验证本机 `lo` 绑定，仍不能代替两台真实服务器的 WG 断链验收。全部关键真机项目通过后再合并 `main`。

## Helan 第一轮真机执行入口

第一轮入口为 `tests/helan_round1.sh`，已收到 Helan 的实际运行输出，结果见下表；通过范围仅限本机 HY2 传输，不能替代后续防护验收。

在 `root@helan` 执行：

```bash
curl -fLsS --connect-timeout 10 --max-time 120 'https://raw.githubusercontent.com/liucong552-art/ceshi/feature/hy2-jieshu-parity/tests/helan_round1.sh' -o /root/helan_hy2_round1.sh && bash /root/helan_hy2_round1.sh
```

脚本只读记录已有服务、nft 表名和接口，下载固定提交的测试源码及校验过 SHA256 的官方核心，测试普通、Salamander、`bindDevice=lo` 三种模式。每种模式验证 TLS、TCP、UDP、访问日志和错误密码拒绝。

执行后保留 `/root/hy2-realtest.*/round1.log`、`result.json` 和失败诊断，移除下载的测试载荷；它不会安装管理系统、重启已有服务或改防火墙。此轮不测试公网 UDP 可达性、内核配额/IP 槽位、真实 WG-NAT 或系统重启。

### 已收到的 Helan 第一轮实测结果

用户于 2026-10-27 提供完整终端输出：北京时间 10:50:40 开始（日志 UTC 02:50:40），Debian 13，内核 `6.12.88+deb13-amd64`，1 CPU，约 1 GiB 内存，无现有本项目管理路径，nft 未安装。官方核心哈希核对一致。

| 模式 | TLS 验证 | TCP | UDP | 访问日志 | 错误密码拒绝 |
|---|---|---|---|---|---|
| plain | PASS | PASS | PASS | PASS | PASS |
| salamander | PASS | PASS | PASS | PASS | PASS |
| bindDevice=lo | PASS | PASS | PASS | PASS | PASS |

第一轮 15/15。该证据验证了 Helan 上真实核心传输和 lo 绑定，不将其视为真实 WG 出口、配额/IP 或重启验收。

## Helan 第二轮：真实内核防护测试

`tests/helan_round2.sh` 配合 `tests/hy2_kernel_acceptance.py` 在一次性网络命名空间中测试实际 nft 规则和数据包。候选管理器固定为 `d4c405bc19e2c2c2ce06d8c3c6be6376fe3617e8`；不会调用整套安装器或修改主机网络规则。

21 项覆盖双向配额、IPv4/IPv6 共用配额、保存/恢复不清零或重复计费、耗尽后恢复仍拒绝、IPv4/IPv6 槽位与隔离、并发抢占、刷新/空闲释放、真实 watchdog 健康巡检与删表恢复、中断提交恢复、不限 IP 时的地址族隔离、删除单端口保留其他端口，以及真实 HY2 加密代理达到配额后断流。

Helan 缺少 nft 时，仅让 APT 下载包并临时解包使用，不执行系统包安装或启动 nftables 服务。退出后测试网络空间销毁，保留结果及诊断；APT 索引可能更新。

已检查第二轮脚本语法和主机网络拒绝执行保护，本地回归为 58 项通过（新增 3 项隔离拒绝保护）。

### 已收到的 Helan 第二轮实测结果

用户于 2026-10-29 提供完整终端输出：北京时间 10:51:24 开始（日志 UTC 02:51:24），私有解包的 nftables 为 `1.1.3`，官方核心哈希一致。测试脚本固定为 `5115443ef98e9fcede3b8e29e05027783c18f44d`，管理器固定为 `d4c405bc19e2c2c2ce06d8c3c6be6376fe3617e8`。

全部 21 项通过，耗时 26.369 秒，退出码 0。主机网络空间与测试空间 ID 不同，退出后测试空间销毁；APT 输出确认仅下载并未安装包。

| 覆盖范围 | 项数 | 结果 |
|---|---:|---|
| IPv4/IPv6 UDP 基线、双向/共享配额、上传/下载耗尽拒绝 | 5 | PASS |
| 配额保存恢复、读失败保护、耗尽恢复 | 3 | PASS |
| IPv4/IPv6 槽位、并发抢占、活跃刷新与空闲释放 | 4 | PASS |
| watchdog 健康巡检、缺表修复、中断提交恢复 | 4 | PASS |
| 不限 IP 时地址族隔离、移除限制保留隔离、单端口删除 | 4 | PASS |
| 真实 HY2 加密代理达到配额后断流 | 1 | PASS |

前两轮累计 36 项真机检查通过。正式证书、完整 systemd 安装、真实 WG-NAT、重启恢复、自动限速实测与 CPU 测量仍未完成。

### 第一次安装后的关机保存服务

准备 systemd 验收时发现，安装器原先只 enable 关机保存服务，没有在当前开机周期启动它，导致首次重启前 ExecStop 尚未挂载。现于安装完成阶段启动该 oneshot/RemainAfterExit 服务；不改变配额保存或 watchdog 定时频率。后续真机验收必须同时检查它为 enabled 和 active。

## Helan 第三轮：正式安装与真实重启

入口 `tests/helan_round3.sh`，候选安装器固定为 `670c6fbe8a942796db59bbd9d5bb92606a012c30`。已收到 Helan 安装及实际重启后的完整输出，结果见下表。

`prepare` 仅允许全新 amd64 Helan，拒绝覆盖既有项目和 ACME 账户。本地提示输入域名和邮箱，严格检查所有 A 记录指向 Helan、无 AAAA 记录、TCP/80 和 UDP/443 未被占用。使用专用域名关闭 CDN 代理，并确保 TCP/80 公网可达。此轮会安装系统依赖、管理器、正式证书和 systemd 服务，保留配置及报告。

官方核心、官方安装器、acme.sh 均校验固定 SHA-256。正式证书通过系统公有 CA 信任链验证；客户端不指定自签 CA 或 insecure。

安装前修复两处首装问题，并新增本地回归：acme.sh 的 `--install` 要求当前目录包含 `acme.sh`，现使用私有引导目录正确命名、安装后清理；`--install-cert` 会立即执行重载命令，现仅在主节点部署事务中临时延迟该动作，待新配置、状态和监听完成后明确运行重载钩子。普通定时续期仍对缺失证书/状态报告失败。

前半 10 项实际检查：管理服务及首次关机保存启动、公有 CA TLS 的 TCP/UDP、真实临时节点 CLI 与订阅、主/临时配额及 IP 计数、真实耗尽断流、watchdog 保留状态、证书重载钩子、systemd 故障自动重启、到期 ExecStopPost 清理、重启前配额基线。

脚本不自动重启。前半成功后执行 `systemctl reboot`，重新 SSH 连接再运行 `bash /root/helan_hy2_round3.sh verify`。后半必须检测到真实 boot ID 变化，在任何手动修复前核对自动恢复；7 项覆盖服务自动启动、耗尽/活跃配额恢复、端口/凭据/绝对过期时间/订阅保持、正式 TLS 真实传输、耗尽节点继续拒绝、测试节点清理、撤去主节点的临时测试限制及严格 doctor 检查。主节点会保留运行，真实 WG-NAT、公网客户端 UDP、实际证书续期、自动限速与 CPU 测量另行验收。

本地回归 65 项通过（增加首次关机保存、3 项 ACME 首装回归及 3 项重启报告保护）；语法检查包含全部测试 Python/Shell。

### 已收到的 Helan 第三轮实测结果

用户于 2026-10-29 上传完整终端记录：北京时间 11:15:54 开始安装（UTC 03:15:54），脚本固定为 `2c82b7eb050a2489af95bbc46b4aadde17daf4c7`。前半 10/10 通过。执行真实 `reboot` 并重新 SSH 连接后，北京时间 11:19:47（UTC 03:19:47）运行 `verify`，后半 7/7 通过；结构化报告为 `passed=7`、`failed=0`、`reboot_acceptance_complete=true`。

| 覆盖范围 | 项数 | 结果 |
|---|---:|---|
| 正式管理器/systemd/首次关机保存、公有 CA TLS 主节点 TCP+UDP | 2 | PASS |
| 真实临时节点 CLI/订阅、主/临时节点配额与 IP、加密代理耗尽断流 | 3 | PASS |
| watchdog、正式证书重载、systemd 故障重启、到期清理、重启基线 | 5 | PASS |
| 重启自动恢复、活跃及耗尽配额、凭据/绝对过期时间/订阅 | 3 | PASS |
| 重启后实际传输、耗尽拒绝、测试节点清理、严格 doctor | 4 | PASS |

前三轮共 53 项通过。第三轮完成后测试临时节点和主节点的临时测试限制已清理，正式主节点保留运行。公网 Windows 客户端、真实双机 WG-NAT、实际 ACME 续期、自动限速与 CPU 测量仍未完成。终端开头的 404 来源于照抄旧 README 的仓库占位符；README 下载段已换为本仓库实际验收分支，并修正下载失败后继续 chmod 的流程。

## 第四轮入口：Windows 公网客户端

`tests/helan_round4.ps1` 在用户 Windows PowerShell 中执行，使用现有 SSH 身份文件和已经确认的主机密钥。通过 SSH 只读读取已安装的主节点配置，TLS 仅使用正式域名和系统信任链；不输出节点密码、不改 VPS 服务/防火墙、不启用系统全局代理。临时配置限制为当前 Windows 用户及 SYSTEM 可读，退出后停止本轮客户端并删除含凭据的配置。

官方 Windows amd64 `v2.13.0` 二进制 SHA-256 由官方 GitHub Release 资产 digest 核对，为 `162ef8fe55dc7ec810dda662908f8e65ec36bd6da39cb87c3bd66184ab68f067`。4 项检查分别为核心哈希、公网 HY2 TLS/认证、经 SOCKS5 的实际 HTTPS 及 Helan 出口 IP、经 HY2 UDP 转发的 DNS 事务 ID/应答/成功状态。

新增 Windows CI 仅在自身回环网络启动真实官方核心、受信任的临时 CA、TCP/UDP 回显，另检查 DNS 响应拒绝条件；不能当作用户 Windows 到 Helan 的公网结果。公网第四轮仍待用户执行。只读导出保护新增 4 项回归，本地 Python 回归现为 69 项通过。

Windows CI 已在提交 `2dd6358360d40cb9a3ae16762d0355adcc9c7ae3` 实际完成 4/4：官方核心哈希、受信任 TLS/认证、TCP 回显、UDP 回显与 DNS 拒绝保护；Linux 回归及真实核心测试同时通过。后续兼容检查使用 Windows 默认 PowerShell 5.1 解析入口。出口判定接受域名的公网 A 地址及服务器实际配置的公网 IPv6，避免双栈服务器访问 HTTPS 时因选择 IPv6 而误报。

## 重装 Debian 13 后的一次性非 WG 入口

用户于 2026-10-29 告知 Helan 已重新安装 Debian 13，并要求一次性测试全部、WG 后测。以上 53 项是**旧系统历史记录**；新系统须重新执行，不沿用旧结果。

入口 `tests/helan_onepass.ps1` 从 Windows 发起，使用已经确认的新主机密钥和本地现有 SSH 身份。服务器端 `tests/helan_onepass.py` 限定 root、Helan、amd64、Debian 13、真实 systemd 和全新项目路径，下载同一固定提交的完整源码并校验候选安装器与官方核心。所有安装及含凭据输出保存在 root 私有日志。用户在 Windows 执行一次后，systemd 后台完成服务器部分，自动重启一次；Windows 自动重连，再完成真正的公网客户端测试。同一提交、域名、邮箱及 CPU 时长重复执行会读取原任务进度，不重复安装或重新签发。

覆盖范围：

- 全部离线回归；实际普通/Salamander/lo 绑定的 TLS、TCP、UDP、日志及拒绝错误认证。
- 21 项实际 HY2 内核配额/IP/watchdog 用例，完整 systemd/正式证书安装、临时节点 CLI、订阅、到期清理及首次关机保存。
- 实际每日统计双向增长及缺表恢复；真实 portbw 安装、静态/自动策略；真实 SOCKS5 安装、认证、HTTPS、访问日志、每日统计。
- 实际错误核心的 SHA 拒绝及回滚，确认服务、凭据和配额保持；一次真实 ZeroSSL 强制续期、证书变化与正式 TLS 传输。
- 断开主机网络的两层 netns 实际报文实验：TCP/UDP、IPv4/IPv6 共用上/下行限速与内核超限计数、自动降速/保护/冷却/恢复、规则损坏修复与未知规则保护；真实 3proxy 错密、私网 DNS 目标、SMTP、UDP ASSOC、并发连接/IP 槽位、空闲释放、配额断流与恢复、域名访问日志；手册原命令的暂停/黑名单与清理。
- 默认完整 3600 秒 CPU 采样，场景为 1 个主 HY2、2 个临时 HY2、1 个 SOCKS5 账号及原有定时器的空闲受管栈。测量是整机非空闲 CPU，包含系统及 SSH 状态轮询，不表示生产负载或相对 VLESS 的收益。未降低任何业务保护或巡检频率。
- 真实重启后在手动修复前检查 HY2 自动恢复、耗尽拒绝、凭据/期限/统计/限速策略与服务保持、真实黑名单开机恢复；只清理测试账号及测试端口策略，保留已安装的管理器和主节点；最后进行 Windows 公网 TLS/认证、HTTPS 出口和 UDP DNS。

WG/双机 NAT 按用户要求排除。公网 IPv6 连通性与 Cloudflare DDNS 写入分别需要实际公网 IPv6 路径和单独配置的服务商凭据，缺少时明确列为未配置，不以回环/模拟代替。无这些外部条件的结果名称为「已配置的非 WG 验收完成」，不声称所有外部接口已验收。

从 Windows PowerShell 下载当前分支的固定快照入口（会提示输入域名/邮箱，也可用 `-Domain`、`-AcmeEmail` 显式传入）：

```powershell
$ref=(Invoke-RestMethod 'https://api.github.com/repos/liucong552-art/ceshi/git/ref/heads/feature/hy2-jieshu-parity').object.sha
$entry=Join-Path $env:TEMP 'helan_onepass.ps1'
Invoke-WebRequest -UseBasicParsing -Uri "https://raw.githubusercontent.com/liucong552-art/ceshi/$ref/tests/helan_onepass.ps1" -OutFile $entry
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $entry -RunnerRef $ref
```

应保持 Windows 窗口运行，预计约 70–90 分钟。服务器报告保存在 `/root/hy2-onepass.*/result.json`，各阶段私有日志位于其 `logs/`，Windows 保存汇总。失败阶段会保留诊断并停止，不把未执行项目标为通过。新系统的本轮测试尚待用户执行。

实现本地回归现为 78 项通过。新增可选组件真实报文 CI 已在 `2956804271b3907bf901fec211e0404059fbd2e4` 完成 6/6：双栈暂停/黑名单、真实 SOCKS5 防护与审计、共享上传、共享下载、规则故障与自动限速生命周期；每组包含实际数据包断言。该证据来自 CI，不能替代重装后的 Helan 输出。
