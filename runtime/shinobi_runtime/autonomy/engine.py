"""Deterministic, bounded autonomous-world decisions.

This layer never writes repository state. It translates one already-scheduled
host review into a small set of lawful semantic intents. The command planner
remains responsible for authority validation, conservation, manifests, and
persistence.

The important scaling rule is that a review receives only the owner's declared
causal neighborhood from game data. It never scans the world to discover what
it could act on.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence, Tuple

from shinobi_runtime.sim.events import CampaignTime


@dataclass(frozen=True)
class AutonomousDecision:
    kind: str
    actor_ref: str
    reason: str
    payload: Mapping[str, Any]
    material: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind:
            raise ValueError("autonomous decision kind must be non-empty")
        if not isinstance(self.actor_ref, str) or not self.actor_ref:
            raise ValueError("autonomous decision actor_ref must be non-empty")
        if not isinstance(self.reason, str) or not self.reason:
            raise ValueError("autonomous decision reason must be non-empty")
        if not isinstance(self.payload, Mapping):
            raise TypeError("autonomous decision payload must be an object")


@dataclass(frozen=True)
class AutonomousPolicyBook:
    profiles: Mapping[str, Mapping[str, Any]]
    faction_assignments: Mapping[str, Mapping[str, Any]]
    team_profiles: Mapping[str, Mapping[str, Any]]
    institution_assignments: Mapping[str, Mapping[str, Any]]

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "AutonomousPolicyBook":
        if not isinstance(record, Mapping) or record.get("schema") != "autonomy-policies":
            raise ValueError("autonomy policy book is invalid")
        profiles = record.get("profiles")
        assignments = record.get("faction_assignments")
        team_profiles = record.get("team_profiles")
        institution_assignments = record.get("institution_assignments", {})
        if not all(
            isinstance(value, Mapping)
            for value in (profiles, assignments, team_profiles, institution_assignments)
        ):
            raise ValueError("autonomy policy maps are invalid")
        return cls(
            profiles=dict(profiles),
            faction_assignments=dict(assignments),
            team_profiles=dict(team_profiles),
            institution_assignments=dict(institution_assignments),
        )

    def faction_context(
        self, faction_id: str
    ) -> Tuple[Mapping[str, Any], Mapping[str, Any]]:
        assignment = self.faction_assignments.get(faction_id)
        if not isinstance(assignment, Mapping):
            return {}, {}
        profile_ref = assignment.get("profile_ref")
        profile = self.profiles.get(profile_ref)
        if not isinstance(profile_ref, str) or not isinstance(profile, Mapping):
            raise ValueError(
                "faction autonomy assignment references an unknown profile"
            )
        return profile, assignment

    def institution_assignment(self, institution_id: str) -> Mapping[str, Any]:
        value = self.institution_assignments.get(institution_id)
        return value if isinstance(value, Mapping) else {}

    def team_profile(self, team_type: str) -> Mapping[str, Any]:
        profile = self.team_profiles.get(team_type)
        if isinstance(profile, Mapping):
            return profile
        fallback = self.team_profiles.get("default")
        if not isinstance(fallback, Mapping):
            raise ValueError("team autonomy policy lacks a default profile")
        return fallback


def _stable_index(
    identity: str, at: CampaignTime, modulo: int, salt: str = ""
) -> int:
    if modulo <= 0:
        return 0
    material = f"{identity}\x00{at}\x00{salt}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big") % modulo


def _leader(faction: Mapping[str, Any]) -> Optional[str]:
    leadership = faction.get("leadership_ids")
    if isinstance(leadership, Sequence) and not isinstance(
        leadership, (str, bytes, bytearray)
    ):
        for value in leadership:
            if isinstance(value, str) and value:
                return value
    key_members = faction.get("key_member_ids")
    if isinstance(key_members, Sequence) and not isinstance(
        key_members, (str, bytes, bytearray)
    ):
        for value in key_members:
            if isinstance(value, str) and value:
                return value
    return None


def _player_offer_review_enabled(assignment: Mapping[str, Any]) -> bool:
    config = assignment.get("player_offer")
    return (
        isinstance(config, Mapping)
        and config.get("enabled") is True
        and config.get("evaluate_each_review") is True
    )


def review_faction(
    *,
    faction_record: Mapping[str, Any],
    at: CampaignTime,
    compacted_reviews: int,
    policy_book: AutonomousPolicyBook,
) -> Tuple[AutonomousDecision, ...]:
    """Return at most three bounded semantic intents for one faction review.

    Routine repeated reviews are analytically compacted. A ten-year skip does
    not create hundreds of missions. Player-facing mission demand may be checked
    once per real faction review when static policy explicitly opts in, while an
    unresolved offer remains protected by the mission subsystem's pending-offer
    guard.
    """

    faction = faction_record.get("faction")
    if not isinstance(faction, Mapping) or faction.get("status") != "active":
        return ()
    faction_id = faction.get("id")
    if not isinstance(faction_id, str) or not faction_id:
        return ()
    profile, assignment = policy_book.faction_context(faction_id)
    if not profile:
        return ()
    actor = _leader(faction) or faction_id
    cycle = profile.get("action_cycle")
    if not isinstance(cycle, Sequence) or isinstance(
        cycle, (str, bytes, bytearray)
    ) or not cycle:
        return ()
    actions = tuple(value for value in cycle if isinstance(value, str) and value)
    if not actions:
        return ()
    action = actions[
        _stable_index(faction_id, at, len(actions), salt=str(compacted_reviews))
    ]
    reason = str(
        profile.get(
            "reason",
            faction.get("current_plan")
            or faction.get("goal")
            or "scheduled autonomous review",
        )
    )
    plan_state = (
        faction.get("plan_state")
        if isinstance(faction.get("plan_state"), Mapping)
        else {}
    )
    open_missions = (
        [
            value
            for value in plan_state.get("autonomous_mission_refs", [])
            if isinstance(value, str) and value
        ]
        if isinstance(plan_state, Mapping)
        and isinstance(plan_state.get("autonomous_mission_refs", []), list)
        else []
    )
    formed_teams = (
        [
            value
            for value in plan_state.get("autonomous_team_refs", [])
            if isinstance(value, str) and value
        ]
        if isinstance(plan_state, Mapping)
        and isinstance(plan_state.get("autonomous_team_refs", []), list)
        else []
    )
    team_creation = (
        dict(assignment.get("team_creation", {}))
        if isinstance(assignment.get("team_creation"), Mapping)
        else None
    )
    if team_creation and team_creation.get("team_id") in formed_teams:
        team_creation = None
    common = {
        "faction_id": faction_id,
        "profile_ref": assignment.get("profile_ref"),
        "compacted_reviews": compacted_reviews,
        "force_ref": assignment.get("force_ref"),
        "formation_registry_ref": assignment.get("formation_registry_ref"),
        "formation_size": assignment.get("formation_size"),
        "max_operational_personnel": assignment.get("max_operational_personnel"),
        "team_refs": list(assignment.get("team_refs", []))
        if isinstance(assignment.get("team_refs"), list)
        else [],
        "institution_ref": assignment.get("institution_ref"),
        "institution_registry_ref": assignment.get("institution_registry_ref"),
        "source_pool_id": assignment.get("source_pool_id"),
        "destination_pool_id": assignment.get("destination_pool_id"),
        "mission_participant_refs": list(
            assignment.get("mission_participant_refs", [])
        )
        if isinstance(assignment.get("mission_participant_refs"), list)
        else [],
        "classification": profile.get("classification", "restricted"),
        "mission_objective_cycle": list(
            profile.get("mission_objective_cycle", [])
        )
        if isinstance(profile.get("mission_objective_cycle"), list)
        else [],
        "mission_difficulty": int(profile.get("mission_difficulty", 60))
        if isinstance(profile.get("mission_difficulty", 60), int)
        and not isinstance(profile.get("mission_difficulty", 60), bool)
        else 60,
        "team_creation": team_creation,
        "open_autonomous_mission_refs": open_missions,
        "formed_autonomous_team_refs": formed_teams,
        "allow_formation_expand": bool(
            assignment.get("allow_formation_expand", False)
        ),
    }
    decisions = []

    # Player-facing tasking is an independent institutional concern. It must not
    # disappear merely because the faction's deterministic routine action chose
    # paperwork, drilling, or another valid background task this review.
    offer_review = _player_offer_review_enabled(assignment)
    if offer_review:
        decisions.append(
            AutonomousDecision(
                kind="mission_generate",
                actor_ref=actor,
                reason=(
                    "Review lawful player-eligible mission demand through the "
                    "configured institutional offer lane."
                ),
                payload=common,
                material=True,
            )
        )

    if open_missions:
        decisions.append(
            AutonomousDecision(
                kind="mission_advance",
                actor_ref=actor,
                reason=(
                    "Advance one already-active autonomous mission before "
                    "creating additional background tasking."
                ),
                payload=common,
                material=True,
            )
        )
    if common["team_creation"]:
        decisions.append(
            AutonomousDecision(
                kind="team_form",
                actor_ref=actor,
                reason=(
                    "A lawful named cell is available to be organized from "
                    "already-existing personnel."
                ),
                payload=common,
                material=True,
            )
        )
    formation_size = common.get("formation_size")
    if (
        not common["team_creation"]
        and isinstance(formation_size, int)
        and not isinstance(formation_size, bool)
        and formation_size > 0
        and action != "formation_expand"
    ):
        decisions.append(
            AutonomousDecision(
                kind="formation_expand",
                actor_ref=actor,
                reason=(
                    "Maintain a bounded operational formation topology from "
                    "already-conserved force manpower."
                ),
                payload=common,
                material=True,
            )
        )
    if action == "formation_expand" and not common["allow_formation_expand"]:
        action = "formation_drill"
    if not (offer_review and action == "mission_generate"):
        decisions.append(
            AutonomousDecision(
                kind=action,
                actor_ref=actor,
                reason=reason,
                payload=common,
                material=action != "routine_summary",
            )
        )
    if compacted_reviews > 1:
        decisions.append(
            AutonomousDecision(
                kind="routine_summary",
                actor_ref=actor,
                reason="Compacted routine owner activity between material reviews.",
                payload={**common, "routine_review_count": compacted_reviews},
                material=False,
            )
        )
    # A review may emit more material decisions than one transaction should
    # eventually execute, but silently slicing them here made the slice a
    # fictional limit.  The command layer owns bounded execution/continuation;
    # the policy engine must expose the complete deterministic demand set.
    return tuple(decisions)


def review_team(
    *,
    team_record: Mapping[str, Any],
    at: CampaignTime,
    compacted_reviews: int,
    policy_book: AutonomousPolicyBook,
) -> Tuple[AutonomousDecision, ...]:
    if (
        team_record.get("schema") != "exact-team"
        or team_record.get("status") != "active"
    ):
        return ()
    team_id = team_record.get("id")
    team_type = team_record.get("team_type")
    leader = team_record.get("leader_ref")
    if not all(
        isinstance(value, str) and value for value in (team_id, team_type, leader)
    ):
        return ()
    profile = policy_book.team_profile(team_type)
    payload = {
        "team_id": team_id,
        "team_type": team_type,
        "doctrine_ref": team_record.get("doctrine_ref"),
        "training_focus": list(profile.get("training_focus", []))
        if isinstance(profile.get("training_focus"), list)
        else [],
        "doctrine_identity": str(
            profile.get("doctrine_identity", "adaptive mission-team doctrine")
        ),
        "motto": str(
            profile.get(
                "motto", "Observe. Coordinate. Complete the mission. Return together."
            )
        ),
        "compacted_reviews": compacted_reviews,
    }
    return (
        AutonomousDecision(
            kind="team_development_review",
            actor_ref=leader,
            reason=(
                "The exact team reviews doctrine, training priorities, and "
                "practiced coordination."
            ),
            payload=payload,
            material=True,
        ),
    )
