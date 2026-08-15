"""Install a durable handled lifecycle for player-visible team check-ins."""
from __future__ import annotations

from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _exact_payload, _json_bytes, _stable_id
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.specs import COMMAND_SPECS, CommandSpec
from shinobi_runtime.commands.team_checkin_records import event_id_for_checkin, project_team_checkin
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest

_HANDLINGS = frozenset(("acknowledge", "discussed"))
_INSTALLED = False


def _plan_team_checkin_handoff_resolution(self: Any, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime) -> _BuiltPlan:
    _exact_payload(command.payload, ("checkin_ref", "handling"), command.command_type)
    checkin_ref = _stable_id(command.payload.get("checkin_ref"), "team_checkin_ref_invalid", prefix="team_checkin.")
    handling = command.payload.get("handling")
    if handling not in _HANDLINGS:
        raise CommandRejectedError("team_checkin_handling_invalid")
    try:
        projected = project_team_checkin(self.repository, checkin_ref, command.actor_id)
    except ValueError as exc:
        raise CommandRejectedError("team_checkin_not_player_visible") from exc
    if projected.get("handled"):
        raise CommandRejectedError("team_checkin_already_handled")
    source_event_ref = event_id_for_checkin(checkin_ref)
    source_event = self._world_event_by_id(source_event_ref)
    if not isinstance(source_event, Mapping) or source_event.get("kind") != "player_led_team_checkin_ready":
        raise CommandRejectedError("team_checkin_event_invalid")
    team_ref = projected.get("team_ref")
    contact_ref = projected.get("contact_actor_ref")
    if not isinstance(team_ref, str) or not isinstance(contact_ref, str):
        raise CommandRejectedError("team_checkin_event_invalid")
    visibility = source_event.get("visibility")
    classification = visibility.get("classification") if isinstance(visibility, Mapping) else None
    if classification not in ("public", "restricted", "secret"):
        classification = "restricted"

    world_events = self._world_events()
    event_id = self._append_semantic_event(
        world_events,
        command=command,
        kind="player_led_team_checkin_handled",
        at=current_time,
        host_refs=(team_ref,),
        actor_refs=(command.actor_id,),
        causal_refs=(source_event_ref,),
        affected_owner_refs=(),
        material_consequence_refs=(f"team_checkin_handling:{handling}:{checkin_ref}",),
        classification=classification,
        audience_refs=(command.actor_id,),
        source_refs=(command.actor_id,),
        reducer_ref="shinobi_runtime.commands.team_checkin_handoffs.team_checkin_handoff_resolution",
    )
    writes = {
        self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
        **self._world_event_writes(world_events),
    }
    writes = self._prune_noop_writes(writes)
    expected_paths = tuple(sorted(writes))

    def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
        if overlay.changed_paths != expected_paths:
            raise ValueError("team check-in handoff write set changed after planning")
        self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
        if not any(path == "state/reg/world-events.json" or path.startswith("state/history/events/") for path in expected_paths):
            raise ValueError("team check-in handling event did not persist")

    return _BuiltPlan(
        code="team_checkin_handoff_resolution_ready",
        affected_refs=expected_paths,
        writes=writes,
        result={
            "command_type": command.command_type,
            "checkin_ref": checkin_ref,
            "source_event_ref": source_event_ref,
            "team_ref": team_ref,
            "contact_actor_ref": contact_ref,
            "handling": handling,
            "status": "handled",
            "semantic_event_id": event_id,
        },
        validator=validate,
    )


def install_team_checkin_handoffs() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.commands import campaign_player_handoffs as module

    COMMAND_SPECS.setdefault(
        "team_checkin_handoff_resolution",
        CommandSpec(
            ("checkin_ref", "handling"),
            (),
            "Record one player-visible team check-in as handled without inventing dialogue, commitments, or team changes.",
            {
                "checkin_ref": "team_checkin.<id>",
                "handling": "acknowledge|discussed",
            },
        ),
    )
    planner = module.CampaignCommandPlanner
    setattr(planner, "_team_checkin_handoff_resolution", _plan_team_checkin_handoff_resolution)
    planner.COMMAND_TYPES = frozenset(COMMAND_SPECS)
    _INSTALLED = True


__all__ = ["install_team_checkin_handoffs"]
