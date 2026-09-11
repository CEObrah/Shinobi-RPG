import copy
import json
from pathlib import Path

import pytest

import shinobi_runtime.commands.combat_adaptation as adaptation
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.jianghu_scene import JianghuSceneCommandsMixin
from shinobi_runtime.martial_world.exact_combat import (
    canonical_player_action_kind,
    initialize_combat,
    resolve_exchange,
)
from shinobi_runtime.martial_world.mobile_party import active_mobile_parties
from shinobi_runtime.martial_world.physical_presence import (
    effective_person_presence,
    physical_unavailable_person_refs,
    same_effective_location,
)
from shinobi_runtime.martial_world.physical_travel import rebuild_stranded_journey
from shinobi_runtime.martial_world.route_frontier import (
    route_combatant_available_for_postcombat_movement,
    route_combatant_physically_controlled_for_seizure,
    route_return_extra,
)
from shinobi_runtime.martial_world.scheduler import route_ids_needing_service
from shinobi_runtime.martial_world.scene_sessions import ATTEMPT_LEDGER_PATH, HISTORY_HEAD_PATH
from shinobi_runtime.sim.events import CampaignTime

ROOT = Path(__file__).resolve().parents[2]
ROUTES = "state/martial-world/route-operations.json"


def _load(rel):
    return json.loads((ROOT / rel).read_text())


def _clean_person(row):
    person = copy.deepcopy(row)
    person["fatigue_milli"] = 0
    person["health"] = {
        "status": "ready", "injuries": [], "blood_lost_ml": 0,
        "shock": 0, "consciousness": 100,
    }
    person["poison_burdens"] = {}
    person["pending_poison_burdens"] = {}
    return person


def _two_fighters():
    roster = _load("state/martial-world/people/house_tang.json")["people"]
    player = _clean_person(roster[0])
    target = _clean_person(roster[3])
    people = {player["person_id"]: player, target["person_id"]: target}
    combat = initialize_combat(
        combat_ref="combat:test:branch-hardening",
        side_a_refs=[player["person_id"]], side_b_refs=[target["person_id"]],
        people=people, zone_ref="site.house_tang", started_at="x",
        objective={"kind": "eliminate", "target_refs": [target["person_id"]]},
        awareness_mode="mutual", initial_range_band=1,
        equipment_ledger=_load("state/martial-world/equipment-ledger.json"),
    )
    return player, target, people, combat


def test_player_combat_action_aliases_are_canonical_and_unknown_tokens_fail_closed():
    assert canonical_player_action_kind("slash") == "cut"
    assert canonical_player_action_kind("decapitate") == "cut"
    assert canonical_player_action_kind("behead") == "cut"
    assert canonical_player_action_kind("stab") == "thrust"
    assert canonical_player_action_kind("guard") == "hold"
    assert canonical_player_action_kind("attack", allow_auto=True) == "attack"
    with pytest.raises(ValueError, match="unsupported explicit combat action"):
        canonical_player_action_kind("spinny-death-swipe")


def test_unsupported_player_action_cannot_advance_exact_combat_or_spend_resources():
    player, target, people, combat = _two_fighters()
    combat_before = copy.deepcopy(combat)
    ledger = _load("state/martial-world/equipment-ledger.json")
    ledger_before = copy.deepcopy(ledger)
    people_before = copy.deepcopy(people)
    with pytest.raises(ValueError, match="unsupported explicit combat action"):
        resolve_exchange(
            combat=combat, people=people, equipment_ledger=ledger, doctrines={},
            player_ref=player["person_id"], player_action_kind="whirlwind_slash",
            player_target_ref=target["person_id"], player_weapon_ref="weapon_jian",
            player_hit_zone="neck", player_targeting_intent="lethal",
        )
    assert combat == combat_before
    assert people == people_before
    assert ledger == ledger_before
    assert int(combat["elapsed_ms"]) == 0


def test_player_hold_is_a_real_exchange_without_inventing_a_player_attack():
    player, target, people, combat = _two_fighters()
    result = resolve_exchange(
        combat=combat, people=people,
        equipment_ledger=_load("state/martial-world/equipment-ledger.json"), doctrines={},
        player_ref=player["person_id"], player_action_kind="hold",
        player_target_ref=player["person_id"], player_weapon_ref="body_unarmed",
        player_hit_zone="auto", player_targeting_intent="disable",
        player_auto_qi=False, player_auto_poison=False,
    )
    assert int(result["combat_after"]["elapsed_ms"]) > 0
    player_events = [row for row in result["events"] if row.get("actor_ref") == player["person_id"]]
    assert player_events
    assert player_events[0]["action_kind"] == "hold"
    assert player_events[0]["result"] == "holding_guard_position"
    assert not any(row.get("action_kind") in {"cut", "thrust", "hidden_weapon_throw"} for row in player_events)


def _adapt_person(ref):
    return {
        "person_id": ref,
        "attributes": {"intelligence": 100},
        "health": {"status": "healthy", "consciousness": 100, "injuries": []},
        "qi": 0, "qi_control": 0, "current_qi_milli": 0,
    }


def test_structural_player_action_rejection_stops_standing_span_after_one_exchange(monkeypatch):
    monkeypatch.setattr(adaptation, "_update_social_cursor", lambda state, events, **kwargs: copy.deepcopy(state))
    calls = []

    def resolver(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        combat = copy.deepcopy(kwargs["combat"])
        combat["elapsed_ms"] = int(combat.get("elapsed_ms", 0)) + 1000
        return {
            "combat_after": combat,
            "people_after": copy.deepcopy(kwargs["people"]),
            "equipment_ledger_after": copy.deepcopy(kwargs["equipment_ledger"]),
            "events": [{
                "actor_ref": "wei", "action_kind": "cut", "weapon_ref": "weapon_jian",
                "intended_ref": "enemy", "hit_zone": "neck",
                "result": "action_rejected", "reason": "unsupported physical action",
            }],
            "exchanges_resolved": 1,
            "narrative_projection": {"beats": [], "narration_rules": []},
            "combat_information": {},
        }

    combat = {
        "status": "active", "elapsed_ms": 0,
        "sides": {"a": ["wei"], "b": ["enemy"]},
        "combatants": {"wei": {"status_families": []}, "enemy": {"status_families": []}},
    }
    result = adaptation.adaptive_standing_span(
        resolver, fallback=lambda base, **kwargs: base(**kwargs),
        combat=combat, people={"wei": _adapt_person("wei"), "enemy": _adapt_person("enemy")},
        equipment_ledger={}, player_ref="wei", social_state={}, raw_target_ref="auto",
        raw_action_kind="cut", raw_weapon_ref="weapon_jian", hit_zone="neck",
        target_structure_ref=None, targeting_intent="lethal", explicit_poison_ref=None,
        poison_auto=False, explicit_qi_allocation_milli=None, qi_auto=False,
        exchange_count=None, duration_seconds=None, until_resolution=True,
    )
    assert len(calls) == 1
    assert result["exchanges_resolved"] == 1
    assert result["scope_stop_reason"] == "player_action_rejected"
    assert result["continuation_required"] is False


def _reader(rows):
    def read_json(path):
        if path not in rows:
            raise FileNotFoundError(path)
        return copy.deepcopy(rows[path])
    return read_json


def test_stranded_route_owner_prevents_stale_endpoint_teleport_and_is_not_schedulable():
    rows = {
        ROUTES: {"movements": {
            "move.defeated": {
                "status": "stranded", "route_ref": "route.changan.huashan",
                "participant_refs": ["wei", "ally"], "separated_person_refs": ["ally"],
            }
        }}
    }
    read_json = _reader(rows)
    wei = {"person_id": "wei", "location_ref": "site.changan.inn"}
    ally = {"person_id": "ally", "location_ref": "site.changan.inn"}
    wei_presence = effective_person_presence(read_json, "wei", person=wei)
    ally_presence = effective_person_presence(read_json, "ally", person=ally)
    assert wei_presence["location_ref"] == "route.changan.huashan"
    assert wei_presence["space_ref"] == "movement:move.defeated"
    assert ally_presence["location_ref"] == "route.changan.huashan"
    assert ally_presence["space_ref"] == "movement:move.defeated:separated:ally"
    assert same_effective_location(read_json, "wei", "ally", left_person=wei, right_person=ally) is False
    assert {"wei", "ally"}.issubset(physical_unavailable_person_refs(read_json))
    assert route_ids_needing_service(rows[ROUTES]["movements"]) == []


def test_stranded_party_still_exists_in_physical_mobile_party_projection():
    rows = {
        ROUTES: {"movements": {
            "move.defeated": {
                "status": "stranded", "route_ref": "route.changan.huashan",
                "movement_kind": "player_strategic_travel", "participant_refs": ["wei", "ally"],
                "leader_ref": "wei", "beneficiary_ref": "house_tang",
            }
        }}
    }
    parties = active_mobile_parties(_reader(rows))
    assert len(parties) == 1
    assert parties[0]["party_ref"] == "move.defeated"
    assert parties[0]["member_refs"] == ["wei", "ally"]


def _stranded_journey():
    return {
        "status": "stranded",
        "movement_kind": "player_strategic_travel",
        "movement_ref": "move.defeated",
        "purpose_ref": "mission.test",
        "route_ref": "route.b.c",
        "route_refs": ["route.a.b", "route.b.c", "route.c.d"],
        "route_index": 1,
        "journey_nodes": ["a", "b", "c", "d"],
        "segment_required_seconds": [1000, 2000, 3000],
        "segment_provisioning_seconds": [1200, 2400, 3600],
        "segment_edge_start_milli": [0, 0, 0],
        "segment_edge_end_milli": [1000, 1000, 1000],
        "origin_place_ref": "a",
        "destination_place_ref": "d",
        "destination_site_ref": "site.d.inn",
        "segment_origin_place_ref": "b",
        "segment_destination_place_ref": "c",
        "elapsed_seconds": 500,
        "required_seconds": 2000,
        "edge_start_milli": 0,
        "edge_end_milli": 1000,
        "participant_refs": ["wei", "ally"],
        "leader_ref": "wei",
        "provision_reservation": {"ration_days_reserved": 20},
        "separated_person_refs": ["ally"],
        "stranded_at": "0061-01-01T00:00:00",
        "stranded_reason": "combat_defeat",
    }


def test_stranded_journey_resume_preserves_exact_edge_progress_and_requires_fresh_provisions():
    movement = _stranded_journey()
    restarted = rebuild_stranded_journey(
        movement, at=__import__("datetime").datetime(61, 1, 1, 0, 1, 0), direction="resume"
    )
    assert restarted["route_refs"] == ["route.b.c", "route.c.d"]
    assert restarted["journey_nodes"] == ["b", "c", "d"]
    assert restarted["edge_start_milli"] == 250
    assert restarted["edge_end_milli"] == 1000
    assert restarted["required_seconds"] == 1500
    assert restarted["destination_place_ref"] == "d"
    assert restarted["destination_site_ref"] == "site.d.inn"
    assert restarted["status"] == "active"
    assert "provision_reservation" not in restarted
    assert "separated_person_refs" not in restarted
    assert "stranded_reason" not in restarted


def test_stranded_journey_turn_back_reverses_from_exact_edge_progress():
    movement = _stranded_journey()
    restarted = rebuild_stranded_journey(
        movement, at=__import__("datetime").datetime(61, 1, 1, 0, 1, 0), direction="turn_back"
    )
    assert restarted["route_refs"] == ["route.b.c", "route.a.b"]
    assert restarted["journey_nodes"] == ["c", "b", "a"]
    assert restarted["edge_start_milli"] == 250
    assert restarted["edge_end_milli"] == 0
    assert restarted["required_seconds"] == 500
    assert restarted["destination_place_ref"] == "a"
    assert "destination_site_ref" not in restarted
    assert restarted["status"] == "active"


def test_postcombat_return_party_excludes_bodies_no_longer_available_to_move():
    combat_after = {"combatants": {
        "ready": {"status_families": []},
        "escaped": {"status_families": ["escaped"]},
        "not_arrived": {"status_families": ["reinforcing"]},
        "down": {"status_families": ["incapacitated"]},
        "out": {"status_families": ["unconscious"]},
        "dead": {"status_families": ["dead"]},
    }}
    assert route_combatant_available_for_postcombat_movement(combat_after, "ready") is True
    for ref in ("escaped", "not_arrived", "down", "out", "dead"):
        assert route_combatant_available_for_postcombat_movement(combat_after, ref) is False
    assert route_combatant_available_for_postcombat_movement(None, "legacy") is True


def test_parked_return_retry_preserves_carried_cash_and_other_physical_holdings():
    movement = {
        "movement_kind": "raid_return",
        "item_ref": "food_ration_day",
        "quantity": 12000,
        "cash_quantity": 4000,
        "captive_refs": ["captive"],
        "protected_person_refs": ["captive"],
        "escort_refs": ["raider"],
        "nonphysical_debug_field": "drop-me",
    }
    extra = route_return_extra(movement)
    assert extra["item_ref"] == "food_ration_day"
    assert extra["quantity"] == 12000
    assert extra["cash_quantity"] == 4000
    assert extra["captive_refs"] == ["captive"]
    assert "nonphysical_debug_field" not in extra


def test_route_seizure_requires_actual_postcombat_control_not_only_side_victory():
    combat_after = {"combatants": {
        "captured": {"status_families": ["incapacitated"]},
        "escaped": {"status_families": ["escaped"]},
        "not_arrived": {"status_families": ["reinforcing"]},
    }}
    assert route_combatant_physically_controlled_for_seizure(combat_after, "captured") is True
    assert route_combatant_physically_controlled_for_seizure(combat_after, "escaped") is False
    assert route_combatant_physically_controlled_for_seizure(combat_after, "not_arrived") is False
    assert route_combatant_physically_controlled_for_seizure(None, "legacy") is True


class _InteractionRepo:
    def __init__(self):
        self.rows = {
            "state/scene.json": {"present_person_ids": ["wei", "npc"], "visible_person_ids": ["wei", "npc"]},
        }
    def read_json(self, path):
        if path not in self.rows:
            raise FileNotFoundError(path)
        return copy.deepcopy(self.rows[path])


class _InteractionHarness(JianghuSceneCommandsMixin):
    def __init__(self):
        self.repository = _InteractionRepo()
        self.scene_path = "state/scene.json"
        self.people = {
            "wei": {"person_id": "wei", "location_ref": "site.test"},
            "npc": {"person_id": "npc", "location_ref": "site.test"},
        }
    def _person(self, ref):
        person = copy.deepcopy(self.people[ref])
        return f"people:{ref}", {"people": [person]}, 0, person
    def _simple_plan(self, command, meta, current_time, *, writes_records, code, result):
        return {"writes_records": writes_records, "code": code, "result": result}


@pytest.mark.parametrize("alias", ["propose", "counteroffer"])
def test_interaction_offer_synonyms_canonicalize_instead_of_rejecting(alias):
    command = CommandEnvelope(
        campaign_id="campaign.test", request_id=f"request.{alias}", actor_id="wei",
        command_type="jianghu_interaction_resolution", expected_revision=1,
        submitted_at="2026-09-10T00:00:00Z",
        payload={"action": alias, "target_ref": "npc", "player_statement": "Three taels."},
    )
    built = _InteractionHarness()._jianghu_interaction_resolution(
        command, {}, CampaignTime.parse("SE-0061-01-01T00:00:00")
    )
    row = built["writes_records"][ATTEMPT_LEDGER_PATH]["attempts"][-1]
    assert row["action"] == "offer"
    assert row["thread_status"] == "open"

from shinobi_runtime.commands.jianghu_development import JianghuDevelopmentCommandsMixin
from shinobi_runtime.martial_world.escort_living_world import route_extortion_settlement_terms


def test_outlaw_extortion_terms_ground_five_tael_opening_and_four_tael_floor_for_twelve_travelers():
    terms = route_extortion_settlement_terms(
        attacker_faction_type="outlaw_faction",
        attacker_intent="hostile_interception",
        motive_kind="opportunistic_predation",
        observed_escort_count=12,
        cargo_value_cash=0,
    )
    assert terms == {"minimum_cash": 4000, "opening_demand_cash": 5000}
    assert route_extortion_settlement_terms(
        attacker_faction_type="martial_house",
        attacker_intent="hostile_interception",
        motive_kind="grievance",
        observed_escort_count=12,
    ) is None


class _SettlementRepo:
    def __init__(self):
        self.rows = {
            "state/martial-world/route-operations.json": {
                "schema": "jianghu-route-operations-state-1.0",
                "movements": {
                    "move.test": {
                        "movement_kind": "player_strategic_travel",
                        "status": "contact_pending",
                        "route_ref": "route.test",
                        "participant_refs": ["wei", "ally"],
                        "contact_ref": "contact.test",
                        "combat_ref": "combat.test",
                        "contact_attacker_faction_ref": "outlaw.test",
                        "contact_attacker_refs": ["enemy"],
                        "contact_intent": "hostile_interception",
                    },
                },
                "contacts": {
                    "contact.test": {
                        "status": "active",
                        "movement_ref": "move.test",
                        "route_ref": "route.test",
                        "combat_ref": "combat.test",
                        "attacker_faction_ref": "outlaw.test",
                        "attacker_refs": ["enemy"],
                        "escort_refs": ["wei", "ally"],
                        "attacker_intent": "hostile_interception",
                        "motive_kind": "opportunistic_predation",
                        "settlement_terms": {"minimum_cash": 4000, "opening_demand_cash": 5000},
                    },
                },
            },
            "state/martial-world/combats.json": {
                "schema": "jianghu-combats-state-1.0",
                "combats": {
                    "combat.test": {
                        "combat_id": "combat.test",
                        "status": "active",
                        "elapsed_ms": 0,
                        "sides": {"side_a": ["wei", "ally"], "side_b": ["enemy"]},
                        "combatants": {"wei": {}, "ally": {}, "enemy": {}},
                    }
                },
            },
            "game/data/martial-world/faction-identities.json": {
                "identities": {"outlaw.test": {"faction_type": "outlaw_faction"}},
            },
            "state/scene.json": {"active_combat_ref": "combat.test", "location_id": "route.test"},
        }
        self.people = {
            "wei": {"person_id": "wei", "personal_cash": 5000, "health": {"status": "ready", "consciousness": 100}},
        }

    def read_json(self, path):
        if path.startswith("people/"):
            return copy.deepcopy({"schema": "jianghu-person-lite-roster-1.0", "faction_ref": "house_tang", "people": [self.people["wei"]]})
        if path not in self.rows:
            raise FileNotFoundError(path)
        return copy.deepcopy(self.rows[path])


class _SettlementHarness(JianghuDevelopmentCommandsMixin):
    def __init__(self):
        self.repository = _SettlementRepo()
        self.scene_path = "state/scene.json"

    def _person(self, ref):
        if ref != "wei":
            raise RuntimeError(ref)
        person = copy.deepcopy(self.repository.people[ref])
        return "people/wei.json", {"schema": "jianghu-person-lite-roster-1.0", "faction_ref": "house_tang", "people": [person]}, 0, person

    def _simple_plan(self, command, meta, current_time, *, writes_records, code, result, scene=None):
        return {"writes_records": writes_records, "code": code, "result": result, "scene": scene}


def _settlement_command(cash):
    return CommandEnvelope(
        campaign_id="campaign.test", request_id=f"settle.{cash}", actor_id="wei",
        command_type="jianghu_route_contact_settlement_resolution", expected_revision=1,
        submitted_at="2026-09-10T00:00:00Z",
        payload={"action": "pay_for_passage", "combat_ref": "combat.test", "cash": cash},
    )


def test_route_contact_payment_is_atomic_actor_cash_to_physical_return_and_passage(monkeypatch):
    import shinobi_runtime.commands.jianghu_development as development

    harness = _SettlementHarness()
    observed = {}

    def fake_reconcile(*, read_json, at, player_ref, combat_ref):
        observed["route"] = read_json("state/martial-world/route-operations.json")
        observed["combat"] = read_json("state/martial-world/combats.json")
        observed["roster"] = read_json("people/wei.json")
        route = copy.deepcopy(observed["route"])
        route["movements"]["move.test"]["status"] = "active"
        route["movements"]["move.test"].pop("contact_ref", None)
        route["movements"]["move.test"].pop("combat_ref", None)
        route["contacts"].pop("contact.test", None)
        route["movements"]["return.test"] = {
            "movement_kind": "raid_return", "status": "active", "route_ref": "route.test",
            "participant_refs": ["enemy"], "cash_quantity": 4000,
        }
        combats = copy.deepcopy(observed["combat"])
        combats["combats"].pop("combat.test", None)
        return {
            "state/martial-world/route-operations.json": route,
            "state/martial-world/combats.json": combats,
            "state/martial-world/scheduler.json": {"schema": "scheduler", "one_off": {}},
        }

    monkeypatch.setattr(development, "reconcile_resolved_player_route_contact_records", fake_reconcile)
    built = harness._jianghu_route_contact_settlement_resolution(
        _settlement_command(4000), {}, CampaignTime.parse("SE-0061-01-01T00:00:00")
    )
    assert built["code"] == "jianghu_route_contact_settlement_paid"
    assert built["result"]["passage_settled"] is True
    assert observed["route"]["movements"]["move.test"]["contact_settlement_cash"] == 4000
    assert observed["combat"]["combats"]["combat.test"]["status"] == "resolved"
    assert observed["combat"]["combats"]["combat.test"]["resolution_kind"] == "negotiated_passage"
    assert observed["roster"]["people"][0]["personal_cash"] == 1000
    assert built["writes_records"]["state/martial-world/route-operations.json"]["movements"]["return.test"]["cash_quantity"] == 4000
    assert built["writes_records"]["people/wei.json"]["people"][0]["personal_cash"] == 1000


def test_route_contact_payment_below_private_floor_fails_without_spending(monkeypatch):
    import shinobi_runtime.commands.jianghu_development as development

    harness = _SettlementHarness()
    monkeypatch.setattr(development, "reconcile_resolved_player_route_contact_records", lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not reconcile")))
    with pytest.raises(Exception) as caught:
        harness._jianghu_route_contact_settlement_resolution(
            _settlement_command(3000), {}, CampaignTime.parse("SE-0061-01-01T00:00:00")
        )
    assert getattr(caught.value, "code", None) == "jianghu_route_contact_offer_insufficient"
    assert harness.repository.people["wei"]["personal_cash"] == 5000


def test_successfully_escaped_fighter_leaves_active_combat_presence_for_route_owner():
    rows = {
        "state/martial-world/combats.json": {
            "combats": {
                "combat.test": {
                    "status": "active", "elapsed_ms": 1000,
                    "sides": {"side_a": ["wei", "ally"], "side_b": ["enemy"]},
                    "combatants": {
                        "wei": {"status_families": ["escaped"]},
                        "ally": {"status_families": []},
                        "enemy": {"status_families": []},
                    },
                }
            }
        },
        ROUTES: {
            "movements": {
                "move.test": {
                    "status": "contact_pending", "route_ref": "route.test",
                    "participant_refs": ["wei", "ally"], "combat_ref": "combat.test",
                    "separated_person_refs": ["wei"],
                }
            }
        },
    }
    presence = effective_person_presence(
        _reader(rows), "wei", person={"person_id": "wei", "location_ref": "site.stale"}
    )
    assert presence["presence_kind"] == "route"
    assert presence["owner_ref"] == "move.test"
    assert presence["location_ref"] == "route.test"
    assert presence["space_ref"] == "movement:move.test:separated:wei"
    assert same_effective_location(
        _reader(rows), "wei", "ally",
        left_person={"person_id": "wei", "location_ref": "site.stale"},
        right_person={"person_id": "ally", "location_ref": "site.stale"},
    ) is False


from shinobi_runtime.commands.jianghu_extended import JianghuExtendedCommandsMixin


class _CashOfferRepo:
    def __init__(self):
        self.people = {
            "wei": {"person_id": "wei", "personal_cash": 3418, "health": {"status": "ready", "consciousness": 100}},
            "han": {"person_id": "han", "personal_cash": 1770, "health": {"status": "ready", "consciousness": 100}},
        }
        self.rows = {
            "state/martial-world/combats.json": {
                "schema": "jianghu-combats-state-1.0",
                "combats": {
                    "combat.offer": {
                        "combat_id": "combat.offer", "status": "active", "elapsed_ms": 0,
                        "sides": {"side_a": ["wei", "han"], "side_b": ["enemy"]},
                        "combatants": {"wei": {}, "han": {}, "enemy": {}},
                    }
                },
            },
            "state/martial-world/custody.json": {"schema": "jianghu-custody-state-1.0", "records": []},
            HISTORY_HEAD_PATH: {
                "schema": "jianghu-scene-history-head-1.0", "authority": False,
                "mechanical_consequence_authority": False, "total_recorded": 1,
                "latest_shard_ref": None,
                "recent": [{
                    "speech_ref": "scene_speech_offer_582", "at": "SE-0061-09-27T21:21:45",
                    "session_ref": "combat.offer", "speaker_ref": "han",
                    "speech_kind": "nonbinding_proposal",
                    "statement": "I can cover the 582 copper if you decide to pay them.",
                    "proposal_terms": {"cash": 582, "recipient_ref": "wei"},
                    "basis_refs": [], "resolves_thread_ref": None, "resolves_question_ref": None,
                    "truth_status": "attributed_statement", "authority": False,
                    "mechanical_consequence_authority": False,
                }],
                "continuity_by_subject": {}, "continuity_subject_order": [],
            },
        }

    def read_json(self, path):
        if path not in self.rows:
            raise FileNotFoundError(path)
        return copy.deepcopy(self.rows[path])


class _CashOfferHarness(JianghuExtendedCommandsMixin):
    def __init__(self):
        self.repository = _CashOfferRepo()

    def _person(self, ref):
        roster = {"schema": "jianghu-person-lite-roster-1.0", "faction_ref": "house_tang", "people": [
            copy.deepcopy(self.repository.people["wei"]), copy.deepcopy(self.repository.people["han"]),
        ]}
        index = 0 if ref == "wei" else 1
        return "people/party.json", roster, index, copy.deepcopy(roster["people"][index])

    def _same_effective_location(self, left_ref, right_ref):
        return {left_ref, right_ref} == {"wei", "han"}

    def _simple_plan(self, command, meta, current_time, *, writes_records, code, result, scene=None):
        return {"writes_records": writes_records, "code": code, "result": result}


def _accept_cash_offer(cash):
    return CommandEnvelope(
        campaign_id="campaign.test", request_id=f"accept.offer.{cash}", actor_id="wei",
        command_type="jianghu_property_transfer_resolution", expected_revision=1,
        submitted_at="2026-09-10T00:00:00Z",
        payload={
            "action": "accept_cash_offer", "other_ref": "han", "cash": cash,
            "basis_ref": "scene_speech_offer_582",
        },
    )


def test_player_can_accept_exact_recorded_allied_cash_offer_without_treating_speech_as_money():
    harness = _CashOfferHarness()
    built = harness._jianghu_property_transfer_resolution(
        _accept_cash_offer(582), {}, CampaignTime.parse("SE-0061-09-27T21:21:45")
    )
    roster = built["writes_records"]["people/party.json"]["people"]
    by_ref = {row["person_id"]: row for row in roster}
    assert by_ref["wei"]["personal_cash"] == 4000
    assert by_ref["han"]["personal_cash"] == 1188
    assert built["result"]["basis_ref"] == "scene_speech_offer_582"
    assert built["result"]["restraint_basis"]["kind"] == "recorded_cash_offer"


def test_player_cannot_take_more_than_recorded_cash_offer():
    harness = _CashOfferHarness()
    with pytest.raises(Exception) as caught:
        harness._jianghu_property_transfer_resolution(
            _accept_cash_offer(583), {}, CampaignTime.parse("SE-0061-09-27T21:21:45")
        )
    assert getattr(caught.value, "code", None) == "jianghu_property_cash_offer_exceeds_terms"


class _DisengageRepo:
    def __init__(self):
        self.rows = {
            "state/martial-world/combats.json": {
                "schema": "jianghu-combats-state-1.0",
                "combats": {
                    "combat.escape": {
                        "combat_id": "combat.escape",
                        "status": "active",
                        "elapsed_ms": 1000,
                        "objective": {"kind": "preserve_route_mission", "movement_ref": "move.escape"},
                        "sides": {"side_a": ["wei", "ally"], "side_b": ["enemy"]},
                        "combatants": {
                            "wei": {"status_families": []},
                            "ally": {"status_families": []},
                            "enemy": {"status_families": []},
                        },
                    }
                },
            },
            "state/martial-world/equipment-ledger.json": {
                "schema": "jianghu-equipment-ledger-1.0", "policy_assignments": {}, "person_loadouts": {},
            },
            ROUTES: {
                "schema": "jianghu-route-operations-state-1.0",
                "movements": {
                    "move.escape": {
                        "status": "contact_pending", "route_ref": "route.test",
                        "participant_refs": ["wei", "ally"], "combat_ref": "combat.escape",
                    }
                },
                "contacts": {},
            },
            "state/martial-world/social.json": {"schema": "social", "relationships": {}},
            "state/martial-world/scheduler.json": {"schema": "scheduler", "one_off": {}},
            "state/scene.json": {"active_combat_ref": "combat.escape", "location_id": "route.test"},
        }
        self.people = {
            "wei": {"person_id": "wei", "faction_ref": "house_tang", "health": {"status": "ready", "consciousness": 100}},
            "ally": {"person_id": "ally", "faction_ref": "house_tang", "health": {"status": "ready", "consciousness": 100}},
            "enemy": {"person_id": "enemy", "faction_ref": "outlaw.test", "health": {"status": "ready", "consciousness": 100}},
        }

    def read_json(self, path):
        if path not in self.rows:
            raise FileNotFoundError(path)
        return copy.deepcopy(self.rows[path])


class _DisengageHarness(JianghuExtendedCommandsMixin):
    def __init__(self):
        self.repository = _DisengageRepo()
        self.scene_path = "state/scene.json"

    def _person(self, ref):
        person = copy.deepcopy(self.repository.people[ref])
        return f"people/{ref}.json", {"people": [person]}, 0, person

    def _time_plan_exact(self, command, meta, current_time, *, seconds):
        return object()

    def _time_after_record(self, time_plan, path, fallback):
        return copy.deepcopy(dict(fallback))

    def _combine_time_plan(self, command, time_plan, *, extra_records, code, result, scene_override=None):
        return {
            "extra_records": copy.deepcopy(dict(extra_records)),
            "code": code,
            "result": copy.deepcopy(dict(result)),
            "scene": copy.deepcopy(scene_override),
        }


def test_successful_route_disengage_marks_escapee_separated_before_allies_finish_fighting(monkeypatch):
    import shinobi_runtime.commands.jianghu_extended as extended

    harness = _DisengageHarness()

    def fake_disengage(*, combat, actor_ref, people, equipment_ledger):
        after = copy.deepcopy(dict(combat))
        after["elapsed_ms"] = int(after.get("elapsed_ms", 0)) + 1000
        after["combatants"][actor_ref]["status_families"] = ["escaped"]
        return {"escaped": True, "reason": "clear", "combat_after": after}

    monkeypatch.setattr(extended, "attempt_disengage", fake_disengage)
    command = CommandEnvelope(
        campaign_id="campaign.test", request_id="escape.route", actor_id="wei",
        command_type="jianghu_combat_resolution", expected_revision=1,
        submitted_at="2026-09-10T00:00:00Z",
        payload={"action": "disengage", "combat_ref": "combat.escape"},
    )
    built = harness._jianghu_combat_core_resolution(
        command, {}, CampaignTime.parse("SE-0061-01-01T00:00:00")
    )
    assert built["result"]["escaped"] is True
    assert built["result"]["combat_status"] == "active"
    movement = built["extra_records"][ROUTES]["movements"]["move.escape"]
    assert movement["status"] == "contact_pending"
    assert movement["separated_person_refs"] == ["wei"]
