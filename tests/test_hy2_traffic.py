import datetime as dt
from pathlib import Path
import tempfile
import unittest

from hy2_test_support import ROOT, module

traffic = module(ROOT / 'traffic.py', 'hy2_traffic_test')


class DailyTrafficTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'main').mkdir()
        (self.root / 'temp').mkdir()
        (self.root / 'main/main.env').write_text('MAIN_PORT=8443\nMAIN_PASSWORD=' + 'a' * 32 + '\n')
        self.node = self.root / 'temp/hy2-temp-user.env'
        self.node.write_text('TAG=hy2-temp-user\nPORT=40001\nPASSWORD=' + 'b' * 32 + '\nCREATE_EPOCH=100\nEXPIRE_EPOCH=9999999999\n')
        self.now = dt.datetime(2026, 10, 27, 12, tzinfo=traffic.TZ)

    def test_discovers_actual_hy2_fields_and_udp_rules(self):
        nodes = traffic.discover(self.root, self.now)
        self.assertEqual(nodes['main']['port'], 8443)
        self.assertEqual(nodes['hy2-temp-user']['port'], 40001)
        commands = traffic.reconcile(nodes, {(tag, d): None for tag in nodes for d in ('u', 'd')}, [], {})
        rules = [c for c in commands if c.startswith('add rule')]
        self.assertEqual(len(rules), 4)
        self.assertTrue(all(' udp ' in c for c in rules))
        self.assertTrue(all(' drop ' not in c and 'quota' not in c for c in commands))

    def test_collect_is_idempotent_and_reused_port_is_a_new_identity(self):
        nodes = traffic.discover(self.root, self.now)
        snapshot = [{'table': {'name': traffic.TABLE, 'family': 'inet'}}]
        for chain in ('input', 'output'):
            snapshot.append({'chain': dict(name=chain, type='filter', hook=chain, prio=10, policy='accept')})
        for tag, node in nodes.items():
            for direction, chain, value in (('u', 'input', 100), ('d', 'output', 200)):
                name = 't_{}_{}_{}'.format(node['key'], 'a' * 16, direction)
                snapshot.append({'counter': {'name': name, 'bytes': value}})
                snapshot.append({'rule': {'chain': chain, 'comment': name, 'expr': [
                    {'match': {'op': '==', 'left': {'payload': {'protocol': 'udp', 'field': 'dport' if chain == 'input' else 'sport'}}, 'right': node['port']}},
                    {'counter': name},
                ]}})

        class Backend:
            def snapshot(self):
                return snapshot
            def apply(self, commands):
                self.commands = commands
        backend = Backend()
        first = traffic.collect(self.root, backend, self.now)
        second = traffic.collect(self.root, backend, self.now)
        day = second['users']['hy2-temp-user']['days']['2026-10-27']
        self.assertEqual(day, {'upload': 100, 'download': 200})
        self.assertEqual(first, second)
        self.assertEqual(backend.commands, [])
        self.node.write_text(self.node.read_text().replace('b' * 32, 'c' * 32))
        changed = traffic.collect(self.root, backend, self.now)
        self.assertNotEqual(first['users']['hy2-temp-user']['key'], changed['users']['hy2-temp-user']['key'])
        self.assertEqual(changed['users']['hy2-temp-user']['days']['2026-10-27'], {'upload': 0, 'download': 0})
        self.node.unlink()
        cleaned = traffic.collect(self.root, backend, self.now)
        self.assertNotIn('hy2-temp-user', cleaned['users'])

    def test_30_beijing_days_retention(self):
        node = traffic.discover(self.root, self.now)['main']
        state = {'users': {'main': dict(node, days={
            '2026-09-27': {'upload': 1, 'download': 2},
            '2026-09-28': {'upload': 1, 'download': 2},
            '2026-10-27': {'upload': 3, 'download': 4},
        })}}
        traffic.prune(state, {'main': node}, self.now.date())
        self.assertEqual(set(state['users']['main']['days']), {'2026-09-28', '2026-10-27'})


if __name__ == '__main__':
    unittest.main()
