"""Fund deterministic player-facing mission rewards at offer creation.

Generic semantic mission creation escrows currency rewards up front. Living-world
player offers are created inside an autonomous faction review rather than through
that command, so they must apply the same conservation rule before the review is
committed. This mixin adds one rank-typical success bonus per participant, funds
those terms from the mission's institutional treasury, and records the escrow in
the same semantic offer event.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, Mapping, Optional

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.mission_owner import MissionOwner, mission_owner_path
from shinobi_runtime.commands.paths import INVENTORY_REGISTRY_PATH


_ID_CLEAN = re.compile(r"[^a-z0-9.:-]+")


class PlayerMissionRewardFundingMixin:
    """Give autonomous player offers conserved, rank-banded compensation."""

    @staticmethod
    def _reward_term_id(mission_id: str, participant_ref: str) -> str:
        mission_suffix = mission_id.removeprefix("mission.")
        participant = _ID_CLEAN.sub(".", participant_ref.lower()).strip(".")
        return f"term.{mission_suffix}.reward.{participant}"

    def _fund_player_offer_reward(
        self,
        *,
        offer: Mapping[str, Any],
        world_events: Dict[str, Any],
        record_writes: Dict[str, Dict[str, Any]],
    ) -> Mapping[str, Any]:
        mission_id = offer.get("mission_id")
        if not isinstance(mission_id, str) or not mission_id.startswith("mission."):
            raise CommandRejectedError("player_offer_reward_mission_invalid")
        path = mission_owner_path(mission_id)
        raw_owner = record_writes.get(path)
        if not isinstance(raw_owner, Mapping):
            raise CommandRejectedError("player_offer_reward_mission_invalid")
        try:
            owner = MissionOwner.from_record(raw_owner)
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("player_offer_reward_mission_invalid") from exc
        if owner.mission.state != "offered" or owner.mission.settlement_terms:
            raise CommandRejectedError("player_offer_reward_mission_invalid")

        mechanics = self._economy_mechanics()
        rank_rules = mechanics.get("mission_ranks") if isinstance(mechanics, Mapping) else None
        rank_rule = rank_rules.get(owner.mission_rank) if isinstance(rank_rules, Mapping) else None
        typical = rank_rule.get("participant_bonus_typical_ryo") if isinstance(rank_rule, Mapping) else None
        maximum = rank_rule.get("participant_bonus_max_ryo") if isinstance(rank_rule, Mapping) else None
        if (
            isinstance(typical, bool)
            or not isinstance(typical, int)
            or typical <= 0
            or isinstance(maximum, bool)
            or not isinstance(maximum, int)
            or typical > maximum
        ):
            raise CommandRejectedError("economy_mechanics_invalid")

        terms = [
            {
                "term_id": self._reward_term_id(mission_id, participant_ref),
                "direction": "reward",
                "account_ref": participant_ref,
                "asset_ref": "currency.ryo",
                "quantity": typical,
                "applies_on": ["succeeded"],
                "objective_id": None,
                "objective_status": None,
            }
            for participant_ref in owner.mission.participant_refs
        ]
        escrow_total = typical * len(terms)
        if escrow_total > maximum * len(terms):
            raise CommandRejectedError("mission_reward_exceeds_rank_band")

        inventory_source = record_writes.get(INVENTORY_REGISTRY_PATH)
        if inventory_source is None:
            try:
                inventory = copy.deepcopy(self.repository.read_json(INVENTORY_REGISTRY_PATH))
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("mission_reward_funding_invalid") from exc
        else:
            inventory = copy.deepcopy(inventory_source)
        holders = inventory.get("holders") if isinstance(inventory, dict) else None
        funding = holders.get(owner.funding_holder_ref) if isinstance(holders, dict) else None
        if not isinstance(holders, dict) or not isinstance(funding, dict):
            raise CommandRejectedError("mission_reward_funding_invalid")
        balance = funding.get("currency.ryo", 0)
        if isinstance(balance, bool) or not isinstance(balance, int) or balance < escrow_total:
            raise CommandRejectedError("mission_reward_funding_insufficient")
        escrow_ref = "escrow." + mission_id
        if escrow_ref in holders and holders[escrow_ref]:
            raise CommandRejectedError("mission_reward_escrow_conflict")
        funding["currency.ryo"] = balance - escrow_total
        holders[escrow_ref] = {"currency.ryo": escrow_total}

        updated = dict(raw_owner)
        updated["settlement_terms"] = terms
        updated["escrow_holder_ref"] = escrow_ref
        try:
            MissionOwner.from_record(updated)
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("player_offer_reward_mission_invalid") from exc
        record_writes[path] = updated
        record_writes[INVENTORY_REGISTRY_PATH] = inventory

        event_id = offer.get("event_id")
        events = world_events.get("events") if isinstance(world_events, dict) else None
        event = next(
            (
                row
                for row in events or ()
                if isinstance(row, dict) and row.get("id") == event_id
            ),
            None,
        )
        if not isinstance(event, dict):
            raise CommandRejectedError("player_offer_reward_event_missing")
        material = event.get("material_consequence_refs")
        affected = event.get("affected_owner_refs")
        if not isinstance(material, list) or not isinstance(affected, list):
            raise CommandRejectedError("player_offer_reward_event_invalid")
        material.extend(
            (
                f"reward_escrow:currency.ryo:{escrow_total}:{owner.funding_holder_ref}->{escrow_ref}",
                f"participant_bonus_ryo:{typical}",
            )
        )
        if INVENTORY_REGISTRY_PATH not in affected:
            affected.append(INVENTORY_REGISTRY_PATH)
            affected.sort()

        result = dict(offer)
        result.update(
            {
                "funding_holder_ref": owner.funding_holder_ref,
                "escrow_holder_ref": escrow_ref,
                "participant_bonus_ryo": typical,
                "escrowed_reward_ryo": escrow_total,
                "reward_term_ids": [term["term_id"] for term in terms],
            }
        )
        return result

    def _maybe_offer_player_mission(
        self,
        *,
        decision: Any,
        at: Any,
        command: Any,
        scheduler: Any,
        world_events: Dict[str, Any],
        record_writes: Dict[str, Dict[str, Any]],
        faction_record: Dict[str, Any],
    ) -> Optional[Mapping[str, Any]]:
        offer = super()._maybe_offer_player_mission(
            decision=decision,
            at=at,
            command=command,
            scheduler=scheduler,
            world_events=world_events,
            record_writes=record_writes,
            faction_record=faction_record,
        )
        if (
            not isinstance(offer, Mapping)
            or offer.get("kind") != "player_mission_offer"
            or offer.get("skipped") is not None
            or not isinstance(offer.get("event_id"), str)
        ):
            return offer
        return self._fund_player_offer_reward(
            offer=offer,
            world_events=world_events,
            record_writes=record_writes,
        )


__all__ = ["PlayerMissionRewardFundingMixin"]
