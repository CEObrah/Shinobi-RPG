import copy

from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.jianghu_scene import JianghuSceneCommandsMixin
from shinobi_runtime.martial_world.scene_sessions import ATTEMPT_LEDGER_PATH, SESSION_PATH, active_scene_thread_page
from shinobi_runtime.sim.events import CampaignTime


PLAYER = "pc.test"
NPC = "npc.test"
SITE = "site.test.hall"


class _Repo:
    def __init__(self):
        self.rows = {
            "state/scene.json": {
                "present_person_ids": [PLAYER, NPC],
                "visible_person_ids": [PLAYER, NPC],
            },
        }

    def read_json(self, path):
        if path not in self.rows:
            raise FileNotFoundError(path)
        return copy.deepcopy(self.rows[path])


class _Harness(JianghuSceneCommandsMixin):
    def __init__(self):
        self.repository = _Repo()
        self.scene_path = "state/scene.json"
        self.people = {
            PLAYER: {"person_id": PLAYER, "location_ref": SITE},
            NPC: {"person_id": NPC, "location_ref": SITE},
        }

    def _person(self, ref):
        person = copy.deepcopy(self.people[ref])
        return f"people:{ref}", {"people": [person]}, 0, person

    def _simple_plan(self, command, meta, current_time, *, writes_records, code, result):
        return {
            "writes_records": writes_records,
            "code": code,
            "result": result,
            "world_time": str(current_time),
        }


def _request():
    return CommandEnvelope(
        campaign_id="campaign.test",
        request_id="request.scene.auto-open",
        actor_id=PLAYER,
        command_type="jianghu_interaction_resolution",
        expected_revision=1,
        submitted_at="SE-0061-01-01T00:00:00",
        payload={
            "action": "request",
            "target_ref": NPC,
            "player_statement": "Tell me what you think I should do.",
            "topic": "advice",
        },
    )


def test_response_bearing_person_interaction_auto_opens_nonmechanical_conversation():
    harness = _Harness()
    now = CampaignTime.parse("SE-0061-01-01T00:00:00")

    built = harness._jianghu_interaction_resolution(_request(), {}, now)

    assert built["code"] == "jianghu_interaction_recorded"
    session = built["writes_records"][SESSION_PATH]
    assert session["authority"] is False
    assert session["mechanical_consequence_authority"] is False
    assert session["kind"] == "conversation"
    assert session["participant_refs"] == [PLAYER, NPC]
    ledger = built["writes_records"][ATTEMPT_LEDGER_PATH]
    row = ledger["attempts"][-1]
    assert row["scene_session_ref"] == session["session_ref"]
    assert row["thread_kind"] == "conversation"
    assert row["thread_status"] == "open"
    assert row["world_response_status"] == "not_established_by_attempt"


def test_active_scene_thread_page_recovers_older_threads_hidden_by_hot_window():
    harness = _Harness()
    session_ref = "scene_session_long_conversation"
    refs = [f"thread_{index:02d}" for index in range(20)]
    harness.repository.rows[SESSION_PATH] = {
        "schema": "jianghu-scene-session-1.0",
        "session_ref": session_ref,
        "status": "active",
        "open_thread_refs": refs,
    }
    harness.repository.rows[ATTEMPT_LEDGER_PATH] = {
        "schema": "jianghu-interaction-attempt-ledger-1.0",
        "authority": False,
        "mechanical_consequence_authority": False,
        "total_recorded": len(refs),
        "attempts": [
            {
                "attempt_ref": ref,
                "actor_ref": PLAYER,
                "target_ref": NPC,
                "at": f"SE-0061-01-01T00:{index:02d}:00",
                "action": "request",
                "player_statement": f"Request {index}",
                "scene_session_ref": session_ref,
                "thread_status": "open",
                "thread_kind": "conversation",
            }
            for index, ref in enumerate(refs)
        ],
    }

    first = active_scene_thread_page(harness.repository.read_json)
    assert first["count"] == 20
    assert [row["attempt_ref"] for row in first["threads"]] == refs[:16]
    assert first["truncated"] is True
    assert first["next_cursor"] == "16"
    second = active_scene_thread_page(harness.repository.read_json, cursor=first["next_cursor"])
    assert [row["attempt_ref"] for row in second["threads"]] == refs[16:]
    assert second["truncated"] is False


def test_salient_reversible_scene_fact_records_continuity_without_hard_authority():
    from shinobi_runtime.martial_world.scene_sessions import HISTORY_HEAD_PATH, new_session_record

    harness = _Harness()
    now = CampaignTime.parse("SE-0061-01-01T00:01:00")
    session = new_session_record(
        session_ref="scene_session_fact_test",
        kind="conversation",
        location_ref=SITE,
        participant_refs=[PLAYER, NPC],
        at=str(now),
        purpose="Test reversible local continuity.",
    )
    harness.repository.rows[SESSION_PATH] = session
    command = CommandEnvelope(
        campaign_id="campaign.test",
        request_id="request.scene.record-fact",
        actor_id=PLAYER,
        command_type="jianghu_scene_session_resolution",
        expected_revision=1,
        submitted_at=str(now),
        payload={
            "action": "record_fact",
            "session_ref": session["session_ref"],
            "actor_ref": PLAYER,
            "fact_kind": "object_state",
            "description": "Wei places the established bowl on Kai's head.",
            "participant_refs": [NPC],
            "basis_refs": [PLAYER, NPC],
        },
    )

    built = harness._jianghu_scene_session_resolution(command, {}, now)

    assert built["code"] == "jianghu_scene_fact_recorded"
    assert built["result"]["mechanical_consequence_authority"] is False
    head = built["writes_records"][HISTORY_HEAD_PATH]
    fact = head["recent"][-1]
    assert fact["fact_kind"] == "object_state"
    assert fact["truth_status"] == "observed_reversible_scene_fact"
    assert fact["scope"] == "scene_local_history_only"
    assert fact["authority"] is False
    assert fact["mechanical_consequence_authority"] is False


def test_reversible_scene_fact_cannot_shadow_active_combat_geometry():
    from shinobi_runtime.api.contracts import CommandRejectedError
    from shinobi_runtime.martial_world.scene_sessions import new_session_record

    harness = _Harness()
    now = CampaignTime.parse("SE-0061-01-01T00:02:00")
    session = new_session_record(
        session_ref="scene_session_combat_guard",
        kind="conversation",
        location_ref=SITE,
        participant_refs=[PLAYER, NPC],
        at=str(now),
    )
    harness.repository.rows[SESSION_PATH] = session
    harness.repository.rows["state/martial-world/combats.json"] = {
        "combats": {
            "combat.test": {
                "status": "active",
                "zone_ref": SITE,
                "elapsed_ms": 0,
                "combatants": {PLAYER: {}, NPC: {}},
                "sides": {"side_a": [PLAYER], "side_b": [NPC]},
            }
        }
    }
    command = CommandEnvelope(
        campaign_id="campaign.test",
        request_id="request.scene.combat-fact-guard",
        actor_id=PLAYER,
        command_type="jianghu_scene_session_resolution",
        expected_revision=1,
        submitted_at=str(now),
        payload={
            "action": "record_fact",
            "session_ref": session["session_ref"],
            "actor_ref": PLAYER,
            "fact_kind": "positioning",
            "description": "Wei steps behind the opponent during the fight.",
            "participant_refs": [NPC],
            "basis_refs": [PLAYER, NPC],
        },
    )

    import pytest
    with pytest.raises(CommandRejectedError, match="jianghu_scene_fact_requires_combat_authority"):
        harness._jianghu_scene_session_resolution(command, {}, now)


def test_targeted_speak_is_response_bearing_without_extra_flag():
    harness = _Harness()
    now = CampaignTime.parse("SE-0061-01-01T00:03:00")
    command = CommandEnvelope(
        campaign_id="campaign.test",
        request_id="request.scene.speak-thread",
        actor_id=PLAYER,
        command_type="jianghu_interaction_resolution",
        expected_revision=1,
        submitted_at=str(now),
        payload={
            "action": "speak",
            "target_ref": NPC,
            "player_statement": "You knew this road was dangerous and said nothing.",
            "topic": "accusation",
        },
    )

    built = harness._jianghu_interaction_resolution(command, {}, now)

    session = built["writes_records"][SESSION_PATH]
    row = built["writes_records"][ATTEMPT_LEDGER_PATH]["attempts"][-1]
    assert row["expects_response"] is True
    assert row["thread_kind"] == "conversation"
    assert row["thread_status"] == "open"
    assert row["attempt_ref"] in session["open_thread_refs"]


def test_explicit_no_response_suppresses_default_speak_thread():
    harness = _Harness()
    now = CampaignTime.parse("SE-0061-01-01T00:04:00")
    command = CommandEnvelope(
        campaign_id="campaign.test", request_id="request.scene.final-speak", actor_id=PLAYER,
        command_type="jianghu_interaction_resolution", expected_revision=1, submitted_at=str(now),
        payload={"action": "speak", "target_ref": NPC, "player_statement": "Enough. We're done here.", "expects_response": False},
    )
    built = harness._jianghu_interaction_resolution(command, {}, now)
    row = built["writes_records"][ATTEMPT_LEDGER_PATH]["attempts"][-1]
    assert row["expects_response"] is False
    assert row["thread_status"] == "not_applicable"
    assert SESSION_PATH not in built["writes_records"]


def test_typed_improvised_prop_requires_prior_object_fact_and_stays_nonmechanical():
    import pytest
    from shinobi_runtime.api.contracts import CommandRejectedError
    from shinobi_runtime.martial_world.scene_sessions import HISTORY_HEAD_PATH, new_session_record

    harness = _Harness()
    now = CampaignTime.parse("SE-0061-01-01T00:04:00")
    session = new_session_record(
        session_ref="scene_session_improvised_prop",
        kind="conversation",
        location_ref=SITE,
        participant_refs=[PLAYER, NPC],
        at=str(now),
    )
    harness.repository.rows[SESSION_PATH] = session

    source = CommandEnvelope(
        campaign_id="campaign.test", request_id="request.scene.prop-source", actor_id=PLAYER,
        command_type="jianghu_scene_session_resolution", expected_revision=1, submitted_at=str(now),
        payload={
            "action":"record_fact","session_ref":session["session_ref"],"actor_ref":PLAYER,
            "fact_kind":"object_state","description":"A ceramic bowl is already on the table within reach.",
            "participant_refs":[NPC],"basis_refs":[PLAYER,NPC],
            "improvised_prop":{"form":"small_rigid","material":"ceramic","condition":"intact"},
        },
    )
    source_plan = harness._jianghu_scene_session_resolution(source, {}, now)
    for path, row in source_plan["writes_records"].items():
        harness.repository.rows[path] = copy.deepcopy(row)
    source_ref = source_plan["result"]["fact_ref"]

    typed = CommandEnvelope(
        campaign_id="campaign.test", request_id="request.scene.prop-typed", actor_id=PLAYER,
        command_type="jianghu_scene_session_resolution", expected_revision=1, submitted_at=str(now),
        payload={
            "action":"record_fact","session_ref":session["session_ref"],"actor_ref":PLAYER,
            "fact_kind":"object_state","description":"Wei lifts the already-established ceramic bowl into his hand.",
            "participant_refs":[NPC],"basis_refs":[source_ref],
            "improvised_prop":{"form":"small_rigid","material":"ceramic","condition":"intact"},
        },
    )
    typed_plan = harness._jianghu_scene_session_resolution(typed, {}, now)
    fact = typed_plan["writes_records"][HISTORY_HEAD_PATH]["recent"][-1]
    assert fact["basis_refs"] == [source_ref]
    assert fact["source_object_fact_ref"] == source_ref
    assert fact["improvised_prop"] == {
        "kind":"mundane_improvised_prop","form":"small_rigid","material":"ceramic","condition":"intact",
    }
    assert fact["mechanical_consequence_authority"] is False

    swapped = CommandEnvelope(
        campaign_id="campaign.test", request_id="request.scene.prop-swap", actor_id=PLAYER,
        command_type="jianghu_scene_session_resolution", expected_revision=1, submitted_at=str(now),
        payload={
            "action":"record_fact","session_ref":session["session_ref"],"actor_ref":PLAYER,
            "fact_kind":"object_state","description":"Wei treats the established ceramic bowl as a heavy metal bar.",
            "participant_refs":[],"basis_refs":[source_ref],
            "improvised_prop":{"form":"heavy_rigid","material":"metal","condition":"intact"},
        },
    )
    with pytest.raises(CommandRejectedError, match="descriptor_mismatch"):
        harness._jianghu_scene_session_resolution(swapped, {}, now)


def test_attributed_speech_may_cite_prior_history_from_same_active_session():
    from shinobi_runtime.martial_world.scene_sessions import HISTORY_HEAD_PATH, new_session_record

    harness = _Harness()
    now = CampaignTime.parse("SE-0061-01-01T00:05:00")
    session = new_session_record(
        session_ref="scene_session_history_basis",
        kind="conversation",
        location_ref=SITE,
        participant_refs=[PLAYER, NPC],
        at=str(now),
    )
    harness.repository.rows[SESSION_PATH] = session

    fact_command = CommandEnvelope(
        campaign_id="campaign.test", request_id="request.scene.history-basis-fact", actor_id=PLAYER,
        command_type="jianghu_scene_session_resolution", expected_revision=1, submitted_at=str(now),
        payload={
            "action":"record_fact", "session_ref":session["session_ref"], "actor_ref":PLAYER,
            "fact_kind":"object_state", "description":"A tea bowl is already on the table.",
            "participant_refs":[NPC], "basis_refs":[PLAYER,NPC],
        },
    )
    fact_plan = harness._jianghu_scene_session_resolution(fact_command, {}, now)
    for path, row in fact_plan["writes_records"].items():
        harness.repository.rows[path] = copy.deepcopy(row)
    fact_ref = fact_plan["result"]["fact_ref"]

    speech_command = CommandEnvelope(
        campaign_id="campaign.test", request_id="request.scene.history-basis-speech", actor_id=PLAYER,
        command_type="jianghu_scene_session_resolution", expected_revision=1, submitted_at=str(now),
        payload={
            "action":"record_speech", "session_ref":session["session_ref"], "speaker_ref":NPC,
            "statement":"You mean the bowl already sitting between us.", "speech_kind":"clarification",
            "basis_refs":[fact_ref],
        },
    )
    speech_plan = harness._jianghu_scene_session_resolution(speech_command, {}, now)
    recent = speech_plan["writes_records"][HISTORY_HEAD_PATH]["recent"][-1]
    assert recent["basis_refs"] == [fact_ref]
    assert recent["truth_status"] == "attributed_statement"
