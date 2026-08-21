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


def test_discovery_returns_exact_inspectable_refs_and_bounded_terms():
    index = {"active": {"contract.demo": _contract()}}
    rows = player_visible_contract_rows(
        index,
        player_id="pc_wei_tang",
        faction_ref="house_tang",
        world_time="SE-0061-08-14T21:15:00",
    )
    assert rows == [{
        "object_ref": "contract:contract.demo",
        "contract_ref": "contract.demo",
        "contract_type": "escort",
        "status": "offered",
        "issuer_ref": "market:central_plains",
        "beneficiary_ref": None,
        "reward_cash": 123,
        "expires_at": "0061-09-01T00:00:00",
        "objective_kind": "escort_shipment",
        "minimum_escort_count": 2,
    }]
