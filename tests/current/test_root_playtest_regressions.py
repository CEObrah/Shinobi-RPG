from __future__ import annotations

import copy
import json
from pathlib import Path

import shinobi_runtime.martial_world.exact_combat as exact
from shinobi_runtime.api.travel_operations import combat_observation_scene_projection

ROOT = Path(__file__).resolve().parents[2]


def _person(base, ref, faction):
    row = copy.deepcopy(base)
    row['person_id'] = ref
    row['faction_ref'] = faction
    row['fatigue_milli'] = 0
    row['health'] = {'status':'ready','injuries':[],'blood_lost_ml':0,'shock':0,'consciousness':100}
    row['poison_burdens'] = {}
    row['pending_poison_burdens'] = {}
    return row


def _ledger(*refs):
    return {
        'schema':'jianghu-equipment-ledger-1.0','policy_assignments':{},
        'person_loadouts':{ref:{'items':{},'condition_milli':{}} for ref in refs},
    }


def test_field_medic_role_never_initiates_autonomous_attack_without_explicit_support_order():
    base = json.loads((ROOT/'state/martial-world/people/house_tang.json').read_text())['people'][0]
    player = _person(base, 'medic-role.player', 'faction.player')
    medic = _person(base, 'medic-role.han', 'faction.player')
    enemy = _person(base, 'medic-role.enemy', 'faction.enemy')
    people = {row['person_id']: row for row in (player, medic, enemy)}
    gear = _ledger(*people)
    combat = exact.initialize_combat(
        combat_ref='medic-role', side_a_refs=[player['person_id'], medic['person_id']],
        side_b_refs=[enemy['person_id']], people=people, zone_ref='test', started_at='x',
        objective={'kind':'eliminate'}, equipment_ledger=gear,
    )
    result = exact.resolve_exchange(
        combat=combat, people=people, equipment_ledger=gear, doctrines={},
        player_ref=player['person_id'], player_action_kind='unarmed_strike',
        player_target_ref=enemy['person_id'], player_weapon_ref='body_unarmed',
        player_hit_zone='chest', player_targeting_intent='disable',
        player_retinue_context={
            'leader_ref': player['person_id'],
            'member_refs': [medic['person_id']],
            'temporary_member_refs': [],
            'member_roles': {medic['person_id']: 'field_medic'},
            'combat_doctrine_ref': 'doctrine.player_retinue.tang_wei.personal_guard',
        },
    )
    medic_events = [row for row in result['events'] if row.get('actor_ref') == medic['person_id']]
    assert any(row.get('result') == 'holding_medical_support_position' for row in medic_events)
    assert not any(row.get('action_kind') in {'thrust','cut','hidden_weapon_throw','bow_shot','unarmed_strike'} for row in medic_events)


def test_public_combat_projection_separates_cumulative_observation_from_current_visibility():
    combat = {
        'combat_id':'combat:visibility','status':'active','elapsed_ms':1000,
        'sides':{'side_a':['pc'],'side_b':['enemy.visible','enemy.gone']},
        'combatants':{
            'pc':{'observed_refs':['enemy.visible','enemy.gone']},
            'enemy.visible':{'observed_refs':[],'status_families':[]},
            'enemy.gone':{'observed_refs':[],'status_families':['escaped']},
        },
        'positions':{
            'pc':{'zone_ref':'z','x_mm':0,'y_mm':0},
            'enemy.visible':{'zone_ref':'z','x_mm':2000,'y_mm':0},
            'enemy.gone':{'zone_ref':'z','x_mm':4000,'y_mm':0},
        },
        'obstacles':[],
    }
    state={'state/martial-world/combats.json':{'combats':{'combat:visibility':combat}}}
    result=combat_observation_scene_projection(read_json=lambda path: state[path], player_id='pc')
    assert result is not None
    observation=result['player_observation']
    assert observation['confirmed_observed_hostile_count_cumulative'] == 2
    assert observation['currently_visible_hostile_count'] == 1
    assert 'current_visibility_is_not_a_hidden_force_census' in result['current_count_semantics']


def test_combat_narrative_projection_uses_exact_structure_language_and_protects_critical_allies():
    before = {
        'ally': {'health': {'status':'ready','consciousness':100,'shock':0}},
        'pc': {'health': {'status':'ready','consciousness':100,'shock':0}},
        'enemy': {'health': {'status':'ready','consciousness':100,'shock':0}},
    }
    after = copy.deepcopy(before)
    after['ally']['health'].update(status='incapacitated', consciousness=0, shock=180)
    combat = {
        'elapsed_ms':1200,
        'sides':{'side_a':['pc','ally'],'side_b':['enemy']},
        'combatants':{'pc':{'observed_refs':['enemy']},'ally':{},'enemy':{}},
    }
    projection = exact._combat_narrative_projection(
        combat_before=combat, combat_after=combat, people_before=before, people_after=after,
        events=[{'actor_ref':'pc','actual_ref':'enemy','result':'contact','hit_zone':'neck','ended_at_ms':900}],
        player_ref='pc', combat_information={'visible_hostiles_current':1,'observed_combat_capable_remaining':1},
    )
    contact=next(row for row in projection['beats'] if row.get('kind')=='contact')
    assert contact['contact_zone']=='neck'
    assert 'contact_structure_ref' not in contact
    critical=next(row for row in projection['beats'] if row.get('kind')=='critical_ally_casualty')
    assert critical['must_narrate_before_next_decision'] is True
    assert projection['protected_salience_count'] == 1
