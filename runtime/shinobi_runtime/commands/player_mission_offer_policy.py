"""Player-offer objective selection independent from generic faction missions.

A faction profile may support abstract mission kinds whose material mechanics are
not yet suitable for a player-facing assignment.  The optional player_offer
objective_cycle narrows only the player offer surface; autonomous faction work
continues to use the generic profile cycle.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Mapping, Optional

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.sim.scheduler import CausalSchedulerRegistry


class PlayerMissionOfferPolicyMixin:
    def _player_offer_objective_cycle(self, faction_id: str) -> tuple[str, ...]:
        try:
            _profile, assignment = self._autonomy_policy_book().faction_context(faction_id)
        except (TypeError, ValueError, CommandRejectedError):
            return ()
        config = assignment.get("player_offer") if isinstance(assignment, Mapping) else None
        raw = config.get("objective_cycle") if isinstance(config, Mapping) else None
        if raw is None:
            return ()
        if (
            not isinstance(raw, list)
            or not raw
            or len(raw) > 16
            or any(not isinstance(value, str) or not value for value in raw)
            or len(set(raw)) != len(raw)
        ):
            raise CommandRejectedError("player_offer_objective_cycle_invalid")
        return tuple(raw)

    def _mission_objective_kind(
        self,
        payload: Mapping[str, Any],
        faction_id: str,
        at: CampaignTime,
    ) -> str:
        cycle = getattr(self, "_active_player_offer_objective_cycle", ())
        if not cycle:
            return super()._mission_objective_kind(payload, faction_id, at)
        identity = f"{faction_id}\x00{at}\x00player-offer-objective"
        digest = hashlib.sha256(identity.encode("utf-8")).digest()
        return cycle[int.from_bytes(digest[:8], "big") % len(cycle)]

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
        faction_id = (
            decision.payload.get("faction_id")
            if hasattr(decision, "payload") and isinstance(decision.payload, Mapping)
            else None
        )
        cycle = self._player_offer_objective_cycle(faction_id) if isinstance(faction_id, str) else ()
        prior = getattr(self, "_active_player_offer_objective_cycle", None)
        self._active_player_offer_objective_cycle = cycle
        try:
            return super()._maybe_offer_player_mission(
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
                    del self._active_player_offer_objective_cycle
                except AttributeError:
                    pass
            else:
                self._active_player_offer_objective_cycle = prior


__all__ = ["PlayerMissionOfferPolicyMixin"]
