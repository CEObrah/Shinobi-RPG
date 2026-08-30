from shinobi_runtime.api.command_discovery import build_scene_header, compact_play_context


def test_scene_header_formats_canonical_world_time_and_route_setting():
    header = build_scene_header("SE-0061-09-27T21:15:00", "route.changan.huashan")

    assert header == {
        "date_text": "SE 61, 9th month, 27th day",
        "time_text": "21:15",
        "location_text": "Chang'an to Mount Hua Road",
        "text": "SE 61, 9th month, 27th day | 21:15 | Chang'an to Mount Hua Road",
    }


def test_scene_header_uses_canonical_place_name_and_never_guesses_unknown_location():
    assert build_scene_header("SE-0061-01-01T08:03:00", "changan")["location_text"] == "Chang'an"
    assert build_scene_header("SE-0061-01-01T08:03:00", "site.unknown.example")["location_text"] == "site.unknown.example"


def test_scene_header_preserves_unparsed_time_instead_of_inventing_conversion():
    header = build_scene_header("unexpected-calendar-value", "huashan")

    assert header["date_text"] == "unexpected-calendar-value"
    assert header["time_text"] == "Time unavailable"
    assert header["location_text"] == "Mount Hua"


def test_compact_play_context_always_projects_header_and_exact_render_contract():
    context = {
        "campaign": {"world_time": "SE-0061-09-27T21:15:00"},
        "scene": {"location_id": "route.changan.huashan"},
        "player": {"current_location_id": "changan"},
        "commands": {"supported_command_types": [], "limits": {}},
    }

    compact = compact_play_context(context)

    assert compact["scene_header"]["text"] == "SE 61, 9th month, 27th day | 21:15 | Chang'an to Mount Hua Road"
    assert compact["presentation_contract"] == {
        "ic_turn_header_required": True,
        "ic_turn_header_position": "first_visible_line",
        "ic_turn_header_source": "scene_header.text",
        "ic_turn_header_render_exactly": True,
        "calendar_conversion_rule": "Do not convert the canonical era unless a registered mapping explicitly provides that conversion.",
    }
