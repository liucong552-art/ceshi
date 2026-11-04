"""Non-root vendor integrity and HY2/WG compatibility checks."""
import hashlib
import importlib.util
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PortbwIntegrationTests(unittest.TestCase):
    def test_upstream_binary_pair_is_unmodified(self):
        source = (ROOT / "portbw.py").read_bytes()
        installer = (ROOT / "portbw-install.sh").read_text(encoding="utf-8")
        expected = re.search(r"^EXPECTED_SHA='([0-9a-f]{64})'$", installer, re.M)
        self.assertIsNotNone(expected)
        self.assertEqual(hashlib.sha256(source).hexdigest(), expected.group(1))
        self.assertEqual(expected.group(1),
                         "66a79fd8ff9f4b00c43a62ef3530251cd6934301bfc5412d497b47d94a3f70d1")
        subprocess.run(["bash", "-n", str(ROOT / "portbw-install.sh")], check=True, timeout=20)
        compile(source, "portbw.py", "exec")

    def test_portbw_is_standalone_and_udp_aware(self):
        source = (ROOT / "portbw.py").read_text(encoding="utf-8")
        self.assertIn("TRANSPORTS=('tcp','udp')", source)
        self.assertIn("TABLE='pbw_policy'", source)
        self.assertIn("hook input priority -5", source)
        self.assertIn("hook output priority -5", source)
        self.assertNotIn("/var/lib/vless-reality/", source)
        self.assertNotIn("/var/lib/hy2/", source)
        self.assertNotIn("nft flush ruleset", source)

    def test_hy2_owns_separate_nft_tables(self):
        source = (ROOT / "hy2.sh").read_text(encoding="utf-8")
        self.assertIn("hy2_pq", source)
        self.assertIn("hy2_iplimit", source)
        self.assertNotIn("pbw_policy", source)
        self.assertIn("bindDevice", source)

    def test_wg_is_already_adapted_from_jieshu(self):
        src = (ROOT / "vpswg.sh").read_text(encoding="utf-8")
        self.assertIn("/var/lib/hy2/temp/*.env", src)
        self.assertIn("/run/hy2-wg/temp.lock", src)
        self.assertIn("HY2_WG_STATE_LOCK_HELD=1", src)
        self.assertNotIn("/run/vless-reality/temp.lock", src)
        self.assertIn('install_direct_failsafe "$OLD_MARK_DEC"', src)
        self.assertIn('remove_direct_failsafe "$OLD_MARK_DEC"', src)


if __name__ == "__main__":
    unittest.main()
