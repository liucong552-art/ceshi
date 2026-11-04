"""HY2 grace must persist across reboot and only clear on verified recovery."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "hy2.sh"


def watchdog_text():
    src = SOURCE.read_text(encoding="utf-8")
    parts = re.findall(
        r'cat >"\$\{HY2_SBIN_DIR\}/hy2_managed_watchdog.sh" <<\'EOF\'\n(.*?)\nEOF',
        src, re.S,
    )
    if len(parts) != 1:
        raise AssertionError("could not isolate generated HY2 watchdog")
    return parts[0] + "\n"


class WatchdogGraceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = watchdog_text()

    def test_embedded_bash_syntax(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "wd.sh"
            file.write_text(self.src, encoding="utf-8")
            subprocess.run(["bash", "-n", str(file)], check=True)

    def test_grace_and_health_gates(self):
        s = self.src
        for required in (
            "WD_GRACE_SECONDS=259200",
            "WATCHDOG_GRACE_START_EPOCH",
            'wd_record_failed_recovery "$meta" "$tag" "guard_unavailable"',
            'wd_record_failed_recovery "$meta" "$tag" "restart_or_healthcheck_failed"',
            'wd_final_boot_ready',
            'wd_node_healthy "$tag" "$port" "$meta" "$landing"',
            'wd_clear_failure_state "$meta"',
            "start < 10#$created",
            "checked_now - hs <= handshake_max",
        ):
            self.assertIn(required, s)
        self.assertNotIn("连续恢复失败", s)
        self.assertNotIn('hy2_meta_upsert "$meta" WATCHDOG_FAILURES 0\n    continue', s)

    def test_three_failures_persist_72h_across_reboots(self):
        s = self.src
        helper = s[s.index("WD_GRACE_SECONDS=259200"):
                   s.index("stop_temp_listener_failclosed()")]
        with tempfile.TemporaryDirectory() as folder:
            meta = Path(folder) / "meta.env"
            created = 1_700_000_000
            start = created + 200
            meta.write_text(
                f"TAG=hy2-temp-test\nCREATE_EPOCH={created}\n"
                "WATCHDOG_FAILURES=0\nWATCHDOG_GRACE_START_EPOCH=0\n",
                encoding="utf-8",
            )
            test_shell = r'''#!/usr/bin/env bash
set -Eeuo pipefail
meta="$1"
now="$2"
rc=0
hy2_meta_get() {
  awk -F= -v k="$2" '$1==k {print substr($0,index($0,"=")+1); exit}' "$1"
}
hy2_meta_upsert() {
  local f="$1" k="$2" v="$3"
  awk -F= -v k="$k" -v v="$v" '
     BEGIN { found=0 }
     $1==k { print k "=" v; found=1; next }
     { print }
     END { if(!found) print k "=" v }
  ' "$f" >"$f.tmp" && mv "$f.tmp" "$f"
}
'''
            test_shell += helper
            test_shell += r'''
wd_final_boot_ready() { return 1; }
for i in 1 2 3; do
  wd_record_failed_recovery "$meta" hy2-temp-test simulated
done
echo "START=$(wd_grace_start "$meta")"
echo "COUNT=$(wd_failure_count "$meta")"
now=$((now + 10000))
wd_record_failed_recovery "$meta" hy2-temp-test simulated
echo "REBOOT_START=$(wd_grace_start "$meta")"
echo "REBOOT_COUNT=$(wd_failure_count "$meta")"
now=$((now + 259200))
wd_record_failed_recovery "$meta" hy2-temp-test simulated
echo "DEFERRED_START=$(wd_grace_start "$meta")"
wd_clear_failure_state "$meta"
echo "RECOVERED_START=$(wd_grace_start "$meta")"
echo "RECOVERED_COUNT=$(wd_failure_count "$meta")"
'''
            script = Path(folder) / "test.sh"
            script.write_text(test_shell, encoding="utf-8")
            run = subprocess.run(
                ["bash", str(script), str(meta), str(start)],
                capture_output=True, text=True, timeout=12)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            expected = {
                "START": str(start), "COUNT": "3",
                "REBOOT_START": str(start), "REBOOT_COUNT": "3",
                "DEFERRED_START": str(start),
                "RECOVERED_START": "0", "RECOVERED_COUNT": "0",
            }
            result = dict(line.split("=", 1) for line in run.stdout.splitlines()
                          if "=" in line)
            for k, v in expected.items():
                with self.subTest(k=k):
                    self.assertEqual(result.get(k), v)


if __name__ == "__main__":
    unittest.main()
