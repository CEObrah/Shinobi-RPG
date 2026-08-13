"""Production campaign planner freshness guards and living-world orchestration.

The generic planner preserves scene narrative metadata across time settlement.
That is useful for stable history, but temporal handoff fields can become false
once the campaign clock moves. This production extension removes only fields
whose truth is tied to the pre-advance decision surface while composing bounded
living-world behavior over the existing authoritative domain reducers.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Mapping, Sequence

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.autonomy_error_boundaries import AutonomyErrorBoundaryMixin
from shinobi_runtime.commands.campaign_planner import CampaignCommandPlanner as _BaseCampaignCommandPlanner
from shinobi_runtime.commands.core import _BuiltPlan, _json_bytes
from shinobi_runtime.commands.development_breakthrough_dossier import DevelopmentBreakthroughDossierMixin
from shinobi_runtime.commands.development_cursor_authority import DevelopmentCursorAuthorityMixin
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.house_resource_conservation import HouseResourceConservationMixin
from shinobi_runtime.commands.institution_team_orders import InstitutionTeamOrdersMixin
from shinobi_runtime.commands.living_world_intelligence import LivingWorldIntelligenceMixin
from shinobi_runtime.commands.mission_player_agency import MissionPlayerAgencyMixin
from shinobi_runtime.commands.mission_subject_transport import MissionSubjectTransportMixin
from shinobi_runtime.commands.player_mission_offer_policy import PlayerMissionOfferPolicyMixin
from shinobi_runtime.commands.player_mission_reward_funding import PlayerMissionRewardFundingMixin
from shinobi_runtime.commands.runtime_stability import RuntimeStabilityMixin
from shinobi_runtime.commands.standing_training_mission_absence import StandingTrainingMissionAbsenceMixin
from shinobi_runtime.commands.standing_training_participation import StandingTrainingParticipationMixin
from shinobi_runtime.commands.team_intelligence import TeamIntelligenceMixin
from shinobi_runtime.commands.team_lifecycle_intelligence import TeamLifecycleIntelligenceMixin
from shinobi_runtime.sim.events import CampaignTime

_TRANSIENT_TIME_HANDOFF_FIELDS = (
    "current_scene_type",
    "current_tension",
    "active_questions",
    "approaching_consequences",
)
_OPERATIONAL_TEAM_DRILL_CATEGORIES = frozenset(("combat", "tracking", "stealth"))
_VALIDATOR_FAILURE_SUFFIXES = {
    "time command write set changed after planning": "write_set",
    "time command core clocks diverge": "core_clocks",
    "causal interrupt did not close time passage": "interrupt_scene",
    "canon pressure after-image differs from causal review": "canon_pressure_after_image",
    "faction review after-image differs from plan": "faction_after_image",
    "world registry review after-image differs from plan": "world_registry_after_image",
    "house review after-image differs from plan": "house_after_image",
    "autonomous owner after-image differs from plan": "autonomous_owner_after_image",
    "population demographic after-image differs from plan": "population_after_image",
    "person continuity after-image differs from plan": "person_continuity_after_image",
    "commitment due after-image differs from plan": "commitment_after_image",
    "time semantic history after-image differs from plan": "world_event_after_image",
    "living-world House progression changed write set after planning": "house_progression_write_set",
    "House progression after-image differs from settled plan": "house_progression_after_image",
    "House development cursor advanced beyond reviewed time": "house_progression_cursor",
}


def _refresh_time_advanced_plan(plan: _BuiltPlan, scene_path: str) -> _BuiltPlan:
    raw_scene = plan.writes.get(scene_path)
    if raw_scene is None:
        return plan
    try:
        scene = json.loads(raw_scene.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise CommandRejectedError("campaign_scene_invalid") from exc
    if not isinstance(scene, dict):
        raise CommandRejectedError("campaign_scene_invalid")

    changed = False
    pressures = scene.get("observable_pressures")
    if isinstance(pressures, list) and pressures:
        scene["observable_pressures"] = []
        changed = True
    elif pressures is not None and not isinstance(pressures, list):
        raise CommandRejectedError("campaign_scene_invalid")

    narrative = scene.get("narrative")
    if narrative is not None:
        if not isinstance(narrative, dict):
            raise CommandRejectedError("campaign_scene_invalid")
        for field in _TRANSIENT_TIME_HANDOFF_FIELDS:
            if field in narrative:
                narrative.pop(field)
                changed = True

    if not changed:
        return plan
    writes: Dict[str, bytes] = dict(plan.writes)
    writes[scene_path] = _json_bytes(scene)
    return _BuiltPlan(
        code=plan.code,
        affected_refs=plan.affected_refs,
        writes=writes,
        result=plan.result,
        validator=plan.validator,
    )


def _validation_failure_code(code: str, exc: Exception) -> str:
    suffix = _VALIDATOR_FAILURE_SUFFIXES.get(str(exc), "structural")
    return f"{code}__{suffix}"


def _guard_plan_validator(
    plan: _BuiltPlan,
    code: str,
    *,
    overlay_adapter: Callable[[Any], Any] | None = None,
) -> _BuiltPlan:
    """Keep internal after-image failures distinct from caller input errors."""

    original = plan.validator

    def validate(overlay: Any, manifest: Any) -> None:
        candidate = overlay_adapter(overlay) if overlay_adapter is not None else overlay
        try:
            original(candidate, manifest)
        except CommandRejectedError:
            raise
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError(_validation_failure_code(code, exc)) from exc

    return _BuiltPlan(
        code=plan.code,
        affected_refs=plan.affected_refs,
        writes=plan.writes,
        result=plan.result,
        validator=validate,
    )


class CampaignCommandPlanner(
    PlayerMissionRewardFundingMixin,
    PlayerMissionOfferPolicyMixin,
    MissionPlayerAgencyMixin,
    MissionSubjectTransportMixin,
    AutonomyErrorBoundaryMixin,
    InstitutionTeamOrdersMixin,
    DevelopmentBreakthroughDossierMixin,
    DevelopmentCursorAuthorityMixin,
    RuntimeStabilityMixin,
    HouseResourceConservationMixin,
    StandingTrainingMissionAbsenceMixin,
    StandingTrainingParticipationMixin,
    LivingWorldIntelligenceMixin,
    TeamLifecycleIntelligenceMixin,
    TeamIntelligenceMixin,
    _BaseCampaignCommandPlanner,
):
    """Production planner with living-world autonomy and stability guards."""

    def _training_facility_capacity(
        self,
        location_ref: str,
        *,
        required_slots: int,
        base_quality_factor: object,
        required_categories: Sequence[str] = (),
        module_required: bool = False,
    ) -> tuple[str, str]:
        try:
            return super()._training_facility_capacity(
                location_ref,
                required_slots=required_slots,
                base_quality_factor=base_quality_factor,
                required_categories=required_categories,
                module_required=module_required,
            )
        except CommandRejectedError as exc:
            if exc.code != "training_facility_category_unsupported" or "team_drill" not in required_categories:
                raise
        relaxed = tuple(
            category
            for category in required_categories
            if category not in _OPERATIONAL_TEAM_DRILL_CATEGORIES
        )
        return super()._training_facility_capacity(
            location_ref,
            required_slots=required_slots,
            base_quality_factor=base_quality_factor,
            required_categories=relaxed,
            module_required=module_required,
        )

    def _advance_time(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        plan = super()._advance_time(command, meta, current_time)
        base_writes = plan.writes
        plan = _guard_plan_validator(
            plan,
            "advance_time_base_validation_invalid",
            overlay_adapter=lambda overlay: self._development_cursor_validation_view(
                overlay, base_writes
            ),
        )
        plan = self._apply_house_progression_to_time_plan(plan)
        plan = _refresh_time_advanced_plan(plan, self.scene_path)
        return _guard_plan_validator(plan, "advance_time_composed_validation_invalid")


__all__ = ["CampaignCommandPlanner"]
