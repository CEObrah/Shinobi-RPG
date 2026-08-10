"""Deterministic sparse reputation evidence mechanics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping


@dataclass(frozen=True)
class ReputationEvidence:
    signal_score: int
    base_weight: int
    source_reliability: int
    clarity: int
    channel_integrity: int
    audience_relevance: int
    corroboration: int

    def __post_init__(self) -> None:
        for name in (
            "signal_score",
            "base_weight",
            "source_reliability",
            "clarity",
            "channel_integrity",
            "audience_relevance",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
                raise ValueError(f"{name} must be 0..100")
        if isinstance(self.corroboration, bool) or not isinstance(self.corroboration, int) or not 0 <= self.corroboration <= 120:
            raise ValueError("corroboration must be 0..120")

    @property
    def effective_weight(self) -> int:
        numerator = (
            self.base_weight
            * self.source_reliability
            * self.clarity
            * self.channel_integrity
            * self.audience_relevance
            * self.corroboration
        )
        # Equivalent to round(base * factor/100 * ...), but entirely integer
        # and stable across runtimes. Half-up is preferable to banker's rounding
        # for a rules engine.
        denominator = 100 ** 5
        return (numerator + denominator // 2) // denominator


def update_axis(
    prior: Mapping[str, Any] | None,
    evidence: ReputationEvidence,
    *,
    prior_mass_cap: int = 400,
) -> Dict[str, int]:
    """Apply one evidence item to one sparse audience-belief axis."""

    weight = evidence.effective_weight
    if weight <= 0:
        if prior is None:
            return {}
        score = prior.get("score")
        mass = prior.get("evidence_mass")
        confidence = prior.get("confidence")
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (score, mass, confidence)):
            raise ValueError("prior reputation axis invalid")
        return {"score": score, "evidence_mass": mass, "confidence": confidence}

    if prior is None:
        new_mass = min(prior_mass_cap, weight)
        confidence = min(100, 20 + (80 * new_mass + prior_mass_cap // 2) // prior_mass_cap)
        return {
            "score": evidence.signal_score,
            "evidence_mass": new_mass,
            "confidence": confidence,
        }

    old_score = prior.get("score")
    old_mass = prior.get("evidence_mass")
    if (
        isinstance(old_score, bool)
        or not isinstance(old_score, int)
        or not 0 <= old_score <= 100
        or isinstance(old_mass, bool)
        or not isinstance(old_mass, int)
        or old_mass < 0
    ):
        raise ValueError("prior reputation axis invalid")
    prior_mass = min(old_mass, max(0, prior_mass_cap - weight))
    denominator = prior_mass + weight
    if denominator <= 0:
        return {}
    numerator = old_score * prior_mass + evidence.signal_score * weight
    new_score = (numerator + denominator // 2) // denominator
    new_mass = min(prior_mass_cap, denominator)
    confidence = min(100, 20 + (80 * new_mass + prior_mass_cap // 2) // prior_mass_cap)
    return {
        "score": max(0, min(100, new_score)),
        "evidence_mass": new_mass,
        "confidence": confidence,
    }
