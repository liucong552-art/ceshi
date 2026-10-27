"""Offline tests for access logging; real Debian/3proxy acceptance is separate."""
import contextlib
import datetime as dt
import importlib.util
import io
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

core = load_module("socks5", "socks5.py")
audit = load_module("socks5_audit", "socks5-audit.py")


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.node = {
            "username": "u123", "password": "passABC", "port": 40007,
            "maxconn": 128, "connections": 64, "id": "s5-demo",
        }
        self.settings = {"wan_if": "eth0"}

    def test_default_audit_enabled(self):
        cfg = core.proxy_config(self.node, self.settings)
        self.assertIn("\nlog\n", cfg)
        self.assertIn("S5AUDIT %E %C %c %n %R %r %I %O", cfg)
        self.assertIn("deny u123 * * 25,465,587 CONNECT", cfg)
        self.assertIn("deny u123 * * * UDPASSOC,BIND", cfg)
        self.assertIn("socks -4 -p40007 -i0.0.0.0 -Deeth0", cfg)
        self.assertNotIn("passABC", cfg.split("logformat")[1])

    def test_old_config_compatibility_for_migration(self):
        cfg = core.proxy_config(self.node, self.settings, access_log=False)
        self.assertNotIn("S5AUDIT", cfg)
        self.assertNotIn("\nlog\n", cfg)
        self.assertTrue(cfg.endswith("socks -4 -p40007 -i0.0.0.0 -Deeth0\n"))

    def test_existing_guard_audit_cli_not_repurposed(self):
        health = core.parser().parse_args(["audit", "40007"])
        self.assertEqual(health.action, "audit")
        a = core.parser().parse_args(["access", "40007", "7"])
        self.assertEqual((a.action, a.port, a.days), ("access", "40007", "7"))


class ParserTests(unittest.TestCase):
    def test_parse_success_and_rejected_records(self):
        end = int(dt.datetime.now(dt.timezone.utc).timestamp())
        good = f"{end} S5AUDIT 000 38.244.61.82 40555 example.com 93.184.215.14 443 99 100"
        parsed = audit.parse_record(good, end-86400, end)
        self.assertEqual(parsed[2:4], ("38.244.61.82", "example.com"))
        self.assertIsNone(audit.parse_record(good, end-172800, end-86400))
        self.assertIsNone(audit.parse_record("hacker S5AUDIT unsafe", end-86400, end))
        self.assertIsNone(audit.parse_record(good.replace(" 99 100", " -1 100"), end-86400, end))
        self.assertIsNone(audit.parse_record(good.replace("38.244.61.82", "127.0.0.1"), end-86400, end))

    def test_domain_and_failures_and_minutes(self):
        end = int(dt.datetime.now(dt.timezone.utc).timestamp())
        minute = end - 40
        lines = [
            f"{minute} S5AUDIT 000 1.1.1.1 30000 EXAMPLE.COM 93.184.215.14 443 15 40",
            f"{minute+1} S5AUDIT 000 2.2.2.2 30001 example.com 93.184.215.14 443 15 40",
            f"{minute+2} S5AUDIT 000 1.1.1.1 30002 8.8.8.8 8.8.8.8 443 15 40",
            f"{minute+3} S5AUDIT 110 3.3.3.3 30003 fail.example 93.184.215.14 443 0 0",
            "not a proxy record",
        ]
        domains, clients, first, last, minutes, events, bad, failed = audit.summarize(lines, end-86400, end)
        self.assertEqual(domains["example.com"], 2)
        self.assertEqual(sum(domains.values()), 2)
        self.assertEqual(clients["1.1.1.1"], 2)
        self.assertNotIn("3.3.3.3", clients)
        self.assertEqual((events,bad,failed), (4,0,1))
        self.assertEqual(first["1.1.1.1"], minute)
        self.assertEqual(last["1.1.1.1"], minute+2)
        self.assertTrue(any(len(ips)>1 for ips in minutes.values()) or
                        dt.datetime.fromtimestamp(minute, audit.BJ).minute !=
                        dt.datetime.fromtimestamp(minute+1, audit.BJ).minute)

    def test_endpoint_parsing(self):
        self.assertEqual(audit.peer_ip("1.2.3.4:5555"), "1.2.3.4")
        self.assertEqual(audit.peer_ip("[2001:db8::a]:443"), "2001:db8::a")
        self.assertIsNone(audit.peer_ip("broken"))

    def test_report_four_sections_and_warning(self):
        end = int(dt.datetime.now(dt.timezone.utc).timestamp())
        line = f"{end} S5AUDIT 000 38.244.61.82 4242 test.example 93.184.215.14 443 20 30"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            audit.display(40007, 7, "socks5-@s5-demo.service",
                          journal_lines=[line], ss_snapshot={"38.244.61.82": 2})
        output = buf.getvalue()
        for heading in ("【1 / 4", "【2 / 4", "【3 / 4", "【4 / 4"):
            self.assertIn(heading, output)
        self.assertIn("test.example", output)
        self.assertIn("38.244.61.82", output)
        self.assertIn("2 条连接", output)
        self.assertNotIn("警告：指定时间段没有", output)

    def test_no_logs_not_confused_with_no_abuse(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            audit.display(40007, 7, "socks5-@s5-demo.service", journal_lines=[], ss_snapshot={})
        self.assertIn("不能据此判断无人使用或没有滥用", buf.getvalue())

    def test_rejects_bad_args(self):
        with self.assertRaises(ValueError):
            audit.display(40007, 0, "socks5-@s5-demo.service", journal_lines=[])
        with self.assertRaises(ValueError):
            audit.display(40007, 7, "../evil.service", journal_lines=[])


class ReadOnlyAuditExtrasTests(unittest.TestCase):
    def setUp(self):
        self.end = int(dt.datetime.now(dt.timezone.utc).timestamp())
        self.start = self.end - 7 * 86400
        self.unit = "socks5-@s5-demo.service"

    def record(self, hostname="news.example.com", *, err=0, ip="38.244.61.82", stamp=None):
        stamp = self.end - 30 if stamp is None else stamp
        return f"{stamp} S5AUDIT {err:05d} {ip} 44001 {hostname} 93.184.215.14 443 12 34"

    def test_old_top20_matches_full_report_identically(self):
        records = []
        for i in range(32):
            records.extend([self.record(f"d{i:02d}.example.com")] * (i + 1))
        records.append(self.record("hidden.example.com", err=10))
        records.append(self.record("8.8.8.8"))
        old = io.StringIO()
        new = io.StringIO()
        with contextlib.redirect_stdout(old):
            audit.display(40007, 7, self.unit, journal_lines=records, ss_snapshot={})
        with contextlib.redirect_stdout(new):
            audit.display_all_domains(40007, 7, self.unit, journal_lines=records)
        self.assertIn("【1 / 4 网站访问排行：最多前 20 名】", old.getvalue())
        old_top = old.getvalue().split("【1 / 4 网站访问排行：最多前 20 名】")[1].split("【2 / 4")[0]
        all_text = new.getvalue()
        self.assertIn("域名总数：32 个；有域名的成功连接：528 次。", all_text)
        for i in range(32):
            self.assertIn(f"d{i:02d}.example.com", all_text)
        self.assertNotIn("hidden.example.com", all_text)
        self.assertNotIn("8.8.8.8", all_text)
        # Every original ranked entry is present with exactly the same count.
        import re
        top_pairs = re.findall(r"\d+\.\s+(d\d\d\.example\.com)\s+(\d+) 次连接", old_top)
        full_pairs = re.findall(r"\d+\.\s+(d\d\d\.example\.com)\s+(\d+) 次连接", all_text)
        self.assertEqual(top_pairs,full_pairs[:20])

    def test_exact_error_code_counts_for_real_test_pattern(self):
        records = (
            [self.record("mail.google.com",err=10) for _ in range(28)] +
            [self.record("cachefly.cachefly.net",err=95)] +
            [self.record("[0.0.0.0]",err=100) for _ in range(2)] +
            [self.record("edition.cnn.com") for _ in range(98)] +
            ["junk S5AUDIT broken"]
        )
        codes, entries, valid, invalid = audit.collect_error_details(
            records,self.start,self.end,limit=10)
        self.assertEqual(codes,{10:28,95:1,100:2})
        self.assertEqual((valid,invalid), (129,1))
        self.assertEqual(len(entries),10)
        self.assertEqual(sum(codes.values()),31)
        full = io.StringIO()
        with contextlib.redirect_stdout(full):
            audit.display_error_details(40007,7,self.unit,journal_lines=records,limit=10)
        result=full.getvalue()
        for fragment in ("错误码=00010 | 28 次", "错误码=00095 | 1 次",
                         "错误码=00100 | 2 次", "显示 10 / 31 条",
                         "成功 98、错误码非零 31、不完整 1",
                         "socks5 access-errors 40007 7 --all"):
            self.assertIn(fragment,result)
        self.assertNotIn("目标主机=edition.cnn.com",result)

    def test_all_errors_are_shown_newest_first(self):
        records=[
            self.record("past.example.com",err=10,stamp=self.end-30),
            self.record("[0.0.0.0]",err=100,stamp=self.end-10),
            self.record("recent.example.com",err=95,stamp=self.end-20),
            self.record("pass.example.com",err=0,stamp=self.end-1)
        ]
        buffer=io.StringIO()
        with contextlib.redirect_stdout(buffer):
            audit.display_error_details(40007,7,self.unit,journal_lines=records,limit=None)
        output=buffer.getvalue()
        self.assertIn("显示 3 / 3 条",output)
        self.assertNotIn("pass.example.com",output)
        self.assertLess(output.index("目标主机=[0.0.0.0]"),output.index("目标主机=recent.example.com"))
        self.assertLess(output.index("目标主机=recent.example.com"),output.index("目标主机=past.example.com"))
        self.assertIn("（非有效域名）",output)
        self.assertIn("目标IP=93.184.215.14 | 目标端口=443",output)
        self.assertIn("来源IP=38.244.61.82",output)

    def test_new_commands_are_only_read_reports(self):
        a=core.parser().parse_args(["access-domains","40002","7"])
        b=core.parser().parse_args(["access-errors","40002","7","--limit","40"])
        c=core.parser().parse_args(["access-errors","40002","7","--all"])
        self.assertEqual((a.action,a.port,a.days),("access-domains","40002","7"))
        self.assertEqual((b.action,b.limit,b.all),("access-errors","40",False))
        self.assertTrue(c.all)

    def test_new_reports_validate_ranges(self):
        with self.assertRaises(ValueError):
            audit.display_all_domains(40007, 0, self.unit, journal_lines=[])
        with self.assertRaises(ValueError):
            audit.display_error_details(40007,7,"../bad.service",journal_lines=[])
        with self.assertRaises(ValueError):
            audit.collect_error_details([],self.start,self.end,limit=0)
        with self.assertRaises(ValueError):
            audit.display_error_details(40007,7,self.unit,journal_lines=[],limit=-1)

    def test_original_proxy_acl_and_logging_stay_unchanged(self):
        node={"username":"u123","password":"p123","port":40007,"connections":64,"maxconn":128}
        config=core.proxy_config(node,{"wan_if":"eth0"})
        self.assertIn("connlim 64 0 u123",config)
        self.assertIn("connlim 120 60 u123",config)
        self.assertIn("deny u123 * * 25,465,587 CONNECT",config)
        self.assertIn("deny u123 * * * UDPASSOC,BIND",config)
        self.assertIn("S5AUDIT",config)
        self.assertNotIn("example.com",config)
        parser_actions=core.parser()._subparsers._group_actions[0].choices
        self.assertFalse(any(name.startswith("block-") for name in parser_actions))



class InstallerReleaseRefTests(unittest.TestCase):
    def test_installer_defaults_main_and_supports_explicit_staging(self):
        installer = (ROOT / "socks5-install.sh").read_text(encoding="utf8")
        self.assertIn('S5_SOURCE_REF="${S5_SOURCE_REF:-main}"', installer)
        self.assertIn("main|feature/hy2-jieshu-parity)", installer)
        self.assertIn('RAW="https://raw.githubusercontent.com/liucong552-art/ceshi/$S5_SOURCE_REF"', installer)

    def test_installer_sha256_pins_match_source_files(self):
        import hashlib
        installer = (ROOT / "socks5-install.sh").read_text(encoding="utf8")
        for variable, filename in (
            ("expected_core", "socks5.py"),
            ("expected_traffic", "socks5-traffic.py"),
            ("expected_audit", "socks5-audit.py")
        ):
            value = hashlib.sha256((ROOT / filename).read_bytes()).hexdigest()
            self.assertIn(f'{variable}="{value}"', installer)



if __name__ == "__main__":
    unittest.main()
