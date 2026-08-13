"""Extracted semantic command domain from the repository command planner.

The mixin owns domain reducers; orchestration, transaction framing, shared owner
resolution, and causal scheduler settlement remain on RepositoryCommandPlanner.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import field
from decimal import Decimal
from typing import (
    Any,
    Dict,
    Mapping,
    Optional,
    Sequence,
)

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.core import (
    _BuiltPlan, _OwnerResolutionCache, _campaign_datetime, _exact_payload, _json_bytes, _stable_id,
)
from shinobi_runtime.commands.paths import (
    INFORMATION_REGISTRY_PATH as _INFORMATION_REGISTRY_PATH,
    DEVELOPMENT_BANK_PATH as _DEVELOPMENT_BANK_PATH,
    RELATIONSHIP_RULES_PATH as _RELATIONSHIP_RULES_PATH,
    RELATIONSHIP_INDEX_PATH as _RELATIONSHIP_INDEX_PATH,
    NAMED_ITEMS_PATH as _NAMED_ITEMS_PATH,
    TECHNIQUE_MANIFEST_PATH as _TECHNIQUE_MANIFEST_PATH,
    TECHNIQUE_LEARNING_PATH as _TECHNIQUE_LEARNING_PATH,
    REPUTATION_MECHANICS_PATH as _REPUTATION_MECHANICS_PATH,
    REPUTATION_SIGNALS_PATH as _REPUTATION_SIGNALS_PATH,
    REPUTATION_INDEX_PATH as _REPUTATION_INDEX_PATH,
)
from shinobi_runtime.reducers import (
    InformationClaim,
    TrainingInputs,
    deliver_claim,
    settle_training,
)
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.domain import (
    ReputationEvidence,
    update_axis,
)
from shinobi_runtime.tx.manifest import TransactionManifest
from shinobi_runtime.people import field_usable_method_refs


class SocialCommandsMixin:
    def _information_registry(self) -> Dict[str, Any]:
        raw = self.repository.read_optional_bytes(_INFORMATION_REGISTRY_PATH)
        if raw is None:
            return {
                "schema": "information-registry",
                "owner_id": "registry.information",
                "owner_type": "information_registry",
                "claims": {},
                "deliveries": [],
                "knowledge": {},
            }
        try:
            registry = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CommandRejectedError("information_registry_invalid") from exc
        if (
            not isinstance(registry, dict)
            or registry.get("schema") != "information-registry"
            or not isinstance(registry.get("claims"), dict)
            or not isinstance(registry.get("deliveries"), list)
        ):
            raise CommandRejectedError("information_registry_invalid")
        knowledge = registry.setdefault("knowledge", {})
        if not isinstance(knowledge, dict):
            raise CommandRejectedError("information_registry_invalid")
        return registry
    @staticmethod
    def _relationship_shard_path(source_ref: str) -> str:
        component = re.sub(r"[^a-z0-9._-]", "_", source_ref)
        if not component or len(component) > 128:
            raise CommandRejectedError("relationship_source_invalid")
        return f"state/reg/relationship-edges/{component}.json"
    def _relationship_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _exact_payload(
            command.payload,
            ("target_ref", "relationship_type", "interaction_kind", "summary", "visibility"),
            command.command_type,
        )
        target_ref = _stable_id(command.payload["target_ref"], "relationship_target_invalid")
        if target_ref == command.actor_id:
            raise CommandRejectedError("relationship_target_invalid")
        relationship_type = command.payload["relationship_type"]
        interaction_kind = command.payload["interaction_kind"]
        summary = command.payload["summary"]
        visibility = command.payload["visibility"]
        if not isinstance(relationship_type, str) or not relationship_type or len(relationship_type) > 80:
            raise CommandRejectedError("relationship_type_invalid")
        if not isinstance(interaction_kind, str) or not interaction_kind or len(interaction_kind) > 80:
            raise CommandRejectedError("relationship_interaction_invalid")
        if not isinstance(summary, str) or not summary or len(summary) > 1000:
            raise CommandRejectedError("relationship_summary_invalid")
        if visibility not in ("public", "restricted", "secret"):
            raise CommandRejectedError("relationship_visibility_invalid")
        cache = _OwnerResolutionCache()
        try:
            self._resolve_covered_owner(target_ref, cache=cache)
            self._resolve_covered_owner(command.actor_id, cache=cache)
        except CommandRejectedError as exc:
            raise CommandRejectedError("relationship_person_unresolved") from exc
        try:
            rules = self.repository.read_json(_RELATIONSHIP_RULES_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("relationship_rules_invalid") from exc
        baseline = rules.get("baseline") if isinstance(rules, Mapping) else None
        interactions = rules.get("interactions") if isinstance(rules, Mapping) else None
        effect = interactions.get(interaction_kind) if isinstance(interactions, Mapping) else None
        if not isinstance(baseline, Mapping) or not isinstance(effect, Mapping):
            raise CommandRejectedError("relationship_interaction_unknown")
        shard_path = self._relationship_shard_path(target_ref)
        raw_shard = self.repository.read_optional_bytes(shard_path)
        if raw_shard is None:
            shard: Dict[str, Any] = {
                "schema": "relationship-edge-shard",
                "source_id": target_ref,
                "relationship_edges": {},
            }
            new_shard = True
        else:
            try:
                shard = json.loads(raw_shard.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CommandRejectedError("relationship_registry_invalid") from exc
            new_shard = False
        edges = shard.get("relationship_edges") if isinstance(shard, dict) else None
        if not isinstance(edges, dict) or shard.get("source_id") != target_ref:
            raise CommandRejectedError("relationship_registry_invalid")
        safe_type = re.sub(r"[^a-z0-9._-]", "_", relationship_type.lower())
        safe_source = re.sub(r"[^a-z0-9._-]", "_", target_ref)
        safe_target = re.sub(r"[^a-z0-9._-]", "_", command.actor_id)
        edge_id = f"rel.{safe_source}.{safe_target}.{safe_type}"
        existing = edges.get(edge_id)
        if existing is not None and not isinstance(existing, Mapping):
            raise CommandRejectedError("relationship_registry_invalid")
        values: Dict[str, Any] = dict(existing) if isinstance(existing, Mapping) else {
            "id": edge_id,
            "source_id": target_ref,
            "target_id": command.actor_id,
            "relationship_type": relationship_type,
            "trust": int(baseline.get("trust", 50)),
            "respect": int(baseline.get("respect", 50)),
            "affection": int(baseline.get("affection", 50)),
            "history": "",
            "current_tension": str(baseline.get("current_tension", "none_saved")),
            "duty": int(baseline.get("duty", 0)),
        }
        # Reputation conditions a stranger's initial expectations only when this
        # exact observer already has a persisted audience profile for the actor.
        # It never substitutes for relationship history after the edge exists.
        if existing is None:
            reputation = self._reputation_profile_for(command.actor_id, target_ref)
            if isinstance(reputation, Mapping):
                standing = reputation.get("standing")
                dimensions = reputation.get("dimensions")
                standing = standing if isinstance(standing, Mapping) else {}
                dimensions = dimensions if isinstance(dimensions, Mapping) else {}

                def rep_score(container: Mapping[str, Any], key: str) -> Optional[int]:
                    axis = container.get(key)
                    score = axis.get("score") if isinstance(axis, Mapping) else None
                    return score if isinstance(score, int) and not isinstance(score, bool) else None

                prestige = rep_score(standing, "prestige")
                renown = rep_score(standing, "renown")
                infamy = rep_score(standing, "infamy")
                reliability = rep_score(dimensions, "mission_reliability")
                loyalty = rep_score(dimensions, "institutional_loyalty")
                respect_shift = sum((score - 50) for score in (prestige, renown) if score is not None) // 10
                trust_shift = sum((score - 50) for score in (reliability, loyalty) if score is not None) // 12
                if infamy is not None:
                    trust_shift -= max(0, infamy - 50) // 10
                values["respect"] = max(0, min(100, int(values["respect"]) + respect_shift))
                values["trust"] = max(0, min(100, int(values["trust"]) + trust_shift))
        for key in ("trust", "respect", "affection", "duty"):
            prior = values.get(key, 0)
            delta = effect.get(key, 0)
            if isinstance(prior, bool) or not isinstance(prior, int) or isinstance(delta, bool) or not isinstance(delta, int):
                raise CommandRejectedError("relationship_rules_invalid")
            values[key] = max(0, min(100, prior + delta))
        tension = effect.get("tension")
        if isinstance(tension, str) and tension:
            values["current_tension"] = tension
        old_history = values.get("history")
        old_history = old_history if isinstance(old_history, str) else ""
        addition = f"{current_time}: {summary}"
        values["history"] = addition if not old_history else (old_history + " | " + addition)[-4000:]
        edges[edge_id] = values

        try:
            index = copy.deepcopy(self.repository.read_json(_RELATIONSHIP_INDEX_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("relationship_index_invalid") from exc
        edge_index = index.get("edge_index") if isinstance(index, dict) else None
        person_shards = index.get("person_shards") if isinstance(index, dict) else None
        if not isinstance(edge_index, dict) or not isinstance(person_shards, dict):
            raise CommandRejectedError("relationship_index_invalid")
        was_new_edge = edge_id not in edge_index
        edge_index[edge_id] = shard_path
        for person_ref in (target_ref, command.actor_id):
            refs = person_shards.setdefault(person_ref, [])
            if not isinstance(refs, list):
                raise CommandRejectedError("relationship_index_invalid")
            if shard_path not in refs:
                refs.append(shard_path)
                refs.sort()
        if was_new_edge:
            index["edge_count"] = int(index.get("edge_count", 0)) + 1
        if new_shard:
            index["source_shard_count"] = int(index.get("source_shard_count", 0)) + 1

        world_events = self._world_events()
        event_id = self._append_semantic_event(
            world_events, command=command, kind="relationship_changed", at=current_time,
            host_refs=(), actor_refs=(command.actor_id, target_ref),
            affected_owner_refs=(shard_path, _RELATIONSHIP_INDEX_PATH),
            material_consequence_refs=(edge_id,), classification=visibility,
            audience_refs=(command.actor_id, target_ref),
            reducer_ref="shinobi_runtime.commands.relationship_resolution",
        )
        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            shard_path: _json_bytes(shard),
            _RELATIONSHIP_INDEX_PATH: _json_bytes(index),
            **self._world_event_writes(world_events),
        }
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("relationship write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
            staged = overlay.read_json(shard_path)
            if edge_id not in staged.get("relationship_edges", {}):
                raise ValueError("relationship edge did not persist")
            staged_index = overlay.read_json(_RELATIONSHIP_INDEX_PATH)
            if staged_index.get("edge_index", {}).get(edge_id) != shard_path:
                raise ValueError("relationship derived index is stale")

        return _BuiltPlan(
            code="relationship_resolution_ready", affected_refs=expected_paths, writes=writes,
            result={
                "command_type": command.command_type, "relationship_edge_id": edge_id,
                "target_ref": target_ref, "interaction_kind": interaction_kind,
                "scores": {k: values[k] for k in ("trust", "respect", "affection", "duty")},
                "semantic_event_id": event_id,
            }, validator=validate,
        )
    def _career_authority(self, actor_ref: str, institution_ref: Optional[str]) -> str:
        if institution_ref is None:
            raise CommandRejectedError("career_institution_required")
        decision = self._domain_authority(cache=_OwnerResolutionCache()).owner_leadership(
            holder_ref=actor_ref, owner_ref=institution_ref
        )
        if not decision.allowed:
            raise CommandRejectedError("career_authority_denied")
        return decision.basis
    def _career_status_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _exact_payload(
            command.payload,
            ("action", "subject_ref", "target_rank_or_status", "institution_ref", "reason", "visibility"),
            command.command_type,
        )
        action = command.payload["action"]
        if action not in ("promote", "demote", "graduate", "retire", "status_change"):
            raise CommandRejectedError("career_action_invalid")
        subject_ref = _stable_id(command.payload["subject_ref"], "career_subject_invalid")
        target = command.payload["target_rank_or_status"]
        if not isinstance(target, str) or not target or len(target) > 160:
            raise CommandRejectedError("career_target_invalid")
        institution_raw = command.payload["institution_ref"]
        institution_ref = None if institution_raw is None else _stable_id(institution_raw, "career_institution_invalid")
        reason = command.payload["reason"]
        visibility = command.payload["visibility"]
        if not isinstance(reason, str) or not reason or len(reason) > 1000:
            raise CommandRejectedError("career_reason_invalid")
        if visibility not in ("public", "restricted", "secret"):
            raise CommandRejectedError("career_visibility_invalid")
        authority_basis = "self_retirement"
        if action == "retire" and command.actor_id == subject_ref:
            pass
        else:
            authority_basis = self._career_authority(command.actor_id, institution_ref)
        path, subject = self._resolve_actor_for_write(subject_ref)
        if subject.get("schema") != "shinobi_character":
            raise CommandRejectedError("career_subject_not_character")
        previous = subject.get("official_rank_or_status")
        subject["official_rank_or_status"] = target
        career = subject.get("career_state")
        if career is None:
            career = {}
            subject["career_state"] = career
        if not isinstance(career, dict):
            raise CommandRejectedError("career_state_invalid")
        career["rank"] = target
        career["current_rank_or_status"] = target
        if action in ("promote", "graduate"):
            career["promotion_eligible"] = False
        if action == "retire":
            career["assignment"] = "retired"
            career["retirement_eligible"] = False
        life = subject.get("life_course_state")
        if life is None:
            life = {"rank_history": [], "status_history": [], "injury_events": [], "relationship_events": [], "location_history": []}
            subject["life_course_state"] = life
        if not isinstance(life, dict):
            raise CommandRejectedError("career_history_invalid")
        history = life.setdefault("rank_history", [])
        if not isinstance(history, list):
            raise CommandRejectedError("career_history_invalid")
        history.append({"at": str(current_time), "rank": target, "reason": reason})
        status_history = life.setdefault("status_history", [])
        if isinstance(status_history, list):
            status_history.append(f"{current_time}: {action}: {target}: {reason}")

        world_events = self._world_events()
        event_id = self._append_semantic_event(
            world_events, command=command, kind="career_status_changed", at=current_time,
            host_refs=tuple(x for x in (institution_ref,) if x), actor_refs=(command.actor_id, subject_ref),
            affected_owner_refs=(path,), material_consequence_refs=(f"career:{subject_ref}:{previous}->{target}",),
            classification=visibility, audience_refs=(command.actor_id, subject_ref),
            reducer_ref="shinobi_runtime.commands.career_status_resolution",
        )
        writes={self.meta_path:_json_bytes(self._meta_after(meta,command,world_time=current_time)),path:_json_bytes(subject),**self._world_event_writes(world_events)}
        writes=self._prune_noop_writes(writes); expected=tuple(sorted(writes))
        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths!=expected: raise ValueError("career write set changed after planning")
            self._assert_meta(overlay,manifest,meta_path=self.meta_path,command=command,world_time=current_time)
            if overlay.read_json(path).get("official_rank_or_status")!=target: raise ValueError("career status did not persist")
        return _BuiltPlan(code="career_status_resolution_ready",affected_refs=expected,writes=writes,
            result={"command_type":command.command_type,"action":action,"subject_ref":subject_ref,"previous":previous,"current":target,"authority_basis":authority_basis,"semantic_event_id":event_id},validator=validate)
    def _office_assignment_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _exact_payload(
            command.payload,
            ("action", "subject_ref", "institution_ref", "office_ref", "reason", "visibility"),
            command.command_type,
        )
        action=command.payload["action"]
        if action not in ("appoint","remove","transfer"):
            raise CommandRejectedError("office_action_invalid")
        subject_ref=_stable_id(command.payload["subject_ref"],"office_subject_invalid")
        institution_ref=_stable_id(command.payload["institution_ref"],"office_institution_invalid")
        office_raw=command.payload["office_ref"]
        office_ref=None if office_raw is None else _stable_id(office_raw,"office_ref_invalid")
        if action in ("appoint","transfer") and office_ref is None:
            raise CommandRejectedError("office_ref_required")
        if action=="remove" and office_ref is not None:
            raise CommandRejectedError("office_remove_ref_must_be_null")
        reason=command.payload["reason"]; visibility=command.payload["visibility"]
        if not isinstance(reason,str) or not reason or len(reason)>1000: raise CommandRejectedError("office_reason_invalid")
        if visibility not in ("public","restricted","secret"): raise CommandRejectedError("office_visibility_invalid")
        authority_basis=self._career_authority(command.actor_id,institution_ref)
        path,subject=self._resolve_actor_for_write(subject_ref)
        if subject.get("schema")!="shinobi_character": raise CommandRejectedError("office_subject_not_character")
        previous=subject.get("current_assignment_or_office")
        current=None if action=="remove" else office_ref
        subject["current_assignment_or_office"]=current
        career=subject.get("career_state")
        if career is None: career={}; subject["career_state"]=career
        if not isinstance(career,dict): raise CommandRejectedError("career_state_invalid")
        career["current_assignment_or_office"]=current
        career["assignment"]="unassigned" if current is None else current
        life=subject.get("life_course_state")
        if isinstance(life,dict):
            status=life.setdefault("status_history",[])
            if isinstance(status,list): status.append(f"{current_time}: office {action}: {previous}->{current}: {reason}")
        world_events=self._world_events()
        event_id=self._append_semantic_event(world_events,command=command,kind="office_assignment_changed",at=current_time,
            host_refs=(institution_ref,),actor_refs=(command.actor_id,subject_ref),affected_owner_refs=(path,),
            material_consequence_refs=(f"office:{subject_ref}:{previous}->{current}",),classification=visibility,
            audience_refs=(command.actor_id,subject_ref),reducer_ref="shinobi_runtime.commands.office_assignment_resolution")
        writes={self.meta_path:_json_bytes(self._meta_after(meta,command,world_time=current_time)),path:_json_bytes(subject),**self._world_event_writes(world_events)}
        writes=self._prune_noop_writes(writes); expected=tuple(sorted(writes))
        def validate(overlay: StagedOverlay,manifest:TransactionManifest)->None:
            if overlay.changed_paths!=expected: raise ValueError("office write set changed after planning")
            self._assert_meta(overlay,manifest,meta_path=self.meta_path,command=command,world_time=current_time)
            if overlay.read_json(path).get("current_assignment_or_office")!=current: raise ValueError("office assignment did not persist")
        return _BuiltPlan(code="office_assignment_resolution_ready",affected_refs=expected,writes=writes,
            result={"command_type":command.command_type,"action":action,"subject_ref":subject_ref,"previous":previous,"current":current,"authority_basis":authority_basis,"semantic_event_id":event_id},validator=validate)
    def _technique_record(self, technique_ref: str) -> Mapping[str, Any]:
        try:
            manifest = self.repository.read_json(_TECHNIQUE_MANIFEST_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("technique_manifest_invalid") from exc
        routes = manifest.get("techniques") if isinstance(manifest, Mapping) else None
        path = routes.get(technique_ref) if isinstance(routes, Mapping) else None
        if not isinstance(path, str):
            raise CommandRejectedError("technique_unresolved")
        try:
            record = self.repository.read_json(path)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("technique_unresolved") from exc
        if not isinstance(record, Mapping) or record.get("method_id") != technique_ref:
            raise CommandRejectedError("technique_record_invalid")
        return record
    def _technique_threshold(self, record: Mapping[str, Any]) -> int:
        try:
            mechanics = self.repository.read_json(_TECHNIQUE_LEARNING_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("technique_learning_mechanics_invalid") from exc
        table = mechanics.get("field_usable_mastery_by_rank") if isinstance(mechanics, Mapping) else None
        rank = record.get("rank_band")
        threshold = table.get(rank, table.get("unranked")) if isinstance(table, Mapping) else None
        if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 0:
            raise CommandRejectedError("technique_learning_mechanics_invalid")
        return threshold
    @staticmethod
    def _technique_prerequisites_met(student: Mapping[str, Any], record: Mapping[str, Any]) -> bool:
        prerequisites = record.get("prerequisites", [])
        if not isinstance(prerequisites, list):
            raise CommandRejectedError("technique_repertoire_invalid")
        try:
            known = field_usable_method_refs(student)
        except ValueError as exc:
            raise CommandRejectedError("technique_repertoire_invalid") from exc
        return all(isinstance(ref, str) and ref in known for ref in prerequisites)
    def _technique_learning_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _exact_payload(
            command.payload,
            ("action", "student_ref", "technique_ref", "teacher_ref", "target_time", "active_hours", "summary", "visibility"),
            command.command_type,
        )
        action = command.payload["action"]
        if action not in ("begin", "practice", "evaluate"):
            raise CommandRejectedError("technique_learning_action_invalid")
        student_ref = _stable_id(command.payload["student_ref"], "technique_student_invalid")
        if student_ref != command.actor_id:
            raise CommandRejectedError("technique_student_actor_mismatch")
        technique_ref = _stable_id(command.payload["technique_ref"], "technique_ref_invalid")
        teacher_raw = command.payload["teacher_ref"]
        teacher_ref = None if teacher_raw is None else _stable_id(teacher_raw, "technique_teacher_invalid")
        summary = command.payload["summary"]
        visibility = command.payload["visibility"]
        if not isinstance(summary, str) or not summary or len(summary) > 1000:
            raise CommandRejectedError("technique_learning_summary_invalid")
        if visibility not in ("public", "restricted", "secret"):
            raise CommandRejectedError("technique_learning_visibility_invalid")
        technique = self._technique_record(technique_ref)
        threshold = self._technique_threshold(technique)
        student_path, student = self._resolve_actor_for_write(student_ref)
        repertoire = student.get("repertoire")
        if not isinstance(repertoire, dict):
            raise CommandRejectedError("technique_repertoire_invalid")
        latent = repertoire.get("latent_or_locked_techniques")
        mastery = repertoire.get("method_mastery")
        if not isinstance(latent, list) or not isinstance(mastery, dict):
            raise CommandRejectedError("technique_repertoire_invalid")
        try:
            field = field_usable_method_refs(student)
        except ValueError as exc:
            raise CommandRejectedError("technique_repertoire_invalid") from exc
        if technique_ref in field and action in ("begin", "evaluate"):
            raise CommandRejectedError("technique_already_field_usable")

        teacher: Optional[Mapping[str, Any]] = None
        if teacher_ref is not None:
            _teacher_path, teacher = self._resolve_actor_for_write(teacher_ref)
            try:
                teacher_field = field_usable_method_refs(teacher)
            except ValueError as exc:
                raise CommandRejectedError("technique_teacher_lacks_method") from exc
            if technique_ref not in teacher_field:
                raise CommandRejectedError("technique_teacher_lacks_method")
            if teacher.get("current_location_id") != student.get("current_location_id"):
                raise CommandRejectedError("technique_teacher_not_colocated")

        world_time = current_time
        banks: Optional[Dict[str, Any]] = None
        outcome_result: Dict[str, Any] = {}
        base: Optional[_BuiltPlan] = None
        if action == "begin":
            if teacher_ref is None or teacher_ref == student_ref:
                raise CommandRejectedError("technique_access_source_required")
            if not self._technique_prerequisites_met(student, technique):
                raise CommandRejectedError("technique_prerequisites_unmet")
            if technique_ref not in latent:
                latent.append(technique_ref)
                latent.sort()
            mastery.setdefault(technique_ref, 0)
            if command.payload["target_time"] is not None or command.payload["active_hours"] is not None:
                raise CommandRejectedError("technique_learning_action_fields_invalid")
            outcome_result={"starting_mastery":int(mastery[technique_ref]),"ending_mastery":int(mastery[technique_ref]),"field_usable":False}
        elif action == "practice":
            if technique_ref not in latent and technique_ref not in field:
                raise CommandRejectedError("technique_learning_not_started")
            try:
                target_time=CampaignTime.parse(command.payload["target_time"])
                active_hours=Decimal(str(command.payload["active_hours"]))
            except Exception as exc:
                raise CommandRejectedError("technique_practice_time_invalid") from exc
            if target_time<=current_time or not active_hours.is_finite() or active_hours<=0:
                raise CommandRejectedError("technique_practice_time_invalid")
            elapsed=Decimal(int((_campaign_datetime(target_time)-_campaign_datetime(current_time)).total_seconds()))/Decimal(3600)
            if active_hours>elapsed:
                raise CommandRejectedError("technique_practice_time_invalid")
            base=self._time_spanning_base(command,meta,current_time,target_time=target_time)
            if base.result.get("interrupted") or CampaignTime.parse(base.result["world_time"])!=target_time:
                raise CommandRejectedError("time_boundary_requires_domain_settlement")
            world_time=target_time
            target=f"repertoire.method_mastery.{technique_ref}"
            # Ensure the dynamic method map has a lawful integer leaf before using the common reducer.
            if technique_ref not in mastery:
                mastery[technique_ref]=0
            starting=int(mastery[technique_ref])
            aptitude=self._training_aptitude(student,target)
            health_factor,recovery_factor=self._health_recovery_factor(student)
            model=self._training_model("training.self_directed")
            factors=model.get("base_factors")
            if not isinstance(factors,Mapping): raise CommandRejectedError("training_model_registry_invalid")
            instructor_quality=Decimal(str(factors["instructor_quality"]))
            if teacher is not None:
                teacher_mastery=teacher.get("repertoire",{}).get("method_mastery",{}).get(technique_ref,threshold)
                if isinstance(teacher_mastery,bool) or not isinstance(teacher_mastery,int): raise CommandRejectedError("technique_teacher_invalid")
                instructor_quality=max(Decimal("0.85"),min(Decimal("1.25"),Decimal("0.90")+Decimal(teacher_mastery)/Decimal(500)))
            location=student.get("current_location_id")
            if not isinstance(location,str): raise CommandRejectedError("training_context_invalid")
            facility_slots,facility_quality=self._training_facility_capacity(location,required_slots=1,base_quality_factor=factors["facility_quality"],required_categories=("technique",),module_required=False)
            try:banks=copy.deepcopy(self.repository.read_json(_DEVELOPMENT_BANK_PATH))
            except (FileNotFoundError,ValueError) as exc: raise CommandRejectedError("development_bank_invalid") from exc
            entries=banks.get("entries") if isinstance(banks,dict) else None
            if not isinstance(entries,dict): raise CommandRejectedError("development_bank_invalid")
            entry=entries.setdefault(student_ref,{"owner_type":"character","resolved_through":str(current_time),"credits":{}})
            if not isinstance(entry,dict) or not isinstance(entry.get("credits"),dict): raise CommandRejectedError("development_bank_invalid")
            residual=entry["credits"].get(target,0)
            outcome=settle_training(TrainingInputs(scheduled_hours=str(active_hours),attendance="1",
                available_instructor_hours=str(active_hours) if teacher is not None else "0",
                required_instructor_hours=str(active_hours) if teacher is not None else "0",
                facility_slots=facility_slots,required_slots="1",equipment_sets="1",required_sets="1",
                instructor_quality_factor=str(instructor_quality),facility_quality_factor=facility_quality,
                equipment_factor=str(factors["equipment"]),health_factor=health_factor,recovery_factor=recovery_factor,
                relevance_factor=str(factors["relevance"]),difficulty_fit_factor=str(factors["difficulty_fit"]),
                aptitude=aptitude,experience_modifier="1",current_value=starting,residual_units=residual,representation="exact"))
            mastery[technique_ref]=outcome.ending_value
            entry["credits"][target]=float(outcome.residual_units); entry["resolved_through"]=str(target_time)
            qualified=outcome.ending_value>=threshold and self._technique_prerequisites_met(student,technique)
            if qualified and technique_ref in latent:
                latent.remove(technique_ref)
            outcome_result={"starting_mastery":starting,"ending_mastery":outcome.ending_value,"points_gained":outcome.points_gained,"threshold":threshold,"field_usable":qualified or (technique_ref in field and technique_ref not in latent)}
        else:
            if command.payload["target_time"] is not None or command.payload["active_hours"] is not None or teacher_ref is not None:
                raise CommandRejectedError("technique_learning_action_fields_invalid")
            value=mastery.get(technique_ref)
            if isinstance(value,bool) or not isinstance(value,int): raise CommandRejectedError("technique_learning_not_started")
            qualified=value>=threshold and self._technique_prerequisites_met(student,technique)
            if not qualified: raise CommandRejectedError("technique_not_field_usable_yet")
            if technique_ref in latent: latent.remove(technique_ref)
            outcome_result={"starting_mastery":value,"ending_mastery":value,"threshold":threshold,"field_usable":True}

        world_events=self._world_events_after(base)
        event_id=self._append_semantic_event(world_events,command=command,kind="technique_learning_changed",at=world_time,
            host_refs=(),actor_refs=tuple(x for x in (student_ref,teacher_ref) if x),affected_owner_refs=(student_path,),
            material_consequence_refs=(f"technique:{student_ref}:{technique_ref}:{action}",),classification=visibility,
            audience_refs=tuple(x for x in (student_ref,teacher_ref) if x),reducer_ref="shinobi_runtime.commands.technique_learning_resolution")
        writes=dict(base.writes) if base is not None else {self.meta_path:_json_bytes(self._meta_after(meta,command,world_time=world_time))}
        writes[student_path]=_json_bytes(student)
        if banks is not None:writes[_DEVELOPMENT_BANK_PATH]=_json_bytes(banks)
        writes.update(self._world_event_writes(world_events)); writes=self._prune_noop_writes(writes); expected=tuple(sorted(writes))
        def validate(overlay:StagedOverlay,manifest:TransactionManifest)->None:
            if overlay.changed_paths!=expected: raise ValueError("technique learning write set changed after planning")
            self._assert_meta(overlay,manifest,meta_path=self.meta_path,command=command,world_time=world_time)
            staged=overlay.read_json(student_path).get("repertoire",{})
            if staged.get("method_mastery",{}).get(technique_ref)!=mastery.get(technique_ref): raise ValueError("technique mastery mismatch")
        return _BuiltPlan(code="technique_learning_resolution_ready",affected_refs=expected,writes=writes,
            result={"command_type":command.command_type,"action":action,"student_ref":student_ref,"technique_ref":technique_ref,**outcome_result,"world_time":str(world_time),"semantic_event_id":event_id},validator=validate)
    @staticmethod
    def _reputation_file_slug(value: str) -> str:
        return re.sub(r"[^a-zA-Z0-9._-]+", "_", value).strip("._-")
    @staticmethod
    def _reputation_subject_type(subject_ref: str, record: Optional[Mapping[str, Any]] = None) -> str:
        schema = record.get("schema") if isinstance(record, Mapping) else None
        if subject_ref.startswith("team.") or schema in ("exact-team", "team"):
            return "team"
        if subject_ref.startswith("formation.") or schema == "formation":
            return "formation"
        if subject_ref.startswith("force.") or schema == "force":
            return "force"
        if subject_ref.startswith("person.") or subject_ref.startswith("pc_") or subject_ref.startswith("canon_") or schema in ("shinobi_character", "person-core"):
            return "person"
        if subject_ref.startswith("faction") or schema == "faction-owner":
            return "faction"
        if subject_ref.startswith("place."):
            return "settlement"
        if subject_ref.startswith("house.") or subject_ref.startswith("institution"):
            return "institution"
        return "other"
    @staticmethod
    def _reputation_subject_path(subject_ref: str) -> str:
        safe = re.sub(r"[^a-z0-9._-]+", "_", subject_ref.lower()).strip("._-")
        digest = hashlib.sha256(subject_ref.encode("utf-8")).hexdigest()[:12]
        return f"state/reputation/subjects/{safe[:72]}.{digest}.json"
    @staticmethod
    def _reputation_profile_path(subject_ref: str, audience_id: str) -> str:
        digest = hashlib.sha256(f"{subject_ref}\x00{audience_id}".encode("utf-8")).hexdigest()[:24]
        return f"state/reputation/audiences/profile.{digest}.json"
    def _reputation_profile_for(self, subject_ref: str, audience_id: str) -> Optional[Mapping[str, Any]]:
        try:
            index = self.repository.read_json(_REPUTATION_INDEX_PATH)
        except (FileNotFoundError, ValueError):
            return None
        subjects = index.get("subjects") if isinstance(index, Mapping) else None
        subject_path = subjects.get(subject_ref) if isinstance(subjects, Mapping) else None
        if not isinstance(subject_path, str):
            return None
        try:
            subject = self.repository.read_json(subject_path)
        except (FileNotFoundError, ValueError):
            return None
        profiles = subject.get("audience_profiles") if isinstance(subject, Mapping) else None
        profile_path = profiles.get(audience_id) if isinstance(profiles, Mapping) else None
        if not isinstance(profile_path, str):
            return None
        try:
            profile = self.repository.read_json(profile_path)
        except (FileNotFoundError, ValueError):
            return None
        return profile if isinstance(profile, Mapping) else None
    def _reputation_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _exact_payload(
            command.payload,
            ("subject_ref", "audience_id", "source_event_ref", "signal_ref", "summary"),
            command.command_type,
        )
        subject_ref = _stable_id(command.payload["subject_ref"], "reputation_subject_invalid")
        audience_id = _stable_id(command.payload["audience_id"], "reputation_audience_invalid")
        source_event_ref = _stable_id(command.payload["source_event_ref"], "reputation_source_event_invalid", prefix="event.")
        signal_ref = _stable_id(command.payload["signal_ref"], "reputation_signal_invalid", prefix="reputation.signal.")
        summary = command.payload["summary"]
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 1000:
            raise CommandRejectedError("reputation_summary_invalid")

        source_event = self._world_event_by_id(source_event_ref)
        if not isinstance(source_event, Mapping):
            raise CommandRejectedError("reputation_source_event_unresolved")
        source_kind = source_event.get("kind")
        if not isinstance(source_kind, str):
            raise CommandRejectedError("reputation_source_event_invalid")
        visibility = source_event.get("visibility")
        witnesses = visibility.get("witness_refs") if isinstance(visibility, Mapping) else None
        audiences = visibility.get("audience_refs") if isinstance(visibility, Mapping) else None
        if not isinstance(witnesses, list) or not isinstance(audiences, list):
            raise CommandRejectedError("reputation_source_visibility_invalid")
        direct_witness = audience_id in witnesses
        direct_audience = audience_id in audiences
        if not direct_witness and not direct_audience:
            raise CommandRejectedError("reputation_audience_has_no_evidence_route")

        causal_values = []
        for key in ("actor_refs", "host_refs", "affected_owner_refs", "material_consequence_refs"):
            values = source_event.get(key)
            if isinstance(values, list):
                causal_values.extend(v for v in values if isinstance(v, str))
        if not any(subject_ref == value or subject_ref in value for value in causal_values):
            raise CommandRejectedError("reputation_subject_not_in_source_event")

        try:
            signal_registry = self.repository.read_json(_REPUTATION_SIGNALS_PATH)
            mechanics = self.repository.read_json(_REPUTATION_MECHANICS_PATH)
            index = copy.deepcopy(self.repository.read_json(_REPUTATION_INDEX_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("reputation_registry_invalid") from exc
        signals = signal_registry.get("signals") if isinstance(signal_registry, Mapping) else None
        signal = signals.get(signal_ref) if isinstance(signals, Mapping) else None
        if not isinstance(signal, Mapping):
            raise CommandRejectedError("reputation_signal_unknown")
        allowed = signal.get("allowed_event_kinds")
        if not isinstance(allowed, list) or source_kind not in allowed:
            raise CommandRejectedError("reputation_signal_source_mismatch")
        evidence_cfg = mechanics.get("evidence_update") if isinstance(mechanics, Mapping) else None
        prior_mass_cap = evidence_cfg.get("prior_mass_cap") if isinstance(evidence_cfg, Mapping) else None
        if isinstance(prior_mass_cap, bool) or not isinstance(prior_mass_cap, int) or prior_mass_cap <= 0:
            raise CommandRejectedError("reputation_mechanics_invalid")
        audience_relevance = signal.get("audience_relevance")
        if isinstance(audience_relevance, bool) or not isinstance(audience_relevance, int):
            raise CommandRejectedError("reputation_signal_invalid")
        memory_class = signal.get("memory_class")
        if memory_class not in ("ephemeral", "normal", "durable", "historical"):
            raise CommandRejectedError("reputation_signal_invalid")

        subjects = index.get("subjects") if isinstance(index, dict) else None
        if not isinstance(subjects, dict):
            raise CommandRejectedError("reputation_index_invalid")
        subject_path = subjects.get(subject_ref)
        subject_record: Optional[Mapping[str, Any]] = None
        if not isinstance(subject_path, str):
            try:
                _resolved_path, subject_record = self._resolve_covered_owner(subject_ref, cache=_OwnerResolutionCache())
            except CommandRejectedError:
                try:
                    _force_ref, _formation_path, formation = self._formation_by_id(subject_ref)
                    subject_record = formation
                except CommandRejectedError:
                    subject_record = None
            subject_path = self._reputation_subject_path(subject_ref)
            subject = {
                "schema": "reputation-subject",
                "subject_id": subject_ref,
                "subject_type": self._reputation_subject_type(subject_ref, subject_record),
                "as_of": str(current_time),
                "authority": True,
                "audience_profiles": {},
                "institutional_status_sources": [],
                "notes": [],
            }
            subjects[subject_ref] = subject_path
            index["subject_count"] = len(subjects)
        else:
            try:
                subject = copy.deepcopy(self.repository.read_json(subject_path))
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("reputation_subject_invalid") from exc
        profiles = subject.get("audience_profiles") if isinstance(subject, dict) else None
        if not isinstance(profiles, dict):
            raise CommandRejectedError("reputation_subject_invalid")
        profile_path = profiles.get(audience_id)
        new_profile = not isinstance(profile_path, str)
        if new_profile:
            profile_path = self._reputation_profile_path(subject_ref, audience_id)
            profile: Dict[str, Any] = {
                "schema": "reputation-audience-profile",
                "subject_id": subject_ref,
                "audience_id": audience_id,
                "as_of": str(current_time),
                "authority": True,
                "standing": {},
                "dimensions": {},
                "evidence_count": 0,
                "last_event_refs": [],
                "memory_class": memory_class,
            }
            profiles[audience_id] = profile_path
        else:
            try:
                profile = copy.deepcopy(self.repository.read_json(profile_path))
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("reputation_profile_invalid") from exc
        if profile.get("subject_id") != subject_ref or profile.get("audience_id") != audience_id:
            raise CommandRejectedError("reputation_profile_invalid")
        standing = profile.get("standing")
        dimensions = profile.get("dimensions")
        if not isinstance(standing, dict) or not isinstance(dimensions, dict):
            raise CommandRejectedError("reputation_profile_invalid")

        source_reliability = 100 if direct_witness else 95
        clarity = 100 if direct_witness else 95
        channel_integrity = 100
        corroboration = 100
        applied: Dict[str, Dict[str, int]] = {}
        for category, target in (("standing", standing), ("dimensions", dimensions)):
            definitions = signal.get(category)
            if not isinstance(definitions, Mapping):
                raise CommandRejectedError("reputation_signal_invalid")
            for axis, spec in sorted(definitions.items()):
                if not isinstance(axis, str) or not isinstance(spec, Mapping):
                    raise CommandRejectedError("reputation_signal_invalid")
                score = spec.get("score")
                base_weight = spec.get("base_weight")
                if any(isinstance(v, bool) or not isinstance(v, int) for v in (score, base_weight)):
                    raise CommandRejectedError("reputation_signal_invalid")
                evidence = ReputationEvidence(
                    signal_score=score,
                    base_weight=base_weight,
                    source_reliability=source_reliability,
                    clarity=clarity,
                    channel_integrity=channel_integrity,
                    audience_relevance=audience_relevance,
                    corroboration=corroboration,
                )
                updated = update_axis(target.get(axis) if isinstance(target.get(axis), Mapping) else None, evidence, prior_mass_cap=prior_mass_cap)
                if updated:
                    target[axis] = updated
                    applied[f"{category}.{axis}"] = dict(updated)

        rep_event_id = f"reputation.event.{command.digest[:24]}"
        rep_event_path = f"state/reputation/events/{rep_event_id}.json"
        if self.repository.read_optional_bytes(rep_event_path) is not None:
            raise CommandRejectedError("reputation_event_conflict")
        rep_event = {
            "schema": "reputation-event",
            "event_id": rep_event_id,
            "subject_id": subject_ref,
            "event_type": signal_ref,
            "occurred_at": str(current_time),
            "source_event_ref": source_event_ref,
            "authority": True,
            "signals": copy.deepcopy(dict(signal.get("dimensions", {}))),
            "standing_signals": copy.deepcopy(dict(signal.get("standing", {}))),
            "visibility": {"audience_id": audience_id, "source_classification": visibility.get("classification")},
            "witnesses": list(witnesses),
            "report_routes": [],
            "deliveries": {audience_id: {"profile_ref": profile_path, "source_event_ref": source_event_ref}},
            "status": "applied",
        }
        profile["as_of"] = str(current_time)
        profile["memory_class"] = memory_class
        profile["evidence_count"] = int(profile.get("evidence_count", 0)) + 1
        last_refs = profile.setdefault("last_event_refs", [])
        if not isinstance(last_refs, list):
            raise CommandRejectedError("reputation_profile_invalid")
        if rep_event_id not in last_refs:
            last_refs.append(rep_event_id)
            del last_refs[:-12]
        subject["as_of"] = str(current_time)
        if new_profile:
            index["audience_profile_count"] = int(index.get("audience_profile_count", 0)) + 1
        index["event_count"] = int(index.get("event_count", 0)) + 1

        world_events = self._world_events()
        semantic_event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind="reputation_updated",
            at=current_time,
            actor_refs=(command.actor_id,),
            causal_refs=(source_event_ref,),
            affected_owner_refs=(subject_path, profile_path, rep_event_path),
            material_consequence_refs=tuple(sorted(applied)),
            classification=str(visibility.get("classification") or "restricted"),
            audience_refs=(audience_id,),
            reducer_ref="shinobi_runtime.domain.reputation.update_axis",
        )
        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            _REPUTATION_INDEX_PATH: _json_bytes(index),
            subject_path: _json_bytes(subject),
            profile_path: _json_bytes(profile),
            rep_event_path: _json_bytes(rep_event),
            **self._world_event_writes(world_events),
        }
        writes = self._prune_noop_writes(writes)
        expected = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected:
                raise ValueError("reputation write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
            staged_profile = overlay.read_json(profile_path)
            if staged_profile.get("subject_id") != subject_ref or staged_profile.get("audience_id") != audience_id:
                raise ValueError("reputation profile identity changed")
            if overlay.read_json(rep_event_path).get("source_event_ref") != source_event_ref:
                raise ValueError("reputation provenance missing")

        return _BuiltPlan(
            code="reputation_resolution_ready",
            affected_refs=expected,
            writes=writes,
            result={
                "command_type": command.command_type,
                "subject_ref": subject_ref,
                "audience_id": audience_id,
                "source_event_ref": source_event_ref,
                "signal_ref": signal_ref,
                "profile_ref": profile_path,
                "applied": applied,
                "semantic_event_id": semantic_event_id,
            },
            validator=validate,
        )
    def _asset_transfer_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _exact_payload(
            command.payload,
            ("item_ref", "from_holder_ref", "to_holder_ref", "transfer_kind", "summary", "visibility"),
            command.command_type,
        )
        item_ref = _stable_id(command.payload["item_ref"], "asset_item_invalid", prefix="item_")
        from_ref = _stable_id(command.payload["from_holder_ref"], "asset_holder_invalid")
        to_ref = _stable_id(command.payload["to_holder_ref"], "asset_holder_invalid")
        if from_ref == to_ref:
            raise CommandRejectedError("asset_holder_invalid")
        transfer_kind = command.payload["transfer_kind"]
        if transfer_kind not in ("give", "issue", "return", "custody_transfer"):
            raise CommandRejectedError("asset_transfer_kind_invalid")
        summary = command.payload["summary"]
        visibility = command.payload["visibility"]
        if not isinstance(summary, str) or not summary or len(summary) > 1000:
            raise CommandRejectedError("asset_summary_invalid")
        if visibility not in ("public", "restricted", "secret"):
            raise CommandRejectedError("asset_visibility_invalid")
        cache = _OwnerResolutionCache()
        try:
            self._resolve_covered_owner(to_ref, cache=cache)
        except CommandRejectedError as exc:
            raise CommandRejectedError("asset_recipient_unresolved") from exc
        try:
            registry = copy.deepcopy(self.repository.read_json(_NAMED_ITEMS_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("asset_registry_invalid") from exc
        items = registry.get("named_items") if isinstance(registry, dict) else None
        if not isinstance(items, list):
            raise CommandRejectedError("asset_registry_invalid")
        matches = [item for item in items if isinstance(item, dict) and item.get("id") == item_ref]
        if len(matches) != 1:
            raise CommandRejectedError("asset_item_unresolved")
        item = matches[0]
        if item.get("physical_holder_id") != from_ref:
            raise CommandRejectedError("asset_holder_mismatch")
        authority_basis = "holder_self"
        if command.actor_id != from_ref:
            decision = self._domain_authority(cache=cache).owner_leadership(
                holder_ref=command.actor_id, owner_ref=from_ref
            )
            if not decision.allowed:
                raise CommandRejectedError("asset_transfer_not_authorized")
            authority_basis = decision.basis
        item["physical_holder_id"] = to_ref
        world_events = self._world_events()
        event_id = self._append_semantic_event(
            world_events, command=command, kind="asset_transferred", at=current_time,
            host_refs=(), actor_refs=(command.actor_id, from_ref, to_ref),
            affected_owner_refs=(_NAMED_ITEMS_PATH,), material_consequence_refs=(item_ref,),
            classification=visibility, audience_refs=(command.actor_id, from_ref, to_ref),
            reducer_ref="shinobi_runtime.commands.asset_transfer_resolution",
        )
        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            _NAMED_ITEMS_PATH: _json_bytes(registry),
            **self._world_event_writes(world_events),
        }
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("asset transfer write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
            staged = overlay.read_json(_NAMED_ITEMS_PATH).get("named_items", [])
            found = [entry for entry in staged if isinstance(entry, Mapping) and entry.get("id") == item_ref]
            if len(found) != 1 or found[0].get("physical_holder_id") != to_ref:
                raise ValueError("asset custody did not persist")

        return _BuiltPlan(
            code="asset_transfer_resolution_ready", affected_refs=expected_paths, writes=writes,
            result={
                "command_type": command.command_type, "item_ref": item_ref,
                "from_holder_ref": from_ref, "to_holder_ref": to_ref,
                "authority_basis": authority_basis, "semantic_event_id": event_id,
            }, validator=validate,
        )
    def _information_claim_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _exact_payload(
            command.payload,
            ("claim_id", "subject_ref", "source_ref", "holder_ref", "epistemic_kind", "confidence_milli", "evidence_refs", "context_ref"),
            command.command_type,
        )
        claim_id = _stable_id(command.payload["claim_id"], "information_claim_invalid", prefix="claim.")
        subject_ref = _stable_id(command.payload["subject_ref"], "information_subject_invalid")
        source_ref = _stable_id(command.payload["source_ref"], "information_source_invalid")
        holder_ref = _stable_id(command.payload["holder_ref"], "information_holder_invalid")
        if holder_ref != command.actor_id:
            raise CommandRejectedError("information_holder_actor_mismatch")
        evidence = command.payload["evidence_refs"]
        if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes, bytearray)) or any(not isinstance(item, str) or not item for item in evidence):
            raise CommandRejectedError("information_evidence_invalid")
        context_raw = command.payload["context_ref"]
        context_ref = None
        if context_raw is not None:
            context_ref = _stable_id(context_raw, "information_context_invalid")
            if context_ref.startswith("mission."):
                _mission_path, mission_owner = self._read_mission(
                    context_ref, actor_id=command.actor_id, current_time=current_time
                )
                if mission_owner.mission.state != "active":
                    raise CommandRejectedError("information_context_mission_not_active")
                if holder_ref not in mission_owner.mission.participant_refs:
                    raise CommandRejectedError("information_holder_not_mission_participant")
            else:
                raise CommandRejectedError("information_context_invalid")
        cache = _OwnerResolutionCache()
        try:
            self._resolve_covered_owner(holder_ref, cache=cache)
            self._resolve_covered_owner(subject_ref, cache=cache)
        except CommandRejectedError as exc:
            raise CommandRejectedError("information_owner_unresolved") from exc
        registry = self._information_registry()
        if claim_id in registry["claims"]:
            raise CommandRejectedError("information_claim_conflict")
        known = registry["knowledge"].get(holder_ref, [])
        if not isinstance(known, list):
            raise CommandRejectedError("information_registry_invalid")
        world_events = self._world_events()
        for ref in evidence:
            if ref.startswith("claim."):
                if ref not in registry["claims"] or ref not in known:
                    raise CommandRejectedError("information_evidence_not_known")
            elif ref.startswith("event."):
                if self._world_event_by_id(ref, registry=world_events) is None:
                    raise CommandRejectedError("information_evidence_unresolved")
            else:
                raise CommandRejectedError("information_evidence_unresolved")
        if source_ref.startswith("event."):
            if self._world_event_by_id(source_ref, registry=world_events) is None:
                raise CommandRejectedError("information_source_unresolved")
        else:
            try:
                self._resolve_covered_owner(source_ref, cache=cache)
            except CommandRejectedError as exc:
                raise CommandRejectedError("information_source_unresolved") from exc
        try:
            claim = InformationClaim(
                claim_id=claim_id,
                subject_ref=subject_ref,
                source_ref=source_ref,
                collected_at=current_time,
                epistemic_kind=command.payload["epistemic_kind"],
                confidence_milli=command.payload["confidence_milli"],
                evidence_refs=tuple(evidence),
            )
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("information_claim_invalid") from exc
        claim_record = {
            "claim_id": claim.claim_id,
            "subject_ref": claim.subject_ref,
            "source_ref": claim.source_ref,
            "collected_at": str(claim.collected_at),
            "epistemic_kind": claim.epistemic_kind,
            "confidence_milli": claim.confidence_milli,
            "evidence_refs": list(claim.evidence_refs),
        }
        registry["claims"][claim_id] = claim_record
        holder_claims = registry["knowledge"].setdefault(holder_ref, [])
        if claim_id not in holder_claims:
            holder_claims.append(claim_id)
            holder_claims.sort()
        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind="information_claim_created",
            at=current_time,
            host_refs=(subject_ref,),
            actor_refs=(holder_ref,),
            causal_refs=tuple((*evidence, *((context_ref,) if context_ref is not None else ()))),
            affected_owner_refs=(_INFORMATION_REGISTRY_PATH,),
            material_consequence_refs=(claim_id,),
            classification="restricted",
            audience_refs=(holder_ref,),
            knowledge_refs=(claim_id,),
            source_refs=(source_ref,),
            reducer_ref="shinobi_runtime.reducers.information.InformationClaim",
        )
        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            _INFORMATION_REGISTRY_PATH: _json_bytes(registry),
            **self._world_event_writes(world_events),
        }
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("information claim write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
            staged = overlay.read_json(_INFORMATION_REGISTRY_PATH)
            if claim_id not in staged.get("knowledge", {}).get(holder_ref, []):
                raise ValueError("claim holder knowledge was not persisted")

        return _BuiltPlan(
            code="information_claim_resolution_ready",
            affected_refs=expected_paths,
            writes=writes,
            result={"command_type": command.command_type, "claim_id": claim_id, "holder_ref": holder_ref, "context_ref": context_ref, "semantic_event_id": event_id},
            validator=validate,
        )
    def _information_delivery(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _exact_payload(
            command.payload,
            ("claim_id", "sender_ref", "recipient_ref", "channel", "channel_confidence_milli"),
            command.command_type,
        )
        claim_id = _stable_id(command.payload["claim_id"], "information_claim_invalid", prefix="claim.")
        sender_ref = _stable_id(command.payload["sender_ref"], "information_sender_invalid")
        if sender_ref != command.actor_id:
            raise CommandRejectedError("information_sender_actor_mismatch")
        recipient_ref = _stable_id(command.payload["recipient_ref"], "information_recipient_invalid")
        cache = _OwnerResolutionCache()
        try:
            self._resolve_covered_owner(sender_ref, cache=cache)
            self._resolve_covered_owner(recipient_ref, cache=cache)
        except CommandRejectedError as exc:
            raise CommandRejectedError("information_participant_unresolved") from exc
        registry = self._information_registry()
        claim_record = registry["claims"].get(claim_id)
        if not isinstance(claim_record, Mapping):
            raise CommandRejectedError("information_claim_not_found")
        sender_known = registry["knowledge"].get(sender_ref, [])
        if not isinstance(sender_known, list) or claim_id not in sender_known:
            raise CommandRejectedError("information_sender_does_not_know_claim")
        try:
            claim = InformationClaim(
                claim_id=claim_record.get("claim_id"),
                subject_ref=claim_record.get("subject_ref"),
                source_ref=claim_record.get("source_ref"),
                collected_at=CampaignTime.parse(claim_record.get("collected_at")),
                epistemic_kind=claim_record.get("epistemic_kind"),
                confidence_milli=claim_record.get("confidence_milli"),
                evidence_refs=tuple(claim_record.get("evidence_refs", [])),
            )
            delivery_id = "delivery." + command.digest[:24]
            delivery = deliver_claim(
                claim,
                delivery_id=delivery_id,
                sender_ref=sender_ref,
                recipient_ref=recipient_ref,
                channel=command.payload["channel"],
                delivered_at=current_time,
                channel_confidence_milli=command.payload["channel_confidence_milli"],
            )
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("information_delivery_invalid") from exc
        registry["deliveries"].append(dict(delivery.to_record()))
        recipient_known = registry["knowledge"].setdefault(recipient_ref, [])
        if claim_id not in recipient_known:
            recipient_known.append(claim_id)
            recipient_known.sort()
        world_events = self._world_events()
        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind="information_delivered",
            at=current_time,
            host_refs=(str(claim_record.get("subject_ref")),),
            actor_refs=(sender_ref, recipient_ref),
            causal_refs=(claim_id,),
            affected_owner_refs=(_INFORMATION_REGISTRY_PATH,),
            material_consequence_refs=(delivery_id,),
            classification="restricted",
            audience_refs=(recipient_ref,),
            knowledge_refs=(claim_id,),
            source_refs=(str(claim_record.get("source_ref")), sender_ref),
            reducer_ref="shinobi_runtime.reducers.information.deliver_claim",
        )
        scene = copy.deepcopy(self._scene_base(current_time))
        if recipient_ref == command.actor_id:
            narrative = scene.get("narrative")
            available = narrative.get("available_reports") if isinstance(narrative, dict) else None
            if isinstance(available, list) and delivery_id not in available:
                available.append(delivery_id)
                del available[:-6]
        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            self.scene_path: _json_bytes(scene),
            _INFORMATION_REGISTRY_PATH: _json_bytes(registry),
            **self._world_event_writes(world_events),
        }
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("information delivery write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
            staged = overlay.read_json(_INFORMATION_REGISTRY_PATH)
            if staged["deliveries"][-1]["delivery_id"] != delivery_id:
                raise ValueError("information delivery did not persist")
            if claim_id not in staged.get("knowledge", {}).get(recipient_ref, []):
                raise ValueError("recipient knowledge was not persisted")
            if "fact_ref" in staged["deliveries"][-1]:
                raise ValueError("information delivery illegally granted world truth")

        return _BuiltPlan(
            code="information_delivery_ready",
            affected_refs=expected_paths,
            writes=writes,
            result={"command_type": command.command_type, "claim_id": claim_id, "delivery": delivery.to_record(), "semantic_event_id": event_id},
            validator=validate,
        )

