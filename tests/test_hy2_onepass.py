"""Onepass must reject unsafe archives, wrong hosts and false reboot reports."""
import io
import json
from pathlib import Path
import signal
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from hy2_test_support import ROOT, module

target=module(ROOT/'tests/helan_onepass.py','hy2_onepass_target')
lab=module(ROOT/'tests/hy2_extras_kernel.py','hy2_onepass_lab_target')


class OnepassSafetyTests(unittest.TestCase):
    def request(self): return {'ref':'a'*40,'domain':'hy2.example.com','email':'test@example.com','cpu_seconds':3600}

    def test_untrusted_request_is_rejected(self):
        for changes in ({'ref':'main'},{'domain':'$(touch marker).example'},{'email':'a@b.com\nvalue'},{'cpu_seconds':0}):
            with self.subTest(changes=changes),self.assertRaises(RuntimeError): target.request_guard(dict(self.request(),**changes))

    def test_wrong_host_refuses_before_reading_state(self):
        with patch.object(target.os,'geteuid',return_value=0),patch.object(target.platform,'node',return_value='production'),patch.object(Path,'read_text') as read:
            with self.assertRaisesRegex(RuntimeError,'Dedicated Helan'): target.host_guard()
            read.assert_not_called()

    def test_archive_is_data_only_and_rejects_traversal_links(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for index,(name,kind) in enumerate((('repo/../../escape','file'),('/absolute','file'),('repo/link','symlink'))):
                archive=root/f'{index}.tgz'
                with tarfile.open(archive,'w:gz') as out:
                    member=tarfile.TarInfo(name)
                    if kind=='symlink': member.type=tarfile.SYMTYPE; member.linkname='/etc/passwd'; out.addfile(member)
                    else: member.size=1; out.addfile(member,io.BytesIO(b'x'))
                with self.assertRaises(RuntimeError): target.extract_archive(archive,root/f'src{index}')
            safe=root/'safe.tgz'
            with tarfile.open(safe,'w:gz') as out:
                member=tarfile.TarInfo('repo/tests/example.py');member.size=4;out.addfile(member,io.BytesIO(b'pass'))
            target.extract_archive(safe,root/'good')
            self.assertEqual((root/'good/tests/example.py').read_text(),'pass')
            self.assertEqual((root/'good/tests/example.py').stat().st_mode & 0o777,0o600)

    def test_resuming_does_not_reinstall_or_reboot(self):
        with tempfile.TemporaryDirectory() as temp:
            work=Path(temp); (work/'request.json').write_text(json.dumps(self.request()))
            fake_pointer=MagicMock();fake_pointer.exists.return_value=True
            with patch.object(target,'host_guard'),patch.object(target,'POINTER',fake_pointer),patch.object(target,'current',return_value=work),patch.object(target,'public_status',return_value={'state':'SERVER_PASS'}),patch.object(target,'command') as cmd:
                self.assertEqual(target.start_locked(self.request()),{'state':'SERVER_PASS'})
                cmd.assert_not_called()

    def test_same_boot_refuses_before_recovery_commands(self):
        runner=target.Runner.__new__(target.Runner);runner.req={'initial_boot_id':'old'};runner.phase=MagicMock()
        with patch.object(target,'boot',return_value='old'),self.assertRaisesRegex(RuntimeError,'Refusing to claim'): runner.after()
        runner.phase.assert_not_called()

    def test_wrong_public_client_commit_cannot_complete_the_suite(self):
        with tempfile.TemporaryDirectory() as temp:
            runner=target.Runner.__new__(target.Runner);runner.work=Path(temp);runner.req=self.request()
            target.atomic(runner.work/'status.json',{'state':'SERVER_PASS','cases':[]})
            client={'mode':'public','passed':4,'failed':0,'public_udp_client_tested':True,'runner_ref':'b'*40}
            with self.assertRaisesRegex(RuntimeError,'wrong source'): runner.finish(client)
            self.assertEqual(target.load(runner.work/'status.json')['state'],'SERVER_PASS')
            self.assertFalse((runner.work/'result.json').exists())

    def test_timed_out_phase_stops_only_its_own_process_group(self):
        with tempfile.TemporaryDirectory() as temp:
            runner=target.Runner.__new__(target.Runner);runner.work=Path(temp);runner.src=Path(temp);runner.env={}
            (runner.work/'logs').mkdir();target.atomic(runner.work/'status.json',{'cases':[]})
            proc=MagicMock();proc.pid=22222;proc.wait.side_effect=[subprocess.TimeoutExpired('fixture',1),0]
            with patch.object(target.subprocess,'Popen',return_value=proc),patch.object(target.os,'killpg') as kill:
                with self.assertRaises(subprocess.TimeoutExpired): runner.phase('fixture',['unused'],timeout=1)
                kill.assert_called_once_with(22222,signal.SIGTERM)
            self.assertEqual(target.load(runner.work/'status.json')['cases'][0]['status'],'FAIL')

    def test_positive_counters_without_cases_cannot_complete_acceptance(self):
        with tempfile.TemporaryDirectory() as temp:
            runner=target.Runner.__new__(target.Runner);runner.work=Path(temp);runner.req=self.request()
            target.atomic(runner.work/'status.json',{'state':'SERVER_PASS','cases':[]})
            client={'mode':'public','passed':4,'failed':0,'public_udp_client_tested':True,'runner_ref':'a'*40,'cases':[]}
            with self.assertRaisesRegex(RuntimeError,'Partial client'):runner.finish(client)
            self.assertFalse((runner.work/'result.json').exists())

    def test_optional_packet_lab_refuses_host_network_before_mutation(self):
        with patch.dict(lab.os.environ,{'HY2_TEST_HOST_NETNS':'net:[1]'}),patch.object(lab.os,'geteuid',return_value=0),patch.object(lab.os,'readlink',return_value='net:[1]'),patch.object(lab,'run') as run:
            with self.assertRaisesRegex(RuntimeError,'host-network'):lab.isolated(fresh=True)
            run.assert_not_called()


if __name__=='__main__':unittest.main()
