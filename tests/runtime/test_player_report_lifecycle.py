from __future__ import annotations

import json
from pathlib import Path

from shinobi_runtime.api.player_report_lifecycle import (
    _briefing_for_front_as_of,
    _full_event_world_front_for_delivery,
)
from shinobi_runtime.store import RepositoryStore


ROOT = Path(__file__).resolve().parents[2]


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def test_scene_template_registers_durable_handled_report_refs() -> None:
    template = json.loads((ROOT / "runtime/contracts/templates/scene.template.json").read_text())
    narrative = template["object_contracts"]["/narrative"]["allowed_keys"]
    assert "handled_report_refs" in narrative
    assert template["type_contracts"]["/narrative/handled_report_refs"] == ["array"]
    assert template["array_contracts"]["/narrative/handled_report_refs"]["item_types"] == ["string"]


def test_legacy_report_lookup_is_not_limited_to_recent_four_archives(tmp_path: Path) -> None:
    archive_refs = [f"state/history/events/segment-{index:06d}.json" for index in range(1, 7)]
    _write_json(
        tmp_path / "state/reg/world-events.json",
        {"schema": "world-event-registry", "archive_refs": archive_refs, "events": []},
    )
    for index, path in enumerate(archive_refs, start=1):
        events = []
        if index == 1:
            events.append(
                {
                    "kind": "world_front_information_delivered",
                    "host_refs": ["pressure_test", "faction.test"],
                    "material_consequence_refs": ["delivery.world_front.old"],
                    "visibility": {"audience_refs": ["pc_wei_tang"], "witness_refs": []},
                }
            )
        _write_json(tmp_path / path, {"schema": "world-event-archive", "events": events})

    repo = RepositoryStore(tmp_path)
    assert _full_event_world_front_for_delivery(repo, "delivery.world_front.old", "pc_wei_tang") == "pressure_test"


def test_briefing_phase_is_reconstructed_as_of_delivery_not_current_front(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "game/rules/autonomy/world-fronts.json",
        {
            "schema": "world-front-policy",
            "version": 1,
            "phase_thresholds": {"developing_evidence": 1, "operational_evidence": 3, "crisis_evidence": 6},
            "material_action_kinds": ["information_report"],
            "fronts": {"pressure_test": {"player_visibility": "public_or_knowledge"}},
        },
    )
    _write_json(
        tmp_path / "state/canon/pressures.json",
        {
            "schema": "canon-pressure-registry",
            "pressures": {
                "pressure_test": {
                    "id": "pressure_test",
                    "title": "Test mission pressure",
                    "status": "active",
                    "stakes": ["contracts", "diplomacy", "border incidents"],
                    "evidence_refs": ["e1", "e2", "e3", "e4"],
                    "chronology": [
                        {"at": "SE-0061-06-20T07:00:00", "kind": "committed_domain_evidence", "status_after": "active"},
                        {"at": "SE-0061-06-26T07:00:00", "kind": "committed_domain_evidence", "status_after": "active"},
                        {"at": "SE-0061-06-27T07:00:00", "kind": "committed_domain_evidence", "status_after": "active"},
                        {"at": "SE-0061-06-28T07:00:00", "kind": "committed_domain_evidence", "status_after": "active"},
                    ],
                }
            },
        },
    )
    briefing = _briefing_for_front_as_of(
        RepositoryStore(tmp_path), "pressure_test", "SE-0061-06-25T07:00:00"
    )
    assert briefing is not None
    assert briefing["phase"] == "developing"
    assert briefing["summary"] == "Test mission pressure is assessed as developing."
    assert briefing["as_of"] == "SE-0061-06-25T07:00:00"
