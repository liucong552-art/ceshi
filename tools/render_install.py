#!/usr/bin/env python3
"""Run real HY2 generators in an isolated prefix; never run the installer main."""
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from helpers import heredocs  # noqa: E402


def main():
    prefix = Path(sys.argv[1]).resolve()
    if prefix == Path('/') or prefix.exists():
        raise SystemExit('choose a new empty sandbox directory')
    prefix.mkdir(parents=True)
    text = (ROOT / 'hy2.sh').read_text(encoding='utf-8')
    assert text.endswith('main "$@"\n')
    text = text.removesuffix('main "$@"\n')
    # Longest roots first. Replacements also apply inside nested generators.
    for path in ('/usr/local/', '/etc/', '/var/', '/root/', '/run/'):
        text = text.replace(path, prefix.as_posix() + path)
    calls = '''
systemd-tmpfiles() { :; }
chown() { :; }
install_dirs
install_env_template
install_tmpfiles
install_common_lib
install_render_table
install_quota_lib
install_iplimit_lib
install_main_script
install_quota_scripts
install_iplimit_scripts
install_hy2_management_scripts
install_root_helper
install_systemd_units
install_logrotate_rules
'''
    # This prefix contains only generated files and mocked administrative calls.
    for path in ('etc/default', 'etc/tmpfiles.d', 'etc/logrotate.d', 'etc/systemd/system', 'root'):
        (prefix / path).mkdir(parents=True, exist_ok=True)
    subprocess.run(['bash', '-s'], input=text + calls, text=True, check=True)
    # Main and temporary service units are generated only at node deployment.
    # Exercise those real generator functions without starting a service/core.
    main_script = prefix / 'root/onekey_hy2_main_tls.sh'
    main_source = main_script.read_text(encoding='utf-8').removesuffix('main "$@"\n')
    subprocess.run(['bash', '-s'], input=main_source + '''
write_main_unit /bin/true
hy2_write_temp_unit hy2-temp-verify /nonexistent/verify.yaml local
''', text=True, check=True)
    # WG and NAT units have literal heredoc payloads. Verify every payload,
    # including the drop-in in the context of a minimal wg-quick base unit.
    names = {
        'WG_EXIT_FIREWALL': 'usr/local/sbin/wg_exit_firewall.sh',
        'WG_EXIT_SERVICE': 'etc/systemd/system/wg-exit-firewall@.service',
        'WG_EXIT_TIMER': 'etc/systemd/system/wg-exit-firewall@.timer',
        'WG_NAT_GUARD': 'usr/local/sbin/wg_nat_guard.sh',
        'WG_GUARD_SERVICE': 'etc/systemd/system/wg-nat-guard@.service',
        'WG_GUARD_TIMER': 'etc/systemd/system/wg-nat-guard@.timer',
        'WG_GUARD_DROPIN': 'etc/systemd/system/wg-quick@wg-nat.service.d/10-vr-nat-guard.conf',
        'SH_SET_PEER': 'usr/local/sbin/wg_nat_set_peer.sh',
        'SH_HEALTH': 'usr/local/sbin/wg_nat_healthcheck.sh',
        'PREFLIGHT': 'usr/local/sbin/hy2_nat_preflight.sh',
        'CREATOR': 'usr/local/sbin/hy2_mktemp_nat.sh',
    }
    for filename in ('nat.sh', 'vpswg.sh', 'natjichang.sh'):
        for _, marker, body in heredocs((ROOT / filename).read_text(encoding='utf-8')):
            if marker not in names:
                continue
            target = prefix / names[marker]
            target.parent.mkdir(parents=True, exist_ok=True)
            for path in ('/usr/local/', '/etc/', '/var/', '/root/', '/run/'):
                body = body.replace(path, prefix.as_posix() + path)
            target.write_text(body, encoding='utf-8')
            target.chmod(0o755 if target.suffix == '.sh' else 0o644)
    base = prefix / 'etc/systemd/system/wg-quick@wg-nat.service'
    base.write_text('[Service]\nType=oneshot\nExecStart=/bin/true\nRemainAfterExit=yes\n')
    scripts = list(prefix.rglob('*.sh'))
    for path in scripts:
        subprocess.run(['bash', '-n', str(path)], check=True)
    for path in prefix.rglob('*.py'):
        compile(path.read_text(encoding='utf-8'), str(path), 'exec')
    units = sorted(path for path in (prefix / 'etc/systemd/system').glob('*')
                   if path.suffix in ('.service', '.timer'))
    subprocess.run(['systemd-analyze', 'verify', *map(str, units)], check=True)
    print(f'Actual generators rendered and verified: {len(scripts)} scripts, {len(units)} units')


if __name__ == '__main__':
    main()
