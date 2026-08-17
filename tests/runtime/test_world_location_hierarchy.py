from __future__ import annotations

from pathlib import Path

from shinobi_runtime.domain import LocationGraph
from shinobi_runtime.store import RepositoryStore


ROOT = Path(__file__).resolve().parents[2]
MAJOR_VILLAGES = ("suna", "kiri", "kumo", "iwa")


def test_location_hierarchy_is_acyclic_and_route_anchor_is_not_parentage() -> None:
    repo = RepositoryStore(ROOT)
    world = repo.read_json("state/world/routes-and-settlements.json")
    graph = LocationGraph(world)
    assert graph.parent("place.kiri.harbor") == "place.kiri"
    assert graph.settlement("place.kiri.harbor") == "place.kiri"
    # Harbor remains a strategic maritime node even though it is contained by Kiri.
    assert graph.anchor("place.kiri.harbor") == "place.kiri.harbor"
    assert graph.country("place.kiri.harbor") == "land_water"
    assert graph.parent("place.kumo.command") == "place.kumo"
    assert graph.anchor("place.kumo.command") == "place.kumo"


def test_major_village_operational_sites_are_typed_instead_of_ambient_placeholders() -> None:
    repo = RepositoryStore(ROOT)
    places = repo.read_json("state/world/routes-and-settlements.json")["payload"]["places"]
    for village in MAJOR_VILLAGES:
        prefix = f"place.{village}."
        children = [row for row in places if row["id"].startswith(prefix)]
        assert children
        assert all(row.get("kind") != "ambient_location" for row in children)
        assert all(row.get("parent_location_ref") == f"place.{village}" for row in children)


def test_strategic_and_local_access_routes_are_explicitly_separate() -> None:
    repo = RepositoryStore(ROOT)
    routes = repo.read_json("state/world/routes-and-settlements.json")["payload"]["routes"]
    assert routes
    assert {row["scope"] for row in routes} == {"strategic", "local_access"}
    assert all(row["knowledge_classification"] == "public" for row in routes)
    local_ids = {row["id"] for row in routes if row["scope"] == "local_access"}
    assert local_ids == {
        "route_kiri_harbor",
        "route_konoha_forty_fourth_training",
        "route_konoha_third_training",
    }
