from __future__ import annotations

import json
from pathlib import Path

from shinobi_runtime.commands.campaign_runtime_planner import CampaignCommandPlanner
from shinobi_runtime.store import RepositoryStore

ROOT = Path(__file__).resolve().parents[2]


def test_mission_markets_are_geographically_anchored_to_real_places() -> None:
    repo = RepositoryStore(ROOT)
    world = repo.read_json("state/world/economies-and-mission-markets.json")
    places = {
        row["id"]
        for row in repo.read_json("state/world/routes-and-settlements.json")["payload"]["places"]
    }
    markets = world["payload"]["economies_and_mission_markets"]["markets"]
    assert markets
    assert all(row["place_ref"] in places for row in markets)
    assert {row["id"]: row["place_ref"] for row in markets} == {
        "market_konoha_missions": "place.konoha",
        "market_suna_missions": "place.suna",
        "market_maritime_missions": "place.kiri.harbor",
    }


def test_institutional_market_routing_uses_acting_faction_not_konoha_default() -> None:
    planner = CampaignCommandPlanner(RepositoryStore(ROOT))
    mechanics = planner._operational_world_mechanics()["mission_market"]["objective_signal_candidates"]
    assert planner._owned_mission_market(
        "faction_suna", signal_candidates=mechanics["escort"]
    ) == ("market_suna_missions", "place.suna", "desert_escort")
    assert planner._owned_mission_market(
        "faction_kiri", signal_candidates=mechanics["protect"]
    ) == ("market_maritime_missions", "place.kiri.harbor", "shipping_security")
    assert planner._owned_mission_market(
        "faction_konoha", signal_candidates=mechanics["investigate"]
    ) == ("market_konoha_missions", "place.konoha", "investigation")


def test_every_objective_mapping_has_a_live_market_consumer() -> None:
    repo = RepositoryStore(ROOT)
    mechanics = repo.read_json("game/data/mechanics/operational-world.json")["mission_market"]
    markets = repo.read_json("state/world/economies-and-mission-markets.json")["payload"]["economies_and_mission_markets"]["markets"]
    demand = {signal for row in markets for signal in row["demand"]}
    for candidates in mechanics["objective_signal_candidates"].values():
        assert any(signal in demand for signal in candidates)
