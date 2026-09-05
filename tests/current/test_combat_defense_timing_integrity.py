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


def test_production_installer_wraps_exact_pending_and_defense_timing_once(monkeypatch):
    from shinobi_runtime.commands import jianghu_extended as extended
    from shinobi_runtime.martial_world import exact_combat as exact

    captured: list[int] = []

    def base_pending(action):
        return {
            "actor_ref": action.actor_ref,
            "commit_at_ms": action.commit_at_ms,
            "release_at_ms": action.release_at_ms,
        }

    def base_record(combat, **kwargs):
        captured.append(int(kwargs["response_start_ms"]))

    monkeypatch.setattr(exact, "_pending_action_record", base_pending)
    monkeypatch.setattr(exact, "_record_defensive_interruption", base_record)
    monkeypatch.setattr(exact, "_production_defense_timing_safety_installed", False, raising=False)
    # Isolate the new exact-combat hook from the already-covered span wrapper.
    monkeypatch.setattr(extended, "_production_combat_span_safety_installed", True, raising=False)

    safety.install_production_combat_span_safety()
    first_pending = exact._pending_action_record
    first_record = exact._record_defensive_interruption
    safety.install_production_combat_span_safety()

    assert exact._pending_action_record is first_pending
    assert exact._record_defensive_interruption is first_record

    attacker = SimpleNamespace(
        actor_ref="attacker",
        start_at_ms=600,
        commit_at_ms=800,
        release_at_ms=1000,
    )
    combat = {
        "_pending_actions": {
            "attacker": exact._pending_action_record(attacker),
            "defender": {
                "start_at_ms": 550,
                "commit_at_ms": 850,
                "release_at_ms": 1100,
            },
        }
    }
    exact._record_defensive_interruption(
        combat,
        defender_ref="defender",
        attacker_ref="attacker",
        response="brace",
        response_start_ms=0,
        response_contact_ms=900,
    )

    assert combat["_pending_actions"]["attacker"]["start_at_ms"] == 600
    assert captured == [600]
