"""Explicit standing-order participation for autonomous exact-team training.

The base living-world trainer deliberately excludes the authenticated player.
This mixin preserves that safe default and permits participation only when a
persisted campaign policy names the player, the exact team, and a bounded target
cycle. It also permits routine non-player team assembly at a registered base
when that movement is already covered by a standing team order. Training
instructor policy is independent from player participation so a team may exclude
the player from autonomous hours while still replacing weak routine instructors.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _OwnerResolutionCache
from shinobi_runtime.commands.team_composition import capability_profile_from_record, player_controlled_record
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.sim.scheduler import CausalSchedulerRegistry

_POLICY_PATH = "game/rules/training/autonomy-participation.json"


def _registered_training_instructors(
    policy: Mapping[str, Any], team_instructors: Sequence[str]
) -> Tuple[str, ...]:
    strategy = policy.get("instructor_strategy", "augment_team_instructors")
    if strategy not in ("augment_team_instructors", "replace_team_instructors"):
        raise CommandRejectedError("team_training_participation_policy_invalid")
    ordered: list[str] = [] if strategy == "replace_team_instructors" else [
        ref for ref in team_instructors if isinstance(ref, str) and ref
    ]
    policy_refs = policy.get("instructor_refs", [])
    if not isinstance(policy_refs, list) or any(
        not isinstance(ref, str) or not ref for ref in policy_refs
    ):
        raise CommandRejectedError("team_training_participation_policy_invalid")
    for ref in policy_refs:
        if ref not in ordered:
            ordered.append(ref)
    return tuple(ordered)


class StandingTrainingParticipationMixin:
    def _team_participation_policy(self, team: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
        team_id = team.get("id")
        if not isinstance(team_id, str):
            return None
        try:
            registry = self.repository.read_json(_POLICY_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("team_training_participation_policy_invalid") from exc
        policies = registry.get("policies") if isinstance(registry, Mapping) else None
        policy = policies.get(team_id) if isinstance(policies, Mapping) else None
        if policy is None:
            return None
        if not isinstance(policy, Mapping) or policy.get("enabled") is not True:
            return None
        participant_ref = policy.get("participant_ref")
        target_cycle = policy.get("target_cycle")
        if (
            not isinstance(participant_ref, str)
            or not isinstance(target_cycle, list)
            or any(not isinstance(value, str) or not value for value in target_cycle)
        ):
            raise CommandRejectedError("team_training_participation_policy_invalid")
        _registered_training_instructors(policy, ())
        return policy

    def _training_candidates(
        self,
        *,
        team: Mapping[str, Any],
        person_ref: str,
        person: Mapping[str, Any],
        policy_cycle: Sequence[str],
    ) -> Tuple[str, ...]:
        policy = self._team_participation_policy(team)
        if (
            policy is not None
            and policy.get("participates_in_autonomous_training") is True
            and policy.get("participant_ref") == person_ref
            and player_controlled_record(person)
        ):
            ordered: list[str] = []
            for target in policy.get("target_cycle", []):
                if target in ordered:
                    continue
                try:
                    self._training_target(dict(person), target)
                except CommandRejectedError:
                    continue
                ordered.append(target)
            return tuple(ordered)
        return super()._training_candidates(
            team=team,
            person_ref=person_ref,
            person=person,
            policy_cycle=policy_cycle,
        )

    def _eligible_autonomous_group(
        self,
        *,
        team: Mapping[str, Any],
        record_writes: Mapping[str, Mapping[str, Any]],
    ) -> Optional[Tuple[str, Mapping[str, Any], str, list[Tuple[str, str, Dict[str, Any]]]]]:
        policy = self._team_participation_policy(team)
        if policy is None:
            return super()._eligible_autonomous_group(team=team, record_writes=record_writes)

        training = team.get("training")
        members = team.get("member_refs")
        instructors = training.get("instructor_refs") if isinstance(training, Mapping) else None
        if not isinstance(members, list) or not isinstance(instructors, list):
            return None
        participant_ref = policy.get("participant_ref")
        player_participates = policy.get("participates_in_autonomous_training") is True
        member_rows: list[Tuple[str, str, Dict[str, Any]]] = []
        for member_ref in members:
            if not isinstance(member_ref, str):
                continue
            try:
                path, _digest, view = self._resolve_covered_owner_view(
                    member_ref, cache=_OwnerResolutionCache()
                )
            except CommandRejectedError:
                continue
            source = record_writes.get(path)
            candidate_view = source if source is not None else view
            resolved = (
                copy.deepcopy(dict(candidate_view))
                if isinstance(candidate_view, Mapping)
                else None
            )
            if not isinstance(resolved, dict):
                continue
            if player_controlled_record(resolved):
                if member_ref != participant_ref or not player_participates:
                    continue
            profile = capability_profile_from_record(member_ref, resolved)
            location = resolved.get("current_location_id")
            if not profile.available or not isinstance(location, str) or not location:
                continue
            member_rows.append((member_ref, path, resolved))

        registered_instructors = _registered_training_instructors(policy, instructors)
        best = None
        for instructor_ref in sorted(registered_instructors):
            try:
                _path, _digest, view = self._resolve_covered_owner_view(
                    instructor_ref, cache=_OwnerResolutionCache()
                )
            except CommandRejectedError:
                continue
            if not isinstance(view, Mapping) or player_controlled_record(view):
                continue
            profile = capability_profile_from_record(instructor_ref, view)
            location = view.get("current_location_id")
            if not profile.available or not isinstance(location, str) or not location:
                continue
            group = [row for row in member_rows if row[2].get("current_location_id") == location]
            if len(group) < 2:
                continue
            candidate = (instructor_ref, view, location, group)
            if (
                best is None
                or len(group) > len(best[3])
                or (len(group) == len(best[3]) and instructor_ref < best[0])
            ):
                best = candidate
        return best

    def _apply_standing_team_assembly(
        self,
        *,
        team: Mapping[str, Any],
        at: CampaignTime,
        scheduler: CausalSchedulerRegistry,
        record_writes: Dict[str, Dict[str, Any]],
    ) -> None:
        policy = self._team_participation_policy(team)
        if policy is None or policy.get("assemble_nonplayer_members") is not True:
            return
        if team.get("status") != "active" or team.get("current_assignment_ref") is not None:
            return
        members = [value for value in team.get("member_refs", []) if isinstance(value, str)]
        if self._team_active_mission_ref(scheduler=scheduler, member_refs=members) is not None:
            return
        assembly = policy.get("assembly_location_ref")
        if not isinstance(assembly, str) or not assembly:
            raise CommandRejectedError("team_training_participation_policy_invalid")
        try:
            graph = self._location_graph()
            if graph.place(assembly) is None and graph.anchor(assembly) == assembly:
                self._resolve_covered_owner(assembly, cache=_OwnerResolutionCache())
        except (CommandRejectedError, TypeError, ValueError) as exc:
            raise CommandRejectedError("team_training_assembly_location_invalid") from exc

        for member_ref in members:
            try:
                path, _digest, view = self._resolve_covered_owner_view(
                    member_ref, cache=_OwnerResolutionCache()
                )
            except CommandRejectedError:
                continue
            source = record_writes.get(path)
            candidate_view = source if source is not None else view
            record = (
                copy.deepcopy(dict(candidate_view))
                if isinstance(candidate_view, Mapping)
                else None
            )
            if not isinstance(record, dict) or player_controlled_record(record):
                continue
            profile = capability_profile_from_record(member_ref, record)
            if not profile.available:
                continue
            location = record.get("current_location_id")
            if not isinstance(location, str) or not location or location == assembly:
                continue
            # Standing assembly is intentionally bounded to local Konoha/Sword
            # Manor duty. Strategic cross-country relocation still requires the
            # ordinary movement/travel domains and can never be inferred here.
            if not (location.startswith("place.konoha") or location == "place.sword_manor"):
                continue
            record["current_location_id"] = assembly
            life = record.get("life_course_state")
            if isinstance(life, dict):
                history = life.get("location_history")
                if isinstance(history, list):
                    history.append({
                        "at": str(at),
                        "location_id": assembly,
                        "reason": "standing exact-team training assembly",
                    })
                    del history[:-64]
            record_writes[path] = record

    def _apply_autonomous_team_training(
        self,
        *,
        team: Dict[str, Any],
        owner_ref: str,
        at: CampaignTime,
        compacted: int,
        command: Any,
        scheduler: CausalSchedulerRegistry,
        policy_book: Any,
        world_events: Dict[str, Any],
        record_writes: Dict[str, Dict[str, Any]],
    ) -> Mapping[str, Any]:
        self._apply_standing_team_assembly(
            team=team,
            at=at,
            scheduler=scheduler,
            record_writes=record_writes,
        )
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


__all__ = ["StandingTrainingParticipationMixin", "_registered_training_instructors"]
