"""Keep the durable scene resume projection fresh and provide one narrow repair."""
from __future__ import annotations

import copy
import hashlib
import json
from functools import wraps
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _json_bytes
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.commands.specs import COMMAND_SPECS, CommandSpec
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest

_MISSION_INDEX = "state/mission/context-index.json"
_INSTALLED = False
_PASSIVE = frozenset(("advance_time", "advance_until_event", "report_handoff_resolution", "team_checkin_handoff_resolution", "scene_projection_repair"))


def _scene_id(scene: Mapping[str, Any]) -> str:
    value = copy.deepcopy(dict(scene))
    value.pop("scene_id", None)
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return "scene_resume_" + hashlib.sha256(raw).hexdigest()[:20]


class _ValidatorView:
    def __init__(self, overlay: Any, path: str, raw: bytes) -> None:
        self._overlay, self._path, self._raw = overlay, path, raw
        self.changed_paths = overlay.changed_paths

    def read_json(self, path: str) -> Any:
        return json.loads(self._raw.decode()) if path == self._path else self._overlay.read_json(path)

    def read_bytes(self, path: str) -> bytes:
        return self._raw if path == self._path else self._overlay.read_bytes(path)

    def read_optional_bytes(self, path: str) -> bytes | None:
        if path == self._path:
            return self._raw
        reader = getattr(self._overlay, "read_optional_bytes", None)
        if callable(reader):
            return reader(path)
        try:
            return self._overlay.read_bytes(path)
        except FileNotFoundError:
            return None

    def digest(self, path: str) -> str:
        return hashlib.sha256(self._raw).hexdigest() if path == self._path else self._overlay.digest(path)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._overlay, name)


def normalize_scene_resume_plan(plan: _BuiltPlan, scene_path: str, *, command_type: str, command_mode: str) -> _BuiltPlan:
    raw = plan.writes.get(scene_path)
    if raw is None:
        return plan
    try:
        scene = json.loads(raw.decode())
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise CommandRejectedError("campaign_scene_invalid") from exc
    narrative = scene.get("narrative") if isinstance(scene, dict) else None
    summary = scene.get("scene_summary") if isinstance(scene, dict) else None
    if scene.get("schema") != "scene" or not isinstance(narrative, dict) or not isinstance(summary, str) or not summary:
        raise CommandRejectedError("campaign_scene_invalid")
    narrative["last_scene_summary"] = summary
    if command_mode == "gameplay" and command_type not in _PASSIVE:
        narrative["last_major_choice"] = summary
    scene["scene_id"] = _scene_id(scene)
    normalized = _json_bytes(scene)
    if normalized == raw:
        return plan
    writes = dict(plan.writes)
    writes[scene_path] = normalized
    original_validator = plan.validator
    expected = copy.deepcopy(scene)

    def validate(overlay: Any, manifest: Any) -> None:
        original_validator(_ValidatorView(overlay, scene_path, raw), manifest)
        if overlay.read_json(scene_path) != expected:
            raise ValueError("scene resume projection changed after planning")

    return _BuiltPlan(plan.code, plan.affected_refs, writes, plan.result, validate)


def _plan_repair(self: Any, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime) -> _BuiltPlan:
    if command.payload:
        raise CommandRejectedError("scene_projection_repair_payload_fields_invalid")
    if command.actor_id != meta.get("player_id"):
        raise CommandRejectedError("scene_projection_repair_actor_not_authorized")
    try:
        scene = copy.deepcopy(self.repository.read_json(self.scene_path))
        index = self.repository.read_json(_MISSION_INDEX)
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("scene_projection_repair_source_invalid") from exc
    narrative = scene.get("narrative") if isinstance(scene, dict) else None
    loaded = scene.get("loaded_owner_ids") if isinstance(scene, dict) else None
    if scene.get("schema") != "scene" or scene.get("world_time") != str(current_time) or not isinstance(narrative, dict) or not isinstance(loaded, list):
        raise CommandRejectedError("campaign_scene_invalid")
    routes = index.get("current_by_participant") if isinstance(index, Mapping) else None
    missions = routes.get(command.actor_id) if isinstance(routes, Mapping) else None
    if not isinstance(missions, list) or any(not isinstance(ref, str) or not ref.startswith("mission.") for ref in missions):
        raise CommandRejectedError("mission_context_index_invalid")
    base_loaded = [ref for ref in loaded if isinstance(ref, str) and not ref.startswith("mission.")]
    scene["loaded_owner_ids"] = base_loaded + [ref for ref in missions if ref not in base_loaded]
    narrative.pop("last_major_choice", None)
    writes = {
        self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
        self.scene_path: _json_bytes(scene),
    }
    expected_paths = tuple(sorted(writes))

    def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
        if overlay.changed_paths != expected_paths:
            raise ValueError("scene projection repair write set changed after planning")
        self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
        if overlay.read_json(self.scene_path) != scene:
            raise ValueError("scene projection repair after-image differs from plan")

    return _BuiltPlan(
        code="scene_projection_repair_ready",
        affected_refs=expected_paths,
        writes=writes,
        result={"command_type": command.command_type, "world_time": str(current_time), "current_mission_refs": list(missions), "status": "scene_projection_repaired"},
        validator=validate,
    )


def install_scene_resume_projection() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.commands import campaign_environment as module

    COMMAND_SPECS.setdefault(
        "scene_projection_repair",
        CommandSpec((), (), "Repair stale player-facing scene resume/cache fields without advancing campaign time or changing world owners.", {}, availability="ooc_dev_repair_only_when_scene_projection_is_stale"),
    )
    planner = module.CampaignCommandPlanner
    setattr(planner, "_scene_projection_repair", _plan_repair)
    planner.COMMAND_TYPES = frozenset(COMMAND_SPECS)

    original = RepositoryCommandPlanner._build
    if not getattr(original, "_scene_resume_projection", False):
        @wraps(original)
        def wrapped(self: Any, command: Any) -> _BuiltPlan:
            return normalize_scene_resume_plan(original(self, command), getattr(self, "scene_path", "state/scene.json"), command_type=command.command_type, command_mode=command.mode)
        wrapped._scene_resume_projection = True
        RepositoryCommandPlanner._build = wrapped
    _INSTALLED = True


__all__ = ["install_scene_resume_projection", "normalize_scene_resume_plan"]
