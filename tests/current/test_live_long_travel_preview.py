from __future__ import annotations

import json
from pathlib import Path

import jsonschema


def test_synthetic_travel_handoff_without_scheduler_event_id_is_schema_valid():
    """A real travel stop may be a synthetic wake rather than a scheduler event.

    The rev-130 Huashan progression blocker was caused by the time reducer
    correctly producing such a travel stop with event_id=None while the scene
    schema incorrectly required every wake to have a scheduler-style string ID.
    """
    root = Path(__file__).resolve().parents[2]
    schema = json.loads((root / "game/schemas/scene.schema.json").read_text())
    scene = {
        "schema": "scene",
        "scene_id": "scene.test.travel",
        "location_id": "route.test",
        "present_person_ids": ["pc"],
        "visible_person_ids": ["pc"],
        "activity_handoff": {
            "event_id": None,
            "kind": "travel_city_stop",
            "requires_player_decision": False,
            "interrupts_continuation": True,
        },
    }

    jsonschema.Draft202012Validator(schema).validate(scene)
