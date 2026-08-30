"""Travel for Wei and an active permanent standing field team.

A standing retinue is a persistent relationship, not a permanent time
commitment. When its leader travels, however, the active companions normally move
as one real party: every member must be available and co-located, movement uses
the slowest functional member, mounted strategic travel needs enough real
horses, and the actual traveling people are committed and moved together.
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
from shinobi_runtime.martial_world.local_travel import base_walking_speed_kph, local_travel_quote
from shinobi_runtime.martial_world.mounts import active_mount_allocations
from shinobi_runtime.martial_world.physical_presence import effective_person_presence
from shinobi_runtime.martial_world.scene_sessions import close_active_session_writes
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


def _close_scene_records(repository: Any, *, at: str) -> dict[str, Mapping[str, Any]]:
    try:
        rows = close_active_session_writes(repository.read_json, at=str(at), reason="player_left")
    except ValueError as exc:
        raise CommandRejectedError("jianghu_scene_transition_invalid") from exc
    return {str(path): copy.deepcopy(dict(row)) for path, row in rows.items()}


class JianghuTravelTeamCommandsMixin:
    def _standing_travel_party(
        self,
        *,
        leader_ref: str,
        faction_ref: str,
        start_location: str,
    ) -> tuple[list[str], dict[str, Mapping[str, Any]]]:
        deployments = self.repository.read_json(_DEPLOYMENTS)
        party_refs = active_retinue_party(
            deployments,
            leader_ref=leader_ref,
            principals=[leader_ref],
        )
        party_people: dict[str, Mapping[str, Any]] = {}
        for ref in party_refs:
            if not self._person_available_for_activity(ref):
                raise CommandRejectedError("jianghu_travel_party_member_unavailable")
            _member_path, _member_roster, _member_ordinal, member = self._person(ref)
            if str(member.get("faction_ref") or "") != faction_ref:
                raise CommandRejectedError("jianghu_travel_party_member_unavailable")
            presence = effective_person_presence(self.repository.read_json, ref, person=member)
            if str(presence.get("location_ref") or "") != start_location:
                raise CommandRejectedError("jianghu_travel_party_not_colocated")
            party_people[ref] = member
        return list(party_refs), party_people

    def _party_movement_profile(
        self,
        *,
        party_refs: list[str],
        party_people: Mapping[str, Mapping[str, Any]],
        mode: str,
        route_error_code: str,
    ) -> dict[str, Any]:
        equipment = self.repository.read_json(_EQUIPMENT)
        catalog = self.repository.read_json(_EQUIPMENT_DATA)
        speed_candidates: list[int] = []
        encumbrance_candidates: list[int] = []
        walking_candidates: list[int] = []
        total_carried_mass_kg = 0
        actor_mass_by_ref: dict[str, int] = {}
        actor_enc_by_ref: dict[str, Mapping[str, Any]] = {}
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
                raise CommandRejectedError(route_error_code) from exc
            movement_factor = max(1, int(enc["movement_factor_milli"]))
            load_time_milli = max(1000, 1_000_000 // movement_factor)
            if mode in {"horse", "pack"}:
                load_time_milli = 1000 + max(0, load_time_milli - 1000) // 2
            speed_milli = (
                max(50, 500 + mounted_stability // 2)
                if mode == "horse"
                else max(50, walking * movement_factor // 1000)
            )
            speed_candidates.append(speed_milli)
            encumbrance_candidates.append(load_time_milli)
            walking_candidates.append(max(50, walking * movement_factor // 1000))
            total_carried_mass_kg += int(mass)
            actor_mass_by_ref[ref] = int(mass)
            actor_enc_by_ref[ref] = enc
        return {
            "party_speed_milli": min(speed_candidates) if speed_candidates else 1000,
            "party_walking_milli": min(walking_candidates) if walking_candidates else 1000,
            "party_encumbrance_milli": max(encumbrance_candidates) if encumbrance_candidates else 1000,
            "total_carried_mass_kg": total_carried_mass_kg,
            "mass_by_ref": actor_mass_by_ref,
            "encumbrance_by_ref": actor_enc_by_ref,
        }

    @staticmethod
    def _move_party_in_roster(
        roster: Mapping[str, Any], *, party_refs: list[str], destination: str
    ) -> dict[str, Any]:
        out = copy.deepcopy(dict(roster))
        rows = out.get("people", [])
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
        return out

    def _jianghu_local_travel_resolution(
        self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime
    ):
        destination = str(command.payload.get("destination_site_ref") or "")
        rpath, _roster, _ordinal, person = self._person(command.actor_id)
        self._require_person_available_for_activity(command.actor_id)
        start_location = str(effective_person_presence(
            self.repository.read_json, command.actor_id, person=person
        ).get("location_ref") or "")
        if not start_location:
            raise CommandRejectedError("jianghu_person_location_invalid")
        faction_ref = str(person.get("faction_ref") or "")
        party_refs, party_people = self._standing_travel_party(
            leader_ref=command.actor_id,
            faction_ref=faction_ref,
            start_location=start_location,
        )
        profile = self._party_movement_profile(
            party_refs=party_refs,
            party_people=party_people,
            mode="foot",
            route_error_code="jianghu_local_route_invalid",
        )
        walking_milli = max(50, int(profile["party_walking_milli"]))
        try:
            quote = local_travel_quote(
                start_site_ref=start_location,
                end_site_ref=destination,
                walking_speed_kph=base_walking_speed_kph() * walking_milli / 1000.0,
            )
        except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
            raise CommandRejectedError("jianghu_local_route_invalid") from exc
        quote = {
            **quote,
            "walking_capacity_milli": walking_milli,
            "travel_party_refs": list(party_refs),
            "travel_party_count": len(party_refs),
        }
        time_plan, extra, _target = self._timed_person_activity_plan(
            command,
            meta,
            current_time,
            person_refs=party_refs,
            seconds=max(60, int(quote["walking_minutes"]) * 60),
            activity_ref=f"local-travel:{command.request_id}",
            activity_kind="travel",
            owner_ref=faction_ref or command.actor_id,
            location_ref=start_location,
            staged_records=_close_scene_records(self.repository, at=str(current_time)),
        )
        extra[rpath] = self._move_party_in_roster(
            extra[rpath], party_refs=party_refs, destination=destination
        )
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
            scene_override=scene,
            code="jianghu_local_travel_ready",
            result={
                "command_type": command.command_type,
                "from_site_ref": start_location,
                "destination_site_ref": destination,
                **quote,
            },
        )



__all__ = ["JianghuTravelTeamCommandsMixin"]
