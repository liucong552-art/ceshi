#!/usr/bin/env python3
"""Installed non-WG components, real renewal/rollback, CPU and boot checks."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import time

from hy2_systemd_acceptance import Acceptance, CORE_SHA, boot_id, digest, metadata, require

ROOT = Path(__file__).resolve().parents[1]
S5ID = 'onepass-s5'
S5FILE = Path('/var/lib/socks5-manager/nodes') / (S5ID + '.json')
PBWPORTS = (49992, 49993)
BANPORT, BANIP = 49991, '192.0.2.1'


def load(path): return json.loads(Path(path).read_text())


class Addons(Acceptance):
    def __init__(self, work, stage):
        require(os.geteuid()==0 and platform.node().split('.')[0]=='helan', 'Helan root required')
        require(work.parent==Path('/root') and work.name.startswith('hy2-onepass.'), 'Unexpected onepass directory')
        self.request=load(work/'request.json')
        round3=Path('/root/helan_hy2_round3.path').read_text().strip()
        manifest=load(Path(round3)/'manifest.json')
        super().__init__(work/'addons', stage, manifest)
        self.work.mkdir(exist_ok=True, mode=0o700)
        self.outer, self.round3 = work, Path(round3)
        self.iface=self.command(['bash','-c',"ip -4 route get 1.1.1.1 | sed -nE 's/.* dev ([A-Za-z0-9_.-]+).*/\\1/p'"]).stdout.strip()
        require(self.iface and not self.iface.startswith(('wg','tun','lo','tailscale')), 'Physical default interface required')

    def history(self, kind):
        path = '/var/lib/hy2/traffic/daily.json' if kind=='hy2' else '/var/lib/socks5-manager/traffic/daily.json'
        users=load(path)['users']
        key='main' if kind=='hy2' else S5ID
        row=users[key] if key in users else next(x for x in users.values() if x.get('id')==key)
        return {name:sum(day[name] for day in row.get('days',{}).values()) for name in ('upload','download')}

    def socks_https(self, bad=False):
        node=load(S5FILE)
        config=self.work/'curl-socks.private.conf'
        password='wrong-password' if bad else node['password']
        config.write_text(f'proxy = "socks5h://127.0.0.1:{node["port"]}"\nproxy-user = "{node["username"]}:{password}"\n')
        config.chmod(0o600)
        try:
            result=self.command(['curl','--fail','--silent','--show-error','--connect-timeout','8','--max-time','25',
                                 '--config',config,'https://www.cloudflare.com/cdn-cgi/trace'], check=False)
            require(result.returncode!=0 if bad else result.returncode==0, 'Actual installed SOCKS HTTPS/auth check failed')
            if not bad: require('ip=' in result.stdout, 'SOCKS HTTPS response is incomplete')
        finally: config.unlink(missing_ok=True)

    def guide_ban(self, remove=False):
        section=(ROOT/'HY2_GUIDE.md').read_text().split('## 十、',1)[1]
        block=re.findall(r'```bash\n(.*?)\n```',section,re.S)[1 if remove else 0]
        block=re.sub(r'^P=40001 IPS="[^"]*"', f'P={BANPORT} IPS="{BANIP}"', block)
        self.command(['bash','-Eeuo','pipefail','-c',block])

    def before(self):
        def traffic():
            self.command(['python3','-B',ROOT/'traffic.py','--install'])
            self.command(['/usr/local/sbin/hy2_traffic','--collect'])
            before=self.history('hy2'); self.probe(self.node('main'))
            self.command(['/usr/local/sbin/hy2_traffic','--collect'])
            after=self.history('hy2')
            require(all(after[k]>before[k] for k in before), 'HY2 daily counters did not count actual upload and download')
            self.command(['nft','delete','table','inet','hy2_daily_traffic'])
            self.command(['/usr/local/sbin/hy2_traffic','--collect'])
            require(self.history('hy2')==after, 'Daily rule restoration reset or duplicated saved history')
        self.case('HY2_daily_actual_both_directions_missing_table_recovery',traffic)
        def portbw():
            roots=lambda:[x for x in json.loads(self.command(['tc','-j','qdisc','show','dev',self.iface]).stdout) if x.get('root')]
            previous=roots()
            self.command(['bash',ROOT/'portbw-install.sh','check','--iface',self.iface])
            self.command(['bash',ROOT/'portbw-install.sh','install','--iface',self.iface],timeout=180)
            self.command(['bash',ROOT/'portbw-install.sh','verify'])
            self.command(['portbw','set',PBWPORTS[0],'2','2'])
            self.command(['portbw','auto','set',PBWPORTS[1],'--up','2','--down','2','--trigger','100','--after','5s',
                          '--auto-up','1','--auto-down','1','--hold','10s','--cooldown','0s'])
            self.command(['portbw','audit'])
            require(roots()==previous, 'Port limiter changed the physical root qdisc')
        self.case('portbw_real_install_static_auto_policies_audit',portbw)
        def socks():
            require(not Path('/var/lib/socks5-manager').exists(), 'Refusing existing SOCKS state')
            self.command(['bash',ROOT/'socks5-install.sh','deps'],timeout=600)
            self.command(['systemctl','disable','--now','3proxy.service'],check=False)
            self.command(['bash',ROOT/'socks5-install.sh','install','--wan-if',self.iface,'--host',self.manifest['domain']],timeout=180)
            self.command(['socks5','add','--id',S5ID,'--seconds','21600','--port','44555','--quota-gib','0.0625',
                          '--ip-limit','1','--sticky','600'],timeout=180)
            self.socks_https(); self.socks_https(bad=True)
            self.command(['socks5','audit',S5ID])
            self.command(['socks5-traffic','--install'])
            self.command(['socks5-traffic','--collect'])
            before=self.history('socks'); self.socks_https(); self.command(['socks5-traffic','--collect'])
            after=self.history('socks'); require(all(after[k]>before[k] for k in before), 'SOCKS daily traffic did not increase')
            logs=self.command(['journalctl','-u','socks5-@'+S5ID+'.service','--no-pager','-o','cat','-n','100']).stdout
            require('S5AUDIT' in logs and 'www.cloudflare.com' in logs, 'Actual managed SOCKS access logs missing')
        self.case('SOCKS_real_install_auth_HTTPS_audit_daily_traffic',socks)
        def rollback():
            fixture=self.work/'bad-core'; fixture.write_text('#!/bin/sh\nif [ "$1" = version ]; then echo "Version: v2.13.0"; exit 0; fi\nexit 1\n'); fixture.chmod(0o700)
            before={kind:(digest(self.node(kind)['config']),digest(self.node(kind)['url'])) for kind in ('main','a','b')}
            usage={kind:self.quota(self.node(kind))[2] for kind in before}
            result=self.command(['bash','/root/onekey_hy2_main_tls.sh'],env={'HYSTERIA_FORCE_UPDATE':'1','HYSTERIA_LOCAL_BINARY':str(fixture)},check=False,timeout=300)
            require(result.returncode!=0, 'Bad core update unexpectedly succeeded')
            require('核心二进制 SHA-256 不匹配' in result.stdout+result.stderr,'Core update failed before the intended hash rejection')
            binary=self.command(['bash','-c','command -v hysteria']).stdout.strip()
            require(digest(binary)==CORE_SHA, 'Core rollback did not restore the verified official binary')
            for kind,hashes in before.items():
                node=self.node(kind); require((digest(node['config']),digest(node['url']))==hashes,'Failed update changed credentials/config')
                self.command(['systemctl','is-active','--quiet',node['unit']])
                require(self.quota(node)[2]>=usage[kind], 'Failed core update reset quota usage')
            self.probe(self.node('main')); self.probe(self.node('a'))
        self.case('actual_bad_core_update_SHA_rejection_and_rollback',rollback)
        def renewal():
            meta=metadata('/var/lib/hy2/main/main.env'); cert=Path(meta['TLS_CERT'])
            fingerprint=lambda:self.command(['openssl','x509','-in',cert,'-fingerprint','-sha256','-noout']).stdout.strip()
            before=fingerprint()
            self.command(['/root/.acme.sh/acme.sh','--renew','--server','zerossl','--force','-d',meta['HY_DOMAIN']],timeout=900)
            require(fingerprint()!=before,'Forced CA renewal did not publish a new leaf certificate')
            self.command(['openssl','x509','-in',cert,'-checkend','604800','-noout'])
            self.probe(self.node('main')); self.probe(self.node('a'))
            self.subscriptions()
        self.case('actual_ZeroSSL_forced_renewal_reload_and_trusted_transport',renewal)
        self.case('host_blacklist_persistence_install_on_unused_test_tuple',lambda:self.guide_ban())

    def checkpoint(self):
        self.cli('pq_save_state.sh',timeout=180)
        baseline=load(self.round3/'baseline.json')
        for kind in baseline['nodes']: baseline['nodes'][kind]['quota']=self.quota(self.node(kind))
        (self.round3/'baseline.json').write_text(json.dumps(baseline,indent=2))
        self.command(['socks5','save']); self.command(['socks5-traffic','--collect']); self.command(['hy2_traffic','--collect'])
        s5=load(S5FILE)
        record={'boot_id':boot_id(),'socks_expires':s5['expires'], 'socks_quota_saved':s5['quota']['saved'],
                'socks_config_sha':digest('/etc/socks5-manager/nodes/'+S5ID+'.cfg'),
                'hy2_daily':self.history('hy2'),'socks_daily':self.history('socks')}
        (self.work/'baseline.json').write_text(json.dumps(record,indent=2))

    def after(self):
        saved=load(self.work/'baseline.json'); require(saved['boot_id']!=boot_id(),'Real reboot required')
        def services():
            for unit in ('hy2-traffic.timer','hy2-traffic-shutdown.service','socks5-traffic.timer','socks5-traffic-shutdown.service',
                         'socks5-save.timer','socks5-gc.timer','socks5-reset.timer','socks5-watch.timer','socks5-shutdown.service',
                         'socks5-@'+S5ID+'.service','portbw-watch.service'):
                self.command(['systemctl','is-enabled','--quiet',unit]); self.command(['systemctl','is-active','--quiet',unit])
            self.command(['socks5','audit',S5ID]); self.command(['portbw','audit'])
            self.command(['bash',ROOT/'portbw-install.sh','verify'])
        self.case('post_boot_optional_services_real_guard_audits',services)
        def data():
            n=load(S5FILE)
            require(n['expires']==saved['socks_expires'] and n['quota']['saved']>=saved['socks_quota_saved'], 'SOCKS expiry/quota reset across boot')
            require(digest('/etc/socks5-manager/nodes/'+S5ID+'.cfg')==saved['socks_config_sha'], 'SOCKS credentials changed across boot')
            for kind,key in (('hy2','hy2_daily'),('socks','socks_daily')):
                current=self.history(kind); require(all(current[k]>=saved[key][k] for k in current), 'Daily history decreased across boot')
            for port in PBWPORTS:
                self.command(['portbw','show',port])
                require(load('/var/lib/portbw/ports/'+str(port)+'.json')['port']==port,'Saved port policy missing')
            self.socks_https()
        self.case('post_boot_SOCKS_credentials_quota_daily_history_port_policies',data)
        def blacklist():
            self.command(['nft','get','element','inet','hy2_ipban','banned','{ '+BANIP+' . '+str(BANPORT)+' }'])
            props=self.command(['systemctl','show','hy2-ipban.service','-p','ExecMainStatus','-p','ExecMainStartTimestampMonotonic']).stdout
            require('ExecMainStatus=0' in props and not re.search(r'ExecMainStartTimestampMonotonic=0(?:\n|$)',props), 'Blacklist restore did not execute this boot')
            self.guide_ban(remove=True)
            require(not Path('/etc/hy2-ipban.nft').exists() and not Path('/etc/systemd/system/hy2-ipban.service').exists(), 'Final unban did not clean persistence')
        self.case('post_boot_actual_blacklist_restore_and_final_unban_cleanup',blacklist)
        def cleanup():
            self.command(['socks5','del',S5ID]); require(not S5FILE.exists(),'Test SOCKS account remains')
            self.command(['socks5-traffic','--collect'])
            for port in PBWPORTS: self.command(['portbw','del',port])
            self.command(['portbw','audit']); self.cli('hy2_doctor.sh','--strict',timeout=180)
        self.case('cleanup_only_test_accounts_and_port_policies_strict_doctor',cleanup)


def cpu(work, seconds):
    require(60<=seconds<=7200,'CPU sample duration out of bounds')
    def reading():
        values=list(map(int,Path('/proc/stat').read_text().splitlines()[0].split()[1:]))
        return sum(values[:8]), values[3]+values[4]
    start=time.monotonic(); first=previous=reading(); samples=[]
    while time.monotonic()-start<seconds:
        time.sleep(min(30,max(0,seconds-(time.monotonic()-start))))
        current=reading(); total=current[0]-previous[0]; idle=current[1]-previous[1]
        samples.append({'elapsed':round(time.monotonic()-start,1),'busy_percent':round(100*(total-idle)/max(total,1),3)})
        previous=current
        if len(samples)%10==0: print(f'CPU sampling {int(time.monotonic()-start)}/{seconds}s',flush=True)
    total=previous[0]-first[0]; idle=previous[1]-first[1]
    report={'seconds':round(time.monotonic()-start,3),'requested_seconds':seconds,
            'average_host_cpu_percent':round(100*(total-idle)/max(total,1),3),'samples':samples,
            'workload':'idle installed stack: main + 2 temporary HY2, one SOCKS account, standard unmodified timers',
            'production_scale_or_VLESS_comparison':False}
    (work/'cpu.json').write_text(json.dumps(report,indent=2)); print(json.dumps({k:v for k,v in report.items() if k!='samples'}),flush=True)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('stage',choices=['before','after','checkpoint','cpu']); parser.add_argument('--work',type=Path,required=True); parser.add_argument('--seconds',type=int,default=3600)
    args=parser.parse_args(); os.umask(0o077)
    require(os.geteuid()==0 and platform.node().split('.')[0]=='helan','Helan root required')
    require(args.work.resolve().parent==Path('/root') and args.work.name.startswith('hy2-onepass.'),'Unexpected report directory')
    if args.stage=='cpu': cpu(args.work,args.seconds)
    else:
        suite=Addons(args.work.resolve(),args.stage); getattr(suite,args.stage)()


if __name__=='__main__': main()
