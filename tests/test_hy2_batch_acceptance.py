"""Regression tests for the one-command HY2 on-VPS acceptance runner.

Run on GitHub Actions without touching any service or nftables state.
"""
import importlib.util
from pathlib import Path
import tempfile
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
             mock.patch.object(batch.os, "uname", return_value=("MEGABOX",)):
            with self.assertRaisesRegex(batch.TestFailure, "only helan"):
                batch.check_machine()
        with mock.patch.object(batch.os, "geteuid", return_value=0), \
             mock.patch.object(batch.os, "uname", return_value=("helan",)):
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


if __name__ == "__main__":
    unittest.main()
