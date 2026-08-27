import copy
import json
from pathlib import Path

from shinobi_runtime.martial_world import exact_combat as exact
from shinobi_runtime.martial_world.health import recovery_advance, wound_from_contact
from shinobi_runtime.martial_world.mounts import active_mount_allocations, mounted_motion_profile, rider_control_milli

ROOT = Path(__file__).resolve().parents[2]


def load(rel):
    return json.loads((ROOT / rel).read_text())


def person(ref, *, level=100):
    base = copy.deepcopy(load('state/martial-world/people/house_tang.json')['people'][0])
    base['person_id'] = ref
    base['faction_ref'] = 'house_tang'
    base['attributes'] = {k: level for k in base['attributes']}
    base['martial_skills'] = {'spear': level, 'sword': level, 'bow': level, 'unarmed': level}
    base['health'] = {'status': 'ready', 'consciousness': 100, 'injuries': []}
    return base


def ledger_for(*refs):
    return {
        'schema': 'jianghu-equipment-ledger-1.0',
        'policy_assignments': {},
        'person_loadouts': {ref: {'items': {'weapon_spear': 1}} for ref in refs},
    }


def test_mounted_combat_has_no_riding_proficiency_and_uses_same_shared_clock():
    rider = person('rider', level=120)
    target = person('target', level=120)
    assert 'riding' not in {str(k).lower() for k in rider['martial_skills']}
    people = {'rider': rider, 'target': target}
    ledger = ledger_for('rider', 'target')

    foot = exact.initialize_combat(
        combat_ref='foot', side_a_refs=['rider'], side_b_refs=['target'], people=people,
        zone_ref='test', started_at='x', objective={'kind': 'eliminate', 'target_refs': ['target']},
        equipment_ledger=ledger,
    )
    mounted = exact.initialize_combat(
        combat_ref='mounted', side_a_refs=['rider'], side_b_refs=['target'], people=people,
        zone_ref='test', started_at='x', objective={'kind': 'eliminate', 'target_refs': ['target']},
        equipment_ledger=ledger, mount_assignments={'rider': {'owner_faction_ref': 'house_tang'}},
    )
    for combat in (foot, mounted):
        combat['positions']['rider'].update(x_mm=0, y_mm=0)
        combat['positions']['target'].update(x_mm=7000, y_mm=0)

    args = dict(
        actor_ref='rider', target_ref='target', action_kind='thrust', weapon_ref='weapon_spear',
        poison_ref=None, hit_zone='chest', target_structure_ref=None, decision_origin='test',
        people=people, equipment_ledger=ledger,
    )
    foot_action = exact._schedule_action(combat=foot, **args)
    mounted_action = exact._schedule_action(combat=mounted, **args)
    assert foot_action.declared_at_ms == mounted_action.declared_at_ms == 0
    assert int(mounted_action.profile.effect_parameters['approach_time_ms']) < int(foot_action.profile.effect_parameters['approach_time_ms'])
    assert mounted_action.contact_at_ms < foot_action.contact_at_ms


def test_mounted_control_is_derived_from_existing_attributes_and_anatomy_only():
    healthy = person('healthy', level=100)
    impaired = copy.deepcopy(healthy)
    lost_leg = recovery_advance(
        wound_from_contact(structure_ref='left_knee', cut=260, pierce=40, blunt=40, penetration=60, created_at='x'),
        elapsed_hours=200000,
    )
    impaired['health']['injuries'] = [lost_leg]
    assert rider_control_milli(impaired) < rider_control_milli(healthy)
    assert mounted_motion_profile(impaired, mount_state={'active': True, 'status': 'active', 'condition_milli': 1000})['effective_speed_mmps'] < mounted_motion_profile(healthy, mount_state={'active': True, 'status': 'active', 'condition_milli': 1000})['effective_speed_mmps']
    assert 'riding' not in {str(k).lower() for k in impaired['martial_skills']}


def test_mounted_spear_momentum_changes_contact_force_not_learned_spear_skill():
    rider = person('rider', level=140)
    target = person('target', level=100)
    ledger = ledger_for('rider')
    state = {'mount': {'kind': 'riding_horse', 'owner_faction_ref': 'house_tang', 'condition_milli': 1000, 'status': 'active', 'active': True}}
    motion = exact._mounted_weapon_motion_milli('rider', rider, ledger, state, action_kind='thrust', weapon=exact._weapon('weapon_spear'))
    assert motion > 1000
    skill_before = rider['martial_skills']['spear']
    foot = exact._contact_damage(
        actor=rider, defender=target, weapon=exact._weapon('weapon_spear'), weapon_ref='weapon_spear',
        action_kind='thrust', range_m=2.0, defense_force_milli=1000, hit_zone='chest',
        target_structure_ref=None, created_at='x', precision_margin=100, motion_milli=1000,
    )
    mounted = exact._contact_damage(
        actor=rider, defender=target, weapon=exact._weapon('weapon_spear'), weapon_ref='weapon_spear',
        action_kind='thrust', range_m=2.0, defense_force_milli=1000, hit_zone='chest',
        target_structure_ref=None, created_at='x', precision_margin=100, motion_milli=motion,
    )
    assert mounted['transmitted_channels']['pierce'] > foot['transmitted_channels']['pierce']
    assert mounted['transmitted_channels']['penetration'] > foot['transmitted_channels']['penetration']
    assert rider['martial_skills']['spear'] == skill_before


def test_mount_is_targetable_and_service_loss_is_a_conserved_pool_event():
    attacker = person('attacker', level=500)
    defender = person('defender', level=100)
    mount = {
        'kind': 'riding_horse', 'owner_faction_ref': 'house_tang', 'condition_milli': 1000,
        'status': 'active', 'active': True, 'inventory_debited': False, 'service_loss_pending': False,
    }
    result = exact._contact_damage(
        actor=attacker, defender=defender, weapon=exact._weapon('weapon_spear'), weapon_ref='weapon_spear',
        action_kind='thrust', range_m=2.0, defense_force_milli=1000, hit_zone='mount',
        target_structure_ref=None, created_at='x', precision_margin=100, motion_milli=1500, mount_state=mount,
    )
    assert result['outcome'] == 'mount_contact'
    assert result['wound'] is None
    assert result['mount_result']['status'] in {'disabled', 'dead'}
    assert result['mount_result']['service_loss'] is True


def test_active_mount_allocations_count_only_live_allocated_horses():
    combats = {
        'combats': {
            'a': {'status': 'active', 'combatants': {
                'x': {'mount': {'owner_faction_ref': 'house_tang', 'active': True, 'status': 'active'}},
                'y': {'mount': {'owner_faction_ref': 'house_tang', 'active': False, 'status': 'disabled'}},
            }},
            'b': {'status': 'resolved', 'combatants': {
                'z': {'mount': {'owner_faction_ref': 'house_tang', 'active': True, 'status': 'active'}},
            }},
        }
    }
    assert active_mount_allocations(combats, faction_ref='house_tang') == 1


def test_mount_loss_before_release_interrupts_already_scheduled_action_on_shared_clock():
    rider = person('rider', level=140)
    target = person('target', level=100)
    people = {'rider': rider, 'target': target}
    ledger = ledger_for('rider', 'target')
    combat = exact.initialize_combat(
        combat_ref='mount-loss-clock', side_a_refs=['rider'], side_b_refs=['target'], people=people,
        zone_ref='test', started_at='x', objective={'kind': 'eliminate', 'target_refs': ['target']},
        equipment_ledger=ledger, mount_assignments={'rider': {'owner_faction_ref': 'house_tang'}},
    )
    combat['positions']['rider'].update(x_mm=0, y_mm=0)
    combat['positions']['target'].update(x_mm=6500, y_mm=0)
    action = exact._schedule_action(
        combat=combat, actor_ref='rider', target_ref='target', action_kind='thrust', weapon_ref='weapon_spear',
        poison_ref=None, hit_zone='chest', target_structure_ref=None, decision_origin='test',
        people=people, equipment_ledger=ledger,
    )
    assert action.profile.effect_parameters['mounted_at_declaration'] is True
    lost_at = max(action.start_at_ms, action.commit_at_ms - 1)
    mount = combat['combatants']['rider']['mount']
    mount.update(active=False, status='disabled', disabled_at_ms=lost_at)
    event = exact._resolve_scheduled_action(combat=combat, action=action, people=copy.deepcopy(people), equipment_ledger=copy.deepcopy(ledger))
    assert event['result'] == 'action_interrupted_by_mount_loss_before_commitment'
    assert event['mount_disabled_at_ms'] == lost_at
