from shinobi_runtime.api.parley_operations import combat_parley_scene_projection


COMBAT_REF = "combat:test:parley"
PLAYER_REF = "pc.test"


def _attempt(*, ref: str, target_ref: str = COMBAT_REF, target_kind: str = "opposing_combat_side", thread_status: str = "open", resolved_at=None, response_ref=None):
    return {
        "attempt_ref": ref,
        "at": "SE-0061-09-27T21:15:00",
        "surface_digest": "digest-" + ref,
        "actor_ref": PLAYER_REF,
        "target_ref": target_ref,
        "target_kind": target_kind,
        "action": "ask",
        "process_ref": COMBAT_REF,
        "player_statement": "State your business.",
        "posture": "parley",
        "topic": "hostile_contact",
        "scopes": [],
        "world_response_status": "not_established_by_attempt",
        "scene_session_ref": None,
        "thread_status": thread_status,
        "resolved_at": resolved_at,
        "response_ref": response_ref,
    }


def test_projection_recovers_legacy_current_combat_question_as_open():
    ledger = {
        "schema": "jianghu-interaction-attempt-ledger-1.0",
        "authority": False,
        "mechanical_consequence_authority": False,
        "total_recorded": 2,
        "attempts": [
            _attempt(ref="interaction_attempt_old", target_ref="combat:test:old", thread_status="not_applicable"),
            _attempt(ref="interaction_attempt_current", thread_status="not_applicable"),
        ],
    }

    projected = combat_parley_scene_projection(
        ledger=ledger,
        recent_history=[],
        combat_ref=COMBAT_REF,
        player_id=PLAYER_REF,
    )

    assert projected["combat_ref"] == COMBAT_REF
    assert projected["target_kind"] == "opposing_combat_side"
    assert projected["open_question_count"] == 1
    assert projected["open_questions"][0]["attempt_ref"] == "interaction_attempt_current"
    assert projected["open_questions"][0]["player_statement"] == "State your business."
    assert projected["response_recording"]["session_ref"] == COMBAT_REF
    assert projected["response_recording"]["speaker_ref"] == COMBAT_REF
    assert projected["response_recording"]["mechanical_consequence_authority"] is False
    assert projected["identity_policy"] == "opposing_person_ids_remain_hidden"


def test_projection_excludes_answered_person_and_other_combat_questions():
    ledger = {
        "attempts": [
            _attempt(
                ref="interaction_attempt_answered",
                thread_status="answered",
                resolved_at="SE-0061-09-27T21:15:00",
                response_ref="scene_speech_answer",
            ),
            _attempt(ref="interaction_attempt_person", target_ref="char.someone", target_kind="person"),
            _attempt(ref="interaction_attempt_other_combat", target_ref="combat:test:other"),
        ]
    }

    projected = combat_parley_scene_projection(
        ledger=ledger,
        recent_history=[],
        combat_ref=COMBAT_REF,
        player_id=PLAYER_REF,
    )

    assert projected["open_question_count"] == 0
    assert projected["open_questions"] == []


def test_projection_exposes_only_group_attributed_speech_for_current_combat():
    history = [
        {
            "speech_ref": "scene_speech_hidden_person",
            "at": "SE-0061-09-27T21:15:00",
            "session_ref": COMBAT_REF,
            "speaker_ref": "enemy.hidden.1",
            "speech_kind": "nonbinding_response",
            "statement": "Hidden identity should not project.",
            "basis_refs": [],
            "resolves_question_ref": None,
            "truth_status": "attributed_statement",
            "authority": False,
            "mechanical_consequence_authority": False,
        },
        {
            "speech_ref": "scene_speech_other_combat",
            "at": "SE-0061-09-27T21:15:00",
            "session_ref": "combat:test:other",
            "speaker_ref": "combat:test:other",
            "speech_kind": "nonbinding_response",
            "statement": "Other fight.",
            "basis_refs": [],
            "resolves_question_ref": None,
            "truth_status": "attributed_statement",
            "authority": False,
            "mechanical_consequence_authority": False,
        },
        {
            "speech_ref": "scene_speech_current",
            "at": "SE-0061-09-27T21:15:00",
            "session_ref": COMBAT_REF,
            "speaker_ref": COMBAT_REF,
            "speech_kind": "nonbinding_response",
            "statement": "Turn back.",
            "basis_refs": [COMBAT_REF],
            "resolves_question_ref": None,
            "truth_status": "attributed_statement",
            "authority": False,
            "mechanical_consequence_authority": False,
        },
    ]

    projected = combat_parley_scene_projection(
        ledger={"attempts": []},
        recent_history=history,
        combat_ref=COMBAT_REF,
        player_id=PLAYER_REF,
    )

    latest = projected["latest_opposing_speech"]
    assert latest["speech_ref"] == "scene_speech_current"
    assert latest["speaker_ref"] == COMBAT_REF
    assert "enemy.hidden.1" not in repr(projected)
