"""One-shot semantic repair for the June 2026 Wei campaign progression defects.

This reducer exists only long enough to repair two already-confirmed production
facts atomically through the normal transaction coordinator:

* Team Fujin's SE-0061-06-05 autonomous review was consumed while Wei's unrelated
  Black Hound mission incorrectly preempted the whole delegated team session.
* mission.offer.0a7361790026211550 was generated without rank-banded reward terms
  or escrow, so its successful settlement paid nobody.

The reducer reuses the canonical autonomous team-training implementation for the
historical boundary and conserves the compensation transfer from Konoha treasury.
It does not alter world time or rewrite the original terminal mission settlement.
Remove this command from the public surface after the repair receipt is verified.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _exact_payload, _json_bytes
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.mission_owner import MissionOwner, mission_owner_path
from shinobi_runtime.commands.paths import INVENTORY_REGISTRY_PATH
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest


_REPAIR_ID = "repair.2026-08-13.fujin-training-and-blackhound-reward"
_MISSION_ID = "mission.offer.0a7361790026211550"
_FUJIN_REF = "team.konoha.fujin"
_FUJIN_PATH = "state/team/fujin.json"
_PLAYER_REF = "pc_wei_tang"
_REPAIR_TRAINING_AT = CampaignTime.parse("SE-0061-06-05T21:15:00")
_EXPECTED_PRIOR_FUJIN_SESSION_END = "SE-0061-05-29T21:15:00"
_EXPECTED_FUJIN_TRAINEES = frozenset(("char.kai", "char.mei_arakawa", "char.riku_hyuga"))


class CampaignRepair20260813Mixin:
    """Exact guarded repair; never a generic arbitrary-patch mechanism."""

    def _campaign_repair_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _exact_payload(command.payload, ("repair_id",), command.command_type)
        if command.payload.get("repair_id") != _REPAIR_ID:
            raise CommandRejectedError("campaign_repair_id_invalid")
        if command.actor_id != meta.get("player_id") or command.actor_id != _PLAYER_REF:
            raise CommandRejectedError("campaign_repair_actor_invalid")
        if current_time < _REPAIR_TRAINING_AT:
            raise CommandRejectedError("campaign_repair_time_invalid")

        # Guard the exact known-missed Team Fujin boundary.  If later gameplay
        # has already introduced another session, fail closed rather than
        # backfilling over newer development.
        try:
            team = copy.deepcopy(self.repository.read_json(_FUJIN_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("campaign_repair_fujin_invalid") from exc
        if (
            not isinstance(team, dict)
            or team.get("schema") != "exact-team"
            or team.get("id") != _FUJIN_REF
            or team.get("status") != "active"
            or team.get("current_assignment_ref") is not None
        ):
            raise CommandRejectedError("campaign_repair_fujin_invalid")
        training = team.get("training")
        recent = training.get("recent_sessions") if isinstance(training, Mapping) else None
        if not isinstance(recent, list) or not recent:
            raise CommandRejectedError("campaign_repair_fujin_history_changed")
        latest = recent[-1]
        if not isinstance(latest, Mapping) or latest.get("ended_at") != _EXPECTED_PRIOR_FUJIN_SESSION_END:
            raise CommandRejectedError("campaign_repair_fujin_history_changed")

        scene = copy.deepcopy(self._scene_base(current_time))
        scheduler = self._load_scheduler(current_time=current_time, scene=scene)
        world_events = self._world_events()
        record_writes: Dict[str, Dict[str, Any]] = {_FUJIN_PATH: team}

        # Re-run the canonical weekly training settlement at the consumed June 5
        # boundary. The standing order explicitly delegated coverage while Wei
        # was mission-bound, so exclude only Wei from this historical session.
        sentinel = object()
        previous = getattr(self, "_standing_training_absent_mission_refs", sentinel)
        self._standing_training_absent_mission_refs = frozenset((_PLAYER_REF,))
        try:
            training_result = self._apply_autonomous_team_training(
                team=team,
                owner_ref=_FUJIN_PATH,
                at=_REPAIR_TRAINING_AT,
                compacted=1,
                command=command,
                scheduler=scheduler,
                policy_book=None,
                world_events=world_events,
                record_writes=record_writes,
            )
        finally:
            if previous is sentinel:
                del self._standing_training_absent_mission_refs
            else:
                self._standing_training_absent_mission_refs = previous
        if not isinstance(training_result, Mapping) or training_result.get("skipped") is not None:
            reason = training_result.get("skipped") if isinstance(training_result, Mapping) else "invalid"
            raise CommandRejectedError("campaign_repair_fujin_training_not_replayable__" + str(reason))
        outcomes = training_result.get("outcomes")
        if not isinstance(outcomes, Mapping) or frozenset(outcomes) != _EXPECTED_FUJIN_TRAINEES:
            raise CommandRejectedError("campaign_repair_fujin_training_roster_changed")
        training_event_id = training_result.get("event_id")
        if not isinstance(training_event_id, str):
            raise CommandRejectedError("campaign_repair_fujin_event_missing")

        # Repair the malformed successful B-rank offer by issuing the rank-typical
        # participant bonus now from the same institutional funding holder. The
        # original terminal settlement remains untouched and therefore honest.
        mission_path = mission_owner_path(_MISSION_ID)
        try:
            mission = MissionOwner.from_record(self.repository.read_json(mission_path))
        except (FileNotFoundError, TypeError, ValueError) as exc:
            raise CommandRejectedError("campaign_repair_mission_invalid") from exc
        if (
            mission.mission_rank != "B"
            or mission.mission.state != "succeeded"
            or mission.escrow_holder_ref is not None
            or mission.mission.settlement_terms
            or mission.mission.settlement is None
            or mission.mission.settlement.reward_term_ids
            or mission.funding_holder_ref != "treasury.konoha"
        ):
            raise CommandRejectedError("campaign_repair_mission_history_changed")

        mechanics = self._economy_mechanics()
        rank_rules = mechanics.get("mission_ranks") if isinstance(mechanics, Mapping) else None
        rank_rule = rank_rules.get("B") if isinstance(rank_rules, Mapping) else None
        bonus = rank_rule.get("participant_bonus_typical_ryo") if isinstance(rank_rule, Mapping) else None
        maximum = rank_rule.get("participant_bonus_max_ryo") if isinstance(rank_rule, Mapping) else None
        if (
            isinstance(bonus, bool)
            or not isinstance(bonus, int)
            or bonus <= 0
            or isinstance(maximum, bool)
            or not isinstance(maximum, int)
            or bonus > maximum
        ):
            raise CommandRejectedError("economy_mechanics_invalid")
        participants = tuple(mission.mission.participant_refs)
        if len(participants) != 6 or _PLAYER_REF not in participants:
            raise CommandRejectedError("campaign_repair_mission_roster_changed")
        total_reward = bonus * len(participants)

        try:
            inventory = copy.deepcopy(self.repository.read_json(INVENTORY_REGISTRY_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("campaign_repair_inventory_invalid") from exc
        holders = inventory.get("holders") if isinstance(inventory, dict) else None
        treasury = holders.get(mission.funding_holder_ref) if isinstance(holders, dict) else None
        if not isinstance(holders, dict) or not isinstance(treasury, dict):
            raise CommandRejectedError("campaign_repair_inventory_invalid")
        treasury_balance = treasury.get("currency.ryo", 0)
        if isinstance(treasury_balance, bool) or not isinstance(treasury_balance, int) or treasury_balance < total_reward:
            raise CommandRejectedError("campaign_repair_treasury_insufficient")
        payout_refs: list[str] = []
        for participant_ref in participants:
            holder = holders.get(participant_ref)
            if not isinstance(holder, dict):
                raise CommandRejectedError("campaign_repair_participant_holder_missing")
            balance = holder.get("currency.ryo", 0)
            if isinstance(balance, bool) or not isinstance(balance, int) or balance < 0:
                raise CommandRejectedError("campaign_repair_participant_balance_invalid")
            holder["currency.ryo"] = balance + bonus
            payout_refs.append(f"repair_reward:currency.ryo:{bonus}:treasury.konoha->{participant_ref}")
        treasury["currency.ryo"] = treasury_balance - total_reward
        record_writes[INVENTORY_REGISTRY_PATH] = inventory

        repair_event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind="campaign_repair_resolution",
            at=current_time,
            host_refs=(_FUJIN_REF, _MISSION_ID),
            actor_refs=(),
            causal_refs=(training_event_id,),
            affected_owner_refs=tuple(sorted(set(record_writes) | {mission_path})),
            material_consequence_refs=(
                _REPAIR_ID,
                _MISSION_ID,
                training_event_id,
                f"mission_reward_repair_total_ryo:{total_reward}",
                *payout_refs,
            ),
            classification="restricted",
            audience_refs=(_PLAYER_REF,),
            source_refs=("runtime.campaign_repair",),
            reducer_ref="shinobi_runtime.commands.campaign_repair_20260813",
        )

        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            **{path: _json_bytes(value) for path, value in record_writes.items()},
            **self._world_event_writes(world_events),
        }
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))
        expected_training_event = training_event_id

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("campaign repair write set changed after planning")
            self._assert_meta(
                overlay,
                manifest,
                meta_path=self.meta_path,
                command=command,
                world_time=current_time,
            )
            repaired_team = overlay.read_json(_FUJIN_PATH)
            repaired_training = repaired_team.get("training") if isinstance(repaired_team, Mapping) else None
            repaired_recent = repaired_training.get("recent_sessions") if isinstance(repaired_training, Mapping) else None
            if (
                not isinstance(repaired_recent, list)
                or not repaired_recent
                or repaired_recent[-1].get("ended_at") != str(_REPAIR_TRAINING_AT)
                or frozenset(repaired_recent[-1].get("member_refs", ())) != _EXPECTED_FUJIN_TRAINEES
            ):
                raise ValueError("campaign repair Fujin training after-image invalid")
            repaired_inventory = overlay.read_json(INVENTORY_REGISTRY_PATH)
            repaired_holders = repaired_inventory.get("holders") if isinstance(repaired_inventory, Mapping) else None
            repaired_treasury = repaired_holders.get("treasury.konoha") if isinstance(repaired_holders, Mapping) else None
            if not isinstance(repaired_treasury, Mapping) or repaired_treasury.get("currency.ryo") != treasury_balance - total_reward:
                raise ValueError("campaign repair treasury after-image invalid")
            for participant_ref in participants:
                before = holders[participant_ref]["currency.ryo"] - bonus
                after_holder = repaired_holders.get(participant_ref) if isinstance(repaired_holders, Mapping) else None
                if not isinstance(after_holder, Mapping) or after_holder.get("currency.ryo") != before + bonus:
                    raise ValueError("campaign repair payout after-image invalid")
            events = overlay.read_json("state/reg/world-events.json").get("events", [])
            if not any(isinstance(row, Mapping) and row.get("id") == repair_event_id for row in events):
                raise ValueError("campaign repair semantic event missing")

        return _BuiltPlan(
            code="campaign_repair_resolution_ready",
            affected_refs=expected_paths,
            writes=writes,
            result={
                "command_type": command.command_type,
                "repair_id": _REPAIR_ID,
                "world_time_unchanged": str(current_time),
                "fujin_training_event_id": expected_training_event,
                "fujin_training": dict(training_result),
                "mission_id": _MISSION_ID,
                "participant_bonus_ryo": bonus,
                "total_reward_ryo": total_reward,
                "rewarded_participant_refs": list(participants),
                "repair_event_id": repair_event_id,
            },
            validator=validate,
        )


__all__ = ["CampaignRepair20260813Mixin"]
