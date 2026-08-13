from __future__ import annotations

import hashlib
from typing import Any, Dict, Mapping, Optional, Sequence

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.autonomy import AutonomousDecision
from shinobi_runtime.commands.constants import TERMINAL_MISSION_STATES as _TERMINAL_MISSION_STATES
from shinobi_runtime.commands.core import _OwnerResolutionCache
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.living_world_support import *
from shinobi_runtime.commands.mission_owner import MissionOwner, mission_owner_path
from shinobi_runtime.reducers import Mission, MissionObjective
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.sim.scheduler import CausalSchedulerRegistry


class LivingWorldMissionMixin:
    @staticmethod
    def _mission_rank_for_difficulty(difficulty: int) -> str:
        if difficulty < 35:
            return "D"
        if difficulty < 50:
            return "C"
        if difficulty < 70:
            return "B"
        if difficulty < 85:
            return "A"
        return "S"

    def _pending_player_offer(
        self,
        refs: Sequence[str],
        *,
        player_ref: str,
        record_writes: Mapping[str, Mapping[str, Any]],
    ) -> Optional[str]:
        """Return one still-live offered/player mission already tracked as a wake.

        ``wake_required_mission_refs`` is already the faction's bounded list of
        missions that cannot be advanced silently. Player offers share that
        exact semantic: they remain pending until the player acts, while NPC
        missions with high-salience consequences can coexist in the same list.
        """

        for mission_id in reversed(tuple(refs)[-32:]):
            if not isinstance(mission_id, str) or not mission_id.startswith("mission."):
                continue
            path = mission_owner_path(mission_id)
            try:
                raw = record_writes.get(path)
                if raw is None:
                    raw = self.repository.read_json(path)
                owner = MissionOwner.from_record(raw)
            except (FileNotFoundError, TypeError, ValueError):
                continue
            if (
                player_ref in owner.mission.participant_refs
                and owner.mission.state not in _TERMINAL_MISSION_STATES
            ):
                return mission_id
        return None

    def _player_offer_team(
        self,
        config: Mapping[str, Any],
        *,
        player_ref: str,
        scheduler: CausalSchedulerRegistry,
        record_writes: Dict[str, Dict[str, Any]],
    ) -> Optional[tuple[str, Dict[str, Any]]]:
        refs = config.get("team_refs")
        if not isinstance(refs, list) or not refs or len(refs) > 16:
            return None
        for team_ref in refs:
            if not isinstance(team_ref, str) or not team_ref.startswith("team."):
                continue
            try:
                _path, team = self._living_team_view(
                    team_ref,
                    record_writes=record_writes,
                )
            except CommandRejectedError:
                continue
            members = [
                ref for ref in team.get("member_refs", []) if isinstance(ref, str)
            ]
            lifecycle = team.get("lifecycle")
            if (
                team.get("status") != "active"
                or team.get("leader_ref") != player_ref
                or player_ref not in members
                or len(members) < 2
                or team.get("current_assignment_ref") is not None
                or (
                    isinstance(lifecycle, Mapping)
                    and lifecycle.get("purpose_status") not in (None, "active")
                )
            ):
                continue

            busy = False
            member_set = set(members)
            for wrapper in scheduler.hosts.values():
                if getattr(wrapper, "authority_kind", None) != "mission":
                    continue
                owner_path = getattr(wrapper, "owner_ref", None)
                if not isinstance(owner_path, str) or not owner_path:
                    continue
                try:
                    raw_owner = record_writes.get(owner_path)
                    if raw_owner is None:
                        raw_owner = self.repository.read_json(owner_path)
                    owner = MissionOwner.from_record(raw_owner)
                except (FileNotFoundError, TypeError, ValueError):
                    continue
                if (
                    owner.mission.state not in _TERMINAL_MISSION_STATES
                    and owner.starts_at is not None
                    and member_set.intersection(owner.mission.participant_refs)
                ):
                    busy = True
                    break
            if busy:
                continue

            nonplayer_ready = True
            for member_ref in members:
                if member_ref == player_ref:
                    continue
                profile = self._living_member_profile(
                    member_ref,
                    record_writes=record_writes,
                )
                if profile is None or not profile.available:
                    nonplayer_ready = False
                    break
            if nonplayer_ready:
                return team_ref, team
        return None

    def _maybe_offer_player_mission(
        self,
        *,
        decision: Any,
        at: CampaignTime,
        command: CommandEnvelope,
        scheduler: CausalSchedulerRegistry,
        world_events: Dict[str, Any],
        record_writes: Dict[str, Dict[str, Any]],
        faction_record: Dict[str, Any],
    ) -> Optional[Mapping[str, Any]]:
        """Persist one lawful mission offer without accepting it for the player.

        The faction review supplies real mission demand. Policy must explicitly
        opt a faction into player offers and name bounded candidate exact teams.
        The authenticated player must lead one of those teams, the team must be
        active/available, and no earlier player offer from this faction may
        remain unresolved. The resulting Mission is ``offered`` with a future
        deadline. Fresh play context surfaces it because the player is an exact
        participant, but no allegiance, dialogue, acceptance, travel, or mission
        start is chosen here.
        """

        if command.mode != "gameplay":
            return None
        payload = decision.payload
        faction_id = payload.get("faction_id")
        if not isinstance(faction_id, str):
            return None
        try:
            _profile, assignment = self._autonomy_policy_book().faction_context(
                faction_id
            )
        except (TypeError, ValueError, CommandRejectedError):
            return None
        config = assignment.get("player_offer") if isinstance(assignment, Mapping) else None
        if not isinstance(config, Mapping) or config.get("enabled") is not True:
            return None

        faction = faction_record.get("faction") if isinstance(faction_record, Mapping) else None
        plan_state = faction.get("plan_state") if isinstance(faction, Mapping) else None
        if not isinstance(plan_state, dict):
            raise CommandRejectedError("faction_owner_invalid")
        wake_refs = plan_state.setdefault("wake_required_mission_refs", [])
        if not isinstance(wake_refs, list):
            raise CommandRejectedError("faction_owner_invalid")
        pending = self._pending_player_offer(
            wake_refs,
            player_ref=command.actor_id,
            record_writes=record_writes,
        )
        if pending is not None:
            return {
                "kind": "player_mission_offer",
                "skipped": "player_offer_already_pending",
                "mission_id": pending,
            }

        selected = self._player_offer_team(
            config,
            player_ref=command.actor_id,
            scheduler=scheduler,
            record_writes=record_writes,
        )
        if selected is None:
            return None
        team_ref, team = selected
        members = tuple(
            ref for ref in team.get("member_refs", []) if isinstance(ref, str)
        )

        difficulty = payload.get("mission_difficulty", 60)
        if isinstance(difficulty, bool) or not isinstance(difficulty, int):
            difficulty = 60
        difficulty = max(20, min(95, difficulty))
        mission_rank = self._mission_rank_for_difficulty(difficulty)
        objective_kind = self._mission_objective_kind(payload, faction_id, at)
        identity = f"{faction_id}\x00{at}\x00{command.actor_id}\x00{team_ref}\x00player-offer"
        suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:18]
        mission_id = f"mission.offer.{suffix}"
        path = mission_owner_path(mission_id)
        existing_raw = record_writes.get(path)
        if existing_raw is None:
            existing_raw = self.repository.read_optional_bytes(path)
        if existing_raw is not None:
            try:
                existing = (
                    MissionOwner.from_record(existing_raw)
                    if isinstance(existing_raw, Mapping)
                    else MissionOwner.from_record(self.repository.read_json(path))
                )
            except (TypeError, ValueError, FileNotFoundError):
                existing = None
            if (
                existing is not None
                and command.actor_id in existing.mission.participant_refs
                and existing.mission.state not in _TERMINAL_MISSION_STATES
            ):
                return {
                    "kind": "player_mission_offer",
                    "skipped": "player_offer_already_recorded",
                    "mission_id": mission_id,
                }
            return None

        objective = MissionObjective(
            objective_id=f"objective.{suffix}",
            kind=objective_kind,
            required=True,
            dependencies=(),
        )
        mission = Mission(
            mission_id=mission_id,
            state="offered",
            participant_refs=members,
            objectives=(objective,),
            settlement_terms=(),
        )
        authority_ref = team.get("assignment_authority_ref")
        if not isinstance(authority_ref, str) or not authority_ref:
            authority_ref = decision.actor_ref
        try:
            self._resolve_covered_owner(faction_id, cache=_OwnerResolutionCache())
            self._resolve_covered_owner(authority_ref, cache=_OwnerResolutionCache())
        except CommandRejectedError:
            return None
        try:
            funding_holder_ref = self._funding_holder_for(faction_id)
        except CommandRejectedError:
            funding_holder_ref = faction_id

        deadline_days = config.get("response_deadline_days", 7)
        if (
            isinstance(deadline_days, bool)
            or not isinstance(deadline_days, int)
            or not 1 <= deadline_days <= 30
        ):
            deadline_days = 7
        offer_time = scheduler.world_time
        deadline = offer_time.add_seconds(deadline_days * 24 * 60 * 60)
        owner = MissionOwner(
            mission=mission,
            issuer_ref=faction_id,
            authority_ref=authority_ref,
            mission_rank=mission_rank,
            funding_holder_ref=funding_holder_ref,
            escrow_holder_ref=None,
            opened_at=offer_time,
            authorized_at=offer_time,
            starts_at=None,
            deadline_at=deadline,
            next_due_at=None,
            operation_ref=team_ref,
            closed_at=None,
        )
        record_writes[path] = dict(owner.to_record())
        self._sync_mission_scheduler(
            scheduler,
            owner=owner,
            path=path,
            current_time=offer_time,
        )
        if mission_id not in wake_refs:
            wake_refs.append(mission_id)
            wake_refs.sort()
            del wake_refs[:-32]

        classification = team.get("classification")
        if classification not in ("public", "restricted", "secret"):
            classification = str(payload.get("classification") or "restricted")
        event_id = self._append_internal_event(
            world_events,
            command=command,
            identity=f"{mission_id}:{offer_time}:player-offer",
            kind="player_mission_offered",
            at=offer_time,
            host_refs=(faction_id, team_ref, mission_id),
            actor_refs=members,
            affected_owner_refs=(path, self.scheduler_path),
            material_consequence_refs=(
                mission_id,
                f"state:offered",
                f"team:{team_ref}",
                f"rank:{mission_rank}",
            ),
            classification=classification,
            audience_refs=(command.actor_id,),
            source_refs=(str(decision.actor_ref), faction_id),
            reducer_ref="shinobi_runtime.commands.living_world.player_mission_offer",
        )
        return {
            "kind": "player_mission_offer",
            "event_id": event_id,
            "mission_id": mission_id,
            "state": "offered",
            "team_ref": team_ref,
            "participant_refs": list(members),
            "objective_kind": objective_kind,
            "difficulty": difficulty,
            "mission_rank": mission_rank,
            "deadline_at": str(deadline),
            "assignment_basis": "lawful_faction_demand_player_led_team_availability",
        }

    def _apply_autonomous_decision(
        self,
        *,
        decision: Any,
        at: CampaignTime,
        command: CommandEnvelope,
        scheduler: CausalSchedulerRegistry,
        world_events: Dict[str, Any],
        record_writes: Dict[str, Dict[str, Any]],
        faction_record: Dict[str, Any],
    ) -> Mapping[str, Any]:
        kind = decision.kind
        payload = decision.payload
        faction_id = payload.get("faction_id")
        if kind == "team_form":
            academy = self._apply_academy_team_form(
                decision=decision,
                at=at,
                command=command,
                scheduler=scheduler,
                world_events=world_events,
                record_writes=record_writes,
                faction_record=faction_record,
            )
            if academy is not None:
                return academy

        if kind == "mission_generate" and isinstance(faction_id, str):
            faction = (
                faction_record.get("faction")
                if isinstance(faction_record, Mapping)
                else None
            )
            plan_state = (
                faction.get("plan_state") if isinstance(faction, Mapping) else None
            )
            if not isinstance(plan_state, dict):
                raise CommandRejectedError("faction_owner_invalid")
            open_refs = plan_state.setdefault("autonomous_mission_refs", [])
            if not isinstance(open_refs, list):
                raise CommandRejectedError("faction_owner_invalid")
            open_refs[:] = [
                value for value in open_refs if isinstance(value, str) and value
            ]
            capacity = self._mission_capacity(payload)
            if len(open_refs) >= capacity:
                return {
                    "kind": kind,
                    "skipped": "autonomous_mission_capacity_reached",
                    "active_missions": len(open_refs),
                    "capacity": capacity,
                }

            player_offer = self._maybe_offer_player_mission(
                decision=decision,
                at=at,
                command=command,
                scheduler=scheduler,
                world_events=world_events,
                record_writes=record_writes,
                faction_record=faction_record,
            )
            if player_offer is not None:
                return player_offer

            objective_kind = self._mission_objective_kind(payload, faction_id, at)
            selection_payload = dict(payload)
            selection_payload["formed_autonomous_team_refs"] = (
                [
                    ref
                    for ref in plan_state.get("autonomous_team_refs", [])
                    if isinstance(ref, str)
                ]
                if isinstance(plan_state.get("autonomous_team_refs"), list)
                else []
            )
            selected = self._select_mission_team(
                faction_id=faction_id,
                payload=selection_payload,
                objective_kind=objective_kind,
                at=at,
                command=command,
                record_writes=record_writes,
            )
            patched_payload = dict(selection_payload)
            selected_team_ref = None
            if selected is not None:
                selected_team_ref, team = selected
                patched_payload["mission_participant_refs"] = [
                    ref
                    for ref in team.get("member_refs", [])
                    if isinstance(ref, str)
                ]
            current_refs = list(open_refs)
            plan_state["autonomous_mission_refs"] = []
            patched = AutonomousDecision(
                kind=decision.kind,
                actor_ref=decision.actor_ref,
                reason=decision.reason,
                payload=patched_payload,
                material=decision.material,
            )
            self._living_world_team_already_routed = True
            try:
                result = super()._apply_autonomous_decision(
                    decision=patched,
                    at=at,
                    command=command,
                    scheduler=scheduler,
                    world_events=world_events,
                    record_writes=record_writes,
                    faction_record=faction_record,
                )
                generated = plan_state.get("autonomous_mission_refs", [])
                generated_refs = (
                    [ref for ref in generated if isinstance(ref, str)]
                    if isinstance(generated, list)
                    else []
                )
            finally:
                plan_state["autonomous_mission_refs"] = current_refs
                if hasattr(self, "_living_world_team_already_routed"):
                    delattr(self, "_living_world_team_already_routed")
            for ref in generated_refs:
                if ref not in plan_state["autonomous_mission_refs"]:
                    plan_state["autonomous_mission_refs"].append(ref)
            plan_state["autonomous_mission_refs"][:] = plan_state[
                "autonomous_mission_refs"
            ][-capacity:]
            mission_id = result.get("mission_id") if isinstance(result, Mapping) else None
            if (
                selected_team_ref
                and isinstance(mission_id, str)
                and result.get("state") == "active"
            ):
                memory = self._faction_memory(
                    faction_id,
                    at=at,
                    record_writes=record_writes,
                )
                memory["active_mission_team_refs"][mission_id] = selected_team_ref
                result = {
                    **dict(result),
                    "team_ref": selected_team_ref,
                    "assignment_basis": "capability_readiness_history_reputation_logistics",
                }
            return result

        if kind == "mission_advance" and isinstance(faction_id, str):
            faction = (
                faction_record.get("faction")
                if isinstance(faction_record, Mapping)
                else None
            )
            plan_state = (
                faction.get("plan_state") if isinstance(faction, Mapping) else None
            )
            refs = (
                plan_state.get("autonomous_mission_refs")
                if isinstance(plan_state, Mapping)
                else None
            )
            if not isinstance(refs, list) or not refs:
                return super()._apply_autonomous_decision(
                    decision=decision,
                    at=at,
                    command=command,
                    scheduler=scheduler,
                    world_events=world_events,
                    record_writes=record_writes,
                    faction_record=faction_record,
                )
            memory = self._faction_memory(
                faction_id,
                at=at,
                record_writes=record_writes,
            )
            mapping = memory.get("active_mission_team_refs", {})
            selected_mission = self._choose_mission_to_advance(
                refs,
                mapping,
                record_writes,
            )
            original = list(refs)
            refs[:] = [selected_mission] + [
                ref for ref in original if ref != selected_mission
            ]
            try:
                mission_record = record_writes.get(
                    mission_owner_path(selected_mission)
                ) or self.repository.read_json(mission_owner_path(selected_mission))
                mission_owner = MissionOwner.from_record(mission_record)
            except (FileNotFoundError, TypeError, ValueError):
                mission_owner = None
            if mission_owner is not None and not self._autonomous_mission_wake_reasons(
                mission_owner
            ):
                objective = mission_owner.mission.objectives[0]
                score = self._autonomous_mission_resolution_score(
                    mission_owner.mission.participant_refs,
                    objective.kind,
                )
                difficulty = payload.get("mission_difficulty", 60)
                if isinstance(difficulty, bool) or not isinstance(difficulty, int):
                    difficulty = 60
                difficulty = max(20, min(95, difficulty))
                wake = self._high_salience_consequence_wake(
                    mission_id=selected_mission,
                    owner=mission_owner,
                    difficulty=difficulty,
                    mission_score=score,
                    succeeded=score >= difficulty,
                    command=command,
                    at=at,
                    faction_id=faction_id,
                    plan_state=plan_state,
                    world_events=world_events,
                    record_writes=record_writes,
                    classification=str(payload.get("classification") or "restricted"),
                )
                if wake is not None:
                    refs[:] = original
                    return wake
            result = super()._apply_autonomous_decision(
                decision=decision,
                at=at,
                command=command,
                scheduler=scheduler,
                world_events=world_events,
                record_writes=record_writes,
                faction_record=faction_record,
            )
            remaining = set(ref for ref in refs if isinstance(ref, str))
            refs[:] = [ref for ref in original if ref in remaining]
            for ref in list(remaining):
                if ref not in refs:
                    refs.append(ref)
            if isinstance(result, Mapping) and isinstance(result.get("outcome"), str):
                return self._after_autonomous_mission_result(
                    result=dict(result),
                    faction_id=faction_id,
                    decision=decision,
                    at=at,
                    command=command,
                    scheduler=scheduler,
                    world_events=world_events,
                    record_writes=record_writes,
                    faction_record=faction_record,
                )
            return result

        return super()._apply_autonomous_decision(
            decision=decision,
            at=at,
            command=command,
            scheduler=scheduler,
            world_events=world_events,
            record_writes=record_writes,
            faction_record=faction_record,
        )

    def _choose_mission_to_advance(
        self,
        refs: Sequence[str],
        mapping: Mapping[str, Any],
        record_writes: Mapping[str, Dict[str, Any]],
    ) -> str:
        ranked = []
        for mission_id in refs:
            if not isinstance(mission_id, str):
                continue
            team_ref = mapping.get(mission_id) if isinstance(mapping, Mapping) else None
            team_ready = 0
            if isinstance(team_ref, str):
                try:
                    _path, team = self._living_team_view(
                        team_ref,
                        record_writes=dict(record_writes),
                    )
                    members = [
                        ref
                        for ref in team.get("member_refs", [])
                        if isinstance(ref, str)
                    ]
                    team_ready = int(
                        bool(members)
                        and all(
                            (profile := self._living_member_profile(
                                ref,
                                record_writes=record_writes,
                            ))
                            is not None
                            and profile.available
                            for ref in members
                        )
                    )
                except CommandRejectedError:
                    team_ready = 0
            ranked.append((team_ready, mission_id))
        if not ranked:
            return next(ref for ref in refs if isinstance(ref, str))
        return max(ranked, key=lambda row: (row[0], row[1]))[1]

    def _high_salience_consequence_wake(
        self,
        *,
        mission_id: str,
        owner: MissionOwner,
        difficulty: int,
        mission_score: int,
        succeeded: bool,
        command: CommandEnvelope,
        at: CampaignTime,
        faction_id: str,
        plan_state: Dict[str, Any],
        world_events: Dict[str, Any],
        record_writes: Mapping[str, Mapping[str, Any]],
        classification: str,
    ) -> Optional[Mapping[str, Any]]:
        risky = []
        for person_ref in owner.mission.participant_refs:
            try:
                path, _digest, view = self._resolve_covered_owner_view(
                    person_ref,
                    cache=_OwnerResolutionCache(),
                )
            except CommandRejectedError:
                continue
            record = record_writes.get(path, view)
            if (
                not isinstance(record, Mapping)
                or not self._routine_high_salience_person(
                    record,
                    person_ref,
                    command.actor_id,
                )
            ):
                continue
            hypothetical = self._routine_consequence_tier(
                mission_id=mission_id,
                person_ref=person_ref,
                difficulty=difficulty,
                mission_score=mission_score,
                succeeded=succeeded,
                high_salience=False,
            )
            if hypothetical in ("killed", "incapacitated"):
                risky.append(person_ref)
        if not risky:
            return None
        wake_refs = plan_state.setdefault("wake_required_mission_refs", [])
        if not isinstance(wake_refs, list):
            raise CommandRejectedError("faction_owner_invalid")
        if mission_id not in wake_refs:
            wake_refs.append(mission_id)
            wake_refs.sort()
        event_id = self._append_internal_event(
            world_events,
            command=command,
            identity=f"{mission_id}:{at}:living-world-wake",
            kind="autonomous_mission_wake_required",
            at=at,
            host_refs=(faction_id, mission_id),
            actor_refs=owner.mission.participant_refs,
            affected_owner_refs=(mission_owner_path(mission_id),),
            material_consequence_refs=tuple(
                f"high_salience_consequence:{ref}" for ref in sorted(risky)
            ),
            classification=classification,
            audience_refs=(),
            source_refs=(str(owner.authority_ref),),
        )
        return {
            "kind": "mission_advance",
            "mission_id": mission_id,
            "skipped": "high_salience_consequence_wake_required",
            "wake_reasons": ["potential_incapacitation_or_death"],
            "wake_person_refs": sorted(risky),
            "event_id": event_id,
        }
