#!/usr/bin/env python3
import json,pathlib,sys
ROOT=pathlib.Path(__file__).resolve().parents[1]
def load(rel):return json.loads((ROOT/rel).read_text(encoding='utf-8'))
def fail(x):print('LIVING WORLD TEST FAILED');print('-',x);sys.exit(1)
def load_factions():
    registry=load('state/reg/factions.json'); records=[]
    inline=registry.get('factions')
    if isinstance(inline,list): records.extend(inline)
    elif isinstance(inline,dict): records.extend(inline.values())
    for faction_id,rel in registry.get('record_index',{}).items():
        record=load(rel).get('faction')
        if not isinstance(record,dict):fail('bad_faction_record:'+str(faction_id)+':'+str(rel))
        if record.get('id')!=faction_id:fail('faction_index_id_mismatch:'+str(faction_id)+':'+str(record.get('id')))
        records.append(record)
    if not records:fail('no_factions_examined')
    return records
ma=load('game/data/content/mission-archetypes.json').get('archetypes',[]); wa=load('game/data/content/world-event-archetypes.json').get('archetypes',[])
if len(ma)<20:fail('mission_archetype_count')
if len(wa)<12:fail('world_event_archetype_count')
for m in ma:
    if any(k in m for k in ('assigned_to','assigned_member_id','assigned_team_id')):fail('preassigned_archetype:'+str(m.get('id')))
active=load('state/reg/missions-contracts-projects.json')
if 'mission_templates' in active or 'mission_assignment_rules' in active:fail('mission_design_bloat_in_state')
world_events=load('state/reg/world-events.json').get('events')
if not isinstance(world_events,list):fail('world_events_not_list')
seen=set()
for pos,event in enumerate(world_events):
    if not isinstance(event,dict): continue
    eid=event.get('id')
    if eid:
        if eid in seen:fail('duplicate_world_event_id:'+eid)
        seen.add(eid)
factions=load_factions()
for f in factions:
    if not f.get('goals') or not f.get('resources') or not f.get('constraints'):fail('thin_faction:'+f.get('id',''))
support_paths=sorted((ROOT/'state/person/world').glob('*.json'))
if not support_paths:fail('no_support_people_examined')
for p in support_paths:
    d=json.loads(p.read_text(encoding='utf-8'))
    if not d.get('history',{}).get('service') or len(d.get('relationships',[]))<2:fail('thin_support:'+p.name)
r=load('state/place/root-primary-complex.json')
site=load('game/data/content/strategic-site-definitions.json').get('records',{}).get('place.konoha.root.primary_complex',{})
if len(site.get('zones',[]))<8 or len(site.get('connections',[]))<5:fail('root_facility_topology')
world_places=load('state/world/routes-and-settlements.json').get('payload',{}).get('places',[])
root_world=next((x for x in world_places if isinstance(x,dict) and x.get('id')=='place.konoha.root.primary_complex'),None)
if not root_world:fail('root_facility_world_registry')
modules=root_world.get('mechanical_modules') or {}
if not {'training','medical','custody'}.issubset(modules):fail('root_facility_mechanical_modules')
if any(k in r for k in ('zones','security_elements','player_knowledge')):fail('root_facility_duplicate_authority')
if not isinstance(r.get('visibility'),str):fail('root_visibility_state')
scene=load('state/scene.json')
if any(k in scene for k in ('decision_packages','action_packages','next_action')):fail('cached_choices')
# Causal scheduler replaces the old process frontier.
scheduler=load('state/time/causal-scheduler.json'); hosts=scheduler.get('hosts',{}); events=scheduler.get('events',[])
if not isinstance(hosts,dict) or not hosts:fail('no_causal_hosts_examined')
if len(hosts)>256:fail('causal_host_bloat')
if any((ROOT/x).exists() for x in ('state/time/frontier.json','state/time/coverage')):fail('legacy_scheduler_present')
metrics=scheduler.get('metrics',{})
for key in ('global_person_scans','named_persons_scanned_per_advance','global_faction_directory_scans','faction_directory_scans_per_advance'):
    if metrics.get(key,0)!=0:fail('global_scheduler_scan:'+key)
pol=load('game/data/mechanics/medical.json').get('ocular_transfer_policy',{})
for k in ('extraction','proper_preservation','implantation'):
    if '100_percent' not in str(pol.get(k,'')):fail('ocular_transfer:'+k)
world_sim=(ROOT/'plugins/shinobi-rpg/skills/shinobi-game-master/references/world-simulation.md').read_text(encoding='utf-8').lower()
for phrase in ('offscreen does not mean frozen','aggregate/cohort/host systems','static canon/reference content as possibility','reputation among informed audiences'):
    if phrase not in world_sim:fail('world_simulation_contract:'+phrase)
print('LIVING WORLD TESTS OK')
print(f'examined mission_archetypes={len(ma)} world_event_archetypes={len(wa)} world_events={len(world_events)} factions={len(factions)} support_people={len(support_paths)} causal_hosts={len(hosts)} queued_events={len(events)}')
