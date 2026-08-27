"""Derived escort-world perception, motive, rest and return helpers.

The module intentionally owns no campaign state.  A convoy/escort movement owns
its real people and route progress; factions own relationships, people and
resources; custody owns prisoners.  Everything here is recomputed from those
owners at the causal boundary where somebody can actually observe or act.
"""
from __future__ import annotations

import hashlib
from typing import Any, Mapping, Sequence

from .exact_combat import capability_from_person
from .health import functional_capacity_factors
from .qi import person_current_qi_milli, qi_recovery_milli, set_person_current_qi_milli


def _stable_permille(*parts: object) -> int:
    raw = "|".join(str(x) for x in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big") % 1000


def person_combat_index(person: Mapping[str, Any]) -> int:
    profile = capability_from_person(person)
    return max(
        1,
        (int(profile.offense) + int(profile.defense) + int(profile.control) + int(profile.mobility)) // 4,
    )


def observed_escort_strength(
    *, observer: Mapping[str, Any], escorts: Sequence[Mapping[str, Any]],
    world_seed: str, observation_ref: str,
) -> dict[str, Any]:
    """Estimate a visible escort party without leaking exact character sheets.

    Headcount is ordinarily countable in a public venue.  Capability is only an
    estimate.  Better perception/intelligence narrows deterministic error, while
    the caller remains responsible for whether the observer can identify the
    institution or any individual through lawful recognition evidence.
    """
    rows = [row for row in escorts if isinstance(row, Mapping)]
    count = len(rows)
    if count <= 0:
        return {
            "visible_escort_count": 0,
            "estimated_combat_index": 0,
            "confidence_milli": 0,
        }
    attrs = observer.get("attributes", {}) if isinstance(observer.get("attributes"), Mapping) else {}
    perception = max(0, int(attrs.get("perception", 0)))
    intelligence = max(0, int(attrs.get("intelligence", 0)))
    actual = max(1, sum(person_combat_index(row) for row in rows) // count)
    observer_quality = min(240, perception + intelligence)
    error_bound = max(60, 380 - observer_quality * 2)
    roll = _stable_permille(world_seed, "escort-strength", observation_ref, observer.get("person_id", ""))
    signed_error = (roll * (error_bound * 2 + 1) // 1000) - error_bound
    estimated = max(1, actual * (1000 + signed_error) // 1000)
    confidence = max(180, min(950, 360 + observer_quality * 3 - error_bound // 2))
    return {
        "visible_escort_count": count,
        "estimated_combat_index": estimated,
        "confidence_milli": confidence,
    }


def observer_fieldcraft_score(person: Mapping[str, Any]) -> int:
    """Derived ability to notice, size up and follow a traveling party."""
    attrs = person.get("attributes", {}) if isinstance(person.get("attributes"), Mapping) else {}
    martial = person.get("martial_skills", {}) if isinstance(person.get("martial_skills"), Mapping) else {}
    return (
        max(0, int(attrs.get("perception", 0))) * 2
        + max(0, int(attrs.get("intelligence", 0)))
        + max(0, int(martial.get("stealth_scouting", 0)))
    )


def best_route_observer(people: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """Choose the best actually present scout/witness without creating observer state."""
    rows = [row for row in people if isinstance(row, Mapping)]
    if not rows:
        return None
    return max(rows, key=lambda row: (observer_fieldcraft_score(row), str(row.get("person_id", ""))))


def route_interception_opportunity_permille(
    *, attacker_faction_type: str, route_threat_milli: int, witness_milli: int,
    hostility: int = 0, observer_confidence_milli: int = 1000,
) -> int:
    """Chance that a geographically plausible faction gets an actionable contact window.

    Outlaws live off road pressure.  Ordinary factions need a serious grievance and
    a plausible local observation opportunity.  This only decides whether the
    faction can consider acting; motive/strength and exact combat remain separate.
    """
    threat = max(0, min(2000, int(route_threat_milli)))
    witness = max(0, min(1000, int(witness_milli)))
    confidence = max(0, min(1000, int(observer_confidence_milli)))
    hostile = max(0, min(100, int(hostility)))
    if str(attacker_faction_type) == "outlaw_faction":
        return min(450, threat * 3 // 10)
    if hostile < 55:
        return 0
    # A feud/revenge party still needs to notice or track the convoy locally.
    base = 25 + (hostile - 55) * 6 + witness // 6
    return max(0, min(400, base * max(250, confidence) // 1000))


_RANSOM_BY_RANK = {
    "commoner": 1_500,
    "merchant": 8_000,
    "local_elite": 20_000,
    "regional_official": 35_000,
    "noble": 80_000,
    "high_official": 120_000,
    "imperial": 250_000,
}


def principal_ransom_value_cash(person: Mapping[str, Any]) -> int:
    """Derive plausible ransom value from current public/social importance.

    This is a motive estimate, not guaranteed ransom income.  No ransom money is
    created here; an actual captive still needs custody and a later bargain.
    """
    rank = str(person.get("social_rank") or "commoner")
    value = int(_RANSOM_BY_RANK.get(rank, _RANSOM_BY_RANK["commoner"]))
    offices = {str(x).split(":", 1)[0] for x in person.get("standing_offices", []) if isinstance(x, str)}
    if offices & {"emperor", "empress", "prince", "princess"}:
        value = max(value, 250_000)
    elif offices & {"leader", "deputy_leader", "grand_minister", "imperial_minister", "imperial_marshal"}:
        value = max(value, 100_000)
    elif offices & {"magistrate", "treasurer", "chief_steward", "merchant_head"}:
        value = max(value, 30_000)
    return value


def interception_decision(
    *, attacker_faction_type: str, relation: Mapping[str, Any] | None,
    own_available_martial: int, own_combat_index: int,
    observed_escort_count: int, observed_escort_combat_index: int,
    cargo_value_cash: int, ransom_value_cash: int, risk_tolerance: int,
    government_risk_milli: int = 0, minimum_attack_advantage_milli: int = 1100,
    civilian_restraint: int = 0,
) -> dict[str, Any]:
    """Decide whether a faction has enough motive and apparent advantage to act.

    Ordinary institutions do not become opportunistic kidnappers merely because a
    rich convoy exists.  Criminal factions may value loot/ransom directly; other
    factions need a serious existing grievance/hostility before violent pursuit
    is even eligible.  Exact combat still determines every physical outcome.
    """
    count = max(0, int(own_available_martial))
    if count <= 0:
        return {"attack": False, "reason": "no_available_force"}
    edge = relation if isinstance(relation, Mapping) else {}
    hostility = max(0, int(edge.get("hostility", 0)))
    trust = int(edge.get("trust", 0))
    criminal = str(attacker_faction_type) == "outlaw_faction"
    grievance = hostility * 5 + max(0, -trust) * 2
    if not criminal and hostility < 55:
        return {
            "attack": False,
            "reason": "no_serious_grievance",
            "hostility": hostility,
        }

    own_power = max(1, count) * max(1, int(own_combat_index))
    apparent_enemy = max(1, int(observed_escort_count)) * max(1, int(observed_escort_combat_index))
    advantage = own_power * 1000 // apparent_enemy
    loot_motive = min(260, max(0, int(cargo_value_cash)) // 4_000)
    ransom_motive = min(320, max(0, int(ransom_value_cash)) // 2_500)
    risk = max(0, min(100, int(risk_tolerance))) * 2
    legal_pressure = max(0, min(1000, int(government_risk_milli))) // 3
    motive = grievance + risk + (loot_motive + ransom_motive if criminal else ransom_motive // 2) - legal_pressure

    base = max(650, int(minimum_attack_advantage_milli))
    if not criminal:
        base = max(base, 1250)
    # Civilian restraint is institutional reluctance to initiate violence
    # against a convoy whose protected principals/crew are civilian.  It never
    # grants immunity: overwhelming grievance and force can still clear the
    # higher physical-advantage requirement.
    restraint = max(0, min(100, int(civilian_restraint)))
    threshold = max(650, base - max(0, motive) * 2) + restraint * 3
    attack = advantage >= threshold
    if ransom_value_cash > cargo_value_cash and ransom_value_cash > 0:
        intent = "kidnap_principal"
    elif hostility >= 70 and not criminal:
        intent = "revenge"
    elif cargo_value_cash > 0:
        intent = "rob_cargo"
    else:
        intent = "hostile_interception"
    return {
        "attack": bool(attack),
        "intent": intent,
        "advantage_milli": advantage,
        "required_advantage_milli": threshold,
        "motive_score": motive,
        "hostility": hostility,
        "criminal": criminal,
        "civilian_restraint": restraint,
    }




_OUTLAW_PUBLIC_RISKS = {
    "road_band": ["road_robbery", "ambush"],
    "mountain_stronghold": ["ambush", "kidnapping", "ransom"],
    "river_pirates": ["cargo_theft", "kidnapping", "ransom"],
    "urban_gang": ["theft", "extortion", "kidnapping"],
    "smuggling_ring": ["cargo_theft", "extortion"],
}


def escort_rest_hours(escorts: Sequence[Mapping[str, Any]]) -> int:
    """Minimum ordinary lodging rest before a surviving escort party returns."""
    rows = [row for row in escorts if isinstance(row, Mapping)]
    if not rows:
        return 0
    fatigue = max(max(0, int(row.get("fatigue_milli", 0))) for row in rows)
    injured = any(
        isinstance(row.get("health"), Mapping)
        and row.get("health", {}).get("status") not in {None, "healthy", "alive"}
        for row in rows
    )
    return min(48, 8 + min(16, fatigue // 150) + (8 if injured else 0))


def escort_can_resume_field_travel(escorts: Sequence[Mapping[str, Any]]) -> bool:
    """An escort does not march an incapacitated or functionally immobile member."""
    for person in escorts:
        if not isinstance(person, Mapping):
            continue
        health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
        if health.get("status") in {"dead", "incapacitated", "critical", "unconscious", "dying"}:
            return False
        wounds = health.get("injuries", []) if isinstance(health.get("injuries"), list) else []
        capacities = functional_capacity_factors([row for row in wounds if isinstance(row, Mapping)])
        if int(capacities.get("field_mobility_milli", 1000)) < 300:
            return False
    return True


def apply_lodging_rest(person: Mapping[str, Any], *, elapsed_hours: int) -> dict[str, Any]:
    """Recover fatigue/current Qi during real safe lodging without healing wounds twice."""
    hours = max(0, int(elapsed_hours))
    out = dict(person)
    if hours <= 0:
        return out
    fatigue_before = max(0, int(out.get("fatigue_milli", 0)))
    # Combat Qi strain is measured in milli-scale burden.  Eight hours of safe
    # sleep should clear ordinary exertion but not extreme overstrain instantly.
    out["fatigue_milli"] = max(0, fatigue_before - hours * 120)
    qi = max(0, int(out.get("qi", 0)))
    current_qi_milli = person_current_qi_milli(out)
    if qi > 0 and current_qi_milli < qi * 1000:
        health = out.get("health", {}) if isinstance(out.get("health"), Mapping) else {}
        health_milli = 1000
        if health.get("status") in {"injured", "critical", "incapacitated", "unconscious"}:
            health_milli = 700
        recovered = qi_recovery_milli(
            qi=qi,
            qi_control=max(0, int(out.get("qi_control", 0))),
            current_qi_milli=current_qi_milli,
            elapsed_minutes=hours * 60,
            rest_state="sleep",
            health_milli=health_milli,
            fatigue_milli=fatigue_before,
        )
        set_person_current_qi_milli(out, int(recovered["current_qi_milli_after"]))
    return out




def interception_force_size(
    *, available_count: int, observed_escort_count: int, hostility: int,
    criminal_scale: int, risk_tolerance: int, known_value_cash: int,
    attacker_faction_type: str,
) -> int:
    """Size a real interception detachment from mission need and capacity.

    This is deliberately not a fixed encounter cap.  The target's visible
    strength establishes the tactical requirement; hostility, criminal
    organization, risk tolerance and known stakes can justify committing more
    of the locally available force. Exact combat terrain/frontage determines
    how many can physically engage at once.
    """
    available=max(0,int(available_count))
    if available<=0:
        return 0
    visible=max(1,int(observed_escort_count)); hostility=max(0,int(hostility))
    scale=max(0,int(criminal_scale)); risk=max(0,min(100,int(risk_tolerance)))
    value=max(0,int(known_value_cash))
    desired=max(2,visible*2+1)
    if attacker_faction_type=="outlaw_faction":
        desired += scale*2
    else:
        desired += max(0,hostility-55)//5
    desired += max(0,risk-50)//10
    # Stakes grow commitment sub-linearly. Tenfold more loot should not cause
    # tenfold more bodies to materialize, but very valuable targets can justify
    # materially larger real detachments when manpower exists.
    if value>0:
        desired += int((value//25_000) ** 0.5)
    return min(available,desired)

__all__ = [
    "apply_lodging_rest",
    "best_route_observer",
    "escort_can_resume_field_travel",
    "escort_rest_hours",
    "interception_decision",
    "observed_escort_strength",
    "observer_fieldcraft_score",
    "person_combat_index",
    "principal_ransom_value_cash",
    "route_interception_opportunity_permille",
]
