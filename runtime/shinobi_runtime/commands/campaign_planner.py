"""Campaign planner extensions for reusable player-facing workflow gaps.

Keep the generic repository planner as the mechanical base. This subclass adds
only mechanics that require campaign-wide policy coordination across existing
domains, while preserving all legacy command behavior when no registered policy
applies.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Mapping, Optional, Tuple

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _exact_payload, _json_bytes, _stable_id
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.paths import INVENTORY_REGISTRY_PATH as _INVENTORY_REGISTRY_PATH
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.domain.equipment import actor_team_policy_roles, loadout_refit_policy
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest


class CampaignCommandPlanner(RepositoryCommandPlanner):
    """Repository planner plus narrowly registered cross-stock team fitting."""

    def _inventory_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        if command.payload.get("action") != "refit":
            return super()._inventory_resolution(command, meta, current_time)

        raw_loadout = command.payload.get("loadout_ref")
        if not isinstance(raw_loadout, str) or not raw_loadout:
            return super()._inventory_resolution(command, meta, current_time)
        try:
            policy = loadout_refit_policy(self.repository, raw_loadout)
        except (FileNotFoundError, TypeError, ValueError) as exc:
            raise CommandRejectedError("inventory_refit_policy_invalid") from exc
        if policy is None:
            return super()._inventory_resolution(command, meta, current_time)
        return self._registered_team_refit_resolution(
            command,
            meta,
            current_time,
            policy=policy,
        )

    def _registered_team_refit_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
        *,
        policy: Mapping[str, Any],
    ) -> _BuiltPlan:
        _exact_payload(
            command.payload,
            (
                "action", "item_ref", "quantity", "stock_ref", "holder_ref",
                "loadout_ref", "contract_ref", "summary", "visibility",
            ),
            command.command_type,
        )
        if (
            command.payload.get("action") != "refit"
            or command.payload.get("item_ref") is not None
            or command.payload.get("quantity") is not None
            or command.payload.get("contract_ref") is not None
        ):
            raise CommandRejectedError("inventory_action_fields_invalid")

        holder_ref = _stable_id(command.payload.get("holder_ref"), "inventory_holder_invalid")
        loadout_ref = _stable_id(command.payload.get("loadout_ref"), "inventory_loadout_invalid")
        stock_ref = _stable_id(
            command.payload.get("stock_ref"),
            "inventory_stock_invalid",
            prefix="stock.",
        )
        summary = command.payload.get("summary")
        visibility = command.payload.get("visibility")
        if not isinstance(summary, str) or not summary or len(summary) > 1000:
            raise CommandRejectedError("inventory_summary_invalid")
        if visibility not in ("public", "restricted", "secret"):
            raise CommandRejectedError("inventory_visibility_invalid")

        policy_holder = policy.get("holder_ref")
        team_ref = policy.get("assignment_ref")
        allowed_roles = policy.get("authorized_team_roles")
        supply_refs = policy.get("supply_stock_refs")
        if (
            policy_holder != holder_ref
            or not isinstance(team_ref, str)
            or not team_ref.startswith("team.")
            or not isinstance(allowed_roles, list)
            or not allowed_roles
            or not isinstance(supply_refs, list)
            or not supply_refs
            or stock_ref != supply_refs[0]
        ):
            raise CommandRejectedError("inventory_refit_policy_mismatch")

        _team_path, team = self._exact_team(team_ref)
        members = team.get("member_refs") if isinstance(team, Mapping) else None
        if (
            team.get("status") != "active"
            or not isinstance(members, list)
            or holder_ref not in members
        ):
            raise CommandRejectedError("inventory_refit_policy_mismatch")
        actor_roles = actor_team_policy_roles(
            team,
            actor_ref=command.actor_id,
            holder_ref=holder_ref,
        )
        if not actor_roles.intersection(allowed_roles):
            raise CommandRejectedError("inventory_refit_not_authorized")
        authority_basis = "team_refit_policy:" + ",".join(sorted(actor_roles.intersection(allowed_roles)))

        char_path, char = self._resolve_actor_for_write(holder_ref)
        if char.get("schema") != "shinobi_character":
            raise CommandRejectedError("inventory_refit_requires_exact_character")

        try:
            inventory = copy.deepcopy(self.repository.read_json(_INVENTORY_REGISTRY_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("inventory_registry_invalid") from exc
        holdings = inventory.get("holders") if isinstance(inventory, dict) else None
        holder = holdings.get(holder_ref) if isinstance(holdings, dict) else None
        if not isinstance(holder, dict):
            raise CommandRejectedError("inventory_holder_invalid")

        stock_records: Dict[str, Tuple[str, Dict[str, Any], str]] = {}
        for supply_ref in supply_refs:
            if not isinstance(supply_ref, str) or not supply_ref.startswith("stock."):
                raise CommandRejectedError("inventory_refit_policy_invalid")
            path, record, owner_ref = self._stock_record(supply_ref)
            stock_records[supply_ref] = (path, record, owner_ref)
        if len({path for path, _record, _owner in stock_records.values()}) != len(stock_records):
            raise CommandRejectedError("inventory_refit_policy_invalid")

        def stock_slot(supply_ref: str, item_ref: str) -> Optional[Tuple[Dict[str, Any], str]]:
            record = stock_records[supply_ref][1]
            try:
                return self._stock_item_key(record, item_ref)
            except CommandRejectedError as exc:
                if exc.code == "inventory_item_not_tracked_by_stock":
                    return None
                raise

        old_ref = char.get("equipment_loadout_id")
        old_quantities = self._loadout_quantities(old_ref) if isinstance(old_ref, str) and old_ref else {}
        target_quantities = self._loadout_quantities(loadout_ref)
        movements: list[Dict[str, Any]] = []
        consequences: list[str] = []

        for item_ref in sorted(set(old_quantities) | set(target_quantities)):
            current = holder.get(item_ref, 0)
            if isinstance(current, bool) or not isinstance(current, int) or current < 0:
                raise CommandRejectedError("inventory_registry_invalid")
            old_standard = old_quantities.get(item_ref, 0)
            target = target_quantities.get(item_ref, 0)
            extras = max(0, current - old_standard)
            desired = target + extras
            delta = desired - current

            if delta > 0:
                remaining = delta
                for supply_ref in supply_refs:
                    slot = stock_slot(supply_ref, item_ref)
                    if slot is None:
                        continue
                    container, key = slot
                    available = container.get(key)
                    if isinstance(available, bool) or not isinstance(available, int) or available < 0:
                        raise CommandRejectedError("inventory_stock_invalid")
                    take = min(available, remaining)
                    if take <= 0:
                        continue
                    container[key] = available - take
                    remaining -= take
                    movements.append(
                        {"direction": "issue", "stock_ref": supply_ref, "item_ref": item_ref, "quantity": take}
                    )
                    consequences.append(f"issue:{item_ref}:{take}:{supply_ref}->{holder_ref}")
                    if remaining == 0:
                        break
                if remaining:
                    raise CommandRejectedError("inventory_stock_insufficient")
                holder[item_ref] = current + delta

            elif delta < 0:
                returned = -delta
                destination: Optional[Tuple[str, Dict[str, Any], str]] = None
                for supply_ref in supply_refs:
                    slot = stock_slot(supply_ref, item_ref)
                    if slot is not None:
                        container, key = slot
                        destination = (supply_ref, container, key)
                        break
                if destination is None:
                    raise CommandRejectedError("inventory_item_not_tracked_by_stock")
                supply_ref, container, key = destination
                stock_count = container.get(key, 0)
                if isinstance(stock_count, bool) or not isinstance(stock_count, int) or stock_count < 0:
                    raise CommandRejectedError("inventory_stock_invalid")
                holder[item_ref] = current - returned
                if holder[item_ref] == 0:
                    holder.pop(item_ref)
                container[key] = stock_count + returned
                movements.append(
                    {"direction": "return", "stock_ref": supply_ref, "item_ref": item_ref, "quantity": returned}
                )
                consequences.append(f"return:{item_ref}:{returned}:{holder_ref}->{supply_ref}")

        char["equipment_loadout_id"] = loadout_ref
        consequences.append(f"refit:{holder_ref}:{old_ref}->{loadout_ref}")

        world_events = self._world_events()
        affected = [_INVENTORY_REGISTRY_PATH, char_path]
        writes: Dict[str, bytes] = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            _INVENTORY_REGISTRY_PATH: _json_bytes(inventory),
            char_path: _json_bytes(char),
        }
        for _supply_ref, (path, record, _owner_ref) in stock_records.items():
            writes[path] = _json_bytes(record)
            affected.append(path)

        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind="inventory_changed",
            at=current_time,
            host_refs=(),
            actor_refs=(command.actor_id,),
            affected_owner_refs=tuple(sorted(set(affected))),
            material_consequence_refs=tuple(consequences),
            classification=visibility,
            audience_refs=(command.actor_id, holder_ref),
            reducer_ref="shinobi_runtime.commands.inventory_resolution.registered_team_refit",
        )
        writes.update(self._world_event_writes(world_events))
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("registered team refit write set changed after planning")
            self._assert_meta(
                overlay,
                manifest,
                meta_path=self.meta_path,
                command=command,
                world_time=current_time,
            )
            if overlay.read_json(_INVENTORY_REGISTRY_PATH) != inventory:
                raise ValueError("registered team refit inventory after-image mismatch")
            if overlay.read_json(char_path) != char:
                raise ValueError("registered team refit character after-image mismatch")
            for _supply_ref, (path, record, _owner_ref) in stock_records.items():
                if overlay.read_json(path) != record:
                    raise ValueError("registered team refit stock after-image mismatch")

        return _BuiltPlan(
            code="inventory_resolution_ready",
            affected_refs=expected_paths,
            writes=writes,
            result={
                "command_type": command.command_type,
                "action": "refit",
                "holder_ref": holder_ref,
                "loadout_ref": loadout_ref,
                "authority_basis": authority_basis,
                "assignment_ref": team_ref,
                "primary_stock_ref": stock_ref,
                "supply_stock_refs": list(supply_refs),
                "supply_movements": movements,
                "consequences": consequences,
                "semantic_event_id": event_id,
            },
            validator=validate,
        )
