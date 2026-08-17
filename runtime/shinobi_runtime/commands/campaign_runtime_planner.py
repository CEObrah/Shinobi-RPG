"""Production campaign planner freshness guards and living-world orchestration."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Mapping, Sequence

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.autonomy_error_boundaries import AutonomyErrorBoundaryMixin
from shinobi_runtime.commands.campaign_planner import CampaignCommandPlanner as _BaseCampaignCommandPlanner
from shinobi_runtime.commands.core import _BuiltPlan, _json_bytes
from shinobi_runtime.commands.development_breakthrough_dossier import DevelopmentBreakthroughDossierMixin
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.house_resource_conservation import HouseResourceConservationMixin
from shinobi_runtime.commands.rostered_house_progression import RosteredHouseProgressionMixin
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
from shinobi_runtime.people.repertoire import (
    field_usable_method_refs,
    technique_prerequisites_met,
    training_package_refs,
)
from shinobi_runtime.sim.events import CampaignTime

_TRANSIENT_TIME_HANDOFF_FIELDS = (
    "current_scene_type",
    "current_tension",
    "active_questions",
    "approaching_consequences",
    "available_reports",
)
_OPERATIONAL_TEAM_DRILL_CATEGORIES = frozenset(("combat", "tracking", "stealth"))
_TRAINING_PROGRESSION_PATH = "game/data/clans/training-progression.json"
_TECH_PACKAGES_PATH = "game/data/tech/packages.json"
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
_MISSING = object()


def _append_unique(values: list[str], value: object) -> None:
    if isinstance(value, str) and value and value not in values:
        values.append(value)


def _fresh_player_facing_time_handoff(
    result: Mapping[str, Any],
) -> tuple[list[str], list[str], list[str]]:
    """Derive only player-safe fresh scene pressure from newly settled results."""

    pressures: list[str] = []
    reports: list[str] = []
    approaching: list[str] = []

    actions = result.get("autonomous_actions", [])
    if isinstance(actions, list):
        for action in actions:
            if not isinstance(action, Mapping):
                continue
            if action.get("kind") == "player_mission_offer" and not action.get("skipped"):
                _append_unique(
                    pressures,
                    "A new mission offer from the Mission Office is awaiting review.",
                )
                _append_unique(
                    reports,
                    "The Mission Office has new operational tasking available for review.",
                )
            if action.get("kind") == "world_operation_progress" and action.get("status") == "succeeded":
                recipients = action.get("report_recipient_refs", [])
                if isinstance(recipients, list) and "pc_wei_tang" in recipients:
                    _append_unique(
                        pressures,
                        "A newly completed institutional operation has delivered a report to Wei.",
                    )
                    _append_unique(
                        reports,
                        "A sourced operational report addressed to Wei is ready for review.",
                    )

    team_reviews = result.get("team_reviews", [])
    if isinstance(team_reviews, list):
        for review in team_reviews:
            if not isinstance(review, Mapping):
                continue
            if review.get("kind") == "player_led_team_checkin":
                team_name = review.get("team_name")
                label = team_name if isinstance(team_name, str) and team_name else "Your team"
                _append_unique(
                    pressures,
                    f"{label} has a fresh internal check-in ready.",
                )
                _append_unique(
                    reports,
                    f"{label} has routine field, training, or readiness matters ready to discuss.",
                )
            if isinstance(review.get("training_commitment_id"), str):
                _append_unique(
                    approaching,
                    "A scheduled team-training obligation is now part of the near-term workload.",
                )

    return pressures[:12], reports[:6], approaching[:8]


def _refresh_time_advanced_plan(
    plan: _BuiltPlan,
    scene_path: str,
    *,
    previous_scene: Mapping[str, Any],
) -> _BuiltPlan:
    raw_scene = plan.writes.get(scene_path)
    if raw_scene is None:
        return plan
    try:
        scene = json.loads(raw_scene.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise CommandRejectedError("campaign_scene_invalid") from exc
    if not isinstance(scene, dict) or not isinstance(previous_scene, Mapping):
        raise CommandRejectedError("campaign_scene_invalid")

    changed = False
    pressures = scene.get("observable_pressures")
    prior_pressures = previous_scene.get("observable_pressures", _MISSING)
    if pressures is not None and not isinstance(pressures, list):
        raise CommandRejectedError("campaign_scene_invalid")
    if isinstance(pressures, list) and prior_pressures is not _MISSING:
        if not isinstance(prior_pressures, list):
            raise CommandRejectedError("campaign_scene_invalid")
        if pressures and pressures == prior_pressures:
            scene["observable_pressures"] = []
            changed = True

    narrative = scene.get("narrative")
    previous_narrative = previous_scene.get("narrative", {})
    if narrative is not None:
        if not isinstance(narrative, dict) or not isinstance(previous_narrative, Mapping):
            raise CommandRejectedError("campaign_scene_invalid")
        for field in _TRANSIENT_TIME_HANDOFF_FIELDS:
            if field not in narrative:
                continue
            previous_value = previous_narrative.get(field, _MISSING)
            if previous_value is not _MISSING and narrative.get(field) == previous_value:
                narrative.pop(field)
                changed = True

    fresh_pressures, fresh_reports, fresh_approaching = _fresh_player_facing_time_handoff(
        plan.result
    )
    if fresh_pressures:
        if scene.get("observable_pressures") != fresh_pressures:
            scene["observable_pressures"] = fresh_pressures
            changed = True
    if fresh_reports or fresh_approaching:
        if narrative is None:
            narrative = {}
            scene["narrative"] = narrative
            changed = True
        if fresh_reports and narrative.get("available_reports") != fresh_reports:
            narrative["available_reports"] = fresh_reports
            changed = True
        if fresh_approaching and narrative.get("approaching_consequences") != fresh_approaching:
            narrative["approaching_consequences"] = fresh_approaching
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
    RuntimeStabilityMixin,
    RosteredHouseProgressionMixin,
    HouseResourceConservationMixin,
    StandingTrainingMissionAbsenceMixin,
    StandingTrainingParticipationMixin,
    LivingWorldIntelligenceMixin,
    TeamLifecycleIntelligenceMixin,
    TeamIntelligenceMixin,
    _BaseCampaignCommandPlanner,
):
    """Production planner with living-world autonomy and stability guards."""

    def _training_progression_institutions(self) -> Mapping[str, Any]:
        try:
            data = self.repository.read_json(_TRAINING_PROGRESSION_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("training_progression_invalid") from exc
        institutions = data.get("institutions") if isinstance(data, Mapping) else None
        if not isinstance(institutions, Mapping):
            raise CommandRejectedError("training_progression_invalid")
        return institutions

    def _tech_package_registry(self) -> Mapping[str, Any]:
        try:
            data = self.repository.read_json(_TECH_PACKAGES_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("technique_package_registry_invalid") from exc
        packages = data.get("packages") if isinstance(data, Mapping) else None
        if not isinstance(packages, Mapping):
            raise CommandRejectedError("technique_package_registry_invalid")
        return packages

    @staticmethod
    def _package_methods(packages: Mapping[str, Any], package_ref: str) -> tuple[str, ...]:
        package = packages.get(package_ref)
        methods = package.get("methods") if isinstance(package, Mapping) else None
        if not isinstance(methods, list) or any(
            not isinstance(item, str) or not item for item in methods
        ):
            raise CommandRejectedError("technique_package_registry_invalid")
        return tuple(methods)

    def _house_protected_methods(
        self,
        institutions: Mapping[str, Any],
        packages: Mapping[str, Any],
    ) -> frozenset[str]:
        policy = institutions.get("house.tang")
        tiers = policy.get("technical_tiers") if isinstance(policy, Mapping) else None
        if not isinstance(tiers, Mapping):
            raise CommandRejectedError("training_progression_invalid")
        tier_refs: set[str] = set()
        house_methods: set[str] = set()
        for tier in tiers.values():
            package_ref = tier.get("package_ref") if isinstance(tier, Mapping) else None
            if not isinstance(package_ref, str) or not package_ref:
                raise CommandRejectedError("training_progression_invalid")
            tier_refs.add(package_ref)
            house_methods.update(self._package_methods(packages, package_ref))
        ordinary_methods: set[str] = set()
        for package_ref in packages:
            if package_ref in tier_refs or package_ref == "PKG_INVISIBLE_COURT_STYLE":
                continue
            ordinary_methods.update(self._package_methods(packages, package_ref))
        return frozenset(house_methods - ordinary_methods)

    def _house_technique_allowed(
        self,
        record: Mapping[str, Any],
        technique_ref: str,
        institutions: Mapping[str, Any],
        packages: Mapping[str, Any],
    ) -> bool:
        policy = institutions.get("house.tang")
        house_state = record.get("house_tang")
        standing = house_state.get("rank") if isinstance(house_state, Mapping) else None
        if not isinstance(policy, Mapping) or not isinstance(standing, str):
            return False
        mapping = policy.get("standing_to_technical_tier")
        order = policy.get("technical_tier_order")
        tiers = policy.get("technical_tiers")
        if not isinstance(mapping, Mapping) or not isinstance(order, list) or not isinstance(tiers, Mapping):
            raise CommandRejectedError("training_progression_invalid")
        tier_id = mapping.get(standing)
        if not isinstance(tier_id, str) or tier_id not in order:
            raise CommandRejectedError("house_technical_tier_unresolved")
        allowed: set[str] = set()
        for current_tier in order:
            tier = tiers.get(current_tier)
            package_ref = tier.get("package_ref") if isinstance(tier, Mapping) else None
            if not isinstance(package_ref, str):
                raise CommandRejectedError("training_progression_invalid")
            allowed.update(self._package_methods(packages, package_ref))
            if current_tier == tier_id:
                break
        return technique_ref in allowed

    def _clan_policy_for_method(
        self,
        technique_ref: str,
        institutions: Mapping[str, Any],
        packages: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        matches: list[Mapping[str, Any]] = []
        for institution_ref, policy in institutions.items():
            if institution_ref == "house.tang" or not isinstance(policy, Mapping):
                continue
            package_ref = policy.get("membership_package_ref")
            if not isinstance(package_ref, str):
                continue
            if technique_ref in self._package_methods(packages, package_ref):
                matches.append(policy)
        if len(matches) > 1:
            raise CommandRejectedError("technique_institution_access_ambiguous")
        return matches[0] if matches else None

    def _clan_technique_allowed(
        self,
        record: Mapping[str, Any],
        technique_ref: str,
        policy: Mapping[str, Any],
        packages: Mapping[str, Any],
    ) -> bool:
        package_ref = policy.get("membership_package_ref")
        if not isinstance(package_ref, str) or package_ref not in training_package_refs(record):
            return False
        methods = self._package_methods(packages, package_ref)
        try:
            index = methods.index(technique_ref)
            known = field_usable_method_refs(record)
        except ValueError as exc:
            raise CommandRejectedError("technique_repertoire_invalid") from exc
        if index == 0:
            return True
        return methods[index - 1] in known

    def _assert_institutional_technique_access(
        self,
        student: Mapping[str, Any],
        teacher: Mapping[str, Any],
        technique_ref: str,
    ) -> None:
        institutions = self._training_progression_institutions()
        packages = self._tech_package_registry()
        if technique_ref in self._house_protected_methods(institutions, packages):
            if not self._house_technique_allowed(student, technique_ref, institutions, packages):
                raise CommandRejectedError("house_technical_tier_access_denied")
            if not self._house_technique_allowed(teacher, technique_ref, institutions, packages):
                raise CommandRejectedError("house_teacher_access_denied")
            return
        policy = self._clan_policy_for_method(technique_ref, institutions, packages)
        if policy is None:
            return
        if not self._clan_technique_allowed(student, technique_ref, policy, packages):
            raise CommandRejectedError("clan_instruction_access_denied")
        if not self._clan_technique_allowed(teacher, technique_ref, policy, packages):
            raise CommandRejectedError("clan_teacher_access_denied")

    @staticmethod
    def _technique_prerequisites_met(
        student: Mapping[str, Any],
        record: Mapping[str, Any],
    ) -> bool:
        try:
            return technique_prerequisites_met(student, record)
        except ValueError as exc:
            raise CommandRejectedError("technique_repertoire_invalid") from exc

    def _technique_learning_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        if command.payload.get("action") == "begin":
            student_ref = command.payload.get("student_ref")
            teacher_ref = command.payload.get("teacher_ref")
            technique_ref = command.payload.get("technique_ref")
            if (
                isinstance(student_ref, str)
                and student_ref == command.actor_id
                and isinstance(teacher_ref, str)
                and teacher_ref != student_ref
                and isinstance(technique_ref, str)
            ):
                _student_path, student = self._resolve_actor_for_write(student_ref)
                _teacher_path, teacher = self._resolve_actor_for_write(teacher_ref)
                self._assert_institutional_technique_access(
                    student,
                    teacher,
                    technique_ref,
                )
        return super()._technique_learning_resolution(command, meta, current_time)

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
        try:
            previous_scene = self.repository.read_json(self.scene_path)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("campaign_scene_invalid") from exc
        if not isinstance(previous_scene, Mapping):
            raise CommandRejectedError("campaign_scene_invalid")

        plan = super()._advance_time(command, meta, current_time)
        plan = _guard_plan_validator(plan, "advance_time_base_validation_invalid")
        plan = self._apply_house_progression_to_time_plan(plan)
        plan = _refresh_time_advanced_plan(
            plan,
            self.scene_path,
            previous_scene=previous_scene,
        )
        return _guard_plan_validator(plan, "advance_time_composed_validation_invalid")


__all__ = ["CampaignCommandPlanner"]
