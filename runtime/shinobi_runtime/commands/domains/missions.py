"""Extracted semantic command domain from the repository command planner.

The mixin owns domain reducers; orchestration, transaction framing, shared owner
resolution, and causal scheduler settlement remain on RepositoryCommandPlanner.
"""

from __future__ import annotations

import copy
from typing import (
    Any,
    Dict,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.mission_owner import MissionOwner, mission_owner_path
from shinobi_runtime.commands.core import (
    _BuiltPlan,
    _OwnerResolutionCache,
    _declared_payload,
    _exact_payload,
    _json_bytes,
    _stable_id,
)
from shinobi_runtime.commands.constants import (
    MISSION_TRANSITION_TARGETS as _MISSION_TRANSITION_TARGETS,
    OBJECTIVE_TARGETS as _OBJECTIVE_TARGETS,
    TERMINAL_MISSION_STATES as _TERMINAL_MISSION_STATES,
    TERMINAL_WORLD_EVENT_STATES as _TERMINAL_WORLD_EVENT_STATES,
    OBJECTIVE_EVIDENCE_EVENT_KINDS as _OBJECTIVE_EVIDENCE_EVENT_KINDS,
)
from shinobi_runtime.commands.paths import (
    WORLD_EVENT_REGISTRY_PATH as _WORLD_EVENT_REGISTRY_PATH,
    INVENTORY_REGISTRY_PATH as _INVENTORY_REGISTRY_PATH,
)
from shinobi_runtime.reducers import (
    Mission,
    MissionObjective,
    SettlementTerm,
)
from shinobi_runtime.reducers.missions import (
    MissionTransitionError,
    ObjectiveDependencyError,
    SettlementConflictError,
    derive_mission_outcome,
    settle_mission,
    transition_mission,
    update_objective,
)
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.sim.scheduler import (
    CausalSchedulerRegistry,
    SchedulerHost,
    one_shot_event,
)
from shinobi_runtime.sim.hosts import HostState
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest


class MissionCommandsMixin:
    def _mission_scene(
        self,
        *,
        current_time: CampaignTime,
        owner: MissionOwner,
        summary: str,
    ) -> Dict[str, Any]:
        scene = copy.deepcopy(self._scene_base(current_time))
        loaded = scene.get("loaded_owner_ids")
        if not isinstance(loaded, list) or any(
            not isinstance(item, str)
            or not item
            or len(item.encode("utf-8")) > 256
            for item in loaded
        ):
            raise CommandRejectedError("campaign_scene_invalid")
        # Mission references are an immediate causal working set, not an
        # append-only history.  Preserve every non-mission causal owner, replace
        # historical mission references with the mission being changed now, and
        # never discard non-mission context merely to fit the scene budget.
        non_mission_refs = {
            item for item in loaded if not item.startswith("mission.")
        }
        rebuilt_loaded = sorted(non_mission_refs | {owner.mission_id})
        if len(rebuilt_loaded) > self.MAX_SCENE_LOADED_OWNER_IDS:
            raise CommandRejectedError("campaign_scene_context_budget_exceeded")
        scene["loaded_owner_ids"] = rebuilt_loaded
        boundaries = scene.get("known_clock_boundaries", [])
        if not isinstance(boundaries, list):
            raise CommandRejectedError("campaign_scene_invalid")
        event_id = owner.mission_id + ".next_due"
        retained = [
            item
            for item in boundaries
            if isinstance(item, dict) and item.get("event_id") != event_id
        ]
        if owner.next_due_at is not None and owner.mission.state not in _TERMINAL_MISSION_STATES:
            retained.append(
                {
                    "due_at": str(owner.next_due_at),
                    "event_id": event_id,
                    "visibility": "player_known",
                }
            )
        retained.sort(key=lambda item: (item.get("due_at", ""), item.get("event_id", "")))
        scene["known_clock_boundaries"] = retained
        scene["scene_summary"] = summary
        scene["decision_required"] = (
            summary + " No other consequential player choice is resolved by this command."
        )
        return scene
    def _mission_creation(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _declared_payload(command.payload, command.command_type)
        mission_id = _stable_id(
            command.payload["mission_id"], "mission_id_invalid", prefix="mission."
        )
        path = mission_owner_path(mission_id)
        if self.repository.read_optional_bytes(path) is not None:
            raise CommandRejectedError("mission_owner_already_exists")
        issuer_ref = _stable_id(command.payload["issuer_ref"], "mission_issuer_invalid")
        if issuer_ref != command.actor_id:
            raise CommandRejectedError("mission_issuer_actor_mismatch")
        authority_ref = _stable_id(
            command.payload["authority_ref"], "mission_authority_invalid"
        )
        mission_rank = command.payload["mission_rank"]
        economy_mechanics = self._economy_mechanics()
        mission_rank_rules = economy_mechanics.get("mission_ranks")
        if not isinstance(mission_rank, str) or not isinstance(mission_rank_rules, Mapping) or mission_rank not in mission_rank_rules:
            raise CommandRejectedError("mission_rank_invalid")
        rank_rule = mission_rank_rules[mission_rank]
        if not isinstance(rank_rule, Mapping):
            raise CommandRejectedError("economy_mechanics_invalid")
        participants_raw = command.payload["participant_refs"]
        if (
            not isinstance(participants_raw, Sequence) or isinstance(participants_raw, (str, bytes, bytearray))
            or not participants_raw
            or len(participants_raw) > 16
        ):
            raise CommandRejectedError("mission_participants_invalid")
        participant_refs = tuple(
            _stable_id(item, "mission_participants_invalid") for item in participants_raw
        )
        if len(participant_refs) != len(set(participant_refs)):
            raise CommandRejectedError("mission_participants_invalid")
        owner_cache = _OwnerResolutionCache()
        try:
            self._resolve_covered_owner(issuer_ref, cache=owner_cache)
            self._resolve_covered_owner(authority_ref, cache=owner_cache)
            for ref in participant_refs:
                self._resolve_covered_owner(ref, cache=owner_cache)
        except CommandRejectedError as exc:
            raise CommandRejectedError("mission_authority_unresolved") from exc
        authority_decision = self._domain_authority(cache=owner_cache).mission_tasking(
            issuer_ref=issuer_ref,
            authority_ref=authority_ref,
            participant_refs=participant_refs,
            candidate_team_refs=self._active_exact_team_refs(),
        )
        if not authority_decision.allowed:
            raise CommandRejectedError("mission_tasking_not_authorized")

        objectives_raw = command.payload["objectives"]
        if not isinstance(objectives_raw, Sequence) or isinstance(objectives_raw, (str, bytes, bytearray)) or not 1 <= len(objectives_raw) <= 24:
            raise CommandRejectedError("mission_objectives_invalid")
        objectives = []
        for raw in objectives_raw:
            if not isinstance(raw, Mapping) or frozenset(raw) != frozenset(
                ("objective_id", "kind", "required", "dependencies")
            ):
                raise CommandRejectedError("mission_objectives_invalid")
            dependencies = raw.get("dependencies")
            if not isinstance(dependencies, Sequence) or isinstance(dependencies, (str, bytes, bytearray)):
                raise CommandRejectedError("mission_objectives_invalid")
            try:
                objectives.append(
                    MissionObjective(
                        objective_id=raw.get("objective_id"),
                        kind=raw.get("kind"),
                        required=raw.get("required"),
                        dependencies=tuple(dependencies),
                    )
                )
            except (TypeError, ValueError) as exc:
                raise CommandRejectedError("mission_objectives_invalid") from exc

        terms_raw = command.payload["settlement_terms"]
        if not isinstance(terms_raw, Sequence) or isinstance(terms_raw, (str, bytes, bytearray)) or len(terms_raw) > 32:
            raise CommandRejectedError("mission_settlement_terms_invalid")
        terms = []
        for raw in terms_raw:
            if not isinstance(raw, Mapping) or frozenset(raw) != SettlementTerm.RECORD_FIELDS:
                raise CommandRejectedError("mission_settlement_terms_invalid")
            applies_on = raw.get("applies_on")
            if not isinstance(applies_on, Sequence) or isinstance(applies_on, (str, bytes, bytearray)):
                raise CommandRejectedError("mission_settlement_terms_invalid")
            try:
                terms.append(
                    SettlementTerm(
                        term_id=raw.get("term_id"),
                        direction=raw.get("direction"),
                        account_ref=raw.get("account_ref"),
                        asset_ref=raw.get("asset_ref"),
                        quantity=raw.get("quantity"),
                        applies_on=tuple(applies_on),
                        objective_id=raw.get("objective_id"),
                        objective_status=raw.get("objective_status"),
                    )
                )
            except (TypeError, ValueError) as exc:
                raise CommandRejectedError("mission_settlement_terms_invalid") from exc

        # Mission cash compensation is funded when the mission is created, not
        # conjured when it succeeds. Escrow all possible currency reward terms;
        # unused alternatives return to the funding account at settlement.
        currency_reward_terms = [
            term for term in terms
            if term.direction == "reward" and term.asset_ref == "currency.ryo"
        ]
        escrow_total_ryo = sum(term.quantity for term in currency_reward_terms)
        bonus_max = rank_rule.get("participant_bonus_max_ryo")
        if isinstance(bonus_max, bool) or not isinstance(bonus_max, int) or bonus_max < 0:
            raise CommandRejectedError("economy_mechanics_invalid")
        if escrow_total_ryo > bonus_max * len(participant_refs):
            raise CommandRejectedError("mission_reward_exceeds_rank_band")
        _economy_world_record, economy_finance = self._economy_world()
        funding_holder_ref = self._funding_holder_for(issuer_ref, finance=economy_finance)
        escrow_holder_ref = None
        mission_inventory: Optional[Dict[str, Any]] = None
        if escrow_total_ryo:
            try:
                mission_inventory = copy.deepcopy(self.repository.read_json(_INVENTORY_REGISTRY_PATH))
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("mission_reward_funding_invalid") from exc
            holders = mission_inventory.get("holders") if isinstance(mission_inventory, dict) else None
            funding = holders.get(funding_holder_ref) if isinstance(holders, dict) else None
            if not isinstance(holders, dict) or not isinstance(funding, dict):
                raise CommandRejectedError("mission_reward_funding_invalid")
            balance = funding.get("currency.ryo", 0)
            if isinstance(balance, bool) or not isinstance(balance, int) or balance < escrow_total_ryo:
                raise CommandRejectedError("mission_reward_funding_insufficient")
            escrow_holder_ref = "escrow." + mission_id
            if escrow_holder_ref in holders and holders[escrow_holder_ref]:
                raise CommandRejectedError("mission_reward_escrow_conflict")
            escrow = holders.setdefault(escrow_holder_ref, {})
            if not isinstance(escrow, dict):
                raise CommandRejectedError("mission_reward_escrow_conflict")
            funding["currency.ryo"] = balance - escrow_total_ryo
            escrow["currency.ryo"] = escrow_total_ryo

        def optional_future(value: object, code: str) -> Optional[CampaignTime]:
            if value is None:
                return None
            try:
                parsed = CampaignTime.parse(value)
            except (TypeError, ValueError) as exc:
                raise CommandRejectedError(code) from exc
            if parsed <= current_time:
                raise CommandRejectedError(code)
            return parsed

        deadline = optional_future(command.payload.get("deadline_at"), "mission_deadline_invalid")
        next_due_at = optional_future(command.payload.get("next_due_at"), "mission_next_due_invalid")
        operation_ref = command.payload.get("operation_ref")
        if operation_ref is not None:
            operation_ref = _stable_id(operation_ref, "mission_operation_ref_invalid")
        if next_due_at is None and operation_ref is None:
            raise CommandRejectedError("mission_next_boundary_required")
        try:
            mission = Mission(
                mission_id=mission_id,
                state="offered",
                participant_refs=participant_refs,
                objectives=tuple(objectives),
                settlement_terms=tuple(terms),
            )
            owner = MissionOwner(
                mission=mission,
                issuer_ref=issuer_ref,
                authority_ref=authority_ref,
                mission_rank=mission_rank,
                funding_holder_ref=funding_holder_ref,
                escrow_holder_ref=escrow_holder_ref,
                opened_at=current_time,
                authorized_at=current_time,
                starts_at=None,
                deadline_at=deadline,
                next_due_at=next_due_at,
                operation_ref=operation_ref,
                closed_at=None,
            )
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("mission_creation_invalid") from exc

        scene = self._mission_scene(
            current_time=current_time,
            owner=owner,
            summary=f"Mission {mission_id} is lawfully offered at {current_time}; no acceptance is implied.",
        )
        scheduler = self._load_scheduler(
            current_time=current_time, scene=self._scene_base(current_time)
        )
        host_id = "host." + mission_id
        if host_id in scheduler.hosts:
            raise CommandRejectedError("mission_scheduler_conflict")
        due_candidates = [value for value in (next_due_at, deadline) if value is not None]
        if due_candidates:
            due = min(due_candidates)
            scheduler.add_host(
                SchedulerHost(
                    state=HostState(
                        host_id=host_id,
                        kind="mission",
                        resolved_through=current_time,
                        safe_through=due.add_seconds(-1),
                        handler_ref="causal.scheduler",
                        rng_namespace=mission_id,
                        next_due=due,
                    ),
                    authority_kind="mission",
                    owner_ref=path,
                    metadata={"mission_id": mission_id},
                )
            )
            scheduler.upsert_event(
                one_shot_event(
                    kind="mission.boundary",
                    identity=mission_id,
                    source_host=host_id,
                    target_host=host_id,
                    due_at=due,
                    payload={"mission_id": mission_id, "owner_ref": path},
                    priority=20,
                    visibility="player_known",
                    requires_player=True,
                )
            )
        scheduler.metrics.update(
            {
                "host_count": len(scheduler.hosts),
                "pending_event_count": len(scheduler.queue),
            }
        )
        world_events = self._world_events()
        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind="mission_created",
            at=current_time,
            host_refs=(issuer_ref, authority_ref),
            actor_refs=participant_refs,
            causal_refs=(mission_id,),
            affected_owner_refs=tuple(
                ref for ref in (path, self.scheduler_path, _INVENTORY_REGISTRY_PATH if mission_inventory is not None else None)
                if isinstance(ref, str)
            ),
            material_consequence_refs=tuple(
                [mission_id] + ([f"reward_escrow:{escrow_total_ryo}ryo:{funding_holder_ref}->{escrow_holder_ref}"] if escrow_total_ryo else [])
            ),
            audience_refs=(command.actor_id,),
            reducer_ref="shinobi_runtime.reducers.missions.Mission",
        )
        writes = {
            self.meta_path: _json_bytes(
                self._meta_after(meta, command, world_time=current_time)
            ),
            self.scene_path: _json_bytes(scene),
            self.scheduler_path: _json_bytes(scheduler.to_record()),
            path: _json_bytes(owner.to_record()),
            **self._world_event_writes(world_events),
        }
        if mission_inventory is not None:
            writes[_INVENTORY_REGISTRY_PATH] = _json_bytes(mission_inventory)
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("mission creation write set changed after planning")
            self._assert_meta(
                overlay,
                manifest,
                meta_path=self.meta_path,
                command=command,
                world_time=current_time,
            )
            staged_owner = MissionOwner.from_record(overlay.read_json(path))
            if staged_owner.mission_rank != mission_rank or staged_owner.funding_holder_ref != funding_holder_ref or staged_owner.escrow_holder_ref != escrow_holder_ref:
                raise ValueError("mission financial routing did not persist")
            CausalSchedulerRegistry.from_record(overlay.read_json(self.scheduler_path))
            if mission_inventory is not None:
                staged_inventory = overlay.read_json(_INVENTORY_REGISTRY_PATH)
                staged_holders = staged_inventory.get("holders", {})
                if staged_holders.get(escrow_holder_ref, {}).get("currency.ryo") != escrow_total_ryo:
                    raise ValueError("mission reward escrow did not persist")
            events = overlay.read_json(_WORLD_EVENT_REGISTRY_PATH).get("events", [])
            if not any(item.get("id") == event_id for item in events if isinstance(item, dict)):
                raise ValueError("mission creation lacks semantic history")

        return _BuiltPlan(
            code="mission_creation_ready",
            affected_refs=expected_paths,
            writes=writes,
            result={
                "command_type": command.command_type,
                "mission_id": mission_id,
                "mission_rank": mission_rank,
                "state": "offered",
                "funding_holder_ref": funding_holder_ref,
                "escrow_holder_ref": escrow_holder_ref,
                "escrowed_reward_ryo": escrow_total_ryo,
                "next_due_at": None if next_due_at is None else str(next_due_at),
                "deadline_at": None if deadline is None else str(deadline),
                "semantic_event_id": event_id,
            },
            validator=validate,
        )
    def _read_mission(
        self,
        mission_id: object,
        *,
        actor_id: str,
        current_time: CampaignTime,
    ) -> Tuple[str, MissionOwner]:
        try:
            path = mission_owner_path(mission_id)
        except ValueError as exc:
            raise CommandRejectedError("mission_id_invalid") from exc
        try:
            owner = MissionOwner.from_record(self.repository.read_json(path))
        except FileNotFoundError as exc:
            raise CommandRejectedError("mission_owner_not_found") from exc
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("mission_owner_invalid") from exc
        if owner.mission_id != mission_id:
            raise CommandRejectedError("mission_owner_identity_mismatch")
        try:
            owner_cache = _OwnerResolutionCache()
            self._resolve_covered_owner(owner.issuer_ref, cache=owner_cache)
            self._resolve_covered_owner(owner.authority_ref, cache=owner_cache)
        except CommandRejectedError as exc:
            raise CommandRejectedError("mission_authority_unresolved") from exc
        if actor_id not in owner.mission.participant_refs:
            raise CommandRejectedError("actor_not_mission_participant")
        if owner.next_due_at is not None and owner.next_due_at <= current_time:
            raise CommandRejectedError("time_boundary_requires_mission_settlement")
        if owner.deadline_at is not None and owner.deadline_at <= current_time:
            raise CommandRejectedError("time_boundary_requires_mission_settlement")
        return path, owner
    @staticmethod
    def _objective_evidence_token(
        mission_id: str,
        objective_id: str,
        target_status: str,
        progress_milli: int,
    ) -> str:
        return (
            f"mission-objective:{mission_id}:{objective_id}:"
            f"{target_status}:{progress_milli}"
        )
    def _mission_objective_evidence(
        self,
        *,
        owner: MissionOwner,
        objective_id: str,
        target_status: str,
        progress_milli: int,
        evidence_event_id: str,
        current_time: CampaignTime,
    ) -> Tuple[str, str]:
        """Validate one prior semantic event as objective evidence.

        Gameplay reducers create material events first. Mission state then cites
        that event. The mission command never fabricates the underlying result.
        """
        try:
            registry = self._world_events()
            event, registry_digest = self._world_event_record_and_digest(
                evidence_event_id, registry=registry
            )
        except CommandRejectedError as exc:
            raise CommandRejectedError("mission_objective_evidence_unavailable") from exc
        if (
            registry.get("owner_id") != "registry.world_events"
            or registry.get("owner_type") != "world_event_registry"
            or event is None
            or registry_digest is None
        ):
            raise CommandRejectedError("mission_objective_evidence_required")
        if event.get("status") not in _TERMINAL_WORLD_EVENT_STATES:
            raise CommandRejectedError("mission_objective_evidence_not_terminal")
        causal_refs = event.get("causal_refs")
        if not isinstance(causal_refs, list) or owner.mission_id not in causal_refs:
            raise CommandRejectedError("mission_objective_evidence_wrong_mission")
        objective = owner.mission.objective_by_id.get(objective_id)
        if objective is None:
            raise CommandRejectedError("mission_objective_update_invalid")
        allowed_kinds = _OBJECTIVE_EVIDENCE_EVENT_KINDS.get(objective.kind, frozenset())
        if event.get("kind") not in allowed_kinds:
            raise CommandRejectedError("mission_objective_evidence_kind_invalid")
        timing = event.get("timing")
        execution = event.get("execution")
        if not isinstance(timing, Mapping) or not isinstance(execution, Mapping):
            raise CommandRejectedError("mission_objective_evidence_invalid")
        raw_evidence_time = timing.get("ended_at") or timing.get("occurred_at")
        try:
            evidence_time = CampaignTime.parse(raw_evidence_time)
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("mission_objective_evidence_invalid") from exc
        receipt_refs = execution.get("receipt_refs")
        if (
            evidence_time < owner.opened_at
            or evidence_time > current_time
            or not isinstance(execution.get("transaction_ref"), str)
            or not execution.get("transaction_ref")
            or not isinstance(receipt_refs, list)
            or not receipt_refs
            or any(not isinstance(value, str) or not value for value in receipt_refs)
        ):
            raise CommandRejectedError("mission_objective_evidence_invalid")
        # Terminal success/failure must be supported by material consequences
        # rather than by the caller's requested status alone for combat goals.
        consequences = event.get("material_consequence_refs")
        if not isinstance(consequences, list) or any(not isinstance(v, str) for v in consequences):
            raise CommandRejectedError("mission_objective_evidence_invalid")
        if objective.kind in ("defeat", "capture", "restrain"):
            success_prefixes = {
                "defeat": ("death:", "incapacitated:", "captured:", "combat:"),
                "capture": ("captured:", "combat:"),
                "restrain": ("captured:", "incapacitated:", "combat:"),
            }[objective.kind]
            failure_prefixes = ("escaped:",)
            if target_status == "succeeded" and not any(v.startswith(success_prefixes) for v in consequences):
                raise CommandRejectedError("mission_objective_evidence_result_invalid")
            if target_status == "failed" and not any(v.startswith(failure_prefixes) for v in consequences):
                raise CommandRejectedError("mission_objective_evidence_result_invalid")
        return evidence_event_id, registry_digest
    def _sync_mission_scheduler(
        self,
        scheduler: CausalSchedulerRegistry,
        *,
        owner: MissionOwner,
        path: str,
        current_time: CampaignTime,
    ) -> None:
        """Synchronize the scheduler mirror for one authoritative mission owner."""

        host_id = "host." + owner.mission_id
        scheduler.queue.replace(
            event
            for event in scheduler.queue.snapshot()
            if event.target_host != host_id
        )
        scheduler.hosts.pop(host_id, None)
        if owner.mission.state in _TERMINAL_MISSION_STATES:
            scheduler.metrics.update(
                {
                    "host_count": len(scheduler.hosts),
                    "pending_event_count": len(scheduler.queue),
                }
            )
            return
        due_candidates = [
            value
            for value in (owner.next_due_at, owner.deadline_at)
            if value is not None and value > current_time
        ]
        if due_candidates:
            due = min(due_candidates)
            scheduler.add_host(
                SchedulerHost(
                    state=HostState(
                        host_id=host_id,
                        kind="mission",
                        resolved_through=current_time,
                        safe_through=due.add_seconds(-1),
                        handler_ref="causal.scheduler",
                        rng_namespace=owner.mission_id,
                        next_due=None,
                    ),
                    authority_kind="mission",
                    owner_ref=path,
                    metadata={"mission_id": owner.mission_id},
                )
            )
            scheduler.upsert_event(
                one_shot_event(
                    kind="mission.boundary",
                    identity=owner.mission_id,
                    source_host=host_id,
                    target_host=host_id,
                    due_at=due,
                    payload={"mission_id": owner.mission_id, "owner_ref": path},
                    priority=20,
                    visibility="player_known",
                    requires_player=True,
                )
            )
        scheduler.metrics.update(
            {
                "host_count": len(scheduler.hosts),
                "pending_event_count": len(scheduler.queue),
            }
        )
    def _mission_settlement_inventory(
        self, owner: MissionOwner
    ) -> Tuple[Optional[Dict[str, Any]], Tuple[str, ...]]:
        """Apply selected mission settlement terms through conserved holders.

        Currency rewards come from the mission escrow funded at creation. Currency
        costs return to the mission funding account. Any unused reward escrow is
        returned when the mission closes. Non-currency terms retain the issuer as
        counterparty because ordinary institutional stock has a separate authority.
        """

        settlement = owner.mission.settlement
        if settlement is None:
            return None, ()
        selected_ids = set(settlement.reward_term_ids) | set(settlement.cost_term_ids)
        has_currency_escrow = isinstance(owner.escrow_holder_ref, str) and bool(owner.escrow_holder_ref)
        if not selected_ids and not has_currency_escrow:
            return None, ()
        try:
            inventory = copy.deepcopy(self.repository.read_json(_INVENTORY_REGISTRY_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("mission_settlement_inventory_invalid") from exc
        holders = inventory.get("holders") if isinstance(inventory, dict) else None
        if not isinstance(holders, dict):
            raise CommandRejectedError("mission_settlement_inventory_invalid")
        term_by_id = {term.term_id: term for term in owner.mission.settlement_terms}
        consequences: list[str] = []
        for term_id in sorted(selected_ids):
            term = term_by_id.get(term_id)
            if term is None:
                raise CommandRejectedError("mission_settlement_term_invalid")
            if term.asset_ref == "currency.ryo":
                if term.direction == "reward":
                    if not has_currency_escrow:
                        raise CommandRejectedError("mission_settlement_escrow_missing")
                    source_ref, destination_ref = owner.escrow_holder_ref, term.account_ref
                else:
                    source_ref, destination_ref = term.account_ref, owner.funding_holder_ref
            elif term.direction == "reward":
                source_ref, destination_ref = owner.issuer_ref, term.account_ref
            else:
                source_ref, destination_ref = term.account_ref, owner.issuer_ref
            if source_ref == destination_ref:
                raise CommandRejectedError("mission_settlement_self_transfer")
            source = holders.get(source_ref)
            if not isinstance(source, dict):
                raise CommandRejectedError("mission_settlement_source_unfunded")
            destination = holders.setdefault(destination_ref, {})
            if not isinstance(destination, dict):
                raise CommandRejectedError("mission_settlement_destination_invalid")
            available = source.get(term.asset_ref, 0)
            if isinstance(available, bool) or not isinstance(available, int) or available < term.quantity:
                raise CommandRejectedError("mission_settlement_asset_insufficient")
            existing = destination.get(term.asset_ref, 0)
            if isinstance(existing, bool) or not isinstance(existing, int) or existing < 0:
                raise CommandRejectedError("mission_settlement_destination_invalid")
            source[term.asset_ref] = available - term.quantity
            if source[term.asset_ref] == 0:
                source.pop(term.asset_ref)
            destination[term.asset_ref] = existing + term.quantity
            consequences.append(
                f"mission_settlement:{term.direction}:{term.asset_ref}:{term.quantity}:{source_ref}->{destination_ref}"
            )

        # Mission creation conservatively escrowed every possible currency reward
        # branch. Return anything not selected by the actual terminal outcome.
        if has_currency_escrow:
            escrow = holders.get(owner.escrow_holder_ref)
            funding = holders.setdefault(owner.funding_holder_ref, {})
            if not isinstance(escrow, dict) or not isinstance(funding, dict):
                raise CommandRejectedError("mission_settlement_escrow_invalid")
            remainder = escrow.get("currency.ryo", 0)
            funding_balance = funding.get("currency.ryo", 0)
            if (
                isinstance(remainder, bool) or not isinstance(remainder, int) or remainder < 0
                or isinstance(funding_balance, bool) or not isinstance(funding_balance, int) or funding_balance < 0
            ):
                raise CommandRejectedError("mission_settlement_escrow_invalid")
            if remainder:
                funding["currency.ryo"] = funding_balance + remainder
                consequences.append(
                    f"mission_escrow_refund:currency.ryo:{remainder}:{owner.escrow_holder_ref}->{owner.funding_holder_ref}"
                )
            escrow.pop("currency.ryo", None)
            if not escrow:
                holders.pop(owner.escrow_holder_ref, None)
        return inventory, tuple(consequences)
    def _mission_built_plan(
        self,
        *,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
        path: str,
        owner: MissionOwner,
        code: str,
        summary: str,
        result: Mapping[str, Any],
        guarded_read_digests: Optional[Mapping[str, str]] = None,
        extra_writes: Optional[Mapping[str, bytes]] = None,
        extra_material_consequence_refs: Sequence[str] = (),
    ) -> _BuiltPlan:
        scene = self._mission_scene(
            current_time=current_time,
            owner=owner,
            summary=summary,
        )
        scheduler = self._load_scheduler(
            current_time=current_time, scene=self._scene_base(current_time)
        )
        self._sync_mission_scheduler(
            scheduler, owner=owner, path=path, current_time=current_time
        )
        world_events = self._world_events()
        if command.command_type == "mission_transition":
            event_kind = "mission_state_changed"
            material = (f"mission:{owner.mission_id}:state:{owner.mission.state}",)
        elif command.command_type == "mission_objective_update":
            event_kind = "mission_objective_resolved"
            objective_id = result.get("objective_id")
            objective_status = result.get("status")
            material = (f"mission:{owner.mission_id}:objective:{objective_id}:{objective_status}",)
        else:
            event_kind = "mission_settled"
            material = (f"mission:{owner.mission_id}:state:{owner.mission.state}",)
        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind=event_kind,
            at=current_time,
            host_refs=(owner.issuer_ref, owner.authority_ref),
            actor_refs=owner.mission.participant_refs,
            causal_refs=tuple(x for x in (owner.mission_id, owner.operation_ref) if isinstance(x, str)),
            affected_owner_refs=(path, self.scheduler_path, *tuple(sorted((extra_writes or {}).keys()))),
            material_consequence_refs=tuple(material) + tuple(extra_material_consequence_refs),
            audience_refs=(command.actor_id,),
            reducer_ref="shinobi_runtime.reducers.missions",
        )
        scheduler_bytes = _json_bytes(scheduler.to_record())
        writes = {
            self.meta_path: _json_bytes(
                self._meta_after(meta, command, world_time=current_time)
            ),
            self.scene_path: _json_bytes(scene),
            path: _json_bytes(owner.to_record()),
            **self._world_event_writes(world_events),
        }
        for extra_path, extra_bytes in sorted((extra_writes or {}).items()):
            if extra_path in writes and writes[extra_path] != extra_bytes:
                raise CommandRejectedError("mission_extra_write_conflict")
            writes[extra_path] = extra_bytes
        if self.repository.read_optional_bytes(self.scheduler_path) != scheduler_bytes:
            writes[self.scheduler_path] = scheduler_bytes
        expected_record = owner.to_record()
        expected_paths = tuple(sorted(writes))
        expected_read_digests = dict(guarded_read_digests or {})

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("mission command write set changed after planning")
            for guarded_path, guarded_digest in expected_read_digests.items():
                if self.repository.digest(guarded_path) != guarded_digest:
                    raise ValueError("mission causal evidence changed after planning")
            self._assert_meta(
                overlay,
                manifest,
                meta_path=self.meta_path,
                command=command,
                world_time=current_time,
            )
            resolved = MissionOwner.from_record(overlay.read_json(path))
            if resolved.to_record() != expected_record:
                raise ValueError("mission after-image differs from reducer result")
            for extra_path, extra_bytes in sorted((extra_writes or {}).items()):
                if overlay.read_bytes(extra_path) != extra_bytes:
                    raise ValueError("mission settlement after-image mismatch")
            CausalSchedulerRegistry.from_record(overlay.read_json(self.scheduler_path))
            staged_events = overlay.read_json(_WORLD_EVENT_REGISTRY_PATH).get("events", [])
            if not any(
                isinstance(item, Mapping) and item.get("id") == event_id
                for item in staged_events
            ):
                raise ValueError("mission command lacks semantic history")
            staged_scene = overlay.read_json(self.scene_path)
            staged_loaded = (
                staged_scene.get("loaded_owner_ids", [])
                if isinstance(staged_scene, Mapping)
                else []
            )
            staged_mission_refs = (
                [
                    item
                    for item in staged_loaded
                    if isinstance(item, str) and item.startswith("mission.")
                ]
                if isinstance(staged_loaded, list)
                else []
            )
            if (
                not isinstance(staged_scene, dict)
                or staged_scene.get("schema") != "scene"
                or staged_scene.get("world_time") != str(current_time)
                or not isinstance(staged_loaded, list)
                or len(staged_loaded) > self.MAX_SCENE_LOADED_OWNER_IDS
                or staged_mission_refs != [resolved.mission_id]
            ):
                raise ValueError("mission scene after-image is incoherent")

        enriched_result = dict(result)
        enriched_result["semantic_event_id"] = event_id
        return _BuiltPlan(
            code=code,
            affected_refs=expected_paths,
            writes=writes,
            result=enriched_result,
            validator=validate,
        )
    def _mission_transition(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _exact_payload(
            command.payload,
            ("mission_id", "target_state"),
            command.command_type,
        )
        mission_id = _stable_id(
            command.payload["mission_id"],
            "mission_id_invalid",
            prefix="mission.",
        )
        target_state = command.payload["target_state"]
        if target_state not in _MISSION_TRANSITION_TARGETS:
            raise CommandRejectedError("mission_transition_target_invalid")
        path, owner = self._read_mission(
            mission_id,
            actor_id=command.actor_id,
            current_time=current_time,
        )
        if target_state == owner.mission.state:
            raise CommandRejectedError("command_no_op")
        if target_state == "resolving" and any(
            objective.required and objective.status not in ("succeeded", "failed")
            for objective in owner.mission.objectives
        ):
            raise CommandRejectedError("mission_outcome_unresolved")
        if target_state == "aborted" and command.actor_id not in (
            owner.issuer_ref,
            owner.authority_ref,
        ):
            raise CommandRejectedError("actor_not_mission_authority")
        try:
            transitioned = transition_mission(
                owner.mission,
                target_state,
                reason_ref=(
                    "command." + command.digest if target_state == "aborted" else None
                ),
            )
            if target_state == "aborted":
                transitioned = settle_mission(
                    transitioned,
                    "settle." + command.digest,
                ).mission
            updated = owner.with_mission(transitioned, effective_at=current_time)
        except (MissionTransitionError, SettlementConflictError, TypeError, ValueError) as exc:
            raise CommandRejectedError("mission_transition_invalid") from exc
        settlement_inventory: Optional[Dict[str, Any]] = None
        settlement_consequences: Tuple[str, ...] = ()
        if target_state == "aborted" and updated.mission.settlement is not None:
            settlement_inventory, settlement_consequences = self._mission_settlement_inventory(updated)
        summary = (
            f"Mission {mission_id} is now {updated.mission.state} at {current_time}."
        )
        return self._mission_built_plan(
            command=command,
            meta=meta,
            current_time=current_time,
            path=path,
            owner=updated,
            code="mission_transition_ready",
            summary=summary,
            result={
                "command_type": command.command_type,
                "mission_id": mission_id,
                "state": updated.mission.state,
                "settlement_transfers": list(settlement_consequences),
            },
            extra_writes=(
                {_INVENTORY_REGISTRY_PATH: _json_bytes(settlement_inventory)}
                if settlement_inventory is not None else None
            ),
            extra_material_consequence_refs=settlement_consequences,
        )
    def _mission_objective_update(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _exact_payload(
            command.payload,
            ("mission_id", "objective_id", "target_status", "progress_milli", "evidence_event_id"),
            command.command_type,
        )
        mission_id = _stable_id(
            command.payload["mission_id"],
            "mission_id_invalid",
            prefix="mission.",
        )
        objective_id = _stable_id(
            command.payload["objective_id"],
            "mission_objective_id_invalid",
        )
        target_status = command.payload["target_status"]
        progress = command.payload["progress_milli"]
        if target_status not in _OBJECTIVE_TARGETS:
            raise CommandRejectedError("mission_objective_status_invalid")
        if (
            isinstance(progress, bool)
            or not isinstance(progress, int)
            or not 0 <= progress <= 1000
            or (target_status == "in_progress" and not 0 <= progress < 1000)
            or (target_status == "succeeded" and progress != 1000)
            or (target_status == "failed" and progress == 1000)
        ):
            raise CommandRejectedError("mission_objective_progress_invalid")
        if target_status == "in_progress":
            raise CommandRejectedError(
                "mission_objective_progress_evidence_unsupported"
            )
        path, owner = self._read_mission(
            mission_id,
            actor_id=command.actor_id,
            current_time=current_time,
        )
        evidence_event_id = _stable_id(
            command.payload["evidence_event_id"],
            "mission_objective_evidence_event_invalid",
            prefix="event.",
        )
        resolution_ref, evidence_digest = self._mission_objective_evidence(
            owner=owner,
            objective_id=objective_id,
            target_status=target_status,
            progress_milli=progress,
            evidence_event_id=evidence_event_id,
            current_time=current_time,
        )
        try:
            updated_mission = update_objective(
                owner.mission,
                objective_id,
                target_status,
                progress_milli=progress,
                resolution_ref=resolution_ref,
            )
        except (
            KeyError,
            MissionTransitionError,
            ObjectiveDependencyError,
            TypeError,
            ValueError,
        ) as exc:
            raise CommandRejectedError("mission_objective_update_invalid") from exc
        if updated_mission == owner.mission:
            raise CommandRejectedError("command_no_op")
        updated = owner.with_mission(updated_mission, effective_at=current_time)
        objective = updated.mission.objective_by_id[objective_id]
        summary = (
            f"Mission {mission_id} objective {objective_id} is {objective.status} "
            f"at {current_time}."
        )
        return self._mission_built_plan(
            command=command,
            meta=meta,
            current_time=current_time,
            path=path,
            owner=updated,
            code="mission_objective_update_ready",
            summary=summary,
            result={
                "command_type": command.command_type,
                "mission_id": mission_id,
                "objective_id": objective_id,
                "status": objective.status,
                "progress_milli": objective.progress_milli,
            },
            guarded_read_digests={
                _WORLD_EVENT_REGISTRY_PATH: evidence_digest,
            },
        )
    def _mission_derive_and_settle(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _exact_payload(command.payload, ("mission_id",), command.command_type)
        mission_id = _stable_id(
            command.payload["mission_id"],
            "mission_id_invalid",
            prefix="mission.",
        )
        path, owner = self._read_mission(
            mission_id,
            actor_id=command.actor_id,
            current_time=current_time,
        )
        if owner.mission.state != "resolving":
            raise CommandRejectedError("mission_not_resolving")
        try:
            derived = derive_mission_outcome(owner.mission)
            if derived.state == "resolving":
                raise CommandRejectedError("mission_outcome_unresolved")
            settlement_result = settle_mission(
                derived,
                "settle." + command.digest,
            )
            updated = owner.with_mission(
                settlement_result.mission,
                effective_at=current_time,
            )
        except CommandRejectedError:
            raise
        except (MissionTransitionError, SettlementConflictError, TypeError, ValueError) as exc:
            raise CommandRejectedError("mission_settlement_invalid") from exc
        settlement = updated.mission.settlement
        if settlement is None:
            raise CommandRejectedError("mission_settlement_invalid")
        settlement_inventory, settlement_consequences = self._mission_settlement_inventory(updated)
        summary = (
            f"Mission {mission_id} closes as {updated.mission.state} at {current_time}."
        )
        return self._mission_built_plan(
            command=command,
            meta=meta,
            current_time=current_time,
            path=path,
            owner=updated,
            code="mission_derive_and_settle_ready",
            summary=summary,
            result={
                "command_type": command.command_type,
                "mission_id": mission_id,
                "state": updated.mission.state,
                "settlement_token": settlement.settlement_token,
                "reward_term_ids": list(settlement.reward_term_ids),
                "cost_term_ids": list(settlement.cost_term_ids),
                "settlement_transfers": list(settlement_consequences),
            },
            extra_writes=(
                {_INVENTORY_REGISTRY_PATH: _json_bytes(settlement_inventory)}
                if settlement_inventory is not None else None
            ),
            extra_material_consequence_refs=settlement_consequences,
        )

