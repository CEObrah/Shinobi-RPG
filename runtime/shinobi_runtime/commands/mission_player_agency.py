"""Player-agency extensions for mission lifecycle transitions."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Tuple

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _exact_payload, _json_bytes, _stable_id
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.paths import INVENTORY_REGISTRY_PATH as _INVENTORY_REGISTRY_PATH
from shinobi_runtime.reducers.missions import (
    MissionTransitionError,
    SettlementConflictError,
    settle_mission,
    transition_mission,
)
from shinobi_runtime.sim.events import CampaignTime


class MissionPlayerAgencyMixin:
    """Let an offered mission participant decline before accepting it.

    Mission authority retains exclusive power to abort accepted/active work.
    This narrow exception only applies while the mission is still ``offered``
    and therefore represents refusal of a proposed assignment rather than
    unilateral cancellation of an undertaken mission.
    """

    def _mission_transition(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        if command.payload.get("target_state") != "aborted":
            return super()._mission_transition(command, meta, current_time)

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
        path, owner = self._read_mission(
            mission_id,
            actor_id=command.actor_id,
            current_time=current_time,
        )
        participant_decline = (
            owner.mission.state == "offered"
            and command.actor_id in owner.mission.participant_refs
        )
        if not participant_decline:
            return super()._mission_transition(command, meta, current_time)

        try:
            transitioned = transition_mission(
                owner.mission,
                "aborted",
                reason_ref="command." + command.digest,
            )
            transitioned = settle_mission(
                transitioned,
                "settle." + command.digest,
            ).mission
            updated = owner.with_mission(transitioned, effective_at=current_time)
        except (
            MissionTransitionError,
            SettlementConflictError,
            TypeError,
            ValueError,
        ) as exc:
            raise CommandRejectedError("mission_transition_invalid") from exc

        settlement_inventory: Optional[dict[str, Any]] = None
        settlement_consequences: Tuple[str, ...] = ()
        if updated.mission.settlement is not None:
            settlement_inventory, settlement_consequences = (
                self._mission_settlement_inventory(updated)
            )
        summary = (
            f"Mission {mission_id} is declined and closed at {current_time}."
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
                "transition_kind": "participant_decline",
                "settlement_transfers": list(settlement_consequences),
            },
            extra_writes=(
                {_INVENTORY_REGISTRY_PATH: _json_bytes(settlement_inventory)}
                if settlement_inventory is not None
                else None
            ),
            extra_material_consequence_refs=settlement_consequences,
        )


__all__ = ["MissionPlayerAgencyMixin"]