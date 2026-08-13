import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read_json(relative: str):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def repository_routes():
    root = read_json("runtime/contracts/repository-map.json")
    routes = dict(root["routes"])
    for relative in root["route_shards"].values():
        routes.update(read_json(relative)["routes"])
    return routes


def test_every_repository_route_domain_resolves_to_the_rule_router():
    domains = read_json("runtime/contracts/rule-router.json")["domains"]
    missing = {
        name: route["domain"]
        for name, route in repository_routes().items()
        if "domain" in route and route["domain"] not in domains
    }
    assert missing == {}


def test_mission_lifecycle_routes_to_its_registered_update_authority():
    domains = read_json("runtime/contracts/rule-router.json")["domains"]
    assert domains["mission_lifecycle"] == [
        "runtime/contracts/system-contracts/missions_projects.json"
    ]
    assert repository_routes()["mission_runtime_known_id"]["domain"] == (
        "mission_lifecycle"
    )
