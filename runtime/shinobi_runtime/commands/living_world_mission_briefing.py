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
    def _dynamic_player_offer_source(self, *, demand_key: str, source_kind: object) -> Optional[Mapping[str, Any]]:
        """Resolve one concrete, already-persisted source for a dynamic offer lane.

        Dynamic offer lanes deliberately fail closed.  A mission market score is
        not enough to invent a criminal, convoy, alarm, or target.  The source
        registry must already contain the corresponding case/shipment/alarm.
        Stable lexical ordering keeps previews deterministic and prevents
        prospecting by retrying the same world state.
        """

        if source_kind == "legal_case":
            try:
                registry = self.repository.read_json("state/reg/legal-cases.json")
            except (FileNotFoundError, ValueError):
                return None
            cases = registry.get("cases") if isinstance(registry, Mapping) else None
            if not isinstance(cases, Mapping):
                return None
            eligible = []
            for case_ref, case in cases.items():
                if not isinstance(case_ref, str) or not isinstance(case, Mapping):
                    continue
                if case.get("status") not in ("open", "warranted", "bounty_posted"):
                    continue
                if not case.get("evidence_refs"):
                    continue
                if case.get("visibility") == "secret":
                    continue
                issuer = case.get("issuer_ref")
                if issuer not in (
                    "faction.konoha_mission_office",
                    "faction.konoha_anbu",
                    "faction.fire_border_authority",
                    "faction.fire_daimyo_liaison",
                ):
                    continue
                eligible.append((case_ref, case))
            if not eligible:
                return None
            case_ref, case = sorted(eligible, key=lambda row: row[0])[0]
            return {
                "source_ref": case_ref,
                "case": case,
            }

        if source_kind in ("lawful_shipment", "contraband_shipment"):
            try:
                registry = self.repository.read_json("state/reg/commerce.json")
            except (FileNotFoundError, ValueError):
                return None
            shipments = registry.get("shipments") if isinstance(registry, Mapping) else None
            if not isinstance(shipments, Mapping):
                return None
            contracts = registry.get("contracts") if isinstance(registry, Mapping) else None
            if not isinstance(contracts, Mapping):
                return None
            want_contraband = source_kind == "contraband_shipment"
            eligible = []
            for shipment_ref, shipment in shipments.items():
                if not isinstance(shipment_ref, str) or not isinstance(shipment, Mapping):
                    continue
                if shipment.get("status") != "in_transit":
                    continue
                contract = contracts.get(shipment.get("contract_ref"))
                if not isinstance(contract, Mapping) or bool(contract.get("contraband")) != want_contraband:
                    continue
                eligible.append((shipment_ref, shipment, contract))
            if not eligible:
                return None
            shipment_ref, shipment, contract = sorted(eligible, key=lambda row: row[0])[0]
            return {
                "source_ref": shipment_ref,
                "shipment": shipment,
                "contract": contract,
            }

        if source_kind == "security_alarm":
            try:
                registry = self.repository.read_json("state/reg/security-networks.json")
            except (FileNotFoundError, ValueError):
                return None
            alarms = registry.get("alarms") if isinstance(registry, Mapping) else None
            sectors = registry.get("sectors") if isinstance(registry, Mapping) else None
            if not isinstance(alarms, Mapping) or not isinstance(sectors, Mapping):
                return None
            eligible = []
            for alarm_ref, alarm in alarms.items():
                if not isinstance(alarm_ref, str) or not isinstance(alarm, Mapping):
                    continue
                if alarm.get("status") not in ("open", "acknowledged"):
                    continue
                recipients = alarm.get("recipient_refs")
                if not isinstance(recipients, list) or not any(
                    ref in recipients
                    for ref in (
                        "faction.konoha_mission_office",
                        "faction.konoha_anbu",
                        "canon_hiruzen",
                    )
                ):
                    continue
                sector = sectors.get(alarm.get("sector_ref"))
                if not isinstance(sector, Mapping):
                    continue
                eligible.append((alarm_ref, alarm, sector))
            if not eligible:
                return None
            alarm_ref, alarm, sector = sorted(eligible, key=lambda row: row[0])[0]
            return {"source_ref": alarm_ref, "alarm": alarm, "sector": sector}

        return None

    def _dynamic_player_offer_briefing_available(self, *, demand_key: str, source_kind: object) -> bool:
        return self._dynamic_player_offer_source(demand_key=demand_key, source_kind=source_kind) is not None

    def _route_endpoints(self, route_id: str) -> tuple[str, str]:
        try:
            graph = self._location_graph()
        except (CommandRejectedError, TypeError, ValueError) as exc:
            raise CommandRejectedError("player_mission_briefing_location_invalid") from exc
        for row in graph.routes:
            if row.get("id") == route_id:
                origin = row.get("from")
                destination = row.get("to")
                if isinstance(origin, str) and isinstance(destination, str):
                    return origin, destination
        raise CommandRejectedError("player_mission_briefing_route_invalid")

    def _dynamic_player_offer_briefing_config(
        self,
        *,
        faction_id: str,
        objective_kind: str,
        demand_key: str,
        source_kind: object,
    ) -> Optional[Mapping[str, Any]]:
        source = self._dynamic_player_offer_source(demand_key=demand_key, source_kind=source_kind)
        if not isinstance(source, Mapping):
            return None
        report_place = "place.konoha.mission_assignment_desk"

        if source_kind == "legal_case":
            case = source["case"]
            case_ref = str(source["source_ref"])
            return {
                "subject_kind": "case",
                "subject_ref": case_ref,
                "subject_label": f"Evidence-backed {case.get('case_kind', 'case')} ({case_ref})",
                "report_place_ref": report_place,
                "origin_place_ref": "place.konoha",
                "destination_place_ref": None,
                "route_id": None,
                "threat_summary": "An evidence-backed case requires further investigation. No target location or hostile presence is implied by the assignment.",
                "threat_source_ref": None,
                "intelligence_constraints": [
                    "Treat the case record and its cited evidence as the investigative starting point, not proof of facts it does not establish.",
                    "A warrant authorizes the stated legal action; it does not reveal the subject's current location.",
                ],
                "report_delay_hours": 2,
                "depart_delay_hours": 6,
                "completion_condition": "Return sourced findings or lawful custody/evidence sufficient to progress the assigned case.",
            }

        if source_kind in ("lawful_shipment", "contraband_shipment"):
            shipment = source["shipment"]
            contract = source["contract"]
            shipment_ref = str(source["source_ref"])
            route_id = str(shipment.get("route_ref"))
            origin, destination = self._route_endpoints(route_id)
            if source_kind == "lawful_shipment":
                return {
                    "subject_kind": "asset",
                    "subject_ref": shipment_ref,
                    "subject_label": f"In-transit shipment {shipment_ref}: {shipment.get('quantity')} × {shipment.get('item_ref')}",
                    "report_place_ref": report_place,
                    "origin_place_ref": origin,
                    "destination_place_ref": destination,
                    "route_id": route_id,
                    "threat_summary": "A real funded shipment is in transit. The assignment is to protect its custody and route completion; no attacker is assumed.",
                    "threat_source_ref": None,
                    "intelligence_constraints": [
                        "Protect the registered cargo and custodian rather than inventing unreported opposition.",
                        "Record loss, delay, diversion, or inspection as evidence if it actually occurs.",
                    ],
                    "report_delay_hours": 2,
                    "depart_delay_hours": 6,
                    "completion_condition": "Escort the registered shipment through its actual route until delivered or return a sourced interruption report.",
                }
            return {
                "subject_kind": "asset",
                "subject_ref": shipment_ref,
                "subject_label": f"Evidence-backed contraband shipment {shipment_ref}",
                "report_place_ref": report_place,
                "origin_place_ref": origin,
                "destination_place_ref": destination,
                "route_id": route_id,
                "threat_summary": "A registered in-transit shipment is marked contraband. Interdiction authority applies to that cargo only; no additional offenders are assumed.",
                "threat_source_ref": contract.get("carrier_ref") if isinstance(contract.get("carrier_ref"), str) else None,
                "intelligence_constraints": [
                    "Interdict only the registered shipment and persons lawfully tied to evidence discovered during the operation.",
                    "Preserve cargo, crossing records, and chain of custody for any later legal case.",
                ],
                "report_delay_hours": 2,
                "depart_delay_hours": 6,
                "completion_condition": "Secure, clear, or lawfully seize the registered contraband shipment and return preserved evidence.",
            }

        if source_kind == "security_alarm":
            alarm = source["alarm"]
            sector = source["sector"]
            alarm_ref = str(source["source_ref"])
            place_ref = sector.get("place_ref")
            if not isinstance(place_ref, str):
                return None
            return {
                "subject_kind": "information",
                "subject_ref": str(alarm.get("evidence_ref")),
                "subject_label": f"Security alarm {alarm_ref} at {place_ref}",
                "report_place_ref": report_place,
                "origin_place_ref": "place.konoha",
                "destination_place_ref": place_ref if place_ref != "place.konoha" else None,
                "route_id": None,
                "threat_summary": "A persistent security network generated an evidence-backed alarm. The alarm identifies a subject for verification, not automatic hostile intent.",
                "threat_source_ref": alarm.get("subject_ref") if isinstance(alarm.get("subject_ref"), str) else None,
                "intelligence_constraints": [
                    "Verify the alarm against its evidence before escalating force.",
                    "Preserve false-alarm, intrusion, and attribution outcomes distinctly.",
                ],
                "report_delay_hours": 1,
                "depart_delay_hours": 4,
                "completion_condition": "Resolve the registered alarm with sourced evidence and report the result to the issuing security authority.",
            }

        return None

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
        briefing_key = getattr(self, "_active_player_offer_demand_key", None)
        template = templates.get(briefing_key) if isinstance(templates, Mapping) and isinstance(briefing_key, str) else None
        dynamic_sources = config.get("dynamic_briefing_sources") if isinstance(config, Mapping) else None
        if not isinstance(template, Mapping) and isinstance(briefing_key, str) and isinstance(dynamic_sources, Mapping):
            source_kind = dynamic_sources.get(briefing_key)
            if source_kind is not None:
                template = self._dynamic_player_offer_briefing_config(
                    faction_id=faction_id,
                    objective_kind=objective_kind,
                    demand_key=briefing_key,
                    source_kind=source_kind,
                )
        if not isinstance(template, Mapping):
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
        elif subject_kind in ("asset", "information", "case"):
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
                # Local alarm response inside the origin place needs no travel
                # route.  Cross-place briefings still require a registered one.
                if template.get("destination_place_ref") != template.get("origin_place_ref"):
                    raise CommandRejectedError("player_mission_briefing_route_invalid")
            if not any(row.get("id") == route_id for row in graph.routes):
                if route_id is not None:
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
