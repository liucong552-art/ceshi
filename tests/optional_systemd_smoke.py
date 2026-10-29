#!/usr/bin/env python3
"""Real optional installers on an ephemeral GitHub VM, never a user host."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]


def main():
    if os.geteuid()!=0 or os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('GITHUB_REPOSITORY')!='liucong552-art/ceshi':
        raise RuntimeError('Refusing optional systemd smoke outside this ephemeral GitHub job')
    work=Path(tempfile.mkdtemp(prefix='hy2-optional-systemd-')); os.chmod(work,0o700)
    def run(args,**kw):
        result=subprocess.run(list(map(str,args)),capture_output=True,text=True,timeout=240,**kw)
        with (work/'commands.private.log').open('a') as log: log.write(result.stdout+result.stderr)
        if result.returncode:
            safe=re.sub(r'(?:socks5|hy2)://\S+','[redacted link]',(result.stdout+result.stderr)[-2000:])
            raise RuntimeError(f'{args[0]} exited {result.returncode}: {safe}')
        return result.stdout
    for path in ('/etc/portbw','/var/lib/socks5-manager','/usr/local/lib/socks5-manager'):
        if Path(path).exists(): raise RuntimeError('CI host was not clean')
    iface=json.loads(run(['ip','-j','-4','route','get','1.1.1.1']))[0]['dev']
    roots=lambda:[x for x in json.loads(run(['tc','-j','qdisc','show','dev',iface])) if x.get('root')]
    prior=roots()
    run(['bash',ROOT/'portbw-install.sh','install','--iface',iface]);run(['bash',ROOT/'portbw-install.sh','verify'])
    run(['portbw','set','49992','2','2']);run(['portbw','audit'])
    if roots()!=prior: raise RuntimeError('Portbw modified the root qdisc')
    print('PASS: actual portbw installer, notify service, policies and root-qdisc preservation',flush=True)
    run(['bash',ROOT/'socks5-install.sh','install','--wan-if',iface,'--host','example.com'])
    run(['socks5','add','--id','ci-systemd','--seconds','900','--port','44555','--ip-limit','1','--quota-gib','0.0625'])
    run(['socks5-traffic','--install'])
    node=json.loads(Path('/var/lib/socks5-manager/nodes/ci-systemd.json').read_text())
    config=work/'curl.private.conf'
    config.write_text(f'proxy="socks5h://127.0.0.1:44555"\nproxy-user="{node["username"]}:{node["password"]}"\n');config.chmod(0o600)
    try: run(['curl','--fail','--silent','--show-error','--max-time','25','--config',config,'https://www.cloudflare.com/cdn-cgi/trace'])
    finally: config.unlink(missing_ok=True)
    run(['socks5','save']);run(['socks5-traffic','--collect']);run(['socks5','audit','ci-systemd'])
    users=json.loads(Path('/var/lib/socks5-manager/traffic/daily.json').read_text())['users']
    days=users['ci-systemd']['days']
    if not all(sum(day[d] for day in days.values())>0 for d in ('upload','download')): raise RuntimeError('Actual daily traffic is empty')
    run(['systemctl','restart','socks5-restore.service']);run(['socks5','audit','ci-systemd'])
    print('PASS: actual SOCKS installer, systemd auth/HTTPS, nft guards, daily traffic and restore',flush=True)
    run(['socks5','del','ci-systemd']);run(['portbw','del','49992'])
    print('PASS: installed optional test policy/account cleanup',flush=True)


if __name__=='__main__': main()
