#!/usr/bin/env python3
from __future__ import annotations
import json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
errors=[]
def err(x): errors.append(x)
def read_json(path):
    try:return json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:err(f'json:{path.relative_to(ROOT)}:{exc}');return {}
def parse_time(value):
    if not isinstance(value,str): return None
    m=re.fullmatch(r'SE-(-?\d+)-(\d+)-(\d+)T(\d+):(\d+):(\d+)',value)
    if not m:return None
    y,mo,d,h,mi,s=map(int,m.groups()); return (((((y*372)+(mo-1)*31+d-1)*24+h)*60+mi)*60+s)
meta=read_json(ROOT/'state/meta.json'); scheduler=read_json(ROOT/'state/time/causal-scheduler.json')
world_time=meta.get('time'); world_key=parse_time(world_time)
if world_key is None:err(f'bad_world_time:{world_time}')
if scheduler.get('world_time')!=world_time:err(f'scheduler_time_drift:{scheduler.get("world_time")}:{world_time}')
hosts=scheduler.get('hosts',{}); events=scheduler.get('events',[])
if not isinstance(hosts,dict):err('scheduler_hosts_not_object');hosts={}
if not isinstance(events,list):err('scheduler_events_not_list');events=[]
events_by_host={hid:[] for hid in hosts}
for event in events:
    if isinstance(event,dict) and event.get('target_host') in hosts:events_by_host[event['target_host']].append(event)

registry=read_json(ROOT/'state/reg/missions-contracts-projects.json')
if registry.get('schema')!='shinobi-domain-registry' or registry.get('owner_id')!='missions_contracts_projects':err('mission_registry_identity')
closed={'completed','failed','cancelled','expired','succeeded','aborted'}; open_status={'scheduled','active','blocked','proposed','accepted','resolving'}
def check_due(record,kind):
    status=record.get('status'); rid=record.get('id'); due=record.get('next_due_at') or record.get('deadline_at')
    if status in open_status:
        if due is not None:
            k=parse_time(due)
            if k is None:err(f'bad_{kind}_due:{rid}:{due}')
            elif world_key is not None and k<=world_key:err(f'overdue_{kind}:{rid}:{due}:{world_time}')
    elif status in closed:
        if record.get('next_due_at') is not None:err(f'closed_{kind}_has_next_due:{rid}')
    else:err(f'unknown_{kind}_status:{rid}:{status}')
for m in registry.get('active_missions',[]):
    if not isinstance(m,dict):err(f'unstructured_mission:{m!r}');continue
    check_due(m,'mission')
for c in registry.get('contracts',[]):
    if not isinstance(c,dict):err(f'unstructured_contract:{c!r}');continue
    check_due(c,'contract')
for p in registry.get('projects',[]):
    if not isinstance(p,dict):err(f'unstructured_project:{p!r}');continue
    check_due(p,'project')

# Material commitments are a first-class registry and scheduled when they have a due time.
commit=read_json(ROOT/'state/reg/commitments.json')
if commit.get('schema')!='commitment-registry' or commit.get('owner_id')!='registry.commitments':err('commitment_registry_identity')
records=commit.get('records',[])
if not isinstance(records,list):err('commitment_records_not_list');records=[]
seen=set()
for rec in records:
    if not isinstance(rec,dict):err(f'bad_commitment:{rec!r}');continue
    rid=rec.get('id')
    if not isinstance(rid,str) or not rid:err('commitment_missing_id');continue
    if rid in seen:err(f'duplicate_commitment:{rid}')
    seen.add(rid)
    status=rec.get('status')
    due=rec.get('due_at')
    if status in {'open','active','scheduled'} and due is not None:
        k=parse_time(due)
        if k is None:err(f'commitment_bad_due:{rid}:{due}')
        elif world_key is not None and k<=world_key:err(f'commitment_overdue:{rid}:{due}')
        host_id=rec.get('host_ref') or f'host.commitment.{rid}'
        if host_id not in hosts:err(f'commitment_host_missing:{rid}:{host_id}')

# Every active canon pressure mirrors exactly one causal host boundary.
pressure_registry=read_json(ROOT/'state/canon/pressures.json'); pressures=list((pressure_registry.get('pressures') or {}).values())
for front in pressures:
    fid=front.get('id')
    if front.get('status') not in {'active','active_hidden','latent_active'}:continue
    host_id=f'host.canon_pressure.{fid}'; wrapper=hosts.get(host_id)
    if not isinstance(wrapper,dict):err(f'canon_pressure_host_missing:{fid}');continue
    state=wrapper.get('state') or {}; boundary=front.get('next_boundary') or {}
    if boundary.get('host_ref')!=host_id:err(f'canon_pressure_host_drift:{fid}')
    if boundary.get('settled_through')!=state.get('resolved_through'):err(f'canon_pressure_cursor_drift:{fid}')
    if boundary.get('due_at')!=state.get('next_due'):err(f'canon_pressure_due_drift:{fid}')

if errors:
    print(f'COMMITMENT LIVENESS FAIL {len(errors)}')
    for e in errors:print('-',e)
    sys.exit(1)
print('COMMITMENT LIVENESS OK')
print(f"missions={len(registry.get('active_missions',[]))} contracts={len(registry.get('contracts',[]))} projects={len(registry.get('projects',[]))} commitments={len(records)} pressures={len(pressures)} causal_hosts={len(hosts)}")
