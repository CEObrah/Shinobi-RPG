from shinobi_runtime.api.gm_scene_context import build_gm_scene_context


COMBAT_REF = "combat:test:road_contact"
PLAYER_REF = "pc_wei_tang"


def _context(*, open_thread: bool = False):
    parley = {
        "combat_ref": COMBAT_REF,
        "target_ref": COMBAT_REF,
        "target_kind": "opposing_combat_side",
        "open_threads": [],
        "open_thread_count": 0,
    }
    if open_thread:
        parley["open_threads"] = [{
            "attempt_ref": "interaction_attempt_open",
            "at": "SE-0061-09-27T21:15:00",
            "action": "request",
            "player_statement": "Stand down.",
            "topic": "hostile_contact",
            "posture": "parley",
        }]
        parley["open_thread_count"] = 1

    return {
        "campaign": {
            "campaign_id": "jianghu-test",
            "player_id": PLAYER_REF,
            "revision": 9,
            "world_time": "SE-0061-09-27T21:21:45",
        },
        "player": {
            "person_id": PLAYER_REF,
            "current_location_id": "route.changan.huashan",
        },
        "scene": {
            "location_id": "route.changan.huashan",
            "present_person_ids": [PLAYER_REF],
            "visible_person_ids": [PLAYER_REF],
            "active_combat_ref": COMBAT_REF,
            "combat_parley": parley,
        },
        "active_scene_session": None,
        "recent_scene_history": [
            {
                "speech_ref": "scene_speech_old_unrelated",
                "at": "SE-0061-09-20T10:00:00",
                "session_ref": "scene_session:old",
                "speaker_ref": "npc.old",
                "speech_kind": "observation",
                "statement": "Old unrelated line.",
                "truth_status": "attributed_statement",
                "authority": False,
                "mechanical_consequence_authority": False,
            },
            {
                "speech_ref": "scene_speech_combat_reply",
                "at": "SE-0061-09-27T21:15:01",
                "session_ref": COMBAT_REF,
                "speaker_ref": COMBAT_REF,
                "speech_kind": "nonbinding_response",
                "statement": "No. Turn back.",
                "truth_status": "attributed_statement",
                "authority": False,
                "mechanical_consequence_authority": False,
            },
        ],
        "recent_interaction_attempts": [
            {
                "attempt_ref": "interaction_attempt_combat_request",
                "at": "SE-0061-09-27T21:15:00",
                "actor_ref": PLAYER_REF,
                "action": "request",
                "target_ref": COMBAT_REF,
                "target_kind": "opposing_combat_side",
                "process_ref": COMBAT_REF,
                "player_statement": "Stand down. If you do not, I will kill every one of you.",
                "posture": "parley",
                "topic": "hostile_contact",
                "thread_status": "responded",
                "resolved_at": "SE-0061-09-27T21:15:01",
                "response_ref": "scene_speech_combat_reply",
            },
        ],
    }


def test_active_combat_parley_survives_without_formal_scene_session():
    gm = build_gm_scene_context(_context())

    beats = gm["immediate_continuity"]
    assert [row["beat_kind"] for row in beats] == [
        "player_declared_action",
        "attributed_speech",
    ]
    assert beats[0]["player_statement"].startswith("Stand down")
    assert beats[1]["statement"] == "No. Turn back."
    assert all(row.get("speech_ref") != "scene_speech_old_unrelated" for row in beats)
    assert gm["scene_direction"]["recent_continuity_beat_count"] == 2
    assert gm["scene_direction"]["fresh_scene_entry"] is False
    assert gm["recent_player_action_count"] == 1


def test_active_combat_parley_open_thread_is_a_live_human_thread_without_formal_session():
    gm = build_gm_scene_context(_context(open_thread=True))

    assert gm["human_threads"] == [{
        "attempt_ref": "interaction_attempt_open",
        "at": "SE-0061-09-27T21:15:00",
        "action": "request",
        "topic": "hostile_contact",
        "posture": "parley",
        "target_ref": COMBAT_REF,
        "process_ref": COMBAT_REF,
        "thread_status": "open",
        "player_statement": "Stand down.",
    }]
    assert gm["scene_direction"]["open_human_thread_count"] == 1
    assert gm["scene_direction"]["continuation_mode"] == "continue_process_or_compress" or gm["scene_direction"]["continuation_mode"] == "present_people_may_initiate"
