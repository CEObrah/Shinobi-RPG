"""Player mission-assignment requests without caller-authored mission content.

The gameplay command persists only team, acceptable ranks, and a bounded mission
focus. Existing Konoha mission-market/autonomy mechanics still derive every
actual offer field. A request can therefore filter a lawful offer but can never
create a target, objective, destination, threat, reward, or outcome.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Mapping, Optional, Sequence

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.campaign_mission_reporting import CampaignCommandPlanner as _Base
from shinobi_runtime.commands.core import _BuiltPlan, _exact_payload, _json_bytes, _stable_id
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.mission_assignment_requests import (
    MISSION_ASSIGNMENT_DESK_REF,
    MISSION_ASSIGNMENT_OFFICE_REF,
    MISSION_ASSIGNMENT_REQUEST_PATH,
    MISSION_FOCI,
    assignment_request_ref,
    filter_candidates_for_focus,
    fulfill_assignment_request,
    load_assignment_request_registry,
    normalize_acceptable_ranks,
    pending_assignment_request,
)
from shinobi_runtime.commands.specs import COMMAND_SPECS, CommandSpec
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.sim.scheduler import CausalSchedulerRegistry
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest


_TEAM_REGISTRY_PATH = "state/team/registry.json"


def _install_mission_assignment_request_spec() -> None:
    COMMAND_SPECS.setdefault(
        "mission_assignment_request_resolution",
        CommandSpec(
            ("team_ref", "acceptable_ranks", "mission_focus"),
            (),
            "Submit one bounded mission-assignment preference; the assignment authority still derives any actual mission offer from lawful world demand.",
        ),
    )


_install_mission_assignment_request_spec()


class CampaignCommandPlanner(_Base):
    """Production planner with durable player mission solicitation."""

    COMMAND_TYPES = frozenset(COMMAND_SPECS)

    def _active_exact_team_members(
        self,
        record_writes: Mapping[str, Mapping[str, Any]],
    ) -> set[str]:
        """Return members of every active exact team, honoring staged after-images.

        Academy team formation still consumes this compatibility hook while the
        newer generic team-formation path checks membership routes per candidate.
        The exact team owners remain authority; the registry is only the bounded
        list of active owner IDs, and staged team writes override repository
        before-images during one transaction.
        """
        staged_registry = record_writes.get(_TEAM_REGISTRY_PATH)
        if staged_registry is None:
            try:
                registry = self.repository.read_json(_TEAM_REGISTRY_PATH)
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("exact_team_registry_invalid") from exc
        else:
            registry = staged_registry
        refs = registry.get("active_teams") if isinstance(registry, Mapping) else None
        if (
            not isinstance(refs, list)
            or any(not isinstance(ref, str) or not ref for ref in refs)
        ):
            raise CommandRejectedError("exact_team_registry_invalid")

        staged_by_id: Dict[str, Mapping[str, Any]] = {}
        for record in record_writes.values():
            if not isinstance(record, Mapping) or record.get("schema") != "exact-team":
                continue
            team_id = record.get("id")
            if isinstance(team_id, str) and team_id:
                staged_by_id[team_id] = record

        members: set[str] = set()
        for team_ref in refs:
            team = staged_by_id.get(team_ref)
            if team is None:
                try:
                    _path, team = self._exact_team(team_ref)
                except CommandRejectedError:
                    continue
            if team.get("status") == "active":
                members.update(
                    ref for ref in team.get("member_refs", []) if isinstance(ref, str) and ref
                )

        # A team formed earlier in the same transaction may not yet appear in
        # the staged registry projection. It is still an authoritative staged
        # owner and therefore must reserve its members immediately.
        for team in staged_by_id.values():
            if team.get("status") == "active":
                members.update(
                    ref for ref in team.get("member_refs", []) if isinstance(ref, str) and ref
                )
        return members

    def _mission_assignment_request_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _exact_payload(
            command.payload,
            ("team_ref", "acceptable_ranks", "mission_focus"),
            command.command_type,
        )
        team_ref = _stable_id(
            command.payload.get("team_ref"),
            "mission_assignment_request_team_invalid",
            prefix="team.",
        )
        ranks = normalize_acceptable_ranks(command.payload.get("acceptable_ranks"))
        focus = command.payload.get("mission_focus")
        if focus not in MISSION_FOCI:
            raise CommandRejectedError("mission_assignment_request_focus_invalid")

        _team_path, team = self._exact_team(team_ref)
        members = team.get("member_refs") if isinstance(team, Mapping) else None
        authority_ref = team.get("assignment_authority_ref") if isinstance(team, Mapping) else None
        if (
            team.get("status") != "active"
            or not isinstance(members, list)
            or command.actor_id not in members
            or team.get("leader_ref") != command.actor_id
            or team.get("current_assignment_ref") is not None
            or not isinstance(authority_ref, str)
            or not authority_ref
        ):
            raise CommandRejectedError("mission_assignment_request_team_unavailable")

        scene = copy.deepcopy(self._scene_base(current_time))
        if scene.get("location_id") != MISSION_ASSIGNMENT_DESK_REF:
            raise CommandRejectedError("mission_assignment_request_wrong_place")

        registry = load_assignment_request_registry(self.repository)
        if pending_assignment_request(
            registry,
            requester_ref=command.actor_id,
            team_ref=team_ref,
        ) is not None:
            raise CommandRejectedError("mission_assignment_request_already_pending")

        request_ref = assignment_request_ref(team_ref, command.actor_id, command.digest)
        requests = registry["requests"]
        if request_ref in requests:
            raise CommandRejectedError("mission_assignment_request_conflict")
        requests[request_ref] = {
            "request_ref": request_ref,
            "team_ref": team_ref,
            "requester_ref": command.actor_id,
            "assignment_authority_ref": authority_ref,
            "assignment_office_ref": MISSION_ASSIGNMENT_OFFICE_REF,
            "acceptable_ranks": list(ranks),
            "mission_focus": focus,
            "status": "pending",
            "submitted_at": str(current_time),
        }

        world_events = self._world_events()
        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind="mission_assignment_requested",
            at=current_time,
            host_refs=(MISSION_ASSIGNMENT_OFFICE_REF, team_ref),
            actor_refs=(command.actor_id,),
            place_refs=(MISSION_ASSIGNMENT_DESK_REF,),
            causal_refs=(team_ref,),
            affected_owner_refs=(MISSION_ASSIGNMENT_REQUEST_PATH,),
            material_consequence_refs=(request_ref,),
            classification="restricted",
            audience_refs=(command.actor_id, authority_ref),
            source_refs=(command.actor_id,),
            reducer_ref="shinobi_runtime.commands.campaign_mission_assignment.mission_assignment_request_resolution",
        )
        scene["scene_summary"] = (
            f"{team_ref} submits a {focus} mission assignment request for ranks "
            f"{','.join(ranks)} at {MISSION_ASSIGNMENT_DESK_REF}."
        )
        scene["decision_required"] = None
        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            self.scene_path: _json_bytes(scene),
            MISSION_ASSIGNMENT_REQUEST_PATH: _json_bytes(registry),
            **self._world_event_writes(world_events),
        }
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("mission assignment request write set changed after planning")
            self._assert_meta(
                overlay,
                manifest,
                meta_path=self.meta_path,
                command=command,
                world_time=current_time,
            )
            staged = overlay.read_json(MISSION_ASSIGNMENT_REQUEST_PATH)
            request = staged.get("requests", {}).get(request_ref)
            if (
                not isinstance(request, Mapping)
                or request.get("status") != "pending"
                or tuple(request.get("acceptable_ranks", ())) != ranks
                or request.get("mission_focus") != focus
            ):
                raise ValueError("mission assignment request did not persist")

        return _BuiltPlan(
            code="mission_assignment_request_resolution_ready",
            affected_refs=expected_paths,
            writes=writes,
            result={
                "command_type": command.command_type,
                "request_ref": request_ref,
                "team_ref": team_ref,
                "acceptable_ranks": list(ranks),
                "mission_focus": focus,
                "status": "pending",
                "semantic_event_id": event_id,
            },
            validator=validate,
        )

    def _player_offer_team(
        self,
        config: Mapping[str, Any],
        *,
        player_ref: str,
        scheduler: CausalSchedulerRegistry,
        record_writes: Dict[str, Dict[str, Any]],
    ) -> Optional[tuple[str, Dict[str, Any]]]:
        request = getattr(self, "_active_assignment_request", None)
        if not isinstance(request, Mapping):
            return super()._player_offer_team(
                config,
                player_ref=player_ref,
                scheduler=scheduler,
                record_writes=record_writes,
            )
        team_ref = request.get("team_ref")
        refs = config.get("team_refs")
        if (
            not isinstance(team_ref, str)
            or not isinstance(refs, list)
            or team_ref not in refs
        ):
            return None
        restricted = dict(config)
        restricted["team_refs"] = [team_ref]
        return super()._player_offer_team(
            restricted,
            player_ref=player_ref,
            scheduler=scheduler,
            record_writes=record_writes,
        )

    def _mission_objective_kind(
        self,
        payload: Mapping[str, Any],
        faction_id: str,
        at: CampaignTime,
    ) -> str:
        request = getattr(self, "_active_assignment_request", None)
        if not isinstance(request, Mapping):
            return super()._mission_objective_kind(payload, faction_id, at)
        focus = request.get("mission_focus")
        current = getattr(self, "_active_player_offer_demand_candidates", ())
        filtered = filter_candidates_for_focus(current, focus) if isinstance(focus, str) else ()
        if not filtered:
            raise CommandRejectedError("mission_assignment_request_no_focus_candidate")
        prior = current
        self._active_player_offer_demand_candidates = filtered
        try:
            return super()._mission_objective_kind(payload, faction_id, at)
        finally:
            self._active_player_offer_demand_candidates = prior

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
        registry = load_assignment_request_registry(
            self.repository,
            record_writes=record_writes,
        )
        request = pending_assignment_request(
            registry,
            requester_ref=command.actor_id,
        )
        if request is None:
            return super()._maybe_offer_player_mission(
                decision=decision,
                at=at,
                command=command,
                scheduler=scheduler,
                world_events=world_events,
                record_writes=record_writes,
                faction_record=faction_record,
            )

        ranks = request.get("acceptable_ranks")
        focus = request.get("mission_focus")
        if (
            not isinstance(ranks, list)
            or not ranks
            or not isinstance(focus, str)
            or focus not in MISSION_FOCI
        ):
            raise CommandRejectedError("mission_assignment_request_registry_invalid")
        difficulty = getattr(decision, "payload", {}).get("mission_difficulty", 60)
        if isinstance(difficulty, bool) or not isinstance(difficulty, int):
            difficulty = 60
        difficulty = max(20, min(95, difficulty))
        mission_rank = self._mission_rank_for_difficulty(difficulty)
        if mission_rank not in ranks:
            return None

        faction_id = (
            decision.payload.get("faction_id")
            if hasattr(decision, "payload") and isinstance(decision.payload, Mapping)
            else None
        )
        candidates = self._player_offer_demand_candidates(faction_id) if isinstance(faction_id, str) else ()
        if not filter_candidates_for_focus(candidates, focus):
            return None

        prior = getattr(self, "_active_assignment_request", None)
        self._active_assignment_request = request
        try:
            result = super()._maybe_offer_player_mission(
                decision=decision,
                at=at,
                command=command,
                scheduler=scheduler,
                world_events=world_events,
                record_writes=record_writes,
                faction_record=faction_record,
            )
        finally:
            if prior is None:
                try:
                    del self._active_assignment_request
                except AttributeError:
                    pass
            else:
                self._active_assignment_request = prior

        if not isinstance(result, Mapping) or result.get("state") != "offered":
            return result
        offer_ref = result.get("mission_id")
        team_ref = result.get("team_ref")
        objective_kind = result.get("objective_kind")
        if (
            not isinstance(offer_ref, str)
            or team_ref != request.get("team_ref")
            or result.get("mission_rank") not in ranks
            or not isinstance(objective_kind, str)
            or not filter_candidates_for_focus((("generated", objective_kind),), focus)
        ):
            raise CommandRejectedError("mission_assignment_request_offer_mismatch")

        request_ref = request.get("request_ref")
        if not isinstance(request_ref, str):
            raise CommandRejectedError("mission_assignment_request_registry_invalid")
        fulfill_assignment_request(
            self.repository,
            record_writes,
            request_ref=request_ref,
            offer_ref=offer_ref,
            fulfilled_at=str(scheduler.world_time),
        )
        event_id = self._append_internal_event(
            world_events,
            command=command,
            identity=f"{request_ref}:{offer_ref}:fulfilled",
            kind="mission_assignment_request_fulfilled",
            at=scheduler.world_time,
            host_refs=(MISSION_ASSIGNMENT_OFFICE_REF, request.get("team_ref"), offer_ref),
            actor_refs=(command.actor_id,),
            affected_owner_refs=(MISSION_ASSIGNMENT_REQUEST_PATH,),
            material_consequence_refs=(request_ref, offer_ref),
            classification="restricted",
            audience_refs=(command.actor_id,),
            source_refs=(MISSION_ASSIGNMENT_OFFICE_REF,),
            reducer_ref="shinobi_runtime.commands.campaign_mission_assignment.assignment_request_fulfillment",
        )
        return {
            **dict(result),
            "assignment_request_ref": request_ref,
            "assignment_request_event_id": event_id,
        }


__all__ = ["CampaignCommandPlanner"]
