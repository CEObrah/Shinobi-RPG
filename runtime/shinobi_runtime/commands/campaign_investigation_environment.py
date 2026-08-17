"""Environment-sensitive investigation work over the existing investigation owner.

This adapter does not create evidence or investigation state. It adjusts only the
quality of explicitly mapped fieldwork using deterministic derived environment
across the command interval, then delegates to the existing investigation
reducer and transaction builder unchanged.
"""
from __future__ import annotations

from contextvars import ContextVar
from functools import wraps
from typing import Any, Mapping, Sequence

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.environment_actions import environment_action_profile
from shinobi_runtime.sim.events import CampaignTime

_INSTALLED = False
_QUALITY_PROFILES: ContextVar[Mapping[tuple[str, ...], Mapping[str, Any]]] = ContextVar(
    "investigation_environment_quality_profiles",
    default={},
)


def _paths_key(paths: Sequence[str]) -> tuple[str, ...]:
    return tuple(value for value in paths if isinstance(value, str) and value)


def _adjust_quality(value: int, profile: Mapping[str, Any] | None) -> int:
    if not isinstance(profile, Mapping) or profile.get("applied") is not True:
        return value
    factor = profile.get("factor_milli")
    if isinstance(factor, bool) or not isinstance(factor, int) or factor <= 0:
        raise CommandRejectedError("investigation_environment_invalid")
    return max(0, (value * factor + 500) // 1000)


def _environment_context(
    planner: Any,
    command: CommandEnvelope,
    current_time: CampaignTime,
) -> tuple[dict[tuple[str, ...], Mapping[str, Any]], list[Mapping[str, Any]]]:
    action = command.payload.get("action")
    if action not in ("locate_scene", "examine_scene"):
        return {}, []
    try:
        target_time = CampaignTime.parse(command.payload.get("target_time"))
    except (TypeError, ValueError):
        return {}, []  # the base reducer owns the canonical target-time rejection

    place_ref = command.payload.get("place_ref")
    mission_ref = command.payload.get("mission_ref")
    objective_id = command.payload.get("objective_id")
    if (
        not isinstance(place_ref, str)
        or not place_ref
        or not isinstance(mission_ref, str)
        or not mission_ref
        or not isinstance(objective_id, str)
        or not objective_id
    ):
        return {}, []  # preserve the base reducer's exact input error ownership

    try:
        mechanics = planner._investigation_mechanics()
        _mission_path, mission_owner = planner._read_mission(
            mission_ref,
            actor_id=command.actor_id,
            current_time=current_time,
        )
        objective = mission_owner.mission.objective_by_id.get(objective_id)
        if objective is None:
            raise CommandRejectedError("mission_objective_not_found")
        brief = mission_owner.briefing.to_record() if mission_owner.briefing is not None else None
        if not isinstance(brief, Mapping):
            raise CommandRejectedError("investigation_briefing_missing")
        # Use the same site authority the base reducer enforces: command place,
        # fresh scene location, and mission briefing subject must agree. Checking
        # it here prevents sampling unrelated weather before the base reducer
        # rejects a stale/wrong location.
        scene = planner._scene_base(current_time)
        if (
            scene.get("location_id") != place_ref
            or brief.get("subject_ref") != place_ref
        ):
            raise CommandRejectedError("investigation_scene_location_required")
        _profile_ref, profile = planner._matching_profile(
            mechanics,
            objective.kind,
            brief,
        )
    except CommandRejectedError:
        raise
    except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
        raise CommandRejectedError("investigation_environment_invalid") from exc

    candidates: dict[tuple[str, ...], list[Mapping[str, Any]]] = {}
    summaries: list[Mapping[str, Any]] = []
    try:
        if action == "locate_scene":
            locate_cfg = profile["locate_scene"]
            paths = locate_cfg["skill_paths"]
            if not isinstance(paths, list):
                raise ValueError("investigation locate skills invalid")
            key = _paths_key(paths)
            env = environment_action_profile(
                planner.repository,
                start_time=current_time,
                end_time=target_time,
                place_ref=place_ref,
                action_key="investigation.locate_scene",
            )
            candidates.setdefault(key, []).append(env)
            if env.get("applied") is True:
                summaries.append(env)
        else:
            examine_cfg = profile["examine_scene"]
            roles = examine_cfg["roles"]
            if not isinstance(roles, Mapping):
                raise ValueError("investigation role config invalid")
            for role, role_cfg in sorted(roles.items()):
                if not isinstance(role, str) or not isinstance(role_cfg, Mapping):
                    raise ValueError("investigation role config invalid")
                paths = role_cfg.get("skills")
                if not isinstance(paths, list):
                    raise ValueError("investigation role skills invalid")
                key = _paths_key(paths)
                env = environment_action_profile(
                    planner.repository,
                    start_time=current_time,
                    end_time=target_time,
                    place_ref=place_ref,
                    action_key=f"investigation.examine_scene.{role}",
                )
                candidates.setdefault(key, []).append(env)
                if env.get("applied") is True:
                    summaries.append(env)
    except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
        raise CommandRejectedError("investigation_environment_invalid") from exc

    # `_quality` receives only the authored skill-path tuple. If two roles reuse
    # the exact same tuple but have different environment policies, do not leak a
    # mapped modifier into the neutral role. Ambiguous tuples remain neutral.
    by_paths: dict[tuple[str, ...], Mapping[str, Any]] = {}
    for key, profiles in candidates.items():
        factors = {
            int(profile.get("factor_milli", 1000))
            if isinstance(profile, Mapping) and profile.get("applied") is True
            else 1000
            for profile in profiles
        }
        if len(factors) == 1:
            by_paths[key] = profiles[0]
    return by_paths, summaries


def install_campaign_investigation_environment() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.commands import campaign_investigation as module

    planner = module.CampaignCommandPlanner
    original_quality = planner._quality
    original_resolution = planner._investigation_resolution

    if not getattr(original_quality, "_environment_action_profiles", False):
        def quality(record: Mapping[str, Any], paths: Sequence[str]) -> int:
            value = original_quality(record, paths)
            profile = _QUALITY_PROFILES.get().get(_paths_key(paths))
            return _adjust_quality(value, profile)

        quality._environment_action_profiles = True  # type: ignore[attr-defined]
        planner._quality = staticmethod(quality)

    if not getattr(original_resolution, "_environment_action_profiles", False):
        @wraps(original_resolution)
        def resolution(
            self: Any,
            command: CommandEnvelope,
            meta: Mapping[str, Any],
            current_time: CampaignTime,
        ) -> _BuiltPlan:
            profiles, summaries = _environment_context(self, command, current_time)
            token = _QUALITY_PROFILES.set(profiles)
            try:
                plan = original_resolution(self, command, meta, current_time)
            finally:
                _QUALITY_PROFILES.reset(token)
            if not summaries:
                return plan
            result = dict(plan.result)
            result["environment_effects"] = [dict(value) for value in summaries]
            return _BuiltPlan(
                plan.code,
                plan.affected_refs,
                plan.writes,
                result,
                plan.validator,
            )

        resolution._environment_action_profiles = True  # type: ignore[attr-defined]
        planner._investigation_resolution = resolution

    _INSTALLED = True


__all__ = [
    "install_campaign_investigation_environment",
    "_adjust_quality",
    "_environment_context",
]