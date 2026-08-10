from pathlib import Path
import json, sys
ROOT=Path(__file__).resolve().parents[1]
errs=[]
def err(x): errs.append(x)
def rj(rel):
    try: return json.loads((ROOT/rel).read_text(encoding='utf-8'))
    except Exception as e: err(f'json:{rel}:{e}'); return {}
def all_routes(m=None):
    m=m or rj('runtime/contracts/repository-map.json')
    out=dict(m.get('routes',{}))
    for rel in m.get('route_shards',{}).values(): out.update(rj(rel).get('routes',{}))
    return out

# control plane
for f in ['RUNTIME.md','VOICE.md','REPOSITORY_MAP.md','runtime/contracts/repository-map.json','runtime/contracts/temporal-settlement.json','runtime/contracts/autonomous-world-simulation.json']:
    if not (ROOT/f).exists(): err(f'missing_runtime_file:{f}')
for f in ['P'+'LAY.md','PROTO'+'COL.md']:
    if (ROOT/f).exists(): err(f'obsolete_control_file:{f}')

# repository routes resolve and cannot route to retired temporal/military owners
m=rj('runtime/contracts/repository-map.json')
for req in ['RUNTIME.md','VOICE.md','runtime/contracts/repository-map.json','state/meta.json','state/player.json','state/scene.json']:
    if req not in m.get('hot',[]): err(f'startup_missing:{req}')
retired=('state/time/frontier.json','state/time/coverage/','state/unit/','state/unit-capability/','state/unit-kernel/','state/tactical-team/')
for key,route in all_routes(m).items():
    for field in ['r','i','router','w']:
        for rel in route.get(field,[]):
            if any(str(rel).startswith(prefix) for prefix in retired): err(f'route_targets_retired_owner:{key}:{field}:{rel}')
            if rel and '<' not in rel and '*' not in rel and not (ROOT/rel).exists(): err(f'route_missing:{key}:{field}:{rel}')
    for field in ['g','wg']:
        for pat in route.get(field,[]):
            if not pat: continue
            if any(str(pat).startswith(prefix) for prefix in retired): err(f'route_glob_targets_retired_owner:{key}:{field}:{pat}')
            if list(ROOT.glob(pat)): continue
            prefix=pat.split('*',1)[0].rstrip('/')
            base=(ROOT/prefix) if prefix else ROOT
            if prefix and not base.exists(): err(f'route_glob_base_missing:{key}:{field}:{pat}')

# every first-level authoritative data directory is mapped
mapped=set(rj(m.get('directory_map','runtime/contracts/directory-map.json')).get('dirs',{}))
actual=set()
for base in ['state','data','game']:
    q=ROOT/base
    if not q.exists(): continue
    for p in q.iterdir():
        if p.is_dir(): actual.add(base+'/'+p.name)
for rel in sorted(actual-mapped): err(f'unmapped_directory:{rel}')

# temporal engine flags and causal authority
te=rj('runtime/contracts/temporal-settlement.json')
for term in ['continuous_residual','new_process_rule','hard_interrupt_rule','safe_batching','postconditions']:
    if term not in te: err(f'temporal_engine_missing:{term}')
scheduler=rj('state/time/causal-scheduler.json'); meta=rj('state/meta.json')
if scheduler.get('schema')!='causal-scheduler-registry' or scheduler.get('authority') is not True: err('causal_scheduler_identity')
if scheduler.get('world_time')!=meta.get('time'): err('causal_scheduler_world_time_drift')
hosts=scheduler.get('hosts',{}); events=scheduler.get('events',[]); metrics=scheduler.get('metrics',{})
if not isinstance(hosts,dict) or not hosts: err('no_causal_hosts_examined'); hosts={}
if not isinstance(events,list): err('causal_events_not_list'); events=[]
if len(hosts)>256: err(f'causal_host_bloat:{len(hosts)}')
for legacy in ['state/time/frontier.json','state/time/coverage','state/runtime.json','state/reg/life-course-registry.json']:
    if (ROOT/legacy).exists(): err('legacy_temporal_authority_present:'+legacy)
for key in ('global_person_scans','named_persons_scanned_per_advance','global_faction_directory_scans','faction_directory_scans_per_advance'):
    if key in metrics and metrics.get(key) not in (0,None): err(f'causal_scheduler_global_scan:{key}:{metrics.get(key)}')
queued={hid:[] for hid in hosts}
for event in events:
    target=event.get('target_host') if isinstance(event,dict) else None
    if target not in hosts: err(f'causal_event_unknown_host:{target}'); continue
    queued[target].append(event)
for hid,wrapper in hosts.items():
    state=(wrapper or {}).get('state') or {}
    if state.get('host_id')!=hid: err(f'causal_host_key_drift:{hid}')
    due=min((e.get('due_at') for e in queued.get(hid,[])),default=None)
    if state.get('next_due')!=due: err(f'causal_host_next_due_drift:{hid}:{state.get("next_due")}:{due}')

# runtime catch-up vectors remain meaningful
ct=rj('tests/runtime-catchup.json'); cases={x['id']:x for x in ct.get('cases',[]) if isinstance(x,dict) and 'id' in x}
wk=cases.get('weekly_multi_month',{})
if wk:
    due=wk['first_due_seconds']; target=wk['target_seconds']; step=wk['recurrence']['interval_seconds']; c=0; last=None
    while due<=target: c+=1; last=due; due+=step
    if c!=wk['expected_full_boundaries'] or last!=wk['expected_last_boundary_seconds'] or target-last!=wk['expected_residual_seconds']: err('weekly_catchup_vector_failed')
mo=cases.get('monthly_six_boundaries_plus_partial',{})
if mo and len(mo.get('expected_boundaries',[]))!=6: err('monthly_catchup_vector_failed')
for req in ['successor_continues','hard_interrupt_stops_early','new_process_catches_up']:
    if not cases.get(req,{}).get('required'): err(f'catchup_semantic_case_missing:{req}')

# autonomous offscreen actors contract
aw=rj('runtime/contracts/autonomous-world-simulation.json')
for k in ('action_kinds','selection_rule','materialization_rule','storage_rule','operation_lifecycle','large_conflict_efficiency_rule','npc_mission_rule','interaction_rule','combat_rule','territory_rule','successor_rule','information_rule'):
    if not aw.get(k): err(f'autonomous_contract_missing:{k}')
acts=' '.join(str(x).lower() for x in aw.get('action_kinds',[]))
for need in ('mission','battle','raid','occupation','diplomacy'):
    if need not in acts: err('autonomous_action_family_missing:'+need)
if 'Out-of-character' not in str(aw.get('player_intent_boundary','')): err('player_intent_boundary_missing')
st=aw.get('storage_targets',{})
if not isinstance(st,dict) or not st: err('autonomous_storage_targets_missing')
for name,rel in (st.items() if isinstance(st,dict) else []):
    if any(str(rel).startswith(prefix) for prefix in retired): err('autonomous_storage_target_retired:'+str(name)+':'+str(rel))
    if not (ROOT/rel).exists(): err('autonomous_storage_target_missing:'+str(name)+':'+str(rel))

if errs:
    print('RUNTIME TEST FAILED')
    for x in errs: print('-',x)
    sys.exit(1)
print('RUNTIME TEST OK')
print(f'causal_hosts={len(hosts)} queued_events={len(events)} routes={len(all_routes(m))}')
