from __future__ import annotations

from types import SimpleNamespace

from shinobi_runtime.commands import combat_span_safety as safety


def test_pending_action_record_preserves_physical_action_start_frontier():
    action = SimpleNamespace(start_at_ms=640)

    row = safety.pending_action_record_with_start(
        lambda _action: {
            "actor_ref": "attacker",
            "commit_at_ms": 900,
            "release_at_ms": 1100,
        },
        action,
    )

    assert row["start_at_ms"] == 640
    assert row["commit_at_ms"] == 900
    assert row["release_at_ms"] == 1100


def test_defensive_interruption_never_starts_before_incoming_action_exists():
    captured: dict[str, int | str] = {}

    def base_recorder(
        combat,
        *,
        defender_ref,
        attacker_ref,
        response,
        response_start_ms,
        response_contact_ms,
    ):
        captured.update(
            defender_ref=defender_ref,
            attacker_ref=attacker_ref,
            response=response,
            response_start_ms=response_start_ms,
            response_contact_ms=response_contact_ms,
        )

    combat = {
        "_pending_actions": {
            "attacker": {
                "start_at_ms": 640,
                "commit_at_ms": 760,
                "release_at_ms": 900,
            },
            "defender": {
                "start_at_ms": 500,
                "commit_at_ms": 820,
                "release_at_ms": 1050,
            },
        }
    }

    safety.physically_bounded_defensive_interruption(
        base_recorder,
        combat=combat,
        defender_ref="defender",
        attacker_ref="attacker",
        response="brace",
        response_start_ms=0,
        response_contact_ms=920,
    )

    assert captured["response_start_ms"] == 640
    assert captured["response_contact_ms"] == 920


def test_defense_timing_clamp_does_not_delay_a_lawful_later_reaction():
    captured: dict[str, int] = {}

    def base_recorder(combat, **kwargs):
        captured["response_start_ms"] = int(kwargs["response_start_ms"])

    combat = {
        "_pending_actions": {
            "attacker": {
                "start_at_ms": 500,
                "commit_at_ms": 700,
                "release_at_ms": 850,
            }
        }
    }

    safety.physically_bounded_defensive_interruption(
        base_recorder,
        combat=combat,
        defender_ref="defender",
        attacker_ref="attacker",
        response="parry",
        response_start_ms=720,
        response_contact_ms=900,
    )

    assert captured["response_start_ms"] == 720


def test_canonical_pending_record_contains_physical_start_and_brace_cannot_backdate_it():
    from shinobi_runtime.martial_world import exact_combat as exact

    action = SimpleNamespace(
        actor_ref="attacker", target_ref="defender", action_kind="thrust",
        start_at_ms=600, commit_at_ms=800, release_at_ms=1000,
        contact_at_ms=1050, recovery_end_ms=1200,
        profile=SimpleNamespace(
            delivery="melee", external_contact=True,
            effect_parameters={
                "commitment_milli": 400,
                "physical_reach_m": 1.0,
                "geometry": {"kind": "line"},
            },
        ),
    )
    row = exact._pending_action_record(action)
    assert row["start_at_ms"] == 600

    combat = {
        "_pending_actions": {
            "attacker": row,
            "defender": {"start_at_ms": 550, "commit_at_ms": 850, "release_at_ms": 1100},
        }
    }
    # The incoming attack does not exist before 600 ms. Canonical callers clamp
    # response latency to that causal frontier; this direct recorder therefore
    # receives the already-bounded physical start.
    exact._record_defensive_interruption(
        combat, defender_ref="defender", attacker_ref="attacker", response="brace",
        response_start_ms=600, response_contact_ms=900,
    )
    assert row["start_at_ms"] == 600

