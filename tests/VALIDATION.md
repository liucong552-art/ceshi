# HY2 v4 验证范围（2026-10-27）

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

- 完整 Debian/Ubuntu + systemd 安装、ZeroSSL 签发/续期、核心升级失败回滚。
- 内核 nftables 实际配额拒绝、活跃 IP 槽位保留、IPv6 入站与地址族隔离、真实流量下的端口自动限速。
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

已检查第二轮脚本语法和主机网络拒绝执行保护，本地回归现为 58 项通过（新增 3 项隔离拒绝保护）。第二轮 21 项真实内核用例尚未运行，是否通过以 Helan 实际输出为准。正式证书、完整 systemd 安装、真实 WG-NAT、重启恢复、自动限速实测与 CPU 测量仍未完成。
