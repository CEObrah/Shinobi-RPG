from __future__ import annotations

import json
from pathlib import Path

from shinobi_runtime.api.player_report_projection import (
    _briefing_for_front,
    _event_world_front_for_delivery,
)
from shinobi_runtime.store import RepositoryStore


ROOT = Path(__file__).resolve().parents[2]


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def test_choice_contract_treats_meaningful_event_handoff_as_choice_surface() -> None:
    contract = json.loads((ROOT / "runtime/contracts/choice-presentation.json").read_text())
    assert "meaningful player-facing event handoff" in contract["when_to_offer"]
    assert "decision_required is null" in contract["meaningful_event_handoff_rule"]
    assert "Present its player-readable content first" in contract["report_handoff_rule"]


def test_world_front_briefing_projects_bounded_operational_content(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "game/rules/autonomy/world-fronts.json",
        {
            "schema": "world-front-policy",
            "version": 1,
            "phase_thresholds": {
                "developing_evidence": 1,
                "operational_evidence": 3,
                "crisis_evidence": 6,
            },
            "material_action_kinds": ["information_report"],
            "fronts": {
                "pressure_test": {
                    "player_visibility": "public_or_knowledge"
                }
            },
        },
    )
    _write_json(
        tmp_path / "state/canon/pressures.json",
        {
            "schema": "canon-pressure-registry",
            "pressures": {
                "pressure_test": {
                    "id": "pressure_test",
                    "title": "Test mission-market pressure",
                    "status": "active",
                    "stakes": ["contract shifts", "border incidents", "diplomatic pressure", "hidden fourth"],
                    "evidence_refs": ["event.test"],
                }
            },
        },
    )
    briefing = _briefing_for_front(RepositoryStore(tmp_path), "pressure_test")
    assert briefing is not None
    assert briefing["phase"] == "developing"
    assert briefing["summary"] == "Test mission-market pressure is assessed as developing."
    assert briefing["operational_concerns"] == ["contract shifts", "border incidents", "diplomatic pressure"]


def test_legacy_delivery_context_requires_player_visible_delivery_event(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "state/reg/world-events.json",
        {
            "schema": "world-event-registry",
            "archive_refs": [],
            "events": [
                {
                    "id": "event.report",
                    "kind": "world_front_information_delivered",
                    "host_refs": ["pressure_test", "faction.test"],
                    "material_consequence_refs": ["delivery.world_front.test"],
                    "visibility": {
                        "audience_refs": ["pc_wei_tang"],
                        "witness_refs": []
                    },
                }
            ],
        },
    )
    repo = RepositoryStore(tmp_path)
    assert _event_world_front_for_delivery(repo, "delivery.world_front.test", "pc_wei_tang") == "pressure_test"
    assert _event_world_front_for_delivery(repo, "delivery.world_front.test", "canon_hidden") is None
