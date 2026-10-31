# HY2 主节点、临时节点与 WG-NAT

面向全新 Debian/Ubuntu VPS 的 Hysteria 2 五文件版。项目包含主 HY2 节点、普通临时节点、流量/IP 限制，以及可选的 WG-NAT 出口。

> 当前候选版本：`v3.3.2-rc.1`，未完成生产验收，不建议作为生产正式版发布。
> 仅用于全新机器。不会迁移旧版 Hysteria、旧临时节点或其他脚本留下的状态。

本次修复、兼容性和已知阻塞项见 [CHANGELOG](CHANGELOG.md)、[审查报告](docs/AUDIT.md)、[发布与回滚](docs/RELEASE.md)。完整实机流程见 [Debian/Ubuntu 真机验收](docs/VPS_ACCEPTANCE.md)。已有本项目 schema 1 状态可由候选版读取；其他项目/旧 Hysteria 状态不自动迁移。

| 文件 | 运行位置 | 用途 |
|---|---|---|
| `hy2.sh` | HY2 VPS | 安装主节点与普通临时节点管理系统 |
| `vpswg.sh` | HY2 VPS | 配置 WG-NAT 的 VPS 端 |
| `nat.sh` | NAT 出口机 | 配置并管理 NAT 出口及多个 VPS Peer |
| `natjichang.sh` | HY2 VPS | 安装 WG-NAT 临时节点工具 |
| `README.md` | — | 安装、管理和真机验收说明 |

> `natjichang.sh` 运行在 HY2 VPS，不是在 NAT 出口机。

## 一键下载与服务器测试（main）

**仓库当前是 private，下面匿名 raw 命令仅在仓库公开且 PR 合并后可用。** 当前 main 仍是旧版本；本次修复在 PR 分支 `codex/hy2-hardening-20261031`。本项目不会自动改变可见性或合并 PR。私有仓库请在对应机器完成 GitHub CLI 交互登录，再固定到同一提交下载；不要把 Token 填进脚本/URL：

```bash
apt-get update && apt-get install -y gh ca-certificates
gh auth login --hostname github.com --git-protocol https --web
```

每台机器执行以下下载块（仅下载，尚未安装）：

```bash
(
set -Eeuo pipefail
umask 077
ref=codex/hy2-hardening-20261031 # PR 合并并决定测试 main 后改成 main
commit=$(gh api "repos/liucong552-art/ceshi/commits/$ref" --jq .sha)
stage=$(mktemp -d /root/ceshi-download.XXXXXX)
trap 'rm -rf -- "$stage"' EXIT
for file in hy2.sh nat.sh vpswg.sh natjichang.sh SHA256SUMS; do
  gh api "repos/liucong552-art/ceshi/contents/$file?ref=$commit" \
    -H 'Accept: application/vnd.github.raw+json' >"$stage/$file"
done
(cd "$stage" && sha256sum -c SHA256SUMS)
for file in hy2.sh nat.sh vpswg.sh natjichang.sh; do
  bash -n "$stage/$file"
  install -m 700 "$stage/$file" "/root/$file"
done
printf 'Downloaded ceshi commit %s\n' "$commit"
)
```

随后按机器执行：**HY2 VPS** `bash /root/hy2.sh`；**NAT 出口机** `bash /root/nat.sh init`；**HY2 VPS** `bash /root/vpswg.sh`，完成 NAT Peer 回填和出口检查后再 `bash /root/natjichang.sh`。四入口位置见下表和后文，不能把四个脚本都在 NAT 机执行。

与 `jieshu` 类似，`ceshi/main` 提供可直接复制到全新 Debian 11+ / Ubuntu 20.04+ VPS 的安装命令。下载后先运行 `bash -n`；只有下载及语法检查成功才执行。**HY2 VPS 与 NAT 出口机分别操作，请勿混用。** 建议先给测试机建立 VPS 快照。

### 1. HY2 VPS：安装管理器

```bash
apt-get update && apt-get install -y curl ca-certificates
curl -fL --retry 3 https://raw.githubusercontent.com/liucong552-art/ceshi/main/hy2.sh -o /root/hy2.sh && chmod 700 /root/hy2.sh && bash -n /root/hy2.sh && bash /root/hy2.sh
```

首次安装只部署管理模块，**不会自动生成一个可用的主节点**。必须修改域名及邮箱、签发证书并部署主节点：

```bash
nano /etc/default/hy2-main
# 填写 HY_DOMAIN、ACME_EMAIL；保证 A 记录指向本机、TCP/80 可访问
bash /root/onekey_hy2_main_tls.sh
hy2_doctor.sh --strict
```

主节点 UDP/443 和临时节点 UDP 端口需要在服务商防火墙中放行。查看主节点链接：`cat /root/hy2_main_url.txt`。

### 2. NAT 出口机：安装 WireGuard NAT

```bash
apt-get update && apt-get install -y curl ca-certificates
curl -fL --retry 3 https://raw.githubusercontent.com/liucong552-art/ceshi/main/nat.sh -o /root/nat.sh && chmod 700 /root/nat.sh && bash -n /root/nat.sh && bash /root/nat.sh init
```

### 3. HY2 VPS：配置 WG-NAT（可选）

```bash
curl -fL --retry 3 https://raw.githubusercontent.com/liucong552-art/ceshi/main/vpswg.sh -o /root/vpswg.sh && chmod 700 /root/vpswg.sh && bash -n /root/vpswg.sh && bash /root/vpswg.sh
```

先按下方“部署 WG-NAT”章节，在 NAT 机执行 `nat.sh add`，回 HY2 VPS 执行 NAT 机输出的 `wg_nat_set_peer.sh` 命令，并确保 `wg_nat_healthcheck.sh` 成功。最后：

```bash
curl -fL --retry 3 https://raw.githubusercontent.com/liucong552-art/ceshi/main/natjichang.sh -o /root/natjichang.sh && chmod 700 /root/natjichang.sh && bash -n /root/natjichang.sh && bash /root/natjichang.sh
bash /root/natjichang.sh check
```

### 4. 最小真机测试（HY2 VPS）

```bash
bash /root/hy2.sh version
hy2_doctor.sh --strict
id=release-test IP_VERSION=4 IP_LIMIT=1 PQ_GIB=1 D=300 hy2_mktemp.sh
hy2_audit.sh
FORCE=1 hy2_cleanup_one.sh hy2-temp-release-test
```

以上命令需要先完成主节点部署。额外按文末验收清单验证 WG-NAT 出口、断线防泄漏、重启恢复、IPv6 和流量/IP 限制。CI 仅能自动验证源代码语法与静态约束，不等于真实服务器验收合格。

**注意：** `nat.sh init/add/del` 会重启 WG 接口/调整防火墙；不要直接在正在给客户提供出口的机器上试验。

## 使用前准备

- 全新 Debian 11+ 或 Ubuntu 20.04+
- `root` 用户，且 PID 1 为 systemd
- 一个 A 记录指向 HY2 VPS 公网 IPv4 的域名
- 创建 IPv6 临时节点时，再准备一个 AAAA 记录指向该 VPS 的域名
- TCP `80` 可从公网访问，用于首次签发证书
- 放行主节点 UDP `443`
- 放行临时节点 UDP 端口范围，默认 `40000-50050`
- 使用 WG-NAT 时，NAT 机与 HY2 VPS 都要放行 UDP `51820`
- 域名如使用 Cloudflare，必须保持 DNS only（灰云）

## 一、准备五个文件

将以下五个文件放在同一目录：

```text
hy2.sh
vpswg.sh
nat.sh
natjichang.sh
README.md
```

设置脚本权限：

```bash
chmod 700 hy2.sh vpswg.sh nat.sh natjichang.sh
```

上传到自己的 GitHub 仓库后，也可以使用 Raw 地址下载。示例：

```bash
RAW_BASE='https://raw.githubusercontent.com/<owner>/<repo>/refs/heads/main'
curl -fsSL "$RAW_BASE/hy2.sh" -o /root/hy2.sh
curl -fsSL "$RAW_BASE/vpswg.sh" -o /root/vpswg.sh
curl -fsSL "$RAW_BASE/nat.sh" -o /root/nat.sh
curl -fsSL "$RAW_BASE/natjichang.sh" -o /root/natjichang.sh
chmod 700 /root/hy2.sh /root/vpswg.sh /root/nat.sh /root/natjichang.sh
```

将 `<owner>/<repo>` 替换成实际仓库地址。

## 二、安装 HY2 主节点

### 1. 安装管理系统

在 **HY2 VPS** 执行：

```bash
bash /root/hy2.sh
```

脚本会创建：

- `/etc/default/hy2-main`
- `/root/onekey_hy2_main_tls.sh`
- `/usr/local/sbin/hy2_mktemp.sh`
- 配额、IP 限制、审计、清理、恢复和 watchdog 工具
- 对应的 systemd service 与 timer

### 2. 修改配置

```bash
nano /etc/default/hy2-main
```

最少需要填写：

```ini
HY_DOMAIN=hy2.example.com
ACME_EMAIL=admin@example.com
MASQ_URL=https://www.apple.com/
```

创建 IPv6 临时节点时再填写：

```ini
HY_IPV6_DOMAIN=hy2-v6.example.com
```

主要默认参数：

```ini
HY_LISTEN=0.0.0.0:443
TEMP_PORT_START=40000
TEMP_PORT_END=50050
ENABLE_SALAMANDER=0
HYSTERIA_VERSION=latest
HYSTERIA_UPDATE_POLICY=install-only
```

说明：

- 主节点固定监听 IPv4 UDP `443`
- 普通 IPv4 临时节点使用 `HY_DOMAIN`
- IPv6 临时节点使用 `HY_IPV6_DOMAIN`
- `install-only` 表示首次安装核心，之后重复执行部署不会偷偷升级 Hysteria
- 默认使用正式证书，不使用自签证书或跳过证书验证

### 3. 创建或更新主节点

```bash
bash /root/onekey_hy2_main_tls.sh
```

严格检查：

```bash
hy2_doctor.sh --strict
```

主节点链接：

```bash
cat /root/hy2_main_url.txt
```

Base64 订阅：

```bash
cat /root/hy2_main_subscription_base64.txt
```

链接默认为 v2rayN 可导入的 `hy2://` 格式。

### 4. 显式升级 Hysteria 核心

普通重装不会自动升级核心。需要升级时明确执行：

```bash
HYSTERIA_FORCE_UPDATE=1 HYSTERIA_VERSION=vX.Y.Z \
  bash /root/onekey_hy2_main_tls.sh
```

升级到官方当前稳定版：

```bash
HYSTERIA_FORCE_UPDATE=1 HYSTERIA_VERSION=latest \
  bash /root/onekey_hy2_main_tls.sh
```

升级过程会：

- 备份旧核心
- 使用候选核心加载主节点及全部临时节点配置
- 记录升级前活跃服务
- 重启并验证服务状态与 UDP 监听
- 任一步失败时恢复旧核心和原有服务

生产使用建议固定明确版本，并按需配置 `HYSTERIA_BINARY_SHA256`。

## 三、创建普通临时节点

`D` 是有效期秒数；`PQ_GIB` 是双向总流量；`IP_LIMIT` 是活跃来源 IP 数量。

`id` 可省略，手动填写时只支持英文字母、数字、点、下划线和连字符。

### 按分钟

```bash
MINUTES=30; IP_VERSION=4 IP_LIMIT=3 PQ_GIB=1 D=$((MINUTES*60)) hy2_mktemp.sh
```

### 按小时

```bash
HOURS=2; IP_VERSION=4 IP_LIMIT=3 PQ_GIB=5 D=$((HOURS*60*60)) hy2_mktemp.sh
```

### 按天

```bash
DAYS=7; IP_VERSION=4 IP_LIMIT=3 PQ_GIB=50 D=$((DAYS*24*60*60)) hy2_mktemp.sh
```

### 自定义节点名称

```bash
id=user01 IP_VERSION=4 IP_LIMIT=2 PQ_GIB=10 D=86400 hy2_mktemp.sh
```

### 固定端口

下面示例固定使用 UDP `40000`，有效期 1 小时：

```bash
id=fixed PORT_START=40000 PORT_END=40000 IP_VERSION=4 IP_LIMIT=3 PQ_GIB=1 D=3600 hy2_mktemp.sh
```

### 创建 IPv6 入站节点

先确保 `/etc/default/hy2-main` 中已填写 `HY_IPV6_DOMAIN`，且 AAAA 记录指向当前 VPS。

```bash
id=tmp6 IP_VERSION=6 IP_LIMIT=3 PQ_GIB=1 D=3600 hy2_mktemp.sh
```

常用调整：

```bash
# 不限制活跃来源 IP
IP_LIMIT=0 PQ_GIB=10 D=3600 hy2_mktemp.sh

# 不限制流量
IP_LIMIT=3 D=3600 hy2_mktemp.sh
```

即使 `IP_LIMIT=0`，IPv4/IPv6 地址族隔离仍会保持启用。

## 四、查看与删除节点

### 查看全部节点

```bash
hy2_audit.sh
```

### 综合健康检查

```bash
hy2_doctor.sh --strict
```

也可以执行：

```bash
bash /root/hy2.sh doctor
bash /root/hy2.sh version
```

### 重新生成临时节点订阅

```bash
hy2_temp_sub.sh
```

### 强制删除一个临时节点

先运行 `hy2_audit.sh` 找到完整 TAG，然后执行：

```bash
FORCE=1 hy2_cleanup_one.sh hy2-temp-节点名称
```

普通临时节点与 WG-NAT 临时节点使用同一清理命令。

### 清空全部临时节点

```bash
hy2_clear_all.sh
```

## 五、流量与来源 IP 限制

### 设置端口双向总流量

```bash
pq_add.sh 40000 50
```

表示 UDP `40000` 的上行与下行合计最多 `50 GiB`。

### 查看全部配额

```bash
pq_audit.sh
```

### 删除端口配额

```bash
pq_del.sh 40000
```

### 设置活跃来源 IP 数量

```bash
ip_set.sh 40000 2 300
```

表示最多允许 2 个活跃来源 IP，每个槽位保持 300 秒。

### 删除 IP 数量限制

```bash
ip_del.sh 40000
```

删除 IP 数量限制后，地址族隔离仍然保留。

临时节点到期或被删除时，其配额和 IP 限制也会一起清理。

## 六、部署 WG-NAT

WG-NAT 让 HY2 VPS 上指定的临时节点通过另一台机器的公网 IPv4 出口访问互联网。

### 1. 初始化 NAT 出口机

把 `nat.sh` 放到 **NAT 出口机** 的 `/root/nat.sh`，执行：

```bash
chmod 700 /root/nat.sh
bash /root/nat.sh version
bash /root/nat.sh init
```

无法自动识别公网网卡时：

```bash
WAN_IF=eth0 bash /root/nat.sh init
```

### 2. 配置 HY2 VPS

在 **HY2 VPS** 执行：

```bash
chmod 700 /root/vpswg.sh
bash /root/vpswg.sh
```

脚本会输出 VPS WireGuard 公钥。以后查询：

```bash
cat /etc/wireguard/wg-nat.pub
```

### 3. 在 NAT 机添加 VPS

在 **NAT 出口机** 执行：

```bash
bash /root/nat.sh add hy2 hy2.example.com '这里替换成VPS的WireGuard公钥'
```

三个参数分别是：

- `hy2`：Peer 名称，例如 `vps1`、`hk1`
- `hy2.example.com`：HY2 VPS 的公网 IPv4 域名或 IPv4 地址
- 第三个参数：VPS 的 WireGuard 公钥

NAT 机会自动分配 WG 地址，并打印 VPS 回填命令。

同名再次执行 `add` 会保留原 WG 地址，只更新 Endpoint 或公钥。

### 4. 回到 VPS 执行回填命令

原样执行 NAT 机输出的命令。例如：

```bash
/usr/local/sbin/wg_nat_set_peer.sh 'NAT机WireGuard公钥' '10.66.66.1/32'
```

然后检查出口：

```bash
/usr/local/sbin/wg_nat_healthcheck.sh
```

出现下面结果后再继续：

```text
OK EXIT_IP=x.x.x.x
```

### 5. 安装 WG-NAT 临时节点工具

在 **HY2 VPS** 执行：

```bash
chmod 700 /root/natjichang.sh
bash /root/natjichang.sh
bash /root/natjichang.sh check
```

## 七、创建 WG-NAT 临时节点

### IPv4 入站、NAT IPv4 出口

```bash
id=nat4 IP_VERSION=4 IP_LIMIT=3 PQ_GIB=1 D=1800 hy2_mktemp_nat.sh
```

### IPv6 入站、NAT IPv4 出口

```bash
id=nat6 IP_VERSION=6 IP_LIMIT=3 PQ_GIB=1 D=1800 hy2_mktemp_nat.sh
```

正常使用不要设置 `SKIP_HEALTHCHECK=1`。

WG-NAT 临时节点只有在以下条件全部成立时才允许启动：

- WireGuard 接口正常
- 策略路由正确
- fail-close 路由与 OUTPUT 防泄漏规则存在
- NAT Peer 有有效握手
- 配额、IP 限制和地址族隔离规则完整

保护缺失时，启动检查会拒绝启动，watchdog 检测后会停止/修复相关节点。`bindDevice`、prohibit 和 OUTPUT guard 提供多层出站保护，但不能据此承诺任意特权清空规则时零窗口，也尚未闭环 DNS/masquerade 的出网路径；详见审查报告。

## 八、NAT 机常用命令

```bash
# 查看 Peer
bash /root/nat.sh list

# 查看整体状态
bash /root/nat.sh status

# 更新同名 Peer
bash /root/nat.sh add hy2 hy2.example.com '新的VPS公钥'

# 删除 Peer
bash /root/nat.sh del hy2

# 查看版本
bash /root/nat.sh version
```

## 九、故障保护与恢复

- 主节点和临时节点启动前会检查防护规则
- 配额或 IP 限制丢失时，watchdog 尝试恢复
- 检测到临时节点防护缺失时先停止对应监听再修复；检测前窗口仍存在
- WG-NAT 故障时，策略路由 `prohibit` 和 OUTPUT REJECT 防止出口泄漏
- 临时节点创建采用事务式清理，失败不会保留半套状态
- Hysteria 核心升级失败会恢复旧二进制与原活跃服务
- 证书续期钩子会验证证书有效期及公私钥匹配
- 已到期节点不会在证书续期时被错误重启
- 配置了 `HYSTERIA_BINARY_SHA256` 时，doctor 会检查当前核心哈希

手动触发全部保护恢复：

```bash
/usr/local/sbin/hy2_restore_all.sh
```

手动触发 watchdog：

```bash
systemctl start hy2-managed-watchdog.service
```

## 十、真机验收清单

以下是简表；逐项操作、故障注入边界和发布门槛见 [完整真机验收](docs/VPS_ACCEPTANCE.md)。CI 的真实 WG/nft/iptables 测试在独立 namespace 中进行，不等于公网 VPS、证书和 HY2 客户端端到端测试。

建议先在一台全新 HY2 VPS 和一台全新 NAT VPS 测试：

1. `hy2_doctor.sh --strict` 无错误
2. 主节点能导入 v2rayN 并连接
3. 主节点出口 IP 是 HY2 VPS
4. 普通临时节点能连接并按时间自动删除
5. 达到 `PQ_GIB` 后端口停止转发
6. 超出 `IP_LIMIT` 后新来源 IP 被拒绝
7. IPv6 临时节点可连接，IPv4/IPv6 不串入
8. WG-NAT 临时节点出口 IP 是 NAT 机
9. 停止 `wg-quick@wg-nat` 后，NAT 节点断流且不泄漏 VPS 本机出口
10. 清空 HY2 nftables 表后，watchdog 能恢复或停止节点
11. HY2 VPS 与 NAT 机重启后，Peer、路由、配额和临时节点状态恢复
12. 模拟证书续期后，主节点和未到期临时节点正常重载

## 十一、已知范围

- 只面向全新机器
- 不迁移旧 Hysteria、旧 WireGuard 或旧 nftables 状态
- 主节点固定为 IPv4 UDP `443`
- 不提供一键卸载命令
- 不应与其他会频繁重建相同 nftables、iptables 或策略路由的管理脚本混用
- 在真实生产使用前，必须完成上一节的真机验收

仅在自己拥有或已获明确授权的服务器和网络上使用，并遵守当地法律和服务商条款。
