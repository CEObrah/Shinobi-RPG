from __future__ import annotations

from types import SimpleNamespace

from shinobi_runtime.combat.models import ActionProfile
from shinobi_runtime.martial_world import exact_combat as exact


def _pending_record(*, start_at_ms: int, commit_at_ms: int, release_at_ms: int) -> dict:
    action = SimpleNamespace(
        actor_ref="wei",
        target_ref="enemy",
        action_kind="thrust",
        start_at_ms=start_at_ms,
        commit_at_ms=commit_at_ms,
        release_at_ms=release_at_ms,
        contact_at_ms=release_at_ms,
        recovery_end_ms=release_at_ms + 200,
        profile=ActionProfile(
            method_ref="thrust",
            effect_kind="physical",
            delivery="direct",
            startup_ms=200,
            external_contact=True,
            speed_score=100,
            effect_parameters={
                "commitment_milli": 400,
                "approach_speed_mmps": 0,
                "melee_movement_budget_mm": 0,
                "movement_intent": "",
                "physical_reach_m": 1.15,
            },
        ),
    )
    return exact._pending_action_record(action)


def test_canonical_pending_action_record_carries_physical_start_time() -> None:
    row = _pending_record(start_at_ms=620, commit_at_ms=760, release_at_ms=980)
    assert row["start_at_ms"] == 620
    assert row["commit_at_ms"] == 760
    assert row["release_at_ms"] == 980


def _record(response: str, *, offense_start: int, response_start: int) -> dict:
    combat = {"_pending_actions": {"wei": _pending_record(start_at_ms=offense_start, commit_at_ms=760, release_at_ms=980)}}
    exact._record_defensive_interruption(
        combat,
        defender_ref="wei",
        attacker_ref="spear",
        response=response,
        response_start_ms=response_start,
        response_contact_ms=900,
    )
    return combat.get("_defense_interruptions", {})


def test_brace_does_not_cancel_offense_that_already_started() -> None:
    assert _record("brace", offense_start=620, response_start=700) == {}


def test_brace_can_preempt_offense_that_has_not_started_yet() -> None:
    rows = _record("brace", offense_start=760, response_start=700)
    assert rows["wei"]["response"] == "brace"
    assert rows["wei"]["started_at_ms"] == 700


def test_parry_still_interrupts_offense_that_already_started() -> None:
    rows = _record("parry", offense_start=620, response_start=700)
    assert rows["wei"]["response"] == "parry"
