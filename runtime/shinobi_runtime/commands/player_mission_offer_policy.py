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
    def _player_offer_demand_candidates(self, faction_id: str) -> tuple[tuple[str, str], ...]:
        """Return concrete player-offer demand/objective pairs.

        Mission-market demand may be broad (for example ``capture``) while a
        player-facing mission must already have enough concrete briefing truth
        to be executable.  The policy therefore maps only demand keys with a
        registered briefing template.  Unsupported demand remains real market
        pressure but is not converted into an invented target or enemy.
        """
        try:
            _profile, assignment = self._autonomy_policy_book().faction_context(faction_id)
        except (TypeError, ValueError, CommandRejectedError):
            return ()
        config = assignment.get("player_offer") if isinstance(assignment, Mapping) else None
        if not isinstance(config, Mapping):
            return ()
        raw = config.get("objective_cycle")
        if (
            not isinstance(raw, list)
            or not raw
            or len(raw) > 32
            or any(not isinstance(value, str) or not value for value in raw)
            or len(set(raw)) != len(raw)
        ):
            raise CommandRejectedError("player_offer_objective_cycle_invalid")

        mapping = config.get("market_demand_to_objective", {})
        if not isinstance(mapping, Mapping):
            raise CommandRejectedError("player_offer_market_mapping_invalid")
        templates = config.get("briefing_templates")
        if not isinstance(templates, Mapping):
            raise CommandRejectedError("player_mission_briefing_policy_invalid")
        dynamic_sources = config.get("dynamic_briefing_sources", {})
        if not isinstance(dynamic_sources, Mapping):
            raise CommandRejectedError("player_mission_briefing_policy_invalid")

        def briefing_available(demand_key: str, objective: str) -> bool:
            if demand_key in dynamic_sources:
                resolver = getattr(self, "_dynamic_player_offer_briefing_available", None)
                return bool(callable(resolver) and resolver(demand_key=demand_key, source_kind=dynamic_sources[demand_key]))
            return demand_key in templates or objective in templates
        market_ref = config.get("mission_market_ref")
        if market_ref is None:
            return tuple(
                (key, str(mapping.get(key, key)))
                for key in raw
                if briefing_available(key, str(mapping.get(key, key)))
            )
        if not isinstance(market_ref, str) or not market_ref:
            raise CommandRejectedError("player_offer_market_mapping_invalid")
        try:
            registry = self.repository.read_json("state/reg/mission-markets.json")
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("player_offer_market_invalid") from exc
        markets = registry.get("markets") if isinstance(registry, Mapping) else None
        market = markets.get(market_ref) if isinstance(markets, Mapping) else None
        scores = market.get("demand_scores") if isinstance(market, Mapping) else None
        if not isinstance(scores, Mapping):
            raise CommandRejectedError("player_offer_market_invalid")

        ranked: list[tuple[int, str, str]] = []
        for demand_key in raw:
            score = scores.get(demand_key)
            objective = mapping.get(demand_key, demand_key)
            if (
                isinstance(score, bool) or not isinstance(score, int)
                or not isinstance(objective, str) or not objective
                or not briefing_available(demand_key, objective)
            ):
                continue
            ranked.append((-score, demand_key, objective))
        ranked.sort()
        return tuple((demand_key, objective) for _neg, demand_key, objective in ranked)

    def _player_offer_objective_cycle(self, faction_id: str) -> tuple[str, ...]:
        return tuple(objective for _demand, objective in self._player_offer_demand_candidates(faction_id))

    def _mission_objective_kind(
        self,
        payload: Mapping[str, Any],
        faction_id: str,
        at: CampaignTime,
    ) -> str:
        candidates = getattr(self, "_active_player_offer_demand_candidates", ())
        if not candidates:
            return super()._mission_objective_kind(payload, faction_id, at)
        identity = f"{faction_id}\x00{at}\x00player-offer-objective"
        digest = hashlib.sha256(identity.encode("utf-8")).digest()
        demand_key, objective = candidates[int.from_bytes(digest[:8], "big") % len(candidates)]
        self._active_player_offer_demand_key = demand_key
        return objective

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
        candidates = self._player_offer_demand_candidates(faction_id) if isinstance(faction_id, str) else ()
        prior = getattr(self, "_active_player_offer_demand_candidates", None)
        prior_demand = getattr(self, "_active_player_offer_demand_key", None)
        self._active_player_offer_demand_candidates = candidates
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
                    del self._active_player_offer_demand_candidates
                except AttributeError:
                    pass
            else:
                self._active_player_offer_demand_candidates = prior
            if prior_demand is None:
                try:
                    del self._active_player_offer_demand_key
                except AttributeError:
                    pass
            else:
                self._active_player_offer_demand_key = prior_demand

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
