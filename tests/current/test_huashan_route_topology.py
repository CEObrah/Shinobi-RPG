from __future__ import annotations

from shinobi_runtime.martial_world.geography import load_static_geography
from shinobi_runtime.martial_world.frontier_support import route_lookup
from shinobi_runtime.martial_world.travel import shortest_route


def test_luoyang_to_huashan_uses_direct_registered_corridor_for_convoy_and_foot():
    for mode in ("convoy", "foot"):
        plan = shortest_route(start="luoyang", end="huashan", mode=mode)
        assert plan["nodes"] == ["luoyang", "huashan"]
        assert plan["edges"] == ["route.luoyang.huashan"]
        assert plan["distance_km"] == 267.9


def test_luoyang_to_changan_still_prefers_existing_direct_trunk_route():
    for mode in ("convoy", "foot"):
        plan = shortest_route(start="luoyang", end="changan", mode=mode)
        assert plan["nodes"] == ["luoyang", "changan"]
        assert plan["edges"] == ["route.luoyang.changan"]


def test_frontier_route_index_sees_same_luoyang_huashan_extension_as_planner():
    geography = load_static_geography()
    routes = route_lookup(geography)
    edge = routes["route.luoyang.huashan"]
    assert edge["from"] == "luoyang"
    assert edge["to"] == "huashan"
    assert "convoy" in edge["allowed_modes"]


def test_extension_does_not_override_existing_route_ids():
    geography = load_static_geography()
    ids = [row["id"] for row in geography["routes"]]
    assert len(ids) == len(set(ids))
