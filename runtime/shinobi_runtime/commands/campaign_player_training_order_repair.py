"""Guarded repair for Wei Tang's superseded House training order.

The durable player owner still contains the older 34-hour House schedule after
Wei explicitly replaced it with the committed 48-hour development policy. This
repair updates only that one exact order, and only when the five replacement
training commitments are active. It does not alter training outcomes, time,
House authority, or any other player intent.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _exact_payload, _json_bytes
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.paths import COMMITMENT_REGISTRY_PATH, WORLD_EVENT_REGISTRY_PATH
from shinobi_runtime.commands.specs import COMMAND_SPECS, CommandSpec
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest

_REPAIR_ID = "repair.wei_house_training_order_48h.2026-08-17"
_PLAYER_REF = "pc_wei_tang"
_PLAYER_PATH = "state/player.json"
_REQUIRED_ACTIVE_COMMITMENTS = (
    "commitment.team_fujin.permanent_training.0061-08-05.v2",
    "commitment.blackhound.permanent_training.0061-08-05.v2",
    "commitment.wei.permanent_training.0061-08-05.v2",
    "commitment.sword_manor.permanent_training.0061-08-05.v2",
    "commitment.sword_manor.master_development.0061-08-05.v2",
)
_OLD_ORDER = (
    "Under Wei's House Tang field-command warrant, available House military elements use a repeating "
    "maximum-quality cycle of five six-active-hour training days, one four-active-hour taper, and one "
    "recovery/readiness day. Lawful missions, external assignments, injury, medical restrictions, and "
    "Zhu's retained strategic/dojo authority supersede the schedule; missed volume is not stacked later. "
    "Instructors rotate through active demonstrations, opposition, and practice so teaching does not freeze "
    "their own development. Unit training and doctrine references define the content."
)
_NEW_ORDER = (
    "Under Wei's House Tang field-command warrant, available House military elements use a 48-active-hour "
    "weekly development envelope when duties permit: 34 hours of shared House curriculum plus 14 hours of "
    "individualized development using each person's saved training profile, with one protected recovery day. "
    "Sword Manor capacity is staggered across House cohorts, Team Fujin, and Black Hound. Lawful missions, "
    "external assignments, injury, medical restrictions, and Zhu's retained strategic/dojo authority supersede "
    "training; missed volume is not stacked later. Instructors rotate through teaching, opposition, and personal "
    "practice so instruction does not freeze their own development."
)
_INSTALLED = False


def _active_commitment_ids(repository: Any) -> set[str]:
    try:
        registry = repository.read_json(COMMITMENT_REGISTRY_PATH)
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("campaign_player_training_order_repair_commitments_invalid") from exc
    records = registry.get("records") if isinstance(registry, Mapping) else None
    if registry.get("schema") != "commitment-registry" or not isinstance(records, list):
        raise CommandRejectedError("campaign_player_training_order_repair_commitments_invalid")
    active: set[str] = set()
    for row in records:
        if not isinstance(row, Mapping):
            raise CommandRejectedError("campaign_player_training_order_repair_commitments_invalid")
        ref = row.get("id")
        status = row.get("status")
        if isinstance(ref, str) and status == "active":
            active.add(ref)
    return active


def _repair_order(player: Mapping[str, Any]) -> dict[str, Any]:
    if player.get("schema") != "shinobi_character" or player.get("owner_id") != _PLAYER_REF:
        raise CommandRejectedError("campaign_player_training_order_repair_player_invalid")
    repaired = copy.deepcopy(dict(player))
    goal_state = repaired.get("goal_state")
    orders = goal_state.get("current_orders") if isinstance(goal_state, dict) else None
    if not isinstance(orders, list) or any(not isinstance(value, str) for value in orders):
        raise CommandRejectedError("campaign_player_training_order_repair_player_invalid")
    if _NEW_ORDER in orders and _OLD_ORDER not in orders:
        raise CommandRejectedError("campaign_player_training_order_repair_already_applied")
    if orders.count(_OLD_ORDER) != 1:
        raise CommandRejectedError("campaign_player_training_order_repair_source_not_exact")
    goal_state["current_orders"] = [
        _NEW_ORDER if value == _OLD_ORDER else value
        for value in orders
    ]
    return repaired


def _repair(
    self: Any,
    command: CommandEnvelope,
    meta: Mapping[str, Any],
    current_time: CampaignTime,
) -> _BuiltPlan:
    _exact_payload(command.payload, ("repair_id",), "campaign_player_training_order_repair")
    if command.payload["repair_id"] != _REPAIR_ID:
        raise CommandRejectedError("campaign_player_training_order_repair_id_invalid")
    if command.actor_id != meta.get("player_id") or command.actor_id != _PLAYER_REF:
        raise CommandRejectedError("campaign_player_training_order_repair_actor_invalid")

    active = _active_commitment_ids(self.repository)
    if any(ref not in active for ref in _REQUIRED_ACTIVE_COMMITMENTS):
        raise CommandRejectedError("campaign_player_training_order_repair_policy_not_committed")
    try:
        player = self.repository.read_json(_PLAYER_PATH)
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("campaign_player_training_order_repair_player_invalid") from exc
    repaired = _repair_order(player)

    world_events = self._world_events()
    event_id = self._append_semantic_event(
        world_events,
        command=command,
        kind="campaign_repair_applied",
        at=current_time,
        host_refs=("house.tang", "place.sword_manor"),
        actor_refs=(command.actor_id,),
        affected_owner_refs=(_PLAYER_PATH,),
        material_consequence_refs=(
            "player_training_order_superseded:34_shared_to_34_shared_plus_14_individual",
        ),
        classification="restricted",
        audience_refs=(command.actor_id,),
        source_refs=(
            command.actor_id,
            *_REQUIRED_ACTIVE_COMMITMENTS,
        ),
        reducer_ref="shinobi_runtime.commands.campaign_player_training_order_repair",
    )

    writes = {
        _PLAYER_PATH: _json_bytes(repaired),
        self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
    }
    writes.update(self._world_event_writes(world_events))
    writes = self._prune_noop_writes(writes)
    expected_paths = tuple(sorted(writes))
    expected_player = copy.deepcopy(repaired)

    def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
        if overlay.changed_paths != expected_paths:
            raise ValueError("player training-order repair write set changed after planning")
        self._assert_meta(
            overlay,
            manifest,
            meta_path=self.meta_path,
            command=command,
            world_time=current_time,
        )
        if overlay.read_json(_PLAYER_PATH) != expected_player:
            raise ValueError("player training-order repair player mismatch")
        staged_events = overlay.read_json(WORLD_EVENT_REGISTRY_PATH).get("events", [])
        if not any(isinstance(item, Mapping) and item.get("id") == event_id for item in staged_events):
            raise ValueError("player training-order repair semantic event missing")

    return _BuiltPlan(
        code="campaign_player_training_order_repair_ready",
        affected_refs=expected_paths,
        writes=writes,
        result={
            "command_type": command.command_type,
            "repair_id": _REPAIR_ID,
            "status": "repaired",
            "world_time": str(current_time),
            "repaired_owner_ref": _PLAYER_REF,
        },
        validator=validate,
    )


def install_campaign_player_training_order_repair() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.commands import campaign_environment as module

    COMMAND_SPECS.setdefault(
        "campaign_player_training_order_repair",
        CommandSpec(
            ("repair_id",),
            (),
            "Replace Wei's superseded 34-hour House training order with the committed 48-hour policy.",
            {"repair_id": _REPAIR_ID},
            availability="ooc_dev_guarded_repair_only",
        ),
    )
    planner = module.CampaignCommandPlanner
    setattr(planner, "_campaign_player_training_order_repair", _repair)
    planner.COMMAND_TYPES = frozenset(COMMAND_SPECS)
    _INSTALLED = True


__all__ = [
    "install_campaign_player_training_order_repair",
    "_repair_order",
    "_OLD_ORDER",
    "_NEW_ORDER",
]
