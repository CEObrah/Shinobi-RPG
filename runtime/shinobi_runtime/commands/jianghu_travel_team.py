"""Strategic travel for Wei and an active permanent standing travel team.

A standing retinue is a persistent relationship, not a permanent time
commitment. When its leader undertakes strategic travel, however, the active
three-person team travels as one real party: every member must be available and
co-located, party movement uses the slowest functional member, mounted travel
needs enough real horses, and all four people are committed and moved together.
"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.martial_world.equipment import carried_mass_kg, encumbrance_effects
from shinobi_runtime.martial_world.equipment_state import effective_person_loadout
from shinobi_runtime.martial_world.escort import active_retinue_party
from shinobi_runtime.martial_world.faction_state import inventory_path as canonical_inventory_path
from shinobi_runtime.martial_world.health import functional_capacity_factors
from shinobi_runtime.martial_world.mounts import active_mount_allocations
from shinobi_runtime.martial_world.travel import travel_plan
from shinobi_runtime.sim.events import CampaignTime

_EQUIPMENT = "state/martial-world/equipment-ledger.json"
_COMBATS = "state/martial-world/combats.json"
_DEPLOYMENTS = "state/martial-world/deployments.json"
_LOCAL_SITES = "game/data/martial-world/local-sites.json"
_GEOGRAPHY = "game/data/martial-world/geography.json"
_EQUIPMENT_DATA = "game/data/martial-world/equipment.json"


def _dt(time: CampaignTime) -> datetime:
    return datetime(time.year, time.month, time.day, time.hour, time.minute, time.second)


def _inventory_path(faction_ref: str) -> str:
    return canonical_inventory_path(faction_ref)


class JianghuTravelTeamCommandsMixin:
    def _jianghu_strategic_travel_resolution(
        self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime
    ):
        destination = str(command.payload.get("destination_site_ref"))
        mode = str(command.payload.get("mode"))
        if mode not in {"foot", "horse", "pack"}:
            raise CommandRejectedError("jianghu_travel_mode_invalid")

        rpath, roster, ordinal, person = self._person(command.actor_id)
        self._require_person_available_for_activity(command.actor_id)
        sites_data = self.repository.read_json(_LOCAL_SITES)
        sites = sites_data.get("sites", {}) if isinstance(sites_data, Mapping) else {}
        start_site = sites.get(person.get("location_ref")) if isinstance(sites, Mapping) else None
        end_site = sites.get(destination) if isinstance(sites, Mapping) else None
        if not isinstance(start_site, Mapping) or not isinstance(end_site, Mapping):
            raise CommandRejectedError("jianghu_travel_site_unresolved")
        start = str(start_site.get("parent_place_ref"))
        end = str(end_site.get("parent_place_ref"))
        if start == end:
            raise CommandRejectedError("jianghu_use_local_travel")

        faction_ref = str(person.get("faction_ref") or "")
        deployments = self.repository.read_json(_DEPLOYMENTS)
        party_refs = active_retinue_party(
            deployments,
            leader_ref=command.actor_id,
            principals=[command.actor_id],
        )
        party_people: dict[str, Mapping[str, Any]] = {}
        start_location = str(person.get("location_ref") or "")
        for ref in party_refs:
            if not self._person_available_for_activity(ref):
                raise CommandRejectedError("jianghu_travel_party_member_unavailable")
            _member_path, _member_roster, _member_ordinal, member = self._person(ref)
            if str(member.get("faction_ref") or "") != faction_ref:
                raise CommandRejectedError("jianghu_travel_party_member_unavailable")
            if str(member.get("location_ref") or "") != start_location:
                raise CommandRejectedError("jianghu_travel_party_not_colocated")
            party_people[ref] = member

        if mode in {"horse", "pack"}:
            inv = self.repository.read_json(_inventory_path(faction_ref))
            key = "riding_horses" if mode == "horse" else "pack_animals"
            stock = int(inv.get("transport_assets", {}).get(key, 0))
            if mode == "horse":
                combat_state = self.repository.read_json(_COMBATS)
                stock -= active_mount_allocations(combat_state, faction_ref=faction_ref)
                if stock < len(party_refs):
                    raise CommandRejectedError("jianghu_transport_asset_unavailable")
            elif stock <= 0:
                raise CommandRejectedError("jianghu_transport_asset_unavailable")

        equipment = self.repository.read_json(_EQUIPMENT)
        catalog = self.repository.read_json(_EQUIPMENT_DATA)
        party_speed_candidates: list[int] = []
        party_encumbrance_candidates: list[int] = []
        total_carried_mass_kg = 0
        actor_mass = 0
        actor_enc: Mapping[str, Any] = {}
        for ref in party_refs:
            member = party_people[ref]
            health = member.get("health", {}) if isinstance(member.get("health"), Mapping) else {}
            wounds = health.get("injuries", []) if isinstance(health.get("injuries"), list) else []
            body = functional_capacity_factors([row for row in wounds if isinstance(row, Mapping)])
            walking = max(0, min(1000, int(body.get("walking_milli", 1000))))
            mounted_stability = max(0, min(1000, int(body.get("mounted_stability_milli", 1000))))
            movement_function = mounted_stability if mode == "horse" else walking
            if movement_function <= 0:
                raise CommandRejectedError("jianghu_travel_function_unavailable")
            try:
                loadout = effective_person_loadout(equipment, ref)
                mass = carried_mass_kg(loadout.get("items", {}), catalog)
                attrs = member.get("attributes", {}) if isinstance(member.get("attributes"), Mapping) else {}
                enc = encumbrance_effects(
                    total_mass_kg=mass,
                    strength=int(attrs.get("strength", 0)),
                    endurance=int(attrs.get("endurance", 0)),
                )
            except (KeyError, ValueError) as exc:
                raise CommandRejectedError("jianghu_route_unavailable") from exc
            load_time_milli = max(1000, 1_000_000 // max(1, int(enc["movement_factor_milli"])))
            if mode in {"horse", "pack"}:
                load_time_milli = 1000 + max(0, load_time_milli - 1000) // 2
            speed_milli = (
                max(50, 500 + mounted_stability // 2)
                if mode == "horse"
                else max(50, walking)
            )
            party_speed_candidates.append(speed_milli)
            party_encumbrance_candidates.append(load_time_milli)
            total_carried_mass_kg += int(mass)
            if ref == command.actor_id:
                actor_mass = int(mass)
                actor_enc = enc

        party_speed_milli = min(party_speed_candidates) if party_speed_candidates else 1000
        party_encumbrance_milli = max(party_encumbrance_candidates) if party_encumbrance_candidates else 1000
        try:
            plan = travel_plan(
                world_seed=str(meta.get("world_seed", "jianghu")),
                start_at=_dt(current_time),
                start=start,
                end=end,
                mode=mode,
                party_speed_milli=party_speed_milli,
                encumbrance_milli=party_encumbrance_milli,
            )
            plan = {
                **plan,
                "party_speed_milli": party_speed_milli,
                "party_encumbrance_milli": party_encumbrance_milli,
            }
        except (KeyError, ValueError) as exc:
            raise CommandRejectedError("jianghu_route_unavailable") from exc

        toll = int(plan["toll_cash"])
        cash = int(person.get("personal_cash", 0))
        if cash < toll:
            raise CommandRejectedError("jianghu_travel_toll_cash_insufficient")

        # Toll cash is conserved into the regional market owners traversed by
        # the route instead of disappearing from the economy.
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

        person["personal_cash"] = cash - toll
        staged_roster = copy.deepcopy(roster)
        staged_people = staged_roster.get("people", [])
        if not isinstance(staged_people, list):
            raise CommandRejectedError("jianghu_roster_invalid")
        raw_actor = staged_people[ordinal]
        if not isinstance(raw_actor, Mapping) or raw_actor.get("person_id") != command.actor_id:
            raise CommandRejectedError("jianghu_person_unresolved")
        # Preserve the actor's cash update through the existing canonical person
        # setter while the other travel-team members need no pre-travel mutation.
        from shinobi_runtime.martial_world.live_state import set_roster_person
        staged_records[rpath] = set_roster_person(staged_roster, ordinal, person)

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
            location_ref=start_location,
            staged_records=staged_records,
        )

        final_roster = copy.deepcopy(dict(extra[rpath]))
        rows = final_roster.get("people", [])
        if not isinstance(rows, list):
            raise CommandRejectedError("jianghu_roster_invalid")
        remaining = set(party_refs)
        for i, row in enumerate(rows):
            if not isinstance(row, Mapping):
                continue
            ref = row.get("person_id")
            if ref not in remaining:
                continue
            updated = copy.deepcopy(dict(row))
            updated["location_ref"] = destination
            rows[i] = updated
            remaining.remove(str(ref))
        if remaining:
            raise CommandRejectedError("jianghu_person_unresolved")
        extra[rpath] = final_roster

        scene = self._time_after_record(
            time_plan, self.scene_path, self.repository.read_json(self.scene_path)
        )
        scene["location_id"] = destination
        scene["present_person_ids"] = list(party_refs)
        scene["visible_person_ids"] = list(party_refs)
        return self._combine_time_plan(
            command,
            time_plan,
            extra_records=extra,
            code="jianghu_strategic_travel_completed",
            result={
                "command_type": command.command_type,
                "destination_site_ref": destination,
                "mode": mode,
                "distance_km": plan["distance_km"],
                "toll_cash": toll,
                "segments": plan["segments"],
                "travel_party_refs": list(party_refs),
                "travel_party_count": len(party_refs),
                "party_speed_milli": party_speed_milli,
                "party_encumbrance_milli": party_encumbrance_milli,
                "total_carried_mass_kg": total_carried_mass_kg,
                "carried_mass_kg": actor_mass,
                "encumbrance": actor_enc,
            },
            scene_override=scene,
        )


__all__ = ["JianghuTravelTeamCommandsMixin"]
