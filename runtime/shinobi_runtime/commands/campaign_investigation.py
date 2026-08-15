"""Deterministic mission investigation without caller-owned clues or outcomes.

The caller chooses investigators, assignments, place, and time spent. The
runtime owns the investigation profile, latent case truth, evidence thresholds,
and player-visible observations. This keeps investigation causal: briefing ->
search -> persisted observations/claims -> mission evidence.
"""
from __future__ import annotations

import copy
import hashlib
import json
from decimal import Decimal
from typing import Any, Dict, Mapping, Sequence

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.campaign_intake_onboarding import CampaignCommandPlanner as _Base
from shinobi_runtime.commands.core import _BuiltPlan, _OwnerResolutionCache, _campaign_datetime, _json_bytes, _stable_id
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.specs import COMMAND_SPECS, CommandSpec, CommandVariant
from shinobi_runtime.information import InformationStore
from shinobi_runtime.reducers import InformationClaim
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest

_MECHANICS_PATH = "game/data/mechanics/investigation.json"
_INVESTIGATION_PATH = "state/reg/investigations.json"


def _install_investigation_command_spec() -> None:
    COMMAND_SPECS.setdefault(
        "investigation_resolution",
        CommandSpec(
            (
                "action",
                "mission_ref",
                "objective_id",
                "place_ref",
                "investigator_refs",
                "assignments",
                "target_time",
                "active_hours",
            ),
            (),
            "Resolve bounded mission investigation from registered mechanics without caller-owned clues, culprits, or outcomes.",
            variants={
                "locate_scene": CommandVariant(
                    (
                        "action",
                        "mission_ref",
                        "objective_id",
                        "place_ref",
                        "investigator_refs",
                        "target_time",
                        "active_hours",
                    )
                ),
                "examine_scene": CommandVariant(
                    (
                        "action",
                        "mission_ref",
                        "objective_id",
                        "place_ref",
                        "assignments",
                        "target_time",
                        "active_hours",
                    )
                ),
            },
        ),
    )


_install_investigation_command_spec()


def _select_case_truth(mission_ref: str, profile_ref: str, truth_keys: Sequence[str]) -> str:
    """Choose latent case truth from stable case identity, never request entropy."""
    keys = tuple(value for value in truth_keys if isinstance(value, str) and value)
    if not keys or len(keys) != len(set(keys)):
        raise ValueError("investigation truth keys invalid")
    digest = hashlib.sha256(
        f"{mission_ref}\x00{profile_ref}\x00investigation-case-v1".encode("utf-8")
    ).digest()
    return keys[int.from_bytes(digest[:8], "big") % len(keys)]


def _case_ref(mission_ref: str, profile_ref: str) -> str:
    digest = hashlib.sha256(f"{mission_ref}\x00{profile_ref}".encode("utf-8")).hexdigest()[:24]
    return f"investigation.case.{digest}"


def _claim_ref(case_ref: str, observation_ref: str) -> str:
    digest = hashlib.sha256(f"{case_ref}\x00{observation_ref}".encode("utf-8")).hexdigest()[:24]
    return f"claim.investigation.{digest}"


def _number_at(record: Mapping[str, Any], path: str) -> int | None:
    value: Any = record
    for part in path.split("."):
        if not isinstance(value, Mapping):
            return None
        value = value.get(part)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return int(value)


class _PriorPlanOverlay:
    """Let the nested time-plan validator see exactly its original after-image."""

    def __init__(self, overlay: Any, prior: _BuiltPlan):
        self._overlay = overlay
        self._prior = prior
        self.changed_paths = tuple(sorted(prior.writes))

    def read_json(self, path: str) -> Any:
        raw = self._prior.writes.get(path)
        if raw is None:
            return self._overlay.read_json(path)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValueError("prior investigation time overlay decode failed") from exc

    def __getattr__(self, name: str) -> Any:
        return getattr(self._overlay, name)


class CampaignCommandPlanner(_Base):
    """Production planner with causal, evidence-producing investigations."""

    COMMAND_TYPES = frozenset(COMMAND_SPECS)

    def _investigation_mechanics(self) -> Mapping[str, Any]:
        try:
            row = self.repository.read_json(_MECHANICS_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("investigation_mechanics_invalid") from exc
        profiles = row.get("profiles") if isinstance(row, Mapping) else None
        if row.get("schema") != "investigation-mechanics" or not isinstance(profiles, Mapping):
            raise CommandRejectedError("investigation_mechanics_invalid")
        return row

    def _investigation_registry(self) -> Dict[str, Any]:
        raw = self.repository.read_optional_bytes(_INVESTIGATION_PATH)
        if raw is None:
            return {
                "schema": "investigation-registry",
                "owner_id": "registry.investigations",
                "owner_type": "investigation_registry",
                "cases": {},
            }
        try:
            row = copy.deepcopy(self.repository.read_json(_INVESTIGATION_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("investigation_registry_invalid") from exc
        if (
            not isinstance(row, dict)
            or row.get("schema") != "investigation-registry"
            or row.get("owner_id") != "registry.investigations"
            or row.get("owner_type") != "investigation_registry"
            or not isinstance(row.get("cases"), dict)
        ):
            raise CommandRejectedError("investigation_registry_invalid")
        return row

    @staticmethod
    def _matching_profile(owner: Any, objective_kind: str, mechanics: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
        briefing = getattr(owner, "briefing", None)
        if briefing is None:
            raise CommandRejectedError("investigation_briefing_required")
        brief = briefing.to_record()
        profiles = mechanics.get("profiles")
        matches: list[tuple[str, Mapping[str, Any]]] = []
        if not isinstance(profiles, Mapping):
            raise CommandRejectedError("investigation_mechanics_invalid")
        for profile_ref, profile in profiles.items():
            if not isinstance(profile_ref, str) or not isinstance(profile, Mapping):
                raise CommandRejectedError("investigation_mechanics_invalid")
            match = profile.get("match")
            if not isinstance(match, Mapping):
                raise CommandRejectedError("investigation_mechanics_invalid")
            expected = {
                "objective_kind": objective_kind,
                "subject_kind": brief.get("subject_kind"),
                "subject_ref": brief.get("subject_ref"),
                "subject_label": brief.get("subject_label"),
            }
            if all(match.get(key) == value for key, value in expected.items()):
                matches.append((profile_ref, profile))
        if not matches:
            raise CommandRejectedError("investigation_profile_missing")
        if len(matches) != 1:
            raise CommandRejectedError("investigation_profile_ambiguous")
        return matches[0]

    @staticmethod
    def _hours(command: CommandEnvelope, current_time: CampaignTime, minimum: object) -> tuple[CampaignTime, Decimal]:
        try:
            target_time = CampaignTime.parse(command.payload["target_time"])
            active_hours = Decimal(str(command.payload["active_hours"]))
            minimum_hours = Decimal(str(minimum))
        except Exception as exc:
            raise CommandRejectedError("investigation_time_invalid") from exc
        if target_time <= current_time or not active_hours.is_finite() or not minimum_hours.is_finite():
            raise CommandRejectedError("investigation_time_invalid")
        elapsed = Decimal(
            int((_campaign_datetime(target_time) - _campaign_datetime(current_time)).total_seconds())
        ) / Decimal(3600)
        if active_hours < minimum_hours or active_hours > elapsed:
            raise CommandRejectedError("investigation_active_hours_invalid")
        return target_time, active_hours

    def _investigator_views(
        self,
        refs: Sequence[str],
        *,
        mission_participants: Sequence[str],
        place_ref: str,
    ) -> Dict[str, Mapping[str, Any]]:
        if (
            isinstance(refs, (str, bytes, bytearray))
            or not refs
            or len(refs) > 16
            or any(not isinstance(ref, str) or not ref for ref in refs)
            or len(set(refs)) != len(refs)
        ):
            raise CommandRejectedError("investigation_investigators_invalid")
        participant_set = set(mission_participants)
        cache = _OwnerResolutionCache()
        views: Dict[str, Mapping[str, Any]] = {}
        for ref in refs:
            if ref not in participant_set:
                raise CommandRejectedError("investigation_investigator_not_mission_participant")
            try:
                _path, _digest, view = self._resolve_covered_owner_view(ref, cache=cache)
            except CommandRejectedError as exc:
                raise CommandRejectedError("investigation_investigator_unresolved") from exc
            if view.get("current_location_id") != place_ref:
                raise CommandRejectedError("investigation_investigator_not_colocated")
            if view.get("life_status") not in (None, "alive", "active"):
                raise CommandRejectedError("investigation_investigator_unavailable")
            views[ref] = view
        return views

    @staticmethod
    def _quality(record: Mapping[str, Any], paths: Sequence[str]) -> int:
        values = [value for path in paths if isinstance(path, str) for value in [_number_at(record, path)] if value is not None]
        if not values:
            raise CommandRejectedError("investigation_capability_missing")
        return max(values)

    @staticmethod
    def _append_scene_clues(scene: Dict[str, Any], texts: Sequence[str]) -> None:
        narrative = scene.setdefault("narrative", {})
        if not isinstance(narrative, dict):
            raise CommandRejectedError("campaign_scene_invalid")
        known = narrative.setdefault("known_clues", [])
        if not isinstance(known, list):
            raise CommandRejectedError("campaign_scene_invalid")
        for text in texts:
            if isinstance(text, str) and text and text not in known:
                known.append(text)
        del known[:-24]

    def _record_observations(
        self,
        *,
        information: InformationStore,
        case: Dict[str, Any],
        observations: Sequence[Mapping[str, Any]],
        investigator_refs: Sequence[str],
        at: CampaignTime,
    ) -> tuple[list[str], list[str]]:
        claim_refs: list[str] = []
        texts: list[str] = []
        revealed = case.setdefault("revealed_observations", [])
        if not isinstance(revealed, list):
            raise CommandRejectedError("investigation_registry_invalid")
        prior_claims = [
            row.get("claim_ref")
            for row in revealed
            if isinstance(row, Mapping) and isinstance(row.get("claim_ref"), str)
        ]
        for observation in observations:
            observation_ref = observation.get("observation_ref")
            text = observation.get("text")
            epistemic_kind = observation.get("epistemic_kind", "observation")
            confidence = observation.get("confidence_milli", 850)
            role = observation.get("role")
            stage = observation.get("stage")
            if (
                not isinstance(observation_ref, str)
                or not observation_ref
                or not isinstance(text, str)
                or not text
                or epistemic_kind not in ("observation", "inference")
                or isinstance(confidence, bool)
                or not isinstance(confidence, int)
                or not 0 <= confidence <= 1000
            ):
                raise CommandRejectedError("investigation_mechanics_invalid")
            claim_ref = _claim_ref(case["case_ref"], observation_ref)
            evidence_refs = tuple(prior_claims) if epistemic_kind == "inference" else ()
            try:
                claim = InformationClaim(
                    claim_id=claim_ref,
                    subject_ref=case["case_ref"],
                    source_ref=case["case_ref"],
                    collected_at=at,
                    epistemic_kind=epistemic_kind,
                    confidence_milli=confidence,
                    evidence_refs=evidence_refs,
                )
                record = {
                    "claim_id": claim.claim_id,
                    "subject_ref": claim.subject_ref,
                    "source_ref": claim.source_ref,
                    "collected_at": str(claim.collected_at),
                    "epistemic_kind": claim.epistemic_kind,
                    "confidence_milli": claim.confidence_milli,
                    "evidence_refs": list(claim.evidence_refs),
                }
                information.add_claim(record)
                for investigator_ref in investigator_refs:
                    information.grant(investigator_ref, claim_ref)
            except (TypeError, ValueError) as exc:
                raise CommandRejectedError("investigation_information_invalid") from exc
            revealed.append(
                {
                    "observation_ref": observation_ref,
                    "stage": stage,
                    "role": role,
                    "text": text,
                    "epistemic_kind": epistemic_kind,
                    "confidence_milli": confidence,
                    "claim_ref": claim_ref,
                    "revealed_at": str(at),
                }
            )
            prior_claims.append(claim_ref)
            claim_refs.append(claim_ref)
            texts.append(text)
        return claim_refs, texts

    def _investigation_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        action = command.payload.get("action")
        mission_ref = _stable_id(command.payload.get("mission_ref"), "investigation_mission_invalid", prefix="mission.")
        objective_id = _stable_id(command.payload.get("objective_id"), "investigation_objective_invalid", prefix="objective.")
        place_ref = _stable_id(command.payload.get("place_ref"), "investigation_place_invalid", prefix="place.")
        _mission_path, owner = self._read_mission(mission_ref, actor_id=command.actor_id, current_time=current_time)
        if owner.mission.state != "active":
            raise CommandRejectedError("investigation_mission_not_active")
        objective = owner.mission.objective_by_id.get(objective_id)
        if objective is None or objective.kind != "investigate" or objective.status in ("succeeded", "failed"):
            raise CommandRejectedError("investigation_objective_invalid")
        scene = self._scene_base(current_time)
        if scene.get("location_id") != place_ref:
            raise CommandRejectedError("investigation_wrong_place")

        mechanics = self._investigation_mechanics()
        profile_ref, profile = self._matching_profile(owner, objective.kind, mechanics)
        if owner.briefing is None or owner.briefing.subject_ref != place_ref:
            raise CommandRejectedError("investigation_wrong_place")
        truth_keys = profile.get("truth_keys")
        if not isinstance(truth_keys, Sequence) or isinstance(truth_keys, (str, bytes, bytearray)):
            raise CommandRejectedError("investigation_mechanics_invalid")

        registry = self._investigation_registry()
        cases = registry["cases"]
        case_ref = _case_ref(mission_ref, profile_ref)
        case = cases.get(case_ref)
        if case is not None and not isinstance(case, dict):
            raise CommandRejectedError("investigation_registry_invalid")

        if action == "locate_scene":
            stage_cfg = profile.get("locate_scene")
            if not isinstance(stage_cfg, Mapping):
                raise CommandRejectedError("investigation_mechanics_invalid")
            target_time, active_hours = self._hours(command, current_time, stage_cfg.get("minimum_active_hours"))
            refs = command.payload.get("investigator_refs")
            if not isinstance(refs, Sequence) or isinstance(refs, (str, bytes, bytearray)):
                raise CommandRejectedError("investigation_investigators_invalid")
            investigator_refs = tuple(refs)
            views = self._investigator_views(
                investigator_refs,
                mission_participants=owner.mission.participant_refs,
                place_ref=place_ref,
            )
            if command.actor_id not in investigator_refs:
                raise CommandRejectedError("investigation_player_not_participating")
            if case is not None and case.get("status") not in ("searching",):
                raise CommandRejectedError("investigation_stage_already_resolved")
            if case is None:
                try:
                    truth_key = _select_case_truth(mission_ref, profile_ref, truth_keys)
                except ValueError as exc:
                    raise CommandRejectedError("investigation_mechanics_invalid") from exc
                case = {
                    "case_ref": case_ref,
                    "mission_ref": mission_ref,
                    "objective_id": objective_id,
                    "profile_ref": profile_ref,
                    "place_ref": place_ref,
                    "truth_key": truth_key,
                    "status": "searching",
                    "created_at": str(current_time),
                    "updated_at": str(current_time),
                    "locate_work_units": 0,
                    "revealed_observations": [],
                }
                cases[case_ref] = case
            paths = stage_cfg.get("skill_paths")
            threshold = stage_cfg.get("required_work_units")
            if (
                not isinstance(paths, Sequence)
                or isinstance(paths, (str, bytes, bytearray))
                or isinstance(threshold, bool)
                or not isinstance(threshold, int)
                or threshold <= 0
            ):
                raise CommandRejectedError("investigation_mechanics_invalid")
            scores = sorted((self._quality(view, paths) for view in views.values()), reverse=True)
            team_quality = sum(scores[: min(3, len(scores))]) // min(3, len(scores))
            gained = int(Decimal(team_quality) * active_hours)
            prior_units = case.get("locate_work_units", 0)
            if isinstance(prior_units, bool) or not isinstance(prior_units, int) or prior_units < 0:
                raise CommandRejectedError("investigation_registry_invalid")
            case["locate_work_units"] = prior_units + gained
            resolved = case["locate_work_units"] >= threshold
            observations = []
            if resolved:
                common = stage_cfg.get("observations")
                if not isinstance(common, Sequence) or isinstance(common, (str, bytes, bytearray)):
                    raise CommandRejectedError("investigation_mechanics_invalid")
                observations = [dict(row) for row in common if isinstance(row, Mapping)]
                if len(observations) != len(common):
                    raise CommandRejectedError("investigation_mechanics_invalid")
                case["status"] = "located"
                case["located_at"] = str(target_time)
                case["investigator_refs"] = list(investigator_refs)
            case["updated_at"] = str(target_time)
            resolved_roles: list[str] = []
            unresolved_roles: list[str] = []

        elif action == "examine_scene":
            stage_cfg = profile.get("examine_scene")
            if not isinstance(stage_cfg, Mapping):
                raise CommandRejectedError("investigation_mechanics_invalid")
            target_time, active_hours = self._hours(command, current_time, stage_cfg.get("minimum_active_hours"))
            if case is None or case.get("status") not in ("located", "examining"):
                raise CommandRejectedError("investigation_scene_not_located")
            if case.get("mission_ref") != mission_ref or case.get("objective_id") != objective_id or case.get("place_ref") != place_ref:
                raise CommandRejectedError("investigation_case_mismatch")
            assignments = command.payload.get("assignments")
            roles = stage_cfg.get("roles")
            if not isinstance(assignments, Mapping) or not isinstance(roles, Mapping) or set(assignments) != set(roles):
                raise CommandRejectedError("investigation_assignments_invalid")
            investigator_refs = tuple(assignments[role] for role in roles)
            if any(not isinstance(ref, str) or not ref for ref in investigator_refs) or len(set(investigator_refs)) != len(investigator_refs):
                raise CommandRejectedError("investigation_assignments_invalid")
            views = self._investigator_views(
                investigator_refs,
                mission_participants=owner.mission.participant_refs,
                place_ref=place_ref,
            )
            if command.actor_id not in investigator_refs:
                raise CommandRejectedError("investigation_player_not_participating")
            role_work = case.setdefault("role_work_units", {})
            if not isinstance(role_work, dict):
                raise CommandRejectedError("investigation_registry_invalid")
            resolved_roles = []
            unresolved_roles = []
            newly_resolved: list[str] = []
            for role, cfg in roles.items():
                if not isinstance(role, str) or not isinstance(cfg, Mapping):
                    raise CommandRejectedError("investigation_mechanics_invalid")
                paths = cfg.get("skill_paths")
                threshold = cfg.get("required_work_units")
                if (
                    not isinstance(paths, Sequence)
                    or isinstance(paths, (str, bytes, bytearray))
                    or isinstance(threshold, bool)
                    or not isinstance(threshold, int)
                    or threshold <= 0
                ):
                    raise CommandRejectedError("investigation_mechanics_invalid")
                prior = role_work.get(role, 0)
                if isinstance(prior, bool) or not isinstance(prior, int) or prior < 0:
                    raise CommandRejectedError("investigation_registry_invalid")
                gained = int(Decimal(self._quality(views[assignments[role]], paths)) * active_hours)
                after = prior + gained
                role_work[role] = after
                if after >= threshold:
                    resolved_roles.append(role)
                    if prior < threshold:
                        newly_resolved.append(role)
                else:
                    unresolved_roles.append(role)
            observations = []
            common = stage_cfg.get("common_observations", {})
            truth_observations = stage_cfg.get("truth_observations")
            truth_key = case.get("truth_key")
            truth_rows = truth_observations.get(truth_key) if isinstance(truth_observations, Mapping) else None
            if not isinstance(common, Mapping) or not isinstance(truth_rows, Mapping):
                raise CommandRejectedError("investigation_mechanics_invalid")
            revealed_ids = {
                row.get("observation_ref")
                for row in case.get("revealed_observations", [])
                if isinstance(row, Mapping)
            }
            for role in newly_resolved:
                if role == "synthesis":
                    continue
                row = common.get(role) or truth_rows.get(role)
                if not isinstance(row, Mapping):
                    raise CommandRejectedError("investigation_mechanics_invalid")
                record = dict(row)
                record.setdefault("role", role)
                record.setdefault("stage", "examine_scene")
                if record.get("observation_ref") not in revealed_ids:
                    observations.append(record)
            non_synthesis_roles = [role for role in roles if role != "synthesis"]
            if (
                "synthesis" in resolved_roles
                and all(role in resolved_roles for role in non_synthesis_roles)
            ):
                synthesis = truth_rows.get("synthesis")
                if not isinstance(synthesis, Mapping):
                    raise CommandRejectedError("investigation_mechanics_invalid")
                record = dict(synthesis)
                record.setdefault("role", "synthesis")
                record.setdefault("stage", "examine_scene")
                if record.get("observation_ref") not in revealed_ids:
                    observations.append(record)
                case["status"] = "examined"
                case["examined_at"] = str(target_time)
            else:
                case["status"] = "examining"
            case["assignments"] = {role: assignments[role] for role in roles}
            case["updated_at"] = str(target_time)

        else:
            raise CommandRejectedError("investigation_action_invalid")

        base = self._time_spanning_base(command, meta, current_time, target_time=target_time)
        if base.result.get("interrupted"):
            raise CommandRejectedError("time_boundary_requires_domain_settlement")
        if CampaignTime.parse(base.result.get("world_time")) != target_time:
            raise CommandRejectedError("investigation_time_settlement_incomplete")
        try:
            scene_after = json.loads(base.writes[self.scene_path].decode("utf-8"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise CommandRejectedError("campaign_scene_invalid") from exc
        if not isinstance(scene_after, dict) or scene_after.get("location_id") != place_ref:
            raise CommandRejectedError("investigation_wrong_place")

        staged_information: Dict[str, Dict[str, Any]] = {}
        information = InformationStore(self.repository, staged_information)
        claim_refs, observation_texts = self._record_observations(
            information=information,
            case=case,
            observations=observations,
            investigator_refs=investigator_refs,
            at=target_time,
        )
        self._append_scene_clues(scene_after, observation_texts)
        scene_after["scene_summary"] = (
            f"Investigation {action} resolves through {target_time}; "
            f"case stage={case.get('status')}."
        )
        scene_after["decision_required"] = None

        world_events = self._world_events_after(base)
        material_refs = [f"investigation:{case_ref}:{case.get('status')}", *claim_refs]
        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind="information_claim_created",
            at=target_time,
            host_refs=(mission_ref, case_ref),
            actor_refs=investigator_refs,
            place_refs=(place_ref,),
            causal_refs=(mission_ref, objective_id),
            affected_owner_refs=(_INVESTIGATION_PATH, *information.affected_paths),
            material_consequence_refs=tuple(material_refs),
            classification="restricted",
            audience_refs=investigator_refs,
            knowledge_refs=claim_refs,
            source_refs=(case_ref,),
            reducer_ref="shinobi_runtime.commands.campaign_investigation.investigation_resolution",
        )

        writes = dict(base.writes)
        writes[self.meta_path] = _json_bytes(self._meta_after(meta, command, world_time=target_time))
        writes[self.scene_path] = _json_bytes(scene_after)
        writes[_INVESTIGATION_PATH] = _json_bytes(registry)
        writes.update(information.encoded_writes())
        writes.update(self._world_event_writes(world_events))
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))
        prior_validator = base.validator

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("investigation write set changed after planning")
            prior_validator(_PriorPlanOverlay(overlay, base), manifest)
            self._assert_meta(
                overlay,
                manifest,
                meta_path=self.meta_path,
                command=command,
                world_time=target_time,
            )
            staged_registry = overlay.read_json(_INVESTIGATION_PATH)
            staged_case = staged_registry.get("cases", {}).get(case_ref)
            if not isinstance(staged_case, Mapping) or staged_case.get("status") != case.get("status"):
                raise ValueError("investigation case after-image mismatch")
            staged_scene = overlay.read_json(self.scene_path)
            known = staged_scene.get("narrative", {}).get("known_clues", [])
            if any(text not in known for text in observation_texts):
                raise ValueError("investigation scene projection lost revealed observations")
            for claim_ref in claim_refs:
                claim_shard = overlay.read_json(InformationStore.claim_shard_path(claim_ref))
                if claim_ref not in claim_shard.get("claims", {}):
                    raise ValueError("investigation claim did not persist")
                for investigator_ref in investigator_refs:
                    knowledge = overlay.read_json(InformationStore.knowledge_shard_path(investigator_ref, claim_ref))
                    if claim_ref not in knowledge.get("claim_refs", []):
                        raise ValueError("investigation knowledge grant did not persist")

        return _BuiltPlan(
            code="investigation_resolution_ready",
            affected_refs=expected_paths,
            writes=writes,
            result={
                "command_type": command.command_type,
                "action": action,
                "mission_ref": mission_ref,
                "objective_id": objective_id,
                "case_ref": case_ref,
                "stage": case.get("status"),
                "world_time": str(target_time),
                "investigator_refs": list(investigator_refs),
                "resolved_roles": resolved_roles,
                "unresolved_roles": unresolved_roles,
                "observations": observation_texts,
                "claim_refs": claim_refs,
                "semantic_event_id": event_id,
            },
            validator=validate,
        )


__all__ = ["CampaignCommandPlanner", "_select_case_truth"]
