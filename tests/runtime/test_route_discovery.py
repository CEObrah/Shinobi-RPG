from shinobi_runtime.api.route_discovery import discover_route_options


def _world():
    return {
        "payload": {
            "places": [
                {
                    "id": "place.manor",
                    "route_anchor_ref": "place.village",
                    "knowledge_classification": "public",
                },
                {
                    "id": "place.assignment.hall",
                    "route_anchor_ref": "place.village",
                    "knowledge_classification": "public",
                },
                {
                    "id": "place.village",
                    "knowledge_classification": "public",
                },
                {
                    "id": "place.border",
                    "knowledge_classification": "public",
                },
                {
                    "id": "place.border.outpost",
                    "route_anchor_ref": "place.border",
                    "knowledge_classification": "public",
                },
                {
                    "id": "place.unrelated",
                    "knowledge_classification": "public",
                },
            ],
            "routes": [
                {
                    "id": "route_village_border",
                    "from": "place.village",
                    "to": "place.border",
                    "mode": "road",
                    "status": "open",
                    "travel_days_band": [1, 2],
                    "reference_travel_days": 1.5,
                },
                {
                    "id": "route_village_unrelated",
                    "from": "place.village",
                    "to": "place.unrelated",
                    "mode": "road",
                    "status": "open",
                    "travel_days_band": [2, 3],
                    "reference_travel_days": 2.5,
                },
            ],
        }
    }


def _mechanics():
    return {
        "local_travel": {"reference_hours": 1.0},
        "route_status_multipliers": {"open": 1.0},
    }


def test_same_anchor_exposes_stable_local_route_without_graph_leakage():
    result = discover_route_options(
        _world(),
        _mechanics(),
        origin_id="place.manor",
        destination_id="place.assignment.hall",
    )

    assert result == {
        "origin_id": "place.manor",
        "origin_anchor_ref": "place.village",
        "destination_id": "place.assignment.hall",
        "destination_anchor_ref": "place.village",
        "route_options": [
            {
                "route_id": "route_local",
                "destination_id": "place.assignment.hall",
                "route_kind": "local",
                "reference_hours": 1.0,
                "requires_local_completion": False,
            }
        ],
        "options_truncated": False,
    }


def test_registered_route_is_scoped_to_requested_destination_anchor():
    result = discover_route_options(
        _world(),
        _mechanics(),
        origin_id="place.manor",
        destination_id="place.border",
    )

    assert [option["route_id"] for option in result["route_options"]] == [
        "route_village_border"
    ]
    assert result["route_options"][0]["destination_id"] == "place.border"
    assert result["route_options"][0]["requires_local_completion"] is False
    assert result["options_truncated"] is False


def test_subplace_destination_exposes_anchor_leg_then_local_completion():
    result = discover_route_options(
        _world(),
        _mechanics(),
        origin_id="place.manor",
        destination_id="place.border.outpost",
    )

    option = result["route_options"][0]
    assert option["route_id"] == "route_village_border"
    assert option["destination_id"] == "place.border"
    assert option["final_destination_id"] == "place.border.outpost"
    assert option["requires_local_completion"] is True


def test_same_place_returns_no_route_instead_of_noop_command_hint():
    result = discover_route_options(
        _world(),
        _mechanics(),
        origin_id="place.manor",
        destination_id="place.manor",
    )

    assert result["route_options"] == []
    assert result["options_truncated"] is False


def test_truncation_flag_means_routes_were_actually_omitted():
    world = _world()
    world["payload"]["routes"] = [
        {
            "id": f"route_parallel_{index:02d}",
            "from": "place.village",
            "to": "place.border",
            "mode": "road",
            "status": "open",
            "travel_days_band": [1, 2],
            "reference_travel_days": 1.5,
        }
        for index in range(17)
    ]

    exactly_sixteen = dict(world)
    exactly_sixteen["payload"] = dict(world["payload"])
    exactly_sixteen["payload"]["routes"] = world["payload"]["routes"][:16]
    result_16 = discover_route_options(
        exactly_sixteen,
        _mechanics(),
        origin_id="place.manor",
        destination_id="place.border",
    )
    result_17 = discover_route_options(
        world,
        _mechanics(),
        origin_id="place.manor",
        destination_id="place.border",
    )

    assert len(result_16["route_options"]) == 16
    assert result_16["options_truncated"] is False
    assert len(result_17["route_options"]) == 16
    assert result_17["options_truncated"] is True


def test_nonpublic_route_is_hidden_until_actor_has_exact_route_knowledge():
    world = _world()
    world["payload"]["routes"][0]["knowledge_classification"] = "restricted"

    hidden = discover_route_options(
        world,
        _mechanics(),
        origin_id="place.manor",
        destination_id="place.border",
    )
    known = discover_route_options(
        world,
        _mechanics(),
        origin_id="place.manor",
        destination_id="place.border",
        known_route_refs={"route_village_border"},
    )

    assert hidden["route_options"] == []
    assert [row["route_id"] for row in known["route_options"]] == ["route_village_border"]
