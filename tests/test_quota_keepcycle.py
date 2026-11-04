"""Quota reset with preserved 30-day cycle remains an installed, safe helper."""
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "hy2.sh"
HELPER = ROOT / "pq_keepcycle.sh"


class KeepCycleTest(unittest.TestCase):
    def test_bash_syntax(self):
        for path in (INSTALLER, HELPER):
            with self.subTest(path=path.name):
                subprocess.run(["bash", "-n", str(path)], check=True, timeout=25)

    def test_exact_same_generated_script(self):
        source = INSTALLER.read_text(encoding="utf-8")
        matches = re.findall(
            r'cat >"\$\{HY2_SBIN_DIR\}/pq_keepcycle.sh" <<\'EOF\'\n(.*?)\nEOF\n'
            r'\s+chmod 755 "\$\{HY2_SBIN_DIR\}/pq_keepcycle.sh"',
            source, flags=re.S,
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0] + "\n", HELPER.read_text(encoding="utf-8"))
        self.assertIn('/usr/local/sbin/pq_keepcycle.sh', source)
        self.assertIn('    "${HY2_SBIN_DIR}/pq_add.sh" "${HY2_SBIN_DIR}/pq_keepcycle.sh"', source)

    def test_origin_schedule_and_zero_usage(self):
        s = HELPER.read_text(encoding="utf-8")
        for required in (
            'DURATION > 2592000',
            'NEXT=$(( START + ((NOW - START) / INTERVAL + 1) * INTERVAL ))',
            'NEXT=$(( NEXT + ((NOW - NEXT) / INTERVAL + 1) * INTERVAL ))',
            'hy2_pq_write_meta "$PORT" "$BYTES" 0 "$BYTES"',
            'hy2_pq_rebuild_port "$PORT" "$BYTES"',
            'REBUILD_PENDING 0',
        ):
            self.assertIn(required, s)

    def test_lock_owner_backup_and_fail_closed(self):
        s = HELPER.read_text(encoding="utf-8")
        for required in (
            'HY2_LOCK_DIR}/temp.lock', "hy2_pq_lock",
            'OWNER_KIND', 'OWNER_TAG', 'ORIGINAL_LIMIT_BYTES',
            'pq-keepcycle-', 'BACKUP/quota.env',
            'BACKUP/node.env', 'trap', 'EXPIRE == START + DURATION',
        ):
            self.assertIn(required, s)
        self.assertNotIn('nft flush ruleset', s)
        self.assertNotIn('systemctl stop wg', s)
        self.assertNotIn('source "$TEMP_META"', s)

    def test_schedule_arithmetic_expected(self):
        # A long-lived node should keep its original 30d anchor.
        start = 1700000000
        interval = 2592000
        now = start + 75 * 86400
        recovered_next = start + ((now - start) // interval + 1) * interval
        self.assertEqual(recovered_next, start + 90 * 86400)
        # An overdue saved next date must catch up without shifting the anchor.
        old_next = start + 30 * 86400
        advanced = old_next + ((now - old_next) // interval + 1) * interval
        self.assertEqual(advanced, recovered_next)


if __name__ == "__main__":
    unittest.main()
