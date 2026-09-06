"""Exact-combat integrity for causal defensive response timing.

The physical-defense selector exposes ``reaction_delay_ms`` as latency from the
start of the incoming attack's warning window.  The legacy exact resolver,
however, historically consumed that field as time remaining before contact when
recording defensive interruptions and evaluating counter-intercepts.  Those are
opposite quantities: treating latency as remaining time makes fast reactions
appear late and slow reactions appear early.

This production adapter keeps the physical model's public/trace semantics intact
while translating only the exact resolver's legacy field consumption.  It does
not alter stats, reach, defense quality, damage, Qi, movement, recovery, or
active-defense pressure.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping


def attack_warning_ms(profile: Any) -> int:
    """Return the exact physical warning window represented by an action profile."""
    if profile is None:
        return 150

    startup = max(0, int(getattr(profile, "startup_ms", 0)))
    params = getattr(profile, "effect_parameters", {})
    if not isinstance(params, Mapping):
        params = {}

    approach = params.get("approach_time_ms", 0)
    if isinstance(approach, bool) or not isinstance(approach, int):
        approach = 0

    flight = 0
    projectile = params.get("projectile")
    if isinstance(projectile, Mapping):
        raw_flight = projectile.get("flight_time_ms", 0)
        if isinstance(raw_flight, int) and not isinstance(raw_flight, bool):
            flight = raw_flight

    return max(40, startup + max(0, approach) + max(0, flight))


def resolver_response_lead_ms(*, warning_ms: int, reaction_latency_ms: int) -> int:
    """Translate reaction latency into the resolver's legacy pre-contact lead."""
    warning = max(0, int(warning_ms))
    latency = max(0, int(reaction_latency_ms))
    return max(0, warning - latency)


def causal_response_start_ms(
    *,
    attack_start_ms: int,
    contact_at_ms: int,
    reaction_latency_ms: int,
) -> int:
    """Return the physically causal response start, bounded to the contact time."""
    start = int(attack_start_ms)
    contact = max(start, int(contact_at_ms))
    latency = max(0, int(reaction_latency_ms))
    return min(contact, start + latency)


class _ExactResolverTimingDecision:
    """Proxy one defense decision with exact-resolver timing compatibility.

    Only attribute access to ``reaction_delay_ms`` is translated because the
    legacy exact resolver subtracts that value from contact time.  ``trace()``
    deliberately preserves the physical selector's true latency and records the
    translated lead separately for diagnostics.
    """

    __slots__ = ("_base", "_reaction_latency_ms", "_response_lead_ms")

    def __init__(self, base: Any, *, reaction_latency_ms: int, response_lead_ms: int) -> None:
        self._base = base
        self._reaction_latency_ms = max(0, int(reaction_latency_ms))
        self._response_lead_ms = max(0, int(response_lead_ms))

    @property
    def reaction_delay_ms(self) -> int:
        return self._response_lead_ms

    def trace(self) -> dict[str, Any]:
        trace = dict(self._base.trace())
        trace["reaction_delay_ms"] = self._reaction_latency_ms
        trace["resolver_response_lead_ms"] = self._response_lead_ms
        return trace

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


def adapt_decision_for_exact_resolver(decision: Any, *, profile: Any) -> Any:
    """Adapt only a real active defense for the exact resolver's legacy seam."""
    if not bool(getattr(decision, "detected", False)):
        return decision
    if str(getattr(decision, "response", "none")) == "none":
        return decision

    latency = max(0, int(getattr(decision, "reaction_delay_ms", 0)))
    warning = attack_warning_ms(profile)
    lead = resolver_response_lead_ms(
        warning_ms=warning,
        reaction_latency_ms=latency,
    )
    return _ExactResolverTimingDecision(
        decision,
        reaction_latency_ms=latency,
        response_lead_ms=lead,
    )


def install_combat_reaction_timing_integrity() -> None:
    """Install causal timing translation after pressure/readiness wrappers."""
    from shinobi_runtime.martial_world import exact_combat as exact

    if bool(getattr(exact, "_combat_reaction_timing_integrity_installed", False)):
        return

    base_selector: Callable[..., Any] = exact.select_physical_defense

    def causal_timing_selector(*args: Any, **kwargs: Any) -> Any:
        decision = base_selector(*args, **kwargs)
        profile = kwargs.get("profile")
        if profile is None:
            return decision
        return adapt_decision_for_exact_resolver(decision, profile=profile)

    exact.select_physical_defense = causal_timing_selector
    exact._combat_reaction_timing_integrity_installed = True


__all__ = [
    "adapt_decision_for_exact_resolver",
    "attack_warning_ms",
    "causal_response_start_ms",
    "install_combat_reaction_timing_integrity",
    "resolver_response_lead_ms",
]
