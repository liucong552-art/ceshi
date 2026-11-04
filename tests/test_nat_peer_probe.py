"""NAT peer probe contract: import upstream safely without disrupting WG."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "nat.sh"


class NatProbeContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SCRIPT.read_text(encoding="utf-8")

    def test_nat_script_syntax(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)

    def test_four_readable_commands(self):
        for cmd in ("probe-once", "probe-enable", "probe-disable", "probe-status"):
            with self.subTest(command=cmd):
                self.assertIn(f"    {cmd})", self.source)
                self.assertIn(f"bash $0 {cmd} <name>", self.source)

    def test_worker_script_syntax_and_safety(self):
        matches = re.findall(
            r"cat > \"\$temp\" <<'HY2_PEER_PROBE_WORKER'\n"
            r"(.*?)\nHY2_PEER_PROBE_WORKER",
            self.source, re.S,
        )
        self.assertEqual(len(matches), 1)
        with tempfile.TemporaryDirectory() as tmp:
            worker = Path(tmp) / "worker.sh"
            worker.write_text(matches[0] + "\n", encoding="utf-8")
            subprocess.run(["bash", "-n", str(worker)], check=True)
        for token in ('wg show "$iface" allowed-ips', 'runtime peer mismatch',
                      'ping -n -q -c 1 -W 1 -I "$iface"', 'WG_PROBE_SENT'):
            self.assertIn(token, matches[0])
        for dangerous in ("wg-quick down", "wg-quick up", "wg set ", "iptables -F",
                          "nft flush ruleset"):
            self.assertNotIn(dangerous, matches[0])

    def test_probe_units_are_peer_scoped(self):
        self.assertIn('systemd-escape --template="hy2-wg-${WG_IF}-peer-probe@.timer"', self.source)
        self.assertIn("OnUnitInactiveSec=10s", self.source)
        self.assertIn("ConditionPathExists=/sys/class/net/${WG_IF}", self.source)
        self.assertIn('systemctl enable --now "$unit"', self.source)
        self.assertIn('systemctl disable --now "$unit"', self.source)

    def test_lifecycle_order_preserves_peer_transaction(self):
        add = self.source[self.source.index("cmd_add(){"):self.source.index("cmd_del(){")]
        delete = self.source[self.source.index("cmd_del(){"):self.source.index("cmd_list(){")]
        self.assertLess(add.index("tx_commit"), add.index('hy2_probe_auto_enable "$name"'))
        self.assertLess(delete.index("tx_commit"), delete.index('hy2_probe_auto_disable "$name"'))
        self.assertIn('if ! ( hy2_probe_auto_enable "$name" ); then', add)

    def test_no_vless_probe_name_reintroduced(self):
        self.assertNotIn('vr-wg-${WG_IF}-peer-probe', self.source)
        self.assertNotIn('vr_wg_peer_probe.sh', self.source)


if __name__ == "__main__":
    unittest.main()
