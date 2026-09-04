from shinobi_runtime.martial_world.route_intelligence import route_intelligence_brief


def test_contract_route_intelligence_lists_public_road_threats_without_secret_intent():
    brief = route_intelligence_brief(
        "route.changan.lanzhou", source_place_ref="changan", destination_place_ref="lanzhou"
    )
    assert brief["source_place_ref"] == "changan"
    assert brief["destination_place_ref"] == "lanzhou"
    threats = brief["known_route_threats"]
    assert threats
    assert any(row["faction_ref"] == "faction.broken_tooth_gang" for row in threats)
    assert all(row["known_for"] for row in threats)
    assert all("combat_index" not in row for row in threats)
    assert all("attack" not in row for row in threats)
    local_refs = {row["faction_ref"] for row in brief["settlement_presence"]}
    assert "faction.broken_tooth_gang" in local_refs


def test_house_tang_private_estate_spur_has_no_fake_bandit_warning():
    brief = route_intelligence_brief("route.luoyang.rural_estates")
    assert brief["known_route_threats"] == []


def test_multi_city_route_intelligence_lists_every_crossed_settlement_and_leg_threats():
    from shinobi_runtime.martial_world.escort import hydrate_contract_escort_objective
    from shinobi_runtime.martial_world.route_intelligence import journey_intelligence_brief
    objective = hydrate_contract_escort_objective({
        "kind": "escort_party",
        "source_place_ref": "changan",
        "destination_place_ref": "chengdu",
        "protected_people_count": 4,
    })
    assert len(objective["route_refs"]) > 1
    brief = journey_intelligence_brief(
        objective["route_refs"],
        source_place_ref="changan",
        destination_place_ref="chengdu",
    )
    assert brief["places_crossed"][0] == "changan"
    assert brief["places_crossed"][-1] == "chengdu"
    assert "luoyang" in brief["places_crossed"]
    assert len(brief["legs"]) == len(objective["route_refs"])
    assert brief["known_route_threats"]
    assert all("combat_index" not in row for row in brief["known_route_threats"])
