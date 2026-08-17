"""Evidence-backed partial mission progress and non-blocking mission handoffs.

Mission objectives remain the sole progress authority. This extension permits one
persisted admissible world event to advance a nonterminal objective by a runtime-
derived amount. Existing exact-team doctrine may improve execution efficiency;
it never supplies evidence or terminal success. The module also removes the old
blanket hard-decision marker from routine mission mutations.
"""
from __future__ import annotations

import json
from functools import wraps
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import (
    _BuiltPlan,
    _OwnerResolutionCache,
    _exact_payload,
    _json_bytes,
    _stable_id,
)
from shinobi_runtime.commands.domains.missions import MissionCommandsMixin
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.mission_owner import MissionOwner
from shinobi_runtime.commands.paths import WORLD_EVENT_REGISTRY_PATH
from shinobi_runtime.commands.specs import COMMAND_SPECS, CommandSpec
from shinobi_runtime.reducers.missions import (
    MissionTransitionError,
    ObjectiveDependencyError,
    update_objective,
)
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest

_INSTALLED = False
_COMMAND = "mission_objective_progress_resolution"
_RULES = "game/data/mechanics/mission-progress.json"
_ROUTINE_HANDOFF_COMMANDS = frozenset(
    (
        "mission_transition",
        "mission_objective_update",
        _COMMAND,
        "mission_derive_and_settle",
    )
)


def _progress_rules(repository: Any) -> Mapping[str, Any]:
    try:
        record = repository.read_json(_RULES)
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("mission_progress_rules_invalid") from exc
    if record.get("schema") != "mission-progress-rules" or record.get("version") != 1:
        raise CommandRejectedError("mission_progress_rules_invalid")
    if not isinstance(record.get("base_progress_milli_by_event_kind"), Mapping):
        raise CommandRejectedError("mission_progress_rules_invalid")
    return record


def _history_sources(repository: Any) -> list[tuple[str, Mapping[str, Any]]]:
    try:
        registry = repository.read_json(WORLD_EVENT_REGISTRY_PATH)
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("world_event_registry_invalid") from exc
    if not isinstance(registry, Mapping):
        raise CommandRejectedError("world_event_registry_invalid")
    sources: list[tuple[str, Mapping[str, Any]]] = [(WORLD_EVENT_REGISTRY_PATH, registry)]
    archive_refs = registry.get("archive_refs")
    if isinstance(archive_refs, list):
        for path in archive_refs:
            if not isinstance(path, str) or not path:
                raise CommandRejectedError("world_event_registry_invalid")
            try:
                archive = repository.read_json(path)
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("world_event_registry_invalid") from exc
            if not isinstance(archive, Mapping):
                raise CommandRejectedError("world_event_registry_invalid")
            sources.append((path, archive))
    return sources


def _evidence_usage_token(mission_id: str, objective_id: str, evidence_event_id: str) -> str:
    return f"mission_progress_evidence:{mission_id}:{objective_id}:{evidence_event_id}"


def _evidence_already_used(repository: Any, token: str) -> bool:
    for _path, source in _history_sources(repository):
        events = source.get("events")
        if not isinstance(events, list):
            continue
        for event in events:
            material = event.get("material_consequence_refs") if isinstance(event, Mapping) else None
            if isinstance(material, list) and token in material:
                return True
    return False


def _evidence_guard(repository: Any, evidence_event_id: str) -> dict[str, str]:
    """Guard the exact hot or archived semantic-history shard containing evidence."""

    matches: list[str] = []
    for path, source in _history_sources(repository):
        events = source.get("events")
        if not isinstance(events, list):
            continue
        if any(isinstance(row, Mapping) and row.get("id") == evidence_event_id for row in events):
            matches.append(path)
    if len(matches) != 1:
        raise CommandRejectedError("mission_objective_evidence_unavailable")
    digest = repository.digest(matches[0])
    if not isinstance(digest, str) or not digest:
        raise CommandRejectedError("mission_objective_evidence_uncommitted")
    return {matches[0]: digest}


def _team_doctrine_modifier(
    planner: Any,
    owner: Any,
    objective_kind: str,
    rules: Mapping[str, Any],
) -> tuple[int, dict[str, Any], dict[str, str]]:
    """Return multiplier, bounded public mechanics summary, guarded read digests."""

    operation_ref = owner.operation_ref
    if not isinstance(operation_ref, str) or not operation_ref.startswith("team."):
        return 1000, {"doctrine_applied": False}, {}
    exact_team = getattr(planner, "_exact_team", None)
    if not callable(exact_team):
        return 1000, {"doctrine_applied": False}, {}
    try:
        team_path, team = exact_team(operation_ref)
    except CommandRejectedError:
        return 1000, {"doctrine_applied": False}, {}
    if not isinstance(team, Mapping):
        return 1000, {"doctrine_applied": False}, {}
    team_digest = planner.repository.digest(team_path)
    if not isinstance(team_digest, str) or not team_digest:
        raise CommandRejectedError("mission_team_doctrine_invalid")
    doctrine_ref = team.get("doctrine_ref")
    if not isinstance(doctrine_ref, str) or not doctrine_ref:
        return 1000, {"doctrine_applied": False}, {team_path: team_digest}
    try:
        doctrine_path, doctrine_digest, doctrine = planner._resolve_covered_owner_view(
            doctrine_ref,
            cache=_OwnerResolutionCache(),
        )
    except CommandRejectedError:
        return 1000, {"doctrine_applied": False}, {team_path: team_digest}
    if not isinstance(doctrine_digest, str) or not doctrine_digest:
        raise CommandRejectedError("mission_team_doctrine_invalid")
    if not isinstance(doctrine, Mapping) or doctrine.get("status") != "active":
        return 1000, {"doctrine_applied": False}, {
            team_path: team_digest,
            doctrine_path: doctrine_digest,
        }

    members = team.get("member_refs")
    familiarity = doctrine.get("familiarity")
    roles = doctrine.get("roles")
    if not isinstance(members, list) or not isinstance(familiarity, Mapping) or not isinstance(roles, Mapping):
        raise CommandRejectedError("mission_team_doctrine_invalid")
    participants = [ref for ref in owner.mission.participant_refs if ref in members]
    familiar_values: list[int] = []
    for ref in participants:
        value = familiarity.get(ref)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
            familiar_values = []
            break
        familiar_values.append(value)
    average = sum(familiar_values) // len(familiar_values) if familiar_values else 0

    doctrine_rules = rules.get("doctrine")
    mode_map = rules.get("objective_mode")
    if not isinstance(doctrine_rules, Mapping) or not isinstance(mode_map, Mapping):
        raise CommandRejectedError("mission_progress_rules_invalid")
    floor = doctrine_rules.get("familiarity_bonus_floor")
    per_point = doctrine_rules.get("familiarity_bonus_per_point_milli")
    mode_bonus = doctrine_rules.get("matching_mission_mode_bonus_milli")
    role_bonus = doctrine_rules.get("complete_role_coverage_bonus_milli")
    maximum = doctrine_rules.get("maximum_total_multiplier_milli")
    values = (floor, per_point, mode_bonus, role_bonus, maximum)
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
        raise CommandRejectedError("mission_progress_rules_invalid")

    multiplier = 1000 + max(0, average - floor) * per_point
    expected_mode = mode_map.get(objective_kind)
    matching_mode = False
    modes = doctrine.get("mission_modes")
    if isinstance(expected_mode, str) and isinstance(modes, list):
        matching_mode = any(
            isinstance(row, Mapping) and row.get("mode") == expected_mode
            for row in modes
        )
        if matching_mode:
            multiplier += mode_bonus
    complete_role_coverage = bool(participants) and all(
        isinstance(roles.get(ref), str) and bool(roles.get(ref)) for ref in participants
    )
    if complete_role_coverage:
        multiplier += role_bonus
    multiplier = min(maximum, multiplier)
    return (
        multiplier,
        {
            "doctrine_applied": True,
            "matching_mission_mode": matching_mode,
            "complete_role_coverage": complete_role_coverage,
            "coordination_band": (
                "high" if average >= 80 else "established" if average >= 60 else "developing"
            ),
        },
        {
            team_path: team_digest,
            doctrine_path: doctrine_digest,
        },
    )


def _mission_progress_built_plan(
    self: Any,
    *,
    command: CommandEnvelope,
    meta: Mapping[str, Any],
    current_time: CampaignTime,
    path: str,
    owner: MissionOwner,
    summary: str,
    result: Mapping[str, Any],
    guarded_read_digests: Mapping[str, str],
    material_consequence_refs: tuple[str, ...],
) -> _BuiltPlan:
    """Frame one nonterminal mission-progress write using normal mission authority."""

    scene = self._mission_scene(current_time=current_time, owner=owner, summary=summary)
    scene["decision_required"] = None
    if scene.get("active_combat") is not True:
        scene["time_passage_allowed"] = True

    scheduler = self._load_scheduler(
        current_time=current_time,
        scene=self._scene_base(current_time),
    )
    self._sync_mission_scheduler(
        scheduler,
        owner=owner,
        path=path,
        current_time=current_time,
    )
    world_events = self._world_events()
    event_id = self._append_semantic_event(
        world_events,
        command=command,
        kind="mission_objective_progressed",
        at=current_time,
        host_refs=(owner.issuer_ref, owner.authority_ref),
        actor_refs=owner.mission.participant_refs,
        causal_refs=tuple(
            value
            for value in (owner.mission_id, owner.operation_ref)
            if isinstance(value, str)
        ),
        affected_owner_refs=(path, self.scheduler_path),
        material_consequence_refs=material_consequence_refs,
        audience_refs=(command.actor_id,),
        reducer_ref="shinobi_runtime.commands.mission_progression",
    )
    writes = {
        self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
        self.scene_path: _json_bytes(scene),
        path: _json_bytes(owner.to_record()),
        **self._world_event_writes(world_events),
        **self._scheduler_write_images(scheduler),
    }
    writes = self._prune_noop_writes(writes)
    expected_paths = tuple(sorted(writes))
    expected_record = owner.to_record()
    expected_read_digests = dict(guarded_read_digests)

    def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
        if overlay.changed_paths != expected_paths:
            raise ValueError("mission progress write set changed after planning")
        for guarded_path, guarded_digest in expected_read_digests.items():
            if self.repository.digest(guarded_path) != guarded_digest:
                raise ValueError("mission progress causal evidence changed after planning")
        self._assert_meta(
            overlay,
            manifest,
            meta_path=self.meta_path,
            command=command,
            world_time=current_time,
        )
        resolved = MissionOwner.from_record(overlay.read_json(path))
        if resolved.to_record() != expected_record:
            raise ValueError("mission progress after-image differs from reducer result")
        self._scheduler_from_reader(overlay)
        staged_events = overlay.read_json(WORLD_EVENT_REGISTRY_PATH).get("events", [])
        if not any(
            isinstance(item, Mapping)
            and item.get("id") == event_id
            and item.get("kind") == "mission_objective_progressed"
            for item in staged_events
        ):
            raise ValueError("mission progress lacks semantic history")
        staged_scene = overlay.read_json(self.scene_path)
        if (
            not isinstance(staged_scene, Mapping)
            or staged_scene.get("decision_required") is not None
            or (
                staged_scene.get("active_combat") is not True
                and staged_scene.get("time_passage_allowed") is not True
            )
        ):
            raise ValueError("mission progress scene handoff is incoherent")

    enriched = dict(result)
    enriched["semantic_event_id"] = event_id
    return _BuiltPlan(
        code="mission_objective_progress_ready",
        affected_refs=expected_paths,
        writes=writes,
        result=enriched,
        validator=validate,
    )


def _mission_objective_progress_resolution(
    self: Any,
    command: CommandEnvelope,
    meta: Mapping[str, Any],
    current_time: CampaignTime,
) -> _BuiltPlan:
    _exact_payload(
        command.payload,
        ("mission_id", "objective_id", "evidence_event_id"),
        command.command_type,
    )
    mission_id = _stable_id(command.payload.get("mission_id"), "mission_id_invalid", prefix="mission.")
    objective_id = _stable_id(command.payload.get("objective_id"), "mission_objective_id_invalid")
    evidence_event_id = _stable_id(
        command.payload.get("evidence_event_id"),
        "mission_objective_evidence_event_invalid",
        prefix="event.",
    )
    path, owner = self._read_mission(
        mission_id,
        actor_id=command.actor_id,
        current_time=current_time,
    )
    if owner.mission.state not in ("active", "resolving"):
        raise CommandRejectedError("mission_progress_not_available")
    objective = owner.mission.objective_by_id.get(objective_id)
    if objective is None or objective.status not in ("pending", "in_progress"):
        raise CommandRejectedError("mission_progress_not_available")

    token = _evidence_usage_token(mission_id, objective_id, evidence_event_id)
    if _evidence_already_used(self.repository, token):
        raise CommandRejectedError("mission_progress_evidence_already_used")

    # Reuse the existing mission evidence authority. A terminal world event may
    # be one completed action/exchange while the surrounding mission objective
    # remains nonterminal.
    _resolution_ref, evidence_digest = self._mission_objective_evidence(
        owner=owner,
        objective_id=objective_id,
        target_status="in_progress",
        progress_milli=min(999, objective.progress_milli + 1),
        evidence_event_id=evidence_event_id,
        current_time=current_time,
    )
    if not isinstance(evidence_digest, str) or not evidence_digest:
        raise CommandRejectedError("mission_objective_evidence_uncommitted")
    registry = self._world_events()
    evidence_event, _digest = self._world_event_record_and_digest(
        evidence_event_id,
        registry=registry,
    )
    if not isinstance(evidence_event, Mapping):
        raise CommandRejectedError("mission_objective_evidence_unavailable")

    rules = _progress_rules(self.repository)
    base_table = rules["base_progress_milli_by_event_kind"]
    event_kind = evidence_event.get("kind")
    base = base_table.get(event_kind) if isinstance(event_kind, str) else None
    maximum_progress = rules.get("maximum_nonterminal_progress_milli")
    if (
        isinstance(base, bool)
        or not isinstance(base, int)
        or base <= 0
        or isinstance(maximum_progress, bool)
        or not isinstance(maximum_progress, int)
        or not 1 <= maximum_progress < 1000
    ):
        raise CommandRejectedError("mission_progress_rules_invalid")

    multiplier, execution_profile, doctrine_digests = _team_doctrine_modifier(
        self,
        owner,
        objective.kind,
        rules,
    )
    delta = max(1, base * multiplier // 1000)
    next_progress = min(maximum_progress, objective.progress_milli + delta)
    if next_progress <= objective.progress_milli:
        raise CommandRejectedError("mission_progress_requires_terminal_evidence")
    try:
        updated_mission = update_objective(
            owner.mission,
            objective_id,
            "in_progress",
            progress_milli=next_progress,
            resolution_ref=None,
        )
    except (MissionTransitionError, ObjectiveDependencyError, KeyError, TypeError, ValueError) as exc:
        raise CommandRejectedError("mission_progress_invalid") from exc
    updated = owner.with_mission(updated_mission, effective_at=current_time)
    if not isinstance(updated, MissionOwner):
        # Production owner is strict. Keep direct unit fakes possible only when
        # the specialized builder itself is substituted by the test fixture.
        builder = getattr(self, "_mission_progress_built_plan", None)
        if not callable(builder):
            raise CommandRejectedError("mission_progress_owner_invalid")

    guarded = {**_evidence_guard(self.repository, evidence_event_id), **doctrine_digests}
    builder = getattr(self, "_mission_progress_built_plan", None)
    if not callable(builder):
        builder = _mission_progress_built_plan.__get__(self, type(self))
    return builder(
        command=command,
        meta=meta,
        current_time=current_time,
        path=path,
        owner=updated,
        summary=(
            f"Mission {mission_id} objective {objective_id} has new admissible field evidence at {current_time}."
        ),
        result={
            "command_type": command.command_type,
            "mission_id": mission_id,
            "objective_id": objective_id,
            "status": "in_progress",
            "previous_progress_milli": objective.progress_milli,
            "progress_milli": next_progress,
            "progress_delta_milli": next_progress - objective.progress_milli,
            "evidence_event_id": evidence_event_id,
            "evidence_event_kind": event_kind,
            "execution_profile": execution_profile,
        },
        guarded_read_digests=guarded,
        material_consequence_refs=(
            token,
            f"mission:{mission_id}:objective:{objective_id}:progress:{next_progress}",
        ),
    )


def _normalize_routine_mission_handoff(plan: _BuiltPlan, command: CommandEnvelope, scene_path: str) -> _BuiltPlan:
    if command.command_type not in _ROUTINE_HANDOFF_COMMANDS:
        return plan
    raw = plan.writes.get(scene_path)
    if raw is None:
        return plan
    try:
        scene = json.loads(raw.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
        return plan
    if not isinstance(scene, dict):
        return plan
    scene["decision_required"] = None
    if scene.get("active_combat") is not True:
        scene["time_passage_allowed"] = True
    writes = dict(plan.writes)
    writes[scene_path] = _json_bytes(scene)
    return _BuiltPlan(plan.code, plan.affected_refs, writes, plan.result, plan.validator)


def _install_handoff_normalizer() -> None:
    original = MissionCommandsMixin._mission_built_plan
    if getattr(original, "_mission_routine_handoff_normalizer", False):
        return

    @wraps(original)
    def wrapped(self: Any, *args: Any, **kwargs: Any) -> _BuiltPlan:
        plan = original(self, *args, **kwargs)
        command = kwargs.get("command")
        if not isinstance(command, CommandEnvelope):
            return plan
        scene_path = getattr(self, "scene_path", "state/scene.json")
        return _normalize_routine_mission_handoff(plan, command, scene_path)

    wrapped._mission_routine_handoff_normalizer = True  # type: ignore[attr-defined]
    MissionCommandsMixin._mission_built_plan = wrapped


def _register_planner(planner: type) -> None:
    planner.COMMAND_TYPES = frozenset(COMMAND_SPECS)
    setattr(planner, "_" + _COMMAND, _mission_objective_progress_resolution)
    setattr(planner, "_mission_progress_built_plan", _mission_progress_built_plan)


def install_mission_progression() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    COMMAND_SPECS.setdefault(
        _COMMAND,
        CommandSpec(
            required_fields=("mission_id", "objective_id", "evidence_event_id"),
            summary=(
                "Advance one active mission objective from one persisted admissible world-event evidence record. "
                "Progress is runtime-derived; exact-team doctrine can improve evidence efficiency but cannot create success."
            ),
            payload_hints={
                "mission_id": "mission.<id>",
                "objective_id": "<objective id from exact mission>",
                "evidence_event_id": "event.<id> already caused by this mission",
            },
            availability="active_or_resolving_mission_with_unused_admissible_nonterminal_evidence",
        ),
    )
    _install_handoff_normalizer()

    from shinobi_runtime.commands.planner import RepositoryCommandPlanner
    _register_planner(RepositoryCommandPlanner)
    try:
        from shinobi_runtime.commands.campaign_runtime_planner import CampaignCommandPlanner
        _register_planner(CampaignCommandPlanner)
    except ImportError:
        pass
    try:
        from shinobi_runtime.commands.campaign_mission_assignment import CampaignCommandPlanner as AssignedPlanner
        _register_planner(AssignedPlanner)
    except ImportError:
        pass
    try:
        from shinobi_runtime.commands.campaign_player_handoffs import CampaignCommandPlanner as PlayerHandoffPlanner
        _register_planner(PlayerHandoffPlanner)
    except ImportError:
        pass
    _INSTALLED = True


__all__ = [
    "install_mission_progression",
    "_evidence_already_used",
    "_evidence_guard",
    "_mission_objective_progress_resolution",
    "_mission_progress_built_plan",
    "_normalize_routine_mission_handoff",
    "_team_doctrine_modifier",
]
