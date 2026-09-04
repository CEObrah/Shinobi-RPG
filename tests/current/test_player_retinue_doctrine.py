from shinobi_runtime.combat.team_tactics import plan_player_retinue_exchange
from shinobi_runtime.martial_world.doctrines import resolve_player_retinue_doctrine


def _person(ref, sword=50):
    return {
        'person_id': ref,
        'attributes': {'speed': 60, 'dexterity': 60, 'perception': 60, 'endurance': 60, 'strength': 60},
        'martial_skills': {'sword': sword, 'unarmed': 30},
        'health': {'status': 'healthy', 'consciousness': 100},
    }


def _positions(refs):
    out = {}
    for i, ref in enumerate(refs):
        out[ref] = {'zone_ref': 'z', 'x_mm': i * 800, 'y_mm': 0, 'facing_mdeg': 0, 'body_radius_mm': 300}
    return out


def _plan(enemy_count):
    leader = 'wei'; companions = ['jiang', 'han', 'fu']; enemies = [f'e{i}' for i in range(enemy_count)]
    records = {leader: _person(leader, 100), 'jiang': _person('jiang', 70), 'han': _person('han', 55), 'fu': _person('fu', 50)}
    for i, ref in enumerate(enemies):
        records[ref] = _person(ref, 90 - i * 5)
    positions = _positions([leader, *companions, *enemies])
    plan = plan_player_retinue_exchange(
        side_ref='side_a', leader_ref=leader, permanent_member_refs=companions,
        member_roles={'jiang': 'protective_guard', 'han': 'field_medic', 'fu': 'scout'},
        known_enemy_refs=enemies, records=records, positions=positions,
        doctrine=resolve_player_retinue_doctrine('doctrine.player_retinue.tang_wei.personal_guard'), at_ms=0,
    )
    return plan


def test_retinue_guards_take_distinct_threats_before_principal_when_covered():
    plan = _plan(3)
    assert plan['assignments']['wei']['role'] == 'reserve'
    assert plan['assignments']['wei']['target_ref'] is None
    companion_targets = {plan['assignments'][ref]['target_ref'] for ref in ('jiang', 'han', 'fu')}
    assert len(companion_targets) == 3


def test_retinue_creates_local_numerical_superiority_when_guard_is_free():
    plan = _plan(2)
    targets = [plan['assignments'][ref]['target_ref'] for ref in ('jiang', 'han', 'fu')]
    assert len(set(targets)) == 2
    assert targets.count('e0') == 2
    assert 'defeat_in_detail' in plan['desired_states']


def test_retinue_uses_four_sector_back_to_back_when_outnumbered():
    plan = _plan(5)
    assert plan['formation_mode'] == 'four_sector_back_to_back'
    assert 'collapse_to_four_sector_back_to_back' in plan['desired_states']
    assert 'do_not_pursue_out_of_formation' in plan['desired_states']
    assert plan['assignments']['jiang']['preferred_action'] == 'hold'
    assert plan['assignments']['fu']['preferred_action'] == 'hold'
    assert plan['assignments']['han']['preferred_action'] == 'medical_support_hold'
    assert plan['doctrine_snapshot']['formation']['cohesion'] == 95


def test_retinue_template_values_are_behavior_authority_not_trace_only():
    doctrine = dict(resolve_player_retinue_doctrine('doctrine.player_retinue.tang_wei.personal_guard'))
    doctrine = {key: dict(value) for key, value in doctrine.items()}
    doctrine['allocation']['numerical_superiority_policy'] = 'defeat_in_detail'
    doctrine['formation']['break_for_pursuit'] = True
    leader = 'wei'; companions = ['jiang', 'han', 'fu']; enemies = [f'e{i}' for i in range(5)]
    records = {leader: _person(leader, 100), 'jiang': _person('jiang', 70), 'han': _person('han', 55), 'fu': _person('fu', 50)}
    for i, ref in enumerate(enemies):
        records[ref] = _person(ref, 90 - i * 5)
    plan = plan_player_retinue_exchange(
        side_ref='side_a', leader_ref=leader, permanent_member_refs=companions,
        member_roles={'jiang': 'protective_guard', 'han': 'field_medic', 'fu': 'scout'},
        known_enemy_refs=enemies, records=records, positions=_positions([leader, *companions, *enemies]),
        doctrine=doctrine, at_ms=0,
    )
    assert plan['formation_mode'] == 'four_sector_back_to_back'
    assert 'do_not_pursue_out_of_formation' not in plan['desired_states']
    assert plan['assignments']['jiang']['preferred_action'] == 'attack'
    assert plan['assignments']['fu']['preferred_action'] == 'attack'
    assert plan['assignments']['han']['preferred_action'] == 'medical_support_hold'
    assert plan['assignments']['han']['role'] == 'medical_support'
    assert plan['doctrine_snapshot']['formation']['break_for_pursuit'] is True


def test_retinue_protection_priority_controls_when_principal_screen_overflows():
    doctrine = {key: dict(value) for key, value in resolve_player_retinue_doctrine(
        'doctrine.player_retinue.tang_wei.personal_guard'
    ).items()}
    doctrine['principal']['protection_priority'] = 50
    leader = 'wei'; companions = ['jiang', 'han', 'fu']; enemies = ['e0', 'e1', 'e2']
    records = {leader: _person(leader, 100), **{ref: _person(ref, 60) for ref in companions}}
    records.update({ref: _person(ref, 80) for ref in enemies})
    plan = plan_player_retinue_exchange(
        side_ref='side_a', leader_ref=leader, permanent_member_refs=companions,
        member_roles={'jiang': 'protective_guard', 'han': 'field_medic', 'fu': 'scout'},
        known_enemy_refs=enemies, records=records, positions=_positions([leader, *companions, *enemies]),
        doctrine=doctrine, at_ms=0,
    )
    assert plan['assignments']['wei']['role'] == 'principal'
    assert plan['assignments']['wei']['target_ref'] is None


def test_retinue_cohesion_below_drilled_threshold_prevents_four_sector_formation():
    doctrine = {key: dict(value) for key, value in resolve_player_retinue_doctrine(
        'doctrine.player_retinue.tang_wei.personal_guard'
    ).items()}
    doctrine['formation']['cohesion'] = 69
    leader = 'wei'; companions = ['jiang', 'han', 'fu']; enemies = [f'e{i}' for i in range(5)]
    records = {leader: _person(leader, 100), **{ref: _person(ref, 60) for ref in companions}}
    records.update({ref: _person(ref, 80) for ref in enemies})
    plan = plan_player_retinue_exchange(
        side_ref='side_a', leader_ref=leader, permanent_member_refs=companions,
        member_roles={'jiang': 'protective_guard', 'han': 'field_medic', 'fu': 'scout'},
        known_enemy_refs=enemies, records=records, positions=_positions([leader, *companions, *enemies]),
        doctrine=doctrine, at_ms=0,
    )
    assert plan['formation_mode'] == 'screen_principal'
    assert 'collapse_to_four_sector_back_to_back' not in plan['desired_states']


def test_temporary_escort_does_not_inherit_retinue_coordination_by_default():
    doctrine = {key: dict(value) for key, value in resolve_player_retinue_doctrine(
        'doctrine.player_retinue.tang_wei.personal_guard'
    ).items()}
    leader = 'wei'; companions = ['jiang', 'han', 'fu']; temporary = ['temp.guard']; enemies = ['e0', 'e1']
    records = {leader: _person(leader, 100), **{ref: _person(ref, 60) for ref in [*companions, *temporary]}}
    records.update({ref: _person(ref, 80) for ref in enemies})
    plan = plan_player_retinue_exchange(
        side_ref='side_a', leader_ref=leader, permanent_member_refs=companions,
        temporary_member_refs=temporary,
        member_roles={'jiang': 'protective_guard', 'han': 'field_medic', 'fu': 'scout'},
        known_enemy_refs=enemies, records=records, positions=_positions([leader, *companions, *temporary, *enemies]),
        doctrine=doctrine, at_ms=0,
    )
    assert 'temp.guard' not in plan['core_member_refs']
    assert 'temp.guard' not in plan['assignments']


def test_temporary_escort_inherits_retinue_coordination_only_when_doctrine_enables_it():
    doctrine = {key: dict(value) for key, value in resolve_player_retinue_doctrine(
        'doctrine.player_retinue.tang_wei.personal_guard'
    ).items()}
    doctrine['temporary_members']['inherit_retinue_coordination'] = True
    leader = 'wei'; companions = ['jiang', 'han', 'fu']; temporary = ['temp.guard']; enemies = ['e0', 'e1']
    records = {leader: _person(leader, 100), **{ref: _person(ref, 60) for ref in [*companions, *temporary]}}
    records.update({ref: _person(ref, 80) for ref in enemies})
    plan = plan_player_retinue_exchange(
        side_ref='side_a', leader_ref=leader, permanent_member_refs=companions,
        temporary_member_refs=temporary,
        member_roles={'jiang': 'protective_guard', 'han': 'field_medic', 'fu': 'scout'},
        known_enemy_refs=enemies, records=records, positions=_positions([leader, *companions, *temporary, *enemies]),
        doctrine=doctrine, at_ms=0,
    )
    assert 'temp.guard' in plan['core_member_refs']
    assert 'temp.guard' in plan['assignments']


def test_retinue_rear_exposure_priority_is_causal_not_trace_only():
    doctrine = {key: dict(value) for key, value in resolve_player_retinue_doctrine(
        'doctrine.player_retinue.tang_wei.personal_guard'
    ).items()}
    doctrine['formation']['rear_exposure_priority'] = 40
    leader = 'wei'; companions = ['jiang', 'han', 'fu']; enemies = [f'e{i}' for i in range(5)]
    records = {leader: _person(leader, 100), **{ref: _person(ref, 60) for ref in companions}}
    records.update({ref: _person(ref, 80) for ref in enemies})
    plan = plan_player_retinue_exchange(
        side_ref='side_a', leader_ref=leader, permanent_member_refs=companions,
        member_roles={'jiang': 'protective_guard', 'han': 'field_medic', 'fu': 'scout'},
        known_enemy_refs=enemies, records=records, positions=_positions([leader, *companions, *enemies]),
        doctrine=doctrine, at_ms=0,
    )
    assert plan['formation_mode'] == 'screen_principal'
    assert 'collapse_to_four_sector_back_to_back' not in plan['desired_states']
    assert 'avoid_rear_exposure' not in plan['desired_states']


def test_retinue_no_contact_plan_preserves_doctrine_snapshot_without_runtime_name_errors():
    doctrine = {key: dict(value) for key, value in resolve_player_retinue_doctrine(
        'doctrine.player_retinue.tang_wei.personal_guard'
    ).items()}
    leader = 'wei'; companions = ['jiang', 'han', 'fu']
    records = {leader: _person(leader, 100), **{ref: _person(ref, 60) for ref in companions}}
    plan = plan_player_retinue_exchange(
        side_ref='side_a', leader_ref=leader, permanent_member_refs=companions,
        member_roles={'jiang': 'protective_guard', 'han': 'field_medic', 'fu': 'scout'},
        known_enemy_refs=[], records=records, positions=_positions([leader, *companions]),
        doctrine=doctrine, at_ms=0,
    )
    assert plan['known_enemy_refs'] == []
    assert plan['assignments'] == {}
    assert plan['doctrine_snapshot']['principal']['protection_priority'] == doctrine['principal']['protection_priority']
