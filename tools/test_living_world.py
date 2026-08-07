#!/usr/bin/env python3
import json,pathlib,sys
ROOT=pathlib.Path(__file__).resolve().parents[1]
def load(rel):return json.loads((ROOT/rel).read_text(encoding='utf-8'))
def fail(x):print('LIVING WORLD TEST FAILED');print('-',x);sys.exit(1)
# Cold archetypes, no preassignment or active-state template bloat.
ma=load('data/content/mission-archetypes.json').get('archetypes',[])
wa=load('data/content/world-event-archetypes.json').get('archetypes',[])
if len(ma)<20:fail('mission_archetype_count')
if len(wa)<12:fail('world_event_archetype_count')
for m in ma:
    if any(k in m for k in ('assigned_to','assigned_member_id','assigned_team_id')):fail('preassigned_archetype:'+str(m.get('id')))
active=load('state/reg/missions-contracts-projects.json')
if 'mission_templates' in active or 'mission_assignment_rules' in active:fail('mission_design_bloat_in_state')
if load('state/reg/world-events.json').get('events'):fail('dormant_event_templates_in_state')
# Factions and people have actual opening plans/depth.
for f in load('state/reg/factions.json').get('factions',[]):
    if not f.get('goals') or not f.get('resources') or not f.get('constraints'):fail('thin_faction:'+f.get('id',''))
    if 'pursue goal within current resources' in f.get('current_plan',''):fail('generic_faction_plan:'+f.get('id',''))
for p in (ROOT/'state/person/world').glob('*.json'):
    d=json.loads(p.read_text(encoding='utf-8'))
    if not d.get('history',{}).get('service') or len(d.get('relationships',[]))<2:fail('thin_support:'+p.name)
# Root facility preexists and hidden knowledge remains separate.
r=load('state/place/root-primary-complex.json')
if len(r.get('zones',[]))<8 or len(r.get('security_elements',[]))<5:fail('root_facility')
if not isinstance(r.get('player_knowledge',{}).get('facility_known'),bool):fail('root_knowledge_state')
# Current scene/narrator context.
scene=load('state/scene.json')
if any(k in scene for k in ('decision_packages','action_packages','next_action')):fail('cached_choices')
# Temporal efficiency.
front=load('state/time/frontier.json')
ids={p.get('id') for p in front.get('processes',[])}
if len(front.get('processes',[]))>16:fail('frontier_bloat')
# Dōjutsu transfer policy remains simple and deterministic.
pol=load('data/mechanics/medical.json').get('ocular_transfer_policy',{})
for k in ('extraction','proper_preservation','implantation'):
    if '100_percent' not in str(pol.get(k,'')):fail('ocular_transfer:'+k)
# Voice persona and choice timing.
voice=(ROOT/'VOICE.md').read_text(encoding='utf-8')
for phrase in ('Repository memory is not player memory','estimated in-world','medium','long'):
    if phrase not in voice:fail('voice:'+phrase)
print('LIVING WORLD TESTS OK')
