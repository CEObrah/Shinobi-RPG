"""Mission report handoff for evidence-backed investigative objectives.

A report command never accepts a recipient or an objective outcome from the
caller. It derives the report recipient and place from the live mission,
requires a runtime-produced examined investigation synthesis claim, persists the
information delivery, and emits objective-specific mission evidence. The normal
mission objective reducer remains the separate terminal authority.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.campaign_investigation import (
    CampaignCommandPlanner as _Base,
    _INVESTIGATION_PATH,
)
from shinobi_runtime.commands.core import _BuiltPlan, _exact_payload, _json_bytes, _stable_id
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.paths import WORLD_EVENT_REGISTRY_PATH
from shinobi_runtime.commands.specs import COMMAND_SPECS, CommandSpec
from shinobi_runtime.information import InformationStore
from shinobi_runtime.reducers import InformationClaim
from shinobi_runtime.reducers.information import deliver_claim
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest


def _install_mission_report_command_spec() -> None:
    COMMAND_SPECS.setdefault(
        "mission_report_resolution",
        CommandSpec(
            (
                "mission_ref",
                "objective_id",
                "claim_id",
                "channel",
                "channel_confidence_milli",
            ),
            (),
            "Deliver one runtime-produced objective synthesis to the mission's registered issuer at its registered reporting place; this command never declares objective success.",
        ),
    )


_install_mission_report_command_spec()


def _eligible_synthesis_claim(
    case: Mapping[str, Any],
    *,
    mission_ref: str,
    objective_id: str,
    claim_id: str,
) -> bool:
    if (
        case.get("mission_ref") != mission_ref
        or case.get("objective_id") != objective_id
        or case.get("status") != "examined"
    ):
        return False
    rows = case.get("revealed_observations")
    if not isinstance(rows, list):
        return False
    return any(
        isinstance(row, Mapping)
        and row.get("role") == "synthesis"
        and row.get("claim_ref") == claim_id
        for row in rows
    )


def _mission_report_material_ref(mission_ref: str, objective_id: str, claim_id: str) -> str:
    return f"mission_report:{mission_ref}:{objective_id}:{claim_id}"


class CampaignCommandPlanner(_Base):
    """Production planner with evidence-backed mission reporting."""

    COMMAND_TYPES = frozenset(COMMAND_SPECS)

    def _mission_report_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _exact_payload(
            command.payload,
            ("mission_ref", "objective_id", "claim_id", "channel", "channel_confidence_milli"),
            command.command_type,
        )
        mission_ref = _stable_id(
            command.payload["mission_ref"],
            "mission_report_mission_invalid",
            prefix="mission.",
        )
        objective_id = _stable_id(
            command.payload["objective_id"],
            "mission_report_objective_invalid",
            prefix="objective.",
        )
        claim_id = _stable_id(
            command.payload["claim_id"],
            "mission_report_claim_invalid",
            prefix="claim.",
        )
        _mission_path, owner = self._read_mission(
            mission_ref,
            actor_id=command.actor_id,
            current_time=current_time,
        )
        if owner.mission.state != "active":
            raise CommandRejectedError("mission_report_mission_not_active")
        objective = owner.mission.objective_by_id.get(objective_id)
        if objective is None or objective.kind != "investigate" or objective.status in ("succeeded", "failed"):
            raise CommandRejectedError("mission_report_objective_invalid")
        briefing = owner.briefing
        report_place_ref = None if briefing is None else briefing.report_place_ref
        if not isinstance(report_place_ref, str) or not report_place_ref:
            raise CommandRejectedError("mission_report_place_missing")
        scene = copy.deepcopy(self._scene_base(current_time))
        if scene.get("location_id") != report_place_ref:
            raise CommandRejectedError("mission_report_wrong_place")
        recipient_ref = owner.issuer_ref

        try:
            registry = self.repository.read_json(_INVESTIGATION_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("mission_report_investigation_missing") from exc
        cases = registry.get("cases") if isinstance(registry, Mapping) else None
        if not isinstance(cases, Mapping):
            raise CommandRejectedError("mission_report_investigation_invalid")

        staged_information: dict[str, dict[str, Any]] = {}
        information = InformationStore(self.repository, staged_information)
        try:
            claim_record = information.claim(claim_id)
            sender_known = information.holder_knows(command.actor_id, claim_id)
        except ValueError as exc:
            raise CommandRejectedError("mission_report_information_invalid") from exc
        if not isinstance(claim_record, Mapping) or not sender_known:
            raise CommandRejectedError("mission_report_claim_unavailable")
        case_ref = claim_record.get("subject_ref")
        if not isinstance(case_ref, str) or not case_ref.startswith("investigation.case."):
            raise CommandRejectedError("mission_report_claim_not_investigation")
        case = cases.get(case_ref)
        if not isinstance(case, Mapping) or not _eligible_synthesis_claim(
            case,
            mission_ref=mission_ref,
            objective_id=objective_id,
            claim_id=claim_id,
        ):
            raise CommandRejectedError("mission_report_claim_not_objective_synthesis")

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
                sender_ref=command.actor_id,
                recipient_ref=recipient_ref,
                channel=command.payload["channel"],
                delivered_at=current_time,
                channel_confidence_milli=command.payload["channel_confidence_milli"],
            )
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("mission_report_delivery_invalid") from exc
        try:
            information.add_delivery(dict(delivery.to_record()))
            information.grant(recipient_ref, claim_id)
        except ValueError as exc:
            raise CommandRejectedError("mission_report_information_invalid") from exc

        report_material_ref = _mission_report_material_ref(mission_ref, objective_id, claim_id)
        world_events = self._world_events()
        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind="information_delivered",
            at=current_time,
            host_refs=(mission_ref, case_ref),
            actor_refs=(command.actor_id, recipient_ref),
            place_refs=(report_place_ref,),
            causal_refs=(mission_ref, objective_id, case_ref, claim_id),
            affected_owner_refs=information.affected_paths,
            material_consequence_refs=(delivery_id, report_material_ref),
            classification="restricted",
            audience_refs=(recipient_ref,),
            knowledge_refs=(claim_id,),
            source_refs=(case_ref, command.actor_id),
            reducer_ref="shinobi_runtime.commands.campaign_mission_reporting.mission_report_resolution",
        )
        scene["scene_summary"] = (
            f"Mission report for {mission_ref} objective {objective_id} is delivered "
            f"to {recipient_ref} at {report_place_ref}."
        )
        scene["decision_required"] = None
        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            self.scene_path: _json_bytes(scene),
            **information.encoded_writes(),
            **self._world_event_writes(world_events),
        }
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("mission report write set changed after planning")
            self._assert_meta(
                overlay,
                manifest,
                meta_path=self.meta_path,
                command=command,
                world_time=current_time,
            )
            delivery_shard = overlay.read_json(InformationStore.delivery_shard_path(delivery_id))
            persisted = delivery_shard.get("deliveries", {}).get(delivery_id)
            if not isinstance(persisted, Mapping) or persisted.get("recipient_ref") != recipient_ref:
                raise ValueError("mission report delivery did not persist")
            knowledge = overlay.read_json(InformationStore.knowledge_shard_path(recipient_ref, claim_id))
            if claim_id not in knowledge.get("claim_refs", []):
                raise ValueError("mission report recipient knowledge did not persist")
            events = overlay.read_json(WORLD_EVENT_REGISTRY_PATH).get("events", [])
            report_event = next(
                (
                    row
                    for row in events
                    if isinstance(row, Mapping) and row.get("id") == event_id
                ),
                None,
            )
            if not isinstance(report_event, Mapping):
                raise ValueError("mission report semantic event did not persist")
            if objective_id not in report_event.get("causal_refs", []):
                raise ValueError("mission report lost objective causality")
            if report_material_ref not in report_event.get("material_consequence_refs", []):
                raise ValueError("mission report lost material consequence")

        return _BuiltPlan(
            code="mission_report_resolution_ready",
            affected_refs=expected_paths,
            writes=writes,
            result={
                "command_type": command.command_type,
                "mission_ref": mission_ref,
                "objective_id": objective_id,
                "claim_id": claim_id,
                "recipient_ref": recipient_ref,
                "report_place_ref": report_place_ref,
                "delivery": delivery.to_record(),
                "semantic_event_id": event_id,
            },
            validator=validate,
        )

    def _mission_objective_evidence(
        self,
        *,
        owner: Any,
        objective_id: str,
        target_status: str,
        progress_milli: int,
        evidence_event_id: str,
        current_time: CampaignTime,
    ) -> tuple[str, str]:
        evidence_ref, digest = super()._mission_objective_evidence(
            owner=owner,
            objective_id=objective_id,
            target_status=target_status,
            progress_milli=progress_milli,
            evidence_event_id=evidence_event_id,
            current_time=current_time,
        )
        objective = owner.mission.objective_by_id.get(objective_id)
        briefing = owner.briefing
        if (
            target_status == "succeeded"
            and objective is not None
            and objective.kind == "investigate"
            and briefing is not None
            and isinstance(briefing.report_place_ref, str)
            and briefing.report_place_ref
        ):
            registry = self._world_events()
            event = self._world_event_by_id(evidence_event_id, registry=registry)
            if not isinstance(event, Mapping) or event.get("kind") != "information_delivered":
                raise CommandRejectedError("mission_objective_report_evidence_required")
            causal_refs = event.get("causal_refs")
            material = event.get("material_consequence_refs")
            expected_material = _mission_report_material_ref(
                owner.mission_id,
                objective_id,
                next(
                    (
                        value
                        for value in event.get("knowledge_refs", [])
                        if isinstance(value, str) and value.startswith("claim.")
                    ),
                    "claim.invalid",
                ),
            )
            if (
                not isinstance(causal_refs, list)
                or objective_id not in causal_refs
                or not isinstance(material, list)
                or expected_material not in material
            ):
                raise CommandRejectedError("mission_objective_report_evidence_invalid")
        return evidence_ref, digest


__all__ = [
    "CampaignCommandPlanner",
    "_eligible_synthesis_claim",
    "_mission_report_material_ref",
]
