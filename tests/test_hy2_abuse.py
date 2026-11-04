"""The HY2 abuse utility must never invent history or disclose auth secrets."""
import importlib.util
from pathlib import Path
import json
import subprocess
import tempfile
import unittest
from unittest import mock

FILE = Path(__file__).resolve().parents[1] / "hy2-abuse.py"
spec = importlib.util.spec_from_file_location("hy2_abuse_readonly", FILE)
ab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ab)


class AbuseTest(unittest.TestCase):
    def test_compiles(self):
        compile(FILE.read_bytes(), str(FILE), "exec")

    def test_requires_managed_hy2_port(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "main").mkdir()
            (root / "temp").mkdir()
            (root / "main/main.env").write_text("MAIN_PORT=443\n", encoding="utf-8")
            (root / "temp/hy2-temp-test.env").write_text(
                "TAG=hy2-temp-test\nPORT=40020\nIP_VERSION=4\n", encoding="utf-8")
            with mock.patch.object(ab, "STATE", root):
                self.assertEqual(ab.owner(443)["unit"], "hy2.service")
                self.assertEqual(ab.owner(40020)["unit"], "hy2-temp-test.service")
                with self.assertRaises(ab.AuditError):
                    ab.owner(22)

    def test_rejects_remote_stats_api_and_redirects(self):
        for u in ("http://example.com:9999", "http://192.0.2.10:9999",
                  "https://127.0.0.1:9999", "http://localhost:9999",
                  "http://127.0.0.1:9999?secret=abc",
                  "http://127.0.0.1", "http://127.0.0.1:9999@evil.example"):
            with self.subTest(url=u):
                with self.assertRaises(ab.AuditError):
                    ab.secure_api_url(u)
        self.assertEqual(ab.secure_api_url("http://127.0.0.1:9999"),
                         "http://127.0.0.1:9999")
        self.assertEqual(ab.secure_api_url("http://[::1]:9999"),
                         "http://[::1]:9999")
        with self.assertRaises(ab.AuditError):
            ab.NoRedirect().redirect_request(None, None, 302, "found",
                                             {}, "http://example.com")

    def test_active_ips_json_and_invalid_data(self):
        data = {"nftables": [{"set": {
            "elem": [{"elem": {"val": "198.51.100.8", "expires": 200}},
                     {"elem": {"val": "not-an-ip"}}]}}]}
        with mock.patch.object(ab.subprocess, "run", return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(data), stderr="")):
            result = ab.active_ips(40020, 4)
        self.assertEqual(result, {"state": "ok", "ips": ["198.51.100.8"]})

    def test_real_online_stats_without_credentials(self):
        def api(_url, _secret, endpoint):
            if endpoint == "/online":
                return {"password-as-auth-id": 2, "other-auth-id": 1}
            return {"streams": [
                {"auth": "password-as-auth-id", "req_addr": "example.org:443",
                 "hooked_req_addr": "", "state": "estab"},
                {"auth": "other-auth-id", "req_addr": "192.0.2.3:80",
                 "hooked_req_addr": "site.example:80", "state": "estab"},
            ]}
        with mock.patch.object(ab, "api_json", side_effect=api):
            out = ab.stats("http://127.0.0.1:9999", "secret")
        self.assertEqual(out["online_client_instances"], 3)
        self.assertEqual(out["active_tcp_streams"], 2)
        self.assertEqual(out["target_counts"], {
            "example.org:443": 1, "site.example:80": 1})
        self.assertNotIn("password-as-auth-id", repr(out))

    def test_journal_categorizes_without_dumping_sensitive_messages(self):
        logs = "\n".join([
            json.dumps({"MESSAGE": "authentication failed password=DO_NOT_EXPOSE",
                        "PRIORITY": "3"}),
            json.dumps({"MESSAGE": "{\"level\":\"warn\",\"msg\":\"timeout\"}",
                        "PRIORITY": "6"}),
            json.dumps({"MESSAGE": "normal debug", "PRIORITY": "6"}),
        ])
        with mock.patch.object(ab.subprocess, "run", return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout=logs, stderr="")):
            result = ab.journal_errors("hy2.service", 7)
        self.assertEqual(result["warning_error_events"], 2)
        self.assertEqual(result["categories"]["auth"], 1)
        self.assertEqual(result["categories"]["timeout"], 1)
        self.assertNotIn("DO_NOT_EXPOSE", repr(result))

    def test_no_automatic_blocking_commands(self):
        body = FILE.read_text(encoding="utf-8")
        for word in ("nft flush ruleset", "iptables -F", "hy2-guard ban",
                     "sudo nft", "wg-quick down"):
            self.assertNotIn(word, body)


if __name__ == "__main__":
    unittest.main()
