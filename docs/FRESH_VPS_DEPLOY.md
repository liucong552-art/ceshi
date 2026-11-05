# HY2 新 VPS 首次部署手册（与已验收 jieshu 共用 NAT、portbw）

> **适用场景：** 以后换一台全新、独立的 Debian 11+ / Ubuntu 20.04+ VPS 安装 HY2。推荐 Debian 13（已在 helan 测试过核心路径）。**不从 helan 迁移任何数据**；不复制其 HY2 URL、密码、WireGuard 私钥、配额、日志或测试临时节点。已验收的 jieshu 项目仓库和现有 NAT 服务保持原样。
>
> **发布边界：** 这份手册及其 CI 契约已校验，但**本轮未在新的空白 VPS 上重新执行真实 ACME 签证/完整端到端安装**；helan 的通过记录不能冒充另一台全新机器上的运行结果。首次上新 VPS，仍需做一次基础联网、TLS 和真实客户端冒烟检查；**不重复** jieshu NAT、portbw 共有算法或 72 小时/30 天专项测试。

## 一、准备（仅在新 HY2 VPS）

- root 权限、PID 1 为 systemd、Debian 11+/Ubuntu 20.04+。
- 给新 VPS 配置**新的** IPv4 公网地址与 A 域名（DNS only、禁止 Cloudflare 代理）；确保 TCP/80 公网可达，用于 Hysteria 主节点 ZeroSSL/ACME standalone 签证。
- 防火墙按需放行 HY2 主节点 UDP/443、临时节点 UDP/40000–50050；启用 WG-NAT 时在对应出口和 VPS 允许必要的 UDP/51820。TCP/80 是证书首次签发前提，不应提前被其他服务占用。
- NAT 出口仍使用 **jieshu 已验收的 NAT**；如果新 HY2 VPS 没有自己的 Peer，后续需要在既有 NAT 机上**新增一个 Peer**（是正常接入动作，不是重新安装 NAT；现有 `nat.sh add` 可能重启共享 WG，要在维护窗口操作）。
- 所有命令均在**新 HY2 VPS**执行，除非步骤明确写“既有 NAT 出口机”。

### 1. 下载仅检查、不修改系统的前置工具

```bash
apt-get update && apt-get install -y curl ca-certificates iproute2
curl -fsSL --retry 3 \
  https://raw.githubusercontent.com/liucong552-art/ceshi/main/scripts/hy2_fresh_preflight.sh \
  -o /root/hy2_fresh_preflight.sh
bash -n /root/hy2_fresh_preflight.sh && bash /root/hy2_fresh_preflight.sh before
```

预期 `FRESH_PREFLIGHT_BEFORE=PASS` 与 `READ_ONLY=YES`。如果发现旧 HY2 状态或端口占用会拒绝按全新安装继续；**不要删除或绕过已有运行数据**。本工具只检查本机基本环境，不代替公网防火墙、DNS 和证书签发验证。

## 二、安装 HY2 管理器（新 HY2 VPS）

```bash
curl -fsSL --retry 3 \
  https://raw.githubusercontent.com/liucong552-art/ceshi/main/hy2.sh \
  -o /root/hy2.sh
chmod 700 /root/hy2.sh
bash -n /root/hy2.sh && bash /root/hy2.sh install
bash /root/hy2_fresh_preflight.sh manager
```

必须看到管理器安装成功、`FRESH_PREFLIGHT_MANAGER=PASS`。本步骤**只部署管理器和 systemd 定时器**，不会自动创建一个可连接的 HY2 主节点。主脚本生成 `/etc/default/hy2-main` 与 `/root/onekey_hy2_main_tls.sh`，含网络/证书/密钥配置的项目文件均在新 VPS 本地，不从 helan 复制。

## 三、签发 TLS 并创建 HY2 主节点（新 HY2 VPS）

```bash
nano /etc/default/hy2-main
```

至少填写 `HY_DOMAIN=新VPS的A域名` 与 `ACME_EMAIL=有效邮箱`，保持 `HY_LISTEN=0.0.0.0:443`；确认域名解析的 A 记录指向**当前新 VPS**，TCP/80 可达。主节点必须使用受信任证书；如需可重复部署，提前核对并固定经过验收的 Hysteria 版本、安装器和二进制 SHA256，避免 `latest` 后续发生意外变化。不要将该配置文件或生成的 HY2 链接提交到 GitHub。

```bash
bash /root/onekey_hy2_main_tls.sh
bash /root/hy2_fresh_preflight.sh main
hy2_doctor.sh --strict
```

预期 `FRESH_PREFLIGHT_MAIN=PASS`，`hy2.service` 正常、UDP/443 监听、Doctor 无严重错误。**实际从 v2rayN/其他 HY2 客户端导入新生成的主节点并验证可用**；这是新机的基础接入冒烟测试，不能仅凭本地服务状态替代。

新主节点链接在 **新 VPS 本地** `/root/hy2_main_url.txt`，包含密码；勿上传公开日志。

## 四、复用现成 jieshu NAT 出口（可选、只为需要 NAT 的 HY2 临时节点）

**已有 NAT 机绝对不需要下载或运行 `ceshi/nat.sh`，尤其不得执行 `init`；不重新安装 NAT，也不覆盖 `jieshu` 管理器。** 现成 NAT 服务继续使用原版 jieshu `/root/nat.sh` 和它管理的 `wg-exit`、Peer 探测 timer。

**在新 HY2 VPS**（不是 NAT 机）：

```bash
curl -fsSL --retry 3 \
  https://raw.githubusercontent.com/liucong552-art/ceshi/main/vpswg.sh \
  -o /root/vpswg.sh
chmod 700 /root/vpswg.sh
bash -n /root/vpswg.sh && bash /root/vpswg.sh
cat /etc/wireguard/wg-nat.pub
```

记下**新 VPS**的 WG 公钥（不是原 helan 公钥）。**在既有 jieshu NAT 机**，由管理员查看原有 Peer 及本次新机的对应关系。如果尚无此 VPS 的 Peer，在确认不会覆盖其他 VPS 名称、并安排共享 WG 维护窗口后按 jieshu 原有命令：

```bash
bash /root/nat.sh list
# 仅当此 VPS 的 Peer 尚不存在且已安排维护窗口：
# bash /root/nat.sh add 新唯一Peer名称 新VPS的IPv4域名 '新VPS的WireGuard公钥'
```

这不是 HY2 新 NAT 安装；`add` 会由原版 jieshu 管理器分配 WG 地址，并打印一整条 `wg_nat_set_peer.sh` 回填命令。如果 Peer 原本已经正确绑定新 VPS 的公钥、地址，直接使用相符的映射，不要重复添加。

**回到新 HY2 VPS**，执行 NAT 管理器给出的准确回填命令（**不要猜或复制示例的公钥、WG 地址**），然后：

```bash
/usr/local/sbin/wg_nat_healthcheck.sh
curl -fsSL --retry 3 \
  https://raw.githubusercontent.com/liucong552-art/ceshi/main/natjichang.sh \
  -o /root/natjichang.sh
chmod 700 /root/natjichang.sh
bash -n /root/natjichang.sh && bash /root/natjichang.sh
bash /root/hy2_fresh_preflight.sh wg
```

预期 `FRESH_PREFLIGHT_WG=PASS`。**区别：** 前面主动执行的 `wg_nat_healthcheck.sh` 会修复 sysctl、WG guard 路由/防火墙规则，并进行真实 NAT 出口请求；它不是只读命令。`hy2_fresh_preflight.sh wg` 则仅以只读方式检查服务、Peer、最近握手和标记路由，不能单凭它判定实际公网出口 IP 或真实客户端已连通。仅新 VPS 安装 `natjichang.sh`，为后续 `hy2_mktemp_nat.sh` 提供入口。需要时再创建 NAT 临时节点，并用客户端检查出口是否为既有 NAT；不改动 NAT 管理器自身。

## 五、按需启用 jieshu 的已验收通用能力（新 HY2 VPS）

这些模块**是可选项**，不要为了“完全对标”在未使用时无条件安装。若需要，在新 HY2 VPS 安装，保持已验收源码不变。

### A. 通用 portbw（直接复用 jieshu 同一源码）

`portbw.py` 与 `portbw-install.sh` 在两个仓库的 Git Blob SHA 完全相同，且曾在 jieshu 实测自动触发/恢复。**只需按通用安装器的正常首次安装流程**：

```bash
apt-get update && apt-get install -y curl ca-certificates python3 iproute2 nftables util-linux coreutils procps
curl -fsSL --retry 3 \
  https://raw.githubusercontent.com/liucong552-art/ceshi/main/portbw-install.sh \
  -o /root/portbw-install.sh
curl -fsSL --retry 3 \
  https://raw.githubusercontent.com/liucong552-art/ceshi/main/portbw.py \
  -o /root/portbw.py
bash -n /root/portbw-install.sh
bash /root/portbw-install.sh check &&
bash /root/portbw-install.sh install &&
bash /root/portbw-install.sh verify &&
portbw audit
```

若 `check` 因自动网卡检测失败，先确认真实物理出口网卡，再在 `check` 与 `install` 同时传递 `--iface 实际网卡`；不可猜网卡。安装器**不会自动创建任何限速策略**，需要限速的端口使用 `portbw set/auto set` 按 README 第三章/第四章单独配置。这里不重新压测既有通用算法。

### B. HY2 专属 UDP 每日流量统计（需要时安装）

```bash
curl -fsSL --retry 3 \
  https://raw.githubusercontent.com/liucong552-art/ceshi/main/traffic.py \
  -o /root/hy2_traffic.py
python3 -m py_compile /root/hy2_traffic.py &&
python3 /root/hy2_traffic.py --install
hy2_traffic main
```

统计框架借鉴了 jieshu，但 HY2 的 UDP 端口、密码归属与 nft 表不同，不能直接运行 VLESS 的 `traffic.py` 替代此版本。

### C. HY2 异常审计、手工防火墙

可选 `hy2-abuse.py` 与 `hy2-guard.py` 是专属管理入口；按本仓库 README 说明安装，**不继承任何 helan 的封禁列表或 NAT/WG 密钥**。

## 六、新机器首次完成标准

在新 HY2 VPS 核对：

1. `FRESH_PREFLIGHT_MANAGER=PASS`、`FRESH_PREFLIGHT_MAIN=PASS`；如配置了共享 NAT，`FRESH_PREFLIGHT_WG=PASS`。
2. `hy2_doctor.sh --strict` 无严重错误，`hy2.service` 工作，客户端可实际导入新生成链接并连通。
3. 需要临时节点时，仅创建**一枚短期、专用的全新测试节点**，验证后安全删除；勿在正式业务端口上运行会改配额/规则的批量验收工具。此前 `helan` 的 `tests/hy2_batch_acceptance.py` 明确只允许在测试机运行，**不要改掉其主机保护再在新 VPS 上用 `--live`/ `--edges`/ `--recovery`**。
4. NAT 与通用 portbw 的原有真机验收直接继承 `jieshu`，新 VPS 仅验证**新 Peer 的连通**及新服务进程状态，不重新部署 NAT、不重复压测同一算法。
5. IPv6、真实 30 天自动到期重置、完整 72 小时宽限、证书实际轮换和异常断电保留为条件性/长期专项，不能由常规部署检查冒充已验证。

**全程没有数据迁移要求：新 VPS 生成新凭据；测试机 `helan` 的运行数据不需要保留。** 是否删除或关停 `helan` 是服务器管理员自己的后续操作，本手册不提供自动删除、重装或远程清理命令。
