"""Persistent zero-time personal retinue command surface."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _json_bytes
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.martial_world.live_state import roster_person
from shinobi_runtime.martial_world.scheduler import upsert_one_off_event
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest

_DEPLOYMENTS = "state/martial-world/deployments.json"
_SCHEDULE = "state/martial-world/scheduler.json"
_AUTHORIZED_CHOOSER_OFFICES = {"leader", "deputy_leader", "chief_instructor", "chief_martial_instructor"}


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
            if requested_count not in {0, 2, 3}:
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
            rows[retinue_ref] = {
                "deployment_ref": retinue_ref,
                "operation_kind": "standing_retinue",
                "faction_ref": faction_ref,
                "leader_ref": command.actor_id,
                # chooser_refs is the authority. chooser_ref is a temporary
                # compatibility projection for the existing assignment reducer
                # until that reducer is extracted from time_progression.
                "chooser_refs": chooser_refs,
                "chooser_ref": chooser_refs[0],
                "requested_count": requested_count,
                "member_refs": [],
                "member_roles": {},
                "status": "assignment_pending",
                "requested_at": requested_at.isoformat(),
                "training_policy": "house_curriculum_idle_field_experience_active",
            }
            try:
                schedule = upsert_one_off_event(
                    self.repository.read_json(_SCHEDULE),
                    {
                        "event_id": f"retinue_assignment_review:{retinue_ref}",
                        "kind": "retinue_assignment_review",
                        "owner_ref": retinue_ref,
                        "retinue_ref": retinue_ref,
                        "chooser_refs": chooser_refs,
                        "chooser_ref": chooser_refs[0],
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
                    "chooser_discretion_2_to_3": requested_count == 0,
                    "assignment_review_at": due_at.isoformat(),
                    "time_reserved_hours": 0,
                },
                validator=validate,
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
