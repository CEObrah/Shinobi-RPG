import copy

from shinobi_runtime.martial_world.scene_sessions import (
    ATTEMPT_LEDGER_PATH,
    HISTORY_HEAD_PATH,
    SESSION_PATH,
    append_attributed_speech,
    close_active_session_writes,
    new_session_record,
    resolve_question,
)


class _Records:
    def __init__(self, rows=None):
        self.rows = copy.deepcopy(rows or {})

    def read_json(self, path):
        if path not in self.rows:
            raise FileNotFoundError(path)
        return copy.deepcopy(self.rows[path])

    def apply(self, writes):
        for path, value in writes.items():
            self.rows[path] = copy.deepcopy(value)


def _active_session():
    return new_session_record(
        session_ref="scene_session_test",
        kind="house_council",
        location_ref="site.house_tang.hall",
        participant_refs=["pc_wei_tang", "npc.father"],
        at="0061-09-14T09:15:00",
        process_ref="house_council.test",
        purpose="Discuss the escort assignment.",
        agenda=["route", "command", "supplies"],
    )


def test_scene_close_abandons_unanswered_questions_instead_of_leaving_recent_asks_active():
    session = _active_session()
    session["open_question_refs"] = ["interaction_attempt_q1"]
    ledger = {
        "schema": "jianghu-interaction-attempt-ledger-1.0",
        "authority": False,
        "mechanical_consequence_authority": False,
        "total_recorded": 1,
        "attempts": [
            {
                "attempt_ref": "interaction_attempt_q1",
                "at": "0061-09-14T09:16:00",
                "actor_ref": "pc_wei_tang",
                "target_ref": "npc.father",
                "action": "ask",
                "player_statement": "Who should command the escort?",
                "scene_session_ref": "scene_session_test",
                "thread_status": "open",
                "resolved_at": None,
                "response_ref": None,
            }
        ],
    }
    store = _Records({SESSION_PATH: session, ATTEMPT_LEDGER_PATH: ledger})

    writes = close_active_session_writes(
        store.read_json, at="0061-09-14T09:30:00", reason="completed"
    )

    assert writes[SESSION_PATH]["status"] == "closed"
    assert writes[SESSION_PATH]["close_reason"] == "completed"
    question = writes[ATTEMPT_LEDGER_PATH]["attempts"][0]
    assert question["thread_status"] == "abandoned_with_scene_close"
    assert question["resolved_at"] == "0061-09-14T09:30:00"
    assert question["response_ref"] is None


def test_attributed_speech_is_durable_but_explicitly_non_mechanical():
    store = _Records({SESSION_PATH: _active_session()})
    speech = {
        "speech_ref": "scene_speech_test",
        "at": "0061-09-14T09:20:00",
        "session_ref": "scene_session_test",
        "speaker_ref": "npc.father",
        "speech_kind": "advice",
        "statement": "Keep the escort together until the road is known.",
        "basis_refs": ["house_council.test"],
        "resolves_question_ref": None,
        "truth_status": "attributed_statement",
        "authority": False,
        "mechanical_consequence_authority": False,
    }

    writes = append_attributed_speech(store.read_json, row=speech)
    store.apply(writes)

    assert store.rows[HISTORY_HEAD_PATH]["total_recorded"] == 1
    recent = store.rows[HISTORY_HEAD_PATH]["recent"][-1]
    assert recent["speech_ref"] == "scene_speech_test"
    assert recent["truth_status"] == "attributed_statement"
    assert recent["authority"] is False
    assert recent["mechanical_consequence_authority"] is False
    shard_paths = [path for path in writes if path != HISTORY_HEAD_PATH]
    assert len(shard_paths) == 1
    archived = store.rows[shard_paths[0]]["records"][-1]
    assert archived == recent


def test_answer_resolution_links_question_to_attributed_response():
    ledger = {
        "schema": "jianghu-interaction-attempt-ledger-1.0",
        "authority": False,
        "mechanical_consequence_authority": False,
        "total_recorded": 1,
        "attempts": [
            {
                "attempt_ref": "interaction_attempt_q1",
                "action": "ask",
                "player_statement": "What do you advise?",
                "scene_session_ref": "scene_session_test",
                "thread_status": "open",
                "resolved_at": None,
                "response_ref": None,
            }
        ],
    }

    after, changed = resolve_question(
        ledger,
        question_ref="interaction_attempt_q1",
        response_ref="scene_speech_a1",
        at="0061-09-14T09:21:00",
    )

    assert changed is True
    row = after["attempts"][0]
    assert row["thread_status"] == "answered"
    assert row["response_ref"] == "scene_speech_a1"
    assert row["resolved_at"] == "0061-09-14T09:21:00"
