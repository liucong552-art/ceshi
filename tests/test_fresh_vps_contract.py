"""New independent HY2 VPS deployment contracts; all tests are read-only.

The real installation requires a fresh networked VPS, DNS, and ACME.
CI checks the user-facing command chain and Bash safety but never claims
to have provisioned a real server.
"""
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "scripts" / "hy2_fresh_preflight.sh"
GUIDE = ROOT / "docs" / "FRESH_VPS_DEPLOY.md"
README = ROOT / "README.md"


class FreshInstallContract(unittest.TestCase):
    def test_preflight_syntax_and_help_do_not_require_root(self):
        subprocess.run(["bash", "-n", str(PREFLIGHT)], check=True, timeout=15)
        result = subprocess.run(["bash", str(PREFLIGHT), "--help"],
                                check=True, capture_output=True,
                                text=True, timeout=15)
        for mode in ("before", "manager", "main", "wg"):
            self.assertIn(mode, result.stdout)
        self.assertIn("只读", result.stdout)

    def test_preflight_does_not_modify_services_or_nat(self):
        source = PREFLIGHT.read_text(encoding="utf-8")
        for prohibited in (
            "systemctl stop ", "systemctl restart ", "systemctl enable ",
            "systemctl disable ", "nft flush ", "nft add ", "iptables -",
            "wg set ", "wg-quick up ", "wg-quick down ",
            "rm -rf ", "apt-get install ", "nat.sh init", "nat.sh add",
            "portbw install", "hy2_clear_all.sh",
        ):
            with self.subTest(item=prohibited):
                self.assertNotIn(prohibited, source)
        self.assertIn("FRESH_PREFLIGHT_", source)
        self.assertNotIn('hostname -s)" == "helan"', source)
        self.assertIn("wg_nat_healthcheck.sh >/dev/null 2>&1", source)

    def test_new_vps_flow_is_a_fresh_independent_install(self):
        guide = GUIDE.read_text(encoding="utf-8")
        for name in (
            "scripts/hy2_fresh_preflight.sh", "hy2.sh",
            "vpswg.sh", "natjichang.sh",
            "portbw-install.sh", "portbw.py", "traffic.py",
        ):
            with self.subTest(name=name):
                self.assertIn("/ceshi/main/" + name, guide)
        for step in (
            "bash /root/hy2_fresh_preflight.sh before",
            "bash /root/hy2_fresh_preflight.sh manager",
            "bash /root/hy2_fresh_preflight.sh main",
            "bash /root/hy2_fresh_preflight.sh wg",
            "bash /root/onekey_hy2_main_tls.sh",
            "hy2_doctor.sh --strict",
        ):
            self.assertIn(step, guide)
        self.assertIn("不从 helan 迁移任何数据", guide)
        self.assertIn("不需要下载或运行", guide)
        self.assertIn("既有 jieshu NAT", guide)
        self.assertIn("Git Blob SHA 完全相同", guide)

    def test_manager_generates_scripts_expected_by_guide(self):
        code = (ROOT / "hy2.sh").read_text(encoding="utf-8")
        for item in (
            'cat >"/root/onekey_hy2_main_tls.sh"',
            'install_env_template',
            'install_quota_lib',
            'install_iplimit_lib',
            'install_systemd_units',
            'enable_units',
        ):
            self.assertIn(item, code)
        self.assertIn('systemctl start hy2-managed-shutdown-save.service', code)
        self.assertIn('HY_DOMAIN=', code)
        self.assertIn('ACME_EMAIL=', code)
        self.assertIn('FRESH_PREFLIGHT_', PREFLIGHT.read_text(encoding="utf-8"))

    def test_original_jieshu_remains_external_and_unmodified(self):
        guide = GUIDE.read_text(encoding="utf-8")
        self.assertIn("不重新安装 NAT", guide)
        self.assertIn("NAT 出口", guide)
        self.assertIn("原版 jieshu", guide)
        self.assertNotIn("/ceshi/main/nat.sh", guide)
        # HY2's optional regression batches are explicitly test-host-only.
        self.assertIn("不要改掉其主机保护", guide)

    def test_readme_has_immediate_new_vps_entrypoint(self):
        readme = README.read_text(encoding="utf-8")
        self.assertIn("docs/FRESH_VPS_DEPLOY.md", readme)
        self.assertIn("已经安装并通过 `jieshu/nat.sh` 验收", readme)
        self.assertIn("SHARED_COMPONENT_AUDIT_2026-11-05.md", readme)
        # The old accidental literal escaped newline broke the summary table.
        self.assertNotIn("|\\n| 每日流量", readme)

    def test_docs_do_not_claim_fresh_machine_was_live_installed(self):
        guide = GUIDE.read_text(encoding="utf-8")
        self.assertIn("本轮未在新的空白 VPS", guide)
        self.assertIn("基础接入冒烟测试", guide)
        self.assertNotIn("新机真机安装100%通过", guide)


if __name__ == "__main__":
    unittest.main()
