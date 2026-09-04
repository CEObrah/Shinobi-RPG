from __future__ import annotations

import copy

import pytest

from shinobi_runtime.commands import combat_span_safety as safety
from shinobi_runtime.combat.geometry import initial_positions
from shinobi_runtime.martial_world.legacy_route_contact_force import (
    reconcile_legacy_active_route_contact_force_records,
)

ROUTE_PATH = "state/martial-world/route-operations.json"
COMBAT_PATH = "state/martial-world/combats.json"
FACTION_PATH = "state/martial-world/factions/black_lance_company.json"
COMBAT_REF = "combat.test.legacy-force"
CONTACT_REF = "contact.test.legacy-force"
MOVEMENT_REF = "movement.test.legacy-force"
ESCORTS = ["pc_wei_tang", *[f"escort.{index:02d}" for index in range(1, 12)]]
ATTACKERS = [f"mw.person.black_lance_company.{index:04d}" for index in range(1, 44)]


def _reader(records):
    def read_json(path):
        if path not in records:
            raise FileNotFoundError(path)
        return copy.deepcopy(records[path])
    return read_json


def _legacy_force_records(*, elapsed_ms: int = 0, marked: bool = False):
    side_by_participant = {
        **{ref: "side_a" for ref in ESCORTS},
        **{ref: "side_b" for ref in ATTACKERS},
    }
    positions = initial_positions(
        side_by_participant=side_by_participant,
        zone_ref="route.changan.huashan",
        initial_range_band="near",
    )
    combatants = {
        ref: {"observed_refs": list(ATTACKERS if ref in ESCORTS else ESCORTS)}
        for ref in [*ESCORTS, *ATTACKERS]
    }
    contact = {
        "status": "active",
        "movement_ref": MOVEMENT_REF,
        "combat_ref": COMBAT_REF,
        "attacker_faction_ref": "black_lance_company",
        "escort_refs": list(ESCORTS),
        "attacker_refs": list(ATTACKERS),
    }
    if marked:
        contact["field_equipment_materialized_count"] = len(ATTACKERS)
    return {
        ROUTE_PATH: {
            "schema": "jianghu-route-operations-state-1.0",
            "movements": {
                MOVEMENT_REF: {
                    "movement_kind": "player_strategic_travel",
                    "status": "contact_pending",
                    "route_ref": "route.changan.huashan",
                    "participant_refs": list(ESCORTS),
                    "quantity": 0,
                    "protected_person_refs": [],
                    "rescued_refs": [],
                    "captive_refs": [],
                    "contact_ref": CONTACT_REF,
                    "combat_ref": COMBAT_REF,
                    "contact_attacker_faction_ref": "black_lance_company",
                    "contact_attacker_refs": list(ATTACKERS),
                    "contact_intent": "hostile_interception",
                }
            },
            "contacts": {CONTACT_REF: contact},
        },
        COMBAT_PATH: {
            "schema": "jianghu-combats-state-1.0",
            "combats": {
                COMBAT_REF: {
                    "combat_id": COMBAT_REF,
                    "status": "active",
                    "elapsed_ms": elapsed_ms,
                    "zone_ref": "route.changan.huashan",
                    "objective": {"kind": "protect_cargo", "movement_ref": MOVEMENT_REF},
                    "sides": {"side_a": list(ESCORTS), "side_b": list(ATTACKERS)},
                    "positions": positions,
                    "combatants": combatants,
                }
            },
        },
        FACTION_PATH: {"faction_id": "black_lance_company"},
    }


def test_legacy_zero_value_route_force_is_resized_before_first_exchange_and_geometry_reseeded():
    records = _legacy_force_records()
    before = records[COMBAT_PATH]["combats"][COMBAT_REF]
    writes = reconcile_legacy_active_route_contact_force_records(
        read_json=_reader(records), combat_ref=COMBAT_REF,
    )

    route_after = writes[ROUTE_PATH]
    combat_after = writes[COMBAT_PATH]["combats"][COMBAT_REF]
    contact = route_after["contacts"][CONTACT_REF]
    movement = route_after["movements"][MOVEMENT_REF]
    kept = contact["attacker_refs"]

    assert 1 <= len(kept) < len(ATTACKERS)
    # With twelve visible targets and zero cargo/ransom value, current outlaw
    # mission sizing cannot justify the legacy 43-person whole-faction rush.
    assert len(kept) <= 21
    assert kept == ATTACKERS[:len(kept)]
    assert movement["contact_attacker_refs"] == kept
    assert contact["legacy_force_reconciled_from_count"] == 43
    assert contact["legacy_force_reconciled_count"] == len(kept)
    assert combat_after["sides"]["side_b"] == kept
    assert set(combat_after["combatants"]) == set([*ESCORTS, *kept])
    assert set(combat_after["positions"]) == set([*ESCORTS, *kept])
    assert combat_after["objective"] == {
        "kind": "preserve_route_mission", "movement_ref": MOVEMENT_REF,
    }
    # Formation must be regenerated because initial spacing is a function of
    # side size; retaining coordinates from the 43-person formation is stale.
    assert combat_after["positions"][ESCORTS[0]] != before["positions"][ESCORTS[0]]


def test_legacy_route_force_never_rewrites_after_combat_started():
    records = _legacy_force_records(elapsed_ms=1)
    with pytest.raises(ValueError, match="cannot reconcile after combat start"):
        reconcile_legacy_active_route_contact_force_records(
            read_json=_reader(records), combat_ref=COMBAT_REF,
        )


def test_existing_field_equipment_marker_closes_legacy_force_migration_boundary():
    records = _legacy_force_records(marked=True)
    assert reconcile_legacy_active_route_contact_force_records(
        read_json=_reader(records), combat_ref=COMBAT_REF,
    ) == {}


def test_finite_combat_span_returns_control_at_earliest_protected_casualty_exchange():
    calls: list[int] = []

    def base_resolver(**kwargs):
        frontier = int(kwargs.get("frontier_exchanges", kwargs.get("exchange_count", 3)))
        exchanges = min(3, frontier)
        calls.append(exchanges)
        protected = exchanges >= 2
        beat = ({
            "at_ms": 12_000,
            "kind": "critical_ally_casualty",
            "salience": "protected",
            "must_narrate_before_next_decision": True,
        } if protected else {
            "at_ms": 6_000,
            "kind": "ordinary_contact",
            "salience": "ordinary",
        })
        return {
            "combat_after": {"status": "active", "elapsed_ms": exchanges * 6_000},
            "exchanges_resolved": exchanges,
            "scope_stop_reason": "scope_complete",
            "continuation_required": False,
            "narrative_projection": {
                "scope_stop_reason": "scope_complete",
                "beats": [beat],
            },
        }

    result = safety.bounded_standing_span(
        base_resolver,
        combat={"status": "active", "elapsed_ms": 0},
        exchange_count=3,
        until_resolution=False,
    )

    assert calls[0] == 3
    assert result["exchanges_resolved"] == 2
    assert result["combat_after"]["elapsed_ms"] == 12_000
    assert result["scope_stop_reason"] == "protected_player_decision"
    assert result["narrative_projection"]["scope_stop_reason"] == "protected_player_decision"
    assert result["continuation_required"] is False


def test_delegated_close_pressure_attack_prefers_carried_melee_over_slow_thrown_choice():
    calls: list[str | None] = []

    def base_selector(**kwargs):
        preferred = kwargs.get("preferred_weapon_ref")
        calls.append(preferred)
        if preferred == "weapon_sword":
            return "cut", "weapon_sword"
        return "hidden_weapon_throw", "weapon_needle"

    def melee_selector(**kwargs):
        return "weapon_sword"

    choice = safety.close_pressure_action_for(
        base_selector,
        melee_weapon_selector=melee_selector,
        combat={
            "positions": {
                "wei": {"zone_ref": "road", "x_mm": 0, "y_mm": 0},
                "enemy": {"zone_ref": "road", "x_mm": 2_000, "y_mm": 0},
            },
            "combatants": {"wei": {"ready_weapon_ref": None}},
        },
        people={"wei": {}, "enemy": {}},
        equipment_ledger={},
        actor_ref="wei",
        target_ref="enemy",
        preferred_weapon_ref=None,
    )

    assert choice == ("cut", "weapon_sword")
    assert calls == [None, "weapon_sword"]


def test_close_pressure_policy_never_overrides_explicit_player_weapon_or_far_ranged_choice():
    def base_selector(**kwargs):
        return "hidden_weapon_throw", str(kwargs.get("preferred_weapon_ref") or "weapon_needle")

    explicit = safety.close_pressure_action_for(
        base_selector,
        melee_weapon_selector=lambda **kwargs: "weapon_sword",
        combat={
            "positions": {
                "wei": {"zone_ref": "road", "x_mm": 0, "y_mm": 0},
                "enemy": {"zone_ref": "road", "x_mm": 2_000, "y_mm": 0},
            }
        },
        people={"wei": {}, "enemy": {}}, equipment_ledger={},
        actor_ref="wei", target_ref="enemy", preferred_weapon_ref="weapon_needle",
    )
    far = safety.close_pressure_action_for(
        base_selector,
        melee_weapon_selector=lambda **kwargs: "weapon_sword",
        combat={
            "positions": {
                "wei": {"zone_ref": "road", "x_mm": 0, "y_mm": 0},
                "enemy": {"zone_ref": "road", "x_mm": 10_000, "y_mm": 0},
            }
        },
        people={"wei": {}, "enemy": {}}, equipment_ledger={},
        actor_ref="wei", target_ref="enemy", preferred_weapon_ref=None,
    )

    assert explicit == ("hidden_weapon_throw", "weapon_needle")
    assert far == ("hidden_weapon_throw", "weapon_needle")
