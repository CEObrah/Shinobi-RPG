import copy
import json
from pathlib import Path

from shinobi_runtime.combat.models import CapabilityProfile
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.jianghu_extended import JianghuExtendedCommandsMixin
from shinobi_runtime.martial_world.combat import allocate_qi
from shinobi_runtime.martial_world.exact_combat import (
    _contact_damage,
    _npc_qi_allocation,
    _npc_qi_reserve_milli,
    _precision_margin,
    _qi_channel_effect_milli,
    _qi_effect,
    _qi_enhanced_capability,
    initialize_combat,
    resolve_exchange,
)
from shinobi_runtime.martial_world.qi import person_current_qi_milli
from shinobi_runtime.sim.events import CampaignTime

ROOT = Path(__file__).resolve().parents[2]


def load(rel):
    return json.loads((ROOT / rel).read_text())


def test_allocate_qi_zero_resource_delivers_no_effect_flow():
    result = allocate_qi(
        qi=100,
        qi_control=100,
        current_qi_milli=0,
        allocations_milli={"body": 600, "movement": 400},
        duration_ms=1000,
    )
    assert result["requested_flow_milli_per_second"] == 1000
    assert result["delivered_flow_milli_per_second"] == 0
    assert result["current_qi_milli_spent"] == 0
    assert result["allocations_milli"] == {}
    assert result["requested_allocations_milli"] == {"body": 600, "movement": 400}
    assert result["resource_limited"] is True


def test_allocate_qi_partial_resource_scales_every_delivered_channel():
    result = allocate_qi(
        qi=100,
        qi_control=100,
        current_qi_milli=250,
        allocations_milli={"body": 600, "movement": 400},
        duration_ms=1000,
    )
    assert result["current_qi_milli_spent"] == 250
    assert result["delivered_flow_milli_per_second"] == 250
    assert sum(result["allocations_milli"].values()) == 250
    assert result["allocations_milli"]["body"] == 150
    assert result["allocations_milli"]["movement"] == 100
    assert all(result["allocations_milli"][k] <= result["requested_allocations_milli"][k] for k in result["allocations_milli"])


def test_qi_channels_have_distinct_physical_effects_only_when_delivered():
    base = CapabilityProfile(offense=100, defense=100, control=100, mobility=100, perception=100, stealth=100, capture=100, escape=100, reaction=100)
    delivered = {
        "allocations_milli": {"movement": 350, "body": 350, "sensing": 300},
        "control_efficiency_milli": 800,
    }
    enhanced = _qi_enhanced_capability(base, delivered)
    assert enhanced.mobility > base.mobility
    assert enhanced.escape > base.escape
    assert enhanced.offense > base.offense
    assert enhanced.defense > base.defense
    assert enhanced.perception > base.perception
    assert enhanced.reaction > base.reaction
    empty = {"allocations_milli": {}, "requested_allocations_milli": {"movement": 1000}, "control_efficiency_milli": 1000}
    assert _qi_enhanced_capability(base, empty) == base


def test_sensing_qi_improves_precision_from_delivered_flow_not_requested_flow():
    actor = {"attributes": {"dexterity": 60, "perception": 60, "intelligence": 60}, "martial_skills": {"unarmed": 60}}
    target = {"attributes": {"strength": 40, "dexterity": 40, "endurance": 40, "perception": 40}, "martial_skills": {}}
    plain = _precision_margin(actor=actor, weapon=None, action_kind="unarmed_strike", structure_ref=None, hit_zone="chest", distance_m=0.5, target=target, visibility_milli=1000)
    delivered = _precision_margin(actor=actor, weapon=None, action_kind="unarmed_strike", structure_ref=None, hit_zone="chest", distance_m=0.5, target=target, visibility_milli=1000, qi_result={"allocations_milli": {"sensing": 500}, "control_efficiency_milli": 800})
    requested_only = _precision_margin(actor=actor, weapon=None, action_kind="unarmed_strike", structure_ref=None, hit_zone="chest", distance_m=0.5, target=target, visibility_milli=1000, qi_result={"allocations_milli": {}, "requested_allocations_milli": {"sensing": 500}, "control_efficiency_milli": 800})
    assert delivered > plain
    assert requested_only == plain


def test_body_qi_changes_contact_only_through_boosted_ordinary_attributes():
    actor = {"attributes": {"strength": 100, "dexterity": 100}, "martial_skills": {"unarmed": 100}}
    defender = {"attributes": {"endurance": 50}, "martial_skills": {}}
    plain = _contact_damage(actor=copy.deepcopy(actor), defender=copy.deepcopy(defender), weapon=None, weapon_ref="body_unarmed", action_kind="unarmed_strike", range_m=0.5, defense_force_milli=1000, hit_zone="chest", target_structure_ref=None, created_at="x")
    body = _contact_damage(actor=copy.deepcopy(actor), defender=copy.deepcopy(defender), weapon=None, weapon_ref="body_unarmed", action_kind="unarmed_strike", range_m=0.5, defense_force_milli=1000, hit_zone="chest", target_structure_ref=None, created_at="x", qi_result={"allocations_milli": {"body": 500}, "control_efficiency_milli": 800})
    requested_only = _contact_damage(actor=copy.deepcopy(actor), defender=copy.deepcopy(defender), weapon=None, weapon_ref="body_unarmed", action_kind="unarmed_strike", range_m=0.5, defense_force_milli=1000, hit_zone="chest", target_structure_ref=None, created_at="x", qi_result={"allocations_milli": {}, "requested_allocations_milli": {"body": 500}, "control_efficiency_milli": 800})
    assert body["transmitted_channels"]["blunt"] > plain["transmitted_channels"]["blunt"]
    assert requested_only["transmitted_channels"] == plain["transmitted_channels"]


def test_npc_exact_combat_allocates_and_spends_real_qi():
    roster = load("state/martial-world/people/house_tang.json")["people"]
    player = copy.deepcopy(roster[0])
    npc = copy.deepcopy(roster[3])
    people = {player["person_id"]: player, npc["person_id"]: npc}
    ledger = {"schema": "jianghu-equipment-ledger-1.0", "policy_assignments": {}, "person_loadouts": {}}
    combat = initialize_combat(
        combat_ref="npc-qi",
        side_a_refs=[player["person_id"]],
        side_b_refs=[npc["person_id"]],
        people=people,
        zone_ref="site.house_tang",
        started_at="x",
        objective={"kind": "eliminate", "target_refs": [npc["person_id"]]},
        equipment_ledger=ledger,
    )
    expected = _npc_qi_allocation(npc, {})
    assert expected
    before = person_current_qi_milli(npc)
    result = resolve_exchange(
        combat=combat,
        people=people,
        equipment_ledger=ledger,
        doctrines={},
        player_ref=player["person_id"],
        player_action_kind="unarmed_strike",
        player_target_ref=npc["person_id"],
        player_weapon_ref="body_unarmed",
        player_hit_zone="chest",
        player_targeting_intent="disable",
    )
    assert result["combat_after"]["combatants"][npc["person_id"]]["qi_allocation_milli"]
    assert person_current_qi_milli(result["people_after"][npc["person_id"]]) < before


class _QiPurgeHarness(JianghuExtendedCommandsMixin):
    def __init__(self, person):
        self.path = "state/test-roster.json"
        self.roster = {"schema": "jianghu-faction-roster-1.0", "faction_ref": "faction.test", "people": [copy.deepcopy(person)]}

    def _person(self, person_ref):
        person = self.roster["people"][0]
        assert person["person_id"] == person_ref
        return self.path, copy.deepcopy(self.roster), 0, copy.deepcopy(person)

    def _require_person_available_for_activity(self, _person_ref):
        return None

    def _timed_person_activity_plan(self, command, meta, current_time, **_kwargs):
        return {"kind": "test-time-plan"}, {self.path: copy.deepcopy(self.roster)}, current_time

    def _combine_time_plan(self, command, time_plan, *, extra_records, code, result):
        return {"records": extra_records, "code": code, "result": result}


def test_qi_purge_updates_authoritative_milli_qi_not_only_coarse_projection():
    person = {
        "person_id": "pc.test",
        "qi": 100,
        "qi_control": 80,
        "current_qi": 50,
        "current_qi_milli": 50_750,
        "poison_burdens": {"cardiotoxic": 100},
        "health": {"status": "ready", "injuries": []},
        "location_ref": "site.test",
    }
    harness = _QiPurgeHarness(person)
    command = CommandEnvelope(
        campaign_id="test",
        request_id="qi-purge-milli",
        actor_id="pc.test",
        command_type="jianghu_medicine_resolution",
        expected_revision=1,
        submitted_at="2026-08-27T00:00:00Z",
        payload={"action": "qi_purge", "subject_ref": "pc.test", "poison_ref": "cardiotoxic", "duration_minutes": 30},
        mode="gameplay",
    )
    result = harness._jianghu_medicine_resolution(command, {}, CampaignTime.parse("SE-0061-01-01T00:00:00"))
    after = result["records"][harness.path]["people"][0]
    assert result["result"]["qi_spent"] > 0
    assert after["current_qi_milli"] < person["current_qi_milli"]
    assert after["current_qi"] == after["current_qi_milli"] // 1000


def test_lodging_rest_recovers_from_authoritative_fractional_qi_pool():
    from shinobi_runtime.martial_world.escort_living_world import apply_lodging_rest

    person = {
        'qi': 100,
        'qi_control': 80,
        'current_qi': 50,
        'current_qi_milli': 50_750,
        'fatigue_milli': 200,
        'health': {'status': 'ready'},
    }
    rested = apply_lodging_rest(person, elapsed_hours=1)

    assert rested['current_qi_milli'] > 50_750
    assert rested['current_qi'] == rested['current_qi_milli'] // 1000


def test_field_qi_growth_preserves_fractional_deficit_against_new_capacity():
    from shinobi_runtime.martial_world.field_development import _write_points

    person = {'qi': 100, 'current_qi': 50, 'current_qi_milli': 50_750}
    _write_points(person, 'qi', 2)

    assert person['qi'] == 102
    assert person['current_qi_milli'] == 52_750
    assert person['current_qi'] == 52


def test_institutional_qi_growth_preserves_fractional_authoritative_pool():
    from shinobi_runtime.martial_world.training import _apply_one_gain

    person = {
        'birth_year': 30,
        'qi': 100,
        'qi_control': 80,
        'current_qi': 50,
        'current_qi_milli': 50_750,
        'aptitudes': {'qi': 120},
        'martial_skills': {},
        'attributes': {},
        'professional_skills': {},
    }
    residual = {'qi': 999}
    _apply_one_gain(
        person,
        domain='qi',
        hours_milli=10_000,
        segment={'started_at': '0061-01-01T00:00:00', 'facilities': {}},
        residual=residual,
        evidence={},
        health=1000,
    )

    assert person['qi'] > 100
    gained = person['qi'] - 100
    assert person['current_qi_milli'] == 50_750 + gained * 1000
    assert person['current_qi'] == person['current_qi_milli'] // 1000


def test_player_view_projects_fractional_authoritative_qi_not_stale_coarse_shadow():
    from shinobi_runtime.martial_world.live_state import player_view_from_person

    person = {
        'person_id': 'pc.test',
        'qi': 100,
        'qi_control': 80,
        'current_qi': 90,
        'current_qi_milli': 50_750,
    }
    view = player_view_from_person(person)

    assert view['current_qi_milli'] == 50_750
    assert view['current_qi'] == 50


def test_qi_purge_can_clear_pending_pre_onset_poison_without_destroying_clock():
    from shinobi_runtime.martial_world.poison import pending_poison_burden

    person = {
        'person_id': 'pc.test',
        'qi': 100,
        'qi_control': 80,
        'current_qi': 100,
        'pending_poison_burdens': {
            'cardiotoxic': {
                'poison_ref': 'cardiotoxic', 'burden': 40,
                'activates_at': '0061-01-01T00:00:25',
                'peaks_at': '0061-01-01T00:02:00', 'stage': 'onset',
            },
        },
        'health': {'status': 'ready', 'injuries': []},
        'location_ref': 'site.test',
    }
    harness = _QiPurgeHarness(person)
    command = CommandEnvelope(
        campaign_id='test', request_id='qi-purge-pending', actor_id='pc.test',
        command_type='jianghu_medicine_resolution', expected_revision=1,
        submitted_at='2026-08-27T00:00:00Z',
        payload={'action': 'qi_purge', 'subject_ref': 'pc.test', 'poison_ref': 'cardiotoxic', 'duration_minutes': 60},
        mode='gameplay',
    )
    result = harness._jianghu_medicine_resolution(command, {}, CampaignTime.parse('SE-0061-01-01T00:00:00'))
    after = result['records'][harness.path]['people'][0]

    remaining = pending_poison_burden(after.get('pending_poison_burdens', {}), 'cardiotoxic')
    assert 0 <= remaining < 40
    if remaining:
        row = after['pending_poison_burdens']['cardiotoxic']
        assert row['activates_at'] == '0061-01-01T00:00:25'
        assert row['peaks_at'] == '0061-01-01T00:02:00'


def test_simultaneous_defenses_share_one_authoritative_qi_clock_interval(monkeypatch):
    import shinobi_runtime.martial_world.exact_combat as exact

    base = load('state/martial-world/people/house_tang.json')['people'][0]

    def run(attacker_count: int) -> tuple[int, int]:
        defender = copy.deepcopy(base)
        defender['person_id'] = 'defender'
        defender['qi'] = 100
        defender['qi_control'] = 100
        defender['current_qi'] = 100
        defender.pop('current_qi_milli', None)
        defender['attributes'] = {key: 100 for key in defender['attributes']}
        defender['martial_skills'] = {key: 100 for key in defender['martial_skills']}
        attackers = []
        for idx in range(attacker_count):
            row = copy.deepcopy(base)
            row['person_id'] = f'attacker.{idx}'
            row['qi'] = 0
            row['qi_control'] = 0
            row['current_qi'] = 0
            row['attributes'] = {key: 40 for key in row['attributes']}
            row['martial_skills'] = {key: 40 for key in row['martial_skills']}
            attackers.append(row)
        people = {row['person_id']: row for row in [*attackers, defender]}
        ledger = {'schema': 'jianghu-equipment-ledger-1.0', 'policy_assignments': {}, 'person_loadouts': {}}
        combat = initialize_combat(
            combat_ref=f'qi-simultaneous-{attacker_count}',
            side_a_refs=[row['person_id'] for row in attackers], side_b_refs=['defender'],
            people=people, zone_ref='site.house_tang', started_at='x',
            objective={'kind': 'eliminate', 'target_refs': ['defender']}, equipment_ledger=ledger,
        )
        combat['positions']['defender'].update(x_mm=0, y_mm=0)
        positions = [(900, 0), (0, 900), (-900, 0)]
        for idx, attacker in enumerate(attackers):
            combat['positions'][attacker['person_id']].update(x_mm=positions[idx][0], y_mm=positions[idx][1])
        result = resolve_exchange(
            combat=combat, people=people, equipment_ledger=ledger, doctrines={},
            player_ref='attacker.0', player_action_kind='unarmed_strike',
            player_target_ref='defender', player_weapon_ref='body_unarmed',
            player_hit_zone='chest', player_targeting_intent='disable',
        )
        return result['combat_after']['elapsed_ms'], person_current_qi_milli(result['people_after']['defender'])

    original_observe = exact._observe_visible_enemies
    original_visible = exact._currently_visible_enemies
    monkeypatch.setattr(
        exact, '_observe_visible_enemies',
        lambda combat, actor_ref, enemy_refs, people, at_ms: [] if actor_ref == 'defender' else original_observe(
            combat, actor_ref=actor_ref, enemy_refs=enemy_refs, people=people, at_ms=at_ms,
        ),
    )
    monkeypatch.setattr(
        exact, '_currently_visible_enemies',
        lambda combat, actor_ref, enemy_refs, people: [] if actor_ref == 'defender' else original_visible(
            combat, actor_ref=actor_ref, enemy_refs=enemy_refs, people=people,
        ),
    )
    one_elapsed, one_qi = run(1)
    three_elapsed, three_qi = run(3)

    assert one_elapsed == three_elapsed
    assert one_qi == three_qi


def test_npc_qi_conservation_reserve_is_a_hard_spend_floor():
    person = copy.deepcopy(load('state/martial-world/people/house_tang.json')['people'][0])
    person['qi'] = 100
    person['qi_control'] = 100
    person['current_qi'] = 100
    person.pop('current_qi_milli', None)
    person.pop('combat_doctrine_ref', None)
    doctrine = {'qi_conservation': 75}
    reserve = _npc_qi_reserve_milli(person, doctrine)
    allocation = _npc_qi_allocation(person, doctrine)
    assert reserve == 75_000
    assert allocation

    state = {'qi_allocation_milli': allocation, 'qi_reserve_milli': reserve}
    result = _qi_effect(person=person, combatant_state=state, duration_ms=600_000)

    assert person_current_qi_milli(person) == reserve
    assert result['current_qi_milli_after'] == reserve
    assert result['qi_reserve_milli'] == reserve
    assert result['resource_limited'] is True

    second = _qi_effect(person=person, combatant_state=state, duration_ms=60_000)
    assert second['current_qi_milli_spent'] == 0
    assert person_current_qi_milli(person) == reserve


def test_low_qi_flow_survives_one_millisecond_preview_instead_of_quantizing_to_zero():
    result = allocate_qi(
        qi=125,
        qi_control=35,
        current_qi_milli=125_000,
        allocations_milli={'movement': 75, 'body': 75, 'sensing': 49},
        duration_ms=1,
    )
    assert result['requested_flow_milli_per_second'] == 199
    assert result['delivered_flow_milli_per_second'] == 199
    assert result['allocations_milli'] == {'movement': 75, 'body': 75, 'sensing': 49}
    assert result['current_qi_milli_spent'] == 0
    assert result['qi_flow_carry_milli_ms_after'] == 199


def test_exact_combat_qi_carry_accumulates_sub_milli_flow_without_free_energy():
    person = {'qi': 125, 'qi_control': 35, 'current_qi_milli': 125_000, 'current_qi': 125}
    state = {'qi_allocation_milli': {'movement': 75, 'body': 75, 'sensing': 49}}
    for _ in range(5):
        result = _qi_effect(person=person, combatant_state=state, duration_ms=1)
    assert person_current_qi_milli(person) == 125_000
    assert state['qi_flow_carry_milli_ms'] == 995
    result = _qi_effect(person=person, combatant_state=state, duration_ms=1)
    assert person_current_qi_milli(person) == 124_999
    assert state['qi_flow_carry_milli_ms'] == 194
    assert result['delivered_flow_milli_per_second'] == 199
