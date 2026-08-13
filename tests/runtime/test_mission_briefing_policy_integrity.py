from __future__ import annotations

import json
from pathlib import Path


def test_player_offer_briefing_topology_uses_registered_places_and_routes() -> None:
    autonomy = json.loads(
        Path("game/rules/autonomy/living-world.json").read_text(encoding="utf-8")
    )
    world = json.loads(
        Path("state/world/routes-and-settlements.json").read_text(encoding="utf-8")
    )["payload"]
    places = {row["id"] for row in world["places"]}
    routes = {row["id"] for row in world["routes"]}
    offer = autonomy["faction_assignments"]["faction.konoha_mission_office"]["player_offer"]

    for objective_kind in offer["objective_cycle"]:
        template = offer["briefing_templates"][objective_kind]
        for field in ("report_place_ref", "origin_place_ref", "destination_place_ref"):
            place_ref = template.get(field)
            if place_ref is not None:
                assert place_ref in places, (objective_kind, field, place_ref)
        if template.get("subject_kind") == "place":
            assert template["subject_ref"] in places, (objective_kind, "subject_ref")
        if template.get("destination_place_ref") is not None:
            assert template["route_id"] in routes, (objective_kind, "route_id")
