"""Keep README navigation and live-test reporting aligned with the HY2 release."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
EVIDENCE = ROOT / "tests" / "REAL_MACHINE_ACCEPTANCE_2026-11-04.md"


class ReadmeContractTests(unittest.TestCase):
    def test_expected_navigation_order(self):
        doc = README.read_text(encoding="utf-8")
        anchors = [
            "queries", "create", "modify", "troubleshooting",
            "remove", "install", "wg-nat", "acceptance", "feature-gap",
        ]
        for anchor in anchors:
            self.assertIn(f'<a id="{anchor}"></a>', doc)
        ordered = ["queries", "create", "modify", "troubleshooting",
                   "remove", "install", "acceptance"]
        offsets = [doc.index(f'<a id="{a}"></a>') for a in ordered]
        self.assertEqual(offsets, sorted(offsets))

    def test_jieshu_six_chapter_document_contract(self):
        doc = README.read_text(encoding="utf-8")
        headings = re.findall(r"^## (.+)$", doc, re.M)
        self.assertEqual(headings, [
            "一、查询与检查", "二、新建节点", "三、修改与重置",
            "四、故障排查", "五、暂停、封禁与删除", "六、安装与更新",
        ])
        for anchor in (
            "advanced-queries", "node-link", "active-ips", "daily-traffic",
            "speed-logs", "subscription", "temp-create", "quota-ip",
            "quota-keep-cycle", "quota-reset-time", "portbw", "trouble-guide",
            "port-block", "pause-port", "source-blacklist", "blacklist-report",
            "node-manage", "hy2-install", "wg-nat", "daily-install",
            "portbw-install", "maintenance", "backup-recovery", "release-notes",
        ):
            with self.subTest(anchor=anchor):
                self.assertEqual(doc.count(f'<a id="{anchor}"></a>'), 1)

        self.assertEqual(doc.count("<details"), doc.count("</details>"))
        self.assertEqual(doc.count("```") % 2, 0)
        self.assertIn("portbw set 40020 10 10", doc)
        self.assertIn("静态 10/10 Mbps", doc)
        self.assertIn("自动降速", doc)
        self.assertNotIn("vless_mktemp.sh", doc)

    def test_linked_anchors_exist(self):
        doc = README.read_text(encoding="utf-8")
        anchors = set(re.findall(r'<a id="([a-z0-9-]+)"></a>', doc))
        for anchor in re.findall(r'\]\(#([a-z0-9-]+)\)', doc):
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, anchors)

    def test_evidence_exists_and_scopes_claims(self):
        doc = README.read_text(encoding="utf-8")
        self.assertIn("tests/REAL_MACHINE_ACCEPTANCE_2026-11-04.md", doc)
        report = EVIDENCE.read_text(encoding="utf-8")
        for marker in (
            "Debian 13", "2026-11-04", "PR #3",
            "未实测", "WG fail-closed", "portbw",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, report)
        # Public release documentation must not include the VPS/NAT
        # addresses used during operator testing.
        self.assertNotIn("162.211.231.219", report)
        self.assertNotIn("108.241.87.175", report)


if __name__ == "__main__":
    unittest.main()
