from shinobi_runtime.api.contract_visibility import (
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
        "objective": {"kind": "escort_shipment", "minimum_escort_count": 2},
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
    assert row["minimum_escort_count"] == 2
    # Legacy objectives lawfully render unknown physical terms as zero/None;
    # policy-v3 offers populate these fields rather than hiding logistics.
    for key in (
        "route_ref", "distance_km_tenths", "expected_travel_hours", "terrain",
        "road_quality", "item_ref", "quantity", "cargo_mass_kg", "cargo_value_cash",
        "transport_mode", "wagon_count", "pack_animal_count", "draft_animal_count",
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
