"""Lightweight deterministic team tactical planning for exact combat.

This module never resolves attacks. It translates lawful shared knowledge,
actual capabilities, doctrine and local geometry into temporary roles and
preferred tactical goals. Individual action selection and the physical combat
resolver remain authoritative for success/failure.
"""
from __future__ import annotations

import hashlib
from statistics import median
from typing import Any, Mapping, Sequence

from .geometry import (
    angular_difference_mdeg,
    facing_to_target_mdeg,
    line_of_sight_clear,
    planar_distance_mm,
    surrounding_state,
)

ROLE_ORDER = (
    "anchor", "screen", "control", "shape", "track", "intercept", "pressure",
    "flank", "protect", "ranged_denial", "reserve", "extract", "exploit",
)


def _map(record: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    row = record.get(key)
    return row if isinstance(row, Mapping) else {}


def _value(record: Mapping[str, Any], *keys: str) -> int:
    containers = (
        record,
        _map(record, "martial_skills"),
        _map(record, "attributes"),
        _map(record, "professional_skills"),
    )
    aliases = {
        "speed": ("speed",),
        "dexterity": ("dexterity",),
        "perception": ("perception",),
        "endurance": ("endurance",),
        "strength": ("strength",),
        "command": ("command",),
        "stealth_scouting": ("stealth_scouting",),
        "hidden_weapons": ("hidden_weapons",),
        "bow": ("bow",),
        "unarmed": ("unarmed",),
        "spear": ("spear",),
        "sword": ("sword",),
        "qi": ("qi",),
        "qi_control": ("qi_control",),
    }
    names: list[str] = []
    for key in keys:
        names.extend(aliases.get(key, (key,)))
    best = 0
    for container in containers:
        for name in names:
            raw = container.get(name)
            if isinstance(raw, int) and not isinstance(raw, bool):
                best = max(best, raw)
    return max(0, best)


def _active(record: Mapping[str, Any]) -> bool:
    health = _map(record, "health")
    condition = _map(record, "condition")
    if health.get("status") in {"dead", "unconscious", "incapacitated"}:
        return False
    if condition.get("dead") is True or condition.get("incapacitated") is True:
        return False
    return True


def threat_score(record: Mapping[str, Any]) -> int:
    martial = max(_value(record, "sword"), _value(record, "spear"), _value(record, "bow"), _value(record, "unarmed"))
    return (
        martial * 5
        + _value(record, "speed") * 3
        + _value(record, "dexterity") * 2
        + _value(record, "perception") * 2
        + _value(record, "endurance")
        + _value(record, "qi") * 2
        + _value(record, "command")
    )


def _role_score(
    role: str,
    record: Mapping[str, Any],
    *,
    member_ref: str,
    primary_ref: str,
    positions: Mapping[str, Mapping[str, Any]],
    obstacles: Sequence[Mapping[str, Any]],
) -> int:
    sword = _value(record, "sword"); spear = _value(record, "spear"); bow = _value(record, "bow")
    unarmed = _value(record, "unarmed"); hidden_weapons = _value(record, "hidden_weapons")
    stealth = _value(record, "stealth_scouting"); command = _value(record, "command")
    speed = _value(record, "speed"); dex = _value(record, "dexterity"); perception = _value(record, "perception")
    strength = _value(record, "strength"); endurance = _value(record, "endurance")
    melee = max(sword, spear, unarmed)
    base = {
        "anchor": max(unarmed, spear)*4 + endurance*3 + strength*2 + command,
        "screen": max(sword, spear, unarmed)*3 + perception*3 + speed*2 + command,
        "control": max(spear, unarmed)*5 + dex*2 + speed*2 + perception,
        "shape": max(spear, bow)*4 + perception*3 + command*2 + dex,
        "track": stealth*5 + perception*4 + speed,
        "intercept": max(unarmed, sword, spear)*4 + speed*3 + perception*2 + dex,
        "pressure": melee*5 + speed*2 + dex*2 + endurance,
        "flank": speed*4 + stealth*3 + dex*2 + melee,
        "protect": max(unarmed, sword, spear)*3 + perception*3 + command*2 + endurance,
        "ranged_denial": bow*6 + perception*3 + dex*2,
        "reserve": command*4 + speed*2 + endurance*2 + perception*2,
        "extract": strength*3 + speed*3 + unarmed*2 + endurance*2,
        "exploit": melee*5 + speed*3 + dex*3 + perception*2 + _value(record, "qi"),
    }.get(role, melee*3 + perception)
    member = positions.get(member_ref); primary = positions.get(primary_ref)
    if isinstance(member, Mapping) and isinstance(primary, Mapping) and member.get("zone_ref") == primary.get("zone_ref"):
        d = planar_distance_mm(member, primary)
        if role in {"intercept", "pressure", "control", "anchor", "protect", "exploit"}:
            base += max(0, 120 - d // 100)
        if role in {"ranged_denial", "shape", "track"} and line_of_sight_clear(positions, actor_ref=member_ref, target_ref=primary_ref, obstacles=obstacles):
            base += 120
        if role == "flank":
            target_facing = int(primary.get("facing_mdeg", 0))
            bearing_from_target = facing_to_target_mdeg(primary, member)
            angle = angular_difference_mdeg(target_facing, bearing_from_target)
            base += angle // 500
    return base


def _problem_for_team(
    *, member_records: Sequence[Mapping[str, Any]], primary_record: Mapping[str, Any], known_enemy_count: int
) -> str:
    if known_enemy_count >= max(3, len(member_records)):
        return "multiple_threats"
    speeds = [_value(row, "speed") for row in member_records] or [0]
    primary_speed = _value(primary_record, "speed")
    if primary_speed >= int(median(speeds)) + 40:
        return "enemy_speed_superiority"
    bow = _value(primary_record, "bow")
    melee = max(_value(primary_record, "sword"), _value(primary_record, "spear"), _value(primary_record, "unarmed"))
    if bow >= melee + 30:
        return "enemy_ranged_superiority"
    guard_skill = max(_value(primary_record, "sword"), _value(primary_record, "spear"), _value(primary_record, "unarmed"))
    endurance = _value(primary_record, "endurance")
    qi_control = _value(primary_record, "qi_control")
    if guard_skill + endurance + qi_control // 2 >= melee * 2 + 60:
        return "enemy_defensive_resilience"
    return "superior_or_primary_combatant"


def _role_sequence(problem: str, count: int) -> list[str]:
    sequences = {
        "enemy_speed_superiority": ["track", "control", "intercept", "shape", "pressure", "exploit", "protect", "reserve"],
        "enemy_ranged_superiority": ["screen", "ranged_denial", "flank", "intercept", "pressure", "exploit", "protect", "reserve"],
        "enemy_defensive_resilience": ["shape", "flank", "control", "pressure", "exploit", "intercept", "reserve", "protect"],
        "multiple_threats": ["anchor", "screen", "intercept", "pressure", "ranged_denial", "reserve", "exploit", "protect"],
        "superior_or_primary_combatant": ["anchor", "pressure", "flank", "intercept", "shape", "exploit", "protect", "reserve"],
    }
    base = list(sequences.get(problem, sequences["superior_or_primary_combatant"]))
    while len(base) < count:
        base.extend(["pressure", "intercept", "reserve", "exploit"])
    return base[:count]


def _doctrine_role_bonus(role: str, doctrine: Mapping[str, Any] | None) -> int:
    if not isinstance(doctrine, Mapping):
        return 0
    def d(key: str) -> int:
        raw = doctrine.get(key, 0)
        return max(0, min(100, int(raw))) if isinstance(raw, int) and not isinstance(raw, bool) else 0
    bonus = 0
    if role in {"pressure", "flank", "exploit"}:
        bonus += d("offensive_pressure") * 2
    if role in {"protect", "screen", "intercept", "extract"}:
        bonus += d("mutual_support") * 2 + d("casualty_preservation")
    if role in {"ranged_denial", "shape"}:
        bonus += d("ranged_emphasis") * 2
    if role == "track":
        bonus += d("scouting_emphasis") * 2
    if role in {"anchor", "screen", "reserve"}:
        bonus += d("formation_cohesion") * 2
    return bonus


def _doctrine_target_priority_bonus(record: Mapping[str, Any], doctrine: Mapping[str, Any] | None) -> int:
    """Doctrine changes who a team tries to pressure, never the target's stats."""
    if not isinstance(doctrine, Mapping):
        return 0
    def d(key: str) -> int:
        raw = doctrine.get(key, 0)
        return max(0, min(100, int(raw))) if isinstance(raw, int) and not isinstance(raw, bool) else 0
    bow = _value(record, "bow")
    melee = max(_value(record, "sword"), _value(record, "spear"), _value(record, "unarmed"))
    speed = _value(record, "speed")
    command = _value(record, "command")
    bonus = 0
    # Casualty-preserving teams prioritize threats most capable of immediately
    # harming or disrupting their formation. Aggressive/concentration doctrine
    # weights the strongest accessible combatant more heavily.
    bonus += (melee + speed) * d("casualty_preservation") // 180
    bonus += max(melee, bow) * d("concentration_of_force") // 160
    bonus += command * d("offensive_pressure") // 240
    if bow >= melee:
        bonus += bow * d("ranged_emphasis") // 220
    return bonus


def _doctrine_desired_states(doctrine: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(doctrine, Mapping):
        return []
    def d(key: str) -> int:
        raw = doctrine.get(key, 0)
        return max(0, min(100, int(raw))) if isinstance(raw, int) and not isinstance(raw, bool) else 0
    states: list[str] = []
    if d("mutual_support") >= 70:
        states.append("maintain_mutual_support")
    if d("casualty_preservation") >= 70 or d("withdrawal_discipline") >= 75:
        states.append("preserve_retreat_corridor")
    if d("formation_cohesion") >= 75:
        states.append("avoid_friendly_lane_overlap")
    if d("ranged_emphasis") >= 70:
        states.append("preserve_clear_missile_lane")
    if d("concentration_of_force") >= 70:
        states.append("concentrate_useful_angles_on_primary")
    if d("individual_initiative") >= 75:
        states.append("allow_local_exploitation")
    return states

def _target_accessibility_score(
    target_ref: str,
    *,
    member_refs: Sequence[str],
    positions: Mapping[str, Mapping[str, Any]],
    obstacles: Sequence[Mapping[str, Any]],
) -> int:
    target = positions.get(target_ref)
    if not isinstance(target, Mapping):
        return 0
    visible = 0; total_distance = 0; count = 0
    for member_ref in member_refs:
        member = positions.get(member_ref)
        if not isinstance(member, Mapping) or member.get("zone_ref") != target.get("zone_ref"):
            continue
        count += 1
        total_distance += planar_distance_mm(member, target)
        if line_of_sight_clear(positions, actor_ref=member_ref, target_ref=target_ref, obstacles=obstacles):
            visible += 1
    if count <= 0:
        return -500
    average_distance_m = total_distance // max(1, count) // 1000
    return visible * 100 - min(300, average_distance_m * 8)


def plan_team_exchange(
    *,
    side_ref: str,
    member_refs: Sequence[str],
    known_enemy_refs: Sequence[str],
    records: Mapping[str, Mapping[str, Any]],
    positions: Mapping[str, Mapping[str, Any]],
    obstacles: Sequence[Mapping[str, Any]] = (),
    objective_kind: str = "eliminate",
    doctrine: Mapping[str, Any] | None = None,
    familiarity_by_member: Mapping[str, int] | None = None,
    at_ms: int = 0,
) -> dict[str, Any]:
    members = [ref for ref in dict.fromkeys(member_refs) if ref in records and _active(records[ref])]
    enemies = [ref for ref in dict.fromkeys(known_enemy_refs) if ref in records and _active(records[ref])]
    if not members:
        raise ValueError("team tactical plan requires active members")
    if not enemies:
        return {
            "plan_id": f"plan:{side_ref}:{at_ms}:no_contact",
            "side_ref": side_ref,
            "generated_at_ms": max(0, int(at_ms)),
            "objective_kind": objective_kind,
            "primary_threat_ref": None,
            "tactical_problem": "no_known_contact",
            "desired_states": ["maintain_cohesion", "acquire_contact"],
            "known_enemy_refs": [],
            "coordination_latency_ms": 1000,
            "assignments": {},
        }
    primary = max(
        enemies,
        key=lambda ref: (
            threat_score(records[ref])
            + _target_accessibility_score(ref, member_refs=members, positions=positions, obstacles=obstacles)
            + _doctrine_target_priority_bonus(records[ref], doctrine),
            threat_score(records[ref]),
            ref,
        ),
    )
    member_rows = [records[ref] for ref in members]
    problem = _problem_for_team(member_records=member_rows, primary_record=records[primary], known_enemy_count=len(enemies))
    desired = {
        "enemy_speed_superiority": ["restrict_primary_mobility", "preserve_interception_lane", "create_opposed_angles"],
        "enemy_ranged_superiority": ["deny_clear_shot", "close_or_break_line_of_sight", "preserve_screen"],
        "enemy_defensive_resilience": ["force_reorientation", "create_flank", "reserve_exploitation_window"],
        "multiple_threats": ["avoid_isolation", "maintain_mutual_support", "concentrate_on_primary_without_opening_rear"],
        "superior_or_primary_combatant": ["overload_distinct_defensive_angles", "deny_free_movement", "preserve_exploitation_actor"],
    }[problem]
    if objective_kind in {"capture", "protect", "extract"}:
        desired = [f"objective_{objective_kind}", *desired]
    desired = list(dict.fromkeys([*desired, *_doctrine_desired_states(doctrine)]))

    fam = familiarity_by_member or {}
    avg_familiarity = sum(max(0, min(100, int(fam.get(ref, 0)))) for ref in members) // max(1, len(members))
    best_command = max(_value(records[ref], "command") for ref in members)
    doctrine_coordination = 0
    if isinstance(doctrine, Mapping):
        for key in ("mutual_support", "formation_cohesion", "individual_initiative"):
            raw = doctrine.get(key)
            if isinstance(raw, int) and not isinstance(raw, bool):
                doctrine_coordination += raw
    coordination_latency_ms = max(120, 1400 - best_command*3 - avg_familiarity*6 - doctrine_coordination)

    roles = _role_sequence(problem, len(members))
    unassigned = set(members)
    assignments: dict[str, dict[str, Any]] = {}
    for role in roles:
        if not unassigned:
            break
        selected = max(
            unassigned,
            key=lambda ref: (
                _role_score(role, records[ref], member_ref=ref, primary_ref=primary, positions=positions, obstacles=obstacles)
                + _doctrine_role_bonus(role, doctrine),
                ref,
            ),
        )
        unassigned.remove(selected)
        score = (
            _role_score(role, records[selected], member_ref=selected, primary_ref=primary, positions=positions, obstacles=obstacles)
            + _doctrine_role_bonus(role, doctrine)
        )
        preferred = "hold" if role in {"anchor", "screen", "protect", "reserve"} else "attack"
        if objective_kind == "capture" and role in {"control", "intercept", "exploit"}:
            preferred = "capture"
        assignments[selected] = {
            "role": role,
            "target_ref": primary,
            "preferred_action": preferred,
            "role_score": score,
            "communication_delay_ms": coordination_latency_ms,
            "setup_for_roles": ["exploit"] if role in {"control", "shape", "intercept", "pressure", "flank"} else [],
            "requires_line_of_sight": role in {"ranged_denial", "track", "shape"},
        }

    primary_pos = positions.get(primary, {})
    plan_seed = "|".join([side_ref, str(at_ms), primary, problem, ",".join(sorted(members)), ",".join(sorted(enemies))])
    return {
        "plan_id": "plan:" + hashlib.sha256(plan_seed.encode()).hexdigest()[:20],
        "side_ref": side_ref,
        "generated_at_ms": max(0, int(at_ms)),
        "objective_kind": objective_kind,
        "primary_threat_ref": primary,
        "primary_threat_score": threat_score(records[primary]),
        "primary_position_snapshot": {
            "x_mm": int(primary_pos.get("x_mm", 0)) if isinstance(primary_pos, Mapping) else 0,
            "y_mm": int(primary_pos.get("y_mm", 0)) if isinstance(primary_pos, Mapping) else 0,
        },
        "tactical_problem": problem,
        "desired_states": desired,
        "known_enemy_refs": sorted(enemies),
        "coordination_latency_ms": coordination_latency_ms,
        "assignments": assignments,
    }


def replan_reasons(
    previous_plan: Mapping[str, Any] | None,
    *,
    active_member_refs: Sequence[str],
    known_enemy_refs: Sequence[str],
    positions: Mapping[str, Mapping[str, Any]],
    objective_kind: str,
    material_geometry_shift_mm: int = 4_000,
) -> tuple[str, ...]:
    if not isinstance(previous_plan, Mapping):
        return ("no_existing_plan",)
    reasons: list[str] = []
    primary = previous_plan.get("primary_threat_ref")
    known = set(known_enemy_refs)
    if isinstance(primary, str) and primary not in known:
        reasons.append("primary_threat_lost_or_disabled")
    if previous_plan.get("objective_kind") != objective_kind:
        reasons.append("objective_changed")
    assignments = previous_plan.get("assignments")
    if isinstance(assignments, Mapping):
        missing = set(assignments) - set(active_member_refs)
        if missing:
            reasons.append("assigned_member_unavailable")
    old_known = set(previous_plan.get("known_enemy_refs", ()))
    if known - old_known:
        reasons.append("new_enemy_contact")
    if isinstance(primary, str) and primary in known:
        primary_row = positions.get(primary)
        if isinstance(primary_row, Mapping):
            same_zone_members = [
                ref for ref in active_member_refs
                if isinstance(positions.get(ref), Mapping) and positions.get(ref, {}).get("zone_ref") == primary_row.get("zone_ref")
            ]
            if not same_zone_members:
                reasons.append("primary_threat_unreachable")
    snapshot = previous_plan.get("primary_position_snapshot")
    current = positions.get(primary) if isinstance(primary, str) else None
    if isinstance(snapshot, Mapping) and isinstance(current, Mapping):
        dx = int(current.get("x_mm", 0)) - int(snapshot.get("x_mm", 0))
        dy = int(current.get("y_mm", 0)) - int(snapshot.get("y_mm", 0))
        if math_isqrt(dx*dx+dy*dy) >= material_geometry_shift_mm:
            reasons.append("material_geometry_shift")
    return tuple(dict.fromkeys(reasons))


def math_isqrt(value: int) -> int:
    # Local helper avoids importing floating math into the planner's replan gate.
    if value <= 0:
        return 0
    x = value
    y = (x + 1) // 2
    while y < x:
        x = y
        y = (x + value // x) // 2
    return x


def protection_geometry(
    positions: Mapping[str, Mapping[str, Any]],
    *, protector_ref: str, protected_ref: str, threat_ref: str
) -> dict[str, Any]:
    protector = positions.get(protector_ref); protected = positions.get(protected_ref); threat = positions.get(threat_ref)
    if not all(isinstance(row, Mapping) for row in (protector, protected, threat)):
        return {"can_physically_screen": False}
    # A protector screens only if their footprint lies close to the threat->protected lane and ahead of the protected body.
    from .geometry import trace_attack_geometry
    trace = trace_attack_geometry(
        positions,
        actor_ref=threat_ref,
        aim_ref=protected_ref,
        body_refs=(protector_ref, protected_ref),
        geometry={"shape": "direct", "width_m": 0.4, "length_m": max(1.0, planar_distance_mm(threat, protected)/1000.0 + 1.0)},
        target_limit=2,
        channel="projectile",
    )
    contacts = [row["participant_ref"] for row in trace["contacts"]]
    return {
        "can_physically_screen": bool(contacts and contacts[0] == protector_ref),
        "contact_order": contacts,
    }


__all__ = [
    "ROLE_ORDER",
    "plan_team_exchange",
    "protection_geometry",
    "replan_reasons",
    "threat_score",
]
