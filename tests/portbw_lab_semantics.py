#!/usr/bin/env python3
"""Kernel semantics: concurrent shared police, recovery and fail-closed.

Launched ONLY from portbw_ab_lab_runner.py inside its disposable netns.
This is NOT a CPU benchmark (client/server packet-processing CPU is included).
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time


PORT = 43000
CONTROL = 43080
INTERFACE = "pbwl0"
TRAFFIC = Path(__file__).with_name("portbw_lab_traffic.py")


def run(*cmd, **kwargs):
    return subprocess.run(cmd, check=True, text=True, **kwargs)


def guard():
    if os.environ.get("PORTBW_LAB_ACK") != "isolated-netns-only":
        raise SystemExit("REFUSED: missing explicit test acknowledgement")
    if os.geteuid() or os.stat("/proc/self/ns/net").st_ino == os.stat("/proc/1/ns/net").st_ino:
        raise SystemExit("REFUSED: not in disposable root netns")
    links=json.loads(run("ip","-j","link",stdout=subprocess.PIPE).stdout)
    names={x.get("ifname") for x in links}
    if INTERFACE not in names or "eth0" in names or "wg-nat" in names:
        raise SystemExit("REFUSED: lab interface absent or production interface visible")
    if not os.environ.get("PORTBW_STATE","").startswith("/tmp/portbw-cpu-"):
        raise SystemExit("REFUSED: portbw state must be in private /tmp lab")


def load(path):
    spec=importlib.util.spec_from_file_location("portbw_semantics_target",path)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse_log(path,kind):
    body=path.read_text(encoding="utf-8") if path.exists() else ""
    try:
        return json.loads(body.strip().splitlines()[-1])
    except (IndexError,ValueError) as exc:
        raise RuntimeError(f"{kind} failed: {body[-2000:]!r}") from exc


def flow_batch(pid,root,label,flows,port,seconds):
    """Measure the sum of bytes delivered to a single destination port."""
    logs=root / "results" / "semantics"
    logs.mkdir(parents=True,exist_ok=True)
    workers=[]
    results=[]
    try:
        for i,flow in enumerate(flows):
            proto,family=flow[:2]
            name=f"{label}_{i}_{proto}_{family}"
            serverpath=logs / (name+"_server.log")
            fp=serverpath.open("w")
            p=subprocess.Popen([sys.executable,str(TRAFFIC),"server",
                                "--proto",proto,"--family",family,
                                "--port",str(port),"--seconds",str(seconds+4)],
                               stdout=fp,stderr=subprocess.STDOUT)
            workers.append((p,fp,serverpath,"server"))
        time.sleep(.6)
        for i,flow in enumerate(flows):
            proto,family=flow[:2]
            name=f"{label}_{i}_{proto}_{family}"
            path=logs / (name+"_client.log")
            fp=path.open("w")
            p=subprocess.Popen(["nsenter","-t",str(pid),"-n",sys.executable,str(TRAFFIC),
                                "client","--proto",proto,"--family",family,
                                "--port",str(port),"--seconds",str(seconds),
                                "--mbit",str(flow[2] if len(flow)>2 else 3)],stdout=fp,stderr=subprocess.STDOUT)
            workers.append((p,fp,path,"client"))
        for p,fp,path,role in workers:
            try:
                p.wait(timeout=seconds+14)
            except subprocess.TimeoutExpired:
                p.kill();p.wait()
                raise RuntimeError(f"{label} {role} timed out: {path}")
            fp.flush()
            if p.returncode:
                raise RuntimeError(f"{label} {role} exited {p.returncode}: {path.read_text()[-2000:]}")
        sent=received=0
        flow_report=[]
        for i,flow in enumerate(flows):
            proto,family=flow[:2]
            name=f"{label}_{i}_{proto}_{family}"
            server=parse_log(logs/(name+"_server.log"),"server")
            client=parse_log(logs/(name+"_client.log"),"client")
            if client.get("tx_bytes",0)<=0 or server.get("rx_bytes",0)<=0:
                raise RuntimeError(f"{label}: zero delivered data in {proto}/{family}: {server}, {client}")
            sent+=client["tx_bytes"]
            received+=server["rx_bytes"]
            flow_report.append({"proto":proto,"family":family,
                                "offered_mbps":round(client["tx_bytes"]*8/seconds/1e6,3),
                                "delivered_mbps":round(server["rx_bytes"]*8/seconds/1e6,3)})
        data={"label":label,"port":port,"seconds":seconds,
              "offered_mbps":round(sent*8/seconds/1e6,3),
              "delivered_mbps":round(received*8/seconds/1e6,3),
              "flows":flow_report}
        print("SEMANTIC_FLOW="+json.dumps(data,sort_keys=True),flush=True)
        return data
    finally:
        for p,fp,_,_ in workers:
            if p.poll() is None:
                p.terminate()
                try:p.wait(timeout=2)
                except subprocess.TimeoutExpired:p.kill();p.wait()
            fp.close()


def police_stats(mod, direction="up"):
    """Count only the EXACT shared action; no tc filter stat multiplication."""
    block=mod.tc_action_block(mod.tc_police_index(PORT,direction))
    if block is None:
        raise RuntimeError(f"shared tc police action missing: {direction}")
    parts=__import__("re").findall(
        r'\bSent\s+([0-9]+)\s+bytes\s+([0-9]+)\s+pkt\s+'
        r'\(dropped\s+([0-9]+),\s+overlimits\s+([0-9]+)',block)
    if len(parts)!=1:
        raise RuntimeError(f"shared police action missing/unparseable counters: {block[:1500]}")
    values=list(map(int,parts[0]))
    return {"bytes":values[0],"packets":values[1],
            "drops":values[2],"overlimits":values[3]}


def police_delta(before,after):
    delta={name:after[name]-before[name] for name in before}
    if any(value<0 for value in delta.values()):
        raise RuntimeError(f"police counters reset during experiment: {before}, {after}")
    return delta


def check_shared_cap(scenario,sample,pressure=None):
    """Require honest transport participation and kernel policing evidence.

    The TCP client reports socket-accepted bytes, not hypothetical offered
    demand. TCP congestion control may cut accepted writes below the nominal
    policer rate. No fixed 2.3 Mbps application-write threshold is valid
    for TCP. Direct same-action overlimit/drop counters prove pressure.
    """
    flows=sample["flows"]
    delivered=sample["delivered_mbps"]
    if not .70 < delivered < 1.35:
        raise RuntimeError(f"{scenario}: aggregate throughput not near shared 1 Mbps: {sample}")
    if scenario=="udp-ipv4-ipv6":
        if sample["offered_mbps"]<2.3 or len(flows)!=2 or not all(
                x["proto"]=="udp" and x["offered_mbps"]>1.0
                and x["delivered_mbps"]>.1 for x in flows):
            raise RuntimeError(f"{scenario}: dual-stack UDP overload/participation not proven: {sample}")
    elif scenario.startswith("tcp-udp"):
        tcp=[x for x in flows if x["proto"]=="tcp"]
        udp=[x for x in flows if x["proto"]=="udp"]
        if len(tcp)!=1 or len(udp)!=1:
            raise RuntimeError(f"{scenario}: need exactly TCP+UDP: {sample}")
        # Both traffic classes must make a measurable contribution.
        # A TCP rate of 0.009 Mbps is starvation, not an aggregate proof.
        if not (tcp[0]["offered_mbps"]>=.25
                and tcp[0]["delivered_mbps"]>=.15
                and udp[0]["offered_mbps"]>=.4
                and udp[0]["delivered_mbps"]>=.2):
            raise RuntimeError(f"{scenario}: TCP or UDP is starved: {sample}")
    else:
        raise RuntimeError(f"unknown scenario {scenario}")
    if pressure is None:
        raise RuntimeError(f"{scenario}: missing actual shared police counters")
    if pressure.get("overlimits",0)<3 and pressure.get("drops",0)<3:
        # Even 1 Mbps measured at receiver is not sufficient proof if no
        # overlimit was observed: packet generator may be naturally slower.
        raise RuntimeError(f"{scenario}: no demonstrable policing pressure: {sample}, {pressure}")


def aggregate(mod,config,pid,root,label,seconds):
    # Unpoliced controls FIRST: otherwise low TCP delivery could be
    # attributed to an underperforming echo client rather than the policer.
    for fam in ("ipv4","ipv6"):
        tc=flow_batch(pid,root,f"{label}_tcp_control_{fam}",
                      [("tcp",fam,3)],CONTROL,seconds)
        if tc["delivered_mbps"]<1.5:
            raise RuntimeError(f"{label}: uncapped TCP control is underdriven ({fam}): {tc}")
    udp_control=flow_batch(pid,root,f"{label}_udp_control",
                           [("udp","ipv4"),("udp","ipv6")],CONTROL,seconds)
    if udp_control["delivered_mbps"]<2.3:
        raise RuntimeError(f"{label}: uncapped UDP control is underdriven: {udp_control}")

    scenarios=(
        ("udp-ipv4-ipv6",[("udp","ipv4"),("udp","ipv6")]),
        ("tcp-udp-ipv4",None),
        ("tcp-udp-ipv6",None),
    )
    for scenario,fixed_flows in scenarios:
        # TCP applications obey backpressure. Try a few bounded UDP demand
        # points until both transports participate AND the actual shared
        # policer reports drops/overlimits. Never silently accept an
        # inconclusive underloaded run as a successful aggregate test.
        udp_rates=(None,) if fixed_flows is not None else (.45,.65,.85)
        failures=[]
        passed=False
        for attempt,udp_rate in enumerate(udp_rates,1):
            flows=(fixed_flows if fixed_flows is not None else
                   [("tcp",scenario.rsplit("-",1)[1],3),
                    ("udp",scenario.rsplit("-",1)[1],udp_rate)])
            mod.reconcile(config,monitor=False)
            before=police_stats(mod)
            src=flow_batch(pid,root,f"{label}_{scenario}_attempt{attempt}",
                           flows,PORT,seconds)
            delta=police_delta(before,police_stats(mod))
            print("SEMANTIC_POLICE="+json.dumps({
                "version":label,"scenario":scenario,"attempt":attempt,
                "police_delta":delta,"offered_mbps":src["offered_mbps"],
                "delivered_mbps":src["delivered_mbps"]},sort_keys=True),flush=True)
            try:
                check_shared_cap(scenario,src,delta)
                passed=True
                break
            except RuntimeError as exc:
                failures.append(str(exc))
                print("SEMANTIC_ATTEMPT_INCONCLUSIVE="+json.dumps({
                    "version":label,"scenario":scenario,"attempt":attempt,
                    "reason":str(exc)},ensure_ascii=False),flush=True)
        if not passed:
            raise RuntimeError(
                f"{label}/{scenario}: could not establish shared-cap behavior "
                f"after {len(failures)} attempts: {failures}")


def outbound(mod,config,pid,root,label,seconds):
    """Real one-way egress, UDP IPv4 and IPv6 share 'down' police index."""
    script=Path(__file__).with_name("portbw_lab_outbound_traffic.py")
    output=root/"results"/"semantics"
    output.mkdir(parents=True,exist_ok=True)
    families=("ipv4","ipv6")
    def sample(tag, source_port, families, mbit):
        procs=[];files=[]
        try:
            for family in families:
                log=output/f"{label}_{tag}_{family}_sink.log"
                fp=log.open("w")
                cmd=["nsenter","-t",str(pid),"-n",sys.executable,str(script),
                     "sink","--family",family,"--source-port",str(source_port),
                     "--seconds",str(seconds+3),"--mbit",str(mbit)]
                pr=subprocess.Popen(cmd,stdout=fp,stderr=subprocess.STDOUT)
                procs.append((pr,log,"sink"));files.append(fp)
            time.sleep(.4)
            for family in families:
                log=output/f"{label}_{tag}_{family}_source.log"
                fp=log.open("w")
                cmd=[sys.executable,str(script),"source","--family",family,
                     "--source-port",str(source_port),"--seconds",str(seconds),
                     "--mbit",str(mbit)]
                pr=subprocess.Popen(cmd,stdout=fp,stderr=subprocess.STDOUT)
                procs.append((pr,log,"source"));files.append(fp)
            for pr,log,role in procs:
                try:pr.wait(timeout=seconds+9)
                except subprocess.TimeoutExpired:
                    pr.kill();pr.wait()
                    raise RuntimeError(f"outbound {role} timed out: {log}")
                if pr.returncode:
                    raise RuntimeError(f"outbound {role} failed: {log.read_text()[-1800:]}")
            sent=received=0
            for pr,log,role in procs:
                data=json.loads(log.read_text().strip().splitlines()[-1].split("OUTBOUND_TRAFFIC=",1)[1])
                if role=="source":sent+=data["bytes"]
                else:received+=data["bytes"]
            result={"version":label,"tag":tag,"offered_mbps":round(sent*8/seconds/1e6,3),
                    "delivered_mbps":round(received*8/seconds/1e6,3)}
            print("SEMANTIC_OUTBOUND="+json.dumps(result,sort_keys=True),flush=True)
            return result
        finally:
            for pr,_,_ in procs:
                if pr.poll() is None:
                    pr.terminate()
                    try:pr.wait(timeout=2)
                    except subprocess.TimeoutExpired:pr.kill();pr.wait()
            for fp in files:fp.close()

    # First demonstrate the same output interface can transmit >= 3 Mbps
    # when packets do not match the regulated SOURCE port.
    control=sample("unregulated",43080,families,3)
    if control["delivered_mbps"]<3:
        raise RuntimeError(f"unregulated outbound too slow: {control}")
    before=police_stats(mod,"down")
    measured=sample("regulated",PORT,families,3)
    pressure=police_delta(before,police_stats(mod,"down"))
    if not (measured["offered_mbps"]>3 and .7<measured["delivered_mbps"]<1.35):
        raise RuntimeError(f"outbound IPv4/IPv6 shared cap missing: {measured}")
    if pressure["overlimits"]<3 and pressure["drops"]<3:
        raise RuntimeError(f"outbound police did not observe pressure: {pressure}")
    print("SEMANTIC_OUTBOUND_PASS="+json.dumps({"version":label,"pressure":pressure},
         sort_keys=True),flush=True)


def audit(mod,config):
    p=mod.read_json(mod.port_file(PORT))
    mod.validate_record(PORT,p)
    nft=mod.nft_snapshot()
    mod.validate_base(nft)
    tc=mod.tc_snapshot(config["iface"])
    value,*_=mod.audit_one(PORT,p,config,nft,tc)
    if value!="OK":raise RuntimeError(f"rule audit failed: {value}")


def faults(mod,config,label):
    audit(mod,config)
    snapshot=mod.nft_snapshot()
    rule=next(x["rule"] for x in snapshot if x.get("rule",{}).get("comment")==mod.comment("up",PORT,"count"))
    run("nft","delete","rule","inet",mod.TABLE,"inbound","handle",str(rule["handle"]))
    if mod.nft_ok(PORT,mod.read_json(mod.port_file(PORT))):
        raise RuntimeError(f"{label}: missing count rule incorrectly audited healthy")
    mod.reconcile(config,monitor=False)
    audit(mod,config)
    print(f"SEMANTIC_FAULT={label}: missing managed rule repaired",flush=True)

    injected="portbw-foreign-test-guard"
    run("nft","add","rule","inet",mod.TABLE,"inbound","counter","comment",injected)
    try:
        before=mod.nft_snapshot()
        handles=[x["rule"]["handle"] for x in before
                 if x.get("rule",{}).get("comment")==injected]
        if len(handles)!=1:raise RuntimeError("foreign injection not observable")
        blocked=False
        try:mod.reconcile(config,monitor=False)
        except mod.Error:blocked=True
        if not blocked:raise RuntimeError(f"{label}: foreign rule did not block reconcile")
        after=mod.nft_snapshot()
        surviving=[x["rule"]["handle"] for x in after
                   if x.get("rule",{}).get("comment")==injected]
        if surviving!=handles:
            raise RuntimeError(f"{label}: foreign rule changed/deleted")
        print(f"SEMANTIC_FAULT={label}: foreign rule preserved and fail-closed",flush=True)
    finally:
        # Only delete the EXACT lab-injected foreign rule; never flush.
        allrules=mod.nft_snapshot()
        for x in allrules:
            if x.get("rule",{}).get("comment")==injected:
                run("nft","delete","rule","inet",mod.TABLE,"inbound",
                    "handle",str(x["rule"]["handle"]))
    mod.reconcile(config,monitor=False)
    audit(mod,config)

    corrupt=mod.port_file(PORT+1)
    if corrupt.exists():raise RuntimeError("refuse to overwrite a sibling policy")
    corrupt.parent.mkdir(parents=True,exist_ok=True)
    corrupt.write_text("{ invalid test record",encoding="utf-8")
    try:
        try:mod.reconcile(config,monitor=False)
        except mod.Error:pass
        else:raise RuntimeError(f"{label}: damaged sibling should report failure")
        if not corrupt.is_file():
            raise RuntimeError("damaged sibling was unexpectedly removed")
        audit(mod,config)
    finally:
        corrupt.unlink(missing_ok=True)
    print(f"SEMANTIC_FAULT={label}: corrupt sibling isolated",flush=True)



def lifecycle(mod,config,pid,root,label):
    """High load -> hold -> idle -> restoration; real BOOTTIME and tc counters."""
    rec=mod.read_json(mod.port_file(PORT))
    rec["auto"]={
        "version":mod.AUTO_VERSION,
        "base":{"up":125000,"down":125000},
        "limited":{"up":62500,"down":62500},
        "trigger_bp":7000,"after":5,"hold":10,"cooldown":4,
    }
    mod.reset_auto_boot(rec,mod.boot_id())
    mod.validate_record(PORT,rec)
    mod.write_json(mod.port_file(PORT),rec)
    mod.finish_apply(PORT,rec,config)
    logs=root/"results"/"semantics"
    logs.mkdir(parents=True,exist_ok=True)
    sp=logs/(label+"_lifecycle_server.log")
    cp=logs/(label+"_lifecycle_client.log")
    processes=[];handles=[]
    transitions=[]
    try:
        for role,path in (("server",sp),("client",cp)):
            fp=path.open("w")
            handles.append(fp)
            cmd=([sys.executable,str(TRAFFIC),"server"] if role=="server"
                 else ["nsenter","-t",str(pid),"-n",sys.executable,str(TRAFFIC),"client"])
            cmd+=["--proto","udp","--family","ipv4","--port",str(PORT),
                  "--seconds","24" if role=="server" else "20","--mbit","3"]
            processes.append(subprocess.Popen(cmd,stdout=fp,stderr=subprocess.STDOUT))
            if role=="server":time.sleep(.25)
        for i in range(55):
            before=mod.read_json(mod.port_file(PORT))
            now=time.monotonic()
            mod.reconcile(config,monitor=True)
            current=mod.read_json(mod.port_file(PORT))
            mod.validate_record(PORT,current)
            state={d:current["runtime"]["dirs"][d]["phase"] for d in mod.CHAIN}
            row={"tick":i,"phases":state,"rates":{d:current[d] for d in mod.CHAIN}}
            transitions.append(row)
            if i>=36 and all(current[d]==125000 and state[d]=="monitor"
                             for d in mod.CHAIN):
                # Enough time after high traffic for hold and checkpoint to unwind.
                break
            time.sleep(max(0,1-(time.monotonic()-now)))
        trace=logs/(label+"_lifecycle_phases.json")
        trace.write_text(json.dumps(transitions,indent=2)+"\n",encoding="utf-8")
        lowered=[x for x in transitions if all(x["rates"][d]==62500 for d in mod.CHAIN)]
        restored=[x for x in transitions if
                  all(x["rates"][d]==125000 for d in mod.CHAIN)
                  and x["tick"]>20]
        if not lowered:
            raise RuntimeError(f"{label}: no real 0.5 Mbps auto reduction, see {trace}")
        if not restored:
            raise RuntimeError(f"{label}: no return to 1 Mbps after hold, see {trace}")
        cooldown=[x for x in transitions if all(x["phases"][d]=="cooldown" for d in mod.CHAIN)]
        if not cooldown:raise RuntimeError(f"{label}: cooldown phase missing: {trace}")
        if not any(x["tick"]>cooldown[0]["tick"] and all(x["phases"][d]=="monitor" for d in mod.CHAIN) for x in transitions):
            raise RuntimeError(f"{label}: no transition from cooldown to monitor: {trace}")
        if any("hold" in x["phases"].values() and
               any(x["rates"][d]!=62500 for d in mod.CHAIN
                   if x["phases"][d]=="hold") for x in transitions):
            raise RuntimeError(f"{label}: hold phase with unexpected rate")
        audit(mod,config)
        print("SEMANTIC_LIFECYCLE="+json.dumps(
              {"version":label,"lowered":True,"restored":True,
               "ticks":len(transitions),"trace":str(trace)}),flush=True)
    finally:
        for proc in processes:
            if proc.poll() is None:proc.terminate()
            try:proc.wait(timeout=3)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()
        for fp in handles:fp.close()

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--source",required=True)
    p.add_argument("--root",required=True)
    p.add_argument("--child-pid",required=True,type=int)
    p.add_argument("--label",required=True)
    p.add_argument("--suite",choices=["aggregate","faults","lifecycle"],required=True)
    p.add_argument("--seconds",type=int,default=18)
    args=p.parse_args()
    guard()
    root=Path(args.root).resolve()
    if root.parent!=Path("/tmp") or not root.name.startswith("portbw-cpu-"):
        p.error("root must be private /tmp/portbw-cpu-*")
    if args.suite=="aggregate" and not 12<=args.seconds<=120:
        p.error("aggregate duration must be 12..120")
    mod=load(args.source)
    config=mod.cfg()
    audit(mod,config)
    if args.suite=="aggregate":
        aggregate(mod,config,args.child_pid,root,args.label,args.seconds)
    elif args.suite=="faults":
        faults(mod,config,args.label)
    elif args.suite=="outbound":
        outbound(mod,config,args.child_pid,root,args.label,args.seconds)
    else:
        lifecycle(mod,config,args.child_pid,root,args.label)
    audit(mod,config)
    print("SEMANTIC_PASS="+json.dumps({"version":args.label,"suite":args.suite}),flush=True)


if __name__=="__main__":
    main()
