"""Non-destructive checks: syntax and release invariants (no root/network)."""
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
FILES = ("hy2.sh", "nat.sh", "vpswg.sh", "natjichang.sh")

class ReleaseTests(unittest.TestCase):
    def test_bash_syntax(self):
        for name in FILES:
            with self.subTest(name=name):
                subprocess.run(["bash", "-n", str(ROOT / name)], check=True, timeout=20)

    def test_version(self):
        for name in FILES:
            with self.subTest(name=name):
                run = subprocess.run(["bash", str(ROOT / name), "version"],
                                     capture_output=True, text=True, check=True, timeout=10)
                self.assertIn("3.3.1", run.stdout)

    def test_generated_bash_syntax(self):
        found = 0
        for name in FILES:
            lines = (ROOT / name).read_text().splitlines()
            i = 0
            while i < len(lines):
                match = re.search(r"<<'([A-Z][A-Z0-9_]*)'", lines[i])
                if not match:
                    i += 1
                    continue
                marker, start = match.group(1), i + 1
                i = start
                while i < len(lines) and lines[i] != marker:
                    i += 1
                self.assertLess(i, len(lines), f"{name}:{start} missing {marker}")
                source = "\n".join(lines[start:i]) + "\n"
                if source.lstrip().startswith("#!/usr/bin/env bash"):
                    found += 1
                    check = subprocess.run(["bash", "-n"], input=source, text=True,
                                           capture_output=True, timeout=20)
                    self.assertEqual(check.returncode, 0, f"{name}:{start} {check.stderr}")
                i += 1
        self.assertGreaterEqual(found, 10, "Not enough embedded Bash scripts found")

    def test_nat_peer_isolation(self):
        code = (ROOT / "nat.sh").read_text()
        for action in ("-A", "-C"):
            for direction in ('-i "$WG_IF"', '-o "$WG_IF"'):
                self.assertIn(f'iptables -w 5 {action} "$WG_FWD_CHAIN" {direction} -j DROP', code)

    def test_failed_cleanup_preserves_metadata(self):
        code = (ROOT / "hy2.sh").read_text()
        self.assertIn('if ! HY2_PQ_LOCK_HELD=0 hy2_pq_delete_managed_port "$PORT"; then', code)
        self.assertIn('if ! HY2_IL_LOCK_HELD=0 hy2_il_delete_managed_port "$PORT"; then', code)

    def test_readme_has_server_install_commands(self):
        doc = (ROOT / "README.md").read_text()
        for name in FILES:
            self.assertIn(f"/ceshi/main/{name}", doc)
            self.assertIn(f"bash -n /root/{name}", doc)
