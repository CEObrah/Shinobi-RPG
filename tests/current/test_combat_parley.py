import pytest

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.jianghu_scene import JianghuSceneCommandsMixin
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.martial_world.scene_sessions import ATTEMPT_LEDGER_PATH, HISTORY_HEAD_PATH
from shinobi_runtime.sim.events import CampaignTime


COMBAT_REF = "combat:test:parley"
PLAYER_REF = "pc.test"
ENEMY_REFS = ["enemy.hidden.1", "enemy.hidden.2"]


class _CombatRepository:
    def __init__(self):
        self.rows = {
            "state/scene.json": {
                "schema": "scene",
                "location_id": "route.test",
                "present_person_ids": [PLAYER_REF, "ally.test"],
                "visible_person_ids": [PLAYER_REF, "ally.test"],
                "active_combat_ref": COMBAT_REF,
            },
            "state/martial-world/combats.json": {
                "schema": "jianghu-combat-state-1.0",
                "combats": {
                    COMBAT_REF: {
                        "combat_id": COMBAT_REF,
                        "status": "active",
                        "sides": {
                            "side_a": [PLAYER_REF, "ally.test"],
                            "side_b": list(ENEMY_REFS),
                        },
                        "combatants": {
                            PLAYER_REF: {},
                            "ally.test": {},
                            ENEMY_REFS[0]: {},
                            ENEMY_REFS[1]: {},
                        },
                    }
                },
            },
        }

    def read_json(self, path):
        if path not in self.rows:
            raise FileNotFoundError(path)
        return self.rows[path]


class _SceneHarness(JianghuSceneCommandsMixin):
    def __init__(self):
        self.repository = _CombatRepository()
        self.scene_path = "state/scene.json"

    def _person(self, ref):
        if ref not in {PLAYER_REF, "ally.test"}:
            raise CommandRejectedError("person_not_found")
        return f"people/{ref}.json", {"people": [{"person_id": ref}]}, 0, {
            "person_id": ref,
            "health": {"status": "ready", "consciousness": 100},
            "current_location_id": "route.test",
        }

    def _simple_plan(self, command, meta, current_time, *, writes_records, code, result):
        return {
            "writes_records": writes_records,
            "code": code,
            "result": result,
            "world_time": str(current_time),
        }


def _interaction(target_ref=COMBAT_REF):
    return CommandEnvelope(
        campaign_id="test-campaign",
        request_id="request.combat-parley",
        actor_id=PLAYER_REF,
        command_type="jianghu_interaction_resolution",
        expected_revision=1,
        submitted_at="2026-08-28T00:00:00Z",
        payload={
            "action": "ask",
            "target_ref": target_ref,
            "player_statement": "State your business.",
            "topic": "parley",
        },
    )


def _combat_reply(*, question_ref=None, session_ref=COMBAT_REF, speaker_ref=COMBAT_REF,
                  speech_kind="nonbinding_response", statement="You are not owed an explanation. Turn back.",
                  proposal_terms=None):
    payload = {
        "action": "record_speech",
        "session_ref": session_ref,
        "speaker_ref": speaker_ref,
        "statement": statement,
        "speech_kind": speech_kind,
    }
    if question_ref is not None:
        payload["resolves_question_ref"] = question_ref
    if proposal_terms is not None:
        payload["proposal_terms"] = proposal_terms
    return CommandEnvelope(
        campaign_id="test-campaign",
        request_id="request.combat-parley.reply",
        actor_id=PLAYER_REF,
        command_type="jianghu_scene_session_resolution",
        expected_revision=1,
        submitted_at="2026-08-28T00:00:01Z",
        payload=payload,
    )


def test_combat_side_parley_records_open_question_without_enemy_identity_leak():
    harness = _SceneHarness()
    current_time = CampaignTime.parse("SE-0061-01-01T00:00:00")

    built = harness._jianghu_interaction_resolution(_interaction(), {}, current_time)

    assert built["code"] == "jianghu_interaction_recorded"
    assert built["result"]["target_kind"] == "opposing_combat_side"
    assert built["result"]["world_response_status"] == "not_established_by_attempt"
    ledger = built["writes_records"][ATTEMPT_LEDGER_PATH]
    row = ledger["attempts"][-1]
    assert row["target_ref"] == COMBAT_REF
    assert row["target_kind"] == "opposing_combat_side"
    assert row["thread_status"] == "open"
    assert row["world_response_status"] == "not_established_by_attempt"
    rendered = repr(built)
    assert all(enemy_ref not in rendered for enemy_ref in ENEMY_REFS)


def test_combat_side_reply_can_resolve_question_without_exposing_enemy_person_id():
    harness = _SceneHarness()
    current_time = CampaignTime.parse("SE-0061-01-01T00:00:00")
    asked = harness._jianghu_interaction_resolution(_interaction(), {}, current_time)
    ledger = asked["writes_records"][ATTEMPT_LEDGER_PATH]
    question_ref = ledger["attempts"][-1]["attempt_ref"]
    harness.repository.rows[ATTEMPT_LEDGER_PATH] = ledger

    replied = harness._jianghu_scene_session_resolution(
        _combat_reply(question_ref=question_ref), {}, current_time
    )

    assert replied["code"] == "jianghu_combat_parley_speech_recorded"
    assert replied["result"]["speaker_ref"] == COMBAT_REF
    assert replied["result"]["speaker_kind"] == "opposing_combat_side"
    answered = replied["writes_records"][ATTEMPT_LEDGER_PATH]["attempts"][-1]
    assert answered["thread_status"] == "answered"
    assert answered["response_ref"] == replied["result"]["speech_ref"]
    latest = replied["writes_records"][HISTORY_HEAD_PATH]["recent"][-1]
    assert latest["session_ref"] == COMBAT_REF
    assert latest["speaker_ref"] == COMBAT_REF
    assert latest["resolves_question_ref"] == question_ref
    assert latest["mechanical_consequence_authority"] is False
    rendered = repr(replied)
    assert all(enemy_ref not in rendered for enemy_ref in ENEMY_REFS)


def test_combat_side_reply_accepts_pre_thread_legacy_question_shape():
    harness = _SceneHarness()
    harness.repository.rows[ATTEMPT_LEDGER_PATH] = {
        "schema": "jianghu-interaction-attempt-ledger-1.0",
        "authority": False,
        "mechanical_consequence_authority": False,
        "total_recorded": 1,
        "attempts": [{
            "attempt_ref": "interaction_attempt_legacy_combat",
            "at": "SE-0061-01-01T00:00:00",
            "surface_digest": "legacy-combat-digest",
            "actor_ref": PLAYER_REF,
            "target_ref": COMBAT_REF,
            "target_kind": "opposing_combat_side",
            "action": "ask",
            "process_ref": COMBAT_REF,
            "player_statement": "State your business.",
            "posture": "parley",
            "topic": "hostile_contact",
            "scopes": [],
            "world_response_status": "not_established_by_attempt",
            "scene_session_ref": None,
            "thread_status": "not_applicable",
            "resolved_at": None,
            "response_ref": None,
        }],
    }
    current_time = CampaignTime.parse("SE-0061-01-01T00:00:00")

    replied = harness._jianghu_scene_session_resolution(
        _combat_reply(question_ref="interaction_attempt_legacy_combat"), {}, current_time
    )

    answered = replied["writes_records"][ATTEMPT_LEDGER_PATH]["attempts"][-1]
    assert answered["thread_status"] == "answered"
    assert answered["resolved_at"] == str(current_time)


def test_combat_side_parley_rejects_guessed_nonactive_combat_reference():
    harness = _SceneHarness()
    current_time = CampaignTime.parse("SE-0061-01-01T00:00:00")

    with pytest.raises(CommandRejectedError) as caught:
        harness._jianghu_interaction_resolution(_interaction("combat:test:other"), {}, current_time)

    assert caught.value.code == "jianghu_scene_person_not_player_visible"


def test_active_combat_can_persist_exact_arrived_allied_speech(monkeypatch):
    import shinobi_runtime.commands.jianghu_scene as scene_module

    harness = _SceneHarness()
    monkeypatch.setattr(scene_module, "same_effective_location", lambda *args, **kwargs: True)
    current_time = CampaignTime.parse("SE-0061-01-01T00:00:00")
    command = _combat_reply(
        speaker_ref="ally.test",
        speech_kind="nonbinding_proposal",
        statement="I can cover the 582 copper if you decide to pay them.",
        proposal_terms={"cash": 582, "recipient_ref": PLAYER_REF},
    )

    built = harness._jianghu_scene_session_resolution(command, {}, current_time)

    assert built["code"] == "jianghu_combat_ally_speech_recorded"
    assert built["result"]["speaker_ref"] == "ally.test"
    assert built["result"]["speaker_kind"] == "allied_combatant"
    latest = built["writes_records"][HISTORY_HEAD_PATH]["recent"][-1]
    assert latest["session_ref"] == COMBAT_REF
    assert latest["speaker_ref"] == "ally.test"
    assert latest["proposal_terms"] == {"cash": 582, "recipient_ref": PLAYER_REF}
    assert latest["mechanical_consequence_authority"] is False


def test_active_combat_rejects_exact_hostile_person_as_speech_speaker(monkeypatch):
    import shinobi_runtime.commands.jianghu_scene as scene_module

    harness = _SceneHarness()
    monkeypatch.setattr(scene_module, "same_effective_location", lambda *args, **kwargs: True)
    current_time = CampaignTime.parse("SE-0061-01-01T00:00:00")

    with pytest.raises(CommandRejectedError) as caught:
        harness._jianghu_scene_session_resolution(_combat_reply(speaker_ref=ENEMY_REFS[0]), {}, current_time)
    assert caught.value.code == "jianghu_combat_scene_speaker_not_allied"


def _planner_harness():
    planner = object.__new__(RepositoryCommandPlanner)
    planner.repository = _CombatRepository()
    planner._allow_site_service_presence = False
    planner._base = lambda _command: ({}, CampaignTime.parse("SE-0061-01-01T00:00:00"))
    planner._jianghu_interaction_resolution = lambda command, meta, now: "interaction_allowed"
    planner._jianghu_scene_session_resolution = lambda command, meta, now: "combat_side_speech_allowed"
    return planner


def test_active_combat_allows_interaction_and_combat_session_speech_only():
    planner = _planner_harness()

    assert planner._build(_interaction()) == "interaction_allowed"
    assert planner._build(_combat_reply()) == "combat_side_speech_allowed"
    assert planner._build(_combat_reply(speaker_ref="ally.test")) == "combat_side_speech_allowed"

    unrelated = CommandEnvelope(
        campaign_id="test-campaign",
        request_id="request.combat-parley-unrelated",
        actor_id=PLAYER_REF,
        command_type="advance_time",
        expected_revision=1,
        submitted_at="2026-08-28T00:00:00Z",
        payload={"target_time": "SE-0061-01-02T00:00:00"},
    )
    with pytest.raises(CommandRejectedError) as caught:
        planner._build(unrelated)
    assert caught.value.code == "jianghu_active_combat_requires_resolution"

    with pytest.raises(CommandRejectedError) as caught:
        planner._build(_combat_reply(session_ref="scene_session.fake", speaker_ref="ally.test"))
    assert caught.value.code == "jianghu_active_combat_requires_resolution"


def test_combat_side_request_is_open_thread_and_group_reply_resolves_it_without_hidden_identity():
    harness = _SceneHarness()
    current_time = CampaignTime.parse("SE-0061-01-01T00:00:00")
    request = CommandEnvelope(
        campaign_id="test-campaign",
        request_id="request.combat-parley.request",
        actor_id=PLAYER_REF,
        command_type="jianghu_interaction_resolution",
        expected_revision=1,
        submitted_at="2026-08-28T00:00:00Z",
        payload={
            "action": "request",
            "target_ref": COMBAT_REF,
            "player_statement": "Tell me why you attacked us.",
            "topic": "hostile_contact_motive",
        },
    )

    built = harness._jianghu_interaction_resolution(request, {}, current_time)
    ledger = built["writes_records"][ATTEMPT_LEDGER_PATH]
    row = ledger["attempts"][-1]
    thread_ref = row["attempt_ref"]
    assert row["thread_kind"] == "conversation"
    assert row["thread_status"] == "open"
    harness.repository.rows[ATTEMPT_LEDGER_PATH] = ledger

    reply = CommandEnvelope(
        campaign_id="test-campaign",
        request_id="request.combat-parley.request.reply",
        actor_id=PLAYER_REF,
        command_type="jianghu_scene_session_resolution",
        expected_revision=1,
        submitted_at="2026-08-28T00:00:01Z",
        payload={
            "action": "record_speech",
            "session_ref": COMBAT_REF,
            "speaker_ref": COMBAT_REF,
            "statement": "The cargo. Leave it and walk away.",
            "speech_kind": "nonbinding_response",
            "resolves_thread_ref": thread_ref,
        },
    )
    replied = harness._jianghu_scene_session_resolution(reply, {}, current_time)
    resolved = replied["writes_records"][ATTEMPT_LEDGER_PATH]["attempts"][-1]
    assert resolved["thread_status"] == "responded"
    assert resolved["response_ref"] == replied["result"]["speech_ref"]
    latest = replied["writes_records"][HISTORY_HEAD_PATH]["recent"][-1]
    assert latest["resolves_thread_ref"] == thread_ref
    assert latest["resolves_question_ref"] is None
    assert all(enemy_ref not in repr(replied) for enemy_ref in ENEMY_REFS)


def test_route_extortion_terms_are_gm_private_only_in_combat_parley_projection():
    from shinobi_runtime.api.parley_operations import combat_parley_scene_projection

    route_operations = {
        "movements": {
            "move.test": {
                "movement_ref": "move.test",
                "route_ref": "route.test",
                "contact_ref": "contact.test",
                "combat_ref": COMBAT_REF,
                "participant_refs": [PLAYER_REF, "ally.test"],
            }
        },
        "contacts": {
            "contact.test": {
                "status": "active",
                "movement_ref": "move.test",
                "route_ref": "route.test",
                "combat_ref": COMBAT_REF,
                "attacker_faction_ref": "outlaw.test",
                "attacker_intent": "hostile_interception",
                "motive_kind": "opportunistic_predation",
                "settlement_terms": {"minimum_cash": 4000, "opening_demand_cash": 5000},
            }
        },
    }
    projected = combat_parley_scene_projection(
        ledger={"attempts": []}, recent_history=[], combat_ref=COMBAT_REF,
        player_id=PLAYER_REF, route_operations=route_operations,
    )
    envelope = projected["npc_response_envelope"]
    assert envelope["privacy"] == "gm_private_scene_bounded_omniscient_truth_not_player_knowledge"
    assert envelope["gm_private_causal_context"]["settlement_terms"] == {
        "minimum_cash": 4000, "opening_demand_cash": 5000,
    }
    # The terms are backstage decision support, never promoted into the
    # player-safe parley fields or represented as a binding opponent promise.
    public_projection = dict(projected)
    public_projection.pop("npc_response_envelope", None)
    assert "minimum_cash" not in repr(public_projection)
    assert "opening_demand_cash" not in repr(public_projection)
