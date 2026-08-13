"""Player-offer policy independent from generic faction mission pressure.

A faction profile may support abstract mission kinds whose material mechanics are
not yet suitable for a player-facing assignment. The optional ``player_offer``
policy narrows the player offer surface and may request one offer review per
real faction review. Player offers are evaluated before background NPC mission
capacity so ordinary autonomous work cannot crowd the player out of lawful
tasking. No offer is acceptance, travel, mission start, or player dialogue.
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
            _profile, assignment = self._autonomy_policy_book().faction_context(
                faction_id
            )
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
        """Give an opted-in player offer first refusal on mission demand.

        The base mission reducer historically checked the faction's NPC mission
        capacity before invoking the player-offer path. That allowed unrelated
        background missions to starve a valid player-led team. This production
        layer evaluates the already-authorized offer first. If no offer is
        eligible, normal faction mission generation proceeds unchanged.
        """

        if getattr(decision, "kind", None) == "mission_generate":
            offer = self._maybe_offer_player_mission(
                decision=decision,
                at=at,
                command=command,
                scheduler=scheduler,
                world_events=world_events,
                record_writes=record_writes,
                faction_record=faction_record,
            )
            if offer is not None:
                return offer
        return super()._apply_autonomous_decision(
            decision=decision,
            at=at,
            command=command,
            scheduler=scheduler,
            world_events=world_events,
            record_writes=record_writes,
            faction_record=faction_record,
        )


__all__ = ["PlayerMissionOfferPolicyMixin"]
