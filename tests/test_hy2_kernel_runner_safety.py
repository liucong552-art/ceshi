"""The real-kernel runner must refuse host-network execution before any mutation."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from hy2_test_support import ROOT, module

target = module(ROOT / 'tests/hy2_kernel_acceptance.py', 'hy2_kernel_safety_target')


class KernelRunnerSafetyTests(unittest.TestCase):
    def test_missing_or_same_namespace_refuses_before_network_commands(self):
        for parent in ('', 'net:[100]'):
            with self.subTest(parent=parent), patch.dict(os.environ, {'HY2_TEST_HOST_NETNS': parent}), \
                 patch.object(target.os, 'geteuid', return_value=0), \
                 patch.object(target.os, 'readlink', return_value='net:[100]'), \
                 patch.object(target, 'command') as run:
                with self.assertRaisesRegex(RuntimeError, 'Refusing'):
                    target.ensure_isolation()
                run.assert_not_called()

    def test_nonempty_namespace_refuses_before_interface_or_nft_changes(self):
        links = types.SimpleNamespace(stdout=json.dumps([{'ifname': 'lo'}, {'ifname': 'eth0'}]))
        with patch.dict(os.environ, {'HY2_TEST_HOST_NETNS': 'net:[100]'}), \
             patch.object(target.os, 'geteuid', return_value=0), \
             patch.object(target.os, 'readlink', return_value='net:[200]'), \
             patch.object(target, 'command', return_value=links) as run:
            with self.assertRaisesRegex(RuntimeError, 'fresh namespace'):
                target.ensure_isolation()
            run.assert_called_once_with(['ip', '-j', 'link', 'show'])

    def test_cli_host_refusal_does_not_create_a_report_or_work_directory(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            work = Path(temp) / 'must-not-be-created'
            try:
                parent = os.readlink('/proc/self/ns/net')
            except OSError:
                parent = 'namespace-unavailable'
            proc = subprocess.run([sys.executable, '-B', str(ROOT / 'tests/hy2_kernel_acceptance.py'),
                                   '--work', str(work), '--binary', str(Path(temp) / 'unused')],
                                  env=dict(os.environ, HY2_TEST_HOST_NETNS=parent),
                                  capture_output=True, text=True, timeout=10)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn('Refusing networking changes', proc.stderr)
            self.assertFalse(work.exists())


if __name__ == '__main__':
    unittest.main()
