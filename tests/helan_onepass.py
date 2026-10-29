#!/usr/bin/env python3
"""Dedicated reinstalled Helan: one non-WG suite, one reboot, resumable status.

Started over the user's existing SSH connection by helan_onepass.ps1. All service
changes run on the explicitly named test VPS. Never accesses an SSH private key.
"""
import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time

POINTER=Path('/root/helan_hy2_onepass.path')
APP_REF='670c6fbe8a942796db59bbd9d5bb92606a012c30'
APP_SHA='774b33a8a54f852ee1ccda8c7908f91f0116fb61be9f06cc9c3b3feef4d83ae1'
CORE_SHA='907ba8c9693edb104b20582681fb7dc15639d5b64a9cbb616a7b539190a86691'
CORE_URL='https://github.com/HyNetworks/hysteria/releases/download/app/v2.13.0/hysteria-linux-amd64'


def require(ok,message):
    if not ok: raise RuntimeError(message)


def atomic(path,value):
    path=Path(path); path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.'+path.name,dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(value,stream,indent=2); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)


def load(path): return json.loads(Path(path).read_text())
def boot(): return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def host_guard():
    require(os.geteuid()==0 and platform.node().split('.')[0]=='helan','Dedicated Helan root required')
    require(platform.machine()=='x86_64','Linux amd64 required')
    release={}
    for line in Path('/etc/os-release').read_text().splitlines():
        k,sep,v=line.partition('=')
        if sep: release[k]=v.strip('"')
    require(release.get('ID')=='debian' and release.get('VERSION_ID')=='13','Reinstalled Debian 13 required')
    require(Path('/proc/1/comm').read_text().strip()=='systemd','Real systemd PID 1 required')


def request_guard(req):
    require(re.fullmatch('[0-9a-f]{40}',req.get('ref','')),'Pinned runner commit required')
    domain=req.get('domain','')
    require(len(domain)<=253 and '.' in domain and all(re.fullmatch('[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',x) for x in domain.split('.')),'Invalid test domain')
    require(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',req.get('email','')),'Invalid certificate email')
    require(type(req.get('cpu_seconds')) is int and 60<=req['cpu_seconds']<=7200,'CPU sample must be 60..7200 seconds')


def extract_archive(archive,dest):
    """No links, devices, traversal or absolute paths, including PAX overrides."""
    dest=Path(dest); dest.mkdir(mode=0o700)
    with tarfile.open(archive,'r:gz') as bundle:
        members=bundle.getmembers(); prefix=None
        require(bool(members),'Empty source archive')
        for item in members:
            parts=PurePosixPath(item.name).parts
            require(not item.name.startswith('/') and '..' not in parts and parts,'Unsafe source path')
            require(item.isdir() or item.isfile(),'Source archive contains a link or special file')
            prefix=prefix or parts[0]; require(parts[0]==prefix,'Unexpected source archive root')
            require(item.size<=8*1024*1024,'Unexpected source member size')
        for item in members:
            relative=PurePosixPath(item.name).parts[1:]
            if not relative: continue
            target=dest.joinpath(*relative)
            if item.isdir(): target.mkdir(mode=0o700,parents=True,exist_ok=True)
            else:
                target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                with bundle.extractfile(item) as src,target.open('wb') as out: shutil.copyfileobj(src,out)
                target.chmod(0o600)


def command(args,timeout=120):
    return subprocess.run(list(map(str,args)),check=True,text=True,capture_output=True,timeout=timeout)


def current():
    path=Path(POINTER.read_text().strip())
    require(path.parent==Path('/root') and re.fullmatch(r'hy2-onepass\.[A-Za-z0-9_]+',path.name) and not path.is_symlink(),'Invalid onepass directory')
    return path


def public_status(work):
    status=load(work/'status.json')
    return {**status,'report_directory':str(work),'wg_deferred':True}


def frame(value):
    print('HY2_ONEPASS_BEGIN\n'+json.dumps(value)+'\nHY2_ONEPASS_END',flush=True)


def start(req):
    host_guard()
    with open('/run/helan-hy2-onepass-start.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        return start_locked(req)


def start_locked(req):
    host_guard(); request_guard(req)
    if POINTER.exists():
        work=current(); previous=load(work/'request.json')
        require(all(previous[k]==req[k] for k in ('ref','domain','email','cpu_seconds')),'Another onepass request already owns this test host')
        return public_status(work)
    for path in ('/usr/local/lib/hy2','/var/lib/hy2','/etc/default/hy2-main','/root/.acme.sh','/root/helan_hy2_round3.path',
                 '/usr/local/bin/hysteria','/usr/bin/hysteria','/usr/local/lib/socks5-manager','/usr/local/lib/portbw',
                 '/usr/local/sbin/pq_add.sh','/etc/default/vless-reality','/var/lib/vless-reality','/var/lib/socks5-manager',
                 '/etc/portbw','/etc/hy2-ipban.nft','/etc/wireguard/wg-nat.conf'):
        require(not os.path.lexists(path),'Refusing an existing deployment; fresh test OS required')
    require(shutil.which('3proxy') is None,'Refusing an existing unmanaged SOCKS core')
    work=Path(tempfile.mkdtemp(prefix='hy2-onepass.',dir='/root'))
    req['initial_boot_id']=boot(); atomic(work/'request.json',req)
    atomic(work/'status.json',{'state':'STARTING','phase':'source','cases':[],'started_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'message':'Preparing pinned source'})
    try:
        command(['curl','-fLsS','--retry','2','--connect-timeout','10','--max-time','180',
                 'https://api.github.com/repos/liucong552-art/ceshi/tarball/'+req['ref'],'-o',work/'source.tar.gz'],timeout=240)
        extract_archive(work/'source.tar.gz',work/'source')
        require(hashlib.sha256((work/'source/hy2.sh').read_bytes()).hexdigest()==APP_SHA,'Candidate installer hash mismatch')
        command(['curl','-fLsS','--retry','2','--connect-timeout','10','--max-time','180',CORE_URL,'-o',work/'hysteria'],timeout=240)
        require(hashlib.sha256((work/'hysteria').read_bytes()).hexdigest()==CORE_SHA,'Official core hash mismatch'); (work/'hysteria').chmod(0o700)
        POINTER.write_text(str(work)+'\n'); POINTER.chmod(0o600)
        unit=f'''[Unit]
Description=Finish Helan non-WG acceptance after its single planned reboot
After=network-online.target hy2-managed-restore.service hy2-ipban.service socks5-restore.service portbw-restore.service
Wants=network-online.target
ConditionPathExists={work}/request.json
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 -B {work}/source/tests/helan_onepass.py after
TimeoutStartSec=1800
[Install]
WantedBy=multi-user.target
'''
        Path('/etc/systemd/system/helan-hy2-onepass-after.service').write_text(unit)
        command(['systemctl','daemon-reload']); command(['systemctl','enable','helan-hy2-onepass-after.service'])
        command(['systemd-run','--unit=helan-hy2-onepass-before','--collect','--property=Type=exec','--property=TimeoutStartSec=0',
                 '/usr/bin/python3','-B',work/'source/tests/helan_onepass.py','before'])
    except Exception:
        atomic(work/'status.json',{'state':'FAILED','phase':'bootstrap','cases':[],'message':'Bootstrap stopped; inspect this root-only directory'})
        raise
    return public_status(work)


class Runner:
    def __init__(self):
        host_guard(); self.work=current(); self.req=load(self.work/'request.json'); request_guard(self.req)
        self.src=self.work/'source'; (self.work/'logs').mkdir(exist_ok=True)
        self.env=dict(os.environ,HY2_TEST_RUNNER_REF=self.req['ref'],HY_DOMAIN=self.req['domain'],ACME_EMAIL=self.req['email'],
                      PYTHONDONTWRITEBYTECODE='1',HY2_TEST_HOST_NETNS=os.readlink('/proc/self/ns/net'),HYSTERIA_DISABLE_UPDATE_CHECK='1')

    def update(self,**kw):
        value=load(self.work/'status.json'); value.update(kw); value['updated_utc']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()); atomic(self.work/'status.json',value)

    def phase(self,name,args,count=1,timeout=1200):
        self.update(state='RUNNING',phase=name,message='Running '+name)
        started=time.monotonic(); proc=None; success=False
        try:
            with (self.work/'logs'/f'{name}.private.log').open('w') as log:
                proc=subprocess.Popen(list(map(str,args)),stdout=log,stderr=subprocess.STDOUT,env=self.env,start_new_session=True,cwd=self.src)
                code=proc.wait(timeout=timeout)
                require(code==0,f'{name} exited {code}; inspect its private log')
            success=True
        finally:
            if proc is not None:
                try: os.killpg(proc.pid,signal.SIGTERM)
                except ProcessLookupError: pass
                try: proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid,signal.SIGKILL); proc.wait(timeout=4)
            data=load(self.work/'status.json'); data['cases'].append({'phase':name,'status':'PASS' if success else 'FAIL','checks':count if success else 0,
                                                                    'seconds':round(time.monotonic()-started,3)})
            atomic(self.work/'status.json',data)

    def before(self):
        require(boot()==self.req['initial_boot_id'],'Unexpected boot before pre-reboot acceptance')
        py=[sys.executable,'-B']
        self.phase('system_dependencies',['bash','-Eeuo','pipefail','-c','export DEBIAN_FRONTEND=noninteractive; apt-get update -o Acquire::Retries=3; apt-get install -y --no-install-recommends curl ca-certificates python3 openssl nftables iproute2 util-linux coreutils procps gnupg'],count=0,timeout=900)
        self.phase('offline_bundle_and_regressions',['bash','-Eeuo','pipefail','-c','python3 -B tests/check_bundle.py && python3 -B -m unittest discover -s tests -q'],count=0)
        self.phase('HY2_plain_salamander_bind_TLS_TCP_UDP_auth',py+[self.src/'tests/hy2_loopback.py',self.work/'hysteria','--bind-device'],count=15)
        kernel=self.work/'kernel'; kernel.mkdir()
        self.phase('HY2_kernel_quota_IP_watchdog', ['unshare','--net','--',*py,self.src/'tests/hy2_kernel_acceptance.py','--work',kernel,'--binary',self.work/'hysteria','--source-ref',APP_REF],count=21)
        report=load(kernel/'result.json'); require(report['passed']==21 and report['failed']==0,'Incomplete HY2 kernel report')
        self.phase('install_TLS_systemd_expiry',['bash',self.src/'tests/helan_round3.sh'],count=10,timeout=2400)
        self.phase('installed_optional_components_renewal_rollback',py+[self.src/'tests/helan_onepass_addons.py','before','--work',self.work],count=6,timeout=2400)
        extras=self.work/'extras-kernel'; extras.mkdir()
        self.phase('real_optional_kernel_packets',['unshare','--net','--',*py,self.src/'tests/hy2_extras_kernel.py','suite','--work',extras],count=6,timeout=700)
        er=load(extras/'result.json'); require(er['passed']==6 and er['failed']==0,'Incomplete optional kernel report')
        self.phase('one_hour_CPU_sample',py+[self.src/'tests/helan_onepass_addons.py','cpu','--work',self.work,'--seconds',self.req['cpu_seconds']],timeout=self.req['cpu_seconds']+120)
        self.phase('final_pre_reboot_checkpoint',py+[self.src/'tests/helan_onepass_addons.py','checkpoint','--work',self.work],timeout=240)
        self.update(state='REBOOTING',planned_reboot=True,phase='planned_reboot',message='One planned reboot; automatic reconnection will follow')
        command(['systemd-run','--unit=helan-hy2-onepass-reboot','--on-active=5s','/usr/bin/systemctl','reboot'])

    def after(self):
        require(boot()!=self.req['initial_boot_id'],'Refusing to claim reboot recovery in the original boot')
        state=load(self.work/'status.json')['state']
        if state in ('SERVER_PASS','COMPLETE'): return
        require(load(self.work/'status.json').get('planned_reboot') and state in ('REBOOTING','RUNNING'),'No planned reboot checkpoint to verify')
        self.phase('post_boot_HY2_automatic_recovery',['bash','/root/helan_hy2_round3.sh','verify'],count=7,timeout=700)
        self.phase('post_boot_optional_recovery_cleanup',[sys.executable,'-B',self.src/'tests/helan_onepass_addons.py','after','--work',self.work],count=4,timeout=600)
        self.update(state='SERVER_PASS',phase='waiting_for_Windows_public_client',message='Server and reboot checks passed; Windows will now test the public path')

    def finish(self,client):
        require(load(self.work/'status.json')['state'] in ('SERVER_PASS','COMPLETE'),'Server suite is not complete')
        require(client.get('mode')=='public' and client.get('passed')==4 and client.get('failed')==0 and client.get('public_udp_client_tested') is True,'Public client acceptance incomplete')
        require(client.get('runner_ref')==self.req['ref'],'Client report has wrong source version')
        require(len(client.get('cases',[]))==4 and all(row.get('status')=='PASS' for row in client['cases']),'Partial client cases cannot complete acceptance')
        report=load(self.work/'status.json'); require(all(row['status']=='PASS' for row in report['cases']),'Some server checks failed')
        cpu=load(self.work/'cpu.json')
        require(cpu.get('requested_seconds')==self.req['cpu_seconds'] and cpu.get('seconds',0)>=self.req['cpu_seconds'],'CPU sampling did not finish the requested duration')
        before=load(self.work/'addons/before-result.json'); require(before.get('actual_acme_renewal_tested') and before.get('bad_core_rollback_tested'),'Renewal or rollback acceptance incomplete')
        report.update(state='COMPLETE',phase='complete',message='All configured non-WG acceptance groups passed',
                      windows_client={k:v for k,v in client.items() if k in ('cases','passed','failed','tcp_exit_ip','binary_sha256','public_udp_client_tested')},
                      cpu=cpu,manager_source_ref=APP_REF,runner_ref=self.req['ref'],actual_acme_renewal_tested=True,bad_core_rollback_tested=True,
                      reboot_acceptance_complete=boot()!=self.req['initial_boot_id'],public_udp_client_tested=True,
                      excluded=['WG and dual-machine NAT deferred by user','public IPv6 reachability requires an assigned public IPv6 and client path',
                                'Cloudflare DDNS write requires separately configured provider credentials','CPU measurement is idle-stack overhead, not a production-scale or VLESS comparison'])
        report['passed_checks']=sum(row['checks'] for row in report['cases'])+4
        command(['systemctl','disable','helan-hy2-onepass-after.service'])
        atomic(self.work/'result.json',report); atomic(self.work/'status.json',report)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('action',choices=['start','status','before','after','finish']); args=parser.parse_args(); os.umask(0o077)
    if args.action=='start': frame(start(json.load(sys.stdin))); return
    host_guard()
    if args.action=='status': frame(public_status(current())); return
    runner=Runner()
    try:
        if args.action=='finish': runner.finish(json.load(sys.stdin)); frame(public_status(runner.work))
        else: getattr(runner,args.action)()
    except Exception as error:
        runner.update(state='FAILED',message=str(error)[:600]); raise


if __name__=='__main__': main()
