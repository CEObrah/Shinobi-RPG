"""Individual House roster progression layered over bounded section scheduling.

House cohorts remain scheduling/summary owners. Every rostered identity keeps a
stable lightweight profile in its person-core registry, so training, technique
learning, promotion, and section moves never reroll an established person.
"""
from __future__ import annotations

import copy
import json
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Mapping, MutableMapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _json_bytes
from shinobi_runtime.commands.living_world_house import _BasePlanOverlayView
from shinobi_runtime.people.profiles import (
    numeric_map,
    profile_entry_for,
    refresh_section_summary,
    set_numeric,
    update_standing,
)
from shinobi_runtime.people.repertoire import technique_prerequisites_met
from shinobi_runtime.reducers import TrainingInputs, settle_training
from shinobi_runtime.sim.events import CampaignTime

_HOUSE_ID = "house.tang"
_ROSTER_PATH = "state/person-core/house-tang.json"
_RANKS_PATH = "game/data/house/ranks.json"
_THREE = Decimal("0.001")
_NUMERIC_REQUIREMENT_PATHS = {
    "awareness": "stats.attributes.awareness",
    "coordination": "stats.attributes.coordination",
    "agility": "stats.attributes.agility",
    "composure": "stats.attributes.composure",
    "sword": "stats.martial_skills.sword",
    "movement": "stats.martial_skills.movement",
    "chakra_control": "stats.chakra_dimensions.control",
    "wind": "stats.domain_proficiencies.wind",
    "tactics": "stats.operational_skills.tactics",
    "team_coordination": "stats.operational_skills.team_coordination",
    "leadership": "stats.operational_skills.leadership",
    "medicine": "stats.operational_skills.medicine",
}


def _integer(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CommandRejectedError(code)
    rounded = int(Decimal(str(value)).to_integral_value(rounding=ROUND_HALF_UP))
    if rounded < 0:
        raise CommandRejectedError(code)
    return rounded


class RosteredHouseProgressionMixin:
    """Settle House Tang rostered people without making cohorts character truth."""

    def _house_roster_registry(self, writes: Mapping[str, bytes]) -> dict[str, Any]:
        raw = writes.get(_ROSTER_PATH)
        try:
            value = (
                json.loads(raw.decode("utf-8"))
                if isinstance(raw, (bytes, bytearray))
                else copy.deepcopy(self.repository.read_json(_ROSTER_PATH))
            )
        except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise CommandRejectedError("house_roster_profile_invalid") from exc
        if not isinstance(value, dict) or value.get("schema") != "person-core-registry":
            raise CommandRejectedError("house_roster_profile_invalid")
        if not isinstance(value.get("people"), dict) or not isinstance(value.get("profiles"), dict):
            raise CommandRejectedError("house_roster_profile_invalid")
        return value

    @staticmethod
    def _profile_technique_view(registry: Mapping[str, Any], entry: Mapping[str, Any]) -> Mapping[str, Any]:
        numeric = numeric_map(registry, entry)
        institutional = entry.get("institutional_progression")
        if not isinstance(institutional, Mapping):
            raise CommandRejectedError("house_roster_profile_invalid")
        return {
            "domain_proficiencies": {
                "wind": numeric.get("stats.domain_proficiencies.wind", 0),
                "medical": numeric.get("stats.domain_proficiencies.medical", 0),
                "genjutsu": numeric.get("stats.domain_proficiencies.genjutsu", 0),
                "sealing": numeric.get("stats.domain_proficiencies.sealing", 0),
                "barrier": numeric.get("stats.domain_proficiencies.barrier", 0),
            },
            "chakra_dimensions": {
                "control": numeric.get("stats.chakra_dimensions.control", 0),
            },
            "repertoire": {
                "packages": list(institutional.get("training_package_refs", [])),
                "method_mastery": dict(institutional.get("method_mastery", {})),
                "latent_or_locked_techniques": list(institutional.get("latent_or_locked_techniques", [])),
                "bloodlines": [],
            },
        }

    def _institutional_teacher_available(self, registry: Mapping[str, Any], technique_ref: str, house: Mapping[str, Any]) -> bool:
        home = house.get("home")
        leadership = house.get("leadership")
        refs: list[str] = []
        if isinstance(leadership, Mapping):
            refs.extend(x for x in leadership.values() if isinstance(x, str) and x)
        try:
            threshold = self._technique_threshold(self._technique_record(technique_ref))
        except CommandRejectedError:
            return False
        for ref in dict.fromkeys(refs):
            try:
                _path, record = self._resolve_actor_for_write(ref)
            except CommandRejectedError:
                continue
            if record.get("current_location_id") != home or record.get("life_status") not in ("active", "alive"):
                continue
            repertoire = record.get("repertoire")
            mastery = repertoire.get("method_mastery") if isinstance(repertoire, Mapping) else None
            latent = repertoire.get("latent_or_locked_techniques") if isinstance(repertoire, Mapping) else None
            value = mastery.get(technique_ref) if isinstance(mastery, Mapping) else None
            if isinstance(value, int) and not isinstance(value, bool) and isinstance(latent, list) and technique_ref not in latent and value >= threshold:
                return True
        # Rostered instructors are equally real people; their saved lightweight
        # profiles may satisfy the institutional teaching requirement without
        # exactifying them merely to teach routine curriculum.
        cores = registry.get("people")
        profiles = registry.get("profiles")
        if isinstance(cores, Mapping) and isinstance(profiles, Mapping):
            for ref, core in cores.items():
                if not isinstance(core, Mapping) or core.get("location_ref") != home or core.get("life_status") != "alive":
                    continue
                profile = profiles.get(ref)
                institutional = profile.get("institutional_progression") if isinstance(profile, Mapping) else None
                if not isinstance(institutional, Mapping) or institutional.get("standing") not in ("assistant_instructor", "senior_instructor", "sword_master"):
                    continue
                mastery = institutional.get("method_mastery")
                latent = institutional.get("latent_or_locked_techniques")
                value = mastery.get(technique_ref) if isinstance(mastery, Mapping) else None
                if isinstance(value, int) and not isinstance(value, bool) and isinstance(latent, list) and technique_ref not in latent and value >= threshold:
                    return True
        return False

    def _settle_rostered_method(
        self,
        *,
        registry: MutableMapping[str, Any],
        entry: MutableMapping[str, Any],
        house: Mapping[str, Any],
        package_ref: str,
        active_hours: Decimal,
        technique_share_milli: int,
        factors: Mapping[str, Any],
        resource_factor: Decimal,
    ) -> Mapping[str, Any] | None:
        if technique_share_milli <= 0 or active_hours <= 0:
            return None
        packages = self._tech_package_registry()
        methods = self._package_methods(packages, package_ref)
        institutional = entry.get("institutional_progression")
        if not isinstance(institutional, MutableMapping):
            raise CommandRejectedError("house_roster_profile_invalid")
        mastery = institutional.get("method_mastery")
        latent = institutional.get("latent_or_locked_techniques")
        residuals = institutional.get("method_residual_units")
        if not isinstance(mastery, MutableMapping) or not isinstance(latent, list) or not isinstance(residuals, MutableMapping):
            raise CommandRejectedError("house_roster_profile_invalid")
        view = self._profile_technique_view(registry, entry)
        chosen: str | None = None
        record: Mapping[str, Any] | None = None
        threshold = 0
        for method_ref in methods:
            candidate = self._technique_record(method_ref)
            candidate_threshold = self._technique_threshold(candidate)
            value = mastery.get(method_ref, 0)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise CommandRejectedError("house_roster_profile_invalid")
            if int(value) >= candidate_threshold and method_ref not in latent:
                continue
            if not technique_prerequisites_met(view, candidate):
                continue
            if not self._institutional_teacher_available(registry, method_ref, house):
                continue
            chosen, record, threshold = method_ref, candidate, candidate_threshold
            break
        if chosen is None or record is None:
            return None
        if chosen not in mastery:
            mastery[chosen] = 0
        if chosen not in latent and int(mastery[chosen]) < threshold:
            latent.append(chosen)
            latent.sort()
        numeric = numeric_map(registry, entry)
        aptitude = _integer(numeric.get("aptitude.technical_learning", 100), "house_roster_profile_invalid")
        scheduled = (active_hours * Decimal(technique_share_milli) / Decimal(1000)).quantize(_THREE, rounding=ROUND_HALF_UP)
        attendance = self._policy_milli(factors, "attendance_milli") * resource_factor
        outcome = settle_training(
            TrainingInputs(
                scheduled_hours=str(scheduled),
                attendance=str(attendance),
                available_instructor_hours=str(scheduled),
                required_instructor_hours=str(scheduled),
                facility_slots="1",
                required_slots="1",
                equipment_sets="1",
                required_sets="1",
                instructor_quality_factor=str(self._policy_milli(factors, "instructor_quality_milli", capped=False)),
                facility_quality_factor=str(self._policy_milli(factors, "facility_quality_milli")),
                equipment_factor=str(self._policy_milli(factors, "equipment_milli")),
                health_factor=str(self._policy_milli(factors, "health_milli")),
                recovery_factor=str(self._policy_milli(factors, "recovery_milli")),
                relevance_factor=str(self._policy_milli(factors, "relevance_milli")),
                difficulty_fit_factor=str(self._policy_milli(factors, "difficulty_fit_milli")),
                aptitude=aptitude,
                experience_modifier=str(self._policy_milli(factors, "experience_milli", capped=False)),
                current_value=_integer(mastery[chosen], "house_roster_profile_invalid"),
                residual_units=residuals.get(chosen, 0),
                representation="rostered_cohort",
            )
        )
        mastery[chosen] = outcome.ending_value
        residuals[chosen] = float(outcome.residual_units)
        refreshed_view = self._profile_technique_view(registry, entry)
        if outcome.ending_value >= threshold and technique_prerequisites_met(refreshed_view, record):
            if chosen in latent:
                latent.remove(chosen)
        return {
            "technique_ref": chosen,
            "starting_mastery": outcome.starting_value,
            "ending_mastery": outcome.ending_value,
            "points_gained": outcome.points_gained,
            "field_usable": chosen not in latent and outcome.ending_value >= threshold,
        }

    def _settle_rostered_profile(
        self,
        *,
        registry: MutableMapping[str, Any],
        entry: MutableMapping[str, Any],
        house: Mapping[str, Any],
        cohort: Mapping[str, Any],
        through: CampaignTime,
        policy: Mapping[str, Any],
        resource_factor: Decimal,
    ) -> Mapping[str, Any] | None:
        institutional = entry.get("institutional_progression")
        if not isinstance(institutional, MutableMapping):
            raise CommandRejectedError("house_roster_profile_invalid")
        try:
            prior = CampaignTime.parse(institutional.get("resolved_through"))
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("house_roster_profile_invalid") from exc
        if prior > through:
            raise CommandRejectedError("house_roster_development_cursor_invalid")
        if prior == through:
            return None
        curricula = policy.get("curricula")
        label = cohort.get("training")
        curriculum = curricula.get(label) if isinstance(curricula, Mapping) and isinstance(label, str) else None
        if not isinstance(curriculum, Mapping):
            raise CommandRejectedError("house_training_curriculum_missing")
        factors = curriculum.get("factors_milli")
        targets = curriculum.get("targets")
        if not isinstance(factors, Mapping) or not isinstance(targets, list):
            raise CommandRejectedError("house_training_policy_invalid")
        if sum(x.get("weight_milli", 0) for x in targets if isinstance(x, Mapping)) != 1000:
            raise CommandRejectedError("house_training_policy_invalid")
        active_hours = self._scheduled_house_training_hours(prior, through, policy)
        numeric = numeric_map(registry, entry)
        residuals = institutional.get("development_residual_units")
        if not isinstance(residuals, MutableMapping):
            raise CommandRejectedError("house_roster_profile_invalid")
        field_combat = max(0.0, float(residuals.get("field.combat_exchanges", 0)))
        field_missions = max(0.0, float(residuals.get("field.mission_events", 0)))
        # Field work supplies pressure-tested evidence, not free skill points.
        # It modestly improves later consolidation and is consumed by training.
        field_bonus_milli = min(150, int(field_combat * 3 + field_missions * 12))
        field_experience_factor = Decimal(1000 + field_bonus_milli) / Decimal(1000)
        outcomes: Dict[str, Any] = {}
        for target in targets:
            if not isinstance(target, Mapping):
                raise CommandRejectedError("house_training_policy_invalid")
            target_key, aptitude_key, weight = target.get("target"), target.get("aptitude"), target.get("weight_milli")
            if not isinstance(target_key, str) or not isinstance(aptitude_key, str) or isinstance(weight, bool) or not isinstance(weight, int) or weight <= 0:
                raise CommandRejectedError("house_training_policy_invalid")
            if target_key not in numeric or aptitude_key not in numeric:
                raise CommandRejectedError("house_training_target_missing")
            starting = _integer(numeric[target_key], "house_roster_profile_invalid")
            aptitude = _integer(numeric[aptitude_key], "house_roster_profile_invalid")
            scheduled = (active_hours * Decimal(weight) / Decimal(1000)).quantize(_THREE, rounding=ROUND_HALF_UP)
            attendance = self._policy_milli(factors, "attendance_milli") * resource_factor
            outcome = settle_training(
                TrainingInputs(
                    scheduled_hours=str(scheduled), attendance=str(attendance),
                    available_instructor_hours=str(scheduled), required_instructor_hours=str(scheduled),
                    facility_slots="1", required_slots="1", equipment_sets="1", required_sets="1",
                    instructor_quality_factor=str(self._policy_milli(factors, "instructor_quality_milli", capped=False)),
                    facility_quality_factor=str(self._policy_milli(factors, "facility_quality_milli")),
                    equipment_factor=str(self._policy_milli(factors, "equipment_milli")),
                    health_factor=str(self._policy_milli(factors, "health_milli")),
                    recovery_factor=str(self._policy_milli(factors, "recovery_milli")),
                    relevance_factor=str(self._policy_milli(factors, "relevance_milli")),
                    difficulty_fit_factor=str(self._policy_milli(factors, "difficulty_fit_milli")),
                    aptitude=aptitude,
                    experience_modifier=str(self._policy_milli(factors, "experience_milli", capped=False) * field_experience_factor),
                    current_value=starting,
                    residual_units=residuals.get(target_key, 0),
                    representation="rostered_cohort",
                )
            )
            set_numeric(registry, entry, target_key, outcome.ending_value)
            residuals[target_key] = float(outcome.residual_units)
            outcomes[target_key] = outcome.to_record()
        technique_share = curriculum.get("technique_practice_milli", 0)
        if isinstance(technique_share, bool) or not isinstance(technique_share, int) or not 0 <= technique_share <= 1000:
            raise CommandRejectedError("house_training_policy_invalid")
        package_refs = institutional.get("training_package_refs")
        technique_result = None
        if isinstance(package_refs, list) and package_refs:
            technique_result = self._settle_rostered_method(
                registry=registry, entry=entry, house=house, package_ref=package_refs[-1],
                active_hours=active_hours, technique_share_milli=technique_share,
                factors=factors, resource_factor=resource_factor,
            )
        if active_hours > 0 and field_bonus_milli > 0:
            consolidation_units = max(1, int(active_hours // Decimal(4)))
            residuals["field.combat_exchanges"] = max(0.0, field_combat - consolidation_units)
            residuals["field.mission_events"] = max(0.0, field_missions - max(1, consolidation_units // 4))
        institutional["resolved_through"] = str(through)
        return {
            "member_ref": entry.get("person_ref"), "cohort_id": cohort.get("id"),
            "from": str(prior), "through": str(through), "active_hours": str(active_hours),
            "outcomes": outcomes, "technique": technique_result,
        }

    @staticmethod
    def _rank_minimums_met(registry: Mapping[str, Any], entry: Mapping[str, Any], requirement: Mapping[str, Any]) -> bool:
        minimums = requirement.get("minimums")
        if minimums is None:
            return True
        if not isinstance(minimums, Mapping):
            return False
        numeric = numeric_map(registry, entry)
        for name, required in minimums.items():
            path = _NUMERIC_REQUIREMENT_PATHS.get(name)
            if path is None or isinstance(required, bool) or not isinstance(required, (int, float)):
                return False
            if numeric.get(path, -1) < float(required):
                return False
        return True

    def _field_usable_profile_methods(self, registry: Mapping[str, Any], entry: Mapping[str, Any]) -> set[str]:
        institutional = entry.get("institutional_progression")
        if not isinstance(institutional, Mapping):
            return set()
        mastery = institutional.get("method_mastery")
        latent = institutional.get("latent_or_locked_techniques")
        if not isinstance(mastery, Mapping) or not isinstance(latent, list):
            return set()
        view = self._profile_technique_view(registry, entry)
        result: set[str] = set()
        for ref, value in mastery.items():
            if ref in latent or isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            try:
                rec = self._technique_record(ref)
                threshold = self._technique_threshold(rec)
            except CommandRejectedError:
                continue
            if value >= threshold and technique_prerequisites_met(view, rec):
                result.add(ref)
        return result

    @staticmethod
    def _observed_service_domains(entry: Mapping[str, Any]) -> set[str]:
        institutional = entry.get("institutional_progression")
        history = institutional.get("service_history") if isinstance(institutional, Mapping) else None
        if not isinstance(history, list):
            return set()
        result: set[str] = set()
        for row in history:
            if not isinstance(row, Mapping):
                continue
            domains = row.get("domains")
            if isinstance(domains, list):
                result.update(x for x in domains if isinstance(x, str) and x)
        return result

    def _record_junior_assessment_evidence(
        self,
        registry: MutableMapping[str, Any],
        entry: MutableMapping[str, Any],
        requirement: Mapping[str, Any],
    ) -> str | None:
        institutional = entry.get("institutional_progression")
        rules = requirement.get("credential_rules")
        required = requirement.get("credentials")
        if not isinstance(institutional, MutableMapping) or not isinstance(rules, Mapping) or not isinstance(required, list):
            return None
        credentials = institutional.get("credential_refs")
        if not isinstance(credentials, list):
            raise CommandRejectedError("house_roster_profile_invalid")
        numeric = numeric_map(registry, entry)
        usable = self._field_usable_profile_methods(registry, entry)
        for credential in required:
            if not isinstance(credential, str) or credential in credentials:
                continue
            rule = rules.get(credential)
            if not isinstance(rule, Mapping):
                return None
            minimums = rule.get("minimums", {})
            if isinstance(minimums, Mapping):
                ok = True
                for name, required_value in minimums.items():
                    path = _NUMERIC_REQUIREMENT_PATHS.get(name)
                    if path is None or numeric.get(path, -1) < float(required_value):
                        ok = False
                        break
                if not ok:
                    continue
            if rule.get("requires_all_rank_numeric_minimums") and not self._rank_minimums_met(registry, entry, requirement):
                continue
            required_domains = rule.get("requires_service_domains", [])
            if isinstance(required_domains, list):
                observed_domains = self._observed_service_domains(entry)
                if any(not isinstance(domain, str) or domain not in observed_domains for domain in required_domains):
                    continue
            elif required_domains is not None:
                continue
            prereq_creds = rule.get("requires_credentials", [])
            if isinstance(prereq_creds, list) and any(ref not in credentials for ref in prereq_creds):
                continue
            min_methods = rule.get("minimum_field_usable_current_tier_methods", 0)
            if isinstance(min_methods, int) and not isinstance(min_methods, bool) and len(usable) < min_methods:
                continue
            credentials.append(credential)
            return credential
        return None

    def _move_rostered_person(
        self,
        registry: MutableMapping[str, Any],
        house: MutableMapping[str, Any],
        person_ref: str,
        target_cohort_ref: str,
        standing: str,
    ) -> None:
        cores = registry.get("people")
        profiles = registry.get("profiles")
        if not isinstance(cores, MutableMapping) or not isinstance(profiles, MutableMapping):
            raise CommandRejectedError("house_roster_profile_invalid")
        core = cores.get(person_ref); entry = profiles.get(person_ref)
        if not isinstance(core, MutableMapping) or not isinstance(entry, MutableMapping):
            raise CommandRejectedError("house_roster_profile_invalid")
        cohorts = house.get("cohorts")
        if not isinstance(cohorts, list):
            raise CommandRejectedError("house_owner_invalid")
        target = None
        for cohort in cohorts:
            if not isinstance(cohort, MutableMapping):
                continue
            refs = cohort.get("roster_refs")
            if isinstance(refs, list) and person_ref in refs:
                refs.remove(person_ref)
            if cohort.get("id") == target_cohort_ref:
                target = cohort
        if not isinstance(target, MutableMapping) or not isinstance(target.get("roster_refs"), list):
            raise CommandRejectedError("house_promotion_section_invalid")
        if person_ref not in target["roster_refs"]:
            target["roster_refs"].append(person_ref)
        core["cohort_ref"] = target_cohort_ref
        entry["cohort_ref"] = target_cohort_ref
        role_ref = f"role.house_tang.{standing}"
        if standing != "sword_master":
            core["role_profile_ref"] = role_ref
        for cohort in cohorts:
            if not isinstance(cohort, MutableMapping) or not isinstance(cohort.get("roster_refs"), list):
                continue
            cohort["aggregate_count"] = len(cohort["roster_refs"])
            for slot, ref in enumerate(cohort["roster_refs"]):
                member_core = cores.get(ref)
                if isinstance(member_core, MutableMapping):
                    member_core["cohort_ref"] = cohort.get("id")
                    member_core["cohort_slot"] = slot
                member_profile = profiles.get(ref)
                if isinstance(member_profile, MutableMapping):
                    member_profile["cohort_ref"] = cohort.get("id")

    def _review_rostered_promotions(
        self,
        registry: MutableMapping[str, Any],
        house: MutableMapping[str, Any],
        through: CampaignTime,
    ) -> list[Mapping[str, Any]]:
        try:
            ranks = self.repository.read_json(_RANKS_PATH)
            progression = self._training_progression_institutions()[_HOUSE_ID]
        except (FileNotFoundError, KeyError, ValueError, TypeError) as exc:
            raise CommandRejectedError("house_promotion_policy_invalid") from exc
        order = progression.get("standing_order") if isinstance(progression, Mapping) else None
        mapping = progression.get("standing_to_technical_tier") if isinstance(progression, Mapping) else None
        tiers = progression.get("technical_tiers") if isinstance(progression, Mapping) else None
        sections = progression.get("standing_sections") if isinstance(progression, Mapping) else None
        if not all(isinstance(x, Mapping) for x in (ranks, mapping, tiers, sections)) or not isinstance(order, list):
            raise CommandRejectedError("house_promotion_policy_invalid")
        results: list[Mapping[str, Any]] = []
        for person_ref, entry in list(registry.get("profiles", {}).items()):
            if not isinstance(entry, MutableMapping):
                continue
            institutional = entry.get("institutional_progression")
            if not isinstance(institutional, MutableMapping):
                continue
            standing = institutional.get("standing")
            if standing not in order:
                continue
            idx = order.index(standing)
            if idx >= len(order) - 1:
                continue
            next_standing = order[idx + 1]
            requirement = ranks.get(next_standing)
            if not isinstance(requirement, Mapping):
                continue
            evidence = None
            if standing == "junior_disciple" and next_standing == "senior_disciple":
                evidence = self._record_junior_assessment_evidence(registry, entry, requirement)
            credentials = institutional.get("credential_refs")
            required_credentials = requirement.get("credentials", [])
            credentials_complete = isinstance(credentials, list) and isinstance(required_credentials, list) and all(x in credentials for x in required_credentials)
            if not self._rank_minimums_met(registry, entry, requirement) or not credentials_complete:
                if evidence is not None:
                    results.append({"member_ref": person_ref, "assessment_evidence": evidence, "promoted": False})
                continue
            # Master recognition is a protected board event, never a routine monthly consequence.
            if next_standing == "sword_master":
                continue
            tier = mapping.get(next_standing)
            tier_info = tiers.get(tier) if isinstance(tier, str) else None
            package_ref = tier_info.get("package_ref") if isinstance(tier_info, Mapping) else None
            target_section = sections.get(next_standing)
            if not isinstance(tier, str) or not isinstance(package_ref, str) or not isinstance(target_section, str):
                raise CommandRejectedError("house_promotion_policy_invalid")
            update_standing(entry, standing=next_standing, technical_tier=tier, package_ref=package_ref, at=str(through), reason="authorized House operating review after recorded requirements")
            self._move_rostered_person(registry, house, person_ref, target_section, next_standing)
            results.append({"member_ref": person_ref, "from": standing, "to": next_standing, "promoted": True})
        return results

    def _apply_house_progression_to_time_plan(self, plan: _BuiltPlan) -> _BuiltPlan:
        plan = super()._apply_house_progression_to_time_plan(plan)
        summaries = plan.result.get("house_progression_reviews") if isinstance(plan.result, Mapping) else None
        if not isinstance(summaries, list) or not summaries:
            return plan
        writes = dict(plan.writes)
        base_paths = tuple(sorted(plan.writes))
        prior_validator = plan.validator
        base_json: Dict[str, Any] = {}
        expected_json: Dict[str, Any] = {}
        all_results: list[Mapping[str, Any]] = []
        all_promotions: list[Mapping[str, Any]] = []
        changed = False
        for summary in summaries:
            if not isinstance(summary, Mapping) or summary.get("house_id") != _HOUSE_ID:
                continue
            through_raw = summary.get("through")
            if not isinstance(through_raw, str):
                raise CommandRejectedError("house_progression_result_invalid")
            through = CampaignTime.parse(through_raw)
            house_path = None
            house = None
            for path, raw in writes.items():
                if not path.startswith("state/house/") or not isinstance(raw, (bytes, bytearray)):
                    continue
                candidate = json.loads(raw.decode("utf-8"))
                if isinstance(candidate, dict) and candidate.get("id") == _HOUSE_ID:
                    house_path, house = path, candidate
                    break
            if house_path is None or not isinstance(house, dict):
                raise CommandRejectedError("house_progression_result_invalid")
            base_json[house_path] = copy.deepcopy(house)
            registry = self._house_roster_registry(writes)
            if _ROSTER_PATH in writes:
                base_json[_ROSTER_PATH] = copy.deepcopy(registry)
            policy = self._house_training_policy(_HOUSE_ID)
            if not isinstance(policy, Mapping):
                raise CommandRejectedError("house_training_policy_invalid")
            resource = summary.get("parallel_resource_conservation")
            milli = resource.get("resource_factor_milli", 1000) if isinstance(resource, Mapping) else 1000
            if isinstance(milli, bool) or not isinstance(milli, int) or not 0 <= milli <= 1000:
                raise CommandRejectedError("house_training_resource_invalid")
            resource_factor = Decimal(milli) / Decimal(1000)
            for cohort in house.get("cohorts", []):
                if not isinstance(cohort, Mapping):
                    continue
                refs = cohort.get("roster_refs")
                if not isinstance(refs, list):
                    continue
                for person_ref in tuple(refs):
                    entry = profile_entry_for(registry, person_ref)
                    if not isinstance(entry, MutableMapping):
                        raise CommandRejectedError("house_roster_profile_missing")
                    result = self._settle_rostered_profile(
                        registry=registry, entry=entry, house=house, cohort=cohort,
                        through=through, policy=policy, resource_factor=resource_factor,
                    )
                    if result is not None:
                        all_results.append(result)
            all_promotions.extend(self._review_rostered_promotions(registry, house, through))
            for cohort in house.get("cohorts", []):
                if isinstance(cohort, MutableMapping) and cohort.get("roster_refs"):
                    refresh_section_summary(registry, cohort)
                    profile = cohort.get("cohort_profile")
                    development = profile.get("development") if isinstance(profile, MutableMapping) else None
                    if isinstance(development, MutableMapping):
                        development["resolved_through"] = str(through)
                        development["credits"] = {}
                        development["model"] = "derived_from_persistent_individuals"
            writes[house_path] = _json_bytes(house)
            writes[_ROSTER_PATH] = _json_bytes(registry)
            expected_json[house_path] = copy.deepcopy(house)
            expected_json[_ROSTER_PATH] = copy.deepcopy(registry)
            changed = True
        if not changed:
            return plan
        expected_paths = tuple(sorted(writes))

        def validate(overlay: Any, manifest: Any) -> None:
            if prior_validator is not None:
                prior_validator(_BasePlanOverlayView(overlay, base_paths=base_paths, base_json=base_json), manifest)
            if overlay.changed_paths != expected_paths:
                raise ValueError("rostered House progression changed write set after planning")
            for path, expected in expected_json.items():
                if overlay.read_json(path) != expected:
                    raise ValueError("rostered House progression after-image differs from settled plan")
            roster = overlay.read_json(_ROSTER_PATH)
            if set(roster.get("people", {})) != set(roster.get("profiles", {})):
                raise ValueError("rostered House profile/core identity sets diverged")

        result = dict(plan.result)
        result["house_rostered_individual_progression"] = [dict(row) for row in all_results]
        if all_promotions:
            result["house_rostered_promotion_reviews"] = [dict(row) for row in all_promotions]
        return _BuiltPlan(code=plan.code, affected_refs=expected_paths, writes=writes, result=result, validator=validate)


__all__ = ["RosteredHouseProgressionMixin"]
