import copy

from shinobi_runtime.martial_world.combat_simulation import simulate_exact_combat
from shinobi_runtime.martial_world.doctrines import resolve_individual_doctrine, resolve_player_retinue_doctrine
from shinobi_runtime.martial_world.exact_combat import (
    automatic_resource_policy,
    default_action_for,
    default_target_for,
    initialize_combat,
    resolve_exchange,
)
from shinobi_runtime.martial_world.qi import person_current_qi_milli


def _fighter(ref: str, level: int, *, doctrine: bool = False):
    return {
        'person_id': ref,
        'attributes': {
            'strength': level,
            'speed': level,
            'dexterity': level,
            'perception': level,
            'endurance': level,
            'intelligence': level,
            'willpower': level,
        },
        'martial_skills': {
            'sword': level,
            'unarmed': level,
            'hidden_weapons': level,
        },
        'qi': level,
        'qi_control': level,
        'current_qi': level,
        'current_qi_milli': level * 1000,
        'health': {'status': 'healthy', 'consciousness': 100, 'injuries': []},
        **({'combat_doctrine_ref': 'doctrine.tang_wei.precision_function_denial'} if doctrine else {}),
    }


def _combat(side_a, side_b, *, kind='eliminate'):
    refs = [*side_a, *side_b]
    return {
        'status': 'active',
        'objective': {'kind': kind},
        'awareness_mode': 'mutual',
        'sides': {'side_a': list(side_a), 'side_b': list(side_b)},
        'combatants': {ref: {'status_families': []} for ref in refs},
    }


def _ledger(actor_ref='wei'):
    return {
        'schema': 'jianghu-equipment-ledger-1.0',
        'policy_assignments': {},
        'person_loadouts': {
            actor_ref: {
                'items': {
                    'weapon_needle': 8,
                    'poison_cardiotoxic': 2,
                    'poison_paralytic': 2,
                },
                'condition_milli': {},
            },
        },
    }


def test_wei_and_retinue_resource_doctrine_is_sparse_escalation_policy():
    wei = resolve_individual_doctrine('doctrine.tang_wei.precision_function_denial')['resource_discipline']
    team = resolve_player_retinue_doctrine('doctrine.player_retinue.tang_wei.personal_guard')['resource_discipline']
    assert wei['qi_conservation'] == 92
    assert wei['qi_trigger_threat_percent'] == 90
    assert wei['poison_trigger_threat_percent'] == 110
    assert team['qi_conservation'] == 90
    assert team['qi_trigger_threat_percent'] == 90
    assert team['poison_trigger_threat_percent'] == 110
    assert team['outnumbered_override'] is True
    assert team['principal_overflow_override'] is True
    assert team['formal_nonlethal_poison'] is False


def test_wei_autonomous_resources_stay_off_for_routine_target_and_escalate_for_strong_target():
    wei = _fighter('wei', 100, doctrine=True)
    weak = _fighter('weak', 50)
    strong = _fighter('strong', 130)
    ledger = _ledger()

    routine = automatic_resource_policy(
        combat=_combat(['wei'], ['weak']), actor_ref='wei', target_ref='weak',
        people={'wei': wei, 'weak': weak}, equipment_ledger=ledger, faction_doctrine={},
        action_kind='hidden_weapon_throw', weapon_ref='weapon_needle', intent='lethal',
    )
    assert routine['threat_percent'] < 90
    assert routine['qi_allowed'] is False
    assert routine['qi_allocation_milli'] == {}
    assert routine['poison_allowed'] is False
    assert routine['poison_ref'] is None
    assert routine['qi_reserve_milli'] == 92_000

    dangerous = automatic_resource_policy(
        combat=_combat(['wei'], ['strong']), actor_ref='wei', target_ref='strong',
        people={'wei': wei, 'strong': strong}, equipment_ledger=ledger, faction_doctrine={},
        action_kind='hidden_weapon_throw', weapon_ref='weapon_needle', intent='lethal',
    )
    assert dangerous['threat_percent'] >= 110
    assert dangerous['qi_allowed'] is True
    assert dangerous['qi_allocation_milli']
    assert dangerous['poison_allowed'] is True
    assert dangerous['poison_ref'] == 'cardiotoxic'


def test_automatic_poison_respects_no_poison_vow_without_suppressing_qi_escalation():
    wei = _fighter('wei', 100, doctrine=True)
    strong = _fighter('strong', 130)
    social = {
        'schema': 'jianghu-social-state-1.0',
        'vows': {
            'vow.test': {
                'person_ref': 'wei', 'kind': 'no_poison', 'strength': 100,
                'declared_at': '0061-01-01T00:00:00',
            },
        },
    }
    policy = automatic_resource_policy(
        combat=_combat(['wei'], ['strong']), actor_ref='wei', target_ref='strong',
        people={'wei': wei, 'strong': strong}, equipment_ledger=_ledger(), faction_doctrine={},
        action_kind='hidden_weapon_throw', weapon_ref='weapon_needle', intent='lethal',
        social_state=social,
    )
    assert policy['qi_allowed'] is True
    assert policy['qi_allocation_milli']
    assert policy['poison_allowed'] is True
    assert policy['poison_vow_blocked'] is True
    assert policy['poison_ref'] is None


def test_resource_escalation_can_trigger_from_real_group_danger_but_formal_spar_never_auto_poisons():
    wei = _fighter('wei', 100, doctrine=True)
    enemy_a = _fighter('enemy.a', 60)
    enemy_b = _fighter('enemy.b', 60)
    ledger = _ledger()
    people = {'wei': wei, 'enemy.a': enemy_a, 'enemy.b': enemy_b}

    outnumbered = automatic_resource_policy(
        combat=_combat(['wei'], ['enemy.a', 'enemy.b']), actor_ref='wei', target_ref='enemy.a',
        people=people, equipment_ledger=ledger, faction_doctrine={},
        action_kind='hidden_weapon_throw', weapon_ref='weapon_needle', intent='lethal',
    )
    assert outnumbered['threat_percent'] < 90
    assert outnumbered['escalation_override'] is True
    assert outnumbered['qi_allowed'] is True
    assert outnumbered['poison_allowed'] is True

    trivial_a = _fighter('trivial.a', 15)
    trivial_b = _fighter('trivial.b', 15)
    trivial_people = {'wei': wei, 'trivial.a': trivial_a, 'trivial.b': trivial_b}
    merely_more_bodies = automatic_resource_policy(
        combat=_combat(['wei'], ['trivial.a', 'trivial.b']), actor_ref='wei', target_ref='trivial.a',
        people=trivial_people, equipment_ledger=ledger, faction_doctrine={},
        action_kind='hidden_weapon_throw', weapon_ref='weapon_needle', intent='lethal',
    )
    assert merely_more_bodies['threat_percent'] < 90
    assert merely_more_bodies['escalation_override'] is False
    assert merely_more_bodies['qi_allowed'] is False
    assert merely_more_bodies['poison_allowed'] is False

    strong = _fighter('strong', 130)
    formal = automatic_resource_policy(
        combat=_combat(['wei'], ['strong'], kind='spar'), actor_ref='wei', target_ref='strong',
        people={'wei': wei, 'strong': strong}, equipment_ledger=ledger, faction_doctrine={},
        action_kind='hidden_weapon_throw', weapon_ref='weapon_needle', intent='disable',
    )
    assert formal['qi_allowed'] is True
    assert formal['poison_allowed'] is False
    assert formal['poison_ref'] is None


def test_direct_player_command_overrides_standing_resource_conservation_policy():
    wei = _fighter('wei', 100, doctrine=True)
    weak = _fighter('weak', 40)
    people = {'wei': wei, 'weak': weak}
    ledger = _ledger()
    combat = initialize_combat(
        combat_ref='manual-resource-override', side_a_refs=['wei'], side_b_refs=['weak'],
        people=people, zone_ref='z', started_at='x', objective={'kind': 'eliminate'},
        equipment_ledger=ledger, initial_range_band=2,
    )
    result = resolve_exchange(
        combat=combat, people=people, equipment_ledger=ledger, doctrines={},
        player_ref='wei', player_action_kind='hidden_weapon_throw', player_target_ref='weak',
        player_weapon_ref='weapon_needle', player_hit_zone='chest', player_targeting_intent='lethal',
        player_poison_ref='cardiotoxic', player_qi_allocation_milli={'sensing': 200, 'body': 200},
    )
    assert result['combat_after']['combatants']['wei']['qi_allocation_milli'] == {'sensing': 200, 'body': 200}
    assert 'qi_reserve_milli' not in result['combat_after']['combatants']['wei']
    player_events = [row for row in result['events'] if row.get('actor_ref') == 'wei']
    assert player_events
    assert player_events[0]['poison_ref'] == 'cardiotoxic'


def test_delegated_bounded_simulation_uses_wei_resource_doctrine_instead_of_treating_driver_as_manual():
    ledger = {'schema': 'jianghu-equipment-ledger-1.0', 'policy_assignments': {}, 'person_loadouts': {}}
    wei = _fighter('wei', 100, doctrine=True)
    weak = _fighter('weak', 40)
    before = person_current_qi_milli(wei)
    routine = simulate_exact_combat(
        combat_ref='delegated-routine', side_a_refs=['wei'], side_b_refs=['weak'],
        people={'wei': copy.deepcopy(wei), 'weak': weak}, equipment_ledger=copy.deepcopy(ledger), doctrines={},
        zone_ref='z', started_at='0061-01-01T00:00:00', objective={'kind': 'eliminate'},
        targeting_intent='lethal', max_exchanges=1, delegated_actor_ref='wei',
    )
    assert person_current_qi_milli(routine['people_after']['wei']) == before

    strong = _fighter('strong', 130)
    dangerous = simulate_exact_combat(
        combat_ref='delegated-dangerous', side_a_refs=['wei'], side_b_refs=['strong'],
        people={'wei': copy.deepcopy(wei), 'strong': strong}, equipment_ledger=copy.deepcopy(ledger), doctrines={},
        zone_ref='z', started_at='0061-01-01T00:00:00', objective={'kind': 'eliminate'},
        targeting_intent='lethal', max_exchanges=1, delegated_actor_ref='wei',
    )
    assert person_current_qi_milli(dangerous['people_after']['wei']) < before
    assert person_current_qi_milli(dangerous['people_after']['wei']) >= 92_000


def test_high_level_attack_can_leave_target_weapon_and_resources_to_standing_doctrine():
    wei = _fighter('wei', 100, doctrine=True)
    near = _fighter('near', 40)
    far = _fighter('far', 130)
    people = {'wei': wei, 'near': near, 'far': far}
    ledger = _ledger()
    combat = initialize_combat(
        combat_ref='high-level-attack', side_a_refs=['wei'], side_b_refs=['near', 'far'],
        people=people, zone_ref='z', started_at='x', objective={'kind': 'eliminate'},
        equipment_ledger=ledger, initial_range_band=2,
    )
    # A generic attack selects only among lawfully observed enemies. With no
    # stronger social pressure, deterministic physical proximity wins.
    target = default_target_for(combat=combat, people=people, actor_ref='wei')
    assert target in {'near', 'far'}
    kind, weapon = default_action_for(
        combat=combat, people=people, equipment_ledger=ledger,
        actor_ref='wei', target_ref=target,
    )
    assert kind in {'hidden_weapon_throw', 'unarmed_strike', 'cut', 'thrust'}
    assert isinstance(weapon, str) and weapon
    policy = automatic_resource_policy(
        combat=combat, actor_ref='wei', target_ref=target, people=people,
        equipment_ledger=ledger, faction_doctrine={}, action_kind=kind,
        weapon_ref=weapon, intent='lethal',
    )
    expected_qi = policy['threat_percent'] >= 90 or policy['escalation_override']
    expected_poison = policy['threat_percent'] >= 110 or policy['escalation_override']
    assert policy['qi_allowed'] is expected_qi
    assert policy['poison_allowed'] is expected_poison


def test_combat_command_uses_doctrine_for_omitted_resources_without_delegation_gate():
    from shinobi_runtime.commands.specs import COMMAND_SPECS

    variant = COMMAND_SPECS['jianghu_combat_resolution'].variants['exchange']
    assert variant.required_fields == ('action', 'combat_ref')
    assert {
        'action_kind', 'target_ref', 'weapon_ref', 'hit_zone', 'poison_ref',
        'qi_allocation_milli', 'exchange_count', 'duration_seconds', 'until_resolution',
    } <= set(variant.optional_fields)
    assert 'delegated' not in variant.optional_fields

def test_player_attack_shorthand_uses_personal_and_retinue_resource_policy_inside_exact_resolver():
    wei = _fighter('wei', 100, doctrine=True)
    strong = _fighter('strong', 130)
    people = {'wei': wei, 'strong': strong}
    ledger = _ledger()
    combat = initialize_combat(
        combat_ref='shorthand-resource-policy', side_a_refs=['wei'], side_b_refs=['strong'],
        people=people, zone_ref='z', started_at='x', objective={'kind': 'eliminate'},
        equipment_ledger=ledger, initial_range_band=2,
    )
    result = resolve_exchange(
        combat=combat, people=people, equipment_ledger=ledger, doctrines={},
        player_ref='wei', player_action_kind='hidden_weapon_throw', player_target_ref='strong',
        player_weapon_ref='weapon_needle', player_hit_zone='auto', player_targeting_intent='lethal',
        player_auto_qi=True, player_auto_poison=True,
        player_retinue_context={
            'leader_ref': 'wei', 'member_refs': [], 'member_roles': {},
            'combat_doctrine_ref': 'doctrine.player_retinue.tang_wei.personal_guard',
        },
    )
    state = result['combat_after']['combatants']['wei']
    assert state['qi_allocation_milli']
    assert state['qi_reserve_milli'] == 92_000
    player_events = [row for row in result['events'] if row.get('actor_ref') == 'wei']
    assert player_events
    assert player_events[0]['poison_ref'] == 'cardiotoxic'


def test_high_level_attack_honors_explicit_weapon_without_requiring_exact_technique():
    wei = _fighter('wei', 100, doctrine=True)
    target = _fighter('target', 80)
    people = {'wei': wei, 'target': target}
    ledger = _ledger()
    combat = initialize_combat(
        combat_ref='preferred-weapon-attack', side_a_refs=['wei'], side_b_refs=['target'],
        people=people, zone_ref='z', started_at='x', objective={'kind': 'eliminate'},
        equipment_ledger=ledger, initial_range_band=3,
    )
    kind, weapon = default_action_for(
        combat=combat, people=people, equipment_ledger=ledger,
        actor_ref='wei', target_ref='target', preferred_weapon_ref='weapon_needle',
    )
    assert kind == 'hidden_weapon_throw'
    assert weapon == 'weapon_needle'


def test_explicit_technique_with_omitted_weapon_selects_only_compatible_carried_weapon():
    from shinobi_runtime.martial_world.exact_combat import default_weapon_for_action

    wei = _fighter('wei', 100, doctrine=True)
    ledger = _ledger()
    ledger['person_loadouts']['wei']['items']['weapon_jian'] = 1
    assert default_weapon_for_action(
        people={'wei': wei}, equipment_ledger=ledger, actor_ref='wei', action_kind='thrust',
    ) == 'weapon_jian'
    assert default_weapon_for_action(
        people={'wei': wei}, equipment_ledger=ledger, actor_ref='wei', action_kind='hidden_weapon_throw',
    ) == 'weapon_needle'
    assert default_weapon_for_action(
        people={'wei': wei}, equipment_ledger=ledger, actor_ref='wei', action_kind='unarmed_strike',
    ) == 'body_unarmed'
