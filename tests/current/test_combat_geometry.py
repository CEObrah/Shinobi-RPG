import copy, json
from pathlib import Path
import pytest

from shinobi_runtime.combat.geometry import trace_attack_geometry,line_of_sight_clear,open_retreat_corridors,surrounding_state
from shinobi_runtime.martial_world.exact_combat import initialize_combat,resolve_exchange
from shinobi_runtime.martial_world.equipment_state import effective_person_loadout
from shinobi_runtime.martial_world.health import wound_from_contact

ROOT=Path(__file__).resolve().parents[2]
def load(rel): return json.loads((ROOT/rel).read_text())


def _pos(x,y,z=0): return {'x_mm':x,'y_mm':y,'elevation_mm':z,'facing_mdeg':0,'body_radius_mm':300,'zone_ref':'test'}


def test_lane_contact_is_geometry_not_team_membership_and_friendlies_can_intercept():
    positions={'a':_pos(0,0),'friend':_pos(3000,0),'target':_pos(6000,0),'spread_enemy':_pos(6000,6000)}
    trace=trace_attack_geometry(positions,actor_ref='a',aim_ref='target',body_refs=list(positions),geometry={'shape':'direct','width_m':0.5,'length_m':10},obstacles=[],target_limit=10,maximum_range_m=10,channel='projectile',trajectory={'launch_x_mm':0,'launch_y_mm':0,'launch_elevation_mm':0,'aim_x_mm':6000,'aim_y_mm':0,'aim_elevation_mm':0})
    refs=[r['participant_ref'] for r in trace['contacts']]
    assert refs[0]=='friend'
    assert 'target' in refs
    assert 'spread_enemy' not in refs


def test_projectile_trajectory_does_not_curve_to_moved_target():
    positions={'a':_pos(0,0),'target':_pos(6000,4000)}
    frozen={'launch_x_mm':0,'launch_y_mm':0,'launch_elevation_mm':0,'aim_x_mm':6000,'aim_y_mm':0,'aim_elevation_mm':0}
    trace=trace_attack_geometry(positions,actor_ref='a',aim_ref='target',body_refs=list(positions),geometry={'shape':'direct','width_m':0.2,'length_m':10},obstacles=[],target_limit=1,maximum_range_m=10,channel='projectile',trajectory=frozen)
    assert not trace['contacts']


def test_elevation_is_part_of_projectile_lane():
    positions={'a':_pos(0,0,4000),'below':_pos(4000,0,0),'level':_pos(4000,0,4000)}
    trajectory={'launch_x_mm':0,'launch_y_mm':0,'launch_elevation_mm':4000,'aim_x_mm':8000,'aim_y_mm':0,'aim_elevation_mm':4000}
    trace=trace_attack_geometry(positions,actor_ref='a',aim_ref='level',body_refs=list(positions),geometry={'shape':'direct','width_m':0.2,'length_m':10},obstacles=[],target_limit=10,maximum_range_m=10,channel='projectile',trajectory=trajectory)
    refs=[r['participant_ref'] for r in trace['contacts']]
    assert 'level' in refs and 'below' not in refs


def test_obstacle_blocks_los_and_retreat_requires_open_corridor():
    positions={'a':_pos(0,0),'b':_pos(5000,0)}
    wall={'obstacle_ref':'wall','shape':'rectangle','min_x_mm':2000,'max_x_mm':3000,'min_y_mm':-1000,'max_y_mm':1000,'height_mm':3000,'blocks_los':True,'blocks_movement':True}
    assert not line_of_sight_clear(positions,actor_ref='a',target_ref='b',obstacles=[wall])
    corridors=open_retreat_corridors(positions,actor_ref='a',body_refs=['a','b'],obstacles=[wall])
    assert corridors


def test_invalid_targeting_intent_is_rejected():
    roster=load('state/martial-world/people/house_tang.json')['people']
    people={roster[0]['person_id']:copy.deepcopy(roster[0]),roster[3]['person_id']:copy.deepcopy(roster[3])}
    combat=initialize_combat(combat_ref='t',side_a_refs=[roster[0]['person_id']],side_b_refs=[roster[3]['person_id']],people=people,zone_ref='site.house_tang',started_at='x',objective={'kind':'eliminate','target_refs':[roster[3]['person_id']]})
    ledger=load('state/martial-world/equipment-ledger.json')
    with pytest.raises(ValueError,match='targeting intent'):
        resolve_exchange(combat=combat,people=people,equipment_ledger=ledger,doctrines={},player_ref=roster[0]['person_id'],player_action_kind='thrust',player_target_ref=roster[3]['person_id'],player_weapon_ref='weapon_jian',player_hit_zone='auto',player_targeting_intent='whatever')


def test_bilateral_blindness_blocks_visually_aimed_bow():
    roster=load('state/martial-world/people/house_tang.json')['people']
    archer=copy.deepcopy(roster[0]); target=copy.deepcopy(roster[3])
    archer['health']={'status':'ready','injuries':[
        wound_from_contact(structure_ref='left_eye',cut=0,pierce=180,blunt=0,penetration=180,created_at='x'),
        wound_from_contact(structure_ref='right_eye',cut=0,pierce=180,blunt=0,penetration=180,created_at='x'),
    ]}
    people={archer['person_id']:archer,target['person_id']:target}
    ledger=load('state/martial-world/equipment-ledger.json')
    ledger.setdefault('person_loadouts', {})[archer['person_id']]={'items':{'weapon_bow':1,'item_arrow':6}}
    combat=initialize_combat(combat_ref='t',side_a_refs=[archer['person_id']],side_b_refs=[target['person_id']],people=people,zone_ref='site.house_tang',started_at='x',objective={'kind':'eliminate','target_refs':[target['person_id']]},initial_range_band=2)
    result=resolve_exchange(combat=combat,people=people,equipment_ledger=ledger,doctrines={},player_ref=archer['person_id'],player_action_kind='bow_shot',player_target_ref=target['person_id'],player_weapon_ref='weapon_bow',player_hit_zone='chest',player_targeting_intent='disable')
    player_event=next(e for e in result['events'] if e['actor_ref']==archer['person_id'])
    assert player_event['result']=='visual_targeting_unavailable'


def test_committed_projectile_miss_consumes_arrow_and_advances_combat_clock(monkeypatch):
    roster=load('state/martial-world/people/house_tang.json')['people']
    archer=copy.deepcopy(roster[0]); target=copy.deepcopy(roster[3])
    people={archer['person_id']:archer,target['person_id']:target}
    ledger=load('state/martial-world/equipment-ledger.json')
    ledger.setdefault('person_loadouts', {})[archer['person_id']]={'items':{'weapon_bow':1,'item_arrow':6}}
    import shinobi_runtime.martial_world.exact_combat as exact
    monkeypatch.setattr(exact,'trace_attack_geometry',lambda *args,**kwargs:{'contacts':[],'blocked_by':None})
    combat=initialize_combat(combat_ref='miss-clock',side_a_refs=[archer['person_id']],side_b_refs=[target['person_id']],people=people,zone_ref='site.house_tang',started_at='x',objective={'kind':'eliminate','target_refs':[target['person_id']]},initial_range_band=2)
    before=int(effective_person_loadout(ledger,archer['person_id'])['items']['item_arrow'])
    result=resolve_exchange(combat=combat,people=people,equipment_ledger=ledger,doctrines={},player_ref=archer['person_id'],player_action_kind='bow_shot',player_target_ref=target['person_id'],player_weapon_ref='weapon_bow',player_hit_zone='chest',player_targeting_intent='disable')
    event=next(e for e in result['events'] if e['actor_ref']==archer['person_id'])
    assert event['result']=='miss_no_spatial_intersection'
    assert result['combat_after']['elapsed_ms']>0
    assert int(effective_person_loadout(result['equipment_ledger_after'],archer['person_id'])['items']['item_arrow'])==before-1


def test_defended_or_missed_melee_exchange_advances_combat_clock(monkeypatch):
    roster=load('state/martial-world/people/house_tang.json')['people']
    attacker=copy.deepcopy(roster[3]); defender=copy.deepcopy(roster[0])
    attacker['attributes']={k:1 for k in attacker['attributes']}
    attacker['martial_skills']={k:1 for k in attacker['martial_skills']}
    defender['attributes']={k:500 for k in defender['attributes']}
    defender['martial_skills']={k:500 for k in defender['martial_skills']}
    people={attacker['person_id']:attacker,defender['person_id']:defender}
    combat=initialize_combat(combat_ref='defense-clock',side_a_refs=[attacker['person_id']],side_b_refs=[defender['person_id']],people=people,zone_ref='site.house_tang',started_at='x',objective={'kind':'eliminate','target_refs':[defender['person_id']]})
    import shinobi_runtime.martial_world.exact_combat as exact
    original_observe=exact._observe_visible_enemies
    monkeypatch.setattr(exact,'_observe_visible_enemies',lambda combat,actor_ref,enemy_refs,people,at_ms: [] if actor_ref==defender['person_id'] else original_observe(combat,actor_ref=actor_ref,enemy_refs=enemy_refs,people=people,at_ms=at_ms))
    result=resolve_exchange(combat=combat,people=people,equipment_ledger=load('state/martial-world/equipment-ledger.json'),doctrines={},player_ref=attacker['person_id'],player_action_kind='thrust',player_target_ref=defender['person_id'],player_weapon_ref='weapon_jian',player_hit_zone='chest',player_targeting_intent='disable')
    event=next(e for e in result['events'] if e['actor_ref']==attacker['person_id'])
    assert event['result']=='defended_or_missed'
    assert result['combat_after']['elapsed_ms']>0


def test_idle_exchange_without_observed_targets_still_advances_clock(monkeypatch):
    roster=load('state/martial-world/people/house_tang.json')['people']
    attacker=copy.deepcopy(roster[0]); defender=copy.deepcopy(roster[3])
    people={attacker['person_id']:attacker,defender['person_id']:defender}
    import shinobi_runtime.martial_world.exact_combat as exact
    monkeypatch.setattr(exact,'_observe_visible_enemies',lambda *args,**kwargs:[])
    combat=initialize_combat(combat_ref='idle-clock',side_a_refs=[attacker['person_id']],side_b_refs=[defender['person_id']],people=people,zone_ref='site.house_tang',started_at='x',objective={'kind':'eliminate','target_refs':[defender['person_id']]})
    before=int(combat['elapsed_ms'])
    result=resolve_exchange(combat=combat,people=people,equipment_ledger=load('state/martial-world/equipment-ledger.json'),doctrines={},player_ref=attacker['person_id'],player_action_kind='unarmed_strike',player_target_ref=defender['person_id'],player_weapon_ref='body_unarmed',player_hit_zone='chest',player_targeting_intent='disable')
    assert result['combat_after']['elapsed_ms']>=before+250
    assert all(event['result']=='no_lawfully_known_target' for event in result['events'])


def test_melee_schedule_persists_original_committed_approach_envelope():
    import shinobi_runtime.martial_world.exact_combat as exact
    roster=load('state/martial-world/people/house_tang.json')['people']
    attacker=copy.deepcopy(roster[0]); defender=copy.deepcopy(roster[3])
    people={attacker['person_id']:attacker,defender['person_id']:defender}
    ledger=load('state/martial-world/equipment-ledger.json')
    combat=initialize_combat(combat_ref='approach-envelope',side_a_refs=[attacker['person_id']],side_b_refs=[defender['person_id']],people=people,zone_ref='site.house_tang',started_at='x',objective={'kind':'eliminate','target_refs':[defender['person_id']]})
    combat['positions'][attacker['person_id']].update(x_mm=0,y_mm=0)
    combat['positions'][defender['person_id']].update(x_mm=5000,y_mm=0)
    action=exact._schedule_action(combat=combat,actor_ref=attacker['person_id'],target_ref=defender['person_id'],action_kind='unarmed_strike',weapon_ref='body_unarmed',poison_ref=None,hit_zone='chest',target_structure_ref=None,decision_origin='test',people=people,equipment_ledger=ledger)
    params=action.profile.effect_parameters
    assert int(params['approach_distance_mm'])>0
    assert int(params['approach_time_ms'])>0
    assert params['intended_target_ref']==defender['person_id']
    # If an earlier simultaneous action moves the defender farther away, this
    # attack may fail to close. It may not silently expand the committed run-up.
    from shinobi_runtime.combat.physical_defense import close_attacker_into_reach
    moved_target=copy.deepcopy(combat['positions'][defender['person_id']]); moved_target['x_mm']+=2500
    moved,trace=close_attacker_into_reach(attacker_ref=attacker['person_id'],defender_ref=defender['person_id'],positions={**combat['positions'],defender['person_id']:moved_target},attacker_position=exact._pos(combat['positions'][attacker['person_id']]),defender_position=exact._pos(moved_target),attacker_capability=exact._combat_capability(attacker['person_id'],attacker,ledger,action_skill='unarmed'),profile=action.profile,body_refs=[attacker['person_id'],defender['person_id']],obstacles=[])
    assert trace['moved'] is False
    assert trace['reason']=='target_moved_beyond_committed_approach'
    assert moved.x_mm==combat['positions'][attacker['person_id']]['x_mm']


def test_three_attackers_share_one_reaction_budget_in_exact_exchange(monkeypatch):
    base=load('state/martial-world/people/house_tang.json')['people'][0]
    defender=copy.deepcopy(base); defender['person_id']='defender'
    defender['attributes']={k:300 for k in defender['attributes']}; defender['martial_skills']={k:300 for k in defender['martial_skills']}
    attackers=[]
    for idx in range(3):
        row=copy.deepcopy(base); row['person_id']=f'attacker.{idx}'; row['attributes']={k:5 for k in row['attributes']}; row['martial_skills']={k:5 for k in row['martial_skills']}; attackers.append(row)
    people={row['person_id']:row for row in attackers+[defender]}
    ledger={'schema':'jianghu-equipment-ledger-1.0','policy_assignments':{},'person_loadouts':{}}
    combat=initialize_combat(combat_ref='reaction-saturation',side_a_refs=[row['person_id'] for row in attackers],side_b_refs=[defender['person_id']],people=people,zone_ref='site.house_tang',started_at='x',objective={'kind':'eliminate','target_refs':[defender['person_id']]},equipment_ledger=ledger)
    # Put all three attackers at equal contact distance but different angles.
    combat['positions']['defender'].update(x_mm=0,y_mm=0,facing_mdeg=0)
    combat['positions']['attacker.0'].update(x_mm=900,y_mm=0)
    combat['positions']['attacker.1'].update(x_mm=0,y_mm=900)
    combat['positions']['attacker.2'].update(x_mm=-900,y_mm=0)
    import shinobi_runtime.martial_world.exact_combat as exact
    original_observe=exact._observe_visible_enemies
    monkeypatch.setattr(exact,'_observe_visible_enemies',lambda combat,actor_ref,enemy_refs,people,at_ms: [] if actor_ref=='defender' else original_observe(combat,actor_ref=actor_ref,enemy_refs=enemy_refs,people=people,at_ms=at_ms))
    result=resolve_exchange(combat=combat,people=people,equipment_ledger=ledger,doctrines={},player_ref='attacker.0',player_action_kind='unarmed_strike',player_target_ref='defender',player_weapon_ref='body_unarmed',player_hit_zone='chest',player_targeting_intent='disable')
    pressures=[event['defense_pressure'] for event in result['events'] if event.get('intended_ref')=='defender' and 'defense_pressure' in event]
    assert len(pressures)==3
    distinct=[int(row['distinct_attackers']) for row in pressures]
    available=[int(row['available_milli']) for row in pressures]
    assert distinct==sorted(distinct) and distinct[-1]>=3
    assert min(available[1:])<available[0]
    traces=[event['defense'] for event in result['events'] if event.get('intended_ref')=='defender' and 'defense' in event]
    assert min(int(row['reaction_availability_milli']) for row in traces[1:])<int(traces[0]['reaction_availability_milli'])


def test_three_vs_three_teammates_create_real_defensive_interruptions(monkeypatch):
    import shinobi_runtime.martial_world.exact_combat as exact
    base=load('state/martial-world/people/house_tang.json')['people'][0]
    people={}
    for side,prefix in (("a","a"),("b","b")):
        for idx in range(3):
            row=copy.deepcopy(base); row['person_id']=f'{prefix}{idx}'
            # Side A wingmen are faster so their pressure lands while B1/B2 are
            # still committing attacks into A0. This makes the tradeoff visible.
            level=260 if prefix=='a' and idx in {1,2} else 90 if prefix=='b' else 150
            row['attributes']={k:level for k in row['attributes']}
            row['martial_skills']={k:level for k in row['martial_skills']}
            people[row['person_id']]=row
    ledger={'schema':'jianghu-equipment-ledger-1.0','policy_assignments':{},'person_loadouts':{}}
    combat=initialize_combat(combat_ref='team-pressure',side_a_refs=['a0','a1','a2'],side_b_refs=['b0','b1','b2'],people=people,zone_ref='site.house_tang',started_at='x',objective={'kind':'eliminate','target_refs':['b0','b1','b2']},equipment_ledger=ledger)
    # Keep everyone close enough that timing is dominated by body/reaction speed.
    combat['positions']['a0'].update(x_mm=0,y_mm=0); combat['positions']['a1'].update(x_mm=0,y_mm=800); combat['positions']['a2'].update(x_mm=0,y_mm=-800)
    combat['positions']['b0'].update(x_mm=900,y_mm=0); combat['positions']['b1'].update(x_mm=900,y_mm=800); combat['positions']['b2'].update(x_mm=900,y_mm=-800)

    def forced_plan(combat, *, side, people, doctrine):
        if side=='side_a':
            assignments={'a0':{'target_ref':'b0','role':'pressure'},'a1':{'target_ref':'b1','role':'pressure'},'a2':{'target_ref':'b2','role':'pressure'}}
        else:
            assignments={ref:{'target_ref':'a0','role':'pressure'} for ref in ('b0','b1','b2')}
        combat.setdefault('team_plans',{})[side]={'assignments':assignments}
        return combat['team_plans'][side]
    monkeypatch.setattr(exact,'_refresh_team_plan',forced_plan)
    result=resolve_exchange(combat=combat,people=people,equipment_ledger=ledger,doctrines={},player_ref='a0',player_action_kind='unarmed_strike',player_target_ref='b0',player_weapon_ref='body_unarmed',player_hit_zone='chest',player_targeting_intent='disable')
    by_actor={event.get('actor_ref'):event for event in result['events'] if event.get('actor_ref') in people}
    scheduled_events=[event for event in result['events'] if event.get('actor_ref') in people and 'declared_at_ms' in event]
    assert len(scheduled_events)==6
    assert {int(event['declared_at_ms']) for event in scheduled_events}=={0}
    # All six actors receive a concurrent declaration opportunity. The two B
    # wingmen are under real teammate pressure rather than attacking A0 in a void.
    assert {'a0','a1','a2','b0','b1','b2'} <= set(by_actor)
    interrupted=[by_actor[ref] for ref in ('b1','b2') if str(by_actor[ref].get('result','')).startswith('action_interrupted_by_defense') or str(by_actor[ref].get('result','')).startswith('action_disrupted_by_defense')]
    assert interrupted, {ref:by_actor[ref].get('result') for ref in ('b0','b1','b2')}
    assert all(event.get('defensive_attacker_ref') in {'a1','a2'} for event in interrupted)
    assert '_pending_actions' not in result['combat_after'] and '_defense_interruptions' not in result['combat_after']


def test_recovery_commitments_remain_on_shared_clock_between_exchanges(monkeypatch):
    import shinobi_runtime.martial_world.exact_combat as exact
    base=load('state/martial-world/people/house_tang.json')['people'][0]
    people={}
    for side in ('a','b'):
        for idx in range(2):
            row=copy.deepcopy(base); row['person_id']=f'{side}{idx}'
            row['attributes']={k:150 for k in row['attributes']}
            row['martial_skills']={k:150 for k in row['martial_skills']}
            row['health']={'status':'ready','consciousness':100,'injuries':[]}
            people[row['person_id']]=row
    ledger={'schema':'jianghu-equipment-ledger-1.0','policy_assignments':{},'person_loadouts':{
        'a0':{'items':{'weapon_glaive':1}},'a1':{'items':{'weapon_dagger':1}},
        'b0':{'items':{'weapon_dagger':1}},'b1':{'items':{'weapon_dagger':1}},
    }}
    combat=initialize_combat(combat_ref='shared-recovery-clock',side_a_refs=['a0','a1'],side_b_refs=['b0','b1'],people=people,zone_ref='site.house_tang',started_at='x',objective={'kind':'eliminate','target_refs':['b0','b1']},equipment_ledger=ledger)
    combat['positions']['a0'].update(x_mm=0,y_mm=0); combat['positions']['a1'].update(x_mm=0,y_mm=1000)
    combat['positions']['b0'].update(x_mm=900,y_mm=0); combat['positions']['b1'].update(x_mm=900,y_mm=1000)

    def forced_plan(combat, *, side, people, doctrine):
        assignments=(
            {'a0':{'target_ref':'b0','role':'pressure'},'a1':{'target_ref':'b1','role':'pressure'}}
            if side=='side_a' else
            {'b0':{'target_ref':'a0','role':'pressure'},'b1':{'target_ref':'a1','role':'pressure'}}
        )
        combat.setdefault('team_plans',{})[side]={'assignments':assignments}
        return combat['team_plans'][side]
    monkeypatch.setattr(exact,'_refresh_team_plan',forced_plan)

    first=resolve_exchange(combat=combat,people=people,equipment_ledger=ledger,doctrines={},player_ref='a0',player_action_kind='cut',player_target_ref='b0',player_weapon_ref='weapon_glaive',player_hit_zone='chest',player_targeting_intent='disable')
    first_clock=int(first['combat_after']['elapsed_ms'])
    recovery_until={ref:int(state.get('recovery_until_ms',0)) for ref,state in first['combat_after']['combatants'].items()}
    assert any(until>first_clock for until in recovery_until.values())
    # The exchange stops at the last physical contact, not after waiting out the
    # slowest recovery. Those recoveries therefore remain real future clock locks.
    assert first_clock==max(int(event['contact_at_ms']) for event in first['events'] if 'contact_at_ms' in event)
    assert first['combat_after']['status']=='active'

    second=resolve_exchange(combat=first['combat_after'],people=first['people_after'],equipment_ledger=first['equipment_ledger_after'],doctrines={},player_ref='a0',player_action_kind='cut',player_target_ref='b0',player_weapon_ref='weapon_glaive',player_hit_zone='chest',player_targeting_intent='disable')
    second_events={event['actor_ref']:event for event in second['events'] if event.get('actor_ref') in people and 'start_at_ms' in event}
    assert {'a0','a1','b0','b1'} <= set(second_events)
    for ref,event in second_events.items():
        assert int(event['declared_at_ms'])==first_clock
        assert int(event['start_at_ms'])>=recovery_until[ref]
    # The dagger wingman recovers and recommits before the glaive user. Faster
    # recovery is therefore a real timing advantage, not erased by round order.
    assert int(second_events['a1']['start_at_ms']) < int(second_events['a0']['start_at_ms'])



def test_failed_projectile_interception_does_not_reduce_impact_by_invisible_block(monkeypatch):
    from shinobi_runtime.combat.models import PositionState
    from shinobi_runtime.combat.physical_defense import PhysicalDefenseDecision
    import shinobi_runtime.martial_world.exact_combat as exact
    roster=load('state/martial-world/people/house_tang.json')['people']
    archer=copy.deepcopy(roster[0]); defender=copy.deepcopy(roster[3])
    people={archer['person_id']:archer,defender['person_id']:defender}
    ledger=load('state/martial-world/equipment-ledger.json')
    ledger.setdefault('person_loadouts',{})[archer['person_id']]={'items':{'weapon_bow':1,'item_arrow':6}}
    ledger.setdefault('person_loadouts',{})[defender['person_id']]={'items':{}}
    combat=initialize_combat(combat_ref='failed-projectile-block',side_a_refs=[archer['person_id']],side_b_refs=[defender['person_id']],people=people,zone_ref='site.house_tang',started_at='x',objective={'kind':'eliminate','target_refs':[defender['person_id']]},initial_range_band=1,equipment_ledger=ledger)
    def fake_defense(**kwargs):
        pos=kwargs['defender_position']
        return PhysicalDefenseDecision(detected=True,detection_margin=500,response='block',before_position=pos,after_position=pos,displacement_mm=0,reaction_delay_ms=40,recovery_ms=200,defense_factor_milli=100,reaction_availability_milli=1000,balance_after_milli=900,limb_commitment_after_milli=500,weapon_position_after='committed_guard',attack_angle_mdeg=0,tracking_milli=1000,force_transmission_milli=100,control_disruption=0,displacement_resistance_milli=700,interrupts_attacker=False,contact_surface='body_guard',reason='test')
    monkeypatch.setattr(exact,'select_physical_defense',fake_defense)
    monkeypatch.setattr(exact,'_projectile_interception',lambda **kwargs:{'outcome':'failed','trajectory':dict(kwargs['trajectory']),'speed_factor_milli':1000})
    result=resolve_exchange(combat=combat,people=people,equipment_ledger=ledger,doctrines={},player_ref=archer['person_id'],player_action_kind='bow_shot',player_target_ref=defender['person_id'],player_weapon_ref='weapon_bow',player_hit_zone='chest',player_targeting_intent='disable')
    event=next(e for e in result['events'] if e['actor_ref']==archer['person_id'])
    assert event['interception']['outcome']=='failed'
    if event['result'] in {'contact','physical_contact_no_wound'}:
        channels=event['damage']['transmitted_channels']; profile=event['damage']['weapon_profile']
        assert channels['pierce']==int(profile.get('pierce_score',0))
        assert channels['penetration']==int(profile.get('penetration_score',0))


def test_all_physically_suitable_ready_weapons_can_intercept_projectiles():
    from types import SimpleNamespace
    from shinobi_runtime.combat.models import ActionProfile,CapabilityProfile
    import shinobi_runtime.martial_world.exact_combat as exact
    suitable={
        'weapon_jian':'sword','weapon_dao':'sword','weapon_long_dao':'sword','weapon_short_sword':'sword',
        'weapon_dagger':'sword','weapon_spear':'spear','weapon_short_spear':'spear','weapon_staff':'spear',
        'weapon_glaive':'spear','weapon_bow':'bow',
    }
    cap=CapabilityProfile(offense=500,defense=500,control=500,mobility=500,perception=500,stealth=0,capture=100,escape=500,reaction=500)
    profile=ActionProfile(method_ref='bow_shot',effect_kind='physical',delivery='projectile',startup_ms=100,external_contact=True,speed_score=200,effect_parameters={'projectile_interception_difficulty_milli':900})
    decision=SimpleNamespace(detected=True,response='deflect',detection_margin=500,reaction_availability_milli=1000)
    trajectory={'launch_x_mm':0,'launch_y_mm':0,'launch_elevation_mm':0,'aim_x_mm':5000,'aim_y_mm':0,'aim_elevation_mm':0}
    for weapon_ref,discipline in suitable.items():
        defender={'person_id':'d','attributes':{'strength':100,'speed':100,'dexterity':100,'endurance':100,'perception':100,'intelligence':100,'willpower':100},'martial_skills':{'sword':500,'spear':500,'bow':500,'unarmed':500,'hidden_weapons':500},'health':{'status':'healthy','injuries':[]}}
        ledger={'schema':'jianghu-equipment-ledger-1.0','policy_assignments':{},'person_loadouts':{'d':{'items':{weapon_ref:1}}}}
        state={'ready_weapon_ref':weapon_ref}
        result=exact._projectile_interception(defender_ref='d',defender=defender,defender_state=state,defender_capability=cap,equipment_ledger=ledger,decision=decision,profile=profile,trajectory=trajectory,combat_id='x',attacker_ref='a',at_ms=100)
        assert result['outcome'] in {'clean','partial'}, (weapon_ref,discipline,result)
        assert result['weapon_ref']==weapon_ref


def test_combat_microclock_is_chunk_invariant_for_guard_and_bleeding():
    import shinobi_runtime.martial_world.exact_combat as exact
    base=load('state/martial-world/people/house_tang.json')['people'][0]
    attacker=copy.deepcopy(base); defender=copy.deepcopy(base)
    attacker['person_id']='micro.a'; defender['person_id']='micro.b'
    attacker['health']={
        'status':'injured','consciousness':100,'blood_lost_ml':0,
        'injuries':[{'zone':'wrist','severity':29,'bleeding_ml_per_min':5,'organ_trauma':0,'pain':20,'healing_progress_milli':0}],
    }
    defender['health']={'status':'ready','consciousness':100,'injuries':[]}
    people={'micro.a':attacker,'micro.b':defender}
    ledger={'schema':'jianghu-equipment-ledger-1.0','policy_assignments':{},'person_loadouts':{'micro.a':{'items':{}},'micro.b':{'items':{}}}}
    combat=initialize_combat(
        combat_ref='microclock-chunk-invariant',side_a_refs=['micro.a'],side_b_refs=['micro.b'],people=people,
        zone_ref='site.house_tang',started_at='0061-09-12T00:00:00',objective={'kind':'eliminate','target_refs':['micro.b']},equipment_ledger=ledger,
    )
    whole_combat=copy.deepcopy(combat); whole_people=copy.deepcopy(people)
    exact._settle_combat_physiology_until(whole_combat,whole_people,target_ms=2000,equipment_ledger=ledger)
    chunk_combat=copy.deepcopy(combat); chunk_people=copy.deepcopy(people)
    for at_ms in (250,500,750,1000,1250,1500,1750,2000):
        exact._settle_combat_physiology_until(chunk_combat,chunk_people,target_ms=at_ms,equipment_ledger=ledger)
    assert whole_people['micro.a']['health']==chunk_people['micro.a']['health']
    assert whole_people['micro.a'].get('fatigue_milli')==chunk_people['micro.a'].get('fatigue_milli')
    whole_guard={k:v for k,v in whole_combat['combatants']['micro.a'].items() if k.startswith('guard_exertion_')}
    chunk_guard={k:v for k,v in chunk_combat['combatants']['micro.a'].items() if k.startswith('guard_exertion_')}
    assert whole_guard==chunk_guard


def test_weapon_contact_projects_physiology_at_zero_elapsed_time(monkeypatch):
    import shinobi_runtime.martial_world.exact_combat as exact
    roster=load('state/martial-world/people/house_tang.json')['people']
    attacker=copy.deepcopy(roster[0]); defender=copy.deepcopy(roster[3])
    attacker['person_id']='zero.a'; defender['person_id']='zero.b'
    attacker['attributes']={k:500 for k in attacker['attributes']}; attacker['martial_skills']={k:500 for k in attacker['martial_skills']}
    defender['attributes']={k:1 for k in defender['attributes']}; defender['martial_skills']={k:1 for k in defender['martial_skills']}
    attacker['health']={'status':'ready','consciousness':100,'injuries':[]}; defender['health']={'status':'ready','consciousness':100,'injuries':[]}
    people={'zero.a':attacker,'zero.b':defender}
    ledger={'schema':'jianghu-equipment-ledger-1.0','policy_assignments':{},'person_loadouts':{'zero.a':{'items':{'weapon_jian':1}},'zero.b':{'items':{}}}}
    combat=initialize_combat(
        combat_ref='zero-contact-time',side_a_refs=['zero.a'],side_b_refs=['zero.b'],people=people,
        zone_ref='site.house_tang',started_at='0061-09-12T00:00:00',objective={'kind':'eliminate','target_refs':['zero.b']},
        equipment_ledger=ledger,initial_ready_weapons={'zero.a':'weapon_jian'},
    )
    combat['positions']['zero.a'].update(x_mm=0,y_mm=0); combat['positions']['zero.b'].update(x_mm=700,y_mm=0)
    elapsed=[]; original=exact._apply_physiology
    def observed(person,*,elapsed_seconds,at_iso=None):
        elapsed.append(int(elapsed_seconds))
        return original(person,elapsed_seconds=elapsed_seconds,at_iso=at_iso)
    monkeypatch.setattr(exact,'_apply_physiology',observed)
    result=resolve_exchange(
        combat=combat,people=people,equipment_ledger=ledger,doctrines={},player_ref='zero.a',player_action_kind='thrust',
        player_target_ref='zero.b',player_weapon_ref='weapon_jian',player_hit_zone='chest',player_targeting_intent='disable',
    )
    event=next(e for e in result['events'] if e.get('actor_ref')=='zero.a')
    assert event['result']=='contact'
    assert elapsed and set(elapsed)=={0}


def test_combat_force_context_defaults_tournament_controlled_and_ambush_lethal():
    from shinobi_runtime.martial_world.exact_combat import combat_default_targeting_intent
    tournament = {"objective": {"kind": "tournament_match"}, "awareness_mode": "mutual"}
    assert combat_default_targeting_intent(tournament, doctrine_ref="doctrine.tang_wei.precision_function_denial") == "disable"
    ambush = {"objective": {"kind": "protect_cargo"}, "awareness_mode": "side_b_ambush"}
    assert combat_default_targeting_intent(ambush, doctrine_ref="doctrine.tang_wei.precision_function_denial") == "lethal"
    capture = {"objective": {"kind": "escape_or_subdue"}, "awareness_mode": "mutual"}
    assert combat_default_targeting_intent(capture) == "disable"


def test_fatigue_is_whole_body_combat_burden_not_only_movement_penalty():
    import copy
    import shinobi_runtime.martial_world.exact_combat as exact
    roster=load('state/martial-world/people/house_tang.json')['people']
    fresh=copy.deepcopy(roster[0]); tired=copy.deepcopy(fresh); exhausted=copy.deepcopy(fresh)
    fresh['fatigue_milli']=0; tired['fatigue_milli']=1500; exhausted['fatigue_milli']=3000
    a=exact.capability_from_person(fresh,action_skill='sword')
    b=exact.capability_from_person(tired,action_skill='sword')
    c=exact.capability_from_person(exhausted,action_skill='sword')
    assert b.mobility<a.mobility and b.reaction<a.reaction
    assert b.offense<a.offense and b.control<a.control and b.defense<a.defense
    assert b.capture<a.capture
    assert c.offense==0 and c.control==0 and c.defense==0 and c.mobility==0 and c.reaction==0


def test_fatigue_reduces_muscle_driven_contact_force_to_zero_at_complete_exhaustion():
    import copy
    import shinobi_runtime.martial_world.exact_combat as exact
    roster=load('state/martial-world/people/house_tang.json')['people']
    fresh=copy.deepcopy(roster[0]); tired=copy.deepcopy(fresh); exhausted=copy.deepcopy(fresh)
    fresh['fatigue_milli']=0; tired['fatigue_milli']=1500; exhausted['fatigue_milli']=3000
    defender=copy.deepcopy(roster[3])
    kwargs=dict(defender=defender,weapon=None,weapon_ref='body_unarmed',action_kind='unarmed_strike',range_m=0.5,defense_force_milli=1000,hit_zone='chest',target_structure_ref=None,created_at='x')
    a=exact._contact_damage(actor=fresh,**kwargs)['transmitted_channels']
    b=exact._contact_damage(actor=tired,**kwargs)['transmitted_channels']
    c=exact._contact_damage(actor=exhausted,**kwargs)['transmitted_channels']
    assert sum(b.values()) < sum(a.values())
    assert sum(c.values()) == 0


def test_broken_weapon_cannot_be_used_in_exact_combat():
    roster=load('state/martial-world/people/house_tang.json')['people']
    attacker=copy.deepcopy(roster[0]); defender=copy.deepcopy(roster[3])
    attacker['person_id']='durability.broken.a'; defender['person_id']='durability.broken.b'
    people={attacker['person_id']:attacker,defender['person_id']:defender}
    ledger={
        'schema':'jianghu-equipment-ledger-1.0','policy_assignments':{},
        'person_loadouts':{
            attacker['person_id']:{'items':{'weapon_jian':1},'condition_milli':{'weapon_jian':0}},
            defender['person_id']:{'items':{}},
        },
    }
    combat=initialize_combat(
        combat_ref='broken-weapon',side_a_refs=[attacker['person_id']],side_b_refs=[defender['person_id']],people=people,
        zone_ref='site.house_tang',started_at='0061-09-12T00:00:00',objective={'kind':'eliminate','target_refs':[defender['person_id']]},
        equipment_ledger=ledger,
    )
    result=resolve_exchange(
        combat=combat,people=people,equipment_ledger=ledger,doctrines={},player_ref=attacker['person_id'],player_action_kind='thrust',
        player_target_ref=defender['person_id'],player_weapon_ref='weapon_jian',player_hit_zone='chest',player_targeting_intent='disable',
    )
    event=next(e for e in result['events'] if e.get('actor_ref')==attacker['person_id'])
    assert event['result']=='weapon_not_owned'
    after=effective_person_loadout(result['equipment_ledger_after'],attacker['person_id'])
    assert int(after['condition_milli']['weapon_jian'])==0


def test_damaged_weapon_condition_reduces_function_without_changing_mass_or_reach():
    import shinobi_runtime.martial_world.exact_combat as exact
    person_ref='durability.profile'
    ledger={
        'schema':'jianghu-equipment-ledger-1.0','policy_assignments':{},
        'person_loadouts':{person_ref:{'items':{'weapon_jian':1},'condition_milli':{'weapon_jian':500}}},
    }
    base=exact._weapon('weapon_jian')
    damaged=exact._weapon_for_holder(ledger,person_ref,'weapon_jian')
    assert base is not None and damaged is not None
    assert damaged['mass_kg']==base['mass_kg']
    assert damaged['reach_m']==base['reach_m']
    assert int(damaged['cut'])==int(base['cut'])*500//1000
    assert int(damaged['pierce'])==int(base['pierce'])*500//1000
    assert int(damaged['control'])==int(base['control'])*500//1000
    assert int(damaged['guard'])==int(base['guard'])*500//1000


def test_exact_melee_body_contact_persists_weapon_wear_to_equipment_ledger():
    roster=load('state/martial-world/people/house_tang.json')['people']
    attacker=copy.deepcopy(roster[0]); defender=copy.deepcopy(roster[3])
    attacker['person_id']='durability.contact.a'; defender['person_id']='durability.contact.b'
    attacker['attributes']={k:500 for k in attacker['attributes']}; attacker['martial_skills']={k:500 for k in attacker['martial_skills']}
    defender['attributes']={k:1 for k in defender['attributes']}; defender['martial_skills']={k:1 for k in defender['martial_skills']}
    attacker['health']={'status':'ready','consciousness':100,'injuries':[]}; defender['health']={'status':'ready','consciousness':100,'injuries':[]}
    people={attacker['person_id']:attacker,defender['person_id']:defender}
    ledger={
        'schema':'jianghu-equipment-ledger-1.0','policy_assignments':{},
        'person_loadouts':{
            attacker['person_id']:{'items':{'weapon_jian':1},'condition_milli':{'weapon_jian':1000}},
            defender['person_id']:{'items':{}},
        },
    }
    combat=initialize_combat(
        combat_ref='durability-contact',side_a_refs=[attacker['person_id']],side_b_refs=[defender['person_id']],people=people,
        zone_ref='site.house_tang',started_at='0061-09-12T00:00:00',objective={'kind':'eliminate','target_refs':[defender['person_id']]},
        equipment_ledger=ledger,initial_ready_weapons={attacker['person_id']:'weapon_jian'},
    )
    combat['positions'][attacker['person_id']].update(x_mm=0,y_mm=0)
    combat['positions'][defender['person_id']].update(x_mm=700,y_mm=0)
    result=resolve_exchange(
        combat=combat,people=people,equipment_ledger=ledger,doctrines={},player_ref=attacker['person_id'],player_action_kind='thrust',
        player_target_ref=defender['person_id'],player_weapon_ref='weapon_jian',player_hit_zone='chest',player_targeting_intent='disable',
    )
    event=next(e for e in result['events'] if e.get('actor_ref')==attacker['person_id'])
    assert event['result'] in {'contact','physical_contact_no_wound'}
    after=effective_person_loadout(result['equipment_ledger_after'],attacker['person_id'])
    assert int(after['condition_milli']['weapon_jian'])==997


def test_automatic_weapon_choice_uses_current_condition_not_pristine_catalog_score():
    import shinobi_runtime.martial_world.exact_combat as exact
    actor=copy.deepcopy(load('state/martial-world/people/house_tang.json')['people'][0])
    actor['person_id']='durability.choice'
    people={actor['person_id']:actor}
    ledger={
        'schema':'jianghu-equipment-ledger-1.0','policy_assignments':{},
        'person_loadouts':{
            actor['person_id']:{
                'items':{'weapon_jian':1,'weapon_short_sword':1},
                'condition_milli':{'weapon_jian':10,'weapon_short_sword':1000},
            },
        },
    }
    chosen=exact.default_weapon_for_action(
        people=people,equipment_ledger=ledger,actor_ref=actor['person_id'],action_kind='thrust',
    )
    assert chosen=='weapon_short_sword'


def test_incapacitation_after_commitment_but_before_projectile_release_prevents_release_and_consumption():
    import shinobi_runtime.martial_world.exact_combat as exact
    roster=load('state/martial-world/people/house_tang.json')['people']
    archer=copy.deepcopy(roster[0]); target=copy.deepcopy(roster[3])
    archer['person_id']='release.boundary.a'; target['person_id']='release.boundary.b'
    archer['health']={'status':'ready','consciousness':100,'injuries':[]}; target['health']={'status':'ready','consciousness':100,'injuries':[]}
    people={archer['person_id']:archer,target['person_id']:target}
    ledger={
        'schema':'jianghu-equipment-ledger-1.0','policy_assignments':{},
        'person_loadouts':{
            archer['person_id']:{'items':{'weapon_bow':1,'item_arrow':3},'condition_milli':{'weapon_bow':1000}},
            target['person_id']:{'items':{}},
        },
    }
    combat=initialize_combat(
        combat_ref='release-boundary',side_a_refs=[archer['person_id']],side_b_refs=[target['person_id']],people=people,
        zone_ref='site.house_tang',started_at='0061-09-12T00:00:00',objective={'kind':'eliminate','target_refs':[target['person_id']]},
        initial_range_band=2,equipment_ledger=ledger,
    )
    action=exact._schedule_action(
        combat=combat,actor_ref=archer['person_id'],target_ref=target['person_id'],action_kind='bow_shot',weapon_ref='weapon_bow',poison_ref=None,
        hit_zone='chest',target_structure_ref=None,decision_origin='test',people=people,equipment_ledger=ledger,
    )
    assert action.commit_at_ms < action.release_at_ms
    combat['combatants'][archer['person_id']]['incapacitated_at_ms']=(action.commit_at_ms+action.release_at_ms)//2
    before=int(effective_person_loadout(ledger,archer['person_id'])['items']['item_arrow'])
    event=exact._resolve_scheduled_action(combat=combat,action=action,people=people,equipment_ledger=ledger)
    after=int(effective_person_loadout(ledger,archer['person_id'])['items']['item_arrow'])
    assert event['result']=='action_disrupted_after_commitment_before_release'
    assert after==before
