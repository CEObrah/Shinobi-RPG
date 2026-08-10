"""Causal time settlement and bounded autonomous-world command support."""

from __future__ import annotations

import copy
import hashlib
from typing import (
    Any,
    Dict,
    Iterable,
    Mapping,
    Optional,
    Tuple,
)

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.autonomy import review_faction
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.core import (
    _BuiltPlan,
    _campaign_datetime,
    _exact_payload,
    _json_bytes,
)
from shinobi_runtime.commands.constants import ACTIVE_PRESSURE_STATUSES as _ACTIVE_PRESSURE_STATUSES
from shinobi_runtime.commands.paths import (
    COMMITMENT_REGISTRY_PATH as _COMMITMENT_REGISTRY_PATH,
    COMBAT_ZOOM_REGISTRY_PATH as _COMBAT_ZOOM_REGISTRY_PATH,
    ECONOMY_WORLD_PATH as _ECONOMY_WORLD_PATH,
    INVENTORY_REGISTRY_PATH as _INVENTORY_REGISTRY_PATH,
    PERSON_CONTINUITY_PATH as _PERSON_CONTINUITY_PATH,
    POPULATION_REGISTRY_PATH as _POPULATION_REGISTRY_PATH,
    RECOVERY_POLICY_PATH as _RECOVERY_POLICY_PATH,
    WORLD_EVENT_REGISTRY_PATH as _WORLD_EVENT_REGISTRY_PATH,
)
from shinobi_runtime.reducers import settle_recovery
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.sim.scheduler import (
    CausalSchedulerRegistry,
    settle_scheduler,
)
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest



class TimeCommandsMixin:
    @staticmethod
    def _replace_host_events(
        scheduler: CausalSchedulerRegistry,
        host_id: str,
        events: Iterable[Any],
    ) -> None:
        retained = [
            event for event in scheduler.queue.snapshot() if event.target_host != host_id
        ]
        retained.extend(events)
        scheduler.queue.replace(retained)
        wrapper = scheduler.hosts.get(host_id)
        if wrapper is not None:
            due = min(
                (event.due_at for event in scheduler.queue.snapshot() if event.target_host == host_id),
                default=None,
            )
            wrapper.state.next_due = due
            if due is not None and wrapper.state.safe_through >= due:
                wrapper.state.safe_through = due.add_seconds(-1)
    def _advance_time(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _exact_payload(command.payload, ("target_time",), command.command_type)
        try:
            requested_target = CampaignTime.parse(command.payload["target_time"])
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("target_time_invalid") from exc
        if requested_target <= current_time:
            raise CommandRejectedError(
                "command_no_op"
                if requested_target == current_time
                else "target_time_before_current"
            )
        elapsed = int(
            (
                _campaign_datetime(requested_target)
                - _campaign_datetime(current_time)
            ).total_seconds()
        )
        if elapsed > self.MAX_ADVANCE_SECONDS:
            raise CommandRejectedError("target_time_exceeds_command_horizon")

        scene = copy.deepcopy(self._scene_base(current_time))
        try:
            zoom_registry = self.repository.read_json(_COMBAT_ZOOM_REGISTRY_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("combat_zoom_registry_invalid") from exc
        pending_zoom = zoom_registry.get("pending_by_actor") if isinstance(zoom_registry, Mapping) else None
        if not isinstance(pending_zoom, Mapping):
            raise CommandRejectedError("combat_zoom_registry_invalid")
        if pending_zoom:
            # Aggregate combat has reserved exact identities for a linked scene.
            # Time may not advance until those exact consequences are reconciled
            # back into the parent battle exactly once.
            raise CommandRejectedError("time_boundary_requires_combat_zoom")
        if scene.get("active_combat") is True:
            raise CommandRejectedError("scene_time_passage_blocked")
        if scene.get("time_passage_allowed") is not True:
            boundaries = scene.get("known_clock_boundaries", [])
            if isinstance(boundaries, list):
                for boundary in boundaries:
                    if not isinstance(boundary, Mapping):
                        continue
                    try:
                        due = CampaignTime.parse(boundary.get("due_at"))
                    except (TypeError, ValueError):
                        continue
                    if due <= current_time:
                        raise CommandRejectedError("scene_boundary_requires_player_decision")
            raise CommandRejectedError("scene_time_passage_blocked")

        scheduler = self._load_scheduler(
            current_time=current_time, scene=scene
        )
        try:
            catchup = settle_scheduler(scheduler, target=requested_target)
        except (TypeError, ValueError, RuntimeError) as exc:
            raise CommandRejectedError("causal_scheduler_settlement_failed") from exc
        if catchup.budget_exhausted:
            raise CommandRejectedError("time_boundary_budget_exceeded")
        if catchup.unsafe_host_ids and catchup.interrupt is None:
            raise CommandRejectedError("time_boundary_requires_domain_settlement")
        target = catchup.reached_time

        pressures: Optional[Dict[str, Any]] = None
        faction_writes: Dict[str, Dict[str, Any]] = {}
        faction_reviews: list[str] = []
        pressure_reviews: list[str] = []
        world_registry_writes: Dict[str, Dict[str, Any]] = {}
        world_registry_reviews: list[str] = []
        economy_inventory_write: Optional[Dict[str, Any]] = None
        economy_reviews: list[Mapping[str, Any]] = []
        house_writes: Dict[str, Dict[str, Any]] = {}
        house_reviews: list[str] = []
        population_write: Optional[Dict[str, Any]] = None
        population_reviews: list[Mapping[str, Any]] = []
        continuity_write: Optional[Dict[str, Any]] = None
        continuity_reviews: list[Mapping[str, Any]] = []
        recovery_reviews: list[Mapping[str, Any]] = []
        autonomy_policy = self._autonomy_policy_book()
        autonomy_record_writes: Dict[str, Dict[str, Any]] = {}
        autonomy_results: list[Mapping[str, Any]] = []
        team_reviews: list[Mapping[str, Any]] = []
        commitment_write: Optional[Dict[str, Any]] = None
        commitment_reviews: list[Mapping[str, Any]] = []
        world_events_for_time: Optional[Dict[str, Any]] = None

        for fact in catchup.public_facts:
            if not isinstance(fact, Mapping):
                raise CommandRejectedError("causal_scheduler_settlement_failed")
            kind = fact.get("scheduler_event_kind")
            payload = fact.get("payload")
            if not isinstance(payload, Mapping):
                raise CommandRejectedError("causal_scheduler_settlement_failed")
            try:
                latest_due = CampaignTime.parse(fact.get("latest_due"))
            except (TypeError, ValueError) as exc:
                raise CommandRejectedError("causal_scheduler_settlement_failed") from exc
            compacted = fact.get("compacted_boundaries")
            if isinstance(compacted, bool) or not isinstance(compacted, int) or compacted <= 0:
                raise CommandRejectedError("causal_scheduler_settlement_failed")

            if kind == "person.recovery.periodic_review":
                actor_ref = payload.get("actor_ref")
                owner_ref = payload.get("owner_ref")
                if not isinstance(actor_ref, str) or not isinstance(owner_ref, str):
                    raise CommandRejectedError("person_recovery_boundary_invalid")
                record = autonomy_record_writes.get(owner_ref)
                if record is None:
                    try:
                        loaded = self.repository.read_json(owner_ref)
                    except (FileNotFoundError, ValueError) as exc:
                        raise CommandRejectedError("person_recovery_owner_invalid") from exc
                    if not isinstance(loaded, dict):
                        raise CommandRejectedError("person_recovery_owner_invalid")
                    record = copy.deepcopy(loaded)
                    autonomy_record_writes[owner_ref] = record
                if record.get("owner_id") != actor_ref:
                    raise CommandRejectedError("person_recovery_owner_invalid")
                try:
                    policy = self.repository.read_json(_RECOVERY_POLICY_PATH)
                except (FileNotFoundError, ValueError) as exc:
                    raise CommandRejectedError("recovery_policy_invalid") from exc
                elapsed_seconds = compacted * 24 * 60 * 60
                before_condition = copy.deepcopy(record.get("condition"))
                before_resources = copy.deepcopy(record.get("resources"))
                try:
                    outcome = settle_recovery(record, elapsed_seconds=elapsed_seconds, policy=policy)
                except (TypeError, ValueError) as exc:
                    raise CommandRejectedError("person_recovery_resolution_invalid") from exc
                ready = record.get("life_status") in ("active", "alive") and isinstance(record.get("condition"), Mapping) and record["condition"].get("readiness") == "ready"
                host_id = str(fact.get("target_host"))
                host = scheduler.hosts.get(host_id)
                force_return: Optional[Mapping[str, Any]] = None
                if ready and host is not None and isinstance(host.metadata, Mapping):
                    force_path = host.metadata.get("force_path")
                    force_ref = host.metadata.get("force_ref")
                    return_class = host.metadata.get("return_availability_class")
                    if all(isinstance(value, str) and value for value in (force_path, force_ref, return_class)):
                        force_record = autonomy_record_writes.get(force_path)
                        if force_record is None:
                            try:
                                loaded_force = self.repository.read_json(force_path)
                            except (FileNotFoundError, ValueError) as exc:
                                raise CommandRejectedError("person_recovery_force_invalid") from exc
                            if not isinstance(loaded_force, dict) or loaded_force.get("id") != force_ref:
                                raise CommandRejectedError("person_recovery_force_invalid")
                            force_record = copy.deepcopy(loaded_force)
                            autonomy_record_writes[force_path] = force_record
                        availability = force_record.get("availability")
                        if not isinstance(availability, dict):
                            raise CommandRejectedError("person_recovery_force_invalid")
                        effective_return_class = return_class
                        returned_formation_ref: Optional[str] = None
                        returned_formation_path: Optional[str] = None
                        returned_team_paths: list[str] = []
                        if return_class == "deployed":
                            formation_ref = host.metadata.get("return_formation_ref")
                            formation_path = host.metadata.get("return_formation_path")
                            team_refs = host.metadata.get("return_team_refs")
                            can_reembed = (
                                isinstance(formation_ref, str) and formation_ref
                                and isinstance(formation_path, str) and formation_path
                                and isinstance(team_refs, list) and bool(team_refs)
                                and all(isinstance(ref, str) and ref for ref in team_refs)
                            )
                            formation_record = None
                            qualifying_teams: list[Tuple[str, Dict[str, Any]]] = []
                            if can_reembed:
                                formation_record = autonomy_record_writes.get(formation_path)
                                if formation_record is None:
                                    try:
                                        loaded_formation = self.repository.read_json(formation_path)
                                    except (FileNotFoundError, ValueError):
                                        loaded_formation = None
                                    if isinstance(loaded_formation, dict):
                                        formation_record = copy.deepcopy(loaded_formation)
                                formations = formation_record.get("formations") if isinstance(formation_record, Mapping) else None
                                formation = next(
                                    (
                                        row for row in formations or []
                                        if isinstance(row, dict)
                                        and row.get("id") == formation_ref
                                        and row.get("force_ref") == force_ref
                                    ),
                                    None,
                                )
                                current_location = record.get("current_location_id")
                                if (
                                    not isinstance(formation, dict)
                                    or formation.get("location_ref") != current_location
                                ):
                                    can_reembed = False
                                if can_reembed:
                                    total = formation.get("personnel_total")
                                    authorized = formation.get("authorized_personnel", total)
                                    if (
                                        isinstance(total, bool) or not isinstance(total, int)
                                        or isinstance(authorized, bool) or not isinstance(authorized, int)
                                        or total < 0 or total >= authorized
                                    ):
                                        can_reembed = False
                                if can_reembed:
                                    for team_ref in team_refs:
                                        try:
                                            team_path, team_view = self._exact_team(team_ref)
                                        except CommandRejectedError:
                                            continue
                                        team_record = autonomy_record_writes.get(team_path)
                                        if team_record is None:
                                            team_record = copy.deepcopy(dict(team_view))
                                        members = team_record.get("member_refs")
                                        if (
                                            team_record.get("status") == "active"
                                            and team_record.get("current_assignment_ref") == formation_ref
                                            and isinstance(members, list)
                                            and actor_ref in members
                                        ):
                                            qualifying_teams.append((team_path, team_record))
                                    if not qualifying_teams:
                                        can_reembed = False
                            if can_reembed and isinstance(formation_record, dict) and isinstance(formation, dict):
                                self._resize_formation_strength(formation, int(formation["personnel_total"]) + 1)
                                autonomy_record_writes[formation_path] = formation_record
                                returned_formation_ref = formation_ref
                                returned_formation_path = formation_path
                                for team_path, team_record in qualifying_teams:
                                    embedded = team_record.get("embedded_member_refs")
                                    if not isinstance(embedded, list):
                                        raise CommandRejectedError("team_embedded_assignment_invalid")
                                    if actor_ref not in embedded:
                                        embedded.append(actor_ref)
                                        embedded.sort()
                                    autonomy_record_writes[team_path] = team_record
                                    returned_team_paths.append(team_path)
                            else:
                                # The original formation may have moved, filled,
                                # dissolved, or lost this team's assignment while
                                # the person recovered.  Return the body to the
                                # force's nearest ready partition instead of
                                # inventing a new deployment.
                                effective_return_class = next(
                                    (
                                        candidate for candidate in (
                                            "ready_24h", "mobilizable_7d", "mobilizable_30d",
                                            "training_or_instruction", "essential_fixed_duty",
                                        )
                                        if isinstance(availability.get(candidate), int)
                                        and not isinstance(availability.get(candidate), bool)
                                    ),
                                    None,
                                )
                                if effective_return_class is None:
                                    raise CommandRejectedError("person_recovery_force_invalid")
                        medical = availability.get("medical_or_recovery")
                        destination = availability.get(effective_return_class)
                        if (
                            isinstance(medical, bool) or not isinstance(medical, int) or medical <= 0
                            or isinstance(destination, bool) or not isinstance(destination, int) or destination < 0
                        ):
                            raise CommandRejectedError("person_recovery_force_invalid")
                        availability["medical_or_recovery"] = medical - 1
                        availability[effective_return_class] = destination + 1
                        if sum(value for value in availability.values() if isinstance(value, int) and not isinstance(value, bool)) != force_record.get("total"):
                            raise CommandRejectedError("person_recovery_force_conservation_failed")
                        force_return = {
                            "force_ref": force_ref,
                            "force_path": force_path,
                            "return_availability_class": effective_return_class,
                            "formation_ref": returned_formation_ref,
                            "formation_path": returned_formation_path,
                            "team_paths": returned_team_paths,
                        }
                if ready and host_id in scheduler.hosts:
                    scheduler.queue.replace(
                        event for event in scheduler.queue.snapshot()
                        if not (event.target_host == host_id and event.kind == "person.recovery.periodic_review")
                    )
                    scheduler.hosts[host_id].state.next_due = None
                    scheduler.hosts[host_id].state.safe_through = target
                changed = before_condition != record.get("condition") or before_resources != record.get("resources")
                event_id = None
                if changed:
                    if world_events_for_time is None:
                        world_events_for_time = self._world_events()
                    event_id = self._append_internal_event(
                        world_events_for_time, command=command, identity=f"{actor_ref}:{latest_due}:recovery",
                        kind="person_recovery_progressed", at=latest_due, host_refs=(actor_ref,), actor_refs=(actor_ref,),
                        affected_owner_refs=tuple(
                            dict.fromkeys(
                                [owner_ref]
                                + ([] if force_return is None else [
                                    ref for ref in (
                                        force_return.get("force_path"),
                                        force_return.get("formation_path"),
                                    ) if isinstance(ref, str)
                                ])
                                + ([] if force_return is None else [
                                    ref for ref in force_return.get("team_paths", []) if isinstance(ref, str)
                                ])
                            )
                        ),
                        material_consequence_refs=(f"recovery_hours:{elapsed_seconds // 3600}",),
                        classification="restricted", audience_refs=(), source_refs=(actor_ref,),
                    )
                recovery_reviews.append({
                    "person_ref": actor_ref, "at": str(latest_due), "compacted_days": compacted,
                    "ready": ready, "changed": changed, "event_id": event_id, "outcome": outcome,
                    "force_return": None if force_return is None else dict(force_return),
                })
                continue

            if kind == "faction.periodic_review":
                owner_ref = payload.get("owner_ref")
                faction_id = payload.get("faction_id")
                if not isinstance(owner_ref, str) or not isinstance(faction_id, str):
                    raise CommandRejectedError("faction_owner_invalid")
                if owner_ref not in faction_writes:
                    try:
                        loaded = self.repository.read_json(owner_ref)
                    except (FileNotFoundError, ValueError) as exc:
                        raise CommandRejectedError("faction_owner_invalid") from exc
                    if not isinstance(loaded, dict):
                        raise CommandRejectedError("faction_owner_invalid")
                    faction_writes[owner_ref] = copy.deepcopy(loaded)
                faction_record = faction_writes[owner_ref]
                faction = faction_record.get("faction")
                plan = faction.get("plan_state") if isinstance(faction, dict) else None
                if (
                    not isinstance(plan, dict)
                    or faction.get("status") != "active"
                    or plan.get("status") != "active"
                ):
                    raise CommandRejectedError("faction_owner_invalid")
                plan["last_review_at"] = str(latest_due)
                faction_reviews.append(f"{faction_id}@{latest_due}x{compacted}")
                decisions = review_faction(
                    faction_record=faction_record,
                    at=latest_due,
                    compacted_reviews=compacted,
                    policy_book=autonomy_policy,
                )
                if decisions:
                    if world_events_for_time is None:
                        world_events_for_time = self._world_events()
                    for decision in decisions:
                        autonomy_results.append(
                            self._apply_autonomous_decision(
                                decision=decision,
                                at=latest_due,
                                command=command,
                                scheduler=scheduler,
                                world_events=world_events_for_time,
                                record_writes=autonomy_record_writes,
                                faction_record=faction_record,
                            )
                        )
                continue

            if kind == "team.periodic_review":
                owner_ref = payload.get("owner_ref")
                team_id = payload.get("team_id") or payload.get("identity")
                if not isinstance(owner_ref, str) or not isinstance(team_id, str):
                    raise CommandRejectedError("team_boundary_invalid")
                if world_events_for_time is None:
                    world_events_for_time = self._world_events()
                review = self._apply_team_autonomy_review(
                    owner_ref=owner_ref,
                    at=latest_due,
                    compacted=compacted,
                    command=command,
                    scheduler=scheduler,
                    policy_book=autonomy_policy,
                    world_events=world_events_for_time,
                    record_writes=autonomy_record_writes,
                )
                team_reviews.append(review)
                continue

            if kind == "canon_pressure.periodic_review":
                pressure_id = payload.get("pressure_id")
                if not isinstance(pressure_id, str) or not pressure_id.startswith("pressure_"):
                    raise CommandRejectedError("canon_pressure_boundary_invalid")
                if pressures is None:
                    try:
                        loaded = self.repository.read_json(self.pressures_path)
                    except (FileNotFoundError, ValueError) as exc:
                        raise CommandRejectedError("canon_pressure_registry_invalid") from exc
                    if not isinstance(loaded, dict):
                        raise CommandRejectedError("canon_pressure_registry_invalid")
                    pressures = copy.deepcopy(loaded)
                pressure_map = pressures.get("pressures")
                pressure = pressure_map.get(pressure_id) if isinstance(pressure_map, dict) else None
                if not isinstance(pressure, dict) or pressure.get("id") != pressure_id:
                    raise CommandRejectedError("canon_pressure_registry_invalid")
                boundary = pressure.get("next_boundary")
                constraints = pressure.get("constraints")
                host_id = "host.canon_pressure." + pressure_id
                host = scheduler.hosts.get(host_id)
                if (
                    pressure.get("status") not in _ACTIVE_PRESSURE_STATUSES
                    or not isinstance(boundary, dict)
                    or boundary.get("host_ref") != host_id
                    or not isinstance(constraints, Mapping)
                    or host is None
                ):
                    raise CommandRejectedError("canon_pressure_boundary_invalid")
                # A canon pressure is a conditional pressure, never a forced
                # future.  Periodic review may keep the pressure current but
                # cannot manufacture the published outcome or block years of
                # world time merely because the pressure has actors/resources.
                # Material consequences must emerge through ordinary missions,
                # information, faction actions, or explicit player boundaries.
                boundary["settled_through"] = str(latest_due)
                boundary["due_at"] = (
                    None if host.state.next_due is None else str(host.state.next_due)
                )
                if world_events_for_time is None:
                    world_events_for_time = self._world_events()
                self._append_internal_event(
                    world_events_for_time,
                    command=command,
                    identity=f"{pressure_id}:{latest_due}:pressure",
                    kind="canon_pressure_reviewed",
                    at=latest_due,
                    host_refs=(pressure_id,),
                    actor_refs=tuple(x for x in pressure.get("actors", []) if isinstance(x, str)),
                    material_consequence_refs=(f"conditional_pressure:{pressure_id}",),
                    classification="restricted",
                    audience_refs=(),
                    source_refs=tuple(x for x in pressure.get("source_refs", []) if isinstance(x, str)),
                )
                pressure_reviews.append(f"{pressure_id}@{latest_due}x{compacted}")
                continue

            if kind == "economy.periodic_review":
                owner_ref = payload.get("owner_ref")
                target_host = fact.get("target_host")
                if owner_ref != _ECONOMY_WORLD_PATH or not isinstance(target_host, str):
                    raise CommandRejectedError("economy_boundary_invalid")
                if owner_ref not in world_registry_writes:
                    try:
                        loaded_economy = self.repository.read_json(owner_ref)
                    except (FileNotFoundError, ValueError) as exc:
                        raise CommandRejectedError("economy_state_invalid") from exc
                    if not isinstance(loaded_economy, dict) or loaded_economy.get("schema") != "shinobi-world-registry":
                        raise CommandRejectedError("economy_state_invalid")
                    world_registry_writes[owner_ref] = copy.deepcopy(loaded_economy)
                economy_record = world_registry_writes[owner_ref]
                economy_payload = economy_record.get("payload")
                economy_root = economy_payload.get("economies_and_mission_markets") if isinstance(economy_payload, dict) else None
                finance = economy_root.get("finance") if isinstance(economy_root, dict) else None
                flows = finance.get("recurring_flows") if isinstance(finance, dict) else None
                arrears = finance.get("arrears_ryo") if isinstance(finance, dict) else None
                if not isinstance(finance, dict) or not isinstance(flows, list) or not isinstance(arrears, dict):
                    raise CommandRejectedError("economy_state_invalid")
                if economy_inventory_write is None:
                    try:
                        loaded_inventory = self.repository.read_json(_INVENTORY_REGISTRY_PATH)
                    except (FileNotFoundError, ValueError) as exc:
                        raise CommandRejectedError("economy_inventory_invalid") from exc
                    if not isinstance(loaded_inventory, dict):
                        raise CommandRejectedError("economy_inventory_invalid")
                    economy_inventory_write = copy.deepcopy(loaded_inventory)
                    autonomy_record_writes[_INVENTORY_REGISTRY_PATH] = economy_inventory_write
                holders = economy_inventory_write.get("holders")
                if not isinstance(holders, dict):
                    raise CommandRejectedError("economy_inventory_invalid")
                mechanics = self._economy_mechanics()
                macro_rules = mechanics.get("macro_rules")
                catchup_cap_milli = macro_rules.get("arrears_catchup_cap_milli") if isinstance(macro_rules, Mapping) else None
                if isinstance(catchup_cap_milli, bool) or not isinstance(catchup_cap_milli, int) or catchup_cap_milli < 0 or catchup_cap_milli > 5000:
                    raise CommandRejectedError("economy_mechanics_invalid")
                ordered_flows = sorted(
                    flows,
                    key=lambda row: (
                        row.get("priority", 0) if isinstance(row, Mapping) else 0,
                        row.get("id", "") if isinstance(row, Mapping) else "",
                    ),
                )
                period_expected = period_paid = period_unpaid = 0
                arrears_before = sum(
                    value for value in arrears.values()
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0
                )
                partial_flow_count = 0
                for _period in range(compacted):
                    for flow in ordered_flows:
                        if not isinstance(flow, Mapping):
                            raise CommandRejectedError("economy_state_invalid")
                        flow_id = flow.get("id")
                        source_ref = flow.get("source_ref")
                        destination_ref = flow.get("destination_ref")
                        amount = flow.get("amount_ryo")
                        if (
                            not isinstance(flow_id, str) or not flow_id
                            or not isinstance(source_ref, str) or not source_ref
                            or not isinstance(destination_ref, str) or not destination_ref
                            or source_ref == destination_ref
                            or isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0
                        ):
                            raise CommandRejectedError("economy_state_invalid")
                        old_arrears = arrears.get(flow_id, 0)
                        if isinstance(old_arrears, bool) or not isinstance(old_arrears, int) or old_arrears < 0:
                            raise CommandRejectedError("economy_state_invalid")
                        arrears_catchup_due = min(old_arrears, (amount * catchup_cap_milli) // 1000)
                        due = amount + arrears_catchup_due
                        source = holders.setdefault(source_ref, {})
                        destination = holders.setdefault(destination_ref, {})
                        if not isinstance(source, dict) or not isinstance(destination, dict):
                            raise CommandRejectedError("economy_inventory_invalid")
                        available = source.get("currency.ryo", 0)
                        existing = destination.get("currency.ryo", 0)
                        if (
                            isinstance(available, bool) or not isinstance(available, int) or available < 0
                            or isinstance(existing, bool) or not isinstance(existing, int) or existing < 0
                        ):
                            raise CommandRejectedError("economy_inventory_invalid")
                        paid = min(available, due)
                        current_paid = min(paid, amount)
                        arrears_paid = max(0, paid - amount)
                        new_arrears = max(0, old_arrears - arrears_paid) + (amount - current_paid)
                        source["currency.ryo"] = available - paid
                        destination["currency.ryo"] = existing + paid
                        if new_arrears:
                            arrears[flow_id] = new_arrears
                        else:
                            arrears.pop(flow_id, None)
                        period_expected += amount
                        period_paid += paid
                        period_unpaid += amount - current_paid
                        if paid < due:
                            partial_flow_count += 1
                host = scheduler.hosts.get(target_host)
                successor = host.state.next_due if host is not None else None
                finance["last_settled_at"] = str(latest_due)
                finance["next_due_at"] = None if successor is None else str(successor)
                economy_root["last_settled_at"] = str(latest_due)
                arrears_after = sum(
                    value for value in arrears.values()
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0
                )
                finance["last_period"] = {
                    "settled_at": str(latest_due),
                    "expected_ryo": period_expected,
                    "paid_ryo": period_paid,
                    "unpaid_current_ryo": period_unpaid,
                    "arrears_before_ryo": arrears_before,
                    "arrears_after_ryo": arrears_after,
                    "flow_count": len(ordered_flows) * compacted,
                    "partial_flow_count": partial_flow_count,
                }
                review = {
                    "at": str(latest_due),
                    "compacted_months": compacted,
                    "expected_ryo": period_expected,
                    "paid_ryo": period_paid,
                    "arrears_after_ryo": arrears_after,
                }
                economy_reviews.append(review)
                if world_events_for_time is None:
                    world_events_for_time = self._world_events()
                self._append_internal_event(
                    world_events_for_time, command=command, identity=f"economy:{latest_due}",
                    kind="economy_period_settled", at=latest_due, host_refs=(target_host,),
                    affected_owner_refs=(_ECONOMY_WORLD_PATH, _INVENTORY_REGISTRY_PATH),
                    material_consequence_refs=(
                        f"expected_ryo:{period_expected}", f"paid_ryo:{period_paid}", f"arrears_ryo:{arrears_after}",
                    ), classification="restricted", audience_refs=(), source_refs=(_ECONOMY_WORLD_PATH,),
                )
                continue

            if kind == "world_registry.periodic_review":
                owner_ref = payload.get("owner_ref")
                target_host = fact.get("target_host")
                if not isinstance(owner_ref, str) or not isinstance(target_host, str):
                    raise CommandRejectedError("world_registry_boundary_invalid")
                if owner_ref not in world_registry_writes:
                    try:
                        loaded = self.repository.read_json(owner_ref)
                    except (FileNotFoundError, ValueError) as exc:
                        raise CommandRejectedError("world_registry_invalid") from exc
                    if not isinstance(loaded, dict) or loaded.get("schema") != "shinobi-world-registry":
                        raise CommandRejectedError("world_registry_invalid")
                    world_registry_writes[owner_ref] = copy.deepcopy(loaded)
                registry_record = world_registry_writes[owner_ref]
                payload_record = registry_record.get("payload")
                institutions = payload_record.get("institutions") if isinstance(payload_record, Mapping) else None
                if not isinstance(institutions, list):
                    raise CommandRejectedError("world_registry_invalid")
                host = scheduler.hosts.get(target_host)
                successor = host.state.next_due if host is not None else None
                autonomy_candidates: list[Dict[str, Any]] = []
                for institution in institutions:
                    if not isinstance(institution, dict):
                        raise CommandRejectedError("world_registry_invalid")
                    settlement = institution.get("settlement")
                    if not isinstance(settlement, dict):
                        continue
                    settlement["last_settled_at"] = str(latest_due)
                    settlement["next_due_at"] = None if successor is None else str(successor)
                    autonomy_candidates.append(institution)
                # One bundled host may contain dozens of institutions.  Keep
                # mutable work bounded by advancing all cursors in the single
                # owner while selecting at most eight institutions for material
                # autonomous development.  Academy pipelines are always
                # included because they conserve people across population and
                # force authorities.
                pipeline = [row for row in autonomy_candidates if autonomy_policy.institution_assignment(str(row.get("id")))]
                remaining = [row for row in autonomy_candidates if row not in pipeline]
                stable = int.from_bytes(hashlib.sha256(f"{owner_ref}\x00{latest_due}".encode()).digest()[:8], "big")
                if remaining:
                    offset = stable % len(remaining)
                    remaining = remaining[offset:] + remaining[:offset]
                selected_institutions = (pipeline + remaining)[:8]
                if selected_institutions:
                    if world_events_for_time is None:
                        world_events_for_time = self._world_events()
                    for institution in selected_institutions:
                        autonomy_results.append(
                            self._apply_institution_autonomy_review(
                                institution=institution,
                                at=latest_due,
                                compacted=compacted,
                                command=command,
                                policy_book=autonomy_policy,
                                world_events=world_events_for_time,
                                record_writes=autonomy_record_writes,
                            )
                        )
                world_registry_reviews.append(f"{owner_ref}@{latest_due}x{compacted}")
                continue

            if kind == "house.periodic_review":
                owner_ref = payload.get("owner_ref")
                target_host = fact.get("target_host")
                if not isinstance(owner_ref, str) or not isinstance(target_host, str):
                    raise CommandRejectedError("house_boundary_invalid")
                if owner_ref not in house_writes:
                    try:
                        loaded = self.repository.read_json(owner_ref)
                    except (FileNotFoundError, ValueError) as exc:
                        raise CommandRejectedError("house_owner_invalid") from exc
                    if not isinstance(loaded, dict) or loaded.get("schema") != "house":
                        raise CommandRejectedError("house_owner_invalid")
                    house_writes[owner_ref] = copy.deepcopy(loaded)
                house = house_writes[owner_ref]
                process = house.get("operating_process")
                if not isinstance(process, dict) or process.get("status") != "active":
                    raise CommandRejectedError("house_owner_invalid")
                host = scheduler.hosts.get(target_host)
                successor = host.state.next_due if host is not None else None
                process["last_review"] = str(latest_due)
                process["next_review"] = None if successor is None else str(successor)
                process["quiet_run_count"] = int(process.get("quiet_run_count", 0)) + compacted
                house_reviews.append(f"{owner_ref}@{latest_due}x{compacted}")
                continue

            if kind == "person_continuity.periodic_review":
                owner_ref = payload.get("owner_ref")
                if owner_ref != _PERSON_CONTINUITY_PATH:
                    raise CommandRejectedError("person_continuity_boundary_invalid")
                if continuity_write is None:
                    try:
                        loaded = self.repository.read_json(_PERSON_CONTINUITY_PATH)
                    except (FileNotFoundError, ValueError) as exc:
                        raise CommandRejectedError("person_continuity_registry_invalid") from exc
                    if not isinstance(loaded, dict) or loaded.get("schema") != "person-continuity-registry":
                        raise CommandRejectedError("person_continuity_registry_invalid")
                    continuity_write = copy.deepcopy(loaded)
                entries = continuity_write.get("people")
                if not isinstance(entries, dict):
                    raise CommandRejectedError("person_continuity_registry_invalid")
                progressed = 0
                elapsed_days_total = 0
                for person_ref, row in entries.items():
                    if not isinstance(person_ref, str) or not isinstance(row, dict) or row.get("person_ref") != person_ref:
                        raise CommandRejectedError("person_continuity_registry_invalid")
                    try:
                        prior = CampaignTime.parse(row.get("resolved_through"))
                    except (TypeError, ValueError) as exc:
                        raise CommandRejectedError("person_continuity_registry_invalid") from exc
                    if prior >= latest_due:
                        continue
                    elapsed_days = max(0, int((_campaign_datetime(latest_due) - _campaign_datetime(prior)).total_seconds() // 86400))
                    row["resolved_through"] = str(latest_due)
                    row["life_experience_days"] = int(row.get("life_experience_days", 0)) + elapsed_days
                    row["review_count"] = int(row.get("review_count", 0)) + 1
                    row["career_review_cycles"] = int(row.get("career_review_cycles", 0)) + compacted
                    progressed += 1
                    elapsed_days_total += elapsed_days
                continuity_write["resolved_through"] = str(latest_due)
                continuity_reviews.append({
                    "at": str(latest_due),
                    "compacted_years": compacted,
                    "persistent_people_progressed": progressed,
                    "elapsed_person_days": elapsed_days_total,
                })
                continue

            if kind == "commitment.due":
                commitment_id = payload.get("commitment_id")
                if not isinstance(commitment_id, str) or not commitment_id.startswith("commitment."):
                    raise CommandRejectedError("commitment_due_boundary_invalid")
                if commitment_write is None:
                    try:
                        loaded_commitments = self.repository.read_json(_COMMITMENT_REGISTRY_PATH)
                    except (FileNotFoundError, ValueError) as exc:
                        raise CommandRejectedError("commitment_registry_invalid") from exc
                    if not isinstance(loaded_commitments, dict):
                        raise CommandRejectedError("commitment_registry_invalid")
                    commitment_write = copy.deepcopy(loaded_commitments)
                records = commitment_write.get("records")
                if not isinstance(records, list):
                    raise CommandRejectedError("commitment_registry_invalid")
                matches = [row for row in records if isinstance(row, dict) and row.get("id") == commitment_id]
                if len(matches) != 1:
                    raise CommandRejectedError("commitment_due_boundary_invalid")
                record = matches[0]
                if record.get("status") == "active":
                    record["status"] = "overdue"
                    record["resolution_summary"] = "Due time passed without persisted completion, failure, or cancellation evidence."
                host_id = str(fact.get("target_host"))
                scheduler.hosts.pop(host_id, None)
                scheduler.metrics.update({
                    "pending_event_count": len(scheduler.queue),
                    "host_count": len(scheduler.hosts),
                })
                commitment_reviews.append({
                    "commitment_id": commitment_id,
                    "due_at": str(latest_due),
                    "status": record.get("status"),
                })
                if world_events_for_time is None:
                    world_events_for_time = self._world_events()
                self._append_semantic_event(
                    world_events_for_time, command=command, kind="commitment_overdue", at=latest_due,
                    host_refs=tuple(x for x in (record.get("host_ref"),) if isinstance(x, str)),
                    actor_refs=tuple(x for x in (record.get("subject_ref"), record.get("target_ref")) if isinstance(x, str)),
                    affected_owner_refs=(_COMMITMENT_REGISTRY_PATH,),
                    material_consequence_refs=(commitment_id, "status:overdue"),
                    classification=str(record.get("visibility") or "restricted"),
                    audience_refs=tuple(x for x in (record.get("subject_ref"), record.get("target_ref")) if isinstance(x, str)),
                    reducer_ref="shinobi_runtime.commands.commitment_due_settlement",
                )
                continue

            if kind == "population.periodic_review":
                owner_ref = payload.get("owner_ref")
                policy_ref = payload.get("policy_ref")
                if owner_ref != _POPULATION_REGISTRY_PATH or not isinstance(policy_ref, str):
                    raise CommandRejectedError("population_demography_boundary_invalid")
                if population_write is None:
                    existing_population = autonomy_record_writes.get(_POPULATION_REGISTRY_PATH)
                    if existing_population is not None:
                        population_write = existing_population
                    else:
                        try:
                            loaded = self.repository.read_json(_POPULATION_REGISTRY_PATH)
                        except (FileNotFoundError, ValueError) as exc:
                            raise CommandRejectedError("population_registry_invalid") from exc
                        if not isinstance(loaded, dict):
                            raise CommandRejectedError("population_registry_invalid")
                        population_write = copy.deepcopy(loaded)
                        autonomy_record_writes[_POPULATION_REGISTRY_PATH] = population_write
                population_reviews.append(
                    self._settle_population_demography(
                        population_write,
                        at=latest_due,
                        compacted_years=compacted,
                        policy_ref=policy_ref,
                    )
                )
                continue

            raise CommandRejectedError("causal_scheduler_event_unhandled")

        scene["world_time"] = str(target)
        location = scene.get("location_id")
        if not isinstance(location, str) or not location:
            raise CommandRejectedError("campaign_scene_invalid")

        interrupt_event_id = None
        if catchup.interrupt is None:
            scene["scene_summary"] = (
                f"Time advances from {current_time} to {target} at {location}; "
                "no player decision boundary was reached."
            )
            scene["decision_required"] = (
                "The prior unresolved decision surface remains; this wait creates no "
                "additional consequential player choice."
            )
        else:
            visible = catchup.interrupt.visible_context
            causal_interrupt_id = (
                visible.get("event_id") if isinstance(visible, Mapping) else None
            )
            if not isinstance(causal_interrupt_id, str):
                raise CommandRejectedError("causal_scheduler_interrupt_invalid")
            pending_interrupt = next(
                (
                    event
                    for event in scheduler.queue.snapshot()
                    if event.event_id == causal_interrupt_id
                ),
                None,
            )
            if pending_interrupt is None:
                raise CommandRejectedError("causal_scheduler_interrupt_invalid")
            semantic_interrupt = (
                pending_interrupt.payload.get("scene_event_id")
                or pending_interrupt.payload.get("mission_id")
                or pending_interrupt.payload.get("commitment_id")
                or pending_interrupt.payload.get("identity")
                or causal_interrupt_id
            )
            interrupt_event_id = (
                semantic_interrupt
                if isinstance(semantic_interrupt, str) and semantic_interrupt
                else causal_interrupt_id
            )
            scene["time_passage_allowed"] = False
            scene["scene_summary"] = (
                f"Time reaches {target} at {location}. The causal boundary "
                f"{interrupt_event_id} is due; no player response has been chosen."
            )
            scene["decision_required"] = (
                f"The boundary {interrupt_event_id} requires an explicit player response."
            )

        demographic_event_id = None
        if population_reviews:
            if world_events_for_time is None:
                world_events_for_time = self._world_events()
            total_births = sum(
                row.get("births", 0)
                for review in population_reviews
                for row in review.get("pool_results", [])
                if isinstance(row, Mapping)
            )
            total_deaths = sum(
                row.get("deaths", 0)
                for review in population_reviews
                for row in review.get("pool_results", [])
                if isinstance(row, Mapping)
            )
            demographic_event_id = self._append_semantic_event(
                world_events_for_time, command=command, kind="population_demography_settled", at=target,
                host_refs=("host.population.great_villages",),
                affected_owner_refs=(_POPULATION_REGISTRY_PATH,),
                material_consequence_refs=(f"births:{total_births}", f"deaths:{total_deaths}"),
                classification="restricted",
                audience_refs=(command.actor_id,),
                reducer_ref="shinobi_runtime.commands.population_demography",
            )

        writes: Dict[str, bytes] = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=target)),
            self.scene_path: _json_bytes(scene),
            self.scheduler_path: _json_bytes(scheduler.to_record()),
        }
        if pressures is not None:
            writes[self.pressures_path] = _json_bytes(pressures)
        for owner_ref, faction_record in faction_writes.items():
            writes[owner_ref] = _json_bytes(faction_record)
        for owner_ref, world_record in world_registry_writes.items():
            writes[owner_ref] = _json_bytes(world_record)
        for owner_ref, house_record in house_writes.items():
            writes[owner_ref] = _json_bytes(house_record)
        for owner_ref, autonomous_record in autonomy_record_writes.items():
            writes[owner_ref] = _json_bytes(autonomous_record)
        if population_write is not None:
            writes[_POPULATION_REGISTRY_PATH] = _json_bytes(population_write)
        if continuity_write is not None:
            writes[_PERSON_CONTINUITY_PATH] = _json_bytes(continuity_write)
        if commitment_write is not None:
            writes[_COMMITMENT_REGISTRY_PATH] = _json_bytes(commitment_write)
        if world_events_for_time is not None:
            writes.update(self._world_event_writes(world_events_for_time))
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("time command write set changed after planning")
            self._assert_meta(
                overlay, manifest, meta_path=self.meta_path, command=command, world_time=target
            )
            staged_scene = overlay.read_json(self.scene_path)
            staged_scheduler = CausalSchedulerRegistry.from_record(
                overlay.read_json(self.scheduler_path)
            )
            if staged_scene.get("world_time") != str(target) or staged_scheduler.world_time != target:
                raise ValueError("time command core clocks diverge")
            if catchup.interrupt is not None and staged_scene.get("time_passage_allowed") is not False:
                raise ValueError("causal interrupt did not close time passage")
            if pressures is not None and overlay.read_json(self.pressures_path) != pressures:
                raise ValueError("canon pressure after-image differs from causal review")
            for owner_ref, expected_record in faction_writes.items():
                if owner_ref in expected_paths and overlay.read_json(owner_ref) != expected_record:
                    raise ValueError("faction review after-image differs from plan")
            for owner_ref, expected_record in world_registry_writes.items():
                if owner_ref in expected_paths and overlay.read_json(owner_ref) != expected_record:
                    raise ValueError("world registry review after-image differs from plan")
            for owner_ref, expected_record in house_writes.items():
                if owner_ref in expected_paths and overlay.read_json(owner_ref) != expected_record:
                    raise ValueError("house review after-image differs from plan")
            for owner_ref, expected_record in autonomy_record_writes.items():
                if owner_ref in expected_paths and overlay.read_json(owner_ref) != expected_record:
                    raise ValueError("autonomous owner after-image differs from plan")
            if population_write is not None and overlay.read_json(_POPULATION_REGISTRY_PATH) != population_write:
                raise ValueError("population demographic after-image differs from plan")
            if continuity_write is not None and overlay.read_json(_PERSON_CONTINUITY_PATH) != continuity_write:
                raise ValueError("person continuity after-image differs from plan")
            if commitment_write is not None and overlay.read_json(_COMMITMENT_REGISTRY_PATH) != commitment_write:
                raise ValueError("commitment due after-image differs from plan")
            if world_events_for_time is not None and overlay.read_json(_WORLD_EVENT_REGISTRY_PATH) != world_events_for_time:
                raise ValueError("time semantic history after-image differs from plan")

        return _BuiltPlan(
            code=(
                "advance_time_ready"
                if catchup.interrupt is None
                else "advance_time_interrupt_ready"
            ),
            affected_refs=tuple(sorted(writes)),
            writes=writes,
            result={
                "command_type": command.command_type,
                "world_time": str(target),
                "requested_time": str(requested_target),
                "interrupted": catchup.interrupt is not None,
                "interrupt_event_id": interrupt_event_id,
                "processed_causal_events": list(catchup.processed_event_ids),
                "faction_reviews": sorted(faction_reviews),
                "canon_pressure_reviews": sorted(pressure_reviews),
                "world_registry_reviews": sorted(world_registry_reviews),
                "economy_reviews": [dict(item) for item in economy_reviews],
                "house_reviews": sorted(house_reviews),
                "team_reviews": [dict(item) for item in team_reviews],
                "commitment_reviews": [dict(item) for item in commitment_reviews],
                "autonomous_actions": [dict(item) for item in autonomy_results],
                "population_demographic_reviews": [dict(item) for item in population_reviews],
                "person_continuity_reviews": [dict(item) for item in continuity_reviews],
                "person_recovery_reviews": [dict(item) for item in recovery_reviews],
                "population_demography_event_id": demographic_event_id,
                "scheduler_metrics": dict(scheduler.metrics),
                "named_person_owner_scans": 0,
                "faction_directory_scans": 0,
            },
            validator=validate,
        )

