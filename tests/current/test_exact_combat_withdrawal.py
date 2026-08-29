import copy
import json
from pathlib import Path

import shinobi_runtime.martial_world.exact_combat as exact
from shinobi_runtime.martial_world.exact_combat import attempt_disengage, initialize_combat, resolve_exchange

ROOT=Path(__file__).resolve().parents[2]
def load(rel): return json.loads((ROOT/rel).read_text())


def person(base, ref, faction):
    row=copy.deepcopy(base); row['person_id']=ref; row['faction_ref']=faction
    row['health']={'status':'ready','injuries':[],'blood_lost_ml':0,'shock':0,'consciousness':100}
    row['fatigue_milli']=0; row['poison_burdens']={}; row['pending_poison_burdens']={}
    return row


def ledger(*refs):
    return {'schema':'jianghu-equipment-ledger-1.0','policy_assignments':{},'person_loadouts':{ref:{'items':{}} for ref in refs}}


def doctrine(preservation=60, discipline=45):
    return {'casualty_preservation':preservation,'withdrawal_discipline':discipline}


def test_side_collapse_declares_withdrawal_before_attack(monkeypatch):
    base=load('state/martial-world/people/house_tang.json')['people'][0]
    player=person(base,'withdraw.player','faction.player'); survivor=person(base,'withdraw.survivor','faction.withdraw'); casualty=person(base,'withdraw.casualty','faction.withdraw')
    casualty['health'].update(status='dead',consciousness=0)
    people={row['person_id']:row for row in (player,survivor,casualty)}; gear=ledger(*people)
    combat=initialize_combat(combat_ref='withdraw-collapse',side_a_refs=[player['person_id']],side_b_refs=[survivor['person_id'],casualty['person_id']],people=people,zone_ref='site.house_tang',started_at='x',objective={'kind':'eliminate'},equipment_ledger=gear)
    monkeypatch.setattr(exact,'_observe_visible_enemies',lambda *args,**kwargs:[])
    result=resolve_exchange(combat=combat,people=people,equipment_ledger=gear,doctrines={'faction.withdraw':doctrine()},player_ref=player['person_id'],player_action_kind='unarmed_strike',player_target_ref=survivor['person_id'],player_weapon_ref='body_unarmed',player_hit_zone='chest',player_targeting_intent='disable')
    events=[event for event in result['events'] if event.get('actor_ref')==survivor['person_id']]
    assert events[0]['result']=='withdrawal_declared'
    assert events[-1]['result'] in {'withdrawal_in_progress','withdrew_from_combat'}
    assert not any(event.get('action_kind') for event in events)


def test_multiple_withdrawers_share_one_second_slice(monkeypatch):
    base=load('state/martial-world/people/house_tang.json')['people'][0]
    player=person(base,'shared.player','faction.player'); rows=[person(base,f'shared.npc.{idx}','faction.withdraw') for idx in range(4)]
    for row in rows[2:]: row['health'].update(status='dead',consciousness=0)
    people={row['person_id']:row for row in [player,*rows]}; gear=ledger(*people)
    combat=initialize_combat(combat_ref='withdraw-shared',side_a_refs=[player['person_id']],side_b_refs=[row['person_id'] for row in rows],people=people,zone_ref='site.house_tang',started_at='x',objective={'kind':'eliminate'},equipment_ledger=gear)
    monkeypatch.setattr(exact,'_observe_visible_enemies',lambda *args,**kwargs:[])
    result=resolve_exchange(combat=combat,people=people,equipment_ledger=gear,doctrines={'faction.withdraw':doctrine()},player_ref=player['person_id'],player_action_kind='unarmed_strike',player_target_ref=rows[0]['person_id'],player_weapon_ref='body_unarmed',player_hit_zone='chest',player_targeting_intent='disable')
    assert result['combat_after']['elapsed_ms']==1250
    moved=[event for event in result['events'] if event.get('actor_ref') in {rows[0]['person_id'],rows[1]['person_id']} and event.get('result') in {'withdrawal_in_progress','withdrew_from_combat'}]
    assert len(moved)==2
    assert {event['started_at_ms'] for event in moved}=={250}
    assert {event['ended_at_ms'] for event in moved}=={1250}


def test_future_reinforcement_is_not_counted_as_arrived_strength():
    base=load('state/martial-world/people/house_tang.json')['people'][0]
    player=person(base,'reinforce.player','faction.player'); survivor=person(base,'reinforce.survivor','faction.withdraw'); casualty=person(base,'reinforce.casualty','faction.withdraw'); future=person(base,'reinforce.future','faction.withdraw')
    casualty['health'].update(status='dead',consciousness=0)
    people={row['person_id']:row for row in (player,survivor,casualty,future)}; gear=ledger(*people)
    combat=initialize_combat(combat_ref='withdraw-reinforce',side_a_refs=[player['person_id']],side_b_refs=[survivor['person_id'],casualty['person_id'],future['person_id']],people=people,zone_ref='site.house_tang',started_at='x',objective={'kind':'eliminate'},equipment_ledger=gear,reinforcement_delays_ms={future['person_id']:60_000})
    decision=exact._npc_withdrawal_decision(combat=combat,actor_ref=survivor['person_id'],people=people,faction_doctrine=doctrine())
    assert decision is not None
    assert decision['arrived_side_count']==2
    assert decision['loss_percent']==50


def test_healthy_low_preservation_fighter_does_not_withdraw():
    base=load('state/martial-world/people/house_tang.json')['people'][0]
    actor=person(base,'hold.actor','faction.hold'); casualty=person(base,'hold.casualty','faction.hold'); enemy=person(base,'hold.enemy','faction.enemy')
    casualty['health'].update(status='dead',consciousness=0)
    people={row['person_id']:row for row in (actor,casualty,enemy)}; gear=ledger(*people)
    combat=initialize_combat(combat_ref='withdraw-hold',side_a_refs=[actor['person_id'],casualty['person_id']],side_b_refs=[enemy['person_id']],people=people,zone_ref='site.house_tang',started_at='x',objective={'kind':'eliminate'},equipment_ledger=gear)
    assert exact._npc_withdrawal_decision(combat=combat,actor_ref=actor['person_id'],people=people,faction_doctrine=doctrine(20,10)) is None


def test_dead_enemy_does_not_block_disengagement_clearance():
    base=load('state/martial-world/people/house_tang.json')['people'][0]
    actor=person(base,'clear.actor','faction.actor'); enemy=person(base,'clear.dead','faction.enemy')
    enemy['health'].update(status='dead',consciousness=0)
    actor['attributes']={key:1 for key in actor.get('attributes',{})}; actor['martial_skills']={key:1 for key in actor.get('martial_skills',{})}
    people={row['person_id']:row for row in (actor,enemy)}; gear=ledger(*people)
    combat=initialize_combat(combat_ref='withdraw-clear',side_a_refs=[actor['person_id']],side_b_refs=[enemy['person_id']],people=people,zone_ref='site.house_tang',started_at='x',objective={'kind':'eliminate'},equipment_ledger=gear)
    combat['positions'][actor['person_id']].update(x_mm=0,y_mm=0); combat['positions'][enemy['person_id']].update(x_mm=1200,y_mm=0)
    result=attempt_disengage(combat=combat,actor_ref=actor['person_id'],people=people,equipment_ledger=gear)
    assert result['escaped'] is True
    assert result['movement']['nearest_enemy_mm']==999_999
    assert result['combat_after']['elapsed_ms']==1000
