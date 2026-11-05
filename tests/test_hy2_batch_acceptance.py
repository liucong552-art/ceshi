"""Regression tests for the one-command HY2 on-VPS acceptance runner.

Run on GitHub Actions without touching any service or nftables state.
"""
import importlib.util
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

MODULE = Path(__file__).resolve().parent / "hy2_batch_acceptance.py"
spec = importlib.util.spec_from_file_location("hy2_batch_acceptance", MODULE)
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


class PureContractTests(unittest.TestCase):
    def test_tag_port_strict_isolation(self):
        self.assertTrue(batch.valid_tag("hy2-temp-batch-0123abcd"))
        self.assertFalse(batch.valid_tag("hy2-temp-guardcheck"))
        self.assertFalse(batch.valid_tag("hy2-temp-batch-0123abcd/../main"))
        self.assertFalse(batch.valid_tag("hy2-temp-batch-0123abce\n"))
        self.assertTrue(batch.valid_port("40000"))
        self.assertFalse(batch.valid_port("443"))
        self.assertFalse(batch.valid_port("0"))
        self.assertFalse(batch.valid_port("100000"))
        self.assertFalse(batch.valid_port("40000;rm -rf /"))

    def test_machine_guard_rejects_production(self):
        with mock.patch.object(batch.os, "geteuid", return_value=0), \
             mock.patch.object(batch.os, "uname", return_value=types.SimpleNamespace(nodename="MEGABOX")):
            with self.assertRaisesRegex(batch.TestFailure, "only helan"):
                batch.check_machine()
        with mock.patch.object(batch.os, "geteuid", return_value=0), \
             mock.patch.object(batch.os, "uname", return_value=types.SimpleNamespace(nodename="helan")):
            batch.check_machine()

    def test_meta_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "node.env"
            p.write_text("TAG=hy2-temp-batch-a1b2c3d4\nPORT='40000'\nLANDING=nat\n")
            obj = batch.meta(p)
            self.assertEqual(obj["PORT"], "40000")
            self.assertEqual(obj["LANDING"], "nat")

    def test_quota_reset_retains_anchor(self):
        before = {"NEXT_RESET_EPOCH": "1796433625",
                  "RESET_INTERVAL_SECONDS": "2592000"}
        after = {**before, "ORIGINAL_LIMIT_BYTES": "42949672",
                 "SAVED_USED_BYTES": "0", "REBUILD_PENDING": "0"}
        self.assertTrue(batch.quota_anchor(before, after, 42949672, 1793842000))
        self.assertFalse(batch.quota_anchor(before, {**after, "NEXT_RESET_EPOCH": "1796433626"},
                                            42949672, 1793842000))
        self.assertFalse(batch.quota_anchor(before, {**after, "SAVED_USED_BYTES": "1"},
                                            42949672, 1793842000))

    def test_status_matching_only_our_port(self):
        status = "PAUSED: 40001, 40002\nBAN 40001: 198.51.100.7/32\n"
        self.assertTrue(batch.status_has_port("PAUSED:", "40001", status))
        self.assertFalse(batch.status_has_port("PAUSED:", "40000", status))

    def test_persistent_failsafe_unit_syntax_contract(self):
        tag = "hy2-temp-batch-aabbccdd"
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(batch, "UNIT_DIR", Path(tmp)), \
                 mock.patch.object(batch, "run_systemctl"), \
                 mock.patch.object(batch, "active", return_value=True):
                batch.setup_safety_timer(tag, Path(tmp))
                name = batch.unit_names(tag)
                timer = (Path(tmp) / (name + ".timer")).read_text()
                service = (Path(tmp) / (name + ".service")).read_text()
                self.assertIn("OnCalendar=", timer)
                self.assertIn("Persistent=true", timer)
                self.assertIn("WantedBy=timers.target", timer)
                self.assertIn(f"--cleanup {tag}", service)
                self.assertNotIn("reboot", service.lower())
                self.assertNotIn("wg-quick", service)

    def test_real_client_addresses_never_used_for_ban(self):
        for network in batch.NETS:
            n = batch.ipaddress.ip_network(network)
            self.assertTrue(n.network_address in batch.ipaddress.ip_network("198.51.100.0/24")
                            or n.network_address in batch.ipaddress.ip_network("203.0.113.0/24"))

    def test_cleanup_fails_closed_on_owner_mismatch(self):
        tag = "hy2-temp-batch-0123abcd"
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            node = state / "temp" / (tag + ".env")
            quota = state / "quota" / "40000.env"
            node.parent.mkdir(parents=True)
            quota.parent.mkdir(parents=True)
            node.write_text(f"TAG={tag}\nPORT=40000\nLANDING=nat\n")
            quota.write_text("OWNER_KIND=temp\nOWNER_TAG=hy2-temp-unrelated\n")
            with mock.patch.object(batch, "STATE", state), \
                 mock.patch.object(batch, "check_machine"), \
                 mock.patch.object(batch, "command") as run:
                with self.assertRaisesRegex(batch.TestFailure, "quota ownership mismatch"):
                    batch.cleanup_tag(tag)
                run.assert_not_called()


    def test_maintenance_portbw_skips_when_absent(self):
        with mock.patch.object(batch.shutil, "which", return_value=None):
            why = batch.test_portbw_idle_auto("hy2-temp-batch-a1b2c3d4", "40000", Path("/tmp"))
            self.assertIn("not installed", why)

    def test_cleanup_portbw_marker_rejects_other_node_reusing_port(self):
        tag = "hy2-temp-batch-0123abcd"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "temp").mkdir()
            (root / "temp" / "hy2-temp-other.env").write_text(
                "TAG=hy2-temp-other\nPORT=40000\nLANDING=nat\n")
            workdir = root / "report"
            workdir.mkdir()
            (workdir / "portbw-owned.json").write_text(
                '{"tag":"hy2-temp-batch-0123abcd","port":40000}')
            with mock.patch.object(batch, "STATE", root):
                with self.assertRaisesRegex(batch.TestFailure, "port reused"):
                    batch.cleanup_portbw_marker(tag, workdir)

    def test_maintenance_expiry_waits_for_real_state_removal(self):
        tag = "hy2-temp-batch-0123abcd"
        with mock.patch.object(batch, "meta_for_test", side_effect=[
                {"PORT": "40000"}, None]), \
             mock.patch.object(batch, "active", return_value=False), \
             mock.patch.object(batch.time, "sleep"), \
             mock.patch.object(batch.Path, "exists", return_value=False):
            batch.await_natural_expiry(tag, "40000", timeout_seconds=10)

    def test_maintenance_does_not_fabricate_auto_trigger(self):
        with mock.patch.object(batch, "command") as c, \
             mock.patch.object(batch, "active", return_value=False), \
             mock.patch.object(batch.shutil, "which", return_value="/usr/local/sbin/portbw"), \
             mock.patch.object(batch.Path, "is_file", return_value=False):
            reason = batch.test_portbw_idle_auto("hy2-temp-batch-0123abcd", "40000",
                                                   Path("/tmp"))
            self.assertIn("managed config", reason)
            c.assert_not_called()


    def test_fixed_port_allocator_is_restricted_to_our_candidate(self):
        tag = "hy2-temp-batch-a1b2c3d4"
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(batch, "command") as cmd, \
                 mock.patch.object(batch, "meta_for_test",
                                   return_value={"TAG": tag, "PORT": "40000", "LANDING": "nat"}), \
                 mock.patch.object(batch, "active", return_value=True):
                self.assertEqual(batch.create_test_node(tag, Path(tmp),
                                 duration=1200, fixed_port="40000"), "40000")
                env = cmd.call_args.kwargs["env"]
                self.assertEqual(env["PORT_START"], "40000")
                self.assertEqual(env["PORT_END"], "40000")
            with self.assertRaisesRegex(batch.TestFailure, "unsafe test port"):
                batch.create_test_node(tag, Path(tmp), fixed_port="443")

    def test_freed_port_rejects_leftover_nft_objects(self):
        tag = "hy2-temp-batch-a1b2c3d4"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(batch, "STATE", root), \
                 mock.patch.object(batch, "meta_for_test", return_value=None), \
                 mock.patch.object(batch, "active", return_value=False), \
                 mock.patch.object(batch, "command",
                                   return_value=types.SimpleNamespace(returncode=0)):
                with self.assertRaisesRegex(batch.TestFailure, "orphaned nft quota"):
                    batch.verify_freed_port(tag, "40000")

    def test_freed_port_accepts_clean_nft_and_metadata(self):
        tag = "hy2-temp-batch-a1b2c3d4"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(batch, "STATE", root), \
                 mock.patch.object(batch, "meta_for_test", return_value=None), \
                 mock.patch.object(batch, "active", return_value=False), \
                 mock.patch.object(batch, "command",
                                   return_value=types.SimpleNamespace(returncode=1)), \
                 mock.patch.object(batch, "verify_guard_absent") as verify:
                batch.verify_freed_port(tag, "40000")
                verify.assert_called_once_with("40000")

    def test_concurrent_nodes_must_use_distinct_ports(self):
        with self.assertRaisesRegex(batch.TestFailure, "same UDP port"):
            batch.test_two_node_isolation("hy2-temp-batch-a1b2c3d4", "40000",
                                         "hy2-temp-batch-b1c2d3e4", "40000",
                                         Path("/tmp"))


    def test_short_lived_quota_reset_must_not_create_30_day_anchor(self):
        old = {"RESET_INTERVAL_SECONDS": "0", "NEXT_RESET_EPOCH": "0"}
        good = {**old, "ORIGINAL_LIMIT_BYTES": "42949672",
                "SAVED_USED_BYTES": "0", "REBUILD_PENDING": "0"}
        self.assertTrue(batch.short_quota_state(old, good, 42949672))
        self.assertFalse(batch.short_quota_state(
            old, {**good, "RESET_INTERVAL_SECONDS": "2592000"}, 42949672))
        self.assertFalse(batch.short_quota_state(
            old, {**good, "NEXT_RESET_EPOCH": "1796433625"}, 42949672))
        self.assertFalse(batch.short_quota_state(
            old, {**good, "SAVED_USED_BYTES": "123"}, 42949672))

    def test_keepcycle_accepts_both_short_and_31_day_test_nodes(self):
        """Mock only the real CLI update; exercise actual metadata assertions."""
        for duration in (1200, 2678400):
            with self.subTest(duration=duration), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                tag = "hy2-temp-batch-0123abcd"
                port = "40000"
                (root / "temp").mkdir()
                (root / "quota").mkdir()
                logs = root / "logs"
                logs.mkdir()
                tm = root / "temp" / (tag + ".env")
                q = root / "quota" / (port + ".env")
                next_reset = str(int(batch.time.time()) + 2592000) if duration > 2592000 else "0"
                interval = "2592000" if duration > 2592000 else "0"
                base = (f"PORT={port}\nOWNER_KIND=temp\nOWNER_TAG={tag}\n"
                        f"ORIGINAL_LIMIT_BYTES=21474836\nRESET_INTERVAL_SECONDS={interval}\n"
                        f"NEXT_RESET_EPOCH={next_reset}\nSAVED_USED_BYTES=2500\nREBUILD_PENDING=0\n")
                q.write_text(base)
                tm.write_text(f"TAG={tag}\nPORT={port}\nDURATION_SECONDS={duration}\n"
                              "PQ_LIMIT_BYTES=21474836\n")

                def fake_command(argv, **kwargs):
                    if argv[0] == "bash":
                        q.write_text(base.replace("ORIGINAL_LIMIT_BYTES=21474836",
                                                  "ORIGINAL_LIMIT_BYTES=42949672")
                                         .replace("SAVED_USED_BYTES=2500",
                                                  "SAVED_USED_BYTES=0"))
                        tm.write_text(tm.read_text().replace(
                            "PQ_LIMIT_BYTES=21474836", "PQ_LIMIT_BYTES=42949672"))
                        return types.SimpleNamespace(stdout="updated")
                    if argv[0] == "nft":
                        return types.SimpleNamespace(stdout="quota hy2_pq_q_40000 { 42949672 bytes }")
                    return types.SimpleNamespace(stdout="")

                with mock.patch.object(batch, "STATE", root), \
                     mock.patch.object(batch, "command", side_effect=fake_command):
                    batch.test_keepcycle(tag, port, Path("/root/pq_keepcycle.sh"), logs)
                    now = batch.meta(q)
                    self.assertEqual(now["RESET_INTERVAL_SECONDS"], interval)
                    self.assertEqual(now["NEXT_RESET_EPOCH"], next_reset)
                    self.assertEqual(now["ORIGINAL_LIMIT_BYTES"], "42949672")


    def test_watchdog_recovery_refuses_any_other_temporary_node(self):
        tag = "hy2-temp-batch-a1b2c3d4"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "temp").mkdir()
            (root / "temp" / "hy2-temp-user-node.env").write_text(
                "TAG=hy2-temp-user-node\nPORT=40001\nLANDING=nat\n")
            with mock.patch.object(batch, "STATE", root), \
                 mock.patch.object(batch, "command") as command:
                with self.assertRaisesRegex(batch.TestFailure, "another temporary node"):
                    batch.test_watchdog_service_repair(tag, "40000", root)
                command.assert_not_called()

    def test_watchdog_repair_stops_only_disposable_unit(self):
        tag = "hy2-temp-batch-a1b2c3d4"
        port = "40000"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "temp").mkdir()
            node = root / "temp" / (tag + ".env")
            node.write_text(f"TAG={tag}\nPORT={port}\nLANDING=nat\n")
            listener = {"up": True}
            commands = []

            def fake_active(unit):
                if unit == tag + ".service":
                    return listener["up"]
                if unit in ("hy2.service", "wg-quick@wg-nat.service"):
                    return True
                return False

            def fake_cmd(argv, **_kwargs):
                commands.append(list(argv))
                if argv == ["systemctl", "stop", tag + ".service"]:
                    listener["up"] = False
                elif argv == ["/usr/local/sbin/hy2_managed_watchdog.sh"]:
                    listener["up"] = True
                return types.SimpleNamespace(stdout="", returncode=0)

            with mock.patch.object(batch, "STATE", root), \
                 mock.patch.object(batch, "active", side_effect=fake_active), \
                 mock.patch.object(batch, "command", side_effect=fake_cmd), \
                 mock.patch.object(batch, "test_isolated_node"):
                batch.test_watchdog_service_repair(tag, port, root)
            self.assertIn(["systemctl", "stop", tag + ".service"], commands)
            self.assertIn(["/usr/local/sbin/hy2_managed_watchdog.sh"], commands)
            self.assertNotIn(["systemctl", "stop", "hy2.service"], commands)
            self.assertNotIn(["systemctl", "stop", "wg-quick@wg-nat.service"], commands)
            self.assertTrue(listener["up"])

    def test_recovery_must_observe_stopped_state(self):
        tag = "hy2-temp-batch-a1b2c3d4"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "temp").mkdir()
            (root / "temp" / (tag + ".env")).write_text(
                f"TAG={tag}\nPORT=40000\nLANDING=nat\n")
            with mock.patch.object(batch, "STATE", root), \
                 mock.patch.object(batch, "active", return_value=True), \
                 mock.patch.object(batch, "command") as cmd, \
                 mock.patch.object(batch, "test_isolated_node"):
                with self.assertRaisesRegex(batch.TestFailure, "controlled stop was not observed"):
                    batch.test_watchdog_service_repair(tag, "40000", root)
            self.assertEqual(cmd.call_count, 1)
            self.assertEqual(cmd.call_args[0][0],
                             ["systemctl", "stop", tag + ".service"])


if __name__ == "__main__":
    unittest.main()
