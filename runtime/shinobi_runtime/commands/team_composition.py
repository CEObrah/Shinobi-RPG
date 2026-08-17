"""Pure bounded team-composition and member-derived doctrine projections."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.sim.events import CampaignTime

CAPABILITY_DIMENSIONS = (
    "leadership", "reconnaissance", "control", "assault", "mobility",
    "stealth", "support", "engineering", "capture",
)
TACTICAL_DIMENSIONS = tuple(x for x in CAPABILITY_DIMENSIONS if x != "leadership")
ROLE_BY_DIMENSION = {
    "leadership": "field_lead", "reconnaissance": "reconnaissance_sensor",
    "control": "battlefield_control", "assault": "assault_finisher",
    "mobility": "mobile_interdiction", "stealth": "infiltration",
    "support": "support_recovery", "engineering": "engineering_traps",
    "capture": "capture_restraint",
}
VERB_BY_DIMENSION = {
    "reconnaissance": "See", "control": "Shape", "assault": "Strike",
    "mobility": "Move", "stealth": "Conceal", "support": "Preserve",
    "engineering": "Prepare", "capture": "Secure",
}


@dataclass(frozen=True)
class TeamMemberProfile:
    person_ref: str
    available: bool
    availability_reason: str
    scores: Mapping[str, int]


def player_controlled_record(record: Mapping[str, Any]) -> bool:
    """Detect protected player agency from owner data, never a campaign-specific ID."""
    if record.get("player_controlled") is True:
        return True
    compact = record.get("compact_personality")
    if isinstance(compact, Mapping) and compact.get("player_controlled") is True:
        return True
    roles = record.get("roles")
    if isinstance(roles, Sequence) and not isinstance(roles, (str, bytes, bytearray)):
        if any(str(role).lower() == "player_character" for role in roles):
            return True
    return False


def _score(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, min(100, int(round(float(value)))))


def _section(root: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = root.get(name)
    return value if isinstance(value, Mapping) else {}


def _value(root: Mapping[str, Any], section: str, key: str) -> int:
    return _score(_section(root, section).get(key, 0))


def _flatten_numeric(value: Any, prefix: str = "", depth: int = 0) -> Dict[str, int]:
    if depth > 3 or not isinstance(value, Mapping):
        return {}
    out: Dict[str, int] = {}
    for raw_key, raw in value.items():
        key = f"{prefix}.{str(raw_key).lower()}" if prefix else str(raw_key).lower()
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            out[key] = _score(raw)
        elif isinstance(raw, Mapping):
            out.update(_flatten_numeric(raw, key, depth + 1))
    return out


def _text(record: Mapping[str, Any]) -> str:
    chunks: list[str] = []
    for key in ("roles", "unique_methods", "repertoire", "specialties"):
        raw = record.get(key)
        if isinstance(raw, str):
            chunks.append(raw)
        elif isinstance(raw, Mapping):
            chunks.extend(str(x) for x in raw.keys())
            chunks.extend(str(x) for x in raw.values() if isinstance(x, str))
        elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
            for item in raw:
                if isinstance(item, str):
                    chunks.append(item)
                elif isinstance(item, Mapping):
                    chunks.extend(str(x) for x in item.values() if isinstance(x, str))
    return " ".join(chunks).lower()


def _unavailable(record: Mapping[str, Any]) -> Optional[str]:
    life = str(record.get("life_status", record.get("status", ""))).lower()
    if life in {"dead", "deceased", "captured", "missing", "inactive"}:
        return life
    condition = record.get("condition") if isinstance(record.get("condition"), Mapping) else {}
    ready = str(condition.get("readiness", "")).lower()
    if ready in {"dead", "captured", "incapacitated", "critical", "unconscious"}:
        return ready
    course = record.get("life_course_state") if isinstance(record.get("life_course_state"), Mapping) else {}
    deployment = course.get("deployment") if isinstance(course.get("deployment"), Mapping) else {}
    status = str(deployment.get("status", "")).lower()
    if status in {"deployed", "mission", "on_mission", "traveling", "hospitalized", "recovering", "captured", "unavailable"}:
        return f"deployment:{status}"
    resources = record.get("resources") if isinstance(record.get("resources"), Mapping) else {}
    fatigue, max_fatigue = resources.get("fatigue"), resources.get("max_fatigue")
    if all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (fatigue, max_fatigue)) and max_fatigue > 0 and fatigue / max_fatigue >= 0.85:
        return "fatigue_above_operational_threshold"
    health, max_health = resources.get("health"), resources.get("max_health")
    if all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (health, max_health)) and max_health > 0 and health / max_health < 0.5:
        return "health_below_operational_threshold"
    return None


def capability_profile_from_record(person_ref: str, record: Mapping[str, Any]) -> TeamMemberProfile:
    root = record.get("stats") if record.get("schema") == "person" and isinstance(record.get("stats"), Mapping) else record
    root = root if isinstance(root, Mapping) else {}
    flat, text = _flatten_numeric(root), _text(record)

    def best(*needles: str) -> int:
        values = [v for path, v in flat.items() if any(n in path for n in needles)]
        return max(values) if values else 0

    def avg(*values: int) -> int:
        return sum(_score(v) for v in values) // max(1, len(values))

    def bonus(*words: str) -> int:
        return min(12, 3 * sum(1 for word in words if word in text))

    v = lambda section, key: _value(root, section, key)
    scores = {
        "leadership": avg(v("operational_skills", "leadership"), v("operational_skills", "tactics"), v("operational_skills", "team_coordination"), v("attributes", "intelligence"), v("attributes", "composure"), v("attributes", "presence")) + bonus("leader", "captain", "commander"),
        "reconnaissance": avg(v("attributes", "awareness"), v("operational_skills", "tracking"), v("operational_skills", "investigation"), v("chakra_dimensions", "sensing"), best("reconnaissance", "sensory", "byakugan")) + bonus("sensor", "tracking", "recon", "scout"),
        "control": avg(v("chakra_dimensions", "control"), v("operational_skills", "tactics"), v("operational_skills", "traps"), v("martial_skills", "grappling"), best("ground_control", "space_control", "genjutsu", "restraint", "barrier")) + bonus("control", "restraint", "genjutsu", "terrain"),
        "assault": avg(v("attributes", "strength"), v("attributes", "agility"), v("chakra_dimensions", "output"), best("kenjutsu", "taijutsu", "sword", "close_combat"), v("operational_skills", "tactics")) + bonus("assault", "combat", "sword", "strike"),
        "mobility": avg(v("attributes", "agility"), v("attributes", "coordination"), best("movement", "body_flicker", "shunshin", "pursuit"), v("operational_skills", "survival")) + bonus("mobile", "movement", "pursuit"),
        "stealth": avg(v("operational_skills", "infiltration"), best("stealth"), v("chakra_dimensions", "suppression"), v("attributes", "composure")) + bonus("stealth", "infiltration", "covert"),
        "support": avg(v("operational_skills", "team_coordination"), v("operational_skills", "survival"), v("chakra_dimensions", "control"), best("medical", "healing", "medicine", "support"), v("attributes", "awareness")) + bonus("medical", "support", "recovery", "triage"),
        "engineering": avg(v("operational_skills", "traps"), v("attributes", "intelligence"), v("attributes", "coordination"), best("engineering", "demolition", "explosive", "fuuinjutsu", "seal")) + bonus("engineering", "demolition", "trap", "sabotage"),
        "capture": avg(v("martial_skills", "grappling"), v("chakra_dimensions", "control"), v("operational_skills", "team_coordination"), v("operational_skills", "tactics"), best("capture", "restraint", "binding")) + bonus("capture", "restraint", "binding"),
    }
    scores = {key: min(100, int(value)) for key, value in scores.items()}
    reason = _unavailable(record)
    return TeamMemberProfile(person_ref, reason is None, "ready" if reason is None else reason, scores)


def select_complementary_roster(profiles: Sequence[TeamMemberProfile], *, target_size: int, preferred_refs: Sequence[str] = (), preferred_leader_ref: Optional[str] = None) -> Tuple[TeamMemberProfile, ...]:
    eligible = [p for p in profiles if p.available]
    if len(eligible) < 2:
        return ()
    target = max(2, min(int(target_size), len(eligible), 16))
    preferred = {x for x in preferred_refs if isinstance(x, str)}

    def lead_score(p: TeamMemberProfile) -> int:
        return 4 * p.scores.get("leadership", 0) + p.scores.get("reconnaissance", 0) + p.scores.get("control", 0) + p.scores.get("assault", 0) + (28 if p.person_ref == preferred_leader_ref else 0) + (8 if p.person_ref in preferred else 0)

    selected = [sorted(eligible, key=lambda p: (-lead_score(p), p.person_ref))[0]]
    coverage = {dim: selected[0].scores.get(dim, 0) for dim in TACTICAL_DIMENSIONS}
    while len(selected) < target:
        remaining = [p for p in eligible if p not in selected]
        if not remaining:
            break
        def marginal(p: TeamMemberProfile) -> int:
            gain = sum(max(0, p.scores.get(dim, 0) - coverage[dim]) for dim in TACTICAL_DIMENSIONS)
            baseline = sum(p.scores.get(dim, 0) for dim in TACTICAL_DIMENSIONS) // len(TACTICAL_DIMENSIONS)
            return 3 * gain + baseline + p.scores.get("support", 0) // 2 + (12 if p.person_ref in preferred else 0)
        chosen = sorted(remaining, key=lambda p: (-marginal(p), p.person_ref))[0]
        selected.append(chosen)
        for dim in TACTICAL_DIMENSIONS:
            coverage[dim] = max(coverage[dim], chosen.scores.get(dim, 0))
    return tuple(selected)


def derive_member_roles(roster: Sequence[TeamMemberProfile], *, leader_ref: str) -> Dict[str, str]:
    roles: Dict[str, str] = {}
    used: set[str] = set()
    for profile in roster:
        if profile.person_ref == leader_ref:
            roles[profile.person_ref] = "field_lead"
            continue
        ranked = sorted(TACTICAL_DIMENSIONS, key=lambda dim: (-profile.scores.get(dim, 0), dim))
        best = profile.scores.get(ranked[0], 0)
        chosen = next((dim for dim in ranked if dim not in used and profile.scores.get(dim, 0) >= best - 12), ranked[0])
        used.add(chosen)
        roles[profile.person_ref] = ROLE_BY_DIMENSION[chosen]
    return roles


def _rank(members: Sequence[str], profiles: Mapping[str, TeamMemberProfile], dims: Sequence[str]) -> list[str]:
    return sorted(members, key=lambda ref: (-sum(profiles.get(ref, TeamMemberProfile(ref, True, "ready", {})).scores.get(dim, 0) for dim in dims), ref))[: min(2, len(members))]


def doctrine_seed(profiles: Sequence[TeamMemberProfile]) -> Tuple[str, str]:
    if not profiles:
        return "adaptive combined-arms doctrine", "See. Shape. Secure. Return."
    avg = {dim: sum(p.scores.get(dim, 0) for p in profiles) // len(profiles) for dim in TACTICAL_DIMENSIONS}
    top = sorted(TACTICAL_DIMENSIONS, key=lambda dim: (-avg[dim], dim))[:3]
    identity = " / ".join(ROLE_BY_DIMENSION[dim].replace("_", " ") for dim in top[:2]) + " doctrine"
    motto = ". ".join(VERB_BY_DIMENSION[dim] for dim in top) + ". Return."
    return identity, motto


def build_compact_doctrine(team: Mapping[str, Any], profiles: Mapping[str, TeamMemberProfile], *, at: CampaignTime, doctrine_identity: str, motto: str, training_focus: Sequence[str]) -> Dict[str, Any]:
    team_id, leader = team.get("id"), team.get("leader_ref")
    members = [x for x in team.get("member_refs", []) if isinstance(x, str)]
    roles = team.get("roles") if isinstance(team.get("roles"), Mapping) else {}
    if not isinstance(team_id, str) or not isinstance(leader, str) or leader not in members or len(members) < 2:
        raise CommandRejectedError("team_invalid")
    deputy = team.get("deputy_ref")
    if not isinstance(deputy, str) or deputy not in members or deputy == leader:
        deputy = next(x for x in members if x != leader)
    recon, shape = _rank(members, profiles, ("reconnaissance", "stealth")), _rank(members, profiles, ("control", "engineering"))
    strike, secure = _rank(members, profiles, ("assault", "mobility", "capture")), _rank(members, profiles, ("capture", "leadership", "support"))
    extract = _rank(members, profiles, ("leadership", "support", "mobility"))
    focus = list(dict.fromkeys(x.strip() for x in training_focus if isinstance(x, str) and x.strip()))[:6]
    return {
        "schema": "team-doctrine", "id": f"{team_id}.doctrine", "team_id": team_id,
        "name": f"{team.get('name', team_id)} Combat Doctrine", "status": "active",
        "familiarity": {ref: 0 for ref in members}, "effective_from": str(at), "approved_by": leader,
        "motto": motto.strip(),
        "command": {"captain": leader, "deputy": deputy, "succession_order": [x for x in members if x != leader][:3]},
        "roles": {ref: str(roles.get(ref, "operative")) for ref in members},
        "phases": [
            {"order": 1, "name": "SEE", "primary_members": recon, "objective": "Establish positions, threats, routes, and uncertainty before committing force.", "procedures": ["Cross-check observations before acting on them."]},
            {"order": 2, "name": "SHAPE", "primary_members": shape, "objective": "Alter movement, terrain, information, or routes to create team advantage.", "procedures": ["Create openings for teammates rather than isolated duels."]},
            {"order": 3, "name": "BREAK", "primary_members": strike, "objective": "Exploit the shaped engagement to disrupt coherent enemy action.", "procedures": ["Concentrate on the mission-critical problem."]},
            {"order": 4, "name": "SECURE", "primary_members": secure, "objective": "Complete the authorized objective and stabilize control.", "procedures": ["Mission completion outranks unnecessary pursuit."]},
            {"order": 5, "name": "EXTRACT", "primary_members": extract, "objective": "Account for the team and remove personnel, captives, evidence, or protected assets safely.", "procedures": ["Preserve command and a viable withdrawal route."]},
        ],
        "standing_rules": [
            {"name": "Shared objective", "rule": "Openings belong to the team, not the individual who created them.", "procedures": ["Exploit teammate-created advantages when lawful and useful."]},
            {"name": "Mission first", "rule": "Do not trade the assigned objective for an unnecessary fight.", "procedures": ["Reorient when combat no longer advances the mission."]},
            {"name": "Recover control", "rule": "When coordination breaks, restore information, command, spacing, and an exit before escalating.", "procedures": ["Stabilize before chasing a collapsing plan."]},
        ],
        "mission_modes": [
            {"mode": "reconnaissance", "directive": "Verify while preserving concealment and a report route."},
            {"mode": "capture", "directive": "Create control, restrain the authorized target, and preserve evidence."},
            {"mode": "protection", "directive": "Keep the protected person or asset viable and preserve extraction."},
            {"mode": "direct_combat", "directive": "Concentrate complementary roles on the mission-critical threat."},
        ],
        "contingencies": {"loss_of_control": ["Re-establish the battlefield picture.", "Confirm command and member accountability.", "Create a regroup or withdrawal route."]},
        "extraction": {"primary_members": extract, "procedures": ["Account for every member and material objective before disengagement."]},
        "training": {
            "lead_instructors": [x for x in (team.get("training", {}).get("instructor_refs", []) if isinstance(team.get("training"), Mapping) else []) if isinstance(x, str)] or [leader],
            "scheduled_sessions": [], "shared_drills": focus or ["team coordination", "objective handoff", "extraction"],
            "role_focus": {ref: str(roles.get(ref, "team role execution")) for ref in members},
            "attendance_rule": "Training credit requires actual lawful attendance under the team training model.",
            "interrupt_rule": "Missions, injury, travel, or higher lawful orders interrupt training without double counting.",
            "no_double_counting": True,
        },
        "identity": doctrine_identity.strip(),
    }
