"""Read-only invariants for jieshu-style HY2 README chapter layout."""
from pathlib import Path
import re
import unittest

DOC = Path(__file__).resolve().parents[1] / "README.md"


def between(text, start, end):
    return text.split(start, 1)[1].split(end, 1)[0]


class ReadmeInstallLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        doc = DOC.read_text(encoding="utf-8")
        cls.chapter = between(doc, '<a id="install"></a>',
                              '<a id="acceptance"></a>')
        cls.env = between(cls.chapter, '## 六、安装与更新',
                          '<a id="hy2-install"></a>')
        cls.hy2 = between(cls.chapter, '<a id="hy2-install"></a>',
                          '<a id="wg-nat"></a>')
        cls.nat = between(cls.chapter, '<a id="wg-nat"></a>',
                          '<a id="daily-install"></a>')

    def test_chapter_summaries_and_environment_match_jieshu(self):
        self.assertIn("<summary>环境要求与各脚本运行位置</summary>",
                      self.env)
        self.assertIn("### 先确认运行位置", self.env)
        groups = re.findall(r"<summary>6\.[1-7] [^<]+</summary>",
                            self.chapter)
        self.assertEqual(len(groups), 7)
        self.assertTrue(groups[0].startswith(
            "<summary>6.1 HY2 主节点：安装 → 配置 → 创建主节点 → 检查"))
        self.assertEqual(self.chapter.count("<details>"),
                         self.chapter.count("</details>"))

    def test_main_has_jieshu_four_step_order_and_copyable_commands(self):
        steps = ["### 1. 安装", "### 2. 修改配置",
                 "### 3. 创建或更新主节点", "### 4. 怎样确认安装成功"]
        self.assertEqual([self.hy2.index(s) for s in steps],
                         sorted(self.hy2.index(s) for s in steps))
        for snippet in (
            "apt-get update && apt-get install -y curl ca-certificates nano",
            "/ceshi/main/hy2.sh",
            "bash -n /root/hy2.sh && bash /root/hy2.sh",
            "nano /etc/default/hy2-main",
            "HY_DOMAIN=hy2.example.com",
            "ACME_EMAIL=admin@example.com",
            "HY_LISTEN=0.0.0.0:443",
            "bash /root/onekey_hy2_main_tls.sh",
            "systemctl is-active hy2.service",
            "ss -lun '( sport = :443 )'",
            "hy2_doctor.sh --strict",
            "cat /root/hy2_main_url.txt",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, self.hy2)
        self.assertNotIn("### 五个项目文件", self.hy2)
        self.assertNotIn("<owner>", self.hy2)
        self.assertNotIn("上传到自己的 GitHub 仓库", self.hy2)
        self.assertIn("链接包含密码", self.hy2)

    def test_wg_nat_same_five_host_scoped_steps(self):
        steps = [
            "### 1. 初始化 NAT 出口机",
            "### 2. 配置 HY2 VPS",
            "### 3. 在已有 jieshu NAT 机添加新 VPS",
            "### 4. 回到 HY2 VPS 执行回填命令",
            "### 5. 安装 WG-NAT 临时节点工具",
        ]
        self.assertEqual([self.nat.index(s) for s in steps],
                         sorted(self.nat.index(s) for s in steps))
        for name in ("nat.sh", "vpswg.sh", "natjichang.sh"):
            self.assertIn("bash -n /root/" + name, self.nat)
        for snippet in (
            "已经安装并通过 §jieshu/nat.sh§ 验收",
            "不覆盖原 §/root/nat.sh§",
            "共享 WG 维护窗口",
            "cat /etc/wireguard/wg-nat.pub",
            "/usr/local/sbin/wg_nat_healthcheck.sh",
        ):
            self.assertIn(snippet.replace("§", chr(96)), self.nat)

    def test_install_and_updates_are_directly_in_readme(self):
        daily = between(self.chapter, '<a id="daily-install"></a>',
                        '<a id="portbw-install"></a>')
        maintenance = between(self.chapter, '<a id="maintenance"></a>',
                              '<a id="abuse-install"></a>')
        self.assertIn("### 安装 / 更新", daily)
        self.assertIn("### 检查统计与历史", daily)
        self.assertIn("python3 /root/hy2_traffic.py --install", daily)
        for heading in (
            "#### HY2 VPS：更新管理工具",
            "#### HY2 VPS：更新每日流量统计",
            "#### HY2 VPS：更新 WireGuard / NAT 临时节点工具",
            "#### NAT 出口机：沿用已验收的 jieshu",
            "#### 通用 portbw：单独的受管理更新流程",
        ):
            self.assertIn(heading, maintenance)
        self.assertNotIn("ceshi/main/nat.sh", maintenance)
        self.assertIn("不需要升级或重装来支持 HY2", maintenance)

    def test_hy2_protocol_stays_specific_not_vless(self):
        self.assertIn("UDP/443", self.hy2)
        self.assertIn("Hysteria", self.hy2)
        self.assertNotIn("onekey_reality_ipv4", self.hy2)
        self.assertNotIn("\nss -ltn", self.hy2)


if __name__ == "__main__":
    unittest.main()
