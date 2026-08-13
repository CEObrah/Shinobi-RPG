"""Voluntary House intake with Konoha population conservation and stable identities."""
from __future__ import annotations

import copy
import hashlib
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.campaign_institution_growth import (
    CampaignCommandPlanner as _Base,
    _summary_and_visibility,
)
from shinobi_runtime.commands.core import _BuiltPlan, _declared_payload, _json_bytes, _stable_id
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.paths import COMMITMENT_REGISTRY_PATH, POPULATION_REGISTRY_PATH
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest

_RECRUITMENT_POLICY_PATH = "game/rules/recruitment/policies.json"


class CampaignCommandPlanner(_Base):
    """Production planner with conserved voluntary House intake."""

    def _institution_intake_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _declared_payload(command.payload, command.command_type)
        institution_ref = _stable_id(
            command.payload.get("institution_ref"),
            "institution_intake_institution_invalid",
            prefix="house.",
        )
        source_pool_id = _stable_id(
            command.payload.get("source_pool_id"),
            "institution_intake_source_pool_invalid",
            prefix="pool.",
        )
        policy_ref = _stable_id(
            command.payload.get("policy_ref"),
            "institution_intake_policy_invalid",
            prefix="recruitment.",
        )
        applicant_count = command.payload.get("applicant_count")
        if isinstance(applicant_count, bool) or not isinstance(applicant_count, int) or applicant_count <= 0:
            raise CommandRejectedError("institution_intake_applicant_count_invalid")
        _summary, visibility = _summary_and_visibility(command.payload, "institution_intake")

        try:
            recruitment_registry = self.repository.read_json(_RECRUITMENT_POLICY_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("institution_intake_policy_invalid") from exc
        policies = recruitment_registry.get("policies") if isinstance(recruitment_registry, Mapping) else None
        policy = policies.get(policy_ref) if isinstance(policies, Mapping) else None
        if not isinstance(policy, Mapping) or policy.get("destination_owner_ref") != institution_ref:
            raise CommandRejectedError("institution_intake_policy_invalid")
        authority_ref = policy.get("decision_authority_ref")
        if not isinstance(authority_ref, str) or not authority_ref:
            raise CommandRejectedError("institution_intake_policy_invalid")
        self._require_growth_scope(
            command=command,
            institution_ref=institution_ref,
            scope_ref=f"recruitment:{policy_ref}",
        )

        max_batch = policy.get("max_intake_per_batch")
        acceptance_rate = policy.get("acceptance_rate_milli")
        max_applicants = policy.get("max_applicants_per_batch", max_batch * 2 if isinstance(max_batch, int) else None)
        if (
            isinstance(max_batch, bool)
            or not isinstance(max_batch, int)
            or max_batch <= 0
            or isinstance(max_applicants, bool)
            or not isinstance(max_applicants, int)
            or max_applicants < max_batch
            or applicant_count > max_applicants
            or isinstance(acceptance_rate, bool)
            or not isinstance(acceptance_rate, int)
            or not 0 <= acceptance_rate <= 1000
        ):
            raise CommandRejectedError("institution_intake_policy_invalid")

        house_path, house_base = self._growth_house(institution_ref)
        house = copy.deepcopy(dict(house_base))
        cooldown_days = policy.get("cooldown_days", 30)
        if isinstance(cooldown_days, bool) or not isinstance(cooldown_days, int) or cooldown_days < 1:
            raise CommandRejectedError("institution_intake_policy_invalid")
        try:
            commitments = copy.deepcopy(self.repository.read_json(COMMITMENT_REGISTRY_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("commitment_registry_invalid") from exc
        commitment_records = commitments.get("records") if isinstance(commitments, dict) else None
        if not isinstance(commitment_records, list):
            raise CommandRejectedError("commitment_registry_invalid")
        prior_times = []
        for row in commitment_records:
            if (
                isinstance(row, Mapping)
                and row.get("kind") == "obligation"
                and row.get("subject_ref") == institution_ref
                and row.get("target_ref") == f"intake:{policy_ref}"
                and row.get("host_ref") == institution_ref
                and row.get("status") == "completed"
                and isinstance(row.get("resolved_at"), str)
                and isinstance(row.get("authority_basis"), str)
                and row.get("authority_basis").startswith("institution_intake_policy:")
            ):
                try:
                    prior_times.append(CampaignTime.parse(row.get("resolved_at")))
                except (TypeError, ValueError) as exc:
                    raise CommandRejectedError("institution_intake_history_invalid") from exc
        if prior_times and current_time < max(prior_times).add_seconds(cooldown_days * 24 * 60 * 60):
            raise CommandRejectedError("institution_intake_cooldown_active")

        try:
            population = copy.deepcopy(self.repository.read_json(POPULATION_REGISTRY_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("population_registry_invalid") from exc
        pools = population.get("pools") if isinstance(population, dict) else None
        source = pools.get(source_pool_id) if isinstance(pools, dict) else None
        allowed_owners = policy.get("eligible_source_owner_refs")
        allowed_categories = policy.get("eligible_source_categories")
        if (
            not isinstance(source, dict)
            or source.get("status") != "active"
            or not isinstance(allowed_owners, list)
            or source.get("owner_ref") not in allowed_owners
            or not isinstance(allowed_categories, list)
            or source.get("category") not in allowed_categories
        ):
            raise CommandRejectedError("institution_intake_source_pool_ineligible")
        representation = source.get("representation")
        if not isinstance(representation, dict):
            raise CommandRejectedError("population_registry_invalid")
        anonymous = representation.get("anonymous_count")
        rostered = representation.get("rostered_count")
        rostered_refs = representation.get("rostered_person_refs")
        total = source.get("count")
        if (
            isinstance(anonymous, bool)
            or not isinstance(anonymous, int)
            or anonymous < applicant_count
            or isinstance(rostered, bool)
            or not isinstance(rostered, int)
            or not isinstance(rostered_refs, list)
            or isinstance(total, bool)
            or not isinstance(total, int)
            or anonymous + rostered != total
        ):
            raise CommandRejectedError("population_registry_invalid")

        raw_accept = applicant_count * acceptance_rate
        accepted_count = raw_accept // 1000
        remainder = raw_accept % 1000
        if remainder and int(command.digest[:8], 16) % 1000 < remainder:
            accepted_count += 1
        accepted_count = min(max_batch, accepted_count, anonymous)
        if accepted_count <= 0:
            raise CommandRejectedError("institution_intake_no_applicants_accepted")

        core_path = policy.get("person_core_registry_path")
        identity_bank_path = policy.get("identity_bank_ref")
        home_place_ref = policy.get("home_place_ref")
        role_profile_ref = policy.get("role_profile_ref")
        if any(not isinstance(value, str) or not value for value in (core_path, identity_bank_path, home_place_ref, role_profile_ref)):
            raise CommandRejectedError("institution_intake_policy_invalid")
        try:
            cores = copy.deepcopy(self.repository.read_json(core_path))
            identity_bank = self.repository.read_json(identity_bank_path)
            owner_index = self.repository.read_json("state/index/owners.json")
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("institution_intake_identity_registry_invalid") from exc
        people = cores.get("people") if isinstance(cores, dict) else None
        bank_rows = identity_bank.get("people") if isinstance(identity_bank, Mapping) else None
        prefix_index = owner_index.get("prefix_index") if isinstance(owner_index, Mapping) else None
        ht_shard_path = prefix_index.get("ht") if isinstance(prefix_index, Mapping) else None
        if not isinstance(people, dict) or not isinstance(bank_rows, list) or not isinstance(ht_shard_path, str):
            raise CommandRejectedError("institution_intake_identity_registry_invalid")
        try:
            ht_index = copy.deepcopy(self.repository.read_json(ht_shard_path))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("institution_intake_identity_registry_invalid") from exc
        ht_owners = ht_index.get("owners") if isinstance(ht_index, dict) else None
        if not isinstance(ht_owners, dict):
            raise CommandRejectedError("institution_intake_identity_registry_invalid")

        current_numbers = []
        for person_id in people:
            if isinstance(person_id, str) and person_id.startswith("ht.core."):
                suffix = person_id.removeprefix("ht.core.")
                if suffix.isdigit():
                    current_numbers.append(int(suffix))
        next_number = max(current_numbers, default=0) + 1
        used_names = {
            row.get("name") for row in people.values()
            if isinstance(row, Mapping) and isinstance(row.get("name"), str)
        }
        available_bank = [
            row for row in bank_rows
            if isinstance(row, Mapping)
            and isinstance(row.get("name"), str)
            and row.get("name") not in used_names
            and isinstance(row.get("pronouns"), str)
            and isinstance(row.get("identity_cues"), Mapping)
        ]
        if len(available_bank) < accepted_count:
            raise CommandRejectedError("institution_intake_identity_bank_exhausted")
        start = int(command.digest[8:16], 16) % len(available_bank)
        ordered_bank = available_bank[start:] + available_bank[:start]
        selected_bank = ordered_bank[:accepted_count]

        cohort_id = f"cohort.house_tang.intake.{command.digest[:12]}"
        new_ids: list[str] = []
        for slot, identity in enumerate(selected_bank):
            person_id = f"ht.core.{next_number + slot:03d}"
            new_ids.append(person_id)
            age = identity.get("age_years", 21)
            if isinstance(age, bool) or not isinstance(age, int) or not 18 <= age <= 55:
                raise CommandRejectedError("institution_intake_identity_registry_invalid")
            month = 1 + (int(hashlib.sha256((person_id + ":month").encode("utf-8")).hexdigest()[:4], 16) % 12)
            day = 1 + (int(hashlib.sha256((person_id + ":day").encode("utf-8")).hexdigest()[:4], 16) % 28)
            birth_year = current_time.year - age
            cues = identity.get("identity_cues")
            if set(cues) != {"appearance", "temperament", "doctrine_expression"}:
                raise CommandRejectedError("institution_intake_identity_registry_invalid")
            people[person_id] = {
                "id": person_id,
                "name": identity.get("name"),
                "aliases": [],
                "pronouns": identity.get("pronouns"),
                "birth_date": f"SE-{birth_year:04d}-{month:02d}-{day:02d}",
                "birth_date_source": "sword_manor_intake_identity_assignment",
                "origin": "Konoha / Land of Fire",
                "life_status": "alive",
                "location_ref": home_place_ref,
                "cohort_ref": cohort_id,
                "cohort_slot": slot,
                "role_profile_ref": role_profile_ref,
                "duty_tags": ["manor_rotation", "oath_bound"],
                "resolved_through": str(current_time),
                "identity_cues": dict(cues),
                "component_refs": {},
                "provenance": {
                    "source_kind": "sword_manor_intake",
                    "source_ref": source_pool_id,
                    "materialized_at": str(current_time),
                    "selection_method": policy.get("selection_mode"),
                },
                "affiliation_ref": institution_ref,
            }
            ht_owners[person_id] = core_path

        baseline = policy.get("cohort_baseline")
        numeric_values = baseline.get("numeric_values") if isinstance(baseline, Mapping) else None
        category_tags = baseline.get("category_tags") if isinstance(baseline, Mapping) else None
        if not isinstance(numeric_values, Mapping) or not isinstance(category_tags, list):
            raise CommandRejectedError("institution_intake_policy_invalid")
        numeric_distributions = {}
        for key, value in sorted(numeric_values.items()):
            if not isinstance(key, str) or isinstance(value, bool) or not isinstance(value, (int, float)):
                raise CommandRejectedError("institution_intake_policy_invalid")
            numeric_distributions[key] = {
                "count": accepted_count,
                "mean": float(value),
                "sd": 0.0,
                "min": float(value),
                "max": float(value),
            }
        category_counts = {}
        for tag in category_tags:
            if not isinstance(tag, str) or ":" not in tag:
                raise CommandRejectedError("institution_intake_policy_invalid")
            category_counts[tag] = accepted_count
        cohort = {
            "id": cohort_id,
            "name": f"House Tang Intake Cohort {current_time.year:04d}-{current_time.month:02d}",
            "owner": institution_ref,
            "members": [],
            "roster_refs": new_ids,
            "role": "junior_training_local_defense",
            "doctrine": "Invisible Court Core: Manor Defense",
            "training": "House Tang junior-disciple curriculum",
            "loadout_standard": "load.ht.base",
            "reconstitution_policy_ref": "reconstitution.house_tang",
            "aggregate_count": accepted_count,
            "cohort_profile": {
                "representation": "house_cohort",
                "numeric_distributions": numeric_distributions,
                "category_counts": category_counts,
                "development": {
                    "resolved_through": str(current_time),
                    "credits": {},
                    "model": "representation_neutral_house_cohort",
                },
                "provenance": [f"sword_manor_intake:{source_pool_id}:{policy_ref}:{command.digest[:16]}"],
            },
        }
        cohorts = house.get("cohorts")
        member_ids = house.get("member_ids")
        rostered_member_count = house.get("rostered_member_count")
        if not isinstance(cohorts, list) or not isinstance(member_ids, list) or isinstance(rostered_member_count, bool) or not isinstance(rostered_member_count, int):
            raise CommandRejectedError("institution_intake_house_invalid")
        if any(isinstance(row, Mapping) and row.get("id") == cohort_id for row in cohorts):
            raise CommandRejectedError("institution_intake_cohort_conflict")
        cohorts.append(cohort)
        member_ids.extend(new_ids)
        if len(member_ids) != len(set(member_ids)):
            raise CommandRejectedError("institution_intake_house_invalid")
        house["rostered_member_count"] = rostered_member_count + accepted_count

        intake_record_id = f"commitment.intake.{command.digest[:20]}"
        commitment_records.append({
            "id": intake_record_id,
            "kind": "obligation",
            "subject_ref": institution_ref,
            "target_ref": f"intake:{policy_ref}",
            "host_ref": institution_ref,
            "created_at": str(current_time),
            "due_at": None,
            "status": "completed",
            "summary": f"Resolved {policy_ref}: {accepted_count} accepted from {applicant_count} applicants.",
            "visibility": visibility,
            "authority_basis": f"institution_intake_policy:{institution_ref}:{authority_ref}",
            "resolved_at": str(current_time),
            "resolution_summary": f"Created {cohort_id} with {accepted_count} oath-bound House members.",
        })

        representation["anonymous_count"] = anonymous - accepted_count
        representation["rostered_count"] = rostered + accepted_count
        rostered_refs.extend(new_ids)
        if len(rostered_refs) != len(set(rostered_refs)):
            raise CommandRejectedError("population_registry_invalid")
        source["last_changed_at"] = str(current_time)
        if representation["anonymous_count"] + representation["rostered_count"] != total:
            raise CommandRejectedError("population_conservation_failed")

        scene = self._scene_base(current_time)
        scene_after = copy.deepcopy(scene)
        scene_after["scene_summary"] = (
            f"{institution_ref} resolves one registered disciple intake: {accepted_count} accepted from {applicant_count} applicants."
        )
        scene_after["decision_required"] = None

        world_events = self._world_events()
        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind="institution_intake_resolved",
            at=current_time,
            host_refs=(institution_ref,),
            actor_refs=(command.actor_id, authority_ref),
            place_refs=(home_place_ref,),
            affected_owner_refs=(institution_ref, source_pool_id, *new_ids),
            material_consequence_refs=(
                f"applicants:{applicant_count}",
                f"accepted:{accepted_count}",
                f"oath_required:{bool(policy.get('oath_required'))}",
                f"cohort:{cohort_id}",
            ),
            classification=visibility,
            audience_refs=(command.actor_id, authority_ref, *new_ids),
            reducer_ref="shinobi_runtime.commands.institution_intake_resolution",
        )
        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            POPULATION_REGISTRY_PATH: _json_bytes(population),
            house_path: _json_bytes(house),
            core_path: _json_bytes(cores),
            ht_shard_path: _json_bytes(ht_index),
            COMMITMENT_REGISTRY_PATH: _json_bytes(commitments),
            self.scene_path: _json_bytes(scene_after),
        }
        writes.update(self._world_event_writes(world_events))
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("institution intake write set changed after planning")
            self._assert_meta(
                overlay,
                manifest,
                meta_path=self.meta_path,
                command=command,
                world_time=current_time,
            )
            if overlay.read_json(POPULATION_REGISTRY_PATH) != population:
                raise ValueError("institution intake population after-image mismatch")
            if overlay.read_json(house_path) != house:
                raise ValueError("institution intake House after-image mismatch")
            if overlay.read_json(core_path) != cores:
                raise ValueError("institution intake person-core after-image mismatch")
            if overlay.read_json(ht_shard_path) != ht_index:
                raise ValueError("institution intake owner-index after-image mismatch")
            if overlay.read_json(COMMITMENT_REGISTRY_PATH) != commitments:
                raise ValueError("institution intake history after-image mismatch")
            staged_source = overlay.read_json(POPULATION_REGISTRY_PATH)["pools"][source_pool_id]
            staged_rep = staged_source["representation"]
            if staged_source["count"] != total or staged_rep["anonymous_count"] + staged_rep["rostered_count"] != total:
                raise ValueError("institution intake changed home-population headcount")

        return _BuiltPlan(
            code="institution_intake_resolution_ready",
            affected_refs=expected_paths,
            writes=writes,
            result={
                "command_type": command.command_type,
                "institution_ref": institution_ref,
                "policy_ref": policy_ref,
                "source_pool_id": source_pool_id,
                "applicant_count": applicant_count,
                "accepted_count": accepted_count,
                "rejected_count": applicant_count - accepted_count,
                "new_member_refs": new_ids,
                "cohort_ref": cohort_id,
                "home_population_count_unchanged": True,
                "oath_ref": policy.get("oath_ref") if policy.get("oath_required") is True else None,
                "semantic_event_id": event_id,
            },
            validator=validate,
        )


__all__ = ["CampaignCommandPlanner"]
