#!/usr/bin/env python3
"""Pure deterministic sustained-window decision regression; no shell/kernel."""
import importlib.util
from pathlib import Path
import unittest

p=Path(__file__).resolve().parents[1]/"portbw.py"
spec=importlib.util.spec_from_file_location("portbw_sustained_window_test",p)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
BASE=1250000
OPTIONS={"trigger_bp":10000,"after":5,"hold":30,"cooldown":0}

def point(t,n):
    return {"bytes":n,"start":float(t),"end":float(t),
            "birth_lo":0.0,"birth_hi":0.0,"generation":[11]}

def exercise(rates):
    s=mod.empty_direction()
    total=0
    mod.observe(s,point(0,total),BASE,OPTIONS)
    hist=[]
    for t,mbps in enumerate(rates,1):
        total+=int(mbps*125000)
        mod.observe(s,point(t,total),BASE,OPTIONS)
        hist.append((s["phase"],s["progress"],s["reason"]))
    return s,hist

class WindowAcceptance(unittest.TestCase):
    def test_helan_alternating_9_and_11_reaches_100_percent_mean(self):
        s,h=exercise([9,11,9,11,9,11,9,11])
        self.assertEqual(s["phase"],"entering",h)

    def test_stable_9_never_reaches_100_percent(self):
        s,h=exercise([9]*12)
        self.assertEqual(s["phase"],"monitor",h)

    def test_short_burst_then_idle_cannot_trigger(self):
        s,h=exercise([1,30,1,1,1,1,1,1])
        self.assertEqual(s["phase"],"monitor",h)
        self.assertEqual(s["progress"],0)

    def test_active_11_sustained_triggers(self):
        s,h=exercise([11]*8)
        self.assertEqual(s["phase"],"entering",h)

    def test_counter_reset_breaks_window(self):
        s=mod.empty_direction()
        mod.observe(s,point(0,0),BASE,OPTIONS)
        mod.observe(s,point(1,1375000),BASE,OPTIONS)
        self.assertIsNotNone(s["monitor_start_bytes"])
        mod.observe(s,point(2,100),BASE,OPTIONS)
        self.assertIsNone(s["monitor_start_bytes"])
        self.assertEqual(s["progress"],0)

    def test_old_record_without_anchor_starts_fresh(self):
        s=mod.empty_direction()
        s.pop("monitor_start_bytes")
        mod.observe(s,point(0,0),BASE,OPTIONS)
        mod.observe(s,point(1,1375000),BASE,OPTIONS)
        self.assertEqual(s["monitor_start_bytes"],0)

if __name__=="__main__":
    unittest.main()
