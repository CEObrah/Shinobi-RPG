"""One-use guarded correction for Wei Tang's stale core attributes.

This repair exists only to reconcile three confirmed stale values in the durable
player owner. It requires the exact known before-image and writes the exact
corrected after-image. It does not expose a generic stat editor, award training,
advance time, or alter any other player field.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _exact_payload, _json_bytes
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.paths import WORLD_EVENT_REGISTRY_PATH
from shinobi_runtime.commands.specs import COMMAND_SPECS, CommandSpec
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest

_COMMAND = "campaign_player_attribute_correction"
_REPAIR_ID = "repair.wei_core_attributes.2026-08-17"
_PLAYER_REF = "pc_wei_tang"
_PLAYER_PATH = "state/player.json"
_BEFORE = {
    "intelligence": 170,
    "awareness": 145,
    "coordination": 150,
}
_AFTER = {
    "intelligence": 200,
    "awareness": 200,
    "coordination": 200,
}
_INSTALLED = False


def _correct_attributes(player: Mapping[str, Any]) -> dict[str, Any]:
    if player.get("schema") != "shinobi_character" or player.get("owner_id") != _PLAYER_REF:
        raise CommandRejectedError("campaign_player_attribute_correction_player_invalid")
    attributes = player.get("attributes")
    if not isinstance(attributes, Mapping):
        raise CommandRejectedError("campaign_player_attribute_correction_player_invalid")
    current = {key: attributes.get(key) for key in _BEFORE}
    if current == _AFTER:
        raise CommandRejectedError("campaign_player_attribute_correction_already_applied")
    if current != _BEFORE:
        raise CommandRejectedError("campaign_player_attribute_correction_source_not_exact")

    repaired = copy.deepcopy(dict(player))
    repaired_attributes = repaired.get("attributes")
    if not isinstance(repaired_attributes, dict):
        raise CommandRejectedError("campaign_player_attribute_correction_player_invalid")
    repaired_attributes.update(_AFTER)
    return repaired


def _repair(
    self: Any,
    command: CommandEnvelope,
    meta: Mapping[str, Any],
    current_time: CampaignTime,
) -> _BuiltPlan:
    _exact_payload(command.payload, ("repair_id",), _COMMAND)
    if command.payload["repair_id"] != _REPAIR_ID:
        raise CommandRejectedError("campaign_player_attribute_correction_id_invalid")
    if command.actor_id != meta.get("player_id") or command.actor_id != _PLAYER_REF:
        raise CommandRejectedError("campaign_player_attribute_correction_actor_invalid")

    try:
        player = self.repository.read_json(_PLAYER_PATH)
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("campaign_player_attribute_correction_player_invalid") from exc
    repaired = _correct_attributes(player)

    world_events = self._world_events()
    event_id = self._append_semantic_event(
        world_events,
        command=command,
        kind="campaign_repair_applied",
        at=current_time,
        host_refs=(_PLAYER_REF,),
        actor_refs=(command.actor_id,),
        affected_owner_refs=(_PLAYER_PATH,),
        material_consequence_refs=(
            "player_attribute_corrected:intelligence:170->200",
            "player_attribute_corrected:awareness:145->200",
            "player_attribute_corrected:coordination:150->200",
        ),
        classification="restricted",
        audience_refs=(command.actor_id,),
        source_refs=(command.actor_id,),
        reducer_ref="shinobi_runtime.commands.campaign_player_attribute_correction",
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
            raise ValueError("player attribute correction write set changed after planning")
        self._assert_meta(
            overlay,
            manifest,
            meta_path=self.meta_path,
            command=command,
            world_time=current_time,
        )
        if overlay.read_json(_PLAYER_PATH) != expected_player:
            raise ValueError("player attribute correction after-image differs from plan")
        staged_events = overlay.read_json(WORLD_EVENT_REGISTRY_PATH).get("events", [])
        if not any(isinstance(item, Mapping) and item.get("id") == event_id for item in staged_events):
            raise ValueError("player attribute correction semantic event missing")

    return _BuiltPlan(
        code="campaign_player_attribute_correction_ready",
        affected_refs=expected_paths,
        writes=writes,
        result={
            "command_type": command.command_type,
            "repair_id": _REPAIR_ID,
            "status": "repaired",
            "world_time": str(current_time),
            "repaired_owner_ref": _PLAYER_REF,
            "before": dict(_BEFORE),
            "after": dict(_AFTER),
            "semantic_event_id": event_id,
        },
        validator=validate,
    )


def install_campaign_player_attribute_correction() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.commands import campaign_environment as module

    COMMAND_SPECS.setdefault(
        _COMMAND,
        CommandSpec(
            ("repair_id",),
            (),
            "Apply the one-use guarded correction for Wei's three confirmed stale core attributes.",
            {"repair_id": _REPAIR_ID},
            availability="ooc_dev_guarded_repair_only",
        ),
    )
    planner = module.CampaignCommandPlanner
    setattr(planner, "_" + _COMMAND, _repair)
    planner.COMMAND_TYPES = frozenset(COMMAND_SPECS)
    _INSTALLED = True


__all__ = [
    "install_campaign_player_attribute_correction",
    "_correct_attributes",
    "_BEFORE",
    "_AFTER",
]
