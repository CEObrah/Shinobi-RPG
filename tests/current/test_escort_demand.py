from datetime import datetime

import shinobi_runtime.martial_world.escort as escort


def _world(population: int = 100_000):
    geography = {
        "places": {
            "a": {"climate_profile": "central_plain"},
            "b": {"climate_profile": "temperate_mountain"},
        },
        "routes": [
            {"id": "route.test", "from": "a", "to": "b"},
        ],
    }
    civilians = {
        "places": {
            "a": {"current_population": population},
            "b": {"current_population": population},
        }
    }
    return geography, civilians


def test_most_civilian_travel_does_not_become_paid_martial_escort(monkeypatch):
    geography, civilians = _world()
    monkeypatch.setattr(escort, "_stable_int", lambda *parts: 999)
    rows = escort._party_demand(
        geography=geography,
        civilian_state=civilians,
        at=datetime(61, 9, 1),
    )
    assert rows == []


def test_paid_travel_demand_supports_non_cargo_social_parties(monkeypatch):
    geography, civilians = _world()
    # 35 is inside the incidence window for a large settlement and maps to the
    # official/envoy entry in the deterministic party-kind cycle.
    monkeypatch.setattr(escort, "_stable_int", lambda *parts: 35)
    rows = escort._party_demand(
        geography=geography,
        civilian_state=civilians,
        at=datetime(61, 9, 1),
    )
    assert len(rows) == 2
    assert {row["civilian_party_kind"] for row in rows} == {"official_envoy_party"}
    assert all(row["protected_people_count"] >= 3 for row in rows)
    assert all(row["source_region"] != row["destination_region"] for row in rows)
