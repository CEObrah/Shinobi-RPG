"""Persistent zero-time personal travel-team command surface."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _json_bytes
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.martial_world.live_state import roster_person
from shinobi_runtime.martial_world.retinues import permanent_team_member_eligible
from shinobi_runtime.martial_world.retinue_support import provision_retinue_role_issue
from shinobi_runtime.martial_world.faction_state import inventory_path as faction_inventory_path
from shinobi_runtime.martial_world.scheduler import upsert_one_off_event
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest

_DEPLOYMENTS = "state/martial-world/deployments.json"
_EQUIPMENT = "state/martial-world/equipment-ledger.json"
_SCHEDULE = "state/martial-world/scheduler.json"
_AUTHORIZED_CHOOSER_OFFICES = {"leader", "deputy_leader", "chief_martial_instructor"}
_RETINUE_ROLES = {"protective_guard", "scout", "field_medic", "field_deputy", "companion"}
_WEI_RETINUE_DOCTRINE_REF = "doctrine.player_retinue.tang_wei.personal_guard"


def _dt(value: CampaignTime) -> datetime:
    return datetime(value.year, value.month, value.day, value.hour, value.minute, value.second)


def _office_keys(person: Mapping[str, Any]) -> set[str]:
    return {
        str(row).split(":", 1)[0]
        for row in person.get("standing_offices", [])
        if isinstance(row, str)
    }


class JianghuRetinueCommandsMixin:
    def _jianghu_retinue_resolution(
        self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime
    ) -> _BuiltPlan:
        action = str(command.payload.get("action") or "")
        deployments = copy.deepcopy(self.repository.read_json(_DEPLOYMENTS))
        rows = deployments.setdefault("deployments", {})
        if not isinstance(rows, dict):
            raise CommandRejectedError("jianghu_deployment_state_invalid")

        if action == "request":
            retinue_ref = str(command.payload.get("retinue_ref") or "")
            chooser_refs_raw = command.payload.get("chooser_refs")
            if (
                not isinstance(chooser_refs_raw, (list, tuple))
                or not chooser_refs_raw
                or any(not isinstance(ref, str) or not ref for ref in chooser_refs_raw)
                or len(set(chooser_refs_raw)) != len(chooser_refs_raw)
            ):
                raise CommandRejectedError("retinue_chooser_refs_invalid")
            chooser_refs = [str(ref) for ref in chooser_refs_raw]
            try:
                requested_count = int(command.payload.get("requested_count"))
            except (TypeError, ValueError) as exc:
                raise CommandRejectedError("retinue_requested_count_invalid") from exc
            if not retinue_ref or not retinue_ref.startswith("retinue."):
                raise CommandRejectedError("retinue_ref_invalid")
            # A standing retinue has members, not slots. The requested count is
            # only the size of this initial assignment and is not a permanent cap.
            if requested_count <= 0:
                raise CommandRejectedError("retinue_requested_count_invalid")
            if any(
                isinstance(existing, Mapping)
                and existing.get("operation_kind") == "standing_retinue"
                and existing.get("leader_ref") == command.actor_id
                and str(existing.get("status") or "") in {"assignment_pending", "active"}
                for existing in rows.values()
            ):
                raise CommandRejectedError("retinue_already_exists")

            try:
                _actor_path, actor_roster, _actor_ordinal, actor = roster_person(self.repository, command.actor_id)
            except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
                raise CommandRejectedError("retinue_person_not_found") from exc
            faction_ref = str(actor.get("faction_ref") or actor_roster.get("faction_ref") or "")
            if not faction_ref:
                raise CommandRejectedError("retinue_chooser_wrong_faction")
            for chooser_ref in chooser_refs:
                try:
                    _path, chooser_roster, _ordinal, chooser = roster_person(self.repository, chooser_ref)
                except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
                    raise CommandRejectedError("retinue_person_not_found") from exc
                chooser_faction = str(chooser.get("faction_ref") or chooser_roster.get("faction_ref") or "")
                if chooser_faction != faction_ref:
                    raise CommandRejectedError("retinue_chooser_wrong_faction")
                if not (_office_keys(chooser) & _AUTHORIZED_CHOOSER_OFFICES):
                    raise CommandRejectedError("retinue_chooser_not_authorized")

            requested_at = _dt(current_time)
            due_at = requested_at + timedelta(hours=12)
            row = {
                "operation_kind": "standing_retinue",
                "faction_ref": faction_ref,
                "leader_ref": command.actor_id,
                "chooser_refs": chooser_refs,
                "member_refs": [],
                "member_roles": {},
                "status": "assignment_pending",
                "requested_at": requested_at.isoformat(),
                "training_policy": "house_curriculum_idle_field_experience_active",
                "requested_count": requested_count,
            }
            # Bespoke team doctrine is a player-retinue mechanic only. NPC
            # standing teams, if ever created by tooling, remain governed by
            # faction + individual doctrine and do not acquire generated team
            # doctrine records.
            if command.actor_id == str(meta.get("player_id") or ""):
                row["combat_doctrine_ref"] = _WEI_RETINUE_DOCTRINE_REF
            rows[retinue_ref] = row
            try:
                schedule = upsert_one_off_event(
                    self.repository.read_json(_SCHEDULE),
                    {
                        "event_id": f"retinue_assignment_review:{retinue_ref}",
                        "kind": "retinue_assignment_review",
                        "owner_ref": retinue_ref,
                        "retinue_ref": retinue_ref,
                        "chooser_refs": chooser_refs,
                        "due_at": due_at.isoformat(),
                        "requires_player_decision": False,
                    },
                )
            except (FileNotFoundError, TypeError, ValueError) as exc:
                raise CommandRejectedError("jianghu_scheduler_invalid") from exc

            writes = {
                _DEPLOYMENTS: _json_bytes(deployments),
                _SCHEDULE: _json_bytes(schedule),
                self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            }
            writes = self._prune_noop_writes(writes)
            expected = tuple(sorted(writes))

            def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
                if overlay.changed_paths != expected:
                    raise ValueError("retinue request write set changed after planning")
                self._assert_meta(
                    overlay, manifest, meta_path=self.meta_path,
                    command=command, world_time=current_time,
                )
                state = overlay.read_json(_DEPLOYMENTS)
                row = state.get("deployments", {}).get(retinue_ref) if isinstance(state, Mapping) else None
                if not isinstance(row, Mapping) or row.get("status") != "assignment_pending":
                    raise ValueError("retinue request missing after planning")
                if row.get("chooser_refs") != chooser_refs:
                    raise ValueError("retinue joint chooser authority changed after planning")
                schedule_after = overlay.read_json(_SCHEDULE)
                event = schedule_after.get("one_off", {}).get(f"retinue_assignment_review:{retinue_ref}") if isinstance(schedule_after, Mapping) else None
                if not isinstance(event, Mapping) or event.get("due_at") != due_at.isoformat():
                    raise ValueError("retinue assignment review missing after planning")
                if event.get("chooser_refs") != chooser_refs:
                    raise ValueError("retinue assignment review lost joint chooser authority")

            return _BuiltPlan(
                code="retinue_assignment_requested",
                affected_refs=expected,
                writes=writes,
                result={
                    "command_type": "jianghu_retinue_resolution",
                    "action": "request",
                    "retinue_ref": retinue_ref,
                    "chooser_refs": chooser_refs,
                    "requested_count": requested_count,
                    "assignment_member_count": requested_count,
                    "chooser_authorizes_assignment": True,
                    "selection_mode": "deterministic_house_selection",
                    "permanent_travel_team": True,
                    "assignment_review_at": due_at.isoformat(),
                    "time_reserved_hours": 0,
                },
                validator=validate,
            )

        if action in {"add_member", "remove_member"}:
            retinue_ref = str(command.payload.get("retinue_ref") or "")
            member_ref = str(command.payload.get("member_ref") or "")
            row = rows.get(retinue_ref)
            if not isinstance(row, dict) or row.get("operation_kind") != "standing_retinue" or row.get("status") != "active":
                raise CommandRejectedError("retinue_not_active")
            if row.get("leader_ref") != command.actor_id:
                raise CommandRejectedError("retinue_not_owned_by_actor")
            if not member_ref or member_ref == command.actor_id:
                raise CommandRejectedError("retinue_member_invalid")
            member_refs = row.setdefault("member_refs", [])
            member_roles = row.setdefault("member_roles", {})
            if not isinstance(member_refs, list) or not isinstance(member_roles, dict):
                raise CommandRejectedError("retinue_state_invalid")

            role_issue_writes: dict[str, bytes] = {}
            role_issue_result: dict[str, Any] | None = None
            if action == "add_member":
                role = str(command.payload.get("role") or "")
                if role not in _RETINUE_ROLES:
                    raise CommandRejectedError("retinue_role_invalid")
                if member_ref in member_refs:
                    raise CommandRejectedError("retinue_member_already_present")
                try:
                    _leader_path, leader_roster, _leader_ordinal, leader = roster_person(self.repository, command.actor_id)
                    _member_path, member_roster, _member_ordinal, member = roster_person(self.repository, member_ref)
                except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
                    raise CommandRejectedError("retinue_person_not_found") from exc
                leader_faction = str(leader.get("faction_ref") or leader_roster.get("faction_ref") or "")
                member_faction = str(member.get("faction_ref") or member_roster.get("faction_ref") or "")
                if not leader_faction or member_faction != leader_faction or str(row.get("faction_ref") or "") != leader_faction:
                    raise CommandRejectedError("retinue_member_wrong_faction")
                if not permanent_team_member_eligible(leader, member, year=current_time.year):
                    raise CommandRejectedError("retinue_member_not_eligible")
                member_refs.append(member_ref)
                member_roles[member_ref] = role
                inv_path = faction_inventory_path(leader_faction)
                issued = provision_retinue_role_issue(
                    role=role, faction_ref=leader_faction, person_ref=member_ref,
                    inventory=self.repository.read_json(inv_path),
                    equipment_ledger=self.repository.read_json(_EQUIPMENT),
                )
                if issued.get("issued"):
                    role_issue_writes[inv_path] = _json_bytes(issued["inventory_after"])
                    role_issue_writes[_EQUIPMENT] = _json_bytes(issued["equipment_ledger_after"])
                role_issue_result = {
                    "issued": copy.deepcopy(issued.get("issued", {})),
                    "shortfall": copy.deepcopy(issued.get("shortfall", {})),
                    "fully_provisioned": bool(issued.get("fully_provisioned", True)),
                }
                code = "retinue_member_added"
            else:
                if member_ref not in member_refs:
                    raise CommandRejectedError("retinue_member_not_present")
                member_refs[:] = [ref for ref in member_refs if ref != member_ref]
                member_roles.pop(member_ref, None)
                role = None
                code = "retinue_member_removed"

            writes = {
                _DEPLOYMENTS: _json_bytes(deployments),
                self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
                **role_issue_writes,
            }
            writes = self._prune_noop_writes(writes)
            expected = tuple(sorted(writes))

            def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
                if overlay.changed_paths != expected:
                    raise ValueError("retinue membership write set changed after planning")
                self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
                state = overlay.read_json(_DEPLOYMENTS)
                current = state.get("deployments", {}).get(retinue_ref) if isinstance(state, Mapping) else None
                if not isinstance(current, Mapping):
                    raise ValueError("retinue missing after membership change")
                actual = current.get("member_refs", [])
                if action == "add_member" and member_ref not in actual:
                    raise ValueError("retinue member add missing after planning")
                if action == "remove_member" and member_ref in actual:
                    raise ValueError("retinue member removal missing after planning")

            return _BuiltPlan(
                code=code, affected_refs=expected, writes=writes,
                result={
                    "command_type": "jianghu_retinue_resolution", "action": action,
                    "retinue_ref": retinue_ref, "member_ref": member_ref, "role": role,
                    "member_count_after": len(member_refs), "permanent_cap": None, "time_reserved_hours": 0,
                    **({"role_provisioning": role_issue_result} if role_issue_result is not None else {}),
                }, validator=validate,
            )

        if action == "release":
            retinue_ref = str(command.payload.get("retinue_ref") or "")
            row = rows.get(retinue_ref)
            if not isinstance(row, Mapping) or row.get("operation_kind") != "standing_retinue":
                raise CommandRejectedError("retinue_not_found")
            if row.get("leader_ref") != command.actor_id:
                raise CommandRejectedError("retinue_not_owned_by_actor")
            rows.pop(retinue_ref, None)
            try:
                schedule = copy.deepcopy(self.repository.read_json(_SCHEDULE))
                one_off = schedule.get("one_off", {}) if isinstance(schedule, Mapping) else None
                if isinstance(one_off, dict):
                    one_off.pop(f"retinue_assignment_review:{retinue_ref}", None)
            except (FileNotFoundError, TypeError, ValueError) as exc:
                raise CommandRejectedError("jianghu_scheduler_invalid") from exc
            writes = {
                _DEPLOYMENTS: _json_bytes(deployments),
                _SCHEDULE: _json_bytes(schedule),
                self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            }
            writes = self._prune_noop_writes(writes)
            expected = tuple(sorted(writes))

            def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
                if overlay.changed_paths != expected:
                    raise ValueError("retinue release write set changed after planning")
                self._assert_meta(
                    overlay, manifest, meta_path=self.meta_path,
                    command=command, world_time=current_time,
                )
                state = overlay.read_json(_DEPLOYMENTS)
                remaining = state.get("deployments", {}).get(retinue_ref) if isinstance(state, Mapping) else None
                if remaining is not None:
                    raise ValueError("retinue still present after release")

            return _BuiltPlan(
                code="retinue_released",
                affected_refs=expected,
                writes=writes,
                result={
                    "command_type": "jianghu_retinue_resolution",
                    "action": "release",
                    "retinue_ref": retinue_ref,
                    "time_reserved_hours": 0,
                },
                validator=validate,
            )

        raise CommandRejectedError("retinue_action_invalid")


__all__ = ["JianghuRetinueCommandsMixin"]
