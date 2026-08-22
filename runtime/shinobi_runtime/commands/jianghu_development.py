"""Command-surface integration for field and combat development.

Field development is part of the same conserved command transaction as travel
and combat. A standing retinue is a zero-time identity owner while idle, but
available members automatically join Wei's strategic travel, consume the same
finite travel interval, pause institutional training for those hours, move to
the same destination and gain only the field experience they actually lived.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _json_bytes
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.martial_world.equipment import carried_mass_kg, encumbrance_effects
from shinobi_runtime.martial_world.equipment_state import effective_person_loadout
from shinobi_runtime.martial_world.field_development import apply_combat_events, apply_field_activity
from shinobi_runtime.martial_world.faction_state import inventory_path as canonical_inventory_path
from shinobi_runtime.martial_world.health import functional_capacity_factors
from shinobi_runtime.martial_world.live_state import roster_person, set_roster_person
from shinobi_runtime.martial_world.mounts import active_mount_allocations
from shinobi_runtime.martial_world.travel import travel_plan
from shinobi_runtime.sim.events import CampaignTime

_DEPLOYMENTS = "state/martial-world/deployments.json"
_LOCAL_SITES = "game/data/martial-world/local-sites.json"
_GEOGRAPHY = "game/data/martial-world/geography.json"
_EQUIPMENT = "state/martial-world/equipment-ledger.json"
_EQUIPMENT_DATA = "game/data/martial-world/equipment.json"
_COMBATS = "state/martial-world/combats.json"


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

    def _standing_retinue_member_refs(self, actor_ref: str) -> list[str]:
        try:
            state = self.repository.read_json(_DEPLOYMENTS)
        except FileNotFoundError:
            return []
        rows = state.get("deployments", {}) if isinstance(state, Mapping) else {}
        if not isinstance(rows, Mapping):
            return []
        refs: list[str] = []
        for retinue_ref in sorted(str(ref) for ref in rows if isinstance(ref, str)):
            row = rows.get(retinue_ref)
            if not isinstance(row, Mapping):
                continue
            if row.get("operation_kind") != "standing_retinue" or row.get("status") != "active":
                continue
            if row.get("leader_ref") != actor_ref:
                continue
            members = row.get("member_refs", [])
            if isinstance(members, list):
                refs.extend(str(ref) for ref in members if isinstance(ref, str) and ref)
        return list(dict.fromkeys(refs))

    def _jianghu_strategic_travel_resolution(
        self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime
    ) -> _BuiltPlan:
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
        retinue_refs = self._standing_retinue_member_refs(command.actor_id)
        available_retinue: list[str] = []
        unavailable_retinue: list[str] = []
        for ref in retinue_refs:
            try:
                _p, _r, _o, member = self._person(ref)
            except CommandRejectedError:
                unavailable_retinue.append(ref)
                continue
            if str(member.get("faction_ref") or "") != faction_ref or not self._person_available_for_activity(ref):
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
        movement_capacity: dict[str, int] = {}

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
            movement_capacity[ref] = function
            party_speed_values.append(speed)
            load_time_values.append(load_time)

        # A functionally unable retinue member does not teleport with the party.
        party_refs = [ref for ref in party_refs if ref in member_masses]
        available_retinue = [ref for ref in available_retinue if ref in member_masses]
        unavailable_retinue = list(dict.fromkeys(unavailable_retinue))
        if not party_refs or party_refs[0] != command.actor_id:
            raise CommandRejectedError("jianghu_travel_function_unavailable")

        if mode in {"horse", "pack"}:
            inv = self.repository.read_json(canonical_inventory_path(faction_ref))
            key = "riding_horses" if mode == "horse" else "pack_animals"
            stock = max(0, int(inv.get("transport_assets", {}).get(key, 0)))
            if mode == "horse":
                stock -= active_mount_allocations(
                    self.repository.read_json(_COMBATS), faction_ref=faction_ref
                )
                if stock < len(party_refs):
                    raise CommandRejectedError("jianghu_transport_asset_unavailable")
            elif stock <= 0:
                raise CommandRejectedError("jianghu_transport_asset_unavailable")

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

        # Toll cash is conserved into the regional markets traversed by the
        # party instead of disappearing from the economy.
        geography = self.repository.read_json(_GEOGRAPHY)
        places = geography.get("places", {}) if isinstance(geography, Mapping) else {}
        staged_records: dict[str, Mapping[str, Any]] = {}
        nodes = list(plan.get("nodes", []))
        for i, segment in enumerate(plan.get("segments", [])):
            amount = max(0, int(segment.get("toll_cash", 0))) if isinstance(segment, Mapping) else 0
            if amount <= 0 or i >= len(nodes):
                continue
            place = places.get(str(nodes[i])) if isinstance(places, Mapping) else None
            region = str(place.get("climate_profile") or "") if isinstance(place, Mapping) else ""
            if not region:
                continue
            mpath = f"state/martial-world/markets/{region}.json"
            market = copy.deepcopy(staged_records.get(mpath) or self.repository.read_json(mpath))
            market["cash_pool"] = max(0, int(market.get("cash_pool", 0))) + amount
            staged_records[mpath] = market

        actor["personal_cash"] = cash - toll
        staged_roster = set_roster_person(copy.deepcopy(roster), ordinal, actor)
        staged_records[rpath] = staged_roster
        seconds = max(60, int(round(float(plan["travel_hours"]) * 3600)))
        time_plan, extra, _target = self._timed_person_activity_plan(
            command,
            meta,
            current_time,
            person_refs=party_refs,
            seconds=seconds,
            activity_ref=f"strategic-travel:{command.request_id}",
            activity_kind="travel",
            owner_ref=faction_ref or command.actor_id,
            location_ref=str(actor.get("location_ref") or ""),
            staged_records=staged_records,
        )

        final_roster = copy.deepcopy(dict(extra[rpath]))
        rows = final_roster.get("people", [])
        if not isinstance(rows, list):
            raise CommandRejectedError("jianghu_roster_invalid")
        party_set = set(party_refs)
        found: set[str] = set()
        hours_milli = max(0, int(round(float(plan["travel_hours"]) * 1000)))
        field_summary: dict[str, Any] = {}
        for i, row in enumerate(rows):
            if not isinstance(row, Mapping):
                continue
            ref = str(row.get("person_id") or "")
            if ref not in party_set:
                continue
            developed, summary = apply_field_activity(
                row,
                duration_hours_milli=hours_milli,
                activity_kind="road_travel",
                leader=ref == command.actor_id,
                pressure_milli=650,
            )
            developed["location_ref"] = destination
            rows[i] = developed
            field_summary[ref] = summary
            found.add(ref)
        if found != party_set:
            raise CommandRejectedError("jianghu_person_unresolved")
        extra[rpath] = final_roster

        scene = self._time_after_record(time_plan, self.scene_path, self.repository.read_json(self.scene_path))
        scene["location_id"] = destination
        scene["present_person_ids"] = list(party_refs)
        scene["visible_person_ids"] = list(party_refs)
        result = {
            "command_type": command.command_type,
            "destination_site_ref": destination,
            "mode": mode,
            "distance_km": plan["distance_km"],
            "toll_cash": toll,
            "segments": plan["segments"],
            "party_refs": list(party_refs),
            "retinue_member_refs": list(available_retinue),
            "retinue_unavailable_refs": unavailable_retinue,
            "party_speed_milli": party_speed_milli,
            "party_encumbrance_milli": party_load_time_milli,
            "party_carried_mass_kg": member_masses,
            "field_development": field_summary,
        }
        return self._combine_time_plan(
            command,
            time_plan,
            extra_records=extra,
            code="jianghu_strategic_travel_completed",
            result=result,
            scene_override=scene,
        )

    def _jianghu_combat_resolution(
        self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime
    ) -> _BuiltPlan:
        action = str(command.payload.get("action") or "")
        built = super()._jianghu_combat_resolution(command, meta, current_time)
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
            except Exception:
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
