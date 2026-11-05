"""Release gate is read-only and doesn't promote a SKIP to live coverage."""
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

P = Path(__file__).resolve().parent / "hy2_release_gate.py"
spec = importlib.util.spec_from_file_location("hy2_release_gate", P)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ReleaseGateContract(unittest.TestCase):
    def test_machine_guard_rejects_production(self):
        with mock.patch.object(m.os, "geteuid", return_value=0), \
             mock.patch.object(m.os, "uname",
                               return_value=types.SimpleNamespace(nodename="MEGABOX")):
            with self.assertRaisesRegex(RuntimeError, "only test VPS helan"):
                m.check_machine()

    def test_pass_only_locally_proven_and_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp) / "hy2-batch-accept-1234"
            r.mkdir()
            path = r / "report.json"
            base = {"machine": "helan", "mode": "edges", "overall": "PASS",
                    "cleanup_ok": True, "fallback_timer_retained": False,
                    "results": [{"check": "isolation", "result": "PASS"},
                                {"check": "skipped", "result": "SKIP"}]}
            path.write_text(json.dumps(base))
            data = m.load_reports(Path(tmp))
            self.assertEqual(data["edges"]["pass_count"], 1)
            self.assertEqual(data["edges"]["skip_count"], 1)
            self.assertIn("skipped", data["edges"]["check_names_skipped"])
            path.write_text(json.dumps({**base, "cleanup_ok": False}))
            self.assertEqual(m.load_reports(Path(tmp)), {})
            path.write_text(json.dumps({**base, "fallback_timer_retained": True}))
            self.assertEqual(m.load_reports(Path(tmp)), {})
            path.write_text(json.dumps({**base, "overall": "FAIL"}))
            self.assertEqual(m.load_reports(Path(tmp)), {})

    def test_gate_keeps_pending_faults_even_when_routine_passed(self):
        reps = {mode: {"result": "PASS"} for mode in m.EXPECTED_MODES}
        with mock.patch.object(m, "check_machine"), \
             mock.patch.object(m, "load_reports", return_value=reps), \
             mock.patch.object(m, "current_services", return_value={"hy2": True}), \
             mock.patch.object(m, "current_readonly_audits", return_value={"guard": True}), \
             mock.patch.object(m, "ipv6_readiness",
                               return_value={"ready": False, "reason": "no IPv6"}), \
             mock.patch.object(m, "other_nodes", return_value=2):
            result = m.gate()
            self.assertEqual(result["routine_gate"], "PASS")
            self.assertEqual(result["outstanding"][0]["status"], "PENDING")
            self.assertEqual(result["outstanding"][1]["status"], "SEPARATE_EVIDENCE_REQUIRED")
            self.assertEqual(result["outstanding"][2]["status"], "PENDING")
            self.assertEqual(result["outstanding"][3]["status"], "PENDING")
            self.assertEqual(result["outstanding"][4]["status"], "SKIP_ENVIRONMENT")

    def test_readiness_is_only_preflight_without_ipv6_domain(self):
        with mock.patch.object(m, "get_env", return_value=""):
            value = m.ipv6_readiness()
            self.assertFalse(value["ready"])

    def test_missing_audit_cannot_be_released(self):
        reps = {mode: {"result": "PASS"} for mode in m.EXPECTED_MODES}
        with mock.patch.object(m, "check_machine"), \
             mock.patch.object(m, "load_reports", return_value=reps), \
             mock.patch.object(m, "current_services", return_value={"hy2": True}), \
             mock.patch.object(m, "current_readonly_audits", return_value={"guard": False}), \
             mock.patch.object(m, "ipv6_readiness",
                               return_value={"ready": True, "reason": "ready"}), \
             mock.patch.object(m, "other_nodes", return_value=0):
            self.assertEqual(m.gate()["routine_gate"], "INCOMPLETE")


    def test_optional_recovery_pass_does_not_erase_grace_or_stress_work(self):
        reps = {mode: {"result": "PASS"} for mode in m.EXPECTED_MODES}
        reps["recovery"] = {"result": "PASS", "skip_count": 0}
        with mock.patch.object(m, "check_machine"), \
             mock.patch.object(m, "load_reports", return_value=reps), \
             mock.patch.object(m, "current_services", return_value={"hy2": True}), \
             mock.patch.object(m, "current_readonly_audits", return_value={"guard": True}), \
             mock.patch.object(m, "ipv6_readiness",
                               return_value={"ready": False, "reason": "no IPv6"}), \
             mock.patch.object(m, "other_nodes", return_value=0):
            status = m.gate()
            self.assertEqual(status["routine_gate"], "PASS")
            self.assertEqual(status["outstanding"][0]["status"], "PASS")
            self.assertEqual(status["outstanding"][1]["status"],
                             "SEPARATE_EVIDENCE_REQUIRED")
            self.assertEqual(status["outstanding"][2]["status"], "PENDING")
            self.assertEqual(status["outstanding"][3]["status"], "PENDING")


if __name__ == "__main__":
    unittest.main()
