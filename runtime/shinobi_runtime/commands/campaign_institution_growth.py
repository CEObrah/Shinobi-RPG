"""Bounded strategic-growth authority for campaign institutions."""
from __future__ import annotations

import copy
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.campaign_growth_planner import CampaignCommandPlanner as _Base
from shinobi_runtime.commands.core import (
    _BuiltPlan,
    _OwnerResolutionCache,
    _declared_payload,
    _json_bytes,
    _stable_id,
)
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.paths import COMMITMENT_REGISTRY_PATH, DOMAIN_REGISTRY_PATH
from shinobi_runtime.commands.specs import COMMAND_SPECS, CommandSpec
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest

_GROWTH_POLICY_PATH = "game/rules/institutions/growth-requests.json"


def _install_growth_command_specs() -> None:
    COMMAND_SPECS.setdefault(
        "institution_growth_request_resolution",
        CommandSpec(
            ("institution_ref", "scope_refs", "summary", "visibility"),
            (),
            "Submit one bounded institution-growth request and derive the saved authority holder's decision from registered governance policy.",
        ),
    )
    COMMAND_SPECS.setdefault(
        "institution_intake_resolution",
        CommandSpec(
            ("institution_ref", "source_pool_id", "applicant_count", "policy_ref", "summary", "visibility"),
            (),
            "Resolve one registered voluntary institution intake without deleting residents from their home population.",
        ),
    )


_install_growth_command_specs()


def _summary_and_visibility(payload: Mapping[str, Any], prefix: str) -> tuple[str, str]:
    summary = payload.get("summary")
    visibility = payload.get("visibility")
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 1000:
        raise CommandRejectedError(prefix + "_summary_invalid")
    if visibility not in ("public", "restricted", "secret"):
        raise CommandRejectedError(prefix + "_visibility_invalid")
    return summary.strip(), visibility


class CampaignCommandPlanner(_Base):
    """Production planner with retained-authority growth delegation."""

    COMMAND_TYPES = frozenset(COMMAND_SPECS)

    def _growth_policy_registry(self) -> Mapping[str, Any]:
        try:
            registry = self.repository.read_json(_GROWTH_POLICY_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("institution_growth_policy_invalid") from exc
        policies = registry.get("policies") if isinstance(registry, Mapping) else None
        if not isinstance(policies, Mapping):
            raise CommandRejectedError("institution_growth_policy_invalid")
        return policies

    def _institution_growth_policy(self, institution_ref: str) -> Mapping[str, Any] | None:
        policy = self._growth_policy_registry().get(institution_ref)
        if policy is None:
            return None
        if not isinstance(policy, Mapping):
            raise CommandRejectedError("institution_growth_policy_invalid")
        return policy

    def _growth_house(self, institution_ref: str) -> tuple[str, Mapping[str, Any]]:
        try:
            path, _digest, view = self._resolve_covered_owner_view(
                institution_ref, cache=_OwnerResolutionCache()
            )
        except CommandRejectedError as exc:
            raise CommandRejectedError("institution_growth_owner_unresolved") from exc
        if view.get("schema") != "house" or view.get("id") != institution_ref:
            raise CommandRejectedError("institution_growth_owner_invalid")
        return path, view

    def _active_growth_delegation(
        self,
        *,
        actor_ref: str,
        institution_ref: str,
        scope_ref: str,
        decision_authority_ref: str,
    ) -> str | None:
        try:
            registry = self.repository.read_json(COMMITMENT_REGISTRY_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("commitment_registry_invalid") from exc
        records = registry.get("records") if isinstance(registry, Mapping) else None
        if not isinstance(records, list):
            raise CommandRejectedError("commitment_registry_invalid")
        prefix = f"institution_growth_policy:{institution_ref}:{decision_authority_ref}"
        matches = [
            row for row in records
            if isinstance(row, Mapping)
            and row.get("kind") == "order"
            and row.get("status") == "active"
            and row.get("subject_ref") == actor_ref
            and row.get("target_ref") == scope_ref
            and row.get("host_ref") == institution_ref
            and isinstance(row.get("authority_basis"), str)
            and row.get("authority_basis").startswith(prefix)
        ]
        if len(matches) > 1:
            raise CommandRejectedError("institution_growth_delegation_ambiguous")
        return matches[0].get("id") if matches else None

    def _require_growth_scope(
        self,
        *,
        command: CommandEnvelope,
        institution_ref: str,
        scope_ref: str,
    ) -> str:
        policy = self._institution_growth_policy(institution_ref)
        if policy is None:
            authority = self._domain_authority(cache=_OwnerResolutionCache()).owner_leadership(
                holder_ref=command.actor_id, owner_ref=institution_ref
            )
            if not authority.allowed:
                raise CommandRejectedError("institution_growth_not_authorized")
            return authority.basis or "owner_leadership"
        authority_ref = policy.get("decision_authority_ref")
        if not isinstance(authority_ref, str) or not authority_ref:
            raise CommandRejectedError("institution_growth_policy_invalid")
        if command.actor_id == authority_ref:
            return "retained_strategic_authority"
        delegation = self._active_growth_delegation(
            actor_ref=command.actor_id,
            institution_ref=institution_ref,
            scope_ref=scope_ref,
            decision_authority_ref=authority_ref,
        )
        if delegation is None:
            raise CommandRejectedError("institution_growth_strategic_authority_required")
        return delegation

    def _institution_growth_request_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _declared_payload(command.payload, command.command_type)
        institution_ref = _stable_id(
            command.payload.get("institution_ref"),
            "institution_growth_institution_invalid",
            prefix="house.",
        )
        raw_scopes = command.payload.get("scope_refs")
        if (
            not isinstance(raw_scopes, list)
            or not raw_scopes
            or len(raw_scopes) > 16
            or any(not isinstance(value, str) or not value for value in raw_scopes)
            or len(set(raw_scopes)) != len(raw_scopes)
        ):
            raise CommandRejectedError("institution_growth_scopes_invalid")
        _summary, visibility = _summary_and_visibility(command.payload, "institution_growth")
        _house_path, house = self._growth_house(institution_ref)
        policy = self._institution_growth_policy(institution_ref)
        if policy is None:
            raise CommandRejectedError("institution_growth_policy_missing")
        authority_ref = policy.get("decision_authority_ref")
        requester_refs = policy.get("eligible_requester_refs")
        project_types = policy.get("delegable_project_types")
        recruitment_policies = policy.get("delegable_recruitment_policies")
        if (
            not isinstance(authority_ref, str)
            or not isinstance(requester_refs, list)
            or command.actor_id not in requester_refs
            or not isinstance(project_types, list)
            or not isinstance(recruitment_policies, list)
        ):
            raise CommandRejectedError("institution_growth_request_not_authorized")
        warrant = house.get("field_command_warrant")
        retained = warrant.get("strategic_house_authority_retained_by") if isinstance(warrant, Mapping) else None
        if retained != authority_ref:
            raise CommandRejectedError("institution_growth_policy_authority_mismatch")

        scene = self._scene_base(current_time)
        try:
            _apath, _adigest, authority_view = self._resolve_covered_owner_view(
                authority_ref, cache=_OwnerResolutionCache()
            )
            _ppath, _pdigest, actor_view = self._resolve_covered_owner_view(
                command.actor_id, cache=_OwnerResolutionCache()
            )
        except CommandRejectedError as exc:
            raise CommandRejectedError("institution_growth_decision_authority_unresolved") from exc
        actor_location = actor_view.get("current_location_id") or actor_view.get("location_ref")
        authority_location = authority_view.get("current_location_id") or authority_view.get("location_ref")
        if scene.get("location_id") != actor_location:
            raise CommandRejectedError("institution_growth_requester_not_in_scene")
        if policy.get("remote_review_allowed") is not True and actor_location != authority_location:
            raise CommandRejectedError("institution_growth_decision_authority_not_present")

        allowed = set()
        allowed.update(f"project:{value}" for value in project_types if isinstance(value, str) and value)
        allowed.update(f"recruitment:{value}" for value in recruitment_policies if isinstance(value, str) and value)
        requested = list(raw_scopes)
        approved = [value for value in requested if value in allowed]
        declined = [value for value in requested if value not in allowed]

        try:
            commitments = copy.deepcopy(self.repository.read_json(COMMITMENT_REGISTRY_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("commitment_registry_invalid") from exc
        records = commitments.get("records") if isinstance(commitments, dict) else None
        if not isinstance(records, list):
            raise CommandRejectedError("commitment_registry_invalid")

        delegation_ids: list[str] = []
        for index, scope_ref in enumerate(approved):
            existing = self._active_growth_delegation(
                actor_ref=command.actor_id,
                institution_ref=institution_ref,
                scope_ref=scope_ref,
                decision_authority_ref=authority_ref,
            )
            if existing is not None:
                delegation_ids.append(existing)
                continue
            commitment_id = f"commitment.growth.{command.digest[:16]}.{index:02d}"
            records.append({
                "id": commitment_id,
                "kind": "order",
                "subject_ref": command.actor_id,
                "target_ref": scope_ref,
                "host_ref": institution_ref,
                "created_at": str(current_time),
                "due_at": None,
                "status": "active",
                "summary": f"Delegated {scope_ref} within the registered {institution_ref} growth policy.",
                "visibility": visibility,
                "authority_basis": f"institution_growth_policy:{institution_ref}:{authority_ref}:registered_scope",
            })
            delegation_ids.append(commitment_id)

        decision = "approved" if approved and not declined else "modified" if approved else "declined"
        scene_after = copy.deepcopy(scene)
        scene_after["scene_summary"] = (
            f"{authority_ref} reviews {command.actor_id}'s {institution_ref} growth request; decision={decision}."
        )
        scene_after["decision_required"] = None
        scene_after["time_passage_allowed"] = True

        world_events = self._world_events()
        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind="institution_growth_request_reviewed",
            at=current_time,
            host_refs=(institution_ref,),
            actor_refs=(command.actor_id, authority_ref),
            affected_owner_refs=(institution_ref, COMMITMENT_REGISTRY_PATH),
            material_consequence_refs=tuple(
                [*(f"delegated:{value}" for value in approved), *(f"declined:{value}" for value in declined)]
            ),
            classification=visibility,
            audience_refs=(command.actor_id, authority_ref),
            reducer_ref="shinobi_runtime.commands.institution_growth_request_resolution",
        )
        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            COMMITMENT_REGISTRY_PATH: _json_bytes(commitments),
            self.scene_path: _json_bytes(scene_after),
        }
        writes.update(self._world_event_writes(world_events))
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("institution growth request write set changed after planning")
            self._assert_meta(
                overlay,
                manifest,
                meta_path=self.meta_path,
                command=command,
                world_time=current_time,
            )
            if overlay.read_json(COMMITMENT_REGISTRY_PATH) != commitments:
                raise ValueError("institution growth delegation after-image mismatch")
            if overlay.read_json(self.scene_path) != scene_after:
                raise ValueError("institution growth scene after-image mismatch")

        return _BuiltPlan(
            code="institution_growth_request_resolution_ready",
            affected_refs=expected_paths,
            writes=writes,
            result={
                "command_type": command.command_type,
                "institution_ref": institution_ref,
                "decision_authority_ref": authority_ref,
                "decision": decision,
                "approved_scope_refs": approved,
                "declined_scope_refs": declined,
                "delegation_ids": delegation_ids,
                "semantic_event_id": event_id,
            },
            validator=validate,
        )

    def _institution_project_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        institution_ref = command.payload.get("institution_ref")
        if not isinstance(institution_ref, str) or self._institution_growth_policy(institution_ref) is None:
            return super()._institution_project_resolution(command, meta, current_time)
        action = command.payload.get("action")
        project_type: str | None = None
        if action == "start":
            value = command.payload.get("project_type")
            project_type = value if isinstance(value, str) else None
        elif action in ("advance", "cancel"):
            project_ref = command.payload.get("project_ref")
            try:
                registry = self.repository.read_json(DOMAIN_REGISTRY_PATH)
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("institution_project_registry_invalid") from exc
            projects = registry.get("projects") if isinstance(registry, Mapping) else None
            matches = [row for row in projects or [] if isinstance(row, Mapping) and row.get("id") == project_ref]
            if len(matches) == 1 and isinstance(matches[0].get("project_type"), str):
                project_type = matches[0].get("project_type")
        if not project_type:
            raise CommandRejectedError("institution_project_type_invalid")
        self._require_growth_scope(
            command=command,
            institution_ref=institution_ref,
            scope_ref=f"project:{project_type}",
        )
        return super()._institution_project_resolution(command, meta, current_time)


__all__ = ["CampaignCommandPlanner", "_summary_and_visibility"]
