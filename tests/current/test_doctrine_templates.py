import json
from pathlib import Path

import pytest

from shinobi_runtime.martial_world.doctrines import (
    FACTION_TEAM_FIELDS,
    doctrine_registry,
    resolve_individual_doctrine,
    resolve_player_retinue_doctrine,
    validate_faction_doctrine,
    validate_individual_doctrine,
    validate_player_retinue_doctrine,
)

ROOT = Path(__file__).resolve().parents[2]


def test_combat_doctrine_registry_has_only_bespoke_player_retinue_team_layer():
    registry = doctrine_registry()
    assert registry['schema'] == 'jianghu-combat-doctrines-3.0'
    assert 'team_templates' not in registry
    assert set(registry['player_retinue_templates']) == {'doctrine.player_retinue.tang_wei.personal_guard'}
    team = resolve_player_retinue_doctrine('doctrine.player_retinue.tang_wei.personal_guard')
    assert team['principal']['protection_priority'] == 100
    assert team['principal']['strongest_enemy_policy'] == 'never_forced'
    assert team['allocation']['numerical_superiority_policy'] == 'defeat_in_detail'
    assert team['formation']['encirclement_response'] == 'back_to_back_outward_sectors'
    assert team['formation']['cohesion'] == 95
    assert team['temporary_members']['inherit_retinue_coordination'] is False


def test_wei_individual_doctrine_is_closed_deterministic_behavior_data():
    wei = resolve_individual_doctrine('doctrine.tang_wei.precision_function_denial')
    assert wei['engagement']['movement_economy'] == 'minimal_required'
    assert wei['engagement']['finishing_window'] == 'commit_decisively'
    assert wei['resource_discipline']['fatigue_reserve'] == 85
    assert wei['force_policy']['lethal_attack'] == 'lethal'
    assert wei['force_policy']['formal_spar'] == 'disable'
    assert wei['targeting']['disable_priority'][0] == 'wrist'
    assert set(wei['targeting']['disable_priority']).isdisjoint({'eye', 'neck_vital', 'axilla', 'upper_torso_vital'})
    assert {'eye', 'neck_vital'} <= set(wei['targeting']['lethal_priority'])
    assert 'principle' not in wei and 'selection_rule' not in wei and 'purpose' not in json.dumps(wei)


def test_player_retinue_doctrine_rejects_free_text_or_shape_bloat():
    team = resolve_player_retinue_doctrine('doctrine.player_retinue.tang_wei.personal_guard')
    bad = dict(team); bad['random_prose'] = 'fight heroically'
    with pytest.raises(ValueError):
        validate_player_retinue_doctrine(bad)


def test_faction_doctrine_rejects_unknown_or_missing_behavior_fields():
    seed = json.loads((ROOT / 'game/data/martial-world/world-seed.json').read_text())
    good = seed['martial_factions']['house_tang']['doctrine']
    assert validate_faction_doctrine(good) == good
    bad = dict(good); bad['random_prose'] = 50
    with pytest.raises(ValueError):
        validate_faction_doctrine(bad)
    missing = dict(good); missing.pop('mutual_support')
    with pytest.raises(ValueError):
        validate_faction_doctrine(missing)


def test_individual_doctrine_rejects_free_form_behavior_text():
    good = resolve_individual_doctrine('doctrine.tang_wei.precision_function_denial')
    assert validate_individual_doctrine(good) == good
    invalid = dict(good)
    invalid['engagement'] = {'range_preference': 'I circle dramatically and improvise'}
    with pytest.raises(ValueError):
        validate_individual_doctrine(invalid)


def test_all_authored_faction_doctrines_conform_to_closed_numeric_shape():
    seed = json.loads((ROOT / 'game/data/martial-world/world-seed.json').read_text())
    factions = seed['martial_factions']
    assert len(factions) >= 200
    for ref, row in factions.items():
        doctrine = row.get('doctrine')
        assert isinstance(doctrine, dict), ref
        assert tuple(validate_faction_doctrine(doctrine)) == FACTION_TEAM_FIELDS


def test_wei_force_policy_is_closed_and_contextual():
    from shinobi_runtime.martial_world.doctrines import resolve_force_intent
    wei = resolve_individual_doctrine('doctrine.tang_wei.precision_function_denial')
    assert resolve_force_intent(wei, 'formal_spar') == 'disable'
    assert resolve_force_intent(wei, 'tournament_nonlethal') == 'disable'
    assert resolve_force_intent(wei, 'capture_objective') == 'disable'
    assert resolve_force_intent(wei, 'ambush') == 'lethal'
    assert resolve_force_intent(wei, 'lethal_attack') == 'lethal'
    assert resolve_force_intent(wei, 'battlefield') == 'lethal'


def test_faction_doctrine_changes_small_team_role_mix_instead_of_trace_only_scores():
    from shinobi_runtime.combat.team_tactics import _doctrine_role_sequence

    fields = {key: 0 for key in FACTION_TEAM_FIELDS}
    aggressive = dict(fields)
    aggressive.update({
        'offensive_pressure': 100,
        'close_combat_emphasis': 100,
        'concentration_of_force': 100,
        'pursuit': 100,
        'individual_initiative': 100,
    })
    preserving = dict(fields)
    preserving.update({
        'defensive_caution': 100,
        'mutual_support': 100,
        'reserve_preference': 100,
        'withdrawal_discipline': 100,
        'casualty_preservation': 100,
        'formation_cohesion': 100,
    })

    aggressive_roles = _doctrine_role_sequence('multiple_threats', 3, aggressive)
    preserving_roles = _doctrine_role_sequence('multiple_threats', 3, preserving)

    assert aggressive_roles != preserving_roles
    assert 'reserve' not in aggressive_roles
    assert 'reserve' in preserving_roles


def test_coordination_latency_gates_shared_assignment_without_delaying_physical_action():
    from shinobi_runtime.martial_world.exact_combat import _ready_team_assignment

    plan = {
        'generated_at_ms': 1_000,
        'coordination_latency_ms': 450,
        'assignments': {
            'person.a': {'role': 'flank', 'target_ref': 'person.enemy', 'preferred_action': 'attack'},
        },
    }

    assert _ready_team_assignment(plan, 'person.a', at_ms=1_449) == {}
    assert _ready_team_assignment(plan, 'person.a', at_ms=1_450) == plan['assignments']['person.a']


def test_faction_lethality_threshold_changes_npc_force_intent_without_overriding_personal_policy():
    from shinobi_runtime.martial_world.exact_combat import combat_default_targeting_intent

    hostile = {'objective': {'kind': 'eliminate'}, 'awareness_mode': 'mutual'}
    capture = {'objective': {'kind': 'capture'}, 'awareness_mode': 'mutual'}

    assert combat_default_targeting_intent(hostile, faction_doctrine={'lethality_threshold': 30}) == 'disable'
    assert combat_default_targeting_intent(hostile, faction_doctrine={'lethality_threshold': 85}) == 'lethal'
    assert combat_default_targeting_intent(capture, faction_doctrine={'lethality_threshold': 100}) == 'disable'
    assert combat_default_targeting_intent(
        hostile,
        doctrine_ref='doctrine.tang_wei.precision_function_denial',
        faction_doctrine={'lethality_threshold': 0},
    ) == 'lethal'


def test_individual_defense_doctrine_projects_into_automatic_physical_response(monkeypatch):
    import copy
    import shinobi_runtime.martial_world.exact_combat as exact

    roster = json.loads((ROOT / 'state/martial-world/people/house_tang.json').read_text())['people']
    person = copy.deepcopy(roster[0])
    person['combat_doctrine_ref'] = 'test.doctrine'
    ledger = json.loads((ROOT / 'state/martial-world/equipment-ledger.json').read_text())

    monkeypatch.setattr(exact, 'resolve_individual_doctrine', lambda _ref: {
        'defense': {'primary_response': 'block', 'counterattack_posture': 'rare'},
    })
    participant = exact._participant(
        person['person_id'], person, side_ref='side_a',
        position={'zone_ref': 'test', 'x_mm': 0, 'y_mm': 0, 'facing_mdeg': 0, 'body_radius_mm': 300},
        known_refs=['enemy'], combatant_state={'status_families': ['immobilized']},
        action_profile=None, equipment_ledger=ledger, at_ms=0, intent='hold',
    )
    assert participant.physical_defense_preferences[:2] == ('block', 'brace')
    assert participant.counterattack_posture == 'rare'


def test_counterattack_posture_changes_automatic_defensive_choice():
    from shinobi_runtime.combat.models import (
        ActionProfile, CapabilityProfile, CombatIntent, InformationState,
        Participant, PersonnelState, PositionState, ReactiveDefense,
    )
    from shinobi_runtime.combat.physical_defense import select_physical_defense

    cap = CapabilityProfile(
        offense=100, defense=100, control=100, mobility=50, perception=100,
        stealth=0, capture=50, escape=50, reaction=100,
    )
    attacker_pos = PositionState(zone_ref='z', x_mm=0, y_mm=0, facing_mdeg=0)
    defender_pos = PositionState(zone_ref='z', x_mm=900, y_mm=0, facing_mdeg=180000)
    profile = ActionProfile(
        method_ref='thrust', effect_kind='physical', delivery='direct', startup_ms=400,
        external_contact=True, speed_score=100, effect_parameters={'physical_reach_m': 2.0},
    )

    def participant(ref, position, *, posture='selective'):
        return Participant(
            participant_ref=ref, authoritative_owner_ref=ref, side_ref=ref,
            sequence=0, representation='exact', capability=cap,
            personnel=PersonnelState(total=1, active=1), position=position,
            information=InformationState(observed_refs=('attacker', 'defender')),
            intent=CombatIntent(action='attack'), initiative=100, readiness=100,
            morale=100, cohesion=100, action_profile=profile,
            reactive_defenses=(ReactiveDefense(defense_ref='weapon', defense_kind='weapon_guard'),),
            physical_defense_preferences=('evade','reposition','parry','deflect','block','counter_intercept','brace'),
            counterattack_posture=posture,
        )

    attacker = participant('attacker', attacker_pos)
    def choose(posture):
        defender = participant('defender', defender_pos, posture=posture)
        return select_physical_defense(
            attacker=attacker, defender=defender,
            attacker_position=attacker_pos, defender_position=defender_pos,
            attacker_capability=cap, defender_capability=cap, profile=profile,
            line_of_sight=True,
            participant_positions={'attacker': attacker_pos.to_record(), 'defender': defender_pos.to_record()},
            body_refs=['attacker', 'defender'], obstacles=[], at_ms=0,
        ).response

    assert choose('rare') == 'parry'
    assert choose('active') == 'counter_intercept'


def _doctrine_test_person(ref='actor'):
    return {
        'person_id': ref,
        'attributes': {
            'strength': 90, 'speed': 90, 'dexterity': 90, 'perception': 90,
            'endurance': 90, 'intelligence': 80, 'willpower': 90,
        },
        'martial_skills': {'sword': 100, 'unarmed': 70, 'hidden_weapons': 80},
        'qi': 0, 'qi_control': 0, 'current_qi': 0, 'current_qi_milli': 0,
        'health': {'status': 'healthy', 'consciousness': 100, 'blood_lost_ml': 0, 'injuries': []},
        'combat_doctrine_ref': 'test.doctrine',
    }


def _doctrine_test_ledger():
    return {
        'schema': 'jianghu-equipment-ledger-1.0',
        'policy_assignments': {},
        'person_loadouts': {
            'actor': {'items': {'weapon_jian': 1, 'weapon_needle': 8}, 'condition_milli': {}},
        },
    }


def test_engagement_range_and_movement_economy_change_default_physical_action(monkeypatch):
    import shinobi_runtime.martial_world.exact_combat as exact

    actor = _doctrine_test_person()
    target = _doctrine_test_person('target')
    target.pop('combat_doctrine_ref', None)
    people = {'actor': actor, 'target': target}
    combat = {
        'positions': {
            'actor': {'x_mm': 0, 'y_mm': 0},
            'target': {'x_mm': 3000, 'y_mm': 0},
        },
    }
    ledger = _doctrine_test_ledger()

    def doctrine(range_preference, movement_economy):
        return {'engagement': {
            'range_preference': range_preference,
            'movement_economy': movement_economy,
        }}

    monkeypatch.setattr(exact, 'resolve_individual_doctrine', lambda _ref: doctrine('ranged', 'balanced'))
    assert exact.default_action_for(
        combat=combat, people=people, equipment_ledger=ledger,
        actor_ref='actor', target_ref='target',
    ) == ('hidden_weapon_throw', 'weapon_needle')

    monkeypatch.setattr(exact, 'resolve_individual_doctrine', lambda _ref: doctrine('close', 'balanced'))
    assert exact.default_action_for(
        combat=combat, people=people, equipment_ledger=ledger,
        actor_ref='actor', target_ref='target',
    )[0] in {'cut', 'thrust'}

    combat['positions']['target']['x_mm'] = 2600
    monkeypatch.setattr(exact, 'resolve_individual_doctrine', lambda _ref: doctrine('adaptive', 'minimal_required'))
    assert exact.default_action_for(
        combat=combat, people=people, equipment_ledger=ledger,
        actor_ref='actor', target_ref='target',
    )[0] == 'hidden_weapon_throw'
    monkeypatch.setattr(exact, 'resolve_individual_doctrine', lambda _ref: doctrine('adaptive', 'mobile'))
    assert exact.default_action_for(
        combat=combat, people=people, equipment_ledger=ledger,
        actor_ref='actor', target_ref='target',
    )[0] in {'cut', 'thrust'}


def test_engagement_initiative_commitment_and_fatigue_reserve_change_schedule(monkeypatch):
    import copy
    import shinobi_runtime.martial_world.exact_combat as exact

    actor = _doctrine_test_person()
    target = _doctrine_test_person('target')
    target.pop('combat_doctrine_ref', None)
    people = {'actor': actor, 'target': target}
    ledger = _doctrine_test_ledger()
    current = {'engagement': {'initiative_posture': 'reactive', 'commitment_posture': 'measured', 'finishing_window': 'cautious'},
               'resource_discipline': {'fatigue_reserve': 85}}
    monkeypatch.setattr(exact, 'resolve_individual_doctrine', lambda _ref: current)
    combat = exact.initialize_combat(
        combat_ref='doctrine-schedule', side_a_refs=['actor'], side_b_refs=['target'],
        people=people, zone_ref='z', started_at='x', objective={'kind': 'eliminate'},
        equipment_ledger=ledger, initial_range_band=1,
    )

    measured = exact._schedule_action(
        combat=combat, actor_ref='actor', target_ref='target', action_kind='thrust',
        weapon_ref='weapon_jian', poison_ref=None, hit_zone='chest', target_structure_ref=None,
        decision_origin='test', people=people, equipment_ledger=ledger,
    )

    current['engagement'] = {'initiative_posture': 'assertive', 'commitment_posture': 'committed', 'finishing_window': 'cautious'}
    committed = exact._schedule_action(
        combat=combat, actor_ref='actor', target_ref='target', action_kind='thrust',
        weapon_ref='weapon_jian', poison_ref=None, hit_zone='chest', target_structure_ref=None,
        decision_origin='test', people=people, equipment_ledger=ledger,
    )
    assert committed.start_at_ms < measured.start_at_ms
    assert committed.profile.effect_parameters['commitment_milli'] > measured.profile.effect_parameters['commitment_milli']

    tired_people = copy.deepcopy(people)
    tired_people['actor']['fatigue_milli'] = 1500
    tired = exact._schedule_action(
        combat=combat, actor_ref='actor', target_ref='target', action_kind='thrust',
        weapon_ref='weapon_jian', poison_ref=None, hit_zone='chest', target_structure_ref=None,
        decision_origin='test', people=tired_people, equipment_ledger=ledger,
    )
    assert tired.profile.effect_parameters['commitment_milli'] < committed.profile.effect_parameters['commitment_milli']


def test_engagement_pursuit_and_finishing_window_change_default_target(monkeypatch):
    import copy
    import shinobi_runtime.martial_world.exact_combat as exact

    actor = _doctrine_test_person()
    fleeing = _doctrine_test_person('fleeing')
    steady = _doctrine_test_person('steady')
    for row in (fleeing, steady):
        row.pop('combat_doctrine_ref', None)
    people = {'actor': actor, 'fleeing': fleeing, 'steady': steady}
    ledger = _doctrine_test_ledger()
    current = {'engagement': {'pursuit_posture': 'restrained', 'finishing_window': 'cautious'}}
    monkeypatch.setattr(exact, 'resolve_individual_doctrine', lambda _ref: current)
    combat = exact.initialize_combat(
        combat_ref='doctrine-target', side_a_refs=['actor'], side_b_refs=['fleeing', 'steady'],
        people=people, zone_ref='z', started_at='x', objective={'kind': 'eliminate'},
        equipment_ledger=ledger, initial_range_band=1,
    )
    combat['positions']['actor'].update(x_mm=0, y_mm=0)
    combat['positions']['fleeing'].update(x_mm=1500, y_mm=0, stance='disengaging')
    combat['positions']['steady'].update(x_mm=-1500, y_mm=0, stance='ready')
    monkeypatch.setattr(exact, '_observe_visible_enemies', lambda combat, actor_ref, enemy_refs, people, at_ms: list(enemy_refs))

    assert exact.default_target_for(combat=combat, people=people, actor_ref='actor') == 'steady'

    current['engagement'] = {'pursuit_posture': 'persistent', 'finishing_window': 'cautious'}
    assert exact.default_target_for(combat=combat, people=people, actor_ref='actor') == 'fleeing'

    combat['positions']['fleeing']['stance'] = 'ready'
    wounded_people = copy.deepcopy(people)
    wounded_people['steady']['health']['blood_lost_ml'] = 500
    current['engagement'] = {'pursuit_posture': 'balanced', 'finishing_window': 'commit_decisively'}
    assert exact.default_target_for(combat=combat, people=wounded_people, actor_ref='actor') == 'steady'
