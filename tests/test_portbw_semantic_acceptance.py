#!/usr/bin/env python3
"""Pure tests for real-network shared-cap acceptance; never modify kernel."""
import importlib.util
from pathlib import Path
import unittest

PATH=Path(__file__).resolve().parents[1]/"tests"/"portbw_lab_semantics.py"
spec=importlib.util.spec_from_file_location("semantic_checks",PATH)
sem=importlib.util.module_from_spec(spec)
spec.loader.exec_module(sem)

def sample(tcp_off=.712,tcp_rx=.546,udp_off=.45,udp_rx=.45):
    return {"offered_mbps":round(tcp_off+udp_off,3),
            "delivered_mbps":round(tcp_rx+udp_rx,3),
            "flows":[{"proto":"tcp","family":"ipv4","offered_mbps":tcp_off,"delivered_mbps":tcp_rx},
                     {"proto":"udp","family":"ipv4","offered_mbps":udp_off,"delivered_mbps":udp_rx}]}

class AcceptanceTests(unittest.TestCase):
    def test_measured_helan_tcp_udp_is_accepted(self):
        sem.check_shared_cap("tcp-udp-ipv4",sample(),{"drops":100,"overlimits":100})
    def test_tcp_starvation_is_rejected(self):
        with self.assertRaises(RuntimeError):
            sem.check_shared_cap("tcp-udp-ipv4",sample(.04,.009,3,.996),{"drops":100,"overlimits":100})
    def test_shared_cap_bypass_is_rejected(self):
        with self.assertRaises(RuntimeError):
            sem.check_shared_cap("tcp-udp-ipv4",sample(3,1.1,.45,.45),{"drops":100,"overlimits":100})
    def test_udp_underload_is_rejected(self):
        x={"offered_mbps":1.0,"delivered_mbps":.98,"flows":[
           {"proto":"udp","offered_mbps":.5,"delivered_mbps":.49},
           {"proto":"udp","offered_mbps":.5,"delivered_mbps":.49}]}
        with self.assertRaises(RuntimeError):
            sem.check_shared_cap("udp-ipv4-ipv6",x,{"drops":100,"overlimits":100})
    def test_helan_ipv6_tcp_backpressure(self):
        sem.check_shared_cap("tcp-udp-ipv6",sample(.544,.473,.45,.45),{"drops":100,"overlimits":100})
    def test_missing_kernel_pressure_must_fail(self):
        with self.assertRaises(RuntimeError):
            sem.check_shared_cap("tcp-udp-ipv4",sample(),{"drops":0,"overlimits":0})
    def test_dual_stack_udp_under_cap(self):
        x={"offered_mbps":6,"delivered_mbps":.998,"flows":[
           {"proto":"udp","offered_mbps":3,"delivered_mbps":.572},
           {"proto":"udp","offered_mbps":3,"delivered_mbps":.426}]}
        sem.check_shared_cap("udp-ipv4-ipv6",x,{"drops":100,"overlimits":100})

if __name__=="__main__":
    unittest.main()
