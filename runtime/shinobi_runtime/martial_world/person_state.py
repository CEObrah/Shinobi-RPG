"""Sparse persistent-person storage helpers for the Jianghu campaign.

Persistent rosters store only authoritative identity/capability facts and mutable
state that differs from deterministic defaults. Runtime reads hydrate those
sparse records into ordinary logical person mappings for mechanics.
"""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from .faction_state import read_faction

_HEALTH_DEFAULTS: dict[str, Any] = {
    "status": "ready",
    "injuries": [],
    "toxicity_milli": 0,
    "blood_lost_ml": 0,
    "shock": 0,
    "consciousness": 100,
}

_TRANSIENT_DEFAULTS_KEY = "__state_defaults"

_ROOT = Path(__file__).resolve().parents[3]
_CHARACTER_SYSTEM = _ROOT / "game" / "data" / "martial-world" / "character-system.json"
_KNOWN_MARTIAL_GRADES = frozenset({
    "probationary", "junior", "full", "senior", "elite", "elder",
})

# Persistent training carry uses one stable dense vector instead of repeating
# verbose domain names on every person.  The order is serialization authority:
# append new domains only at the end so old vectors remain readable forever.
# Runtime mechanics still consume the ordinary ``residual_milli`` mapping;
# hydration/compaction are the only layers that know about this storage form.
_TRAINING_RESIDUAL_DOMAINS = (
    "attribute:strength",
    "attribute:speed",
    "attribute:dexterity",
    "attribute:endurance",
    "attribute:perception",
    "attribute:intelligence",
    "attribute:willpower",
    "sword",
    "spear",
    "bow",
    "hidden_weapons",
    "unarmed",
    "stealth_scouting",
    "command",
    "qi",
    "qi_control",
    "professional:medicine",
    "professional:administration",
    "professional:commerce",
    "professional:crafting",
    "professional:instruction",
)
_TRAINING_RESIDUAL_INDEX = {domain: index for index, domain in enumerate(_TRAINING_RESIDUAL_DOMAINS)}


@lru_cache(maxsize=1)
def _skill_keys() -> tuple[tuple[str, ...], tuple[str, ...]]:
    data = json.loads(_CHARACTER_SYSTEM.read_text(encoding="utf-8"))
    martial = data.get("martial_disciplines", {}) if isinstance(data, Mapping) else {}
    professional = data.get("professional_skills", []) if isinstance(data, Mapping) else []
    return (
        tuple(str(k) for k in martial) if isinstance(martial, Mapping) else (),
        tuple(str(k) for k in professional) if isinstance(professional, list) else (),
    )


def martial_member_from_grade(grade: Any) -> bool | None:
    if not isinstance(grade, str) or not grade:
        return None
    if grade in _KNOWN_MARTIAL_GRADES:
        return True
    return None


def _hydrate_skill_map(value: Any, keys: tuple[str, ...]) -> dict[str, int]:
    source = value if isinstance(value, Mapping) else {}
    return {key: max(0, int(source.get(key, 0))) for key in keys}


def _compact_skill_map(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    out: dict[str, int] = {}
    for key, raw in value.items():
        if isinstance(raw, bool):
            continue
        amount = max(0, int(raw))
        if amount:
            out[str(key)] = amount
    return out



def faction_record(repository: Any, faction_ref: str) -> tuple[str, dict[str, Any]]:
    return read_faction(repository, faction_ref)


def home_location_ref(faction: Mapping[str, Any]) -> str | None:
    value = faction.get("local_site_ref") or faction.get("headquarters")
    return str(value) if isinstance(value, str) and value else None


def healthy_health() -> dict[str, Any]:
    return copy.deepcopy(_HEALTH_DEFAULTS)


def _prune_zero_map(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    out: dict[str, int] = {}
    for key, raw in value.items():
        try:
            amount = int(raw)
        except (TypeError, ValueError):
            continue
        if amount:
            out[str(key)] = amount
    return out


def _decode_training_residual_vector(value: Any) -> dict[str, int]:
    """Hydrate the stable persisted carry vector into the logical domain map."""
    if value in (None, []):
        return {}
    if not isinstance(value, list):
        raise ValueError("jianghu training residual vector invalid")
    if len(value) > len(_TRAINING_RESIDUAL_DOMAINS):
        raise ValueError("jianghu training residual vector has unknown trailing domains")
    out: dict[str, int] = {}
    for index, raw in enumerate(value):
        if isinstance(raw, bool):
            raise ValueError("jianghu training residual vector entry invalid")
        try:
            amount = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("jianghu training residual vector entry invalid") from exc
        if amount < 0 or amount >= 1000:
            raise ValueError("jianghu training residual carry must be between 0 and 999")
        if amount:
            out[_TRAINING_RESIDUAL_DOMAINS[index]] = amount
    return out


def _encode_training_residual_vector(value: Any) -> tuple[list[int], dict[str, int]]:
    """Return (dense-known-vector, unknown-domain-fallback-map).

    Unknown domains are retained in the legacy named map rather than silently
    discarded.  Current Jianghu training only emits the canonical domains, but
    this makes compaction safe across future migrations and hand-edited saves.
    """
    residual = _prune_zero_map(value)
    if not residual:
        return [], {}
    highest = -1
    for domain, amount in residual.items():
        index = _TRAINING_RESIDUAL_INDEX.get(domain)
        if index is not None and amount:
            highest = max(highest, index)
    vector = [0] * (highest + 1)
    unknown: dict[str, int] = {}
    for domain, amount in residual.items():
        if amount < 0 or amount >= 1000:
            raise ValueError("jianghu training residual carry must be between 0 and 999")
        index = _TRAINING_RESIDUAL_INDEX.get(domain)
        if index is None:
            unknown[domain] = amount
        elif amount:
            vector[index] = amount
    while vector and vector[-1] == 0:
        vector.pop()
    return vector, unknown


def _decode_training_carry_milli(value: Any) -> dict[str, int]:
    """Decode the canonical one-line decimal carry used by sparse rosters."""
    if value in (None, ""):
        return {}
    if not isinstance(value, str):
        raise ValueError("jianghu training carry must be a decimal vector string")
    parts = value.split(",")
    if len(parts) > len(_TRAINING_RESIDUAL_DOMAINS):
        raise ValueError("jianghu training carry has unknown trailing domains")
    out: dict[str, int] = {}
    for index, raw in enumerate(parts):
        try:
            amount = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("jianghu training carry entry invalid") from exc
        if amount < 0 or amount >= 1000:
            raise ValueError("jianghu training residual carry must be between 0 and 999")
        if amount:
            out[_TRAINING_RESIDUAL_DOMAINS[index]] = amount
    return out


def _encode_training_carry_milli(value: Any) -> tuple[str, dict[str, int]]:
    """Encode current fractional training carry as one compact readable line."""
    vector, unknown = _encode_training_residual_vector(value)
    return ",".join(str(amount) for amount in vector), unknown


def hydrate_person_state(
    person: Mapping[str, Any],
    *,
    faction_ref: str,
    home_location: str | None,
    include_storage_defaults: bool = False,
) -> dict[str, Any]:
    """Return a logical person view from one sparse roster record."""
    out = copy.deepcopy(dict(person))
    stored_faction = out.get("faction_ref")
    if stored_faction not in (None, faction_ref):
        raise ValueError("jianghu sparse person faction conflicts with roster authority")
    out["faction_ref"] = faction_ref
    derived_martial = martial_member_from_grade(out.get("membership_grade"))
    stored_martial = out.pop("martial_member", None)
    if stored_martial is not None and (derived_martial is None or stored_martial is not derived_martial):
        raise ValueError("obsolete martial_member conflicts with membership grade")
    martial_keys, professional_keys = _skill_keys()
    out["martial_skills"] = _hydrate_skill_map(out.get("martial_skills"), martial_keys)
    out["professional_skills"] = _hydrate_skill_map(out.get("professional_skills"), professional_keys)
    out["qi"] = max(0, int(out.get("qi", 0)))
    out["qi_control"] = max(0, int(out.get("qi_control", 0)))
    if home_location is not None:
        out.setdefault("location_ref", home_location)
    out["current_qi"] = max(0, int(out.get("current_qi", out.get("qi", 0))))
    out["fatigue_milli"] = max(0, int(out.get("fatigue_milli", 0)))
    health = out.get("health")
    if isinstance(health, Mapping):
        merged = healthy_health()
        merged.update(copy.deepcopy(dict(health)))
        # Body mass is identity/physical state, not an injury-state default.
        merged.pop("body_mass_kg", None)
        out["health"] = merged
    else:
        out["health"] = healthy_health()
    out.setdefault("standing_offices", [])

    packed_residual = _decode_training_carry_milli(out.pop("training_carry_milli", None))
    training = out.get("training_state")
    if isinstance(training, Mapping):
        state = copy.deepcopy(dict(training))
        vector_residual = _decode_training_residual_vector(state.pop("residual_vector", None))
        named_residual = _prune_zero_map(state.get("residual_milli"))
        for domain, amount in {**vector_residual, **named_residual}.items():
            prior = packed_residual.get(domain)
            if prior is not None and prior != amount:
                raise ValueError("jianghu training residual storage conflicts")
            packed_residual[domain] = amount
        for domain, amount in named_residual.items():
            prior = vector_residual.get(domain)
            if prior is not None and prior != amount:
                raise ValueError("jianghu training residual storage conflicts")
        if packed_residual:
            state["residual_milli"] = packed_residual
        else:
            state.pop("residual_milli", None)
        out["training_state"] = state
    elif packed_residual:
        out["training_state"] = {"residual_milli": packed_residual}
    if include_storage_defaults:
        out[_TRANSIENT_DEFAULTS_KEY] = {
            "faction_ref": faction_ref,
            "home_location_ref": home_location,
        }
    return out


def compact_person_state(
    person: Mapping[str, Any],
    *,
    faction_ref: str | None = None,
    home_location: str | None = None,
) -> dict[str, Any]:
    """Canonicalize a logical person into minimum-sufficient sparse storage."""
    out = copy.deepcopy(dict(person))
    transient = out.pop(_TRANSIENT_DEFAULTS_KEY, None)
    if isinstance(transient, Mapping):
        if faction_ref is None and isinstance(transient.get("faction_ref"), str):
            faction_ref = str(transient["faction_ref"])
        if home_location is None and isinstance(transient.get("home_location_ref"), str):
            home_location = str(transient["home_location_ref"])

    # These are roster/schema/presentation facts, not per-person mutable truth.
    stored_faction = out.pop("faction_ref", None)
    if faction_ref is not None and stored_faction not in (None, faction_ref):
        raise ValueError("jianghu person faction conflicts with roster authority")
    out.pop("headquarters", None)
    out.pop("representation", None)
    out.pop("training_assignment", None)

    derived_martial = martial_member_from_grade(out.get("membership_grade"))
    if derived_martial is not None:
        stored_martial = out.get("martial_member")
        if stored_martial not in (None, derived_martial):
            raise ValueError("jianghu person martial membership conflicts with grade")
        out.pop("martial_member", None)

    for key in ("martial_skills", "professional_skills"):
        out[key] = _compact_skill_map(out.get(key))
    if int(out.get("qi", 0)) == 0:
        out.pop("qi", None)
    else:
        out["qi"] = max(0, int(out["qi"]))
    if int(out.get("qi_control", 0)) == 0:
        out.pop("qi_control", None)
    else:
        out["qi_control"] = max(0, int(out["qi_control"]))

    # Preserve body mass as a direct physical fact while keeping health sparse.
    health_raw = out.get("health")
    if isinstance(health_raw, Mapping) and "body_mass_kg" in health_raw and "body_mass_kg" not in out:
        out["body_mass_kg"] = int(health_raw["body_mass_kg"])
    if isinstance(health_raw, Mapping):
        health = copy.deepcopy(dict(health_raw))
        health.pop("body_mass_kg", None)
        for key, default in _HEALTH_DEFAULTS.items():
            if health.get(key) == default:
                health.pop(key, None)
        # Empty wound arrays and zero current physiology are defaults.
        if health:
            out["health"] = health
        else:
            out.pop("health", None)
    else:
        out.pop("health", None)

    qi = max(0, int(out.get("qi", 0)))
    if int(out.get("current_qi", qi)) == qi:
        out.pop("current_qi", None)
    else:
        out["current_qi"] = max(0, min(qi, int(out.get("current_qi", qi))))

    if int(out.get("fatigue_milli", 0)) == 0:
        out.pop("fatigue_milli", None)
    else:
        out["fatigue_milli"] = max(0, int(out["fatigue_milli"]))

    if home_location is not None and out.get("location_ref") == home_location:
        out.pop("location_ref", None)

    offices = out.get("standing_offices")
    if not isinstance(offices, list) or not offices:
        out.pop("standing_offices", None)

    if int(out.get("personal_cash", 0)) == 0:
        out.pop("personal_cash", None)
    else:
        out["personal_cash"] = max(0, int(out["personal_cash"]))

    packed_residual = _decode_training_carry_milli(out.pop("training_carry_milli", None))
    training = out.get("training_state")
    if isinstance(training, Mapping):
        state = copy.deepcopy(dict(training))
        # Settlement chronology belongs to the scheduler; person training stores
        # only current carry/evidence/focus required by deterministic progression.
        state.pop("last_settled_at", None)
        if int(state.get("institutional_days_applied", 0)) <= 0:
            state.pop("institutional_days_applied", None)
        else:
            state["institutional_days_applied"] = int(state["institutional_days_applied"])
        # Accept either the logical map or an already-compacted vector.  Normal
        # reads hydrate the vector first, but several lifecycle reducers can
        # compact sparse roster rows directly during transfers/departures.
        vector_residual = _decode_training_residual_vector(state.pop("residual_vector", None))
        named_residual = _prune_zero_map(state.get("residual_milli"))
        for domain, amount in {**vector_residual, **named_residual}.items():
            prior = packed_residual.get(domain)
            if prior is not None and prior != amount:
                raise ValueError("jianghu training residual storage conflicts")
            packed_residual[domain] = amount
        residual_packed, residual_unknown = _encode_training_carry_milli(packed_residual)
        evidence = _prune_zero_map(state.get("evidence_milli"))
        state.pop("residual_vector", None)
        if residual_packed:
            out["training_carry_milli"] = residual_packed
        else:
            out.pop("training_carry_milli", None)
        if residual_unknown:
            state["residual_milli"] = residual_unknown
        else:
            state.pop("residual_milli", None)
        if evidence:
            state["evidence_milli"] = evidence
        else:
            state.pop("evidence_milli", None)
        if state.get("focus") in (None, "", "standing_faction_curriculum"):
            state.pop("focus", None)
        if state.get("institutional_paused") is not True:
            state.pop("institutional_paused", None)
        if state:
            out["training_state"] = state
        else:
            out.pop("training_state", None)
    else:
        out.pop("training_state", None)
        residual_packed, residual_unknown = _encode_training_carry_milli(packed_residual)
        if residual_unknown:
            raise ValueError("jianghu training carry contains unknown domain without training state")
        if residual_packed:
            out["training_carry_milli"] = residual_packed
        else:
            out.pop("training_carry_milli", None)

    medicine = out.get("medicine_state")
    if isinstance(medicine, Mapping):
        state = copy.deepcopy(dict(medicine))
        saturation = _prune_zero_map(state.get("category_saturation_milli"))
        active = [copy.deepcopy(dict(row)) for row in state.get("active_effects", []) if isinstance(row, Mapping)] if isinstance(state.get("active_effects", []), list) else []
        toxicity = max(0, int(state.get("toxicity_milli", 0)))
        if saturation:
            state["category_saturation_milli"] = saturation
        else:
            state.pop("category_saturation_milli", None)
        if active:
            state["active_effects"] = active
        else:
            state.pop("active_effects", None)
        if toxicity:
            state["toxicity_milli"] = toxicity
        else:
            state.pop("toxicity_milli", None)
        if saturation or active or toxicity:
            if not isinstance(state.get("last_settled_at"), str) or not state.get("last_settled_at"):
                raise ValueError("active medicine state requires last_settled_at")
            out["medicine_state"] = state
        else:
            out.pop("medicine_state", None)
    else:
        out.pop("medicine_state", None)

    # Static combat behavior is referenced, never duplicated inline.
    out.pop("combat_targeting_doctrine", None)
    return out



def reconcile_faction_population(faction: Mapping[str, Any], roster: Mapping[str, Any]) -> dict[str, Any]:
    """Derive current living population counts from the authoritative roster.

    Faction population counters are compact current aggregates, not independent
    mutable authorities.  Any path that changes life/death or membership can
    call this helper to keep upkeep/recruitment capacity synchronized without a
    historical casualty ledger.
    """
    out = copy.deepcopy(dict(faction))
    rows = roster.get("people", []) if isinstance(roster, Mapping) else []
    if not isinstance(rows, list):
        raise ValueError("jianghu roster people invalid")
    living = 0
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        health = row.get("health", {}) if isinstance(row.get("health"), Mapping) else {}
        if health.get("status") == "dead":
            continue
        living += 1
    out["population"] = living
    return out

def hydrate_roster_state(roster: Mapping[str, Any], *, faction: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(dict(roster))
    faction_ref = str(out.get("faction_ref") or faction.get("faction_id") or "")
    if not faction_ref:
        raise ValueError("jianghu roster faction missing")
    home = home_location_ref(faction)
    people = out.get("people")
    if not isinstance(people, list):
        raise ValueError("jianghu roster people invalid")
    out["people"] = [
        hydrate_person_state(row, faction_ref=faction_ref, home_location=home)
        for row in people
        if isinstance(row, Mapping)
    ]
    return out


def compact_roster_state(roster: Mapping[str, Any], *, faction: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(dict(roster))
    faction_ref = str(out.get("faction_ref") or faction.get("faction_id") or "")
    if not faction_ref:
        raise ValueError("jianghu roster faction missing")
    home = home_location_ref(faction)
    people = out.get("people")
    if not isinstance(people, list):
        raise ValueError("jianghu roster people invalid")
    out["people"] = [
        compact_person_state(row, faction_ref=faction_ref, home_location=home)
        for row in people
        if isinstance(row, Mapping)
    ]
    return out


__all__ = [
    "compact_person_state",
    "compact_roster_state",
    "faction_record",
    "healthy_health",
    "hydrate_roster_state",
    "martial_member_from_grade",
    "reconcile_faction_population",
    "home_location_ref",
    "hydrate_person_state",
]
