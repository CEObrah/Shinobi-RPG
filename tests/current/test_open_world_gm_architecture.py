import copy
import json

from shinobi_runtime.api.command_discovery import compact_play_context
from shinobi_runtime.api.gm_scene_context import build_gm_scene_context
from shinobi_runtime.martial_world.escort_living_world import (
    interception_decision,
    interception_force_size,
)
from shinobi_runtime.martial_world.exact_combat import _engagement_band
from shinobi_runtime.martial_world.scene_sessions import (
    HISTORY_HEAD_PATH,
    SESSION_PATH,
    append_scene_history_record,
    new_session_record,
    relevant_scene_continuity,
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


def test_routine_road_interception_allocates_detachment_not_entire_faction():
    allocated = interception_force_size(
        available_count=43,
        observed_escort_count=12,
        hostility=0,
        criminal_scale=9,
        risk_tolerance=50,
        known_value_cash=0,
        attacker_faction_type="outlaw_faction",
    )

    assert 12 <= allocated < 43
    assert allocated <= 25


def test_higher_stakes_can_increase_route_detachment_without_linear_faction_dump():
    routine = interception_force_size(
        available_count=80,
        observed_escort_count=12,
        hostility=0,
        criminal_scale=9,
        risk_tolerance=50,
        known_value_cash=0,
        attacker_faction_type="outlaw_faction",
    )
    high_value = interception_force_size(
        available_count=80,
        observed_escort_count=12,
        hostility=80,
        criminal_scale=9,
        risk_tolerance=90,
        known_value_cash=750_000,
        attacker_faction_type="outlaw_faction",
    )

    assert high_value > routine
    assert high_value < 80


def test_plausible_small_detachment_can_decline_instead_of_summoning_whole_faction():
    allocated = interception_force_size(
        available_count=4,
        observed_escort_count=12,
        hostility=0,
        criminal_scale=2,
        risk_tolerance=100,
        known_value_cash=0,
        attacker_faction_type="outlaw_faction",
    )
    decision = interception_decision(
        attacker_faction_type="outlaw_faction",
        relation=None,
        own_available_martial=allocated,
        own_combat_index=30,
        observed_escort_count=12,
        observed_escort_combat_index=90,
        cargo_value_cash=0,
        ransom_value_cash=0,
        risk_tolerance=100,
        government_risk_milli=0,
        minimum_attack_advantage_milli=1100,
    )

    assert allocated == 4
    assert decision["attack"] is False


def test_geometry_bands_make_immediate_threat_outrank_remote_or_withdrawing_target():
    immediate = _engagement_band(1800)
    near = _engagement_band(5000)
    remote = _engagement_band(30_000)
    withdrawing_near = _engagement_band(5000, disengaging=True)

    assert immediate < near < remote
    assert withdrawing_near > near


def test_literary_continuity_survives_recent_history_churn_by_person_and_place():
    session = new_session_record(
        session_ref="scene_session_memory",
        kind="conversation",
        location_ref="site.house_tang.hall",
        participant_refs=["pc_wei_tang", "npc.father"],
        at="0061-09-14T09:00:00",
    )
    store = _Records({SESSION_PATH: session})
    basis = {
        "speech_ref": "scene_speech_basis",
        "at": "0061-09-14T09:01:00",
        "session_ref": session["session_ref"],
        "speaker_ref": "npc.father",
        "speech_kind": "advice",
        "statement": "Bring everyone home.",
        "truth_status": "attributed_statement",
        "authority": False,
        "mechanical_consequence_authority": False,
    }
    store.apply(append_scene_history_record(store.read_json, row=basis))
    note = {
        "continuity_ref": "scene_continuity_father_practical_concern",
        "at": "0061-09-14T09:02:00",
        "session_ref": session["session_ref"],
        "continuity_kind": "relationship_expression",
        "summary": "His concern for Wei was expressed as practical insistence on bringing the whole escort home.",
        "subject_refs": ["npc.father"],
        "basis_refs": [basis["speech_ref"]],
        "location_ref": "site.house_tang.hall",
        "truth_status": "derived_narrative_continuity",
        "scope": "scene_history_only",
        "derivation_rule": "interpretive_summary_of_cited_authority_false_scene_history_not_objective_world_truth",
        "authority": False,
        "mechanical_consequence_authority": False,
    }
    store.apply(append_scene_history_record(store.read_json, row=note))

    for index in range(60):
        row = {
            "speech_ref": f"scene_speech_churn_{index}",
            "at": f"0061-09-14T09:{10 + index // 60:02d}:{index % 60:02d}",
            "session_ref": session["session_ref"],
            "speaker_ref": "pc_wei_tang",
            "speech_kind": "observation",
            "statement": f"Routine line {index}",
            "truth_status": "attributed_statement",
            "authority": False,
            "mechanical_consequence_authority": False,
        }
        store.apply(append_scene_history_record(store.read_json, row=row))

    assert all(row.get("continuity_ref") != note["continuity_ref"] for row in store.rows[HISTORY_HEAD_PATH]["recent"])
    remembered = relevant_scene_continuity(
        store.read_json,
        subject_refs=["npc.father"],
        location_ref="site.house_tang.hall",
        limit=8,
    )
    assert any(row.get("continuity_ref") == note["continuity_ref"] for row in remembered)
    remembered_note = next(row for row in remembered if row.get("continuity_ref") == note["continuity_ref"])
    assert remembered_note["authority"] is False
    assert remembered_note["mechanical_consequence_authority"] is False


def test_gm_scene_context_is_scene_first_and_does_not_turn_runtime_rows_into_prose():
    context = {
        "campaign": {"world_time": "SE-0061-09-14T09:00:00"},
        "player": {"person_id": "pc_wei_tang", "current_location_id": "site.house_tang.hall"},
        "scene": {
            "location_id": "site.house_tang.hall",
            "present_person_ids": ["pc_wei_tang", "npc.father"],
            "visible_person_ids": ["pc_wei_tang", "npc.father"],
            "gm_private_director_context": {
                "present_people": [{
                    "person_ref": "npc.father",
                    "character_truth": {
                        "name": "Father",
                        "faction_ref": "house_tang",
                        "health": {"status": "ready"},
                        "attributes": {"strength": 999},
                        "martial_skills": {"sword": 999},
                    },
                    "cognition": {"goal_state": "understand Wei's report"},
                }],
                "relationship_edges": [],
                "scene_pressure": {"private_signal": "father expects an answer"},
            },
        },
        "active_scene_session": {
            "session_ref": "scene_session_memory",
            "kind": "conversation",
            "location_ref": "site.house_tang.hall",
            "participant_refs": ["pc_wei_tang", "npc.father"],
            "open_thread_refs": [],
        },
        "recent_scene_history": [{
            "speech_ref": "scene_speech_recent",
            "at": "0061-09-14T09:01:00",
            "session_ref": "scene_session_memory",
            "speaker_ref": "npc.father",
            "speech_kind": "question",
            "statement": "How many came back?",
            "authority": False,
            "mechanical_consequence_authority": False,
        }],
        "recent_interaction_attempts": [{
            "attempt_ref": "interaction_answer",
            "at": "0061-09-14T09:02:00",
            "action": "speak",
            "target_ref": "npc.father",
            "player_statement": "All twelve.",
            "scene_session_ref": "scene_session_memory",
            "thread_status": "responded",
        }],
        "commands": {"supported_command_types": []},
    }

    gm = build_gm_scene_context(context)
    assert gm["immediate_continuity"][-2]["statement"] == "How many came back?"
    assert gm["immediate_continuity"][-1]["player_statement"] == "All twelve."
    assert gm["immediate_continuity"][-1]["beat_kind"] == "player_declared_action"
    assert gm["writer_contract"]["scene_direction_owner"] == "llm"
    assert gm["writer_contract"]["hard_consequence_owner"] == "runtime"
    assert gm["writer_contract"]["doctrine_source"].startswith("GM Skill")
    assert "chatgpt_owns_prose_dialogue_pacing_focus_and_narrative_scene_lifecycle" not in gm["writer_contract"]
    truth = gm["present_people"][1]["gm_private_direction"]["character_truth"]
    assert "health" in truth
    assert "attributes" not in truth
    assert "martial_skills" not in truth

    assert gm["gm_private_scene_truth"]["director_context"]["scene_pressure"]["private_signal"] == "father expects an answer"

    compact = compact_play_context(context)
    assert compact["scene"]["gm_private_director_context"]["available_in_gm_scene_context"] is True
    assert compact["gm_scene_context"]["gm_private_scene_truth"]["director_context"]["scene_pressure"]["private_signal"] == "father expects an answer"
    assert compact["gm_scene_context"]["purpose"] == "prioritized_writer_workspace_not_prose"
    deep = compact["gm_scene_context"]["deep_reads"]
    assert "suggested_person_refs" not in deep
    assert "supported_object_prefixes" not in deep
    assert "read_hints" not in deep
    assert deep["person_refs_source"] == "play_context.person_reads.suggested_owner_ids"
    assert deep["object_prefixes_source"] == "play_context.object_reads.supported_ref_prefixes"
    assert deep["read_hints_source"] == "play_context.read_hints"


def test_scene_workspace_does_not_treat_sessionless_old_attempts_as_immediate_continuity():
    context = {
        "campaign": {"world_time": "SE-0061-09-20T10:00:00"},
        "player": {"person_id": "pc_wei_tang", "current_location_id": "site.house_tang.hall"},
        "scene": {
            "location_id": "site.house_tang.hall",
            "present_person_ids": ["pc_wei_tang"],
            "visible_person_ids": ["pc_wei_tang"],
        },
        "active_scene_session": None,
        "recent_scene_history": [
            {
                "speech_ref": "old_scene_speech",
                "at": "0061-09-14T09:01:00",
                "session_ref": "scene_session_already_closed",
                "speaker_ref": "npc.father",
                "speech_kind": "statement",
                "statement": "Do not make me count empty saddles again.",
                "authority": False,
                "mechanical_consequence_authority": False,
            },
            {
                "fact_ref": "old_scene_fact",
                "at": "0061-09-14T09:01:30",
                "session_ref": "scene_session_already_closed",
                "fact_kind": "visible_reaction",
                "summary": "Father set the escort report aside after the discussion.",
                "authority": False,
                "mechanical_consequence_authority": False,
            },
        ],
        "recent_interaction_attempts": [{
            "attempt_ref": "old_order",
            "at": "0061-09-14T09:02:00",
            "action": "speak",
            "target_ref": "npc.father",
            "player_statement": "I will bring everyone home.",
            "thread_status": "responded",
        }],
        "commands": {"supported_command_types": []},
    }
    gm = build_gm_scene_context(context)
    assert gm["immediate_continuity"] == []
    assert gm["recent_player_action_count"] == 0


def test_sessionless_populated_scene_explicitly_invites_llm_npc_initiative_without_scripted_prose():
    context = {
        "campaign": {"world_time": "SE-0061-09-20T10:00:00", "player_id": "pc_wei_tang"},
        "player": {"person_id": "pc_wei_tang", "current_location_id": "site.house_tang.hall"},
        "scene": {
            "location_id": "site.house_tang.hall",
            "present_person_ids": ["pc_wei_tang", "npc.father"],
            "visible_person_ids": ["pc_wei_tang", "npc.father"],
            "gm_private_director_context": {
                "present_people": [{
                    "person_ref": "npc.father",
                    "character_truth": {"name": "Father", "health": {"status": "ready"}},
                    "cognition": {"goal_state": "finish the conversation"},
                }],
                "relationship_edges": [],
            },
        },
        "active_scene_session": None,
        "recent_scene_history": [],
        "recent_interaction_attempts": [],
    }

    gm = build_gm_scene_context(context)
    direction = gm["scene_direction"]
    assert direction["llm_is_scene_director"] is True
    assert direction["continuation_mode"] == "present_people_may_initiate"
    assert direction["present_agent_refs"] == ["npc.father"]
    assert direction["next_beat_requirement"] == "advance_grounded_scene_or_compress"
    assert "select_actor_by_pressure_not_cast_order" in direction["director_protocol"]
    assert direction["director_doctrine_source"].startswith("GM Skill")
    assert "directing_contract" not in direction
    assert gm["writer_contract"]["scene_direction_owner"] == "llm"
    assert gm["writer_contract"]["hard_consequence_owner"] == "runtime"
    assert gm["writer_contract"]["doctrine_source"].startswith("GM Skill")
    assert direction["director_doctrine_source"].startswith("GM Skill")
    assert "directing_contract" not in direction
    assert len(direction["director_protocol"]) >= 7
    assert "reject_draft_if" not in direction
    assert "directing_contract" not in direction
    assert direction["agents_with_private_direction_refs"] == ["npc.father"]
    assert direction["beat_candidates"][0]["reason"] in {"private_direction_available", "present_agent"}
    assert direction["beat_candidate_rule"] == "causal_priority_hint_not_speaking_queue_or_script"
    assert len(json.dumps(direction, sort_keys=True)) < 5000


def test_scene_director_preserves_explicit_player_decision_without_freezing_reversible_npc_reaction():
    context = {
        "campaign": {"world_time": "SE-0061-09-20T10:00:00", "player_id": "pc_wei_tang"},
        "player": {"person_id": "pc_wei_tang", "current_location_id": "site.house_tang.hall"},
        "scene": {
            "location_id": "site.house_tang.hall",
            "present_person_ids": ["pc_wei_tang", "npc.father"],
            "visible_person_ids": ["pc_wei_tang", "npc.father"],
            "activity_handoff": {"kind": "house_assignment", "requires_player_decision": True},
        },
        "active_scene_session": None,
        "recent_scene_history": [],
        "recent_interaction_attempts": [],
    }
    gm = build_gm_scene_context(context)
    direction = gm["scene_direction"]
    assert direction["protected_player_decision_pending"] is True
    assert direction["continuation_mode"] == "preserve_player_decision_and_allow_reversible_reaction"
    assert direction["present_agent_refs"] == ["npc.father"]
    assert direction["director_doctrine_source"].startswith("GM Skill")
    assert "directing_contract" not in direction


def test_scene_director_does_not_promote_sessionless_old_thread_into_local_scene():
    context = {
        "campaign": {"world_time": "SE-0061-09-20T10:00:00", "player_id": "pc_wei_tang"},
        "player": {"person_id": "pc_wei_tang", "current_location_id": "site.house_tang.hall"},
        "scene": {
            "location_id": "site.house_tang.hall",
            "present_person_ids": ["pc_wei_tang"],
            "visible_person_ids": ["pc_wei_tang"],
        },
        "active_scene_session": None,
        "recent_scene_history": [],
        "recent_interaction_attempts": [],
        "active_threads": [{
            "attempt_ref": "attempt.old",
            "action": "ask",
            "target_ref": "npc.remote",
            "player_statement": "Send word when you know.",
            "scene_session_ref": "scene_session_old",
            "thread_status": "open",
        }],
    }
    gm = build_gm_scene_context(context)
    assert gm["human_threads"] == []
    assert gm["scene_direction"]["open_human_thread_count"] == 0
    assert gm["scene_direction"]["open_human_target_refs"] == []


def test_scene_director_uses_current_active_session_threads_only():
    context = {
        "campaign": {"world_time": "SE-0061-09-20T10:00:00", "player_id": "pc_wei_tang"},
        "player": {"person_id": "pc_wei_tang", "current_location_id": "site.house_tang.hall"},
        "scene": {
            "location_id": "site.house_tang.hall",
            "present_person_ids": ["pc_wei_tang", "npc.father"],
            "visible_person_ids": ["pc_wei_tang", "npc.father"],
        },
        "active_scene_session": {
            "session_ref": "scene_session_current",
            "kind": "conversation",
            "participant_refs": ["pc_wei_tang", "npc.father"],
            "open_thread_refs": ["attempt.current"],
        },
        "recent_scene_history": [],
        "recent_interaction_attempts": [],
        "active_threads": [{
            "attempt_ref": "attempt.current",
            "action": "ask",
            "target_ref": "npc.father",
            "player_statement": "What do you think?",
            "scene_session_ref": "scene_session_current",
            "thread_status": "open",
        }],
    }
    gm = build_gm_scene_context(context)
    assert gm["scene_direction"]["open_human_target_refs"] == ["npc.father"]
    assert gm["scene_direction"]["open_human_thread_count"] == 1


def test_scene_direction_exposes_llm_owned_scene_lifecycle_affordance():
    context = {
        "campaign": {"world_time": "SE-0061-09-20T10:00:00", "player_id": "pc_wei_tang"},
        "player": {"person_id": "pc_wei_tang", "current_location_id": "site.house_tang.hall"},
        "scene": {
            "location_id": "site.house_tang.hall",
            "present_person_ids": ["pc_wei_tang", "npc.father"],
            "visible_person_ids": ["pc_wei_tang", "npc.father"],
        },
        "active_scene_session": None,
        "recent_scene_history": [],
        "recent_interaction_attempts": [],
    }
    direction = build_gm_scene_context(context)["scene_direction"]
    lifecycle = direction["scene_lifecycle"]
    assert lifecycle["formal_session_active"] is False
    assert lifecycle["open_affordance"] is True
    assert lifecycle["candidate_participant_refs"] == ["pc_wei_tang", "npc.father"]
    assert lifecycle["persistence_route_source"] == "GM Skill scene lifecycle contract"
    assert "rules" not in lifecycle
    assert direction["director_doctrine_source"].startswith("GM Skill")
    assert len(direction["director_protocol"]) == 7
    assert "decide_scene_lifecycle_from_lived_pressure" in direction["director_protocol"]


def test_scene_direction_marks_close_risk_for_live_thread_and_player_decision():
    context = {
        "campaign": {"world_time": "SE-0061-09-20T10:00:00", "player_id": "pc_wei_tang"},
        "player": {"person_id": "pc_wei_tang", "current_location_id": "site.house_tang.hall"},
        "scene": {
            "location_id": "site.house_tang.hall",
            "present_person_ids": ["pc_wei_tang", "npc.father"],
            "visible_person_ids": ["pc_wei_tang", "npc.father"],
            "unresolved_decision": {"decision_ref": "decision.offer"},
        },
        "active_scene_session": {
            "session_ref": "scene_session_current",
            "kind": "conversation",
            "participant_refs": ["pc_wei_tang", "npc.father"],
            "open_thread_refs": ["attempt.current"],
        },
        "active_threads": [{
            "attempt_ref": "attempt.current",
            "action": "ask",
            "target_ref": "npc.father",
            "scene_session_ref": "scene_session_current",
            "thread_status": "open",
        }],
        "recent_scene_history": [],
        "recent_interaction_attempts": [],
    }
    lifecycle = build_gm_scene_context(context)["scene_direction"]["scene_lifecycle"]
    assert lifecycle["formal_session_active"] is True
    assert lifecycle["close_affordance"] is True
    assert set(lifecycle["close_risks"]) == {"open_human_threads", "protected_player_decision"}


def test_scene_direction_does_not_offer_people_session_during_exact_combat():
    context = {
        "campaign": {"world_time": "SE-0061-09-20T10:00:00", "player_id": "pc_wei_tang"},
        "player": {"person_id": "pc_wei_tang", "current_location_id": "site.road"},
        "scene": {
            "location_id": "site.road",
            "present_person_ids": ["pc_wei_tang", "npc.ally"],
            "visible_person_ids": ["pc_wei_tang", "npc.ally"],
            "active_combat_ref": "combat.1",
            "active_combat": True,
        },
        "active_scene_session": None,
        "recent_scene_history": [],
        "recent_interaction_attempts": [],
    }
    lifecycle = build_gm_scene_context(context)["scene_direction"]["scene_lifecycle"]
    assert lifecycle["contested_process_active"] is True
    assert lifecycle["open_affordance"] is False


def test_scene_director_reconciles_formal_session_when_other_participant_is_physically_absent():
    context = {
        "campaign": {"world_time": "SE-0061-09-20T10:00:00"},
        "player": {"person_id": "pc_wei_tang", "current_location_id": "site.house_tang.hall"},
        "scene": {
            "location_id": "site.house_tang.hall",
            "present_person_ids": ["pc_wei_tang"],
            "visible_person_ids": ["pc_wei_tang"],
        },
        "active_scene_session": {
            "session_ref": "scene_session_departed",
            "kind": "conversation",
            "location_ref": "site.house_tang.hall",
            "participant_refs": ["pc_wei_tang"],
            "participant_count": 1,
            "durable_participant_count": 2,
            "physically_absent_participant_refs": ["npc.father"],
            "physically_absent_participant_count": 1,
            "physical_scene_viable": False,
            "lifecycle_reconciliation_recommended": True,
            "open_thread_refs": ["interaction_old_question"],
        },
        "active_threads": [],
        "recent_scene_history": [],
        "recent_interaction_attempts": [],
        "commands": {"supported_command_types": []},
    }

    gm = build_gm_scene_context(context)
    direction = gm["scene_direction"]
    lifecycle = direction["scene_lifecycle"]
    assert direction["continuation_mode"] == "reconcile_stale_formal_session_then_transition"
    assert direction["open_human_thread_count"] == 0
    assert gm["human_threads"] == []
    assert lifecycle["formal_session_presence_viable"] is False
    assert lifecycle["formal_session_absent_participant_refs"] == ["npc.father"]
    assert lifecycle["lifecycle_reconciliation_recommended"] is True


def test_scene_director_prioritizes_active_session_people_over_general_site_cast():
    context = {
        "campaign": {"world_time": "SE-0061-09-20T10:00:00"},
        "player": {"person_id": "pc_wei_tang", "current_location_id": "site.house_tang.hall"},
        "scene": {
            "location_id": "site.house_tang.hall",
            "present_person_ids": ["pc_wei_tang", "npc.bystander", "npc.father"],
            "visible_person_ids": ["pc_wei_tang", "npc.bystander", "npc.father"],
            "scene_session_person_ids": ["pc_wei_tang", "npc.father"],
            "gm_private_director_context": {
                "present_people": [
                    {"person_ref": "npc.bystander", "character_truth": {"name": "Bystander"}},
                    {"person_ref": "npc.father", "character_truth": {"name": "Father", "membership_grade": "elder"}},
                ]
            },
        },
        "active_scene_session": {
            "session_ref": "scene_session_family",
            "kind": "conversation",
            "location_ref": "site.house_tang.hall",
            "participant_refs": ["pc_wei_tang", "npc.father"],
            "participant_count": 2,
            "physical_scene_viable": True,
            "open_thread_refs": [],
        },
        "active_threads": [],
        "recent_scene_history": [],
        "recent_interaction_attempts": [],
        "commands": {"supported_command_types": []},
    }

    gm = build_gm_scene_context(context)
    direction = gm["scene_direction"]
    candidates = direction["beat_candidates"]
    assert direction["continuation_mode"] == "active_scene_continue_or_transition_by_lived_pressure"
    assert candidates[0] == {"person_ref": "npc.father", "reason": "active_formal_session"}
    father = next(row for row in gm["present_people"] if row.get("person_ref") == "npc.father")
    assert father["name"] == "Father"
    assert father["role_hint"] == "elder"


def test_compact_play_context_keeps_scene_lifecycle_but_not_opaque_open_thread_refs():
    context = {
        "campaign": {"world_time": "SE-0061-09-20T10:00:00"},
        "player": {"person_id": "pc_wei_tang", "current_location_id": "site.house_tang.hall"},
        "scene": {"location_id": "site.house_tang.hall", "present_person_ids": ["pc_wei_tang"]},
        "active_scene_session": {
            "session_ref": "scene_session_compact",
            "kind": "conversation",
            "status": "active",
            "location_ref": "site.house_tang.hall",
            "participant_refs": ["pc_wei_tang"],
            "physically_absent_participant_refs": ["npc.father"],
            "physical_scene_viable": False,
            "open_thread_refs": [f"thread_{i}" for i in range(20)],
        },
        "recent_scene_history": [],
        "recent_interaction_attempts": [],
        "commands": {"supported_command_types": []},
    }

    compact = compact_play_context(context)
    session = compact["active_scene_session"]
    assert session["open_thread_count"] == 20
    assert "open_thread_refs" not in session
    assert session["physically_absent_participant_refs"] == ["npc.father"]
    assert session["physical_scene_viable"] is False
