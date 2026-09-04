"""Conserved player-facing faction foundation and institutional transitions."""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Mapping, Sequence

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.martial_world.faction_existence import (
    mark_faction_extinct, register_materialized_faction_bundle,
)
from shinobi_runtime.martial_world.institutional_evolution_frontier import (
    default_dynamic_outlaw_profile, default_founder_admission_policy,
    default_founder_autonomy_policy, founder_curriculum, founder_recruitment_policy,
)
from shinobi_runtime.martial_world.faction_registry import register_faction, unregister_faction
from shinobi_runtime.martial_world.faction_state import (
    compact_faction_state, faction_path, inventory_path, read_faction, roster_path,
)
from shinobi_runtime.martial_world.inventory_state import compact_inventory_state, hydrate_inventory_state
from shinobi_runtime.martial_world.person_state import compact_person_state, compact_roster_state, hydrate_roster_state, reconcile_faction_population
from shinobi_runtime.martial_world.independent_people import compact_independent_person, hydrate_independent_person
from shinobi_runtime.martial_world.scheduler import sync_faction_activity
from shinobi_runtime.martial_world.site_control import active_site_controller
from shinobi_runtime.martial_world.property import detach_faction_policy_holders, transfer_faction_property_authority
from shinobi_runtime.martial_world.institutional_obligations import (
    faction_retirement_blockers, member_transition_blockers,
)
from shinobi_runtime.martial_world.faction_transitions import (
    primary_estate_projection, reconcile_family_transition, retire_faction_relations, retire_organizational_scale, transfer_holdings, transfer_inventory,
)

_REGISTRY = "state/martial-world/faction-registry.json"
_RELATIONS = "state/martial-world/faction-relations.json"
_FAMILY = "state/martial-world/family.json"
_INDEPENDENTS = "state/martial-world/independent-people.json"
_SCHEDULER = "state/martial-world/scheduler.json"
_SOCIAL = "state/martial-world/social.json"
_LOCAL_SITES = "game/data/martial-world/local-sites.json"
_EQUIPMENT = "state/martial-world/equipment-ledger.json"
_DYNAMIC_TYPE_ALIASES = {
    "house": "martial_house", "school": "martial_school", "society": "brotherhood_society",
    "escort": "escort_agency", "outlaw": "outlaw_faction",
}
_DYNAMIC_TYPES = {
    "martial_house", "sect", "martial_school", "escort_agency",
    "brotherhood_society", "outlaw_faction", "contract_hall",
}


def _dt(time: CampaignTime) -> datetime:
    return datetime(time.year, time.month, time.day, time.hour, time.minute, time.second)


def _alive(person: Mapping[str, Any]) -> bool:
    health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
    return health.get("status") != "dead"


def _is_leader(person: Mapping[str, Any]) -> bool:
    return "leader" in {str(x).split(":", 1)[0] for x in person.get("standing_offices", []) if isinstance(x, str)}


def _relation_trust(social: Mapping[str, Any], source_ref: str, target_ref: str) -> int:
    rels = social.get("relationships", {}) if isinstance(social, Mapping) else {}
    row = rels.get(f"{source_ref}|{target_ref}") if isinstance(rels, Mapping) else None
    return int(row.get("trust", 0)) if isinstance(row, Mapping) else 0


def _institutional_people(rows: Sequence[Mapping[str, Any]], *, leader_ref: str) -> list[dict[str, Any]]:
    out = []
    for raw in rows:
        person = copy.deepcopy(dict(raw))
        person.pop("former_faction_ref", None); person.pop("independent_since", None); person.pop("faction_ref", None)
        person["membership_grade"] = str(person.get("membership_grade") or "full")
        person["standing_offices"] = ["leader"] if person.get("person_id") == leader_ref else []
        out.append(person)
    return out


def _dynamic_faction_type(value: Any, *, fallback: str = "martial_school") -> str:
    raw = str(value or fallback)
    resolved = _DYNAMIC_TYPE_ALIASES.get(raw, raw)
    if resolved not in _DYNAMIC_TYPES:
        raise CommandRejectedError("jianghu_faction_type_invalid")
    return resolved


class JianghuFactionLifecycleCommandsMixin:
    def _jianghu_faction_lifecycle_resolution(self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime):
        action = str(command.payload.get("action") or "")
        registry = copy.deepcopy(self.repository.read_json(_REGISTRY)); active = set(registry.get("faction_refs", []))
        relations = copy.deepcopy(self.repository.read_json(_RELATIONS)); family = copy.deepcopy(self.repository.read_json(_FAMILY))
        schedule = copy.deepcopy(self.repository.read_json(_SCHEDULER)); at_iso = str(current_time).removeprefix("SE-")
        writes: dict[str, Any] = {}

        def finish(code: str, result: Mapping[str, Any], changed_registry: Mapping[str, Any], changed_relations: Mapping[str, Any] | None = None):
            refs = changed_registry.get("faction_refs", []) if isinstance(changed_registry, Mapping) else []
            writes[_REGISTRY] = changed_registry
            writes[_SCHEDULER] = sync_faction_activity(schedule, faction_ids=[str(x) for x in refs if isinstance(x, str)], now=_dt(current_time))
            if changed_relations is not None:
                writes[_RELATIONS] = changed_relations
            writes[_FAMILY] = family
            return self._simple_plan(command, meta, current_time, writes_records=writes, code=code, result={"command_type":command.command_type, **dict(result)})

        if action == "found":
            new_ref = str(command.payload.get("new_faction_ref") or "")
            if not new_ref.startswith("faction.") or new_ref in active:
                raise CommandRejectedError("jianghu_faction_new_ref_invalid")
            try:
                self.repository.read_json(faction_path(new_ref))
            except FileNotFoundError:
                pass
            else:
                raise CommandRejectedError("jianghu_faction_new_ref_exists")
            independent = copy.deepcopy(self.repository.read_json(_INDEPENDENTS)); rows = independent.get("people", [])
            if not isinstance(rows, list): raise CommandRejectedError("jianghu_independent_people_invalid")
            hydrated = [hydrate_independent_person(row) for row in rows if isinstance(row, Mapping)]
            by_ref = {str(row.get("person_id")): row for row in hydrated if isinstance(row.get("person_id"), str)}
            member_refs = [str(x) for x in command.payload.get("member_refs", []) if isinstance(x, str)]
            if not member_refs or len(set(member_refs)) != len(member_refs) or command.actor_id not in member_refs or any(ref not in by_ref or not _alive(by_ref[ref]) for ref in member_refs):
                raise CommandRejectedError("jianghu_faction_founders_invalid")
            social = self.repository.read_json(_SOCIAL)
            if any(ref != command.actor_id and _relation_trust(social, ref, command.actor_id) < 40 for ref in member_refs):
                raise CommandRejectedError("jianghu_faction_founder_consent_insufficient")
            if member_transition_blockers(self.repository.read_json, member_refs):
                raise CommandRejectedError("jianghu_faction_founder_activity_active")
            actor = by_ref[command.actor_id]
            cash = max(0, int(command.payload.get("startup_cash", 0))); rations = max(0, int(command.payload.get("startup_ration_days", 0)))
            if cash > max(0, int(actor.get("personal_cash", 0))) or rations > max(0, int(actor.get("travel_ration_days", 0))):
                raise CommandRejectedError("jianghu_faction_startup_resources_insufficient")
            sites = self.repository.read_json(_LOCAL_SITES).get("sites", {}); site_ref = str(command.payload.get("headquarters_site_ref") or "")
            place_ref = str(command.payload.get("headquarters_place_ref") or "")
            site = sites.get(site_ref) if isinstance(sites, Mapping) else None
            if not isinstance(site, Mapping) or str(site.get("parent_place_ref") or "") != place_ref or self._effective_person_location(command.actor_id, actor) != site_ref:
                raise CommandRejectedError("jianghu_faction_foundation_site_invalid")
            if any(self._effective_person_location(ref, by_ref[ref]) != site_ref for ref in member_refs):
                raise CommandRejectedError("jianghu_faction_founders_not_colocated")
            try:
                live_controller = active_site_controller(self.repository.read_json, site_ref)
            except ValueError as exc:
                raise CommandRejectedError("jianghu_faction_site_control_invalid") from exc
            if live_controller:
                raise CommandRejectedError("jianghu_faction_foundation_site_already_owned")
            by_ref[command.actor_id]["personal_cash"] = max(0, int(actor.get("personal_cash", 0))) - cash
            by_ref[command.actor_id]["travel_ration_days"] = max(0, int(actor.get("travel_ration_days", 0))) - rations
            moved = _institutional_people([by_ref[ref] for ref in member_refs], leader_ref=command.actor_id)
            independent["people"] = [compact_independent_person(row) for row in hydrated if str(row.get("person_id")) not in set(member_refs)]
            faction_type = _dynamic_faction_type(command.payload.get("faction_type"))
            faction = {
                "schema":"jianghu-faction-state-1.0", "faction_id":new_ref,
                "name":str(command.payload.get("name") or new_ref), "type":faction_type,
                "headquarters":place_ref, "local_site_ref":site_ref, "treasury_cash":cash, "buildings":{}, "enterprises":{},
                "training":founder_curriculum(moved),
                "recruitment_policy":founder_recruitment_policy(moved),
                "admission_policy":default_founder_admission_policy(),
                "autonomy_policy":default_founder_autonomy_policy(),
                "training_epoch":{"started_at":at_iso,"settled_through":at_iso,"intensity_milli":1000},
            }
            tenure = command.payload.get("membership_tenure"); camp = command.payload.get("jianghu_camp")
            if isinstance(tenure, str) and tenure: faction["membership_tenure"] = tenure
            if isinstance(camp, str) and camp: faction["jianghu_camp"] = camp
            if faction_type == "outlaw_faction":
                faction.update(default_dynamic_outlaw_profile(place_ref=place_ref, site_type=str(site.get("site_type") or "")))
            roster = {"schema":"jianghu-person-lite-roster-1.0","faction_ref":new_ref,"people":moved}
            faction = reconcile_faction_population(faction, roster)
            inventory = {"schema":"jianghu-faction-inventory-1.0","faction_ref":new_ref,"food_ration_days":rations}
            registry = register_materialized_faction_bundle(registry=registry, faction=faction, roster=roster, inventory=inventory)
            writes[_INDEPENDENTS] = independent; writes[faction_path(new_ref)] = compact_faction_state(faction)
            writes[roster_path(new_ref)] = compact_roster_state(roster, faction=faction); writes[inventory_path(new_ref)] = compact_inventory_state(inventory)
            family = reconcile_family_transition(family, moved_refs=member_refs, source_faction_ref="", target_faction_ref=new_ref)
            return finish("jianghu_faction_founded", {"action":action,"faction_ref":new_ref,"member_refs":sorted(member_refs),"startup_cash":cash,"startup_ration_days":rations}, registry)

        source_ref = str(command.payload.get("source_faction_ref") or "")
        if source_ref not in active:
            raise CommandRejectedError("jianghu_faction_source_inactive")
        spath, source = read_faction(self.repository, source_ref); srpath = roster_path(source_ref)
        source_roster = hydrate_roster_state(self.repository.read_json(srpath), faction=source)
        source_people = [copy.deepcopy(dict(row)) for row in source_roster.get("people", []) if isinstance(row, Mapping)]
        actor = next((row for row in source_people if row.get("person_id") == command.actor_id and _alive(row)), None)
        if not isinstance(actor, Mapping) or not _is_leader(actor):
            raise CommandRejectedError("jianghu_faction_lifecycle_not_authorized")

        if action == "split":
            new_ref = str(command.payload.get("new_faction_ref") or "")
            if not new_ref.startswith("faction.") or new_ref in active:
                raise CommandRejectedError("jianghu_faction_new_ref_invalid")
            member_refs = [str(x) for x in command.payload.get("member_refs", []) if isinstance(x, str)]
            moved_set = set(member_refs)
            living = {str(row.get("person_id")): row for row in source_people if _alive(row) and isinstance(row.get("person_id"), str)}
            if command.actor_id not in moved_set or not moved_set or len(moved_set) != len(member_refs) or any(ref not in living for ref in moved_set):
                raise CommandRejectedError("jianghu_faction_split_members_invalid")
            if not any(ref not in moved_set for ref in living):
                raise CommandRejectedError("jianghu_faction_split_requires_surviving_source")
            social = self.repository.read_json(_SOCIAL)
            if any(ref != command.actor_id and _relation_trust(social, ref, command.actor_id) < 30 for ref in moved_set):
                raise CommandRejectedError("jianghu_faction_split_consent_insufficient")
            cash = max(0, int(command.payload.get("treasury_cash", 0)))
            if cash > max(0, int(source.get("treasury_cash", 0))):
                raise CommandRejectedError("jianghu_faction_split_treasury_insufficient")
            site_ref = str(command.payload.get("headquarters_site_ref") or ""); place_ref = str(command.payload.get("headquarters_place_ref") or "")
            sites = self.repository.read_json(_LOCAL_SITES).get("sites", {}); site = sites.get(site_ref) if isinstance(sites, Mapping) else None
            if not isinstance(site, Mapping) or str(site.get("parent_place_ref") or "") != place_ref or self._effective_person_location(command.actor_id, actor) != site_ref:
                raise CommandRejectedError("jianghu_faction_split_site_invalid")
            if site_ref == str(source.get("local_site_ref") or ""):
                raise CommandRejectedError("jianghu_faction_split_cannot_duplicate_primary_estate")
            requested_estates = [str(x) for x in (command.payload.get("estate_site_refs") or []) if isinstance(x, str)]
            if len(set(requested_estates)) != len(requested_estates):
                raise CommandRejectedError("jianghu_faction_split_estate_refs_invalid")
            source_controlled = source.get("controlled_estates", {}) if isinstance(source.get("controlled_estates"), Mapping) else {}
            if any(ref not in source_controlled for ref in requested_estates):
                raise CommandRejectedError("jianghu_faction_split_estate_not_controlled")
            moving_sites = set(requested_estates)
            if isinstance(source_controlled.get(site_ref), Mapping):
                moving_sites.add(site_ref)
            if member_transition_blockers(
                self.repository.read_json, member_refs, source_faction_ref=source_ref,
                moving_site_refs=sorted(moving_sites),
            ):
                raise CommandRejectedError("jianghu_faction_split_activity_active")
            try:
                live_controller = active_site_controller(self.repository.read_json, site_ref)
            except ValueError as exc:
                raise CommandRejectedError("jianghu_faction_site_control_invalid") from exc
            if live_controller and live_controller != source_ref:
                raise CommandRejectedError("jianghu_faction_split_site_already_owned")
            sipath = inventory_path(source_ref); source_inv = hydrate_inventory_state(self.repository.read_json(sipath))
            new_inv = {"schema":"jianghu-faction-inventory-1.0","faction_ref":new_ref,"food_ration_days":0}
            try:
                source_inv, new_inv, moved_inventory = transfer_inventory(
                    source_inv, new_inv, food_ration_days=max(0,int(command.payload.get("food_ration_days",0))),
                    requested=command.payload.get("inventory_transfer") if isinstance(command.payload.get("inventory_transfer"), Mapping) else {},
                )
            except ValueError as exc:
                raise CommandRejectedError("jianghu_faction_split_inventory_insufficient") from exc
            source["treasury_cash"] = max(0, int(source.get("treasury_cash",0))) - cash
            moved_people = _institutional_people([living[ref] for ref in member_refs], leader_ref=command.actor_id)
            source_roster["people"] = [row for row in source_people if str(row.get("person_id")) not in moved_set]
            source = reconcile_faction_population(source, source_roster)
            # If the breakaway group establishes itself at one of the parent
            # faction's real controlled estates, that estate becomes the new
            # primary compound rather than remaining a phantom secondary asset
            # of the parent. Other explicitly requested estates remain secondary
            # controlled estates of the new institution.
            hq_estate = source_controlled.get(site_ref) if isinstance(source_controlled.get(site_ref), Mapping) else None
            selected_estates = set(requested_estates)
            if hq_estate is not None:
                selected_estates.add(site_ref)
            new_type = _dynamic_faction_type(command.payload.get("faction_type"), fallback=str(source.get("type") or "martial_school"))
            new_faction = {
                "schema":"jianghu-faction-state-1.0","faction_id":new_ref,"name":str(command.payload.get("name") or new_ref),
                "type":new_type,"headquarters":place_ref,"local_site_ref":site_ref,
                "treasury_cash":cash,
                "buildings":copy.deepcopy(dict(hq_estate.get("buildings",{}))) if isinstance(hq_estate,Mapping) and isinstance(hq_estate.get("buildings"),Mapping) else {},
                "infrastructure":copy.deepcopy(dict(hq_estate.get("infrastructure",{}))) if isinstance(hq_estate,Mapping) and isinstance(hq_estate.get("infrastructure"),Mapping) else {},
                "enterprises":copy.deepcopy(dict(hq_estate.get("enterprises",{}))) if isinstance(hq_estate,Mapping) and isinstance(hq_estate.get("enterprises"),Mapping) else {},
                "training_epoch":{"started_at":at_iso,"settled_through":at_iso,"intensity_milli":1000},
                "membership_tenure":str(command.payload.get("membership_tenure") or source.get("membership_tenure") or "voluntary"),
            }
            for inherited_key in ("training", "doctrine", "recruitment_policy", "autonomy_policy"):
                inherited = source.get(inherited_key)
                if isinstance(inherited, Mapping) and inherited:
                    new_faction[inherited_key] = copy.deepcopy(dict(inherited))
            from shinobi_runtime.martial_world.faction_state import faction_admission_policy
            new_faction["admission_policy"] = faction_admission_policy(source_ref, source)
            if new_type == "outlaw_faction":
                outlaw_profile = default_dynamic_outlaw_profile(place_ref=place_ref, site_type=str(site.get("site_type") or ""))
                if isinstance(source.get("outlaw_subtype"), str) and source.get("outlaw_subtype"):
                    outlaw_profile["outlaw_subtype"] = str(source["outlaw_subtype"])
                if isinstance(source.get("outlaw_policy"), Mapping):
                    outlaw_profile["outlaw_policy"] = copy.deepcopy(dict(source["outlaw_policy"]))
                new_faction.update(outlaw_profile)
            camp = str(command.payload.get("jianghu_camp") or source.get("jianghu_camp") or "")
            if camp: new_faction["jianghu_camp"] = camp
            secondary = {ref: copy.deepcopy(dict(source_controlled[ref])) for ref in sorted(selected_estates) if ref != site_ref and isinstance(source_controlled.get(ref),Mapping)}
            if secondary: new_faction["controlled_estates"] = secondary
            if selected_estates:
                remaining_controlled = {ref: copy.deepcopy(dict(row)) for ref, row in source_controlled.items() if ref not in selected_estates and isinstance(row, Mapping)}
                if remaining_controlled: source["controlled_estates"] = remaining_controlled
                else: source.pop("controlled_estates", None)
                source_conditions = source.get("site_conditions",{}) if isinstance(source.get("site_conditions"),Mapping) else {}
                moved_conditions = {ref: copy.deepcopy(dict(source_conditions[ref])) for ref in sorted(selected_estates) if isinstance(source_conditions.get(ref),Mapping)}
                if moved_conditions: new_faction["site_conditions"] = moved_conditions
                remaining_conditions = {ref: copy.deepcopy(dict(row)) for ref,row in source_conditions.items() if ref not in selected_estates and isinstance(row,Mapping)}
                if remaining_conditions: source["site_conditions"] = remaining_conditions
                else: source.pop("site_conditions",None)
            new_roster = {"schema":"jianghu-person-lite-roster-1.0","faction_ref":new_ref,"people":moved_people}
            new_faction = reconcile_faction_population(new_faction,new_roster)
            registry = register_materialized_faction_bundle(registry=registry,faction=new_faction,roster=new_roster,inventory=new_inv)
            family = reconcile_family_transition(family,moved_refs=member_refs,source_faction_ref=source_ref,target_faction_ref=new_ref)
            try:
                equipment_transition = detach_faction_policy_holders(
                    self.repository.read_json(_EQUIPMENT), source_faction_ref=source_ref, holder_refs=member_refs,
                )
            except ValueError as exc:
                raise CommandRejectedError("jianghu_faction_split_equipment_conflict") from exc
            writes[_EQUIPMENT] = equipment_transition["equipment_ledger_after"]
            writes[spath]=compact_faction_state(source); writes[srpath]=compact_roster_state(source_roster,faction=source); writes[sipath]=compact_inventory_state(source_inv)
            writes[faction_path(new_ref)]=compact_faction_state(new_faction); writes[roster_path(new_ref)]=compact_roster_state(new_roster,faction=new_faction); writes[inventory_path(new_ref)]=compact_inventory_state(new_inv)
            return finish("jianghu_faction_split",{"action":action,"source_faction_ref":source_ref,"new_faction_ref":new_ref,"member_refs":sorted(member_refs),"treasury_cash":cash,"inventory_transfer":moved_inventory,"equipment_policy_detached_count":equipment_transition["detached_policy_holder_count"]},registry)

        if action == "merge":
            target_ref = str(command.payload.get("target_faction_ref") or "")
            if target_ref == source_ref or target_ref not in active:
                raise CommandRejectedError("jianghu_faction_merge_target_invalid")
            # A merger is a bilateral institutional choice. For gameplay, require
            # strong current mutual trust rather than allowing the player to annex
            # an unrelated NPC institution through a payload alone.
            forward = next((row for row in relations.get("edges",[]) if isinstance(row,Mapping) and row.get("from_faction")==source_ref and row.get("to_faction")==target_ref),{})
            reverse = next((row for row in relations.get("edges",[]) if isinstance(row,Mapping) and row.get("from_faction")==target_ref and row.get("to_faction")==source_ref),{})
            if command.mode == "gameplay" and min(int(forward.get("trust",0)),int(reverse.get("trust",0))) < 70:
                raise CommandRejectedError("jianghu_faction_merge_consent_insufficient")
            tpath,target=read_faction(self.repository,target_ref); trpath=roster_path(target_ref); target_roster=hydrate_roster_state(self.repository.read_json(trpath),faction=target)
            sipath=inventory_path(source_ref); tipath=inventory_path(target_ref)
            source_inv=hydrate_inventory_state(self.repository.read_json(sipath)); target_inv=hydrate_inventory_state(self.repository.read_json(tipath))
            source_inv,target_inv,moved_inventory=transfer_inventory(source_inv,target_inv,transfer_all=True)
            moved_refs=[str(row.get("person_id")) for row in source_people if _alive(row) and isinstance(row.get("person_id"),str)]
            if faction_retirement_blockers(self.repository.read_json, source_ref) or member_transition_blockers(
                self.repository.read_json, moved_refs, source_faction_ref=source_ref,
            ):
                raise CommandRejectedError("jianghu_faction_merge_activity_active")
            moved_people=_institutional_people([row for row in source_people if str(row.get("person_id")) in set(moved_refs)],leader_ref="")
            target_rows=[copy.deepcopy(dict(row)) for row in target_roster.get("people",[]) if isinstance(row,Mapping)]
            existing={str(row.get("person_id")) for row in target_rows}
            if any(ref in existing for ref in moved_refs): raise CommandRejectedError("jianghu_faction_merge_person_conflict")
            target_roster["people"] = target_rows + moved_people
            source_roster["people"] = [row for row in source_people if str(row.get("person_id")) not in set(moved_refs)]
            target["treasury_cash"] = max(0,int(target.get("treasury_cash",0))) + max(0,int(source.get("treasury_cash",0))); source["treasury_cash"] = 0
            source, target, moved_holdings = transfer_holdings(source, target)
            source = retire_organizational_scale(source)
            # Institutional merger transfers every real compound, including
            # secondary estates captured before the merger, and preserves any
            # current structural damage at those sites.  A site collision is
            # rejected rather than silently dropping one physical owner.
            controlled=target.setdefault("controlled_estates",{})
            if not isinstance(controlled,dict): raise CommandRejectedError("jianghu_faction_merge_control_invalid")
            estates_to_move: dict[str,dict[str,Any]] = {}
            estate=primary_estate_projection(source,acquired_at=at_iso)
            if estate:
                primary_site,primary_row=estate; estates_to_move[primary_site]=primary_row
            source_secondary=source.get("controlled_estates",{}) if isinstance(source.get("controlled_estates"),Mapping) else {}
            for estate_ref,estate_row in source_secondary.items():
                if isinstance(estate_ref,str) and isinstance(estate_row,Mapping): estates_to_move[estate_ref]=copy.deepcopy(dict(estate_row))
            target_primary=str(target.get("local_site_ref") or "")
            collisions=[ref for ref in estates_to_move if ref==target_primary or ref in controlled]
            if collisions: raise CommandRejectedError("jianghu_faction_merge_estate_conflict")
            for estate_ref in sorted(estates_to_move): controlled[estate_ref]=estates_to_move[estate_ref]
            source_conditions=source.get("site_conditions",{}) if isinstance(source.get("site_conditions"),Mapping) else {}
            target_conditions=target.setdefault("site_conditions",{}) if source_conditions else target.get("site_conditions")
            if source_conditions:
                if not isinstance(target_conditions,dict): raise CommandRejectedError("jianghu_faction_merge_site_condition_invalid")
                for estate_ref,condition in source_conditions.items():
                    if not isinstance(estate_ref,str) or not isinstance(condition,Mapping): continue
                    if estate_ref in target_conditions: raise CommandRejectedError("jianghu_faction_merge_site_condition_conflict")
                    target_conditions[estate_ref]=copy.deepcopy(dict(condition))
            source["buildings"]={}; source["infrastructure"]={}; source["enterprises"]={}
            source.pop("controlled_estates",None); source.pop("site_conditions",None)
            source = mark_faction_extinct(reconcile_faction_population(source,source_roster)); target=reconcile_faction_population(target,target_roster)
            try:
                property_transfer = transfer_faction_property_authority(
                    self.repository.read_json(_EQUIPMENT), source_faction_ref=source_ref, target_faction_ref=target_ref,
                )
            except ValueError as exc:
                raise CommandRejectedError("jianghu_faction_merge_property_conflict") from exc
            writes[_EQUIPMENT] = property_transfer["equipment_ledger_after"]
            registry=unregister_faction(registry,source_ref)
            registry["dormant_estate_refs"] = sorted(ref for ref in registry.get("dormant_estate_refs",[]) if ref != source_ref)
            relations=retire_faction_relations(relations,source_ref); family=reconcile_family_transition(family,moved_refs=moved_refs,source_faction_ref=source_ref,target_faction_ref=target_ref)
            writes[spath]=compact_faction_state(source); writes[srpath]=compact_roster_state(source_roster,faction=source); writes[sipath]=compact_inventory_state(source_inv)
            writes[tpath]=compact_faction_state(target); writes[trpath]=compact_roster_state(target_roster,faction=target); writes[tipath]=compact_inventory_state(target_inv)
            return finish("jianghu_factions_merged",{"action":action,"source_faction_ref":source_ref,"target_faction_ref":target_ref,"moved_member_count":len(moved_refs),"inventory_transfer":moved_inventory,"holdings_transfer":moved_holdings,"property_authority_transfer":{"claims":property_transfer["transferred_claim_count"],"recovery_demands":property_transfer["transferred_recovery_demand_count"],"policy_holders":property_transfer["materialized_policy_holder_count"]}},registry,relations)

        if action == "dissolve":
            independent=copy.deepcopy(self.repository.read_json(_INDEPENDENTS)); independent_rows=independent.get("people",[])
            if not isinstance(independent_rows,list): raise CommandRejectedError("jianghu_independent_people_invalid")
            existing={str(row.get("person_id")) for row in independent_rows if isinstance(row,Mapping)}
            moved=[row for row in source_people if _alive(row)]
            if any(str(row.get("person_id")) in existing for row in moved): raise CommandRejectedError("jianghu_faction_dissolve_person_conflict")
            for row in moved:
                person=copy.deepcopy(dict(row)); person.pop("membership_grade",None); person["standing_offices"]=[]; person["former_faction_ref"]=source_ref; person["independent_since"]=at_iso
                independent_rows.append(compact_independent_person(person))
            moved_refs=[str(row.get("person_id")) for row in moved if isinstance(row.get("person_id"),str)]
            if faction_retirement_blockers(self.repository.read_json, source_ref) or member_transition_blockers(
                self.repository.read_json, moved_refs, source_faction_ref=source_ref,
            ):
                raise CommandRejectedError("jianghu_faction_dissolve_activity_active")
            source_roster["people"]=[row for row in source_people if str(row.get("person_id")) not in set(moved_refs)]
            source=mark_faction_extinct(reconcile_faction_population(source,source_roster)); registry=unregister_faction(registry,source_ref)
            relations=retire_faction_relations(relations,source_ref); family=reconcile_family_transition(family,moved_refs=moved_refs,source_faction_ref=source_ref,target_faction_ref=None)
            try:
                equipment_transition = detach_faction_policy_holders(
                    self.repository.read_json(_EQUIPMENT), source_faction_ref=source_ref, holder_refs=moved_refs,
                )
            except ValueError as exc:
                raise CommandRejectedError("jianghu_faction_dissolve_equipment_conflict") from exc
            writes[_EQUIPMENT] = equipment_transition["equipment_ledger_after"]
            writes[_INDEPENDENTS]=independent; writes[spath]=compact_faction_state(source); writes[srpath]=compact_roster_state(source_roster,faction=source)
            return finish("jianghu_faction_dissolved",{"action":action,"source_faction_ref":source_ref,"independent_member_refs":sorted(moved_refs),"equipment_policy_detached_count":equipment_transition["detached_policy_holder_count"]},registry,relations)

        raise CommandRejectedError("jianghu_faction_lifecycle_action_invalid")


__all__ = ["JianghuFactionLifecycleCommandsMixin"]
