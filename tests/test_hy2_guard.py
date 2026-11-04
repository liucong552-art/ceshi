"""Unprivileged tests of the HY2-only manual firewall control plane."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / "hy2-guard.py"
spec = importlib.util.spec_from_file_location("hy2_guard_test_module", SOURCE)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class GuardTest(unittest.TestCase):
    def test_file_compiles(self):
        compile(SOURCE.read_bytes(), str(SOURCE), "exec")

    def test_defensive_schema_validation(self):
        good = guard.canonical({
            "schema": 1, "paused": [443, 40020, 443],
            "bans": {"40020": ["192.0.2.0/24", "2001:db8::/32",
                               "192.0.2.0/24"]}
        })
        self.assertEqual(good["paused"], [443, 40020])
        self.assertEqual(good["bans"]["40020"],
                         ["192.0.2.0/24", "2001:db8::/32"])
        for bad in (
            {"schema": 0, "paused": [], "bans": {}},
            {"schema": 1, "paused": ["40020;reboot"], "bans": {}},
            {"schema": 1, "paused": [], "bans": {"0": []}},
            {"schema": 1, "paused": [], "bans": {"40020": ["192.0.2.1/24"]}},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(guard.GuardError):
                    guard.canonical(bad)

    def test_no_global_flush_or_unowned_chain(self):
        st = {"schema": 1, "paused": [40020],
              "bans": {"40020": ["198.51.100.0/24", "2001:db8::/32"]}}
        new = guard.nft_commands(st, False)
        old = guard.nft_commands(st, True)
        self.assertIn("add table inet hy2_manual_guard", new)
        self.assertIn("hook input priority -20", new)
        self.assertIn("hook prerouting priority -150", new)
        self.assertIn("iifname != \"lo\" meta l4proto { tcp, udp } th dport 40020", new)
        self.assertIn("ip saddr 198.51.100.0/24", new)
        self.assertIn("ip6 saddr 2001:db8::/32", new)
        self.assertIn("flush chain inet hy2_manual_guard input", old)
        self.assertIn("flush chain inet hy2_manual_guard pre", old)
        for batch in (new, old):
            self.assertNotIn("flush ruleset", batch)
            self.assertNotIn("hy2_pq", batch)
            self.assertNotIn("hy2_iplimit", batch)
            self.assertNotIn("wg-nat", batch)

    def test_no_accidental_ssh_or_unmanaged_ports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            temp_dir = root / "temp"
            temp_dir.mkdir()
            main = root / "main.env"
            main.write_text("MAIN_PORT=443\n", encoding="utf-8")
            (temp_dir / "hy2-temp-test.env").write_text(
                "TAG=hy2-temp-test\nPORT=40020\n", encoding="utf-8"
            )
            with mock.patch.object(guard, "MAIN_META", main), \
                    mock.patch.object(guard, "TEMP_STATE", temp_dir):
                guard.require_hy2_port(443)
                guard.require_hy2_port(40020)
                for port in (22, 80, 51820, 65535):
                    with self.assertRaises(guard.GuardError):
                        guard.require_hy2_port(port)

    def test_update_rolls_back_on_disk_write_failure(self):
        old = guard.empty_state()
        fresh = {"schema": 1, "paused": [40020], "bans": {}}
        with mock.patch.object(guard, "load", return_value=old), \
             mock.patch.object(guard, "apply") as apply, \
             mock.patch.object(guard, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                guard.update(fresh)
            self.assertEqual(apply.call_count, 2)
            self.assertEqual(apply.call_args_list[0].args[0], fresh)
            self.assertEqual(apply.call_args_list[1].args[0], old)

    def test_atomic_kernel_update_command(self):
        with mock.patch.object(guard, "table_exists", return_value=True), \
             mock.patch.object(guard.shutil, "which", return_value="/usr/sbin/nft"), \
             mock.patch.object(guard, "run") as runner:
            guard.apply(guard.empty_state())
            self.assertEqual(runner.call_args.args[0], ["nft", "-f", "-"])
            batch = runner.call_args.kwargs["input"]
            self.assertIn("flush chain inet hy2_manual_guard", batch)

    def test_audit_detects_lost_rule(self):
        state = {"schema": 1, "paused": [40020], "bans": {}}
        with mock.patch.object(guard, "load", return_value=state), \
             mock.patch.object(guard.subprocess, "run", return_value=subprocess.CompletedProcess(
                 args=[], returncode=0, stdout="table inet hy2_manual_guard {}", stderr="")):
            with self.assertRaises(guard.GuardError):
                guard.audit()


if __name__ == "__main__":
    unittest.main()
