"""The Windows export remains literal data and refuses non-test hosts."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from hy2_test_support import ROOT, module

target = module(ROOT / 'tests/helan_public_probe.py', 'hy2_public_probe_target')


class PublicProbeTests(unittest.TestCase):
    def meta(self):
        return {'HY_DOMAIN': 'hy2.example.com', 'MAIN_PORT': '443', 'MAIN_PASSWORD': 'secret', 'MANAGER_VERSION': '4.0.0'}

    def test_descriptor_keeps_public_CA_verification_and_supports_obfs(self):
        meta = self.meta()
        result = target.descriptor(meta, 'secret', ['1.1.1.1', '1.1.1.1'])
        self.assertEqual(result['client']['tls'], {'sni': 'hy2.example.com'})
        self.assertNotIn('insecure', result['client'])
        meta.update(ENABLE_SALAMANDER='1', SALAMANDER_PASSWORD='obfs-secret')
        result = target.descriptor(meta, 'secret', ['1.1.1.1'])
        self.assertEqual(result['client']['obfs']['salamander']['password'], 'obfs-secret')

    def test_invalid_or_ambiguous_input_refuses_without_exposing_credentials(self):
        for updates, addresses, password in (({}, ['127.0.0.1'], 'secret'), ({}, ['1.1.1.1', '8.8.8.8'], 'secret'),
                                               ({}, ['1.1.1.1'], 'wrong'), ({'HY_DOMAIN': '$(bad).example'}, ['1.1.1.1'], 'secret')):
            with self.subTest(updates=updates, addresses=addresses):
                with self.assertRaises(RuntimeError) as got:
                    target.descriptor(dict(self.meta(), **updates), password, addresses)
                self.assertNotIn(password, str(got.exception))

    def test_metadata_is_literal_and_first_field_wins(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'main.env'
            path.write_text('MAIN_PASSWORD=$(must-not-run)\nMAIN_PASSWORD=second\n')
            self.assertEqual(target.metadata(path)['MAIN_PASSWORD'], '$(must-not-run)')

    def test_wrong_host_refuses_before_state_or_service_access(self):
        with patch.object(target.os, 'geteuid', return_value=0), \
             patch.object(target.platform, 'node', return_value='business-host'), \
             patch.object(target, 'metadata') as read, patch.object(target.subprocess, 'run') as command:
            with self.assertRaisesRegex(RuntimeError, 'restricted to Helan'):
                target.main()
            read.assert_not_called(); command.assert_not_called()


if __name__ == '__main__':
    unittest.main()
