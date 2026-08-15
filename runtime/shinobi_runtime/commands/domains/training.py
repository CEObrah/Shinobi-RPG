"""Extracted semantic command domain from the repository command planner.

The mixin owns domain reducers; orchestration, transaction framing, shared owner
resolution, and causal scheduler settlement remain on RepositoryCommandPlanner.
"""

from __future__ import annotations

import copy
import json
from decimal import (
    Decimal,
    ROUND_CEILING,
)
from typing import (
    Any,
    Dict,
    Mapping,
    Optional,
    Tuple,
)

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.core import (
    _BuiltPlan, _OwnerResolutionCache, _campaign_datetime, _declared_payload, _exact_payload, _json_bytes, _stable_id,
)
from shinobi_runtime.commands.constants import TRAINABLE_ROOTS as _TRAINABLE_ROOTS
from shinobi_runtime.commands.paths import (
    DEVELOPMENT_BANK_PATH as _DEVELOPMENT_BANK_PATH,
    TRAINING_MODELS_PATH as _TRAINING_MODELS_PATH,
    RECOVERY_POLICY_PATH as _RECOVERY_POLICY_PATH,
)
from shinobi_runtime.reducers import (
    TrainingInputs,
    settle_recovery,
    settle_training,
)
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.sim.scheduler import CausalSchedulerRegistry
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest


class TrainingCommandsMixin:
    @staticmethod
    def _training_target(record: Dict[str, Any], target: object) -> Tuple[Dict[str, Any], str, int]:
        if not isinstance(target, str) or len(target) > 128:
            raise CommandRejectedError("training_target_invalid")
        parts = target.split(".")
        if len(parts) == 2:
            root, leaf = parts
            root_key = root
            container = record.get(root)
        elif len(parts) == 3 and parts[:2] == ["repertoire", "method_mastery"]:
            root_key = "repertoire.method_mastery"
            repertoire = record.get("repertoire")
            container = repertoire.get("method_mastery") if isinstance(repertoire, dict) else None
            leaf = parts[2]
        else:
            raise CommandRejectedError("training_target_invalid")
        if root_key not in _TRAINABLE_ROOTS or not isinstance(container, dict):
            raise CommandRejectedError("training_target_invalid")
        value = container.get(leaf)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise CommandRejectedError("training_target_invalid")
        return container, leaf, value
    @staticmethod
    def _training_aptitude(record: Mapping[str, Any], target: str) -> int:
        aptitude = record.get("aptitude")
        if not isinstance(aptitude, Mapping):
            raise CommandRejectedError("training_aptitude_missing")
        if target.startswith("operational_skills."):
            key = "tactical_learning"
        elif target.startswith("martial_skills.") or target.startswith("attributes."):
            key = "technical_learning" if target.startswith("martial_skills.") else "physical_learning"
        elif target.startswith("chakra_dimensions."):
            key = "chakra_learning"
        elif target.startswith("domain_proficiencies.wind"):
            key = "nature_transformation_learning"
        elif target.startswith("domain_proficiencies.sensory"):
            key = "sensory_learning"
        elif target.startswith("domain_proficiencies.medical"):
            key = "medical_learning"
        elif target.startswith("domain_proficiencies.genjutsu"):
            key = "genjutsu_learning"
        elif target.startswith("domain_proficiencies."):
            key = "chakra_learning"
        else:
            key = "technical_learning"
        value = aptitude.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise CommandRejectedError("training_aptitude_missing")
        return value
    @staticmethod
    def _health_recovery_factor(record: Mapping[str, Any]) -> Tuple[str, str]:
        condition = record.get("condition")
        if not isinstance(condition, Mapping):
            return "1", "1"
        readiness = condition.get("readiness")
        injuries = condition.get("injuries")
        if readiness == "ready" and isinstance(injuries, list) and not injuries:
            return "1", "1"
        if readiness in ("injured", "limited"):
            return "0.65", "0.75"
        return "0.85", "0.90"
    def _training_model(self, model_ref: str) -> Mapping[str, Any]:
        try:
            registry = self.repository.read_json(_TRAINING_MODELS_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("training_model_registry_invalid") from exc
        models = registry.get("models") if isinstance(registry, Mapping) else None
        model = models.get(model_ref) if isinstance(models, Mapping) else None
        if not isinstance(model, Mapping):
            raise CommandRejectedError("training_model_invalid")
        factors = model.get("base_factors")
        if not isinstance(factors, Mapping):
            raise CommandRejectedError("training_model_registry_invalid")
        return model
    @staticmethod
    def _cohort_training_aptitude(profile: Mapping[str, Any], target: str) -> int:
        distributions = profile.get("numeric_distributions")
        if not isinstance(distributions, Mapping):
            raise CommandRejectedError("training_cohort_profile_invalid")
        logical_target = target[6:] if target.startswith("stats.") else target
        if logical_target.startswith("operational_skills."):
            key = "aptitude.tactical_learning"
        elif logical_target.startswith("martial_skills."):
            key = "aptitude.technical_learning"
        elif logical_target.startswith("attributes."):
            key = "aptitude.physical_learning"
        elif logical_target.startswith("chakra_dimensions."):
            key = "aptitude.chakra_learning"
        elif logical_target.startswith("domain_proficiencies.wind"):
            key = "aptitude.nature_transformation_learning"
        elif logical_target.startswith("domain_proficiencies.sensory"):
            key = "aptitude.sensory_learning"
        elif logical_target.startswith("domain_proficiencies.medical"):
            key = "aptitude.medical_learning"
        elif logical_target.startswith("domain_proficiencies.genjutsu"):
            key = "aptitude.genjutsu_learning"
        elif logical_target.startswith("domain_proficiencies."):
            key = "aptitude.chakra_learning"
        else:
            key = "aptitude.technical_learning"
        distribution = distributions.get(key)
        mean = distribution.get("mean") if isinstance(distribution, Mapping) else None
        if isinstance(mean, bool) or not isinstance(mean, (int, float)) or mean < 0:
            raise CommandRejectedError("training_cohort_aptitude_missing")
        return int(Decimal(str(mean)).to_integral_value(rounding=ROUND_CEILING))
    def _cohort_training_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
        *,
        cohort_ref: str,
        target: object,
        model_ref: str,
        model: Mapping[str, Any],
        context_ref: Optional[str],
        instructor_ref: Optional[str],
        target_time: CampaignTime,
        active_hours: Decimal,
    ) -> _BuiltPlan:
        if context_ref is None or instructor_ref is None:
            raise CommandRejectedError("training_context_required")
        try:
            owner_path, _owner_digest, owner_view = self._resolve_covered_owner_view(
                context_ref, cache=_OwnerResolutionCache()
            )
        except CommandRejectedError as exc:
            raise CommandRejectedError("training_context_invalid") from exc
        owner = copy.deepcopy(self.repository.read_json(owner_path))
        if not isinstance(owner, dict) or owner.get("id") != context_ref:
            raise CommandRejectedError("training_context_invalid")
        cohorts = owner.get("cohorts")
        if not isinstance(cohorts, list):
            raise CommandRejectedError("training_context_invalid")
        matches = [item for item in cohorts if isinstance(item, dict) and item.get("id") == cohort_ref]
        if len(matches) != 1:
            raise CommandRejectedError("training_cohort_invalid")
        cohort = matches[0]
        profile = cohort.get("cohort_profile")
        if not isinstance(profile, dict) or profile.get("representation") != "house_cohort":
            raise CommandRejectedError("training_cohort_invalid")
        count = cohort.get("aggregate_count")
        roster_refs = cohort.get("roster_refs")
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or count <= 0
            or not isinstance(roster_refs, list)
            or len(roster_refs) != count
        ):
            raise CommandRejectedError("training_cohort_invalid")
        authority = self._domain_authority().owner_leadership(
            holder_ref=command.actor_id, owner_ref=context_ref
        )
        if not authority.allowed:
            raise CommandRejectedError("training_actor_not_authorized")
        member_ids = owner.get("member_ids")
        if not isinstance(member_ids, list) or instructor_ref not in member_ids:
            raise CommandRejectedError("training_instructor_not_authorized")
        _instructor_path, instructor = self._resolve_actor_for_write(instructor_ref)
        if instructor.get("life_status") not in ("active", "alive"):
            raise CommandRejectedError("training_instructor_unavailable")
        home = owner.get("home")
        if not isinstance(home, str) or instructor.get("current_location_id") != home:
            raise CommandRejectedError("training_instructor_unavailable")

        distributions = profile.get("numeric_distributions")
        distribution = distributions.get(target) if isinstance(distributions, dict) and isinstance(target, str) else None
        if not isinstance(distribution, dict):
            raise CommandRejectedError("training_target_invalid")
        mean = distribution.get("mean")
        if isinstance(mean, bool) or not isinstance(mean, (int, float)) or mean < 0:
            raise CommandRejectedError("training_target_invalid")
        current_value = int(Decimal(str(mean)).to_integral_value(rounding=ROUND_CEILING))
        aptitude = self._cohort_training_aptitude(profile, target)
        factors = model.get("base_factors")
        if not isinstance(factors, Mapping):
            raise CommandRejectedError("training_model_registry_invalid")
        instructor_quality = max(
            Decimal("0.85"),
            min(
                Decimal("1.20"),
                Decimal("0.90")
                + Decimal(self._training_aptitude(instructor, target[6:] if target.startswith("stats.") else target))
                / Decimal(500),
            ),
        )
        facility_slots, facility_quality_factor = self._training_facility_capacity(
            home,
            required_slots=count,
            base_quality_factor=factors["facility_quality"],
        )
        development = profile.get("development")
        if not isinstance(development, dict):
            development = {"resolved_through": str(current_time), "credits": {}, "model": "representation_neutral_cohort"}
            profile["development"] = development
        credits = development.get("credits")
        if not isinstance(credits, dict):
            raise CommandRejectedError("training_cohort_profile_invalid")
        residual = credits.get(str(target), 0)
        try:
            outcome = settle_training(
                TrainingInputs(
                    scheduled_hours=str(active_hours),
                    attendance="1",
                    available_instructor_hours=str(active_hours),
                    required_instructor_hours=str(active_hours),
                    facility_slots=facility_slots,
                    required_slots=str(count),
                    equipment_sets=str(count),
                    required_sets=str(count),
                    instructor_quality_factor=str(instructor_quality),
                    facility_quality_factor=facility_quality_factor,
                    equipment_factor=factors["equipment"],
                    health_factor="1",
                    recovery_factor="1",
                    relevance_factor=factors["relevance"],
                    difficulty_fit_factor=factors["difficulty_fit"],
                    aptitude=aptitude,
                    experience_modifier="1",
                    current_value=current_value,
                    residual_units=residual,
                    representation="rostered_cohort",
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CommandRejectedError("training_resolution_invalid") from exc

        base = self._time_spanning_base(command, meta, current_time, target_time=target_time)
        if base.result.get("interrupted"):
            raise CommandRejectedError("time_boundary_requires_domain_settlement")
        if CampaignTime.parse(base.result["world_time"]) != target_time:
            raise CommandRejectedError("training_time_settlement_incomplete")
        shift = outcome.points_gained
        if shift:
            for key in ("mean", "min", "max"):
                value = distribution.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise CommandRejectedError("training_cohort_profile_invalid")
                distribution[key] = round(float(value) + shift, 6)
        credits[str(target)] = float(outcome.residual_units)
        development["resolved_through"] = str(target_time)

        world_events = self._world_events_after(base)
        scene_after = json.loads(base.writes[self.scene_path].decode("utf-8"))
        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind="training_resolved",
            at=target_time,
            host_refs=(context_ref, cohort_ref),
            actor_refs=(instructor_ref,),
            place_refs=(home,),
            affected_owner_refs=(owner_path,),
            material_consequence_refs=(
                f"cohort-training:{cohort_ref}:{target}:{current_value}->{current_value + shift}",
            ),
            audience_refs=(command.actor_id,),
            reducer_ref="shinobi_runtime.reducers.training.settle_training",
        )
        scene_after["scene_summary"] = (
            f"Cohort training resolves for {cohort_ref} through {target_time}; "
            f"{target} shifts by {shift} point(s) across the cohort baseline."
        )
        scene_after["decision_required"] = "Choose the next consequential action."
        writes = dict(base.writes)
        writes[self.meta_path] = _json_bytes(self._meta_after(meta, command, world_time=target_time))
        writes[self.scene_path] = _json_bytes(scene_after)
        writes[owner_path] = _json_bytes(owner)
        writes.update(self._world_event_writes(world_events))
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))
        before_roster = tuple(roster_refs)

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("cohort training write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=target_time)
            staged_owner = overlay.read_json(owner_path)
            staged_matches = [item for item in staged_owner.get("cohorts", []) if isinstance(item, Mapping) and item.get("id") == cohort_ref]
            if len(staged_matches) != 1:
                raise ValueError("cohort training lost its cohort owner")
            staged = staged_matches[0]
            if staged.get("aggregate_count") != count or tuple(staged.get("roster_refs", [])) != before_roster:
                raise ValueError("cohort training changed represented headcount or identities")

        return _BuiltPlan(
            code="training_resolution_ready",
            affected_refs=expected_paths,
            writes=writes,
            result={
                "command_type": command.command_type,
                "actor_ref": cohort_ref,
                "representation": "rostered_cohort",
                "context_ref": context_ref,
                "instructor_ref": instructor_ref,
                "model_ref": model_ref,
                "target": target,
                "represented_people": count,
                "points_gained": outcome.points_gained,
                "residual_units": str(outcome.residual_units),
                "world_time": str(target_time),
                "semantic_event_id": event_id,
            },
            validator=validate,
        )
    def _training_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _declared_payload(command.payload, command.command_type)
        actor_ref = _stable_id(command.payload["actor_ref"], "training_actor_invalid")
        target = command.payload["target"]
        model_ref = _stable_id(command.payload["model_ref"], "training_model_invalid", prefix="training.")
        context_raw = command.payload.get("context_ref")
        context_ref = None if context_raw is None else _stable_id(context_raw, "training_context_invalid")
        instructor_raw = command.payload.get("instructor_ref")
        instructor_ref = None if instructor_raw is None else _stable_id(instructor_raw, "training_instructor_invalid")
        model = self._training_model(model_ref)
        factors = model["base_factors"]
        try:
            target_time = CampaignTime.parse(command.payload["target_time"])
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("training_target_time_invalid") from exc
        if target_time <= current_time:
            raise CommandRejectedError("training_target_time_invalid")
        try:
            active_hours = Decimal(str(command.payload["active_hours"]))
        except Exception as exc:
            raise CommandRejectedError("training_active_hours_invalid") from exc
        elapsed_hours = Decimal(
            int((_campaign_datetime(target_time) - _campaign_datetime(current_time)).total_seconds())
        ) / Decimal(3600)
        if not active_hours.is_finite() or active_hours <= 0 or active_hours > elapsed_hours:
            raise CommandRejectedError("training_active_hours_invalid")

        if model.get("context_kind") == "cohort":
            return self._cohort_training_resolution(
                command,
                meta,
                current_time,
                cohort_ref=actor_ref,
                target=target,
                model_ref=model_ref,
                model=model,
                context_ref=context_ref,
                instructor_ref=instructor_ref,
                target_time=target_time,
                active_hours=active_hours,
            )

        actor_path, actor = self._resolve_actor_for_write(actor_ref)
        if actor.get("life_status") not in ("active", "alive"):
            raise CommandRejectedError("training_actor_not_active")

        context_kind = model.get("context_kind")
        team: Optional[Dict[str, Any]] = None
        membership_team_ref: Optional[str] = None
        membership_team_path: Optional[str] = None
        facilities: list[str] = []
        if context_kind == "exact_team":
            if context_ref is None:
                raise CommandRejectedError("training_context_required")
            membership_team_ref = context_ref
            membership_team_path, team_view = self._exact_team(context_ref)
            team = copy.deepcopy(dict(team_view))
            training = team.get("training")
            if not isinstance(training, Mapping) or training.get("model_ref") != model_ref:
                raise CommandRejectedError("training_model_not_available")
            if actor_ref not in team.get("member_refs", []):
                raise CommandRejectedError("training_model_not_available")
            if actor_ref != command.actor_id and team.get("leader_ref") != command.actor_id:
                raise CommandRejectedError("training_actor_not_authorized")
            raw_facilities = training.get("facility_refs", [])
            if not isinstance(raw_facilities, list) or any(not isinstance(x, str) for x in raw_facilities):
                raise CommandRejectedError("training_context_invalid")
            facilities = list(raw_facilities)
        elif context_kind == "individual":
            if context_ref is not None or actor_ref != command.actor_id:
                raise CommandRejectedError("training_actor_not_authorized")
        else:
            raise CommandRejectedError("training_model_registry_invalid")

        requires_instructor = model.get("requires_instructor")
        if not isinstance(requires_instructor, bool):
            raise CommandRejectedError("training_model_registry_invalid")
        instructor_record: Optional[Mapping[str, Any]] = None
        instructor_quality_factor = Decimal(str(factors["instructor_quality"]))
        if requires_instructor:
            if instructor_ref is None or team is None:
                raise CommandRejectedError("training_instructor_required")
            training = team.get("training")
            allowed = training.get("instructor_refs") if isinstance(training, Mapping) else None
            if not isinstance(allowed, list) or instructor_ref not in allowed:
                raise CommandRejectedError("training_instructor_not_authorized")
            _instructor_path, instructor_record = self._resolve_actor_for_write(instructor_ref)
            if instructor_record.get("life_status") not in ("active", "alive"):
                raise CommandRejectedError("training_instructor_unavailable")
            if instructor_record.get("current_location_id") != actor.get("current_location_id"):
                raise CommandRejectedError("training_instructor_unavailable")
            # Instructor quality is derived from the actual instructor rather than
            # a team-named multiplier. Different teams therefore use the same
            # mechanics while their people and doctrine remain consequential.
            instructor_aptitude = self._training_aptitude(instructor_record, target)
            instructor_quality_factor = max(
                Decimal("0.85"),
                min(Decimal("1.20"), Decimal("0.90") + Decimal(instructor_aptitude) / Decimal(500)),
            )
        elif instructor_ref is not None:
            raise CommandRejectedError("training_instructor_not_required")

        actor_location_ref = actor.get("current_location_id")
        if not isinstance(actor_location_ref, str) or not actor_location_ref:
            raise CommandRejectedError("training_context_invalid")
        if facilities and actor_location_ref not in facilities:
            raise CommandRejectedError("training_facility_unavailable")
        facility_slots, facility_quality_factor = self._training_facility_capacity(
            actor_location_ref,
            required_slots=1,
            base_quality_factor=factors["facility_quality"],
            required_categories=(self._training_category_for_target(str(target)),),
            module_required=bool(facilities),
        )

        if team is not None and membership_team_ref is not None:
            if instructor_ref is None:
                raise CommandRejectedError("training_instructor_required")
            self._record_team_training_session(
                team,
                session_ref="training.session." + command.digest[:32],
                member_targets={actor_ref: str(target)},
                instructor_ref=instructor_ref,
                started_at=current_time,
                ended_at=target_time,
                active_hours=active_hours,
            )

        base = self._time_spanning_base(
            command, meta, current_time, target_time=target_time
        )
        if base.result.get("interrupted"):
            raise CommandRejectedError("time_boundary_requires_domain_settlement")
        reached = CampaignTime.parse(base.result["world_time"])
        if reached != target_time:
            raise CommandRejectedError("training_time_settlement_incomplete")

        container, leaf, current_value = self._training_target(actor, target)
        aptitude = self._training_aptitude(actor, target)
        health_factor, recovery_factor = self._health_recovery_factor(actor)

        try:
            banks = copy.deepcopy(self.repository.read_json(_DEVELOPMENT_BANK_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("development_bank_invalid") from exc
        entries = banks.get("entries") if isinstance(banks, dict) else None
        if not isinstance(entries, dict):
            raise CommandRejectedError("development_bank_invalid")
        entry = entries.get(actor_ref)
        if entry is None:
            entry = {
                "owner_type": "character",
                "resolved_through": str(current_time),
                "credits": {},
            }
            entries[actor_ref] = entry
        if not isinstance(entry, dict) or not isinstance(entry.get("credits"), dict):
            raise CommandRejectedError("development_bank_invalid")
        residual = entry["credits"].get(target, 0)
        try:
            outcome = settle_training(
                TrainingInputs(
                    scheduled_hours=str(active_hours),
                    attendance="1",
                    available_instructor_hours=(str(active_hours) if requires_instructor else "0"),
                    required_instructor_hours=(str(active_hours) if requires_instructor else "0"),
                    facility_slots=facility_slots,
                    required_slots="1",
                    equipment_sets="1",
                    required_sets="1",
                    instructor_quality_factor=str(instructor_quality_factor),
                    facility_quality_factor=facility_quality_factor,
                    equipment_factor=factors["equipment"],
                    health_factor=health_factor,
                    recovery_factor=recovery_factor,
                    relevance_factor=factors["relevance"],
                    difficulty_fit_factor=factors["difficulty_fit"],
                    aptitude=aptitude,
                    experience_modifier="1",
                    current_value=current_value,
                    residual_units=residual,
                    representation="exact",
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CommandRejectedError("training_resolution_invalid") from exc
        container[leaf] = outcome.ending_value
        entry["credits"][target] = float(outcome.residual_units)
        entry["resolved_through"] = str(target_time)

        world_events = self._world_events_after(base)
        scene_after = json.loads(base.writes[self.scene_path].decode("utf-8"))
        event_id = self._append_semantic_event(
            world_events, command=command, kind="training_resolved", at=target_time,
            host_refs=tuple(x for x in (actor_ref, membership_team_ref) if x),
            actor_refs=tuple(x for x in (actor_ref, instructor_ref) if x),
            place_refs=(scene_after.get("location_id"),),
            affected_owner_refs=(actor_path, _DEVELOPMENT_BANK_PATH),
            material_consequence_refs=(f"training:{actor_ref}:{target}:{current_value}->{outcome.ending_value}",),
            audience_refs=(command.actor_id,),
            reducer_ref="shinobi_runtime.reducers.training.settle_training",
        )
        scene_after["scene_summary"] = (
            f"Training resolves for {actor_ref} through {target_time}; "
            f"{target} changes {current_value}->{outcome.ending_value}."
        )
        scene_after["decision_required"] = "Choose the next consequential action."

        writes = dict(base.writes)
        writes[self.meta_path] = _json_bytes(self._meta_after(meta, command, world_time=target_time))
        writes[self.scene_path] = _json_bytes(scene_after)
        writes[actor_path] = _json_bytes(actor)
        writes[_DEVELOPMENT_BANK_PATH] = _json_bytes(banks)
        if membership_team_path is not None and team is not None:
            writes[membership_team_path] = _json_bytes(team)
        writes.update(self._world_event_writes(world_events))
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("training write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=target_time)
            staged_actor = overlay.read_json(actor_path)
            staged_container, staged_leaf, staged_value = self._training_target(staged_actor, target)
            if staged_leaf != leaf or staged_value != outcome.ending_value:
                raise ValueError("training target after-image differs from reducer")
            staged_banks = overlay.read_json(_DEVELOPMENT_BANK_PATH)
            if staged_banks["entries"][actor_ref]["resolved_through"] != str(target_time):
                raise ValueError("development cursor did not advance with training")
            if membership_team_path is not None:
                staged_team = overlay.read_json(membership_team_path)
                staged_training = staged_team.get("training")
                sessions = staged_training.get("recent_sessions") if isinstance(staged_training, Mapping) else None
                session_ref = "training.session." + command.digest[:32]
                if not isinstance(sessions, list) or not any(
                    isinstance(row, Mapping) and row.get("session_ref") == session_ref
                    for row in sessions
                ):
                    raise ValueError("team training schedule ledger did not persist")
            self._scheduler_from_reader(overlay)

        return _BuiltPlan(
            code="training_resolution_ready", affected_refs=expected_paths, writes=writes,
            result={
                "command_type": command.command_type, "actor_ref": actor_ref, "target": target,
                "model_ref": model_ref,
                "context_ref": context_ref, "instructor_ref": instructor_ref,
                "active_hours": str(active_hours), "starting_value": current_value,
                "ending_value": outcome.ending_value, "residual_units": str(outcome.residual_units),
                "world_time": str(target_time), "semantic_event_id": event_id,
            }, validator=validate,
        )
    def _recovery_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _exact_payload(
            command.payload,
            ("actor_ref", "target_time", "policy_ref"),
            command.command_type,
        )
        actor_ref = _stable_id(command.payload["actor_ref"], "recovery_actor_invalid")
        if actor_ref != command.actor_id:
            raise CommandRejectedError("recovery_actor_not_authorized")
        policy_ref = _stable_id(command.payload["policy_ref"], "recovery_policy_invalid", prefix="recovery.")
        try:
            target_time = CampaignTime.parse(command.payload["target_time"])
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("recovery_target_time_invalid") from exc
        if target_time <= current_time:
            raise CommandRejectedError("recovery_target_time_invalid")
        try:
            policy = self.repository.read_json(_RECOVERY_POLICY_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("recovery_policy_invalid") from exc
        if not isinstance(policy, Mapping) or policy.get("policy_id") != policy_ref:
            raise CommandRejectedError("recovery_policy_invalid")
        actor_path, actor = self._resolve_actor_for_write(actor_ref)
        if actor.get("life_status") not in ("active", "alive"):
            raise CommandRejectedError("recovery_actor_not_active")
        base = self._time_spanning_base(command, meta, current_time, target_time=target_time)
        if base.result.get("interrupted"):
            raise CommandRejectedError("time_boundary_requires_domain_settlement")
        reached = CampaignTime.parse(base.result["world_time"])
        if reached != target_time:
            raise CommandRejectedError("recovery_time_settlement_incomplete")
        elapsed_seconds = int((_campaign_datetime(target_time) - _campaign_datetime(current_time)).total_seconds())
        try:
            outcome = settle_recovery(actor, elapsed_seconds=elapsed_seconds, policy=policy)
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("recovery_resolution_invalid") from exc
        world_events = self._world_events_after(base)
        event_id = self._append_semantic_event(
            world_events, command=command, kind="recovery_resolved", at=target_time,
            host_refs=(actor_ref,), actor_refs=(actor_ref,),
            affected_owner_refs=(actor_path,),
            material_consequence_refs=(f"recovery:{actor_ref}:{elapsed_seconds}",),
            audience_refs=(command.actor_id,),
            reducer_ref="shinobi_runtime.reducers.health.settle_recovery",
        )
        scene = json.loads(base.writes[self.scene_path].decode("utf-8"))
        scene["scene_summary"] = f"Routine recovery resolves for {actor_ref} through {target_time}."
        scene["decision_required"] = "Choose the next consequential action."
        writes = dict(base.writes)
        writes[self.meta_path] = _json_bytes(self._meta_after(meta, command, world_time=target_time))
        writes[self.scene_path] = _json_bytes(scene)
        writes[actor_path] = _json_bytes(actor)
        writes.update(self._world_event_writes(world_events))
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("recovery write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=target_time)
            staged = overlay.read_json(actor_path)
            if staged.get("resources") != actor.get("resources") or staged.get("condition") != actor.get("condition"):
                raise ValueError("recovery after-image differs from reducer")
            self._scheduler_from_reader(overlay)

        return _BuiltPlan(
            code="recovery_resolution_ready",
            affected_refs=expected_paths,
            writes=writes,
            result={"command_type": command.command_type, "actor_ref": actor_ref, "policy_ref": policy_ref, "world_time": str(target_time), "outcome": outcome, "semantic_event_id": event_id},
            validator=validate,
        )

