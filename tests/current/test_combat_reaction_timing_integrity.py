from __future__ import annotations

from dataclasses import dataclass

from shinobi_runtime.combat.physical_defense import (
    attack_warning_ms,
    causal_response_start_ms,
    resolver_response_lead_ms,
)


@dataclass(frozen=True)
class FakeProfile:
    startup_ms: int
    delivery: str = "direct"
    effect_parameters: dict[str, object] | None = None


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
    assert fast_start == causal_response_start_ms(attack_start_ms=attack_start, contact_at_ms=contact, reaction_latency_ms=50)
    assert slow_start == causal_response_start_ms(attack_start_ms=attack_start, contact_at_ms=contact, reaction_latency_ms=180)


def test_warning_matches_startup_approach_and_projectile_flight() -> None:
    profile = FakeProfile(
        startup_ms=90,
        delivery="projectile",
        effect_parameters={"approach_time_ms": 110, "projectile": {"flight_time_ms": 240}},
    )
    assert attack_warning_ms(profile) == 440


def test_response_lead_is_zero_once_latency_reaches_contact() -> None:
    assert resolver_response_lead_ms(warning_ms=330, reaction_latency_ms=329) == 1
    assert resolver_response_lead_ms(warning_ms=330, reaction_latency_ms=330) == 0
    assert resolver_response_lead_ms(warning_ms=330, reaction_latency_ms=500) == 0
