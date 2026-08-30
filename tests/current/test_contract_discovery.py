from shinobi_runtime.api.contract_visibility import (
    compact_contract_discovery_rows,
    contract_is_player_visible,
    player_visible_contract_rows,
)


def _contract(*, status="offered", beneficiary=None, participants=None, expires="0061-09-01T00:00:00"):
    return {
        "contract_id": "contract.demo",
        "contract_type": "escort",
        "issuer_ref": "market:central_plains",
        "beneficiary_ref": beneficiary,
        "status": status,
        "offered_at": "0061-08-01T00:00:00",
        "expires_at": expires,
        "reward_cash": 123,
        "objective": {
            "kind": "escort_shipment",
            "source_place_ref": "luoyang",
            "destination_place_ref": "changan",
            "item_ref": "food_ration_day",
            "quantity": 1,
        },
        "participants": list(participants or []),
    }


def test_public_unclaimed_offer_is_discoverable_until_expiry():
    row = _contract()
    assert contract_is_player_visible(
        row,
        player_id="pc_wei_tang",
        faction_ref="house_tang",
        world_time="SE-0061-08-14T21:15:00",
    )
    assert not contract_is_player_visible(
        row,
        player_id="pc_wei_tang",
        faction_ref="house_tang",
        world_time="SE-0061-09-02T00:00:00",
    )


def test_claimed_contract_is_visible_only_to_involved_player_or_faction():
    claimed = _contract(status="accepted", beneficiary="house_tang", participants=["pc_wei_tang"])
    assert contract_is_player_visible(
        claimed,
        player_id="pc_wei_tang",
        faction_ref="house_tang",
        world_time="SE-0061-08-14T21:15:00",
    )
    assert not contract_is_player_visible(
        claimed,
        player_id="pc_other",
        faction_ref="other_house",
        world_time="SE-0061-08-14T21:15:00",
    )


def test_discovery_returns_exact_refs_readable_money_and_physical_terms():
    index = {"active": {"contract.demo": _contract()}}
    rows = player_visible_contract_rows(
        index,
        player_id="pc_wei_tang",
        faction_ref="house_tang",
        world_time="SE-0061-08-14T21:15:00",
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["object_ref"] == "contract:contract.demo"
    assert row["contract_ref"] == "contract.demo"
    assert row["contract_type"] == "escort"
    assert row["status"] == "offered"
    assert row["issuer_ref"] == "market:central_plains"
    assert row["beneficiary_ref"] is None
    assert row["reward_cash"] == 123
    assert row["reward_display"] == "123 copper"
    assert row["objective_kind"] == "escort_shipment"
    assert row["escort_kind"] == "cargo"
    assert row["minimum_escort_count"] == 6
    # Aggregate logistics exposes required freight/crew capacity rather than
    # pretending ordinary wagons, draft teams or pack animals are exact objects.
    for key in (
        "route_ref", "distance_km_tenths", "expected_travel_hours", "terrain",
        "road_quality", "item_ref", "quantity", "cargo_mass_kg", "cargo_value_cash",
        "transport_mode", "freight_capacity_kg",
        "civilian_crew_count", "protected_person_refs", "protected_people_count",
        "threat_score",
    ):
        assert key in row


def test_discovery_limit_is_transport_only_and_optional():
    active = {f"contract.{i:03}": {**_contract(), "contract_id": f"contract.{i:03}"} for i in range(80)}
    full = player_visible_contract_rows(
        {"active": active}, player_id="pc_wei_tang", faction_ref="house_tang",
        world_time="SE-0061-08-14T21:15:00",
    )
    page = player_visible_contract_rows(
        {"active": active}, player_id="pc_wei_tang", faction_ref="house_tang",
        world_time="SE-0061-08-14T21:15:00", limit=25,
    )
    assert len(full) == 80
    assert len(page) == 25



def test_discovery_omits_malformed_escort_without_current_endpoints():
    malformed = _contract()
    malformed["objective"] = {"kind": "escort_shipment", "item_ref": "food_ration_day", "quantity": 1}
    rows = player_visible_contract_rows(
        {"active": {"contract.demo": malformed}},
        player_id="pc_wei_tang", faction_ref="house_tang",
        world_time="SE-0061-08-14T21:15:00",
    )
    assert rows == []

def test_contract_discovery_surfaces_compact_public_route_risk_brief():
    contract = _contract()
    contract["objective"] = {
        "kind": "escort_shipment",
        "route_ref": "route.luoyang.changan",
        "source_place_ref": "luoyang",
        "destination_place_ref": "changan",
        "item_ref": "food_ration_day",
        "quantity": 10,
        "cargo_value_cash": 270,
    }
    rows = player_visible_contract_rows(
        {"active": {"contract.demo": contract}},
        player_id="pc_wei_tang",
        faction_ref="house_tang",
        world_time="SE-0061-08-14T21:15:00",
    )
    intel = rows[0]["route_intelligence"]
    assert intel["route_ref"] == "route.luoyang.changan"
    assert intel["known_route_threats"]
    assert intel["settlement_presence_count"] > 0
    # Full endpoint listings belong to exact contract inspection, not the
    # repeated play-context contract list.
    assert "settlement_presence" not in intel


def test_play_context_contract_discovery_is_compact_and_exact_terms_are_demand_loaded():
    rows = [{
        "object_ref": "contract:contract.example",
        "contract_ref": "contract.example",
        "contract_type": "escort",
        "status": "offered",
        "issuer_ref": "market:central_plain",
        "reward_cash": 12345,
        "reward_display": "12 taels, 345 copper",
        "expires_at": "0061-10-13T21:15:00",
        "objective_kind": "escort_shipment",
        "route_refs": ["route.a.b"],
        "places_crossed": ["a", "b"],
        "item_ref": "food_ration_day",
        "quantity": 12000,
        "cargo_mass_kg": 12000,
        "freight_capacity_kg": 12000,
        "minimum_escort_count": 8,
        "threat_score": 31,
        "route_intelligence": {"known_route_threats": ["large repeated payload"]},
    }]
    compact = compact_contract_discovery_rows(rows)
    assert compact == [{
        "object_ref": "contract:contract.example",
        "contract_ref": "contract.example",
        "contract_type": "escort",
        "status": "offered",
        "issuer_ref": "market:central_plain",
        "reward_display": "12 taels, 345 copper",
        "expires_at": "0061-10-13T21:15:00",
        "objective_kind": "escort_shipment",
        "route_refs": ["route.a.b"],
        "places_crossed": ["a", "b"],
        "item_ref": "food_ration_day",
        "quantity": 12000,
        "minimum_escort_count": 8,
        "threat_score": 31,
    }]


def test_contract_discovery_can_skip_route_intelligence_for_every_turn_context():
    index = {"active": {"contract.demo": _contract()}}
    rows = player_visible_contract_rows(
        index, player_id="pc_wei_tang", faction_ref="house_tang",
        world_time="SE-0061-08-14T21:15:00", include_route_intelligence=False,
    )
    assert len(rows) == 1
    assert rows[0]["route_intelligence"] is None
