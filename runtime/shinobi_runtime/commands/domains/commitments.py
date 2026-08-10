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
    Sequence,
)

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.core import (
    _BuiltPlan,
    _OwnerResolutionCache,
    _declared_payload,
    _exact_payload,
    _json_bytes,
    _stable_id,
)
from shinobi_runtime.commands.paths import COMMITMENT_REGISTRY_PATH as _COMMITMENT_REGISTRY_PATH
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.sim.scheduler import (
    CausalSchedulerRegistry,
    SchedulerHost,
    one_shot_event,
)
from shinobi_runtime.sim.hosts import HostState
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest


class CommitmentForceCommandsMixin:
    MAX_TERMINAL_COMMITMENT_HISTORY = 256
    MAX_TERMINAL_ASSIGNMENT_HISTORY = 256

    @staticmethod
    def _trim_terminal_records(
        records: list[Any],
        *,
        terminal_statuses: set[str],
        limit: int,
        preserve_ids: Sequence[str] = (),
    ) -> None:
        """Bound resolved operational history while preserving all live state."""
        preserve = {value for value in preserve_ids if isinstance(value, str) and value}
        terminal_indices = [
            idx for idx, row in enumerate(records)
            if isinstance(row, Mapping) and row.get("status") in terminal_statuses
        ]
        keep_terminal = set(terminal_indices[-limit:])
        for idx in terminal_indices:
            row = records[idx]
            if isinstance(row, Mapping) and row.get("id") in preserve:
                keep_terminal.add(idx)
        records[:] = [
            row for idx, row in enumerate(records)
            if not (
                isinstance(row, Mapping)
                and row.get("status") in terminal_statuses
                and idx not in keep_terminal
            )
        ]

    def _commitment_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _declared_payload(command.payload, command.command_type)
        commitment_id = _stable_id(command.payload["commitment_id"], "commitment_id_invalid", prefix="commitment.")
        kind = command.payload["kind"]
        if kind not in ("promise", "order", "obligation"):
            raise CommandRejectedError("commitment_kind_invalid")
        subject_ref = _stable_id(command.payload["subject_ref"], "commitment_subject_invalid")
        if subject_ref != command.actor_id:
            raise CommandRejectedError("commitment_subject_actor_mismatch")
        target_raw = command.payload.get("target_ref")
        target_ref = None if target_raw is None else _stable_id(target_raw, "commitment_target_invalid")
        host_raw = command.payload.get("host_ref")
        host_ref = None if host_raw is None else _stable_id(host_raw, "commitment_host_invalid")
        due_raw = command.payload.get("due_at")
        due_at = None
        if due_raw is not None:
            try:
                due_at = CampaignTime.parse(due_raw)
            except (TypeError, ValueError) as exc:
                raise CommandRejectedError("commitment_due_invalid") from exc
            if due_at <= current_time:
                raise CommandRejectedError("commitment_due_invalid")
        summary = command.payload["summary"]
        if not isinstance(summary, str) or not summary or len(summary) > 1000:
            raise CommandRejectedError("commitment_summary_invalid")
        visibility = command.payload["visibility"]
        if visibility not in ("public", "restricted", "secret"):
            raise CommandRejectedError("commitment_visibility_invalid")
        cache = _OwnerResolutionCache()
        try:
            self._resolve_covered_owner(subject_ref, cache=cache)
            if target_ref is not None:
                self._resolve_covered_owner(target_ref, cache=cache)
            if host_ref is not None:
                self._resolve_covered_owner(host_ref, cache=cache)
        except CommandRejectedError as exc:
            raise CommandRejectedError("commitment_owner_unresolved") from exc
        authority_basis = "voluntary_commitment"
        if kind == "order":
            if target_ref is None or host_ref is None:
                raise CommandRejectedError("order_authority_required")
            decision = self._domain_authority(cache=cache).team_command(
                commander_ref=subject_ref,
                subject_refs=(target_ref,),
                team_ref=host_ref,
            )
            if not decision.allowed:
                raise CommandRejectedError("order_not_authorized")
            authority_basis = decision.basis
        elif kind == "obligation" and target_ref is not None and target_ref != subject_ref:
            if host_ref is None:
                raise CommandRejectedError("obligation_authority_required")
            decision = self._domain_authority(cache=cache).team_command(
                commander_ref=subject_ref,
                subject_refs=(target_ref,),
                team_ref=host_ref,
            )
            if not decision.allowed:
                raise CommandRejectedError("obligation_not_authorized")
            authority_basis = decision.basis
        try:
            registry = copy.deepcopy(self.repository.read_json(_COMMITMENT_REGISTRY_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("commitment_registry_invalid") from exc
        records = registry.get("records") if isinstance(registry, dict) else None
        if not isinstance(records, list):
            raise CommandRejectedError("commitment_registry_invalid")
        if any(isinstance(r, Mapping) and r.get("id") == commitment_id for r in records):
            raise CommandRejectedError("commitment_id_conflict")
        record = {
            "id": commitment_id,
            "kind": kind,
            "subject_ref": subject_ref,
            "target_ref": target_ref,
            "host_ref": host_ref,
            "created_at": str(current_time),
            "due_at": None if due_at is None else str(due_at),
            "status": "active",
            "summary": summary,
            "visibility": visibility,
            "authority_basis": authority_basis,
        }
        records.append(record)
        self._trim_terminal_records(
            records,
            terminal_statuses={"completed", "cancelled", "failed"},
            limit=self.MAX_TERMINAL_COMMITMENT_HISTORY,
        )
        scene = copy.deepcopy(self._scene_base(current_time))
        scheduler = self._load_scheduler(current_time=current_time, scene=scene)
        if due_at is not None:
            host_id = "host." + commitment_id
            scheduler.add_host(
                SchedulerHost(
                    state=HostState(
                        host_id=host_id,
                        kind="commitment",
                        resolved_through=current_time,
                        safe_through=due_at.add_seconds(-1),
                        handler_ref="causal.scheduler",
                        rng_namespace=commitment_id,
                        next_due=due_at,
                    ),
                    authority_kind="commitment",
                    owner_ref=_COMMITMENT_REGISTRY_PATH,
                    metadata={"commitment_id": commitment_id},
                )
            )
            scheduler.upsert_event(
                one_shot_event(
                    kind="commitment.due",
                    identity=commitment_id,
                    source_host=host_id,
                    target_host=host_id,
                    due_at=due_at,
                    payload={"commitment_id": commitment_id},
                    priority=30,
                    visibility=("player_known" if meta.get("player_id") in (subject_ref, target_ref) else visibility),
                    requires_player=(meta.get("player_id") in (subject_ref, target_ref)),
                )
            )
        world_events = self._world_events()
        event_id = self._append_semantic_event(
            world_events, command=command, kind=f"commitment_{kind}", at=current_time,
            host_refs=tuple(x for x in (host_ref,) if x),
            actor_refs=tuple(x for x in (subject_ref, target_ref) if x),
            affected_owner_refs=(_COMMITMENT_REGISTRY_PATH, self.scheduler_path),
            material_consequence_refs=(commitment_id,), classification=visibility,
            audience_refs=tuple(x for x in (subject_ref, target_ref) if x),
            reducer_ref="shinobi_runtime.commands.commitment_resolution",
        )
        scene["scene_summary"] = summary
        narrative = scene.get("narrative")
        if isinstance(narrative, dict):
            values = narrative.get("promises_and_threats")
            if isinstance(values, list):
                values.append(commitment_id)
                del values[:-6]
        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            self.scene_path: _json_bytes(scene),
            self.scheduler_path: _json_bytes(scheduler.to_record()),
            _COMMITMENT_REGISTRY_PATH: _json_bytes(registry),
            **self._world_event_writes(world_events),
        }
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("commitment write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
            staged = overlay.read_json(_COMMITMENT_REGISTRY_PATH)
            if not any(isinstance(r, Mapping) and r.get("id") == commitment_id for r in staged.get("records", [])):
                raise ValueError("commitment did not persist")
            CausalSchedulerRegistry.from_record(overlay.read_json(self.scheduler_path))

        return _BuiltPlan(
            code="commitment_resolution_ready",
            affected_refs=expected_paths,
            writes=writes,
            result={"command_type": command.command_type, "commitment_id": commitment_id, "kind": kind, "authority_basis": authority_basis, "semantic_event_id": event_id},
            validator=validate,
        )
    def _commitment_transition(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _exact_payload(
            command.payload,
            ("commitment_id", "target_status", "summary"),
            command.command_type,
        )
        commitment_id = _stable_id(
            command.payload["commitment_id"],
            "commitment_id_invalid",
            prefix="commitment.",
        )
        target_status = command.payload["target_status"]
        if target_status not in ("completed", "failed", "cancelled"):
            raise CommandRejectedError("commitment_transition_invalid")
        summary = command.payload["summary"]
        if not isinstance(summary, str) or not summary or len(summary) > 1000:
            raise CommandRejectedError("commitment_transition_summary_invalid")
        try:
            registry = copy.deepcopy(self.repository.read_json(_COMMITMENT_REGISTRY_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("commitment_registry_invalid") from exc
        records = registry.get("records") if isinstance(registry, Mapping) else None
        if not isinstance(records, list):
            raise CommandRejectedError("commitment_registry_invalid")
        matches = [r for r in records if isinstance(r, dict) and r.get("id") == commitment_id]
        if len(matches) != 1:
            raise CommandRejectedError("commitment_not_found")
        record = matches[0]
        if record.get("status") not in ("active", "overdue"):
            raise CommandRejectedError("commitment_not_active")
        subject_ref = record.get("subject_ref")
        target_ref = record.get("target_ref")
        if command.actor_id not in (subject_ref, target_ref):
            raise CommandRejectedError("commitment_transition_not_authorized")
        record["status"] = target_status
        record["resolved_at"] = str(current_time)
        record["resolution_summary"] = summary
        self._trim_terminal_records(
            records,
            terminal_statuses={"completed", "cancelled", "failed"},
            limit=self.MAX_TERMINAL_COMMITMENT_HISTORY,
            preserve_ids=(commitment_id,),
        )

        scene = copy.deepcopy(self._scene_base(current_time))
        scheduler = self._load_scheduler(current_time=current_time, scene=scene)
        host_id = "host." + commitment_id
        retained = [
            event for event in scheduler.queue.snapshot()
            if not (event.target_host == host_id and event.payload.get("commitment_id") == commitment_id)
        ]
        scheduler.queue.replace(retained)
        scheduler.hosts.pop(host_id, None)
        scheduler.metrics.update({
            "pending_event_count": len(scheduler.queue),
            "host_count": len(scheduler.hosts),
        })

        world_events = self._world_events()
        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind=f"commitment_{target_status}",
            at=current_time,
            host_refs=tuple(x for x in (record.get("host_ref"),) if isinstance(x, str)),
            actor_refs=tuple(x for x in (subject_ref, target_ref) if isinstance(x, str)),
            affected_owner_refs=(_COMMITMENT_REGISTRY_PATH, self.scheduler_path),
            material_consequence_refs=(commitment_id,),
            classification=str(record.get("visibility") or "restricted"),
            audience_refs=tuple(x for x in (subject_ref, target_ref) if isinstance(x, str)),
            reducer_ref="shinobi_runtime.commands.commitment_transition",
        )
        scene["scene_summary"] = summary
        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            self.scene_path: _json_bytes(scene),
            self.scheduler_path: _json_bytes(scheduler.to_record()),
            _COMMITMENT_REGISTRY_PATH: _json_bytes(registry),
            **self._world_event_writes(world_events),
        }
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("commitment transition write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
            staged = overlay.read_json(_COMMITMENT_REGISTRY_PATH)
            found = [r for r in staged.get("records", []) if isinstance(r, Mapping) and r.get("id") == commitment_id]
            if len(found) != 1 or found[0].get("status") != target_status:
                raise ValueError("commitment transition did not persist")
            scheduler_record = CausalSchedulerRegistry.from_record(overlay.read_json(self.scheduler_path))
            if host_id in scheduler_record.hosts:
                raise ValueError("resolved commitment scheduler host remained active")

        return _BuiltPlan(
            code="commitment_transition_ready",
            affected_refs=expected_paths,
            writes=writes,
            result={
                "command_type": command.command_type,
                "commitment_id": commitment_id,
                "status": target_status,
                "semantic_event_id": event_id,
            },
            validator=validate,
        )
    def _scene_boundary_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _exact_payload(
            command.payload,
            ("action_kind", "subject_ref", "target_ref", "boundary_event_id", "summary", "visibility"),
            command.command_type,
        )
        action_kind = command.payload["action_kind"]
        if action_kind != "resolve_clock_boundary":
            raise CommandRejectedError("scene_action_kind_invalid")
        subject_ref = _stable_id(command.payload["subject_ref"], "scene_action_subject_invalid")
        target_raw = command.payload.get("target_ref")
        if target_raw is not None:
            raise CommandRejectedError("scene_action_target_invalid")
        summary = command.payload["summary"]
        if not isinstance(summary, str) or not summary or len(summary) > 1000:
            raise CommandRejectedError("scene_action_summary_invalid")
        visibility = command.payload["visibility"]
        if visibility not in ("public", "restricted", "secret"):
            raise CommandRejectedError("scene_action_visibility_invalid")
        scene = copy.deepcopy(self._scene_base(current_time))
        scheduler = self._load_scheduler(current_time=current_time, scene=scene)
        boundary_id = command.payload["boundary_event_id"]
        if not isinstance(boundary_id, str) or not boundary_id:
            raise CommandRejectedError("scene_boundary_id_invalid")
        boundaries = scene.get("known_clock_boundaries")
        if not isinstance(boundaries, list):
            raise CommandRejectedError("campaign_scene_invalid")
        matching = [item for item in boundaries if isinstance(item, Mapping) and item.get("event_id") == boundary_id]
        if len(matching) != 1:
            raise CommandRejectedError("scene_boundary_not_found")
        due = CampaignTime.parse(matching[0].get("due_at"))
        if due > current_time:
            raise CommandRejectedError("scene_boundary_not_due")
        scene["known_clock_boundaries"] = [
            item for item in boundaries if not (isinstance(item, Mapping) and item.get("event_id") == boundary_id)
        ]
        host_id = "host.scene.current"
        retained = [
            event for event in scheduler.queue.snapshot()
            if not (event.target_host == host_id and event.payload.get("scene_event_id") == boundary_id)
        ]
        scheduler.queue.replace(retained)
        wrapper = scheduler.hosts.get(host_id)
        if wrapper is not None:
            wrapper.state.resolved_through = current_time
            wrapper.state.safe_through = current_time
            wrapper.state.next_due = min((event.due_at for event in retained if event.target_host == host_id), default=None)
            if wrapper.state.next_due is None:
                scheduler.hosts.pop(host_id, None)
        scene["time_passage_allowed"] = True
        scene["scene_summary"] = summary
        scene["decision_required"] = "Choose the next consequential action."
        world_events = self._world_events()
        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind="scene_clock_boundary_resolved",
            at=current_time,
            actor_refs=(subject_ref,),
            place_refs=(scene.get("location_id"),),
            causal_refs=(boundary_id,),
            affected_owner_refs=(self.scene_path, self.scheduler_path),
            material_consequence_refs=(f"clock-boundary:{boundary_id}",),
            classification=visibility,
            audience_refs=(command.actor_id,),
            reducer_ref="shinobi_runtime.commands.resolve_clock_boundary",
        )
        scheduler.metrics.update({"pending_event_count": len(scheduler.queue), "host_count": len(scheduler.hosts)})
        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            self.scene_path: _json_bytes(scene),
            self.scheduler_path: _json_bytes(scheduler.to_record()),
            **self._world_event_writes(world_events),
        }
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("scene action write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
            CausalSchedulerRegistry.from_record(overlay.read_json(self.scheduler_path))
            staged = overlay.read_json(self.scene_path)
            if any(item.get("event_id") == boundary_id for item in staged.get("known_clock_boundaries", [])):
                raise ValueError("resolved scene boundary remained authoritative")

        return _BuiltPlan(
            code="scene_boundary_resolution_ready",
            affected_refs=expected_paths,
            writes=writes,
            result={"command_type": command.command_type, "action_kind": action_kind, "boundary_event_id": boundary_id, "semantic_event_id": event_id},
            validator=validate,
        )
    def _force_assignment_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _exact_payload(
            command.payload,
            (
                "assignment_id", "force_ref", "grantor_ref", "recipient_ref",
                "allocated_count", "source_availability_class",
                "operational_attachment_ref", "authority_limits", "expires_at",
            ),
            command.command_type,
        )
        assignment_id = _stable_id(command.payload["assignment_id"], "force_assignment_id_invalid", prefix="assign.")
        force_ref = _stable_id(command.payload["force_ref"], "force_assignment_force_invalid", )
        grantor_ref = _stable_id(command.payload["grantor_ref"], "force_assignment_grantor_invalid")
        if grantor_ref != command.actor_id:
            raise CommandRejectedError("force_assignment_grantor_actor_mismatch")
        recipient_ref = _stable_id(command.payload["recipient_ref"], "force_assignment_recipient_invalid")
        count = command.payload["allocated_count"]
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            raise CommandRejectedError("force_assignment_count_invalid")
        source_class = command.payload["source_availability_class"]
        if not isinstance(source_class, str) or not source_class:
            raise CommandRejectedError("force_assignment_availability_invalid")
        attachment = command.payload["operational_attachment_ref"]
        if attachment is not None:
            attachment = _stable_id(attachment, "force_assignment_attachment_invalid")
        limits = command.payload["authority_limits"]
        if not isinstance(limits, Mapping):
            raise CommandRejectedError("force_assignment_limits_invalid")
        expires_raw = command.payload["expires_at"]
        expires_at = None
        if expires_raw is not None:
            try:
                expires_at = CampaignTime.parse(expires_raw)
            except (TypeError, ValueError) as exc:
                raise CommandRejectedError("force_assignment_expiry_invalid") from exc
            if expires_at <= current_time:
                raise CommandRejectedError("force_assignment_expiry_invalid")

        cache = _OwnerResolutionCache()
        try:
            force_path, _digest, force_view = self._resolve_covered_owner_view(force_ref, cache=cache)
            self._resolve_covered_owner(grantor_ref, cache=cache)
            self._resolve_covered_owner(recipient_ref, cache=cache)
        except CommandRejectedError as exc:
            raise CommandRejectedError("force_assignment_owner_unresolved") from exc
        if not isinstance(force_view, Mapping) or force_view.get("schema") != "force":
            raise CommandRejectedError("force_assignment_force_invalid")
        availability = force_view.get("availability")
        if not isinstance(availability, Mapping) or not isinstance(availability.get(source_class), int):
            raise CommandRejectedError("force_assignment_availability_invalid")
        decision = self._domain_authority(cache=cache).force_grant(
            grantor_ref=grantor_ref, force_record=force_view
        )
        if not decision.allowed:
            raise CommandRejectedError("force_assignment_not_authorized")
        try:
            assignments = copy.deepcopy(self.repository.read_json("state/org/assignments.json"))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("force_assignment_registry_invalid") from exc
        records = assignments.get("records") if isinstance(assignments, dict) else None
        if not isinstance(records, list):
            raise CommandRejectedError("force_assignment_registry_invalid")
        if any(isinstance(r, Mapping) and r.get("id") == assignment_id for r in records):
            raise CommandRejectedError("force_assignment_id_conflict")
        reserved = sum(
            int(r.get("allocated_count", 0))
            for r in records
            if isinstance(r, Mapping)
            and r.get("status", "active") == "active"
            and r.get("source_owner") == force_ref
            and r.get("source_availability_class") == source_class
            and isinstance(r.get("allocated_count", 0), int)
        )
        if reserved + count > availability[source_class]:
            raise CommandRejectedError("force_assignment_availability_insufficient")
        record = {
            "id": assignment_id,
            "source_owner": force_ref,
            "assignment_kind": "aggregate_force_slice",
            "receiving_commander": recipient_ref,
            "grantor_ref": grantor_ref,
            "allocated_count": count,
            "source_availability_class": source_class,
            "operational_attachment_ref": attachment,
            "authority_limits": dict(limits),
            "start": str(current_time),
            "expires_at": None if expires_at is None else str(expires_at),
            "status": "active",
            "return_condition": "expiry, revocation, operation completion, incapacity, or lawful reassignment",
            "return_policy_ref": force_view.get("reconstitution_policy_ref"),
            "formation_refs": [],
            "raw_allocations": [],
        }
        records.append(record)
        self._trim_terminal_records(
            records,
            terminal_statuses={"released", "revoked", "completed"},
            limit=self.MAX_TERMINAL_ASSIGNMENT_HISTORY,
        )
        world_events = self._world_events()
        event_id = self._append_semantic_event(
            world_events, command=command, kind="force_assignment_created", at=current_time,
            host_refs=(force_ref, attachment), actor_refs=(grantor_ref, recipient_ref),
            affected_owner_refs=("state/org/assignments.json",),
            material_consequence_refs=(assignment_id, f"force-command:{force_ref}:{count}"),
            audience_refs=(command.actor_id,),
            reducer_ref="shinobi_runtime.domain.authority.DomainAuthorityResolver",
        )
        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            "state/org/assignments.json": _json_bytes(assignments),
            **self._world_event_writes(world_events),
        }
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("force assignment write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
            staged = overlay.read_json("state/org/assignments.json")
            matches = [r for r in staged.get("records", []) if isinstance(r, Mapping) and r.get("id") == assignment_id]
            if len(matches) != 1 or matches[0].get("allocated_count") != count:
                raise ValueError("force assignment did not persist exactly once")

        return _BuiltPlan(
            code="force_assignment_resolution_ready",
            affected_refs=expected_paths,
            writes=writes,
            result={
                "command_type": command.command_type,
                "assignment_id": assignment_id,
                "force_ref": force_ref,
                "recipient_ref": recipient_ref,
                "allocated_count": count,
                "authority_basis": decision.basis,
                "semantic_event_id": event_id,
            },
            validator=validate,
        )
    def _force_assignment_transition(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _exact_payload(
            command.payload,
            ("assignment_id", "target_status", "reason"),
            command.command_type,
        )
        assignment_id = _stable_id(
            command.payload["assignment_id"],
            "force_assignment_id_invalid",
            prefix="assign.",
        )
        target_status = command.payload["target_status"]
        if target_status not in ("released", "revoked", "completed"):
            raise CommandRejectedError("force_assignment_transition_invalid")
        reason = command.payload["reason"]
        if not isinstance(reason, str) or not reason or len(reason) > 1000:
            raise CommandRejectedError("force_assignment_transition_reason_invalid")
        try:
            assignments = copy.deepcopy(self.repository.read_json("state/org/assignments.json"))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("force_assignment_registry_invalid") from exc
        records = assignments.get("records") if isinstance(assignments, Mapping) else None
        if not isinstance(records, list):
            raise CommandRejectedError("force_assignment_registry_invalid")
        matches = [r for r in records if isinstance(r, dict) and r.get("id") == assignment_id]
        if len(matches) != 1:
            raise CommandRejectedError("force_assignment_not_found")
        record = matches[0]
        if record.get("status", "active") != "active":
            raise CommandRejectedError("force_assignment_not_active")
        force_ref = record.get("source_owner")
        commander_ref = record.get("receiving_commander")
        grantor_ref = record.get("grantor_ref")
        if not all(isinstance(x, str) and x for x in (force_ref, commander_ref)):
            raise CommandRejectedError("force_assignment_registry_invalid")
        cache = _OwnerResolutionCache()
        authorized = command.actor_id == commander_ref
        if not authorized:
            try:
                _p, _d, force_view = self._resolve_covered_owner_view(force_ref, cache=cache)
                decision = self._domain_authority(cache=cache).force_grant(
                    grantor_ref=command.actor_id,
                    force_record=force_view,
                )
                authorized = decision.allowed
            except CommandRejectedError:
                authorized = False
        if not authorized:
            raise CommandRejectedError("force_assignment_transition_not_authorized")
        record["status"] = target_status
        record["ended_at"] = str(current_time)
        record["end_reason"] = reason
        record["returned_to_owner"] = True
        self._trim_terminal_records(
            records,
            terminal_statuses={"released", "revoked", "completed"},
            limit=self.MAX_TERMINAL_ASSIGNMENT_HISTORY,
            preserve_ids=(assignment_id,),
        )

        world_events = self._world_events()
        event_id = self._append_semantic_event(
            world_events, command=command, kind=f"force_assignment_{target_status}", at=current_time,
            host_refs=tuple(x for x in (force_ref, record.get("operational_attachment_ref")) if isinstance(x, str)),
            actor_refs=tuple(x for x in (command.actor_id, commander_ref, grantor_ref) if isinstance(x, str)),
            affected_owner_refs=("state/org/assignments.json",),
            material_consequence_refs=(assignment_id, f"force-return:{force_ref}"),
            audience_refs=(command.actor_id,),
            reducer_ref="shinobi_runtime.domain.authority.DomainAuthorityResolver",
        )
        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            "state/org/assignments.json": _json_bytes(assignments),
            **self._world_event_writes(world_events),
        }
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("force assignment transition write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
            staged = overlay.read_json("state/org/assignments.json")
            found = [r for r in staged.get("records", []) if isinstance(r, Mapping) and r.get("id") == assignment_id]
            if len(found) != 1 or found[0].get("status") != target_status:
                raise ValueError("force assignment transition did not persist")

        return _BuiltPlan(
            code="force_assignment_transition_ready",
            affected_refs=expected_paths,
            writes=writes,
            result={
                "command_type": command.command_type,
                "assignment_id": assignment_id,
                "status": target_status,
                "returned_to_owner": True,
                "semantic_event_id": event_id,
            },
            validator=validate,
        )
    def _formation_lifecycle_resolution(
        self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime
    ) -> _BuiltPlan:
        _exact_payload(
            command.payload,
            (
                "action", "force_ref", "formation_ref", "secondary_formation_ref",
                "formation_size", "target_personnel", "split_personnel",
                "max_operational_personnel", "operational_attachment_ref", "location_ref",
            ),
            command.command_type,
        )
        action = command.payload.get("action")
        kind_map = {
            "mobilize": "formation_expand",
            "drill": "formation_drill",
            "reconstitute": "formation_reconstitute",
            "release": "formation_release",
            "split": "formation_split",
            "merge": "formation_merge",
        }
        if action not in kind_map:
            raise CommandRejectedError("formation_lifecycle_action_invalid")
        formation_ref = command.payload.get("formation_ref")
        if formation_ref is not None:
            formation_ref = _stable_id(formation_ref, "formation_ref_invalid")
        force_raw = command.payload.get("force_ref")
        if force_raw is None and formation_ref is not None:
            _derived_path, derived_force_ref, _derived_formation = self._formation_by_id(formation_ref)
            force_ref = derived_force_ref
        else:
            force_ref = _stable_id(force_raw, "formation_force_ref_invalid")
            if formation_ref is not None:
                _derived_path, derived_force_ref, _derived_formation = self._formation_by_id(formation_ref)
                if derived_force_ref != force_ref:
                    raise CommandRejectedError("formation_force_mismatch")
        secondary_formation_ref = command.payload.get("secondary_formation_ref")
        if secondary_formation_ref is not None:
            secondary_formation_ref = _stable_id(secondary_formation_ref, "secondary_formation_ref_invalid")
        formation_size = command.payload.get("formation_size")
        target_personnel = command.payload.get("target_personnel")
        split_personnel = command.payload.get("split_personnel")
        max_operational = command.payload.get("max_operational_personnel")
        for value, code in (
            (formation_size, "formation_size_invalid"),
            (target_personnel, "formation_target_invalid"),
            (split_personnel, "formation_split_personnel_invalid"),
            (max_operational, "formation_operational_cap_invalid"),
        ):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value <= 0):
                raise CommandRejectedError(code)
        if action in ("drill", "reconstitute", "release", "split", "merge") and formation_ref is None:
            raise CommandRejectedError("formation_ref_required")
        if action == "split" and split_personnel is None:
            raise CommandRejectedError("formation_split_personnel_required")
        if action == "merge" and secondary_formation_ref is None:
            raise CommandRejectedError("secondary_formation_ref_required")
        if action == "merge" and secondary_formation_ref == formation_ref:
            raise CommandRejectedError("formation_merge_requires_distinct_formations")

        attachment = command.payload.get("operational_attachment_ref")
        if attachment is not None:
            attachment = _stable_id(attachment, "formation_attachment_invalid")
        # Formation location is engine-owned. Mobilization always occurs at the
        # force's canonical anchor; all later relocation uses route/time movement.
        if command.payload.get("location_ref") is not None:
            raise CommandRejectedError("formation_location_requires_movement_resolution")
        location_ref = None

        cache = _OwnerResolutionCache()
        try:
            force_path, _digest, force_view = self._resolve_covered_owner_view(force_ref, cache=cache)
        except CommandRejectedError as exc:
            raise CommandRejectedError("formation_force_unresolved") from exc
        if not isinstance(force_view, Mapping) or force_view.get("schema") != "force":
            raise CommandRejectedError("formation_force_unresolved")
        if action == "mobilize":
            location_ref = force_view.get("mobilization_anchor_ref")
            if not isinstance(location_ref, str) or not location_ref.startswith("place."):
                raise CommandRejectedError("formation_mobilization_anchor_missing")
            graph = self._location_graph()
            if graph.place(location_ref) is None:
                raise CommandRejectedError("formation_mobilization_anchor_invalid")
        authority = self._domain_authority(cache=cache)
        grant = authority.force_grant(grantor_ref=command.actor_id, force_record=force_view)
        requested = target_personnel or formation_size or split_personnel or 1
        if not grant.allowed:
            command_decision = authority.force_command(
                commander_ref=command.actor_id,
                force_ref=force_ref,
                operational_attachment_ref=attachment,
                named_actor_refs=(),
                committed_count=requested,
                effective_at=str(current_time),
            )
            if not command_decision.allowed:
                raise CommandRejectedError("formation_lifecycle_not_authorized")
            authority_basis = command_decision.basis
        else:
            authority_basis = grant.basis

        world_events = self._world_events()
        record_writes: Dict[str, Dict[str, Any]] = {}
        formation_path = self._formation_registry_path(force_ref)
        payload = {
            "force_ref": force_ref,
            "formation_registry_ref": formation_path,
            "formation_ref": formation_ref,
            "secondary_formation_ref": secondary_formation_ref,
            "formation_size": formation_size,
            "target_personnel": target_personnel,
            "split_personnel": split_personnel,
            "max_operational_personnel": max_operational,
            "location_ref": location_ref,
            "compacted_reviews": 1,
        }
        result = self._apply_autonomous_formation_action(
            kind=kind_map[action],
            owner_identity=attachment or force_ref,
            actor=command.actor_id,
            payload=payload,
            at=current_time,
            command=command,
            world_events=world_events,
            record_writes=record_writes,
            classification="restricted",
        )
        if result.get("skipped"):
            raise CommandRejectedError("formation_lifecycle_no_effect:" + str(result.get("skipped")))
        writes: Dict[str, bytes] = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            **self._world_event_writes(world_events),
        }
        for path, record in record_writes.items():
            writes[path] = _json_bytes(record)
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("formation lifecycle write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
            staged_force = overlay.read_json(force_path)
            staged_registry = overlay.read_json(formation_path)
            represented = sum(
                row.get("personnel_total", 0) for row in staged_registry.get("formations", [])
                if isinstance(row, Mapping) and isinstance(row.get("personnel_total"), int)
            )
            deployed = staged_force.get("availability", {}).get("deployed")
            if not isinstance(deployed, int) or isinstance(deployed, bool) or represented > deployed:
                raise ValueError("formation lifecycle overrepresented deployed force personnel")
            if sum(staged_force.get("availability", {}).values()) != staged_force.get("total"):
                raise ValueError("formation lifecycle broke force conservation")

        return _BuiltPlan(
            code="formation_lifecycle_resolution_ready",
            affected_refs=expected_paths,
            writes=writes,
            result={
                "command_type": command.command_type,
                "action": action,
                "force_ref": force_ref,
                "authority_basis": authority_basis,
                "location_ref": location_ref,
                **dict(result),
            },
            validator=validate,
        )

