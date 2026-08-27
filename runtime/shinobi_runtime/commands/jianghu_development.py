"""Command-surface integration for field and combat development.

Field development is part of the same conserved command transaction as travel
and combat. A standing retinue is a zero-time identity owner while idle, but
available members automatically join Wei's strategic travel, consume the same
finite travel interval, pause institutional training while committed, move to
the same destination and gain only development supported by the hours they
actually lived: bounded active route duty plus bounded self-practice drawn from
non-route rest time.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _json_bytes
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.martial_world.equipment import carried_mass_kg, encumbrance_effects
from shinobi_runtime.martial_world.aggregate_transport import faction_available_capacity, make_transport_reservation
from shinobi_runtime.martial_world.equipment_state import effective_person_loadout
from shinobi_runtime.martial_world.field_development import apply_combat_events
from shinobi_runtime.martial_world.faction_state import inventory_path as canonical_inventory_path
from shinobi_runtime.martial_world.health import functional_capacity_factors
from shinobi_runtime.martial_world.live_state import roster_person, set_roster_person
from shinobi_runtime.martial_world.mounts import active_mount_allocations
from shinobi_runtime.martial_world.travel import travel_plan
from shinobi_runtime.martial_world.physical_travel import build_route_journey, stage_route_journey
from shinobi_runtime.martial_world.travel_provisions import planned_journey_seconds, provisioning_journey_seconds, reserve_personal_rations
from shinobi_runtime.sim.events import CampaignTime

_DEPLOYMENTS = "state/martial-world/deployments.json"
_LOCAL_SITES = "game/data/martial-world/local-sites.json"
_GEOGRAPHY = "game/data/martial-world/geography.json"
_EQUIPMENT = "state/martial-world/equipment-ledger.json"
_EQUIPMENT_DATA = "game/data/martial-world/equipment.json"
_COMBATS = "state/martial-world/combats.json"
_ROUTE_OPERATIONS = "state/martial-world/route-operations.json"
_SCHEDULER = "state/martial-world/scheduler.json"


def _dt(time: CampaignTime):
    from datetime import datetime
    return datetime(time.year, time.month, time.day, time.hour, time.minute, time.second)


class _PlanReadView:
    def __init__(self, repository: Any, writes: dict[str, bytes]) -> None:
        self._repository = repository
        self._writes = writes

    def read_json(self, path: str) -> Any:
        raw = self._writes.get(path)
        if raw is not None:
            return json.loads(raw.decode("utf-8"))
        return self._repository.read_json(path)


class JianghuDevelopmentCommandsMixin:
    def _replace_person_in_plan(
        self, writes: dict[str, bytes], person_ref: str, person_after: Mapping[str, Any]
    ) -> bool:
        view = _PlanReadView(self.repository, writes)
        try:
            path, roster, ordinal, _current = roster_person(view, person_ref)
        except (FileNotFoundError, KeyError, TypeError, ValueError):
            return False
        writes[path] = _json_bytes(set_roster_person(roster, ordinal, person_after))
        return True

    def _plan_person(self, writes: dict[str, bytes], person_ref: str) -> dict[str, Any] | None:
        view = _PlanReadView(self.repository, writes)
        try:
            _path, _roster, _ordinal, person = roster_person(view, person_ref)
        except (FileNotFoundError, KeyError, TypeError, ValueError):
            return None
        return copy.deepcopy(dict(person))

    @staticmethod
    def _with_plan(
        built: _BuiltPlan, *, writes: Mapping[str, bytes], result: Mapping[str, Any]
    ) -> _BuiltPlan:
        return _BuiltPlan(
            code=built.code,
            affected_refs=tuple(sorted(writes)),
            writes=dict(writes),
            result=dict(result),
            validator=built.validator,
        )

    def _standing_retinue_member_roles(self, actor_ref: str) -> dict[str, str]:
        try:
            state = self.repository.read_json(_DEPLOYMENTS)
        except FileNotFoundError:
            return {}
        rows = state.get("deployments", {}) if isinstance(state, Mapping) else {}
        if not isinstance(rows, Mapping):
            return {}
        roles: dict[str, str] = {}
        for retinue_ref in sorted(str(ref) for ref in rows if isinstance(ref, str)):
            row = rows.get(retinue_ref)
            if not isinstance(row, Mapping):
                continue
            if row.get("operation_kind") != "standing_retinue" or row.get("status") != "active":
                continue
            if row.get("leader_ref") != actor_ref:
                continue
            members = row.get("member_refs", [])
            member_roles = row.get("member_roles", {}) if isinstance(row.get("member_roles"), Mapping) else {}
            if isinstance(members, list):
                for ref in members:
                    if isinstance(ref, str) and ref:
                        roles.setdefault(ref, str(member_roles.get(ref) or ""))
        return roles

    def _standing_retinue_member_refs(self, actor_ref: str) -> list[str]:
        return list(self._standing_retinue_member_roles(actor_ref))

    def _jianghu_strategic_travel_resolution(
        self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime
    ) -> _BuiltPlan:
        """Travel through the same physical route owner used by escorts and raids.

        The command owns Wei's intent to reach a destination. ``route-operations``
        owns the actual moving party, current edge, elapsed travel, exposure and
        hostile contact. A hard road contact commits the partial journey and
        returns control instead of rolling back the trip or teleporting the party.
        """
        destination = str(command.payload.get("destination_site_ref") or "")
        mode = str(command.payload.get("mode") or "")
        if mode not in {"foot", "horse", "pack"}:
            raise CommandRejectedError("jianghu_travel_mode_invalid")

        rpath, roster, ordinal, actor = self._person(command.actor_id)
        self._require_person_available_for_activity(command.actor_id)
        sites_data = self.repository.read_json(_LOCAL_SITES)
        sites = sites_data.get("sites", {}) if isinstance(sites_data, Mapping) else {}
        start_site = sites.get(actor.get("location_ref")) if isinstance(sites, Mapping) else None
        end_site = sites.get(destination) if isinstance(sites, Mapping) else None
        if not isinstance(start_site, Mapping) or not isinstance(end_site, Mapping):
            raise CommandRejectedError("jianghu_travel_site_unresolved")
        start = str(start_site.get("parent_place_ref") or "")
        end = str(end_site.get("parent_place_ref") or "")
        if start == end:
            raise CommandRejectedError("jianghu_use_local_travel")

        faction_ref = str(actor.get("faction_ref") or "")
        retinue_roles = self._standing_retinue_member_roles(command.actor_id)
        retinue_refs = list(retinue_roles)
        available_retinue: list[str] = []
        unavailable_retinue: list[str] = []
        for ref in retinue_refs:
            try:
                _p, _r, _o, member = self._person(ref)
            except CommandRejectedError:
                unavailable_retinue.append(ref)
                continue
            if (
                str(member.get("faction_ref") or "") != faction_ref
                or str(member.get("location_ref") or "") != str(actor.get("location_ref") or "")
                or not self._person_available_for_activity(ref)
            ):
                unavailable_retinue.append(ref)
                continue
            available_retinue.append(ref)
        party_refs = [command.actor_id, *available_retinue]

        equipment = self.repository.read_json(_EQUIPMENT)
        catalog = self.repository.read_json(_EQUIPMENT_DATA)
        member_masses: dict[str, float] = {}
        member_encumbrance: dict[str, Mapping[str, Any]] = {}
        party_speed_values: list[int] = []
        load_time_values: list[int] = []

        for ref in party_refs:
            _p, _r, _o, person = self._person(ref)
            health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
            wounds = health.get("injuries", []) if isinstance(health.get("injuries"), list) else []
            body = functional_capacity_factors([row for row in wounds if isinstance(row, Mapping)])
            walking = max(0, min(1000, int(body.get("walking_milli", 1000))))
            mounted_stability = max(0, min(1000, int(body.get("mounted_stability_milli", 1000))))
            function = mounted_stability if mode == "horse" else walking
            if function <= 0:
                if ref == command.actor_id:
                    raise CommandRejectedError("jianghu_travel_function_unavailable")
                unavailable_retinue.append(ref)
                continue
            try:
                loadout = effective_person_loadout(equipment, ref)
                mass = carried_mass_kg(loadout.get("items", {}), catalog)
                attrs = person.get("attributes", {}) if isinstance(person.get("attributes"), Mapping) else {}
                enc = encumbrance_effects(
                    total_mass_kg=mass,
                    strength=int(attrs.get("strength", 0)),
                    endurance=int(attrs.get("endurance", 0)),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise CommandRejectedError("jianghu_travel_loadout_invalid") from exc
            load_time = max(1000, 1_000_000 // max(1, int(enc["movement_factor_milli"])))
            if mode in {"horse", "pack"}:
                load_time = 1000 + max(0, load_time - 1000) // 2
            speed = max(50, 500 + mounted_stability // 2) if mode == "horse" else max(50, walking)
            member_masses[ref] = float(mass)
            member_encumbrance[ref] = dict(enc)
            party_speed_values.append(speed)
            load_time_values.append(load_time)

        party_refs = [ref for ref in party_refs if ref in member_masses]
        available_retinue = [ref for ref in available_retinue if ref in member_masses]
        unavailable_retinue = list(dict.fromkeys(unavailable_retinue))
        if not party_refs or party_refs[0] != command.actor_id:
            raise CommandRejectedError("jianghu_travel_function_unavailable")

        transport_reservation = None
        if mode in {"horse", "pack"}:
            inv = self.repository.read_json(canonical_inventory_path(faction_ref))
            route_snapshot = self.repository.read_json(_ROUTE_OPERATIONS)
            available_capacity = faction_available_capacity(inv, route_snapshot, faction_ref=faction_ref)
            if mode == "horse":
                rider_slots = max(0, int(available_capacity.get("rider_slots", 0)))
                rider_slots -= active_mount_allocations(self.repository.read_json(_COMBATS), faction_ref=faction_ref)
                if rider_slots < len(party_refs):
                    raise CommandRejectedError("jianghu_transport_capacity_unavailable")
                transport_reservation = make_transport_reservation(provider_kind="faction_pool", provider_ref=faction_ref, rider_slots=len(party_refs))
            else:
                required_freight_kg = max(1, int(sum(member_masses.values()) + 0.999))
                if max(0, int(available_capacity.get("freight_capacity_kg", 0))) < required_freight_kg:
                    raise CommandRejectedError("jianghu_transport_capacity_unavailable")
                transport_reservation = make_transport_reservation(provider_kind="faction_pool", provider_ref=faction_ref, freight_capacity_kg=required_freight_kg)


        try:
            party_speed_milli = min(party_speed_values)
            party_load_time_milli = max(load_time_values)
            plan = travel_plan(
                world_seed=str(meta.get("world_seed", "jianghu")),
                start_at=_dt(current_time),
                start=start,
                end=end,
                mode=mode,
                party_speed_milli=party_speed_milli,
                encumbrance_milli=party_load_time_milli,
            )
        except (KeyError, ValueError) as exc:
            raise CommandRejectedError("jianghu_route_unavailable") from exc

        toll = max(0, int(plan["toll_cash"]))
        cash = max(0, int(actor.get("personal_cash", 0)))
        if cash < toll:
            raise CommandRejectedError("jianghu_travel_toll_cash_insufficient")

        # Toll cash is conserved into the regional markets traversed by the party.
        geography = self.repository.read_json(_GEOGRAPHY)
        places = geography.get("places", {}) if isinstance(geography, Mapping) else {}
        staged_records: dict[str, Mapping[str, Any]] = {}
        nodes = list(plan.get("nodes", []))
        credited_toll = 0
        for i, segment in enumerate(plan.get("segments", [])):
            amount = max(0, int(segment.get("toll_cash", 0))) if isinstance(segment, Mapping) else 0
            if amount <= 0:
                continue
            if i >= len(nodes):
                raise CommandRejectedError("jianghu_travel_toll_destination_unresolved")
            place = places.get(str(nodes[i])) if isinstance(places, Mapping) else None
            region = str(place.get("climate_profile") or "") if isinstance(place, Mapping) else ""
            if not region:
                raise CommandRejectedError("jianghu_travel_toll_destination_unresolved")
            mpath = f"state/martial-world/markets/{region}.json"
            try:
                market = copy.deepcopy(staged_records.get(mpath) or self.repository.read_json(mpath))
            except FileNotFoundError as exc:
                raise CommandRejectedError("jianghu_travel_toll_destination_unresolved") from exc
            if not isinstance(market, Mapping) or market.get("region_id") not in (None, region):
                raise CommandRejectedError("jianghu_travel_toll_destination_unresolved")
            market = copy.deepcopy(dict(market))
            market["cash_pool"] = max(0, int(market.get("cash_pool", 0))) + amount
            staged_records[mpath] = market
            credited_toll += amount
        if credited_toll != toll:
            raise CommandRejectedError("jianghu_travel_toll_conservation_failure")

        try:
            actor, provision_reservation = reserve_personal_rations(
                actor, person_ref=command.actor_id, participant_count=len(party_refs),
                travel_seconds=provisioning_journey_seconds(plan),
            )
        except ValueError as exc:
            raise CommandRejectedError("jianghu_travel_rations_insufficient") from exc
        actor["personal_cash"] = cash - toll
        staged_records[rpath] = set_roster_person(copy.deepcopy(roster), ordinal, actor)

        movement_ref = f"player_travel:{command.request_id}"
        carried_cash = max(0, int(actor.get("personal_cash", 0)))
        for ref in party_refs[1:]:
            try:
                _p, _r, _o, member = self._person(ref)
                carried_cash += max(0, int(member.get("personal_cash", 0)))
            except CommandRejectedError:
                pass
        try:
            movement = build_route_journey(
                movement_ref=movement_ref,
                movement_kind="player_strategic_travel",
                purpose_ref=movement_ref,
                plan=plan,
                participants=party_refs,
                leader_ref=command.actor_id,
                beneficiary_ref=faction_ref,
                started_at=_dt(current_time),
                mode=mode,
                destination_site_ref=destination,
                extra={
                    "carried_cash_value": carried_cash,
                    "party_speed_milli": party_speed_milli,
                    "party_encumbrance_milli": party_load_time_milli,
                    "lodging_payer_kind": "person",
                    "lodging_payer_ref": command.actor_id,
                    "provision_reservation": provision_reservation,
                    **({"transport_reservation": transport_reservation} if transport_reservation else {}),
                },
            )
            route_state = self.repository.read_json(_ROUTE_OPERATIONS)
            scheduler = self.repository.read_json(_SCHEDULER)
            route_after, schedule_after = stage_route_journey(
                route_state=route_state, schedule=scheduler, movement_ref=movement_ref,
                movement=movement, now=_dt(current_time),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CommandRejectedError("jianghu_route_operation_invalid") from exc
        staged_records[_ROUTE_OPERATIONS] = route_after
        staged_records[_SCHEDULER] = schedule_after

        seconds = sum(max(1, int(x)) for x in movement.get("segment_required_seconds", []))
        time_plan, extra, settled_time = self._timed_person_activity_plan(
            command,
            meta,
            current_time,
            person_refs=party_refs,
            seconds=max(60, seconds),
            activity_ref=movement_ref,
            activity_kind="travel",
            owner_ref=faction_ref or command.actor_id,
            location_ref=str(actor.get("location_ref") or ""),
            staged_records=staged_records,
            allow_hard_interrupt=True,
            staged_activity_authority=True,
        )

        route_final = extra.get(_ROUTE_OPERATIONS)
        if not isinstance(route_final, Mapping):
            route_final = self._time_after_record(time_plan, _ROUTE_OPERATIONS, route_after)
        final_movements = route_final.get("movements", {}) if isinstance(route_final, Mapping) else {}
        active_movement = final_movements.get(movement_ref) if isinstance(final_movements, Mapping) else None
        interrupted = bool(time_plan.result.get("interrupted")) or isinstance(active_movement, Mapping)
        elapsed_seconds = max(0, int((_dt(settled_time) - _dt(current_time)).total_seconds()))

        scene = self._time_after_record(time_plan, self.scene_path, self.repository.read_json(self.scene_path))
        if isinstance(active_movement, Mapping):
            if not scene.get("active_combat_ref"):
                scene["location_id"] = str(active_movement.get("route_ref") or scene.get("location_id") or "")
            scene["present_person_ids"] = list(party_refs)
            scene["visible_person_ids"] = list(party_refs)
        else:
            scene["location_id"] = destination
            scene["present_person_ids"] = list(party_refs)
            scene["visible_person_ids"] = list(party_refs)

        result_segments=[]
        for raw_segment in plan.get("segments", []):
            if not isinstance(raw_segment, Mapping):
                continue
            weather = raw_segment.get("weather", {}) if isinstance(raw_segment.get("weather"), Mapping) else {}
            segment_result = {
                "edge_id": str(raw_segment.get("edge_id") or ""),
                "origin_place_ref": str(raw_segment.get("origin_place_ref") or ""),
                "destination_place_ref": str(raw_segment.get("destination_place_ref") or ""),
                "edge_start_milli": int(raw_segment.get("edge_start_milli", 0)),
                "edge_end_milli": int(raw_segment.get("edge_end_milli", 1000)),
                "distance_km_tenths": max(0, int(round(float(raw_segment.get("distance_km", 0)) * 10))),
                "travel_seconds": max(0, int(round(float(raw_segment.get("hours", 0)) * 3600))),
                "provisioning_seconds": max(0, int(round(float(raw_segment.get("provisioning_hours", raw_segment.get("hours", 0))) * 3600))),
                "toll_cash": max(0, int(raw_segment.get("toll_cash", 0))),
            }
            if weather.get("condition") is not None:
                segment_result["weather_condition"] = str(weather.get("condition"))
            if weather.get("ground") is not None:
                segment_result["ground"] = str(weather.get("ground"))
            result_segments.append(segment_result)

        result = {
            "command_type": command.command_type,
            "destination_site_ref": destination,
            "mode": mode,
            "distance_km_tenths": max(0, int(round(float(plan.get("distance_km", 0)) * 10))),
            "toll_cash": toll,
            "segments": result_segments,
            "travel_party_refs": list(party_refs),
            "travel_party_count": len(party_refs),
            "retinue_member_refs": list(available_retinue),
            "retinue_unavailable_refs": unavailable_retinue,
            "party_speed_milli": party_speed_milli,
            "party_encumbrance_milli": party_load_time_milli,
            "party_carried_mass_grams": {ref:max(0,int(round(mass*1000))) for ref,mass in member_masses.items()},
            "movement_ref": movement_ref,
            "elapsed_travel_seconds": elapsed_seconds,
            "interrupted": interrupted,
        }
        if isinstance(active_movement, Mapping):
            result["current_route_ref"] = str(active_movement.get("route_ref") or "")
            result["movement_status"] = str(active_movement.get("status") or "active")
        return self._combine_time_plan(
            command,
            time_plan,
            extra_records=extra,
            code="jianghu_strategic_travel_interrupted" if interrupted else "jianghu_strategic_travel_completed",
            result=result,
            scene_override=scene,
        )

    def _jianghu_combat_resolution(
        self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime
    ) -> _BuiltPlan:
        action = str(command.payload.get("action") or "")
        built = self._jianghu_combat_core_resolution(command, meta, current_time)
        if action != "exchange":
            return built
        events = [row for row in built.result.get("events", []) if isinstance(row, Mapping)] if isinstance(built.result, Mapping) else []
        if not events:
            return built
        refs: set[str] = set()
        for event in events:
            for key in ("actor_ref", "intended_ref", "actual_ref"):
                value = event.get(key)
                if isinstance(value, str) and value:
                    refs.add(value)
        people_before: dict[str, Mapping[str, Any]] = {}
        for ref in sorted(refs):
            try:
                _path, _roster, _ordinal, person = self._person(ref)
            except CommandRejectedError:
                continue
            people_before[ref] = copy.deepcopy(dict(person))
        writes = dict(built.writes)
        people_after: dict[str, Mapping[str, Any]] = {}
        for ref in sorted(refs):
            person = self._plan_person(writes, ref)
            if person is not None:
                people_after[ref] = person
        developed, summary = apply_combat_events(
            people_after,
            people_before=people_before,
            events=events,
        )
        for ref, person in developed.items():
            self._replace_person_in_plan(writes, ref, person)
        result = copy.deepcopy(dict(built.result))
        result["development_actions_counted"] = max(0, int(summary.get("actions_counted", 0)))
        return self._with_plan(built, writes=writes, result=result)


__all__ = ["JianghuDevelopmentCommandsMixin"]
