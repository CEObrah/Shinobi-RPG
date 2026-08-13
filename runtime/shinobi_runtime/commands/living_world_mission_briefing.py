"""Operational briefing projection for living-world player mission offers.

Player-facing missions must be executable assignments, not only objective verbs.
This mixin decorates the ordinary living-world offer path with bounded authored
briefing templates. It never creates a hidden opponent: unknown threat sources
remain explicitly unknown until campaign evidence establishes them.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, Mapping, Optional

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _OwnerResolutionCache
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.mission_owner import MissionBrief, MissionOwner, mission_owner_path
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.sim.scheduler import CausalSchedulerRegistry


class LivingWorldMissionBriefingMixin:
    def _player_offer_briefing_config(
        self,
        *,
        faction_id: str,
        objective_kind: str,
    ) -> Mapping[str, Any]:
        try:
            _profile, assignment = self._autonomy_policy_book().faction_context(faction_id)
        except (TypeError, ValueError, CommandRejectedError) as exc:
            raise CommandRejectedError("player_mission_briefing_policy_invalid") from exc
        config = assignment.get("player_offer") if isinstance(assignment, Mapping) else None
        templates = config.get("briefing_templates") if isinstance(config, Mapping) else None
        template = templates.get(objective_kind) if isinstance(templates, Mapping) else None
        if not isinstance(template, Mapping):
            raise CommandRejectedError("player_mission_briefing_template_missing")
        return template

    def _validate_player_offer_briefing_refs(
        self,
        *,
        template: Mapping[str, Any],
    ) -> None:
        subject_kind = template.get("subject_kind")
        subject_ref = template.get("subject_ref")
        if subject_kind == "person":
            if not isinstance(subject_ref, str) or not subject_ref:
                raise CommandRejectedError("player_mission_briefing_subject_invalid")
            try:
                _path, _digest, person = self._resolve_covered_owner_view(
                    subject_ref,
                    cache=_OwnerResolutionCache(),
                )
            except CommandRejectedError as exc:
                raise CommandRejectedError("player_mission_briefing_subject_invalid") from exc
            if not isinstance(person, Mapping) or person.get("life_status", person.get("life_status", "alive")) == "dead":
                raise CommandRejectedError("player_mission_briefing_subject_invalid")
        elif subject_kind == "place":
            if not isinstance(subject_ref, str) or not subject_ref.startswith("place."):
                raise CommandRejectedError("player_mission_briefing_subject_invalid")
        elif subject_kind in ("asset", "information"):
            if subject_ref is not None and not isinstance(subject_ref, str):
                raise CommandRejectedError("player_mission_briefing_subject_invalid")
        else:
            raise CommandRejectedError("player_mission_briefing_subject_invalid")

        try:
            graph = self._location_graph()
        except (CommandRejectedError, TypeError, ValueError) as exc:
            raise CommandRejectedError("player_mission_briefing_location_invalid") from exc
        place_refs = [
            template.get("report_place_ref"),
            template.get("origin_place_ref"),
            template.get("destination_place_ref"),
        ]
        for place_ref in place_refs:
            if place_ref is None:
                continue
            if not isinstance(place_ref, str) or not place_ref.startswith("place.") or graph.place(place_ref) is None:
                raise CommandRejectedError("player_mission_briefing_location_invalid")
        if subject_kind == "place" and graph.place(subject_ref) is None:
            raise CommandRejectedError("player_mission_briefing_location_invalid")

        route_id = template.get("route_id")
        if template.get("destination_place_ref") is not None:
            if not isinstance(route_id, str) or not route_id:
                raise CommandRejectedError("player_mission_briefing_route_invalid")
            if not any(row.get("id") == route_id for row in graph.routes):
                raise CommandRejectedError("player_mission_briefing_route_invalid")
        elif route_id is not None:
            raise CommandRejectedError("player_mission_briefing_route_invalid")

    def _build_player_offer_briefing(
        self,
        *,
        faction_id: str,
        objective_kind: str,
        mission_id: str,
        opened_at: CampaignTime,
        deadline_at: Optional[CampaignTime],
    ) -> MissionBrief:
        template = self._player_offer_briefing_config(
            faction_id=faction_id,
            objective_kind=objective_kind,
        )
        self._validate_player_offer_briefing_refs(template=template)

        report_delay = template.get("report_delay_hours")
        depart_delay = template.get("depart_delay_hours")
        if (
            isinstance(report_delay, bool)
            or not isinstance(report_delay, int)
            or not 0 <= report_delay <= 48
            or isinstance(depart_delay, bool)
            or not isinstance(depart_delay, int)
            or not report_delay <= depart_delay <= 72
        ):
            raise CommandRejectedError("player_mission_briefing_time_policy_invalid")
        report_at = opened_at.add_seconds(report_delay * 60 * 60)
        depart_by = opened_at.add_seconds(depart_delay * 60 * 60)
        if deadline_at is not None and (report_at > deadline_at or depart_by > deadline_at):
            raise CommandRejectedError("player_mission_briefing_time_policy_invalid")

        constraints = template.get("intelligence_constraints")
        if not isinstance(constraints, list) or any(not isinstance(value, str) for value in constraints):
            raise CommandRejectedError("player_mission_briefing_policy_invalid")
        try:
            return MissionBrief(
                briefing_id=f"briefing.{mission_id.removeprefix('mission.')}",
                objective_kind=objective_kind,
                subject_kind=str(template.get("subject_kind")),
                subject_ref=template.get("subject_ref"),
                subject_label=str(template.get("subject_label")),
                report_place_ref=str(template.get("report_place_ref")),
                origin_place_ref=str(template.get("origin_place_ref")),
                destination_place_ref=template.get("destination_place_ref"),
                route_id=template.get("route_id"),
                threat_summary=str(template.get("threat_summary")),
                threat_source_ref=template.get("threat_source_ref"),
                intelligence_constraints=tuple(constraints),
                report_at=report_at,
                depart_by=depart_by,
                completion_condition=str(template.get("completion_condition")),
            )
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("player_mission_briefing_policy_invalid") from exc

    def _brief_player_offer_record(
        self,
        *,
        mission_id: str,
        faction_id: str,
        record_writes: Dict[str, Dict[str, Any]],
    ) -> MissionOwner:
        path = mission_owner_path(mission_id)
        raw = record_writes.get(path)
        if not isinstance(raw, Mapping):
            raise CommandRejectedError("player_mission_briefing_offer_missing")
        try:
            owner = MissionOwner.from_record(raw)
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("player_mission_briefing_offer_invalid") from exc
        if owner.briefing is not None:
            return owner
        if len(owner.mission.objectives) != 1:
            raise CommandRejectedError("player_mission_briefing_objective_invalid")
        brief = self._build_player_offer_briefing(
            faction_id=faction_id,
            objective_kind=owner.mission.objectives[0].kind,
            mission_id=mission_id,
            opened_at=owner.opened_at,
            deadline_at=owner.deadline_at,
        )
        owner = replace(owner, briefing=brief)
        record_writes[path] = dict(owner.to_record())
        return owner

    def _maybe_offer_player_mission(
        self,
        *,
        decision: Any,
        at: CampaignTime,
        command: CommandEnvelope,
        scheduler: CausalSchedulerRegistry,
        world_events: Dict[str, Any],
        record_writes: Dict[str, Dict[str, Any]],
        faction_record: Dict[str, Any],
    ) -> Optional[Mapping[str, Any]]:
        result = super()._maybe_offer_player_mission(
            decision=decision,
            at=at,
            command=command,
            scheduler=scheduler,
            world_events=world_events,
            record_writes=record_writes,
            faction_record=faction_record,
        )
        if not isinstance(result, Mapping) or result.get("kind") != "player_mission_offer":
            return result
        if result.get("skipped") is not None:
            return result
        mission_id = result.get("mission_id")
        faction_id = decision.payload.get("faction_id") if hasattr(decision, "payload") else None
        if not isinstance(mission_id, str) or not isinstance(faction_id, str):
            raise CommandRejectedError("player_mission_briefing_offer_invalid")
        owner = self._brief_player_offer_record(
            mission_id=mission_id,
            faction_id=faction_id,
            record_writes=record_writes,
        )
        if owner.briefing is None:
            raise CommandRejectedError("player_mission_briefing_offer_invalid")
        return {
            **dict(result),
            "briefing_id": owner.briefing.briefing_id,
            "report_at": None if owner.briefing.report_at is None else str(owner.briefing.report_at),
            "depart_by": None if owner.briefing.depart_by is None else str(owner.briefing.depart_by),
            "assignment_basis": "lawful_faction_demand_player_led_team_availability_operational_brief",
        }


__all__ = ["LivingWorldMissionBriefingMixin"]
