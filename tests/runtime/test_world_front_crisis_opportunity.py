import json
from pathlib import Path

from shinobi_runtime.autonomy import AutonomousDecision
from shinobi_runtime.commands.world_front_evidence import apply_event_evidence
from shinobi_runtime.commands.world_front_progression import route_world_front_decision
from shinobi_runtime.sim.events import CampaignTime


ROOT = Path(__file__).resolve().parents[2]


def _pressure(*, evidence_count: int) -> dict:
    return {
        "id": "pressure_test",
        "status": "active_hidden",
        "actors": ["actor.source"],
        "resources": [],
        "opposition": ["faction.defender"],
        "current_step": None,
        "source_refs": ["faction.source"],
        "evidence_refs": [f"event.prior.{index}" for index in range(evidence_count)],
        "visibility": {"classification": "hidden", "basis_refs": []},
        "knowledge": {"player_refs": [], "npc_refs": []},
        "chronology": [],
    }


def test_crisis_front_can_use_stronger_but_still_profile_lawful_action_cycle() -> None:
    rules = {
        "phase_thresholds": {
            "developing_evidence": 1,
            "operational_evidence": 3,
            "crisis_evidence": 6,
        },
        "material_action_kinds": ["mission_generate", "formation_expand"],
        "fronts": {
            "pressure_test": {
                "faction_roles": {"faction.source": "source"},
                "bootstrap_action_cycle": ["mission_generate"],
                "strategic_action_cycle": ["mission_generate"],
                "crisis_action_cycle": ["formation_expand"],
            }
        },
    }
    registry = {"pressures": {"pressure_test": _pressure(evidence_count=6)}}
    profile = {"action_cycle": ["formation_expand"]}
    decision = AutonomousDecision(
        kind="routine_summary",
        actor_ref="actor.source",
        reason="scheduled review",
        payload={"faction_id": "faction.source"},
    )

    routed, front_ref = route_world_front_decision(
        decision,
        at=CampaignTime.parse("SE-0061-08-16T07:00:00"),
        rules=rules,
        registry=registry,
        profile=profile,
    )

    assert front_ref == "pressure_test"
    assert routed.kind == "formation_expand"
    assert routed.payload["world_front_ref"] == "pressure_test"


def test_context_event_advances_front_without_enlisting_context_host_or_actors() -> None:
    rules = {
        "phase_thresholds": {
            "developing_evidence": 1,
            "operational_evidence": 3,
            "crisis_evidence": 6,
        },
        "material_action_kinds": [],
        "fronts": {
            "pressure_test": {
                "faction_roles": {},
                "player_visibility": "knowledge_only",
                "event_sources": [
                    {
                        "event_kinds": ["promotion_exam_cycle_phase_changed"],
                        "host_refs": ["institution.konoha.academy"],
                        "actor_refs": [],
                        "material_ref_prefixes": [
                            "promotion_exam_cycle.promotion_exam.konoha.chunin."
                        ],
                        "role": "context",
                    }
                ],
            }
        },
    }
    row = _pressure(evidence_count=5)
    registry = {"pressures": {"pressure_test": row}}
    event = {
        "id": "event.exam.finals",
        "kind": "promotion_exam_cycle_phase_changed",
        "host_refs": ["institution.konoha.academy"],
        "actor_refs": ["canon_hiruzen"],
        "material_consequence_refs": [
            "promotion_exam_cycle.promotion_exam.konoha.chunin.0061-07",
            "phase:finals",
        ],
        "timing": {"occurred_at": "SE-0061-08-16T07:29:58"},
        "visibility": {
            "classification": "public",
            "audience_refs": [],
            "witness_refs": [],
        },
        "provenance": {"source_refs": ["institution.konoha.academy"]},
    }

    updates = apply_event_evidence(
        registry=registry,
        rules=rules,
        event=event,
        player_ref="pc_wei_tang",
    )

    assert len(updates) == 1
    assert updates[0]["phase_after"] == "crisis"
    assert updates[0]["player_visible"] is False
    assert "institution.konoha.academy" in row["source_refs"]
    assert "institution.konoha.academy" not in row["opposition"]
    assert "canon_hiruzen" not in row["actors"]
    assert "canon_hiruzen" not in row["opposition"]


def test_oto_front_policy_has_exam_context_and_crisis_escalation_lane() -> None:
    policy = json.loads((ROOT / "game/rules/autonomy/world-fronts.json").read_text())
    front = policy["fronts"]["pressure_oto_konoha_infiltration"]

    assert "formation_expand" in front["crisis_action_cycle"]
    assert front["canon_forcing"] is False
    assert any(
        source.get("role") == "context"
        and "promotion_exam_cycle_phase_changed" in source.get("event_kinds", [])
        and "institution.konoha.academy" in source.get("host_refs", [])
        for source in front["event_sources"]
    )
