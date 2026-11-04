#!/usr/bin/env python3
"""Differential unit tests for the portbw CPU candidate; no kernel rules modified."""
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest import mock

PATH = Path(__file__).resolve().parents[1] / "portbw.py"
spec = importlib.util.spec_from_file_location("portbw_under_test", PATH)
portbw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(portbw)


def original_tc_ok(port, rec, iface, snap):
    """Unmodified main branch tc_ok, for differential verification."""
    try:
        if 'clsact' not in {e.get('kind') for e in snap['qdiscs']}:
            return False
        for direction in portbw.CHAIN:
            tcdir = 'ingress' if direction == 'up' else 'egress'
            rows = snap['rows'][tcdir]
            targets = []
            for family in portbw.FAMILIES:
                for transport in portbw.TRANSPORTS:
                    pref = portbw.tc_pref(port, family, transport, rec)
                    if pref in portbw._used_tc_prefs(rows, port, direction, family, transport):
                        return False
                    entry = portbw.tc_find(iface, port, direction, family, transport, rec,
                                           rows, strict=rec[direction] > 0)
                    targets.append((family, transport, entry))
            if rec[direction] == 0:
                if (any(entry for _, _, entry in targets)
                        or portbw.tc_action_exists(portbw.tc_police_index(port, direction))):
                    return False
                continue
            if not all(entry for _, _, entry in targets):
                return False
            text = snap['text'][tcdir]
            if not all(portbw.tc_rate_is_ok(entry, rec[direction], iface, direction, port,
                                            family, transport, rec, text)
                       for family, transport, entry in targets):
                return False
        return True
    except portbw.Error:
        return False


def flower(port, rec, direction, family, transport):
    key = 'dst_port' if direction == 'up' else 'src_port'
    return {'pref': portbw.tc_pref(port, family, transport, rec),
            'kind': 'flower', 'protocol': portbw.tc_protocol(family), 'chain': 0,
            'options': {'handle': f'0x{portbw.tc_handle(port):x}',
                        'keys': {'eth_type': 'ipv4' if family == 4 else 'ipv6',
                                 'ip_proto': transport, key: port}}}


def test_snapshot(port=40123):
    rec = {'up': 125000, 'down': 250000,
           'tc_prefs': {'4_tcp': 60001, '4_udp': 60002,
                        '6_tcp': 60003, '6_udp': 60004}}
    snap = {'qdiscs': [{'kind': 'clsact'}],
            'rows': {d: [] for d in ('ingress', 'egress')},
            'text': {'ingress': 'fake', 'egress': 'fake'}}
    for direction, tcdir in (('up', 'ingress'), ('down', 'egress')):
        for family in portbw.FAMILIES:
            for transport in portbw.TRANSPORTS:
                snap['rows'][tcdir].append(flower(port, rec, direction, family, transport))
    return rec, snap


class SnapshotTests(unittest.TestCase):
    def test_healthy_nft_table_is_read_only_once(self):
        first = [{'table': {'family': 'inet', 'name': portbw.TABLE}}]
        with mock.patch.object(portbw, 'nft_snapshot', return_value=first) as read, \
             mock.patch.object(portbw, '_check_owned_table', return_value={'inbound': {}, 'outbound': {}}), \
             mock.patch.object(portbw, 'validate_base') as validate, \
             mock.patch.object(portbw, 'run') as run:
            self.assertIs(portbw.ensure_base(), first)
            read.assert_called_once_with(False)
            validate.assert_not_called()
            run.assert_not_called()

    def test_repair_still_rechecks_nft_after_write(self):
        first, second = ['initial'], ['repaired']
        with mock.patch.object(portbw, 'nft_snapshot', side_effect=[first, second]) as read, \
             mock.patch.object(portbw, '_check_owned_table', return_value={'inbound': {}}), \
             mock.patch.object(portbw, 'validate_base') as validate, \
             mock.patch.object(portbw, 'run') as run:
            self.assertIs(portbw.ensure_base(), second)
            self.assertEqual(read.call_count, 2)
            run.assert_called_once()
            validate.assert_called_once_with(second)

    def test_missing_nft_table_is_created_and_rechecked(self):
        second = ['created']
        with mock.patch.object(portbw, 'nft_snapshot', side_effect=[None, second]) as read, \
             mock.patch.object(portbw, 'validate_base') as validate, \
             mock.patch.object(portbw, 'run') as run:
            self.assertIs(portbw.ensure_base(), second)
            self.assertEqual(read.call_count, 2)
            run.assert_called_once()
            validate.assert_called_once_with(second)

    def test_raw_nft_ok_keeps_global_validation(self):
        rec = {'up': 0, 'down': 0}
        with mock.patch.object(portbw, 'validate_base', side_effect=portbw.Error('foreign')):
            self.assertFalse(portbw.nft_ok(40123, rec, []))
            self.assertTrue(portbw.nft_ok(40123, rec, [], validated=True))

    def test_tc_index_equivalent_to_main_with_adversarial_slots(self):
        rec, snap = test_snapshot()
        variants = []
        variants.append(('healthy', rec, snap))
        modified = copy.deepcopy(snap)
        modified['rows']['ingress'].pop()
        variants.append(('missing_ipv6_udp', rec, modified))
        modified = copy.deepcopy(snap)
        modified['rows']['ingress'].append(copy.deepcopy(modified['rows']['ingress'][0]))
        variants.append(('duplicate', rec, modified))
        modified = copy.deepcopy(snap)
        modified['rows']['egress'][0]['options']['keys']['src_port'] = 9
        variants.append(('foreign_same_pref', rec, modified))
        modified = copy.deepcopy(snap)
        modified['rows']['ingress'].append({'pref': 60002, 'kind': 'flower'})
        variants.append(('missing_options_with_owned', rec, modified))
        modified = copy.deepcopy(snap)
        modified['rows']['ingress'].append({'pref': 111, 'kind': 'flower'})
        variants.append(('unrelated_rule', rec, modified))
        modified = copy.deepcopy(snap)
        modified['qdiscs'] = [{'kind': 'fq'}]
        variants.append(('no_clsact', rec, modified))
        modified = copy.deepcopy(snap)
        modified['rows']['ingress'].reverse()
        variants.append(('reverse_order', rec, modified))
        zero_rec = copy.deepcopy(rec)
        zero_rec['up'] = 0
        modified = copy.deepcopy(snap)
        modified['rows']['ingress'] = []
        variants.append(('zero_up_no_rule', zero_rec, modified))
        variants.append(('zero_up_with_rule', zero_rec, snap))
        with mock.patch.object(portbw, 'tc_rate_is_ok', return_value=True), \
             mock.patch.object(portbw, 'tc_action_exists', return_value=False):
            for title, r, s in variants:
                with self.subTest(case=title):
                    indexed = copy.deepcopy(s)
                    indexed['pref_groups'] = {
                        direction: portbw._tc_pref_groups(rows)
                        for direction, rows in indexed['rows'].items()
                    }
                    self.assertEqual(original_tc_ok(40123, r, 'veth-test', s),
                                     portbw.tc_ok(40123, r, 'veth-test', indexed))
        with mock.patch.object(portbw, 'tc_rate_is_ok', return_value=True), \
             mock.patch.object(portbw, 'tc_action_exists', return_value=True):
            self.assertEqual(original_tc_ok(40123, zero_rec, 'veth-test', modified),
                             portbw.tc_ok(40123, zero_rec, 'veth-test', modified))


    def test_tc_text_index_preserves_strict_block_matching(self):
        # Actual newline-separated tc-like filters. The previous synthetic
        # fixture accidentally used literal backslash-n and could pass None==None.
        port=40123
        handle=format(portbw.tc_handle(port),'x')
        blocks=[]
        for direction in ('up','down'):
            field='dst_port' if direction=='up' else 'src_port'
            for family,proto in ((4,'ip'),(6,'ipv6')):
                for transport in portbw.TRANSPORTS:
                    pref=60001+(family==6)*2+(transport=='udp')
                    blocks.append(
                        f'filter protocol {proto} pref {pref} flower chain 0 handle 0x{handle}\n'
                        f'eth_type ipv{family}\n'
                        f'ip_proto {transport}\n'
                        f'{field} {port}\n'
                        'skip_hw\n'
                        f'action order 1: police 0x{portbw.tc_police_index(port,direction):x} '
                        'rate 1000Kbit burst 64Kb action drop/ok\n')
        decoy=('filter protocol ip pref 60001 flower chain 0 handle 0xabc\n'
               'ip_proto udp\n')
        for variant in (
            '\n'.join(blocks),
            decoy + ''.join(blocks),
            ''.join(reversed(blocks)),
            ''.join(blocks) + decoy,
            ''.join(blocks[:2]) + blocks[0] + ''.join(blocks[2:]),
        ):
            with self.subTest(variant=len(variant)):
                indexed=portbw._tc_text_index(variant)
                matches=0
                for direction in ('up','down'):
                    for family in portbw.FAMILIES:
                        for transport in portbw.TRANSPORTS:
                            pref=60001+(family==6)*2+(transport=='udp')
                            old=portbw._tc_filter_body(
                                variant,port,direction,family,transport,pref)
                            new=portbw._tc_filter_body(
                                indexed,port,direction,family,transport,pref)
                            self.assertEqual(old,new)
                            self.assertIsNotNone(old)
                            matches+=1
                self.assertEqual(matches,8)

    def test_nft_index_matches_strict_port_audit(self):
        port=40123
        base=125000
        proto={'match':{'op':'==','left':{'meta':{'key':'l4proto'}},
                        'right':{'set':['tcp','udp']}}}
        def rule(d,kind,handle):
            field='dport' if d=='up' else 'sport'
            expr=[copy.deepcopy(proto),
                  {'match':{'op':'==','left':{'payload':{
                      'protocol':'th','field':field}},'right':port}}]
            expr.append({'limit':{'name':portbw.limitname(d,port)}} if kind=='drop'
                        else {'counter':{'name':portbw.countname(d,port)}})
            if kind=='drop':expr.append({'drop':None})
            return {'rule':{'family':'inet','table':portbw.TABLE,
                            'chain':portbw.CHAIN[d], 'handle':handle,
                            'comment':portbw.comment(d,port,kind),'expr':expr}}
        items=[
            {'metainfo':{}},
            {'table':{'family':'inet','name':portbw.TABLE}},
            {'chain':{'family':'inet','table':portbw.TABLE,'name':'inbound',
                      'type':'filter','hook':'input','policy':'accept','prio':-5}},
            {'chain':{'family':'inet','table':portbw.TABLE,'name':'outbound',
                      'type':'filter','hook':'output','policy':'accept','prio':-5}},
            {'limit':{'name':portbw.limitname('up',port),'rate':base,
                      'rate_unit':'bytes','burst':portbw.burst_bytes(base),
                      'burst_unit':'bytes','inv':True,'per':'second'}},
            {'counter':{'name':portbw.countname('up',port)}},
            {'counter':{'name':portbw.countname('down',port)}},
            rule('up','drop',11),
            rule('up','count',12),
            rule('down','count',13),
        ]
        rec={'up':base,'down':base}
        variants=[('healthy',items,True)]
        bad=copy.deepcopy(items);bad.pop();variants.append(('missing_count',bad,False))
        bad=copy.deepcopy(items);bad.insert(-1,copy.deepcopy(items[-2]))
        variants.append(('duplicate_drop',bad,False))
        bad=copy.deepcopy(items);bad[-3],bad[-2]=bad[-2],bad[-3]
        variants.append(('wrong_order',bad,False))
        bad=copy.deepcopy(items);bad[4]['limit']['rate']=base+1
        variants.append(('wrong_rate',bad,False))
        bad=copy.deepcopy(items);bad.append({'counter':{'name':portbw.countname('up',port)}})
        variants.append(('duplicate_counter',bad,False))
        for name,case,expected in variants:
            with self.subTest(name=name):
                portbw.validate_base(case)
                index=portbw._nft_watch_index(case)
                self.assertEqual(portbw.nft_ok(port,rec,case),
                                 portbw.nft_ok(port,rec,case,validated=True,index=index))
                self.assertEqual(portbw.nft_ok(port,rec,case),expected)
        foreign=copy.deepcopy(items)
        foreign.append({'rule':{'chain':'inbound','handle':22,'comment':'foreign',
                                'expr':[{'accept':None}]}})
        with self.assertRaises(portbw.Error):
            portbw.validate_base(foreign)

    def test_nft_index_preserves_port_rules_and_generations(self):
        port = 40123
        rules = []
        for i,(direction,kind) in enumerate((('up','drop'),('up','count'),('down','count'))):
            rules.append({'rule':{'chain':portbw.CHAIN[direction], 'handle':10+i,
                                  'comment':portbw.comment(direction,port,kind)}})
        items = [{'metainfo':{}}, *rules,
                 {'limit':{'name':portbw.limitname('up',port)}},
                 {'counter':{'name':portbw.countname('up',port)}},
                 {'counter':{'name':portbw.countname('down',port)}}]
        idx = portbw._nft_watch_index(items)
        self.assertEqual(idx['rules'][port], [r['rule'] for r in rules])
        self.assertEqual(idx['generation'][(port,'up')],[10,11])
        self.assertEqual(idx['generation'][(port,'down')],[12])
        self.assertEqual(set(idx['objects'][port]),set(portbw.owned(items,port)[1]))

class BurstPolicyTests(unittest.TestCase):
    def test_ingress_tcp_20_40_mbps_receive_1m_burst(self):
        self.assertEqual(portbw.tc_burst_kb(20*125000,'up'),1024)
        self.assertEqual(portbw.tc_burst_kb(40*125000,'up'),1024)

    def test_egress_half_second_bounded(self):
        for rate in (625000,1250000,2500000,5000000):
            self.assertEqual(portbw.tc_burst_kb(rate,'down'),
                             max(64,min(1024,(rate+2047)//2048)))
        self.assertEqual(portbw.tc_burst_kb(625000,'down'),306)
        self.assertEqual(portbw.tc_burst_kb(2500000,'down'),1024)
        self.assertEqual(portbw.tc_burst_kb(5000000,'down'),1024)

    def test_ingress_bounded_and_monotonic(self):
        values=[portbw.tc_burst_kb(n,'up') for n in
                (1500,125000,500000,1250000,2500000,5000000,12500000,12500000000)]
        self.assertEqual(values,sorted(values))
        self.assertTrue(all(64<=n<=1024 for n in values))
        self.assertEqual(values[0],64)
        self.assertEqual(values[-1],1024)

    def test_unrecognized_direction_rejected(self):
        with self.assertRaises(portbw.Error):
            portbw.tc_burst_kb(125000,'unknown')


if __name__ == '__main__':
    unittest.main()
