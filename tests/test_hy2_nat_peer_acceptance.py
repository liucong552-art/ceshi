"""Pure/mocked regression for role-safe NAT existing-peer probe batch."""
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


class NatProbeContracts(unittest.TestCase):
    def test_never_run_on_hy2_test_or_vless_production(self):
        for name in ("helan", "MEGABOX"):
            with self.subTest(name=name), \
                 mock.patch.object(nat.os, "geteuid", return_value=0), \
                 mock.patch.object(nat.os, "uname",
                                   return_value=types.SimpleNamespace(nodename=name)):
                with self.assertRaisesRegex(RuntimeError, "ONLY on the separate"):
                    nat.check_host()

    def test_no_other_peers_are_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "alpha.peer").write_text("PublicKey = x\n")
            (root / "beta.peer").write_text("PublicKey = y\n")
            with mock.patch.object(nat, "PEERS", root):
                self.assertEqual(nat.discover_peers(), ["alpha", "beta"])

    def test_prior_enabled_timer_is_preserved(self):
        with mock.patch.object(nat, "unit_for", return_value="test.timer"), \
             mock.patch.object(nat, "is_enabled", return_value=True), \
             mock.patch.object(nat, "is_active", return_value=True), \
             mock.patch.object(nat, "wg_snapshot", return_value={"peers": "same"}), \
             mock.patch.object(nat, "command",
                               return_value=types.SimpleNamespace(
                                   stdout="WG_PROBE_SENT: peer=alpha", returncode=0)) as cmd:
            result = nat.test_one_peer("alpha")
            self.assertEqual(result["probe"], "PASS")
            self.assertEqual(result["timer"], "SKIP")
            self.assertEqual(cmd.call_count, 1)
            self.assertNotIn("probe-disable", cmd.call_args[0][0])

    def test_existing_probe_not_verified_is_skip(self):
        with mock.patch.object(nat, "unit_for", return_value="test.timer"), \
             mock.patch.object(nat, "is_enabled", return_value=True), \
             mock.patch.object(nat, "is_active", return_value=True), \
             mock.patch.object(nat, "wg_snapshot", return_value={"peers": "same"}), \
             mock.patch.object(nat, "command",
                               return_value=types.SimpleNamespace(
                                   stdout="WG_PROBE_SKIP: mismatch", returncode=0)):
            result = nat.test_one_peer("alpha")
            self.assertEqual(result["probe"], "SKIP")

    def test_timer_is_undone_even_when_enable_partially_fails(self):
        commands = []
        def command(argv, **kwargs):
            commands.append(argv)
            if "probe-enable" in argv:
                raise RuntimeError("simulated failure")
            return types.SimpleNamespace(stdout="WG_PROBE_SENT: iface=wg-exit", returncode=0)
        with mock.patch.object(nat, "unit_for", return_value="test.timer"), \
             mock.patch.object(nat, "is_enabled", return_value=False), \
             mock.patch.object(nat, "is_active",
                               side_effect=lambda u: u.startswith("wg-quick@")), \
             mock.patch.object(nat, "wg_snapshot", return_value={"peers": "same"}), \
             mock.patch.object(nat, "command", side_effect=command):
            with self.assertRaisesRegex(RuntimeError, "simulated failure"):
                nat.test_one_peer("alpha")
        self.assertTrue(any("probe-disable" in v for v in commands))


if __name__ == "__main__":
    unittest.main()
