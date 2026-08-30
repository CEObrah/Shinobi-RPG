"""Sparse Jianghu faction-state hydration and canonical storage.

Static identity, doctrine, curriculum, and policy live in game data keyed by
faction_id. Mutable faction owners persist current facts plus only campaign
policy overrides that differ from that static profile.
"""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from .faction_politics import faction_camp
from .doctrines import validate_faction_doctrine

_ROOT = Path(__file__).resolve().parents[3]
_WORLD_SEED = _ROOT / "game" / "data" / "martial-world" / "world-seed.json"
_IDENTITIES = _ROOT / "game" / "data" / "martial-world" / "faction-identities.json"

_STATIC_SCALARS = ("name", "type", "outlaw_subtype", "membership_tenure")
_STATIC_MAPPINGS = ("training", "doctrine", "recruitment_policy", "autonomy_policy", "outlaw_policy")
_STATIC_LISTS = ("operating_routes",)


def faction_path(faction_ref: str) -> str:
    return f"state/martial-world/factions/{faction_ref}.json"


def roster_path(faction_ref: str) -> str:
    return f"state/martial-world/people/{faction_ref}.json"


def inventory_path(faction_ref: str) -> str:
    return f"state/martial-world/inventories/{faction_ref}.json"


@lru_cache(maxsize=1)
def _static_factions() -> dict[str, dict[str, Any]]:
    data = json.loads(_WORLD_SEED.read_text(encoding="utf-8"))
    rows = data.get("martial_factions", {}) if isinstance(data, Mapping) else {}
    if not isinstance(rows, Mapping):
        raise ValueError("jianghu world seed faction table invalid")
    return {str(fid): copy.deepcopy(dict(row)) for fid, row in rows.items() if isinstance(row, Mapping)}


def faction_profile(faction_ref: str) -> dict[str, Any] | None:
    row = _static_factions().get(faction_ref)
    return copy.deepcopy(row) if row is not None else None


def all_faction_profiles() -> dict[str, dict[str, Any]]:
    """Return authored public institution profiles as a detached mapping.

    Callers may derive public geography/intelligence from these static profiles,
    but must never mutate the cached source table or treat authored policy as
    mutable campaign state.
    """
    return copy.deepcopy(_static_factions())



def faction_type(faction_ref: str | None) -> str:
    """Return the authored static institution type from the faction profile.

    ``type`` is intentionally hydrated from ``world-seed.json`` rather than
    duplicated into mutable faction state.
    """
    if not isinstance(faction_ref, str) or not faction_ref:
        return ""
    profile = faction_profile(faction_ref)
    value = profile.get("type") if isinstance(profile, Mapping) else None
    return str(value) if isinstance(value, str) else ""




@lru_cache(maxsize=1)
def _identity_policies() -> dict[str, dict[str, Any]]:
    raw = json.loads(_IDENTITIES.read_text(encoding="utf-8"))
    rows = raw.get("identities", {}) if isinstance(raw, Mapping) else {}
    return {str(fid): copy.deepcopy(dict(row)) for fid, row in rows.items() if isinstance(row, Mapping)}


_DYNAMIC_LEADER_TITLES = {
    "martial_house": "House Head",
    "sect": "Sect Leader",
    "martial_school": "School Master",
    "escort_agency": "Chief Escort",
    "outlaw_faction": "Chief",
    "brotherhood_society": "Society Leader",
    "contract_hall": "Hall Master",
    "society": "Society Leader",
}


def faction_presentation_identity(faction_ref: str | None, faction: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return current presentation identity for authored or dynamic factions.

    Authored display titles remain static game data. Dynamic factions derive the
    same public leader-title semantics from their durable current institution
    type, so person reads never require a bootstrap identity row to render a
    lawful office title.
    """
    fid = str(faction_ref or "")
    authored = _identity_policies().get(fid, {})
    if isinstance(authored, Mapping) and authored:
        return copy.deepcopy(dict(authored))
    current = faction if isinstance(faction, Mapping) else {}
    ftype = resolved_faction_type(current)
    if not ftype:
        value = current.get("faction_type") if isinstance(current, Mapping) else None
        ftype = str(value) if isinstance(value, str) else ""
    out: dict[str, Any] = {}
    if ftype:
        out["faction_type"] = ftype
        out["leader_title"] = _DYNAMIC_LEADER_TITLES.get(ftype, "Leader")
    return out


def faction_admission_policy(faction_ref: str | None, faction: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Resolve one recruitment admission policy for authored or dynamic factions."""
    current = faction.get("admission_policy") if isinstance(faction, Mapping) else None
    if isinstance(current, Mapping) and current:
        policy = copy.deepcopy(dict(current))
    else:
        identity = _identity_policies().get(str(faction_ref or ""), {})
        raw = identity.get("admission_policy") if isinstance(identity, Mapping) else None
        policy = copy.deepcopy(dict(raw)) if isinstance(raw, Mapping) else {}
    allowed = [str(x) for x in policy.get("allowed_sexes", []) if str(x) in {"male", "female"}] if isinstance(policy.get("allowed_sexes"), list) else []
    if not allowed:
        allowed = ["female", "male"]
    policy["allowed_sexes"] = list(dict.fromkeys(allowed))
    policy["minimum_entry_age"] = max(0, int(policy.get("minimum_entry_age", 8)))
    policy.setdefault("model", "open")
    return policy

def faction_membership_tenure(faction_ref: str | None, faction: Mapping[str, Any] | None = None) -> str:
    """Return the institution's authored membership-tenure rule.

    Tenure is faction doctrine/policy, not per-person mutable oath state.  A
    ``life_service`` institution therefore needs no duplicated flag on every
    member; all ordinary exit/recruitment paths can consult this one profile.
    """
    if isinstance(faction, Mapping):
        current = faction.get("membership_tenure")
        if isinstance(current, str) and current:
            return current
    if not isinstance(faction_ref, str) or not faction_ref:
        return ""
    profile = faction_profile(faction_ref)
    value = profile.get("membership_tenure") if isinstance(profile, Mapping) else None
    return str(value) if isinstance(value, str) else ""


def allows_ordinary_membership_exit(faction_ref: str | None, faction: Mapping[str, Any] | None = None) -> bool:
    """Whether normal voluntary churn may move a living member outside.

    Death is not an ordinary membership exit.  A future explicit punitive
    expulsion/banishment mechanic, if authored, must remain a separate causal
    action rather than passing through ordinary churn.
    """
    return faction_membership_tenure(faction_ref, faction) != "life_service"


def allows_independent_recruitment(person: Mapping[str, Any], *, target_faction_ref: str) -> bool:
    """Reject ordinary recruitment of a former life-service member elsewhere.

    This is a fail-closed guard for migrated/corrupt independent rows.  Under
    normal play a life-service member never reaches the independent pool via
    ordinary churn in the first place.  Keeping this rule derived from the
    former faction prevents a second mutable oath field from being invented.
    """
    former = person.get("former_faction_ref") if isinstance(person, Mapping) else None
    if not isinstance(former, str) or not former:
        return True
    return faction_membership_tenure(former) != "life_service"


def resolved_faction_type(faction: Mapping[str, Any]) -> str:
    """Resolve the logical type from an already-hydrated view or its ID."""
    value = faction.get("type") if isinstance(faction, Mapping) else None
    if isinstance(value, str) and value:
        return value
    fid = faction.get("faction_id") if isinstance(faction, Mapping) else None
    return faction_type(str(fid)) if isinstance(fid, str) else ""

def _merge_mapping(base: Any, override: Any) -> dict[str, Any]:
    result = copy.deepcopy(dict(base)) if isinstance(base, Mapping) else {}
    if isinstance(override, Mapping):
        result.update(copy.deepcopy(dict(override)))
    return result


def hydrate_faction_state(faction: Mapping[str, Any]) -> dict[str, Any]:
    """Return the logical faction view from sparse hot state."""
    out = copy.deepcopy(dict(faction))
    fid = out.get("faction_id")
    if not isinstance(fid, str) or not fid:
        raise ValueError("jianghu faction_id missing")
    profile = faction_profile(fid)
    if profile is None:
        return out
    for key in _STATIC_SCALARS:
        if key in profile:
            out.setdefault(key, copy.deepcopy(profile[key]))
    for key in _STATIC_LISTS:
        if key in profile:
            out.setdefault(key, copy.deepcopy(profile[key]))
    for key in _STATIC_MAPPINGS:
        if key in profile:
            out[key] = _merge_mapping(profile[key], out.get(key))
    doctrine = out.get("doctrine")
    if isinstance(doctrine, Mapping):
        out["doctrine"] = validate_faction_doctrine(doctrine)
    camp = faction_camp(fid, out)
    if camp:
        out.setdefault("jianghu_camp", camp)
    return out




def living_roster_population(roster: Mapping[str, Any]) -> int:
    people = roster.get("people", []) if isinstance(roster, Mapping) else []
    if not isinstance(people, list):
        return 0
    return sum(
        1 for row in people
        if isinstance(row, Mapping)
        and not (isinstance(row.get("health"), Mapping) and row.get("health", {}).get("status") == "dead")
    )


def with_derived_population(faction: Mapping[str, Any], roster: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(dict(faction))
    out["population"] = living_roster_population(roster)
    return out

def _mapping_deviation(current: Any, baseline: Any) -> dict[str, Any]:
    if not isinstance(current, Mapping):
        return {}
    base = baseline if isinstance(baseline, Mapping) else {}
    return {str(k): copy.deepcopy(v) for k, v in current.items() if k not in base or base.get(k) != v}


def compact_faction_state(faction: Mapping[str, Any]) -> dict[str, Any]:
    """Canonicalize one faction owner to minimum sufficient current state."""
    out = copy.deepcopy(dict(faction))
    fid = out.get("faction_id")
    if not isinstance(fid, str) or not fid:
        raise ValueError("jianghu faction_id missing")
    profile = faction_profile(fid)

    # These are projections of authoritative owners, not independent save truth.
    out.pop("population", None)
    holdings = out.get("holdings")
    if isinstance(holdings, dict):
        holdings.pop("urban_estate_area_m2", None)
        holdings.pop("cultivated_land_mu", None)
    epoch = out.get("training_epoch")
    if isinstance(epoch, dict):
        epoch.pop("current_environment", None)
        epoch.pop("history", None)
        epoch.pop("curriculum_ref", None)

    if profile is not None:
        camp = faction_camp(fid)
        if camp and out.get("jianghu_camp") == camp:
            out.pop("jianghu_camp", None)
        for key in _STATIC_SCALARS + _STATIC_LISTS:
            if key in out and key in profile and out[key] == profile[key]:
                out.pop(key, None)
        for key in _STATIC_MAPPINGS:
            if key not in out:
                continue
            deviation = _mapping_deviation(out.get(key), profile.get(key, {}))
            if deviation:
                out[key] = deviation
            else:
                out.pop(key, None)
    return out


def read_faction(repository: Any, faction_ref: str) -> tuple[str, dict[str, Any]]:
    path = faction_path(faction_ref)
    raw = repository.read_json(path)
    if not isinstance(raw, Mapping):
        raise ValueError("jianghu faction owner invalid")
    if raw.get("faction_id") != faction_ref:
        raise ValueError("jianghu faction path/identity mismatch")
    faction = hydrate_faction_state(raw)
    roster = repository.read_json(roster_path(faction_ref))
    if not isinstance(roster, Mapping):
        raise ValueError("jianghu faction roster invalid")
    return path, with_derived_population(faction, roster)


__all__ = [
    "compact_faction_state",
    "faction_path",
    "faction_profile",
    "faction_admission_policy",
    "all_faction_profiles",
    "faction_type",
    "resolved_faction_type", "faction_presentation_identity",
    "hydrate_faction_state",
    "inventory_path",
    "living_roster_population",
    "read_faction",
    "roster_path",
    "with_derived_population",
]
