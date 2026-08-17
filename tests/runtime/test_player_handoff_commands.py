from __future__ import annotations

import json
from pathlib import Path

from shinobi_runtime.commands.campaign_player_handoffs import CampaignCommandPlanner
from shinobi_runtime.commands.downtime_until_event import install_downtime_until_event
from shinobi_runtime.commands.living_world_support import _OBJECTIVE_DIMENSIONS
from shinobi_runtime.commands.mission_assignment_requests import (
    MISSION_FOCI,
    objective_matches_focus,
)
from shinobi_runtime.commands.specs import COMMAND_SPECS
from shinobi_runtime.store import RepositoryStore


ROOT = Path(__file__).resolve().parents[2]


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def test_general_mission_focus_preserves_all_registered_objective_kinds() -> None:
    assert MISSION_FOCI == frozenset(("general", "combat"))
    assert _OBJECTIVE_DIMENSIONS
    assert all(objective_matches_focus("general", kind) for kind in _OBJECTIVE_DIMENSIONS)


def test_assignment_request_contract_exposes_remote_registered_message() -> None:
    spec = COMMAND_SPECS["mission_assignment_request_resolution"]
    assert spec.required_fields == ("team_ref", "acceptable_ranks", "mission_focus")
    assert spec.optional_fields == ("submission_mode",)
    descriptor = spec.public_descriptor()
    assert descriptor["payload"]["mission_focus"] == "general|combat"
    assert descriptor["payload"]["submission_mode"] == "assignment_desk|registered_message"

    schema = json.loads(
        (ROOT / "game/schemas/mission-assignment-request-registry.schema.json").read_text()
    )
    props = schema["properties"]["requests"]["additionalProperties"]["properties"]
    assert props["mission_focus"]["enum"] == ["general", "combat"]
    assert props["submission_mode"]["enum"] == ["assignment_desk", "registered_message"]
    assert props["submitted_from_place_ref"]["type"] == "string"


def test_secure_communications_discovery_uses_authored_site_and_real_country(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "game/data/content/strategic-site-definitions.json",
        {
            "schema": "site-definition-catalog",
            "records": {
                "place.sword.manor": {"facilities": ["secure_communications"]},
            },
        },
    )
    _write_json(
        tmp_path / "state/world/routes-and-settlements.json",
        {
            "payload": {
                "places": [
                    {"id": "place.sword.manor", "country_id": "country.fire"},
                    {"id": "place.konoha.mission_assignment_desk", "country_id": "country.fire"},
                ]
            }
        },
    )
    planner = object.__new__(CampaignCommandPlanner)
    planner.repository = RepositoryStore(tmp_path)
    assert planner._site_has_secure_communications("place.sword.manor") is True
    assert planner._place_country("place.sword.manor") == planner._place_country(
        "place.konoha.mission_assignment_desk"
    )


def test_report_handoff_command_is_a_closed_player_choice_surface() -> None:
    spec = COMMAND_SPECS["report_handoff_resolution"]
    assert spec.required_fields == ("report_ref", "handling")
    assert spec.optional_fields == ()
    descriptor = spec.public_descriptor()
    assert descriptor["payload"]["handling"] == "acknowledge|keep_compartmented"


def test_event_seeking_wait_registers_on_final_player_handoff_planner() -> None:
    install_downtime_until_event()
    assert "advance_until_event" in CampaignCommandPlanner.COMMAND_TYPES
    assert hasattr(CampaignCommandPlanner, "_advance_until_event")
