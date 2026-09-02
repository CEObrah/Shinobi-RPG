import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(rel: str):
    return json.loads((ROOT / rel).read_text())


def test_outlaw_operating_routes_are_geographically_local_and_cover_regional_travel():
    seed = _load("game/data/martial-world/world-seed.json")
    geography = _load("game/data/martial-world/geography.json")
    factions = seed["martial_factions"]
    route_by_id = {row["id"]: row for row in geography["routes"]}
    outlaws = {fid: row for fid, row in factions.items() if row.get("type") == "outlaw_faction"}

    assert len(outlaws) >= 50
    coverage = Counter()
    for fid, faction in outlaws.items():
        headquarters = faction.get("headquarters")
        routes = faction.get("operating_routes", [])
        assert routes, fid
        for route_ref in routes:
            assert route_ref in route_by_id, (fid, route_ref)
            route = route_by_id[route_ref]
            assert headquarters in {route.get("from"), route.get("to")}, (fid, headquarters, route_ref)
            coverage[route_ref] += 1

    deliberately_local_safe = {"route.luoyang.rural_estates"}
    regional_routes = set(route_by_id) - deliberately_local_safe
    assert all(coverage[route_ref] >= 1 for route_ref in regional_routes)
    assert coverage["route.luoyang.rural_estates"] == 0


def test_outlaw_distribution_uses_existing_factions_instead_of_route_spawn_bloat():
    seed = _load("game/data/martial-world/world-seed.json")
    outlaws = [row for row in seed["martial_factions"].values() if row.get("type") == "outlaw_faction"]
    subtypes = Counter(row.get("outlaw_subtype") for row in outlaws)
    headquarters = Counter(row.get("headquarters") for row in outlaws)

    assert len(headquarters) >= 20
    assert set(subtypes) >= {"road_band", "mountain_stronghold", "smuggling_ring", "urban_gang", "river_pirates"}
    assert max(headquarters.values()) <= 8


def test_active_outlaw_raids_respect_current_operating_route_footprint():
    seed = _load("game/data/martial-world/world-seed.json")
    geography = _load("game/data/martial-world/geography.json")
    deployments = _load("state/martial-world/deployments.json").get("deployments", {})
    factions = seed["martial_factions"]
    route_by_id = {row["id"]: row for row in geography["routes"]}

    checked = 0
    for op_ref, operation in deployments.items():
        if not isinstance(operation, dict) or operation.get("operation_kind") != "faction_raid":
            continue
        if operation.get("status") in {"completed", "cancelled", "failed"}:
            continue
        source_ref = operation.get("faction_ref")
        target_ref = operation.get("target_faction_ref")
        source = factions.get(source_ref, {})
        target = factions.get(target_ref, {})
        if source.get("type") != "outlaw_faction" or not target:
            continue
        local_places = {source.get("headquarters")}
        for route_ref in source.get("operating_routes", []):
            route = route_by_id.get(route_ref, {})
            local_places.update(x for x in (route.get("from"), route.get("to")) if x)
        assert target.get("headquarters") in local_places, (op_ref, sorted(local_places), target.get("headquarters"))
        checked += 1
    assert checked > 0


def test_runtime_outlaw_locality_consumes_list_backed_geography_routes():
    from shinobi_runtime.martial_world.faction_state import hydrate_faction_state
    from shinobi_runtime.martial_world.outlaws import outlaw_raid_target_is_local

    def read_json(rel):
        return _load(rel)

    red_road = hydrate_faction_state(_load("state/martial-world/factions/faction.red_road_band.json"))
    grey_bridge = hydrate_faction_state(_load("state/martial-world/factions/faction.grey_bridge_band.json"))
    assert outlaw_raid_target_is_local(red_road, target_place="kunming", read_json=read_json)
    assert not outlaw_raid_target_is_local(grey_bridge, target_place="chengdu", read_json=read_json)


def test_hostile_choice_filters_impossible_outlaw_raid_targets_before_ranking(monkeypatch):
    import shinobi_runtime.martial_world.strategic_autonomy as strategic

    monkeypatch.setattr(strategic, "stable_permille", lambda *parts: 0)
    edges = [
        {"from_faction": "faction.band", "to_faction": "faction.nonlocal", "hostility": 60},
        {"from_faction": "faction.band", "to_faction": "faction.local", "hostility": 50},
    ]
    chosen = strategic.choose_hostile_action(
        edges, faction_ref="faction.band", year=61, month=10, risk_tolerance=100,
        faction_type="outlaw_faction", outlaw_subtype="road_band",
        eligible_raid_target_refs={"faction.local"},
    )
    assert chosen is not None
    assert chosen["action"] == "faction_raid"
    assert chosen["target_faction_ref"] == "faction.local"

    # Locality limits ordinary raids only. Once hostility is actual war, a
    # strategic strike may lawfully cross the outlaw's normal route footprint.
    war = strategic.choose_hostile_action(
        [{"from_faction": "faction.band", "to_faction": "faction.nonlocal", "hostility": 90}],
        faction_ref="faction.band", year=61, month=10, risk_tolerance=100,
        faction_type="outlaw_faction", outlaw_subtype="road_band",
        eligible_raid_target_refs=set(),
    )
    assert war is not None
    assert war["action"] == "faction_war_strike"
    assert war["target_faction_ref"] == "faction.nonlocal"
