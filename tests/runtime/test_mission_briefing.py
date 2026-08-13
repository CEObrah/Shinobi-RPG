from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from shinobi_runtime.commands.mission_owner import MissionBrief, MissionOwner


def _legacy_owner_record() -> dict:
    return {
        "schema": "mission-runtime",
        "mission_id": "mission.test.protect",
        "issuer_ref": "faction.konoha_mission_office",
        "authority_ref": "canon_hiruzen",
        "mission_rank": "B",
        "funding_holder_ref": "treasury.konoha",
        "escrow_holder_ref": None,
        "opened_at": "SE-0061-06-04T07:00:01",
        "authorized_at": "SE-0061-06-04T07:00:01",
        "starts_at": None,
        "deadline_at": "SE-0061-06-11T07:00:01",
        "next_due_at": None,
        "operation_ref": "team.blackhound",
        "closed_at": None,
        "state": "accepted",
        "participant_refs": ["pc_wei_tang"],
        "objectives": [
            {
                "objective_id": "objective.test.protect",
                "kind": "protect",
                "required": True,
                "dependencies": [],
                "status": "pending",
                "progress_milli": 0,
                "resolution_ref": None,
            }
        ],
        "settlement_terms": [],
        "terminal_reason_ref": None,
        "settlement": None,
    }


def _brief_record() -> dict:
    return {
        "briefing_id": "briefing.test.protect",
        "objective_kind": "protect",
        "subject_kind": "person",
        "subject_ref": "support.daimyo.noboru_shimizu",
        "subject_label": "Noboru Shimizu — Fire Daimyō civil-military liaison",
        "report_place_ref": "place.konoha.mission_assignment_desk",
        "origin_place_ref": "place.konoha",
        "destination_place_ref": "place.fire.capital",
        "route_id": "route_fire_capital_konoha",
        "threat_summary": "Credible interception risk; no hostile actor identified.",
        "threat_source_ref": None,
        "intelligence_constraints": ["Do not attribute a hostile sponsor without evidence."],
        "report_at": "SE-0061-06-04T09:00:01",
        "depart_by": "SE-0061-06-04T13:00:01",
        "completion_condition": "Deliver the protected principal alive and free.",
    }


def test_legacy_mission_owner_loads_without_inventing_briefing() -> None:
    owner = MissionOwner.from_record(_legacy_owner_record())

    assert owner.briefing is None
    assert owner.mission.state == "accepted"
    assert owner.to_record()["briefing"] is None


def test_mission_brief_round_trip_is_typed_and_bounded() -> None:
    record = _legacy_owner_record()
    record["briefing"] = _brief_record()

    owner = MissionOwner.from_record(record)

    assert isinstance(owner.briefing, MissionBrief)
    assert owner.briefing.subject_ref == "support.daimyo.noboru_shimizu"
    assert owner.briefing.threat_source_ref is None
    assert owner.to_record() == record


def test_registered_mission_schema_accepts_typed_brief_and_legacy_owner() -> None:
    schema = json.loads(
        Path("game/schemas/mission-runtime.schema.json").read_text(encoding="utf-8")
    )
    jsonschema.validators.validator_for(schema).check_schema(schema)

    legacy = _legacy_owner_record()
    jsonschema.validate(legacy, schema)

    upgraded = dict(legacy)
    upgraded["briefing"] = _brief_record()
    jsonschema.validate(upgraded, schema)


def test_konoha_player_offer_has_briefs_for_every_player_objective() -> None:
    autonomy = json.loads(
        Path("game/rules/autonomy/living-world.json").read_text(encoding="utf-8")
    )
    assignment = autonomy["faction_assignments"]["faction.konoha_mission_office"]
    offer = assignment["player_offer"]
    objective_cycle = offer["objective_cycle"]

    assert set(offer["briefing_templates"]) == set(objective_cycle)
    assert "deliver" not in objective_cycle


def test_konoha_protection_brief_uses_existing_causal_principal_and_unknown_enemy() -> None:
    autonomy = json.loads(
        Path("game/rules/autonomy/living-world.json").read_text(encoding="utf-8")
    )
    template = autonomy["faction_assignments"]["faction.konoha_mission_office"]["player_offer"]["briefing_templates"]["protect"]

    assert template["subject_ref"] == "support.daimyo.noboru_shimizu"
    assert template["report_place_ref"] == "place.konoha.mission_assignment_desk"
    assert template["destination_place_ref"] == "place.fire.capital"
    assert template["route_id"] == "route_fire_capital_konoha"
    assert template["threat_source_ref"] is None
    assert "no hostile actor has been identified" in template["threat_summary"].lower()
    assert "correspondence" not in json.dumps(template, ensure_ascii=False).lower()
