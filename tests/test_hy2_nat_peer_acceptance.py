"""Static/mocked safeguards for shared jieshu/HY2 NAT compatibility audit."""
import importlib.util
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

FILE = Path(__file__).resolve().parent / "hy2_nat_peer_acceptance.py"
spec = importlib.util.spec_from_file_location("hy2_nat_peer_acceptance", FILE)
nat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nat)


class SharedNatAuditTests(unittest.TestCase):
    def test_never_run_on_hy2_test_or_vless_production(self):
        for name in ("helan", "MEGABOX"):
            with self.subTest(name=name), \
                 mock.patch.object(nat.os, "geteuid", return_value=0), \
                 mock.patch.object(nat.os, "uname",
                                   return_value=types.SimpleNamespace(nodename=name)):
                with self.assertRaisesRegex(RuntimeError, "ONLY on the separate"):
                    nat.check_host()

    def test_auto_detects_jieshu_without_hy2_namespace_guess(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nat.sh"
            path.write_text("vr_probe_unit() {\n  :\n}\nvr_wg_peer_probe.sh\n")
            with mock.patch.object(nat, "NAT_SH", path):
                self.assertEqual(nat.installed_kind(), "jieshu")
            path.write_text("hy2_probe_unit() {\n :\n}\nhy2_wg_peer_probe.sh\n")
            with mock.patch.object(nat, "NAT_SH", path):
                self.assertEqual(nat.installed_kind(), "hy2")
            path.write_text("unknown custom nat code\n")
            with mock.patch.object(nat, "NAT_SH", path):
                with self.assertRaisesRegex(RuntimeError, "unable to identify"):
                    nat.installed_kind()

    def test_readonly_check_never_runs_nat_manager_or_probe_worker(self):
        with mock.patch.object(nat, "read_peer", return_value=("publickey", "10.66.66.1/32")), \
             mock.patch.object(nat, "runtime_allowed_ips",
                               return_value={"publickey": ["10.66.66.1/32"]}), \
             mock.patch.object(nat, "unit_for",
                               return_value="vr-wg-wg-exit-peer-probe@alpha.timer"), \
             mock.patch.object(nat, "is_active", return_value=True), \
             mock.patch.object(nat, "is_enabled", return_value=True), \
             mock.patch.object(nat, "wg_snapshot",
                               return_value=("peers", "allowed-ips")), \
             mock.patch.object(nat, "command") as command:
            result = nat.check_one("alpha", "jieshu", False)
            self.assertEqual(result["configuration"], "PASS")
            self.assertEqual(result["probe"], "NOT_RUN")
            command.assert_not_called()

    def test_readonly_check_fails_closed_on_different_peer_binding(self):
        with mock.patch.object(nat, "read_peer", return_value=("pub1", "10.66.66.1/32")), \
             mock.patch.object(nat, "runtime_allowed_ips",
                               return_value={"pub1": ["10.66.66.3/32"]}), \
             mock.patch.object(nat, "unit_for") as unit:
            with self.assertRaisesRegex(RuntimeError, "mismatch"):
                nat.check_one("alpha", "jieshu", False)
            unit.assert_not_called()

    def test_optin_probe_calls_existing_jieshu_worker_not_nat_sh(self):
        commands = []
        with tempfile.TemporaryDirectory() as tmp:
            helper = Path(tmp) / "vr_wg_peer_probe.sh"
            helper.touch()
            real_path = nat.Path
            def patch_path(value):
                if str(value) == "/usr/local/sbin/vr_wg_peer_probe.sh":
                    return helper
                return real_path(value)
            def fake_command(argv, **kwargs):
                commands.append(list(argv))
                return types.SimpleNamespace(stdout="WG_PROBE_SENT: iface=wg-exit",
                                             returncode=0)
            with mock.patch.object(nat, "read_peer", return_value=("pub1", "10.66.66.1/32")), \
                 mock.patch.object(nat, "runtime_allowed_ips",
                                   return_value={"pub1": ["10.66.66.1/32"]}), \
                 mock.patch.object(nat, "unit_for", return_value="vr-wg-wg-exit-peer-probe@alpha.timer"), \
                 mock.patch.object(nat, "is_active", return_value=True), \
                 mock.patch.object(nat, "is_enabled", return_value=True), \
                 mock.patch.object(nat, "wg_snapshot",
                                   return_value=("peers", "allowed-ips")), \
                 mock.patch.object(nat, "Path", side_effect=patch_path), \
                 mock.patch.object(nat, "command", side_effect=fake_command):
                result = nat.check_one("alpha", "jieshu", True)
            self.assertEqual(result["probe"], "SENT_NOT_HANDSHAKE_PROOF")
            self.assertEqual(commands, [[str(helper), "wg-exit", "alpha"]])
            self.assertTrue(all("probe-enable" not in row and "probe-disable" not in row
                                for row in commands))

    def test_discovery_does_not_delete_other_peers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "alpha.peer").write_text("PublicKey = x\n")
            (root / "beta.peer").write_text("PublicKey = y\n")
            with mock.patch.object(nat, "PEERS", root):
                self.assertEqual(nat.discover(), ["alpha", "beta"])


if __name__ == "__main__":
    unittest.main()
