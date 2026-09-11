"""Bounded development from real Jianghu field activity and combat.

This module is deliberately not a second XP system.  It converts finite time and
resolved physical actions into the same fractional capability carry and
``training_state.evidence_milli`` already consumed by institutional training.
Structured training remains the efficient path; field experience is lower-rate,
health-sensitive, domain-specific, and challenge-gated.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

from .training import training_gain_milli
from .character_rules import martial_discipline_keys
from .qi import person_current_qi_milli, set_person_current_qi_milli

_MARTIAL = martial_discipline_keys()
_COMBAT_MARTIAL = ("sword", "spear", "bow", "hidden_weapons", "unarmed")
_MAX_EVIDENCE_MILLI = 8000


def _clamp(low: int, high: int, value: int) -> int:
    return max(low, min(high, int(value)))


def _health_milli(person: Mapping[str, Any]) -> int:
    health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
    if health.get("status") in {"dead", "incapacitated"} or int(health.get("consciousness", 100)) <= 0:
        return 0
    fatigue = max(0, int(person.get("fatigue_milli", 0)))
    shock = max(0, int(health.get("shock", 0)))
    toxicity = max(0, int(health.get("toxicity_milli", 0)))
    return _clamp(100, 1000, 1000 - fatigue // 2 - shock * 3 - toxicity // 4)


def _domain_value_and_aptitude(person: Mapping[str, Any], domain: str) -> tuple[int, int]:
    apt = person.get("aptitudes", {}) if isinstance(person.get("aptitudes"), Mapping) else {}
    if domain.startswith("attribute:"):
        key = domain.split(":", 1)[1]
        attrs = person.get("attributes", {}) if isinstance(person.get("attributes"), Mapping) else {}
        aptitude = int(apt.get("cognitive" if key in {"perception", "intelligence", "willpower"} else "physical", 100))
        return max(0, int(attrs.get(key, 0))), aptitude
    if domain.startswith("professional:"):
        key = domain.split(":", 1)[1]
        prof = person.get("professional_skills", {}) if isinstance(person.get("professional_skills"), Mapping) else {}
        return max(0, int(prof.get(key, 0))), int(apt.get("cognitive", 100))
    if domain in {"qi", "qi_control"}:
        return max(0, int(person.get(domain, 0))), int(apt.get("qi", 100))
    martial = person.get("martial_skills", {}) if isinstance(person.get("martial_skills"), Mapping) else {}
    return max(0, int(martial.get(domain, 0))), int(apt.get("leadership" if domain == "command" else "martial", 100))


def _write_points(person: dict[str, Any], domain: str, points: int) -> None:
    if points <= 0:
        return
    if domain.startswith("attribute:"):
        key = domain.split(":", 1)[1]
        row = person.setdefault("attributes", {})
        row[key] = max(0, int(row.get(key, 0))) + points
        return
    if domain.startswith("professional:"):
        key = domain.split(":", 1)[1]
        row = person.setdefault("professional_skills", {})
        row[key] = max(0, int(row.get(key, 0))) + points
        return
    if domain in {"qi", "qi_control"}:
        prior = max(0, int(person.get(domain, 0)))
        prior_current_qi_milli = person_current_qi_milli(person) if domain == "qi" else 0
        person[domain] = prior + points
        if domain == "qi":
            set_person_current_qi_milli(person, prior_current_qi_milli + points * 1000)
        return
    row = person.setdefault("martial_skills", {})
    row[domain] = max(0, int(row.get(domain, 0))) + points


def _evidence_key(domain: str) -> str:
    return domain.split(":", 1)[1] if domain.startswith("professional:") else domain


def _apply_gain(
    person: dict[str, Any], *, domain: str, effective_hours_milli: int,
    pressure_milli: int, evidence_gain_milli: int, recovery_milli: int = 1000,
) -> dict[str, int | str]:
    state = copy.deepcopy(dict(person.get("training_state", {}))) if isinstance(person.get("training_state"), Mapping) else {}
    residual = copy.deepcopy(dict(state.get("residual_milli", {}))) if isinstance(state.get("residual_milli"), Mapping) else {}
    evidence = copy.deepcopy(dict(state.get("evidence_milli", {}))) if isinstance(state.get("evidence_milli"), Mapping) else {}
    health = _health_milli(person)
    current, aptitude = _domain_value_and_aptitude(person, domain)
    pressure = _clamp(0, 1400, pressure_milli)
    hours = max(0, int(effective_hours_milli)) * pressure // 1000
    gain = 0
    if health > 0 and hours > 0:
        gain = training_gain_milli(
            current_skill=current,
            aptitude=aptitude,
            hours_milli=hours,
            instructor_skill=None,
            instruction_skill=0,
            facility_level=0,
            health_milli=health,
            novelty_milli=_clamp(650, 1300, 650 + pressure // 2),
            recovery_milli=_clamp(250, 1200, recovery_milli),
        )
    carry = max(0, int(residual.get(domain, 0))) + max(0, int(gain))
    points, remainder = divmod(carry, 1000)
    if remainder:
        residual[domain] = remainder
    else:
        residual.pop(domain, None)
    _write_points(person, domain, points)

    key = _evidence_key(domain)
    evidence_added = 0
    if evidence_gain_milli > 0 and pressure > 0 and health > 0:
        before = max(0, int(evidence.get(key, 0)))
        after = min(_MAX_EVIDENCE_MILLI, before + max(0, int(evidence_gain_milli)) * pressure // 1000)
        evidence_added = after - before
        if after:
            evidence[key] = after
    if residual:
        state["residual_milli"] = residual
    else:
        state.pop("residual_milli", None)
    if evidence:
        state["evidence_milli"] = evidence
    else:
        state.pop("evidence_milli", None)
    if state:
        person["training_state"] = state
    else:
        person.pop("training_state", None)
    return {"domain": domain, "gain_milli": max(0, int(gain)), "points": points, "evidence_added_milli": evidence_added}


def apply_field_activity(
    person: Mapping[str, Any], *, duration_hours_milli: int,
    activity_kind: str = "road_travel", leader: bool = False,
    pressure_milli: int = 700,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply low-rate development from finite road/escort activity.

    Moving through the world is not treated as full-time training.  Only a
    bounded fraction of occupied hours becomes development-equivalent practice.
    Leadership receives command work only when the person actually leads the
    movement.  An explicit personal martial/Qi focus permits a small amount of
    self-practice during long travel; no faction facility or instructor bonus is
    granted while away.
    """
    out = copy.deepcopy(dict(person))
    hours = max(0, int(duration_hours_milli))
    if hours <= 0 or _health_milli(out) <= 0:
        return out, {"activity_kind": activity_kind, "duration_hours_milli": hours, "domains": []}

    # Ordinary travel converts at most about 22% of occupied road time into
    # useful development-equivalent practice.  This keeps a long expedition
    # meaningful without making marching superior to deliberate instruction.
    field_hours = hours * (220 if activity_kind in {"escort_travel", "patrol_travel"} else 180) // 1000
    weights: list[tuple[str, int]] = [
        ("attribute:endurance", 35),
        ("stealth_scouting", 40),
        ("attribute:perception", 25),
    ]
    if leader:
        weights = [("attribute:endurance", 28), ("stealth_scouting", 32), ("attribute:perception", 20), ("command", 20)]
    total = max(1, sum(weight for _domain, weight in weights))
    rows: list[dict[str, int | str]] = []
    for domain, weight in weights:
        effective = field_hours * weight // total
        rows.append(_apply_gain(
            out, domain=domain, effective_hours_milli=effective,
            pressure_milli=_clamp(350, 1100, pressure_milli),
            evidence_gain_milli=max(1, effective // 20), recovery_milli=850,
        ))

    state = out.get("training_state", {}) if isinstance(out.get("training_state"), Mapping) else {}
    focus = state.get("focus")
    if isinstance(focus, str) and focus in set(_COMBAT_MARTIAL) | {"qi", "qi_control"}:
        # Roughly half an hour of useful self-practice per ten road-hours, capped
        # by occupied time.  The zero-facility/self-instructed gain formula still
        # makes this substantially weaker than Sword Manor training.
        practice = min(hours // 12, max(0, (hours + 19_999) // 20_000) * 500)
        if practice > 0:
            rows.append(_apply_gain(
                out, domain=focus, effective_hours_milli=practice,
                pressure_milli=650, evidence_gain_milli=max(1, practice // 30), recovery_milli=750,
            ))
    return out, {"activity_kind": activity_kind, "duration_hours_milli": hours, "leader": bool(leader), "domains": rows}


def _combat_index(person: Mapping[str, Any]) -> int:
    martial = person.get("martial_skills", {}) if isinstance(person.get("martial_skills"), Mapping) else {}
    attrs = person.get("attributes", {}) if isinstance(person.get("attributes"), Mapping) else {}
    best = max((max(0, int(martial.get(key, 0))) for key in _COMBAT_MARTIAL), default=0)
    reaction = (
        max(0, int(attrs.get("speed", 0)))
        + max(0, int(attrs.get("dexterity", 0)))
        + max(0, int(attrs.get("perception", 0)))
        + max(0, int(attrs.get("willpower", 0)))
    )
    return max(1, best * 8 + reaction * 2 + max(0, int(person.get("qi_control", 0))))


def _combat_pressure(actor: Mapping[str, Any], opponent: Mapping[str, Any]) -> int:
    own = _combat_index(actor)
    other = _combat_index(opponent)
    ratio = other * 1000 // max(1, own)
    # Opponents below ~35% of the actor's effective combat capability provide no
    # meaningful technical development.  This is the anti-farming floor.
    if ratio < 350:
        return 0
    return _clamp(180, 1400, ratio)


def _action_domain(action_kind: str, weapon_ref: str, actor: Mapping[str, Any]) -> str:
    action = str(action_kind or "").lower()
    weapon = str(weapon_ref or "").lower()
    if "bow" in action or "bow" in weapon:
        return "bow"
    if "hidden" in action or any(token in weapon for token in ("needle", "dart", "throwing")):
        return "hidden_weapons"
    if "spear" in action or "spear" in weapon:
        return "spear"
    if weapon == "body_unarmed" or any(token in action for token in ("unarmed", "punch", "kick", "grapple", "elbow", "knee")):
        return "unarmed"
    if any(token in weapon for token in ("jian", "sword")) or any(token in action for token in ("sword", "slash", "cut", "thrust")):
        return "sword"
    martial = actor.get("martial_skills", {}) if isinstance(actor.get("martial_skills"), Mapping) else {}
    return max(_COMBAT_MARTIAL, key=lambda key: (max(0, int(martial.get(key, 0))), key))


def apply_combat_events(
    people_after: Mapping[str, Mapping[str, Any]], *,
    people_before: Mapping[str, Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Convert committed exact-combat actions into bounded development.

    Only actions that reached commitment/contact resolution count.  Invalid,
    friendly, unavailable, or pre-commitment-interrupted actions do not.  The
    defender receives small reaction/perception development only when an actual
    physical defense response was resolved.  No kill/reward bonus exists.
    """
    out = {str(ref): copy.deepcopy(dict(person)) for ref, person in people_after.items()}
    baseline = {str(ref): dict(person) for ref, person in people_before.items()}
    ignored = {
        "invalid_target", "friendly_target_rejected", "target_unavailable", "action_rejected",
        "status_blocks_action", "weapon_not_owned", "visual_targeting_unavailable",
        "strength_draw_requirement_not_met", "projectile_resource_unavailable",
        "action_interrupted_before_commitment", "action_interrupted_by_defense_before_commitment",
        "action_interrupted_by_mount_loss_before_commitment",
    }
    seen: set[tuple[str, int, str]] = set()
    rows: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        actor_ref = str(event.get("actor_ref") or "")
        target_ref = str(event.get("actual_ref") or event.get("intended_ref") or "")
        action_kind = str(event.get("action_kind") or "")
        if not actor_ref or not target_ref or actor_ref == target_ref or actor_ref not in out or target_ref not in out:
            continue
        marker = (actor_ref, int(event.get("declared_at_ms", event.get("start_at_ms", 0)) or 0), action_kind)
        if marker in seen:
            continue
        seen.add(marker)
        result = str(event.get("result") or "")
        if result in ignored:
            continue
        actor_base = baseline.get(actor_ref, out[actor_ref])
        target_base = baseline.get(target_ref, out[target_ref])
        pressure = _combat_pressure(actor_base, target_base)
        if pressure <= 0:
            continue
        domain = _action_domain(action_kind, str(event.get("weapon_ref") or ""), actor_base)
        # One exact exchange is a short burst of high-quality experience, not an
        # hour of drills.  Repeated meaningful exchanges accumulate through the
        # normal fractional carry; weak-opponent exchanges never enter here.
        actor, gain = apply_single_combat_action(out[actor_ref], domain=domain, pressure_milli=pressure)
        out[actor_ref] = actor
        row: dict[str, Any] = {"actor_ref": actor_ref, "target_ref": target_ref, "pressure_milli": pressure, "attack": gain}

        defense = event.get("defense") if isinstance(event.get("defense"), Mapping) else {}
        response = str(defense.get("response") or "") if isinstance(defense, Mapping) else ""
        if response and response != "none" and _health_milli(out[target_ref]) > 0:
            reverse = _combat_pressure(target_base, actor_base)
            if reverse > 0:
                defensive_rows = []
                for defensive_domain in ("attribute:dexterity", "attribute:perception"):
                    defensive_rows.append(_apply_gain(
                        out[target_ref], domain=defensive_domain, effective_hours_milli=35,
                        pressure_milli=reverse, evidence_gain_milli=10, recovery_milli=650,
                    ))
                row["defense"] = defensive_rows
        rows.append(row)
    return out, {"actions_counted": len(rows), "actions": rows[:32]}


def apply_single_combat_action(
    person: Mapping[str, Any], *, domain: str, pressure_milli: int,
) -> tuple[dict[str, Any], dict[str, int | str]]:
    out = copy.deepcopy(dict(person))
    if domain not in _COMBAT_MARTIAL:
        return out, {"domain": domain, "gain_milli": 0, "points": 0, "evidence_added_milli": 0}
    pressure = _clamp(0, 1400, pressure_milli)
    if pressure <= 0 or _health_milli(out) <= 0:
        return out, {"domain": domain, "gain_milli": 0, "points": 0, "evidence_added_milli": 0}
    return out, _apply_gain(
        out, domain=domain, effective_hours_milli=120,
        pressure_milli=pressure, evidence_gain_milli=120, recovery_milli=650,
    )


__all__ = ["apply_field_activity", "apply_combat_events", "apply_single_combat_action"]
