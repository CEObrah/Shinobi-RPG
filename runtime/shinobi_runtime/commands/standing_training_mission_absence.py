"""Delegated exact-team training while the registered player participant is deployed.

Standing team-training policy can explicitly authorize routine non-player training
under registered NPC instructors when the player participant is unavailable.  The
base living-world trainer correctly treats any active mission participant as a
team-level training preemption.  That conservative rule becomes too broad once a
persisted participation policy has already delegated the absent player's routine
training coverage: an unrelated mission for that one participant must not freeze
the remaining team.

This mixin narrows mission preemption only inside one autonomous team-training
resolution.  It never trains the deployed player, never ignores missions involving
remaining team members, and never applies without an enabled participation policy
that explicitly permits autonomous player participation/coverage.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence


class StandingTrainingMissionAbsenceMixin:
    """Honor persisted delegated coverage without weakening mission preemption."""

    def _delegated_training_absent_refs(
        self,
        *,
        team: Mapping[str, Any],
        scheduler: Any,
    ) -> frozenset[str]:
        policy = self._team_participation_policy(team)
        if (
            policy is None
            or policy.get("participates_in_autonomous_training") is not True
            or policy.get("assemble_nonplayer_members") is not True
        ):
            return frozenset()
        participant_ref = policy.get("participant_ref")
        members = team.get("member_refs")
        if (
            not isinstance(participant_ref, str)
            or not isinstance(members, list)
            or participant_ref not in members
        ):
            return frozenset()

        # Bypass this mixin's contextual filter while discovering whether the
        # explicitly registered participant is actually unavailable on a live
        # mission.  The later normal team-wide check still sees every remaining
        # member, so a mission involving them continues to preempt training.
        mission_ref = super()._team_active_mission_ref(
            scheduler=scheduler,
            member_refs=(participant_ref,),
        )
        if mission_ref is None:
            return frozenset()
        return frozenset((participant_ref,))

    def _team_active_mission_ref(
        self,
        *,
        scheduler: Any,
        member_refs: Sequence[str],
    ) -> str | None:
        ignored = getattr(self, "_standing_training_absent_mission_refs", frozenset())
        filtered = tuple(ref for ref in member_refs if ref not in ignored)
        if not filtered:
            return None
        return super()._team_active_mission_ref(
            scheduler=scheduler,
            member_refs=filtered,
        )

    def _eligible_autonomous_group(self, *, team: Mapping[str, Any], record_writes: Mapping[str, Mapping[str, Any]]):
        group = super()._eligible_autonomous_group(team=team, record_writes=record_writes)
        ignored = getattr(self, "_standing_training_absent_mission_refs", frozenset())
        if group is None or not ignored:
            return group
        instructor_ref, instructor_record, location_ref, member_rows, assembly_basis = group
        filtered_rows = [row for row in member_rows if row[0] not in ignored]
        if len(filtered_rows) < 2:
            return None
        return instructor_ref, instructor_record, location_ref, filtered_rows, assembly_basis

    def _apply_autonomous_team_training(
        self,
        *,
        team: dict[str, Any],
        owner_ref: str,
        at: Any,
        compacted: int,
        command: Any,
        scheduler: Any,
        policy_book: Any,
        world_events: dict[str, Any],
        record_writes: dict[str, dict[str, Any]],
    ) -> Mapping[str, Any]:
        absent = self._delegated_training_absent_refs(team=team, scheduler=scheduler)
        if not absent:
            return super()._apply_autonomous_team_training(
                team=team,
                owner_ref=owner_ref,
                at=at,
                compacted=compacted,
                command=command,
                scheduler=scheduler,
                policy_book=policy_book,
                world_events=world_events,
                record_writes=record_writes,
            )

        sentinel = object()
        previous = getattr(self, "_standing_training_absent_mission_refs", sentinel)
        self._standing_training_absent_mission_refs = absent
        try:
            return super()._apply_autonomous_team_training(
                team=team,
                owner_ref=owner_ref,
                at=at,
                compacted=compacted,
                command=command,
                scheduler=scheduler,
                policy_book=policy_book,
                world_events=world_events,
                record_writes=record_writes,
            )
        finally:
            if previous is sentinel:
                del self._standing_training_absent_mission_refs
            else:
                self._standing_training_absent_mission_refs = previous


__all__ = ["StandingTrainingMissionAbsenceMixin"]
