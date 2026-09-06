from __future__ import annotations

from dataclasses import dataclass

from shinobi_runtime.api.combat_reaction_timing_integrity import (
    adapt_decision_for_exact_resolver,
    attack_warning_ms,
    causal_response_start_ms,
    install_combat_reaction_timing_integrity,
    resolver_response_lead_ms,
)


@dataclass(frozen=True)
class FakeProfile:
    startup_ms: int
    delivery: str = "direct"
    effect_parameters: dict[str, object] | None = None


@dataclass(frozen=True)
class FakeDecision:
    detected: bool = True
    response: str = "parry"
    reaction_delay_ms: int = 80
    interrupts_attacker: bool = False

    def trace(self) -> dict[str, object]:
        return {
            "detected": self.detected,
            "response": self.response,
            "reaction_delay_ms": self.reaction_delay_ms,
        }


def test_fast_reaction_maps_to_earlier_physical_response_start() -> None:
    attack_start = 700
    contact = 1000
    warning = contact - attack_start

    fast_lead = resolver_response_lead_ms(warning_ms=warning, reaction_latency_ms=50)
    slow_lead = resolver_response_lead_ms(warning_ms=warning, reaction_latency_ms=180)

    fast_start = contact - fast_lead
    slow_start = contact - slow_lead

    assert fast_start == 750
    assert slow_start == 880
    assert fast_start < slow_start
    assert fast_start == causal_response_start_ms(
        attack_start_ms=attack_start,
        contact_at_ms=contact,
        reaction_latency_ms=50,
    )
    assert slow_start == causal_response_start_ms(
        attack_start_ms=attack_start,
        contact_at_ms=contact,
        reaction_latency_ms=180,
    )


def test_warning_matches_startup_approach_and_projectile_flight() -> None:
    profile = FakeProfile(
        startup_ms=90,
        delivery="projectile",
        effect_parameters={
            "approach_time_ms": 110,
            "projectile": {"flight_time_ms": 240},
        },
    )

    assert attack_warning_ms(profile) == 440


def test_exact_adapter_preserves_true_latency_in_trace() -> None:
    profile = FakeProfile(startup_ms=300, effect_parameters={})
    base = FakeDecision(reaction_delay_ms=70)

    adapted = adapt_decision_for_exact_resolver(base, profile=profile)

    assert adapted.reaction_delay_ms == 230
    trace = adapted.trace()
    assert trace["reaction_delay_ms"] == 70
    assert trace["resolver_response_lead_ms"] == 230


def test_no_active_response_keeps_original_decision_semantics() -> None:
    profile = FakeProfile(startup_ms=300, effect_parameters={})
    base = FakeDecision(detected=True, response="none", reaction_delay_ms=300)

    assert adapt_decision_for_exact_resolver(base, profile=profile) is base


def test_counter_intercept_gate_uses_causal_response_start() -> None:
    attack_start = 1000
    contact = 1300
    attacker_commit = 1110
    warning = contact - attack_start

    fast_lead = resolver_response_lead_ms(warning_ms=warning, reaction_latency_ms=60)
    slow_lead = resolver_response_lead_ms(warning_ms=warning, reaction_latency_ms=160)

    assert attacker_commit >= contact - fast_lead
    assert not attacker_commit >= contact - slow_lead


def test_installer_wraps_exact_selector_once(monkeypatch) -> None:
    from shinobi_runtime.martial_world import exact_combat as exact

    decision = FakeDecision(reaction_delay_ms=50)
    profile = FakeProfile(startup_ms=250, effect_parameters={})

    def base_selector(*args, **kwargs):
        return decision

    monkeypatch.setattr(exact, "select_physical_defense", base_selector)
    monkeypatch.setattr(exact, "_combat_reaction_timing_integrity_installed", False, raising=False)

    install_combat_reaction_timing_integrity()
    wrapped = exact.select_physical_defense
    install_combat_reaction_timing_integrity()

    assert exact.select_physical_defense is wrapped
    adapted = exact.select_physical_defense(profile=profile)
    assert adapted.reaction_delay_ms == 200
    assert adapted.trace()["reaction_delay_ms"] == 50
