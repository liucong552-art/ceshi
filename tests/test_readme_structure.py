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
