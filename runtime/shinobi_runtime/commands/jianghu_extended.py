"""Additional Jianghu-native semantic commands.

Every reducer writes the same conserved owners used by autonomous settlement.
Only current Jianghu authorities are consulted.
"""
from __future__ import annotations

import copy
import hashlib
import math
from datetime import datetime, timedelta
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.martial_world.institutional_obligations import estate_claim_value_blockers
from shinobi_runtime.martial_world.live_state import set_roster_person
from shinobi_runtime.martial_world.person_state import reconcile_faction_population
from shinobi_runtime.martial_world.medicine import (
    administer_dose, diagnosis_score, medicine_category, stabilize_wounds,
    toxicity_consequences, treat_poison_burden, wound_treatment_score,
)
from shinobi_runtime.martial_world.poison import active_qi_purge, clear_poison_burden, combined_poison_burdens
from shinobi_runtime.martial_world.qi import person_current_qi_milli, set_person_current_qi_milli
from shinobi_runtime.martial_world.physiology_frontier import attach_person_physiology_wake, detach_person_physiology_wake
from shinobi_runtime.martial_world.physical_presence import effective_person_presence, physical_unavailable_person_refs, same_effective_location
from shinobi_runtime.martial_world.production import workshop_quote, medicine_quote, poison_quote, consume_inputs
from shinobi_runtime.martial_world.travel import travel_plan
from shinobi_runtime.martial_world.regional_economy import region_for_place, execute_purchase, execute_sale
from shinobi_runtime.martial_world.equipment_lifecycle import repair_material_requirements, repair_quote as lifecycle_repair_quote
from shinobi_runtime.martial_world.equipment_state import assigned_policy, compact_equipment_ledger, effective_person_loadout, hydrate_equipment_ledger, loadout_policy
from shinobi_runtime.martial_world.equipment import carried_mass_kg, encumbrance_effects, resolve_equipment_item
from shinobi_runtime.martial_world.social_presence import person_attends_site
from shinobi_runtime.martial_world.relationships import apply_relationship_event
from shinobi_runtime.martial_world.social_causality import (
    add_personal_obligation, apply_martial_events, breach_hostile_commitments, close_family_refs,
    prune_incidental_martial_familiarity, vow_conflicts,
    obligation_ref as personal_obligation_ref, obligations_for_actor,
    record_belief, resolve_personal_obligation,
)
from shinobi_runtime.martial_world.rankings import (
    apply_faction_awareness_evidence, apply_faction_reputation_evidence,
    apply_personal_fame_evidence,
)
from shinobi_runtime.martial_world.property import (
    clear_recovery_demand, issue_recovery_demand, move_claim_after_seizure,
    personally_owned_quantity, policy_owned_quantity, property_evidence_ref,
    provenance_claim, set_nonholder_claim, transfer_faction_property_authority, validate_property_evidence,
)
from shinobi_runtime.martial_world.faction_relations import evaluate_proposal, apply_relation_event, proposal_kind_supported, stage_treaty
from shinobi_runtime.martial_world.family_life import courtship_eligible, marriage_eligible
from shinobi_runtime.martial_world.crime_custody import create_custody_record, create_government_custody_record, custody_transition, crime_attention, government_rescue_infiltration
from shinobi_runtime.martial_world.government import compact_attention_row
from shinobi_runtime.martial_world.government_finance import fund_bounty_escrow
from shinobi_runtime.martial_world.agency import factual_restraint_basis
from shinobi_runtime.martial_world.aggregate_transport import faction_available_capacity
from shinobi_runtime.martial_world.exact_combat import combat_default_targeting_intent, default_action_for, default_target_for, default_weapon_for_action, improvised_weapon_state_from_scene, initialize_combat, resolve_exchange, attempt_disengage
from shinobi_runtime.martial_world.scene_sessions import SESSION_PATH as _SCENE_SESSION_PATH, scene_history_record
from shinobi_runtime.martial_world.health import functional_capacity_factors, record_current_wound
from shinobi_runtime.martial_world.manpower import is_living_and_conscious
from shinobi_runtime.martial_world.mounts import active_mount_allocations
from shinobi_runtime.martial_world.faction_state import (
    compact_faction_state, faction_path as canonical_faction_path, inventory_path as canonical_inventory_path,
    hydrate_faction_state, read_faction, roster_path as canonical_roster_path,
)
from shinobi_runtime.martial_world.person_state import compact_roster_state, hydrate_roster_state
from shinobi_runtime.martial_world.family_simulation import apply_recognized_succession
from shinobi_runtime.martial_world.institutional_lifecycle import settle_institutional_offices
from shinobi_runtime.martial_world.institutional_operations import OPERATIONS_PATH, close_institutional_operation
from shinobi_runtime.martial_world.field_command import build_deployment_structure, validate_deployment_structure
from shinobi_runtime.martial_world.commitments import derived_commitment_state, release_resources
from shinobi_runtime.martial_world.faction_existence import settle_extinctions_from_touched_rosters
from shinobi_runtime.martial_world.death_lifecycle import (
    clean_social_and_custody_for_deaths, close_family_authorities, exact_person_index, is_living,
    prune_dead_from_durable_activities, release_custody_held_by_extinct_factions, settle_exact_death_estates,
)
from shinobi_runtime.martial_world.faction_registry import current_faction_refs
from shinobi_runtime.martial_world.faction_transitions import retire_organizational_scale, transfer_holdings
from shinobi_runtime.martial_world.life_frontier import appoint_civic_successors
from shinobi_runtime.martial_world.civic import compact_civic_person, hydrate_civic_person
from shinobi_runtime.martial_world.security import alarm_response_seconds, breach_repair_quote, forced_entry_resolution, infiltration_resolution, select_watch_guards
from shinobi_runtime.martial_world.research import research_record
from shinobi_runtime.martial_world.environment import combat_environment, site_combat_terrain
from shinobi_runtime.martial_world.site_control import buildings_at_site, infrastructure_at_site, set_site_condition, site_condition
from shinobi_runtime.martial_world.weather import weather_snapshot
from shinobi_runtime.martial_world.scheduler import upsert_one_off_event
from shinobi_runtime.martial_world.infrastructure import (
    infirmary_capacity, storage_capacity_check, transport_capacity_check, workshop_capacity,
    enterprise_scale_value, enterprise_operating_efficiency_milli, compact_project_state,
)

_EQUIPMENT='state/martial-world/equipment-ledger.json'
_RELATIONS='state/martial-world/faction-relations.json'
_FAMILY='state/martial-world/family.json'
_CUSTODY='state/martial-world/custody.json'
_CIVIC='state/martial-world/civic-people.json'
_CIVILIANS='state/martial-world/civilian-populations.json'
_GOVERNMENT='state/martial-world/government.json'
_SCHEDULE='state/martial-world/scheduler.json'
_COMBATS='state/martial-world/combats.json'
_DEPLOYMENTS='state/martial-world/deployments.json'
_PROJECTS='state/martial-world/projects.json'
_ROUTE_OPERATIONS='state/martial-world/route-operations.json'
_SOCIAL='state/martial-world/social.json'
_REPUTATION='state/martial-world/reputation.json'
_LOCAL_SITES='game/data/martial-world/local-sites.json'
_GEOGRAPHY='game/data/martial-world/geography.json'
_EQUIPMENT_DATA='game/data/martial-world/equipment.json'
_LIBRARY_CATALOG='game/data/martial-world/library-records.json'


def _dt(time: CampaignTime) -> datetime:
    return datetime(time.year,time.month,time.day,time.hour,time.minute,time.second)


def _faction_path(fid:str)->str:return canonical_faction_path(fid)
def _inventory_path(fid:str)->str:return canonical_inventory_path(fid)
def _roster_path(fid:str)->str:return canonical_roster_path(fid)


def _office(person: Mapping[str,Any], *allowed:str)->bool:
    roots={str(x).split(':',1)[0] for x in person.get('standing_offices',[]) if isinstance(x,str)}
    return bool(set(allowed)&roots)



def _resolve_player_combat_span(
    *, combat: Mapping[str, Any], people: Mapping[str, Mapping[str, Any]],
    equipment_ledger: Mapping[str, Any], doctrines: Mapping[str, Mapping[str, Any]],
    player_ref: str, social_state: Mapping[str, Any], player_retinue_context: Mapping[str, Any] | None,
    raw_target_ref: str, raw_action_kind: str, raw_weapon_ref: str, hit_zone: str,
    target_structure_ref: str | None, targeting_intent: str | None,
    explicit_poison_ref: str | None, poison_auto: bool,
    explicit_qi_allocation_milli: Mapping[str, Any] | None, qi_auto: bool,
    exchange_count: int | None, duration_seconds: int | None, until_resolution: bool,
    player_improvised_weapon_state: Mapping[str, Any] | None = None,
    frontier_exchanges: int = 160,
) -> dict[str, Any]:
    """Resolve one high-level player combat intent across a bounded span.

    The frontier is a transaction-work boundary, not a combat mechanic. If an
    until-resolution or longer requested span reaches it while combat remains
    active, the result advertises continuation_required so the same already-
    declared intent can continue without asking the player to micromanage.
    """
    combat_cursor=copy.deepcopy(dict(combat))
    people_cursor={str(ref):copy.deepcopy(dict(person)) for ref,person in people.items()}
    ledger_cursor=hydrate_equipment_ledger(equipment_ledger)
    social_cursor=copy.deepcopy(dict(social_state))
    all_events:list[Mapping[str,Any]]=[]
    exchanges=0
    start_elapsed=max(0,int(combat_cursor.get('elapsed_ms',0)))
    duration_ms=(max(1,int(duration_seconds))*1000) if duration_seconds is not None else None
    requested_count=max(1,int(exchange_count)) if exchange_count is not None else None
    doctrine_ref=(
        str(people_cursor[player_ref].get('combat_doctrine_ref'))
        if isinstance(people_cursor.get(player_ref),Mapping) and people_cursor[player_ref].get('combat_doctrine_ref')
        else None
    )
    side_by_ref={
        str(ref):str(side)
        for side,refs in (combat_cursor.get('sides',{}) if isinstance(combat_cursor.get('sides'),Mapping) else {}).items()
        if isinstance(refs,list) for ref in refs if isinstance(ref,str)
    }
    rejected_results={
        'invalid_target','friendly_target_rejected','target_unavailable','action_rejected',
        'target_not_observed','no_lawfully_known_target',
    }
    stop_reason='scope_complete'
    last_resolved:dict[str,Any]|None=None
    improvised_ref=(
        str(player_improvised_weapon_state.get('fact_ref') or '')
        if isinstance(player_improvised_weapon_state,Mapping) else ''
    )

    def requested_scope_complete()->bool:
        if requested_count is not None:
            return exchanges>=requested_count
        if duration_ms is not None:
            return max(0,int(combat_cursor.get('elapsed_ms',0))-start_elapsed)>=duration_ms
        if until_resolution:
            return str(combat_cursor.get('status') or '')!='active'
        return exchanges>=1

    while str(combat_cursor.get('status') or '')=='active' and not requested_scope_complete():
        if exchanges>=max(1,int(frontier_exchanges)):
            stop_reason='execution_frontier'
            break
        try:
            target_ref=(
                default_target_for(
                    combat=combat_cursor,people=people_cursor,actor_ref=player_ref,
                    martial_familiarity=social_cursor,
                )
                if raw_target_ref in {'','auto'} else raw_target_ref
            )
        except ValueError:
            if exchanges==0:raise
            stop_reason='no_lawfully_known_target' if raw_target_ref in {'','auto'} else 'explicit_target_unavailable'
            break
        if raw_target_ref not in {'','auto'} and exchanges>0:
            target_person=people_cursor.get(target_ref)
            target_state=(combat_cursor.get('combatants',{}).get(target_ref) if isinstance(combat_cursor.get('combatants'),Mapping) else None)
            health=target_person.get('health',{}) if isinstance(target_person,Mapping) and isinstance(target_person.get('health'),Mapping) else {}
            statuses=set(str(x) for x in target_state.get('status_families',[]) if isinstance(x,str)) if isinstance(target_state,Mapping) else set()
            if not isinstance(target_person,Mapping) or not isinstance(target_state,Mapping) or str(health.get('status') or '') in {'dead','incapacitated'} or bool(statuses & {'dead','incapacitated'}):
                stop_reason='explicit_target_unavailable'
                break
            if hit_zone=='mount':
                mount=target_state.get('mount') if isinstance(target_state.get('mount'),Mapping) else None
                if not isinstance(mount,Mapping) or not bool(mount.get('active',True)) or str(mount.get('status') or 'active')!='active':
                    stop_reason='explicit_target_unavailable'
                    break
        resolved_intent=(
            str(targeting_intent)
            if targeting_intent not in (None,'')
            else combat_default_targeting_intent(combat_cursor,doctrine_ref=doctrine_ref)
        )
        # A bare/high-level attack delegates the omitted force detail to Wei's
        # standing policy. Personal nonlethal vows therefore constrain that
        # autonomous detail just as they constrain NPC/delegated combat. An
        # explicitly supplied targeting_intent remains the player's override.
        if targeting_intent in (None,'') and resolved_intent=='lethal':
            target_person=people_cursor.get(target_ref,{})
            if vow_conflicts(
                social_cursor,person_ref=player_ref,action_kind='attack',target_ref=target_ref,
                target_faction_ref=str(target_person.get('faction_ref') or '') if isinstance(target_person,Mapping) else '',
                targeting_intent=resolved_intent,
            ):
                resolved_intent='disable'
        if improvised_ref:
            if raw_action_kind not in {'attack','auto','improvised_strike'}:
                raise ValueError('improvised scene prop action conflicts with explicit action kind')
            if raw_weapon_ref not in {'','auto',improvised_ref}:
                raise ValueError('improvised scene prop conflicts with explicit weapon')
            action_kind='improvised_strike'
            weapon_ref=improvised_ref
        elif raw_action_kind in {'attack','auto'}:
            action_kind,weapon_ref=default_action_for(
                combat=combat_cursor,people=people_cursor,equipment_ledger=ledger_cursor,
                actor_ref=player_ref,target_ref=target_ref,martial_familiarity=social_cursor,
                preferred_weapon_ref=(raw_weapon_ref if raw_weapon_ref not in {'','auto'} else None),
            )
        else:
            action_kind=raw_action_kind
            weapon_ref=(
                default_weapon_for_action(
                    people=people_cursor,equipment_ledger=ledger_cursor,actor_ref=player_ref,
                    action_kind=action_kind,
                )
                if raw_weapon_ref in {'','auto'} else raw_weapon_ref
            )
        resolved=resolve_exchange(
            combat=combat_cursor,people=people_cursor,equipment_ledger=ledger_cursor,doctrines=doctrines,
            player_ref=player_ref,player_action_kind=action_kind,player_target_ref=target_ref,
            player_weapon_ref=weapon_ref,player_hit_zone=hit_zone,
            player_target_structure_ref=target_structure_ref,player_targeting_intent=resolved_intent,
            player_poison_ref=explicit_poison_ref,
            player_qi_allocation_milli=explicit_qi_allocation_milli,
            player_auto_qi=bool(qi_auto),player_auto_poison=bool(poison_auto),
            martial_familiarity=social_cursor,player_retinue_context=player_retinue_context,
            player_improvised_weapon_state=(player_improvised_weapon_state if exchanges==0 else None),
            equipment_ledger_hydrated=True,compact_equipment_result=False,mutate_equipment_ledger=True,
        )
        last_resolved=resolved
        combat_cursor=copy.deepcopy(dict(resolved['combat_after']))
        people_cursor={str(ref):copy.deepcopy(dict(person)) for ref,person in resolved['people_after'].items()}
        ledger_cursor=resolved['equipment_ledger_after']
        events=[]
        for raw_event in resolved.get('events',[]):
            if not isinstance(raw_event,Mapping):
                continue
            event=dict(raw_event)
            if str(event.get('actor_ref') or '')==player_ref:
                event['targeting_intent']=resolved_intent
            events.append(event)
        all_events.extend(events)
        exchanges+=1

        # Let adaptation and already-broken commitments affect later exchanges
        # inside the same declared span without persisting them twice. The
        # command wrapper applies the complete event set once to durable social
        # state after the combat clock has advanced.
        social_cursor=apply_martial_events(social_cursor,events,side_by_ref=side_by_ref)
        for event in events:
            if str(event.get('actor_ref') or '')!=player_ref or str(event.get('result') or '') in rejected_results:
                continue
            event_target=str(event.get('intended_ref') or '')
            if not event_target or event_target not in people_cursor:continue
            breached=breach_hostile_commitments(
                social_cursor,actor_ref=player_ref,target_ref=event_target,
                target_faction_ref=str(people_cursor[event_target].get('faction_ref') or ''),
                targeting_intent=resolved_intent,poison_ref=str(event.get('poison_ref') or ''),
            )
            social_cursor=breached['state_after']

    if last_resolved is None:
        raise ValueError('combat span produced no exchange')
    if str(combat_cursor.get('status') or '')!='active':
        stop_reason='combat_resolved'
    elif requested_scope_complete():
        stop_reason='scope_complete'
    continuation_required=(stop_reason=='execution_frontier' and str(combat_cursor.get('status') or '')=='active')
    return {
        **last_resolved,
        'combat_after':combat_cursor,'people_after':people_cursor,
        'equipment_ledger_after':compact_equipment_ledger(ledger_cursor),
        'events':all_events,'exchanges_resolved':exchanges,'scope_stop_reason':stop_reason,
        'continuation_required':continuation_required,
    }


def _relations_edge(registry:Mapping[str,Any],a:str,b:str)->Mapping[str,Any]|None:
    for e in registry.get('edges',[]):
        if isinstance(e,Mapping) and e.get('from_faction')==a and e.get('to_faction')==b:return e
    return None


def _person_relation(social:Mapping[str,Any],a:str,b:str)->dict[str,int]:
    key=f'{a}|{b}'; row=social.get('relationships',{}).get(key,{}) if isinstance(social.get('relationships'),Mapping) else {}
    return {k:int(row.get(k,0)) for k in ('trust','affection','respect','familiarity')}


def _effective_location(owner: Any, person_ref: str, person: Mapping[str, Any]) -> str:
    """Resolve physical location without depending on a sibling command mixin."""
    repository = getattr(owner, 'repository', None)
    read_json = getattr(repository, 'read_json', None)
    if callable(read_json):
        presence = effective_person_presence(read_json, str(person_ref), person=person)
        return str(presence.get('location_ref') or '')
    # Pure reducer harnesses may intentionally omit a repository. In that
    # isolated case the supplied person record is the complete local authority.
    return str(person.get('location_ref') or '')


def _improvised_prop_state_for_combat(owner: Any, *, combat: Mapping[str, Any], actor_ref: str, fact_ref: str) -> dict[str, Any]:
    """Promote one already-established mundane scene prop into combat-local state."""
    if not isinstance(fact_ref,str) or not fact_ref:
        raise ValueError('improvised prop fact ref invalid')
    combatant_state=(combat.get('combatants',{}).get(actor_ref) if isinstance(combat.get('combatants'),Mapping) else None)
    existing=(combatant_state.get('improvised_weapon_state') if isinstance(combatant_state,Mapping) else None)
    if (
        isinstance(existing,Mapping)
        and existing.get('kind')=='scene_improvised_weapon_state'
        and str(existing.get('fact_ref') or '')==fact_ref
        and str(existing.get('holder_ref') or '')==actor_ref
    ):
        if str(existing.get('status') or '')!='held' or int(existing.get('condition_milli',0))<=0:
            raise ValueError('improvised scene prop is no longer usable')
        if str(existing.get('source_location_ref') or '')!=str(combat.get('zone_ref') or ''):
            raise ValueError('improvised scene prop combat location mismatch')
        return copy.deepcopy(dict(existing))

    repository=getattr(owner,'repository',None); read_json=getattr(repository,'read_json',None)
    if not callable(read_json):
        raise ValueError('improvised scene prop requires repository authority')
    session=read_json(_SCENE_SESSION_PATH)
    if not isinstance(session,Mapping) or session.get('schema')!='jianghu-scene-session-1.0':
        raise ValueError('improvised scene prop session unresolved')
    if session.get('status')!='closed' or session.get('close_reason')!='hard_interruption':
        raise ValueError('improvised scene prop requires immediate combat interruption of its scene')
    if actor_ref not in {str(x) for x in session.get('participant_refs',[]) if isinstance(x,str)}:
        raise ValueError('improvised scene prop actor was not in source scene')
    if str(session.get('location_ref') or '')!=str(combat.get('zone_ref') or ''):
        raise ValueError('improvised scene prop source location mismatch')
    if str(session.get('closed_at') or '').removeprefix('SE-') != str(combat.get('started_at') or '').removeprefix('SE-'):
        raise ValueError('improvised scene prop is not from this combat interruption')
    session_ref=str(session.get('session_ref') or '')
    row=scene_history_record(read_json,fact_ref,session_ref=session_ref)
    if not isinstance(row,Mapping) or row.get('fact_kind')!='object_state' or row.get('actor_ref')!=actor_ref:
        raise ValueError('improvised scene prop fact is not Wei object-state continuity')
    prop=row.get('improvised_prop') if isinstance(row.get('improvised_prop'),Mapping) else None
    if not isinstance(prop,Mapping) or prop.get('kind')!='mundane_improvised_prop':
        raise ValueError('scene fact is not a typed mundane improvised prop')
    source_object_fact_ref=str(row.get('source_object_fact_ref') or '')
    basis_refs={ref for ref in row.get('basis_refs',[]) if isinstance(ref,str)} if isinstance(row.get('basis_refs'),list) else set()
    source_object_fact=(
        scene_history_record(read_json,source_object_fact_ref,session_ref=session_ref)
        if source_object_fact_ref and source_object_fact_ref in basis_refs else None
    )
    if not (
        isinstance(source_object_fact,Mapping)
        and source_object_fact.get('fact_kind')=='object_state'
        and source_object_fact.get('truth_status')=='observed_reversible_scene_fact'
        and source_object_fact.get('mechanical_consequence_authority') is False
        and isinstance(source_object_fact.get('improvised_prop'),Mapping)
        and dict(source_object_fact.get('improvised_prop'))==dict(prop)
    ):
        source_object_fact=None
    if not isinstance(source_object_fact,Mapping):
        raise ValueError('improvised scene prop lacks prior scene-object provenance')
    return improvised_weapon_state_from_scene(
        fact_ref=fact_ref, holder_ref=actor_ref,
        source_object_fact_ref=source_object_fact_ref,
        source_session_ref=session_ref, source_location_ref=str(combat.get('zone_ref') or ''),
        summary=str(row.get('summary') or 'mundane improvised prop'),
        form=str(prop.get('form') or ''), material=str(prop.get('material') or ''), condition=str(prop.get('condition') or 'intact'),
    )


class JianghuExtendedCommandsMixin:
    def _person_present_for_command(
        self, scene: Mapping[str,Any], person_ref: str, current_time: CampaignTime, observer_ref: str
    ) -> bool:
        # Scene cast is presentation only. Mechanical co-presence derives from
        # exact physical owners for both the observer and the target.
        if not self._person_available_for_activity(person_ref):
            return False
        try:
            _op,_or,_oo,observer=self._person(observer_ref)
            _rp,_rr,_ro,person=self._person(person_ref)
        except CommandRejectedError:
            return False
        site_ref=_effective_location(self, observer_ref, observer)
        if not site_ref:
            return False
        if self._same_effective_location(observer_ref, person_ref):
            return True
        try:
            sites_data=self.repository.read_json(_LOCAL_SITES); sites=sites_data.get('sites',{}) if isinstance(sites_data,Mapping) else {}
            site=sites.get(site_ref) if isinstance(sites,Mapping) else None
            if not isinstance(site,Mapping):return False
            faction_ref=str(person.get('faction_ref') or '')
            if faction_ref:
                _fp,faction=read_faction(self.repository,faction_ref)
                home=str(faction.get('headquarters') or '')
            else:
                home=str(person.get('home_place_ref') or '')
                if not home:return False
            return person_attends_site(
                person,site_ref=site_ref,site=site,faction_headquarters=home,
                sites=sites,at=_dt(current_time),unavailable_refs=self._physically_unavailable_person_refs(),
            )
        except (FileNotFoundError, KeyError, TypeError, ValueError, CommandRejectedError):
            return False

    def _cleanup_command_deaths(self,writes:Mapping[str,Any],dead_refs:set[str],current_time:CampaignTime)->dict[str,Any]:
        """Close current authorities invalidated by an immediate command death.

        Scheduler-driven deaths already use the same rules in the world
        frontier. This path prevents exact combat from
        leaving a dead leader, captor, spouse-household head or
        deployment member mechanically active until the next monthly wake.
        Permanent kinship identity remains in family state.
        """
        dead={str(x) for x in dead_refs if isinstance(x,str) and x}
        out={str(k):copy.deepcopy(v) for k,v in writes.items()}
        if not dead:return out

        family=copy.deepcopy(out.get(_FAMILY) or self.repository.read_json(_FAMILY))
        social=copy.deepcopy(out.get(_SOCIAL) or self.repository.read_json(_SOCIAL))
        custody=copy.deepcopy(out.get(_CUSTODY) or self.repository.read_json(_CUSTODY))
        
        def _activity_read(path:str):
            return copy.deepcopy(out[path]) if path in out else self.repository.read_json(path)
        commitments=derived_commitment_state(_activity_read)
        deployments=copy.deepcopy(out.get(_DEPLOYMENTS) or self.repository.read_json(_DEPLOYMENTS))

        faction_refs=current_faction_refs(self.repository.read_json)
        person_index=exact_person_index(read_json=self.repository.read_json,writes=out,faction_refs=faction_refs)
        living_people={
            ref:route["person"] for ref,route in person_index.items()
            if ref not in dead and isinstance(route.get("person"),Mapping) and is_living(route["person"])
        }
        family=close_family_authorities(family,dead_refs=sorted(dead),living_people=living_people)
        out[_FAMILY]=family

        faction_dead:dict[str,set[str]]={}
        dead_civic_rows:list[dict[str,Any]]=[]
        # Current offices end at the exact death frontier for every owner kind.
        # Preserve the civic pre-death office rows separately for appointment.
        for ref in sorted(dead):
            route=person_index.get(ref)
            if not isinstance(route,Mapping):continue
            if route.get('owner_kind')=='faction':
                fid=str(route.get('owner_ref') or '')
                if fid:faction_dead.setdefault(fid,set()).add(ref)
            elif route.get('owner_kind')=='civic' and isinstance(route.get('person'),Mapping):
                dead_civic_rows.append(copy.deepcopy(dict(route['person'])))
            path=str(route.get('path') or '')
            if not path:continue
            owner=copy.deepcopy(out.get(path) or self.repository.read_json(path))
            rows=owner.get('people',[]) if isinstance(owner,Mapping) else []
            if not isinstance(rows,list):continue
            idx=next((i for i,row in enumerate(rows) if isinstance(row,Mapping) and row.get('person_id')==ref),None)
            if idx is None:continue
            row=copy.deepcopy(dict(rows[idx]));row['standing_offices']=[];rows[idx]=row;owner['people']=rows;out[path]=owner

        # Faction hereditary succession remains institution-local, but family
        # closure itself was global above and can therefore see cross-owner kin.
        for fid,refs in faction_dead.items():
            fpath=_faction_path(fid); rpath=_roster_path(fid)
            _stored_path,stored_faction=read_faction(self.repository,fid)
            faction=hydrate_faction_state(out.get(fpath,stored_faction))
            roster=hydrate_roster_state(out.get(rpath,self.repository.read_json(rpath)),faction=faction)
            rows=roster.get('people',[])
            if not isinstance(rows,list):continue
            succession=apply_recognized_succession(family,faction_ref=fid,roster_people=[p for p in rows if isinstance(p,Mapping)],year=current_time.year)
            roster['people']=succession['people_after']
            faction=reconcile_faction_population(faction,roster)
            out[fpath]=faction; out[rpath]=compact_roster_state(roster,faction=faction)

        # Personal estates settle before extinction so a last-member kill cannot
        # strand a purse in a dormant roster. Exact spouse/child inheritance is
        # universal across faction, independent and civic owners.
        geography=self.repository.read_json(_GEOGRAPHY)
        places=geography.get('places',{}) if isinstance(geography,Mapping) else {}
        place_region={str(pid):str(row.get('climate_profile') or '') for pid,row in places.items() if isinstance(row,Mapping) and row.get('climate_profile')} if isinstance(places,Mapping) else {}
        sites_doc=self.repository.read_json(_LOCAL_SITES)
        site_rows=sites_doc.get('sites',{}) if isinstance(sites_doc,Mapping) else {}
        settle_exact_death_estates(
            read_json=self.repository.read_json,writes=out,faction_refs=faction_refs,family=family,
            dead_refs=sorted(dead),place_region=place_region,site_rows=site_rows if isinstance(site_rows,Mapping) else {},
        )

        # Civic office death is immediate too. Promote an existing lawful exact
        # civic person first; if necessary consume one regional civilian body.
        if dead_civic_rows:
            civic_owner=copy.deepcopy(out.get(_CIVIC) or self.repository.read_json(_CIVIC))
            civic_rows=[hydrate_civic_person(row) for row in civic_owner.get('people',[]) if isinstance(row,Mapping)]
            civilian_state=copy.deepcopy(out.get(_CIVILIANS) or self.repository.read_json(_CIVILIANS))
            civic_rows,civilian_after,_appointments=appoint_civic_successors(
                civic_rows,dead_rows=dead_civic_rows,civilian_state=civilian_state,
                world_seed=str(self.repository.read_json('state/meta.json').get('world_seed') or 'jianghu'),year=current_time.year,
            )
            civic_owner['people']=[compact_civic_person(row) for row in civic_rows]
            out[_CIVIC]=civic_owner
            if civilian_after!=civilian_state:out[_CIVILIANS]=civilian_after


        social,custody,released_by_captor_death=clean_social_and_custody_for_deaths(
            social,custody,dead_refs=sorted(dead),
        )
        out[_SOCIAL]=social
        out[_CUSTODY]=custody

        # Hereditary succession above has first priority.  If it did not fill a
        # faction leadership vacancy, use the same ordinary office selector as
        # monthly institutional progression before the exact-combat command can
        # commit. Captive people are not eligible; ordinary deployment/travel is
        # not a bar to holding standing institutional authority.
        custody_unavailable={
            str(row.get('person_ref')) for row in (custody.get('records',[]) if isinstance(custody.get('records'),list) else [])
            if isinstance(row,Mapping) and str(row.get('person_ref') or '')
            and str(row.get('status') or '') not in {'released','escaped','rescued','executed'}
        }
        meta=self.repository.read_json('state/meta.json')
        player_ref=str(meta.get('player_id') or '') if isinstance(meta,Mapping) else ''
        for fid in sorted(faction_dead):
            fpath=_faction_path(fid); rpath=_roster_path(fid)
            try:
                _stored_path,stored_faction=read_faction(self.repository,fid)
                faction=hydrate_faction_state(out.get(fpath,stored_faction))
                roster=hydrate_roster_state(out.get(rpath,self.repository.read_json(rpath)),faction=faction)
            except (FileNotFoundError,KeyError,TypeError,ValueError):
                continue
            office_result=settle_institutional_offices(
                faction,roster,year=current_time.year,social=social,
                player_ref=player_ref or None,unavailable_refs=sorted(custody_unavailable),
            )
            roster=office_result['roster']; faction=reconcile_faction_population(faction,roster)
            out[fpath]=compact_faction_state(faction); out[rpath]=compact_roster_state(roster,faction=faction)

        # Death invalidates a person's participation in every finite activity,
        # not only deployments. Keep the activity owner itself when other
        # resources/people remain; remove empty reservation shells entirely.
        commit_rows=commitments.get('commitments',{}) if isinstance(commitments,Mapping) else {}
        person_index=commitments.get('person_index',{}) if isinstance(commitments,Mapping) else {}
        if isinstance(commit_rows,dict) and isinstance(person_index,dict):
            for cid,crow in list(commit_rows.items()):
                if not isinstance(crow,Mapping):continue
                resources=[r for r in crow.get('resources',[]) if not (isinstance(r,Mapping) and r.get('kind')=='person' and str(r.get('ref')) in dead)]
                person_refs=[str(ref) for ref in crow.get('person_refs',[]) if isinstance(ref,str) and str(ref) not in dead]
                if len(resources)!=len(crow.get('resources',[])) or len(person_refs)!=len(crow.get('person_refs',[])):
                    if resources:
                        updated=copy.deepcopy(dict(crow));updated['resources']=resources;updated['person_refs']=person_refs;commit_rows[cid]=updated
                    else:
                        commit_rows.pop(cid,None)
            for ref in dead:
                person_index.pop(ref,None)
            commitments['commitments']=commit_rows;commitments['person_index']=person_index# Temporary deployments restructure immediately after casualties. The
        # deployment remains the same conserved activity; only living exact
        # member refs remain in its command tree and commitment row.
        dep_rows=deployments.get('deployments',{}) if isinstance(deployments,Mapping) else {}
        if isinstance(dep_rows,dict) and isinstance(commit_rows,dict) and isinstance(person_index,dict):
            for dep_ref,row in list(dep_rows.items()):
                if not isinstance(row,Mapping):continue
                structure=row.get('structure',{}) if isinstance(row.get('structure'),Mapping) else {}
                members=[str(x) for x in structure.get('member_refs',[]) if isinstance(x,str)]
                if not (set(members)&dead):continue
                survivors=[ref for ref in members if ref not in dead]
                cid=str(row.get('commitment_ref') or f'commitment:{dep_ref}')
                if not survivors:
                    dep_rows.pop(dep_ref,None)
                    try:commitments=release_resources(commitments,activity_ref=str(dep_ref))
                    except ValueError:pass
                    commit_rows=commitments.get('commitments',{}) if isinstance(commitments.get('commitments'),dict) else {}
                    person_index=commitments.get('person_index',{}) if isinstance(commitments.get('person_index'),dict) else {}
                    continue
                records={}
                for ref in survivors:
                    try:_p,_r,_o,person=self._person(ref)
                    except CommandRejectedError:continue
                    # Prefer any command after-image for a same-faction roster.
                    fid=str(person.get('faction_ref') or '')
                    rpath=_roster_path(fid) if fid else ''
                    if rpath and rpath in out:
                        try:
                            faction=hydrate_faction_state(out.get(_faction_path(fid),read_faction(self.repository,fid)[1])); live_roster=hydrate_roster_state(out[rpath],faction=faction)
                            current=next((p for p in live_roster.get('people',[]) if isinstance(p,Mapping) and p.get('person_id')==ref),None)
                            if isinstance(current,Mapping):person=current
                        except (KeyError,ValueError):pass
                    if person.get('health',{}).get('status')!='dead':records[ref]=person
                survivors=[ref for ref in survivors if ref in records]
                if not survivors:
                    dep_rows.pop(dep_ref,None)
                    try:commitments=release_resources(commitments,activity_ref=str(dep_ref))
                    except ValueError:pass
                    commit_rows=commitments.get('commitments',{}) if isinstance(commitments.get('commitments'),dict) else {}
                    person_index=commitments.get('person_index',{}) if isinstance(commitments.get('person_index'),dict) else {}
                    continue
                preferred=str(structure.get('commander_ref') or ''); deputy=str(structure.get('deputy_ref') or '')
                rebuilt=build_deployment_structure(member_refs=survivors,records=records,preferred_commander_ref=preferred if preferred in survivors else None,preferred_deputy_ref=deputy if deputy in survivors else None)
                validate_deployment_structure(rebuilt)
                updated=copy.deepcopy(dict(row)); updated['structure']=rebuilt; dep_rows[dep_ref]=updated
                crow=commit_rows.get(cid)
                if isinstance(crow,Mapping):
                    next_crow=copy.deepcopy(dict(crow)); next_crow['resources']=[r for r in next_crow.get('resources',[]) if not (isinstance(r,Mapping) and r.get('kind')=='person' and str(r.get('ref')) in dead)]; next_crow['person_refs']=survivors; commit_rows[cid]=next_crow
                for ref in dead:
                    if person_index.get(ref)==cid:person_index.pop(ref,None)
            deployments['deployments']=dep_rows; commitments['commitments']=commit_rows; commitments['person_index']=person_index
            out[_DEPLOYMENTS]=deployments

        # Standing retinues, projects, routes and warrants are durable owners too.
        # Prune them after command-local deployment restructuring, then rebuild
        # availability from those exact after-images before releasing detainees.
        prune_dead_from_durable_activities(
            read_json=self.repository.read_json, writes=out, dead_refs=sorted(dead), faction_refs=faction_refs,
        )
        commitments=derived_commitment_state(
            lambda path: copy.deepcopy(out[path]) if path in out else self.repository.read_json(path)
        )

        # A living detainee whose captor died becomes physically available at
        # this same frontier unless another conserved activity still owns them.
        def _released_after_read(path: str):
            return copy.deepcopy(out[path]) if path in out else self.repository.read_json(path)
        blocked=set(commitments.get('person_index',{})) if isinstance(commitments.get('person_index'),Mapping) else set()
        blocked.update(physical_unavailable_person_refs(_released_after_read))
        for ref in sorted(released_by_captor_death-blocked):
            try:_p,_r,_o,person=self._person(ref)
            except CommandRejectedError:continue
            if person.get('health',{}).get('status')=='dead':continue
            fid=str(person.get('faction_ref') or '')
            if not fid:continue
            fpath=_faction_path(fid); rpath=_roster_path(fid)
            faction_override=out.get(fpath); roster_override=out.get(rpath)
            try:rfp,rf,rrp,rr=self._resume_institutional_training_now([ref],current_time,faction_override=faction_override if isinstance(faction_override,Mapping) else None,roster_override=roster_override if isinstance(roster_override,Mapping) else None)
            except CommandRejectedError:continue
            out[rfp]=rf; out[rrp]=rr

        # Immediate exact-combat deaths close institutional existence in the
        # same transaction. A last-member kill cannot leave a ghost faction
        # waiting for a later scheduler frontier to notice the empty roster.
        relations_state=copy.deepcopy(out.get(_RELATIONS) or self.repository.read_json(_RELATIONS))
        def _load_current_faction(fid:str):
            path=_faction_path(fid)
            if path in out and isinstance(out[path],Mapping):
                return path, hydrate_faction_state(out[path])
            stored_path, stored=read_faction(self.repository,fid)
            return stored_path, hydrate_faction_state(stored)
        extinction=settle_extinctions_from_touched_rosters(
            read_json=self.repository.read_json,writes=out,relations_state=relations_state,
            load_faction=_load_current_faction,relations_path=_RELATIONS,
        )
        if extinction.get('extinct_refs'):
            out[_RELATIONS]=extinction['relations']
            custody_after, extinct_releases = release_custody_held_by_extinct_factions(
                out.get(_CUSTODY, custody), extinct_refs=extinction['extinct_refs'],
            )
            if extinct_releases:
                out[_CUSTODY] = custody_after
                def _extinct_release_read(path: str):
                    return copy.deepcopy(out[path]) if path in out else self.repository.read_json(path)
                released_commitments = derived_commitment_state(_extinct_release_read)
                released_index = released_commitments.get('person_index', {}) if isinstance(released_commitments, Mapping) else {}
                released_blocked = set(str(ref) for ref in released_index) if isinstance(released_index, Mapping) else set()
                released_blocked.update(physical_unavailable_person_refs(_extinct_release_read))
                for released in extinct_releases:
                    ref = released['person_ref']
                    if ref in released_blocked:
                        continue
                    try:
                        _p,_r,_o,person=self._person(ref)
                    except CommandRejectedError:
                        continue
                    if person.get('health',{}).get('status')=='dead':
                        continue
                    fid=str(person.get('faction_ref') or '')
                    if not fid:
                        continue
                    fpath=_faction_path(fid); rpath=_roster_path(fid)
                    try:
                        rfp,rf,rrp,rr=self._resume_institutional_training_now(
                            [ref], current_time,
                            faction_override=out.get(fpath) if isinstance(out.get(fpath),Mapping) else None,
                            roster_override=out.get(rpath) if isinstance(out.get(rpath),Mapping) else None,
                        )
                    except CommandRejectedError:
                        continue
                    out[rfp]=rf; out[rrp]=rr
        return out

    def _jianghu_medicine_resolution(self,command:CommandEnvelope,meta:Mapping[str,Any],current_time:CampaignTime):
        action=str(command.payload.get('action') or '')
        if action=='qi_purge':
            expected={'action','subject_ref','poison_ref','duration_minutes'}
            if set(command.payload)!=expected:raise CommandRejectedError('jianghu_medicine_resolution_payload_fields_invalid')
            subject_ref=str(command.payload['subject_ref']); poison_ref=str(command.payload['poison_ref']); minutes=int(command.payload['duration_minutes'])
            if subject_ref!=command.actor_id:raise CommandRejectedError('jianghu_qi_purge_self_only')
            if minutes<=0 or minutes>720:raise CommandRejectedError('jianghu_qi_purge_duration_invalid')
            rpath,roster,_ordinal,subject=self._person(subject_ref)
            self._require_person_available_for_activity(subject_ref)
            health=subject.get('health',{}) if isinstance(subject.get('health'),Mapping) else {}
            if health.get('status') in {'dead','incapacitated'}:raise CommandRejectedError('jianghu_qi_purge_subject_unusable')
            burdens=subject.get('poison_burdens',{}) if isinstance(subject.get('poison_burdens'),Mapping) else {}
            pending=subject.get('pending_poison_burdens',{}) if isinstance(subject.get('pending_poison_burdens'),Mapping) else {}
            burden=max(0,int(combined_poison_burdens(burdens,pending).get(poison_ref,0)))
            if burden<=0:raise CommandRejectedError('jianghu_qi_purge_no_burden')
            result=active_qi_purge(poison_ref=poison_ref,burden=burden,current_qi_milli=person_current_qi_milli(subject),qi=int(subject.get('qi',0)),qi_control=int(subject.get('qi_control',0)),elapsed_minutes=minutes)
            time_plan,extra,_target=self._timed_person_activity_plan(
                command,meta,current_time,person_refs=[subject_ref],seconds=minutes*60,
                activity_ref=f'qi-detox:{command.request_id}',activity_kind='qi_detox',
                owner_ref=subject_ref,location_ref=_effective_location(self, subject_ref, subject),
            )
            final_roster=copy.deepcopy(dict(extra.get(rpath,roster)))
            rows=final_roster.get('people',[]) if isinstance(final_roster,Mapping) else []
            idx=next((i for i,row in enumerate(rows) if isinstance(row,Mapping) and row.get('person_id')==subject_ref),None)
            if idx is None:raise CommandRejectedError('jianghu_qi_purge_subject_missing_after_time')
            final_person=copy.deepcopy(dict(rows[idx]))
            final_burdens=final_person.get('poison_burdens',{}) if isinstance(final_person.get('poison_burdens'),Mapping) else {}
            final_pending=final_person.get('pending_poison_burdens',{}) if isinstance(final_person.get('pending_poison_burdens'),Mapping) else {}
            cleared_state=clear_poison_burden(
                active=final_burdens,pending=final_pending,poison_ref=poison_ref,
                amount=int(result['burden_cleared']),
            )
            if cleared_state['active_after']:final_person['poison_burdens']=cleared_state['active_after']
            else:final_person.pop('poison_burdens',None)
            if cleared_state['pending_after']:final_person['pending_poison_burdens']=cleared_state['pending_after']
            else:final_person.pop('pending_poison_burdens',None)
            set_person_current_qi_milli(final_person,int(result['current_qi_milli_after']))
            final_roster=set_roster_person(final_roster,idx,final_person); extra[rpath]=final_roster
            final_burden_after=int(cleared_state['active_burden_after'])+int(cleared_state['pending_burden_after'])
            return self._combine_time_plan(command,time_plan,extra_records=extra,code='jianghu_qi_purge_completed',result={'command_type':command.command_type,'subject_ref':subject_ref,'poison_ref':poison_ref,'burden_before':result['burden_before'],'burden_after':final_burden_after,'qi_spent':result['qi_spent'],'elapsed_minutes':minutes})

        if set(command.payload)!={'action','subject_ref','medicine_ref','faction_ref'}:raise CommandRejectedError('jianghu_medicine_resolution_payload_fields_invalid')
        if action!='administer':raise CommandRejectedError('jianghu_medicine_action_invalid')
        subject_ref=str(command.payload['subject_ref']); medicine_ref=str(command.payload['medicine_ref']); faction_ref=str(command.payload['faction_ref'])
        _apath,_,_,actor=self._person(command.actor_id); rpath,roster,ordinal,subject=self._person(subject_ref)
        if actor.get('faction_ref')!=faction_ref:raise CommandRejectedError('jianghu_medicine_faction_mismatch')
        subject_health=subject.get('health',{}) if isinstance(subject.get('health'),Mapping) else {}
        if subject_health.get('status')=='dead':raise CommandRejectedError('jianghu_medicine_subject_dead')
        self._require_person_available_for_activity(command.actor_id)
        if subject_ref in self._active_combat_person_refs():raise CommandRejectedError('jianghu_medicine_subject_in_active_combat')
        _fp,faction=read_faction(self.repository,faction_ref); facility_site=str(faction.get('local_site_ref') or '')
        if infirmary_capacity(faction.get('buildings',{}),faction.get('infrastructure',{})).get('treatment_stations',0)<=0:raise CommandRejectedError('jianghu_infirmary_treatment_capacity_unavailable')
        if facility_site and _effective_location(self, command.actor_id, actor)!=facility_site:raise CommandRejectedError('jianghu_medicine_requires_faction_infirmary')
        if subject_ref!=command.actor_id and not self._same_effective_location(command.actor_id,subject_ref):raise CommandRejectedError('jianghu_medicine_subject_not_present')
        invpath=_inventory_path(faction_ref); inventory=copy.deepcopy(self.repository.read_json(invpath)); medicines=inventory.get('medicines',{})
        try: category=medicine_category(medicine_ref); result=administer_dose(medicine_ref,at=str(current_time).removeprefix('SE-'),inventory=medicines,person_state=subject.get('medicine_state'))
        except (KeyError,ValueError) as exc:raise CommandRejectedError('jianghu_medicine_unavailable_or_invalid') from exc
        inventory['medicines']=result['inventory_after']; subject['medicine_state']=result['medicine_state_after']
        aattrs=actor.get('attributes',{}) if isinstance(actor.get('attributes'),Mapping) else {}; apro=actor.get('professional_skills',{}) if isinstance(actor.get('professional_skills'),Mapping) else {}
        sattrs=subject.get('attributes',{}) if isinstance(subject.get('attributes'),Mapping) else {}
        equipment=inventory.setdefault('equipment',{}); kit_available=int(equipment.get('tool_physicians_kit',0))>0; supply_available=int(equipment.get('supply_medical_bundle',0))>0
        dx=diagnosis_score(medicine=int(apro.get('medicine',0)),intelligence=int(aattrs.get('intelligence',0)),perception=int(aattrs.get('perception',0)),examination_minutes=10,tool_available=kit_available,environment_milli=1000+max(0,int(faction.get('buildings',{}).get('infirmary_apothecary',0)))*40)
        treatment=None
        if category in {'wound','bone','internal'}:
            treatment=wound_treatment_score(medicine=int(apro.get('medicine',0)),dexterity=int(aattrs.get('dexterity',0)),intelligence=int(aattrs.get('intelligence',0)),perception=int(aattrs.get('perception',0)),physician_kit=kit_available,medical_supply=supply_available,environment_milli=1000+max(0,int(faction.get('buildings',{}).get('infirmary_apothecary',0)))*40,treatment_minutes=30,patient_condition_milli=1000)
            subject['health']=stabilize_wounds(subject_health,treatment_score_value=int(treatment['treatment_score']),advanced_procedure_enabled=bool(treatment['advanced_procedure_enabled']),medical_supply_available=bool(treatment['medical_supply_available']))
            if supply_available:equipment['supply_medical_bundle']=int(equipment.get('supply_medical_bundle',0))-1
        elif category=='antidote':
            burdens=subject.get('poison_burdens',{}) if isinstance(subject.get('poison_burdens'),Mapping) else {}
            pending=subject.get('pending_poison_burdens',{}) if isinstance(subject.get('pending_poison_burdens'),Mapping) else {}
            combined=combined_poison_burdens(burdens,pending)
            candidates=[]
            for pref,b in combined.items():
                if int(b)<=0:continue
                try:tr=treat_poison_burden(burden=int(b),medicine=int(apro.get('medicine',0)),intelligence=int(aattrs.get('intelligence',0)),perception=int(aattrs.get('perception',0)),poison_ref=str(pref),medicine_ref=medicine_ref,patient_endurance=int(sattrs.get('endurance',0)),patient_qi=int(subject.get('qi',0)),patient_qi_control=int(subject.get('qi_control',0)),facility_level=int(faction.get('buildings',{}).get('infirmary_apothecary',0)),treatment_minutes=20)
                except KeyError:continue
                candidates.append((int(tr['burden_cleared']),str(pref),tr))
            if candidates:
                _cleared,pref,treatment=max(candidates,key=lambda x:(x[0],x[1]))
                cleared_state=clear_poison_burden(active=burdens,pending=pending,poison_ref=pref,amount=int(treatment['burden_cleared']))
                if cleared_state['active_after']:subject['poison_burdens']=cleared_state['active_after']
                else:subject.pop('poison_burdens',None)
                if cleared_state['pending_after']:subject['pending_poison_burdens']=cleared_state['pending_after']
                else:subject.pop('pending_poison_burdens',None)
        inventory['equipment']=equipment
        consequences=toxicity_consequences(subject['medicine_state'],at=str(current_time).removeprefix('SE-'))
        subject['fatigue_milli']=max(0,int(subject.get('fatigue_milli',0))+int(consequences['fatigue_burden_points']))
        health=copy.deepcopy(subject.get('health',{})); health['toxicity_milli']=int(consequences['toxicity_milli']); health['shock']=max(int(health.get('shock',0)),int(consequences['shock_contribution']))
        if int(consequences['organ_stress'])>0:
            injury={'zone':'abdomen','created_at':str(current_time).removeprefix('SE-'),'cut':0,'pierce':0,'blunt':0,'penetration':0,'severity':min(200,int(consequences['organ_stress'])),'bleeding_ml_per_min':0,'fracture':0,'tendon_damage':0,'nerve_damage':0,'organ_trauma':min(200,int(consequences['organ_stress'])),'pain':min(200,int(consequences['organ_stress'])//2),'function_loss_pct':min(100,int(consequences['organ_stress'])//3),'treated':False,'healing_progress_milli':0}; health['injuries']=record_current_wound(health.get('injuries',[]) if isinstance(health.get('injuries'),list) else [],injury)
        subject['health']=health; staged_roster=set_roster_person(copy.deepcopy(roster),ordinal,subject)
        time_plan,extra,_target=self._timed_person_activity_plan(
            command,meta,current_time,person_refs=[command.actor_id],seconds=60,
            activity_ref=f'medicine:{command.request_id}',activity_kind='medical_treatment',
            owner_ref=faction_ref,location_ref=_effective_location(self, command.actor_id, actor),
            staged_records={rpath:staged_roster,invpath:inventory},allow_unusable_refs=[subject_ref],
        )
        if subject_ref != command.actor_id:
            social_before=self._time_after_record(time_plan,_SOCIAL,self.repository.read_json(_SOCIAL))
            social_event=apply_relationship_event(
                social_before,observer_ref=subject_ref,subject_ref=command.actor_id,
                event_kind='treatment',observer_knows=True,severity_milli=1000,
                protected_player_ref=str(meta.get('player_id') or 'pc_wei_tang'),
            )
            extra[_SOCIAL]=social_event['state_after']
        return self._combine_time_plan(command,time_plan,extra_records=extra,code='jianghu_medicine_administered',result={'command_type':command.command_type,'subject_ref':subject_ref,'medicine_ref':medicine_ref,'effect_multiplier_milli':result['effect_multiplier_milli'],'toxicity_status':result['toxicity_status'],'diagnosis':dx,'treatment':treatment})

    def _jianghu_library_research_resolution(self,command:CommandEnvelope,meta:Mapping[str,Any],current_time:CampaignTime):
        record_ref=str(command.payload.get('record_ref') or '')
        minutes=int(command.payload.get('minutes',0) or 0)
        if not record_ref or minutes<=0 or minutes>720:raise CommandRejectedError('jianghu_library_research_payload_invalid')
        self._require_person_available_for_activity(command.actor_id)
        rpath,roster,idx,actor=self._person(command.actor_id);faction_ref=str(actor.get('faction_ref') or '')
        _fp,faction=read_faction(self.repository,faction_ref)
        if _effective_location(self, command.actor_id, actor)!=str(faction.get('local_site_ref') or ''):raise CommandRejectedError('jianghu_library_research_requires_faction_library')
        level=int(faction.get('buildings',{}).get('library_records',0))
        if level<=0:raise CommandRejectedError('jianghu_library_unavailable')
        holdings=faction.get('holdings',{}) if isinstance(faction.get('holdings'),Mapping) else {}
        held={str(x) for x in holdings.get('record_refs',[]) if isinstance(x,str)} if isinstance(holdings.get('record_refs'),list) else set()
        attrs=actor.get('attributes',{}) if isinstance(actor.get('attributes'),Mapping) else {};prof=actor.get('professional_skills',{}) if isinstance(actor.get('professional_skills'),Mapping) else {}
        topic_row=self.repository.read_json(_LIBRARY_CATALOG).get('records',{}).get(record_ref,{})
        topic=str(topic_row.get('topic') or '') if isinstance(topic_row,Mapping) else ''
        relevant=int(prof.get('medicine',0)) if topic=='medicine' else max(int(prof.get('administration',0)),int(prof.get('commerce',0)))
        try:result=research_record(record_ref=record_ref,catalog=self.repository.read_json(_LIBRARY_CATALOG),held_refs=held,intelligence=int(attrs.get('intelligence',0)),relevant_skill=relevant,library_level=level,infrastructure=faction.get('infrastructure',{}),minutes=minutes)
        except (KeyError,ValueError) as exc:raise CommandRejectedError('jianghu_library_record_unavailable') from exc
        time_plan,extra,_target=self._timed_person_activity_plan(
            command,meta,current_time,person_refs=[command.actor_id],seconds=minutes*60,
            activity_ref=f'research:{command.request_id}',activity_kind='library_research',owner_ref=faction_ref,location_ref=_effective_location(self, command.actor_id, actor),
        )
        return self._combine_time_plan(command,time_plan,extra_records=extra,code='jianghu_library_research_completed',result={'command_type':command.command_type,**result})

    def _jianghu_security_resolution(self,command:CommandEnvelope,meta:Mapping[str,Any],current_time:CampaignTime):
        action=str(command.payload.get('action') or '')
        target_faction_ref=str(command.payload.get('target_faction_ref') or '')
        if action not in {'infiltrate','force_entry','repair_breach'} or not target_faction_ref:
            raise CommandRejectedError('jianghu_security_payload_invalid')
        self._require_person_available_for_activity(command.actor_id)
        arpath,aroster,aidx,actor=self._person(command.actor_id)
        tfpath,target_faction=read_faction(self.repository,target_faction_ref)
        actor_faction_ref=str(actor.get('faction_ref') or '')
        sites_data=self.repository.read_json(_LOCAL_SITES);sites=sites_data.get('sites',{}) if isinstance(sites_data,Mapping) else {}

        if action=='repair_breach':
            if actor_faction_ref!=target_faction_ref: raise CommandRejectedError('jianghu_security_repair_requires_controller')
            target_site=_effective_location(self, command.actor_id, actor)
            if not target_site: raise CommandRejectedError('jianghu_security_site_unresolved')
            controlled=(target_site==str(target_faction.get('local_site_ref') or '') or target_site in (target_faction.get('controlled_estates',{}) if isinstance(target_faction.get('controlled_estates'),Mapping) else {}))
            if not controlled: raise CommandRejectedError('jianghu_security_repair_requires_controlled_site')
            buildings=buildings_at_site(target_faction,target_site); condition=site_condition(target_faction,target_site)
            quote=breach_repair_quote(buildings,condition)
            if int(quote.get('labor_hours',0))<=0: raise CommandRejectedError('jianghu_security_no_breach_to_repair')
            skills=actor.get('professional_skills',{}) if isinstance(actor.get('professional_skills'),Mapping) else {}
            repair_skill=max(0,int(skills.get('crafting',0)),int(skills.get('administration',0)))
            if repair_skill < int(quote.get('required_crafting_or_administration',0)):
                raise CommandRejectedError('jianghu_security_repair_skill_insufficient')
            ipath=_inventory_path(target_faction_ref); inv=copy.deepcopy(self.repository.read_json(ipath)); mats=inv.get('raw_materials',{}) if isinstance(inv.get('raw_materials'),Mapping) else {}
            mats=copy.deepcopy(dict(mats)); required=quote.get('materials',{}) if isinstance(quote.get('materials'),Mapping) else {}
            for ref,qty in required.items():
                if int(mats.get(ref,0))<int(qty): raise CommandRejectedError('jianghu_security_repair_materials_unavailable')
            for ref,qty in required.items(): mats[ref]=int(mats.get(ref,0))-int(qty)
            inv['raw_materials']=mats
            seconds=max(3600,int(quote['labor_hours'])*3600)
            time_plan,extra,_target=self._timed_person_activity_plan(
                command,meta,current_time,person_refs=[command.actor_id],seconds=seconds,
                activity_ref=f'breach-repair:{command.request_id}',activity_kind='breach_repair',owner_ref=target_faction_ref,location_ref=target_site,
                staged_records=self._scene_transition_records(at=str(current_time), reason='player_left'),
            )
            extra[ipath]=inv
            repaired=set_site_condition(target_faction,target_site,None); extra[tfpath]=compact_faction_state(repaired)
            return self._combine_time_plan(command,time_plan,extra_records=extra,code='jianghu_security_breach_repaired',result={'command_type':command.command_type,'action':action,'target_faction_ref':target_faction_ref,'site_ref':target_site,'repair':quote})

        target_site=str(target_faction.get('local_site_ref') or '')
        if not target_site:raise CommandRejectedError('jianghu_security_target_compound_missing')
        actor_location=_effective_location(self, command.actor_id, actor); actor_site=sites.get(actor_location) if isinstance(sites,Mapping) else None
        compound=sites.get(target_site) if isinstance(sites,Mapping) else None
        if not isinstance(actor_site,Mapping) or not isinstance(compound,Mapping):raise CommandRejectedError('jianghu_security_site_unresolved')
        actor_place=str(actor_site.get('parent_place_ref') or '');target_place=str(compound.get('parent_place_ref') or '')
        if not actor_place or actor_place!=target_place:raise CommandRejectedError('jianghu_security_requires_same_settlement')
        if actor_location==target_site:raise CommandRejectedError('jianghu_security_actor_already_inside')
        target_buildings=buildings_at_site(target_faction,target_site); target_infrastructure=infrastructure_at_site(target_faction,target_site); condition=site_condition(target_faction,target_site)
        trpath=_roster_path(target_faction_ref);troster=self.repository.read_json(trpath);tpeople=troster.get('people',[]) if isinstance(troster,Mapping) else []
        if not isinstance(tpeople,list):raise CommandRejectedError('jianghu_roster_invalid')
        guards=select_watch_guards(
            [p for p in tpeople if isinstance(p,Mapping)],faction_ref=target_faction_ref,at=_dt(current_time),
            buildings=target_buildings,infrastructure=target_infrastructure,unavailable_refs=self._unavailable_person_refs(),
            threat_milli=int(target_faction.get('security_threat_milli',500) or 500),
        )
        weather=weather_snapshot(world_seed=str(meta.get('world_seed') or 'jianghu-world'),at=_dt(current_time),place_id=target_place)
        env=combat_environment(terrain='urban',zone_ref=target_site,seed_ref=f'{target_faction_ref}|{target_site}|{current_time.year}-{current_time.month}-{current_time.day}',weather=weather)
        equipment_catalog=self.repository.read_json(_EQUIPMENT_DATA)
        if action=='infiltrate':
            outcome=infiltration_resolution(
                intruder=actor,guards=guards,buildings=target_buildings,infrastructure=target_infrastructure,
                lighting_milli=550 if current_time.hour<6 or current_time.hour>=20 else 1000,
                weather_visibility_milli=int(env.get('visibility_milli',1000)),concealment_milli=int(env.get('concealment_milli',0)),
            )
            attempt_seconds=max(20,int(outcome['climb_seconds']))
            detected=not bool(outcome['success']) and bool(guards)
        else:
            loadout=effective_person_loadout(self.repository.read_json(_EQUIPMENT),command.actor_id)
            impact=0
            for ref,qty in loadout.get('items',{}).items():
                if int(qty)<=0:continue
                row=resolve_equipment_item(equipment_catalog,str(ref))
                if isinstance(row,Mapping):impact=max(impact,int(float(row.get('mass_kg',0))*20)+int(row.get('reach_mm',0) or 0)//200)
            outcome=forced_entry_resolution(intruder=actor,buildings=target_buildings,infrastructure=target_infrastructure,weapon_impact=impact,structure_condition=condition)
            response=alarm_response_seconds(target_buildings,guards,infrastructure=target_infrastructure)
            detected=bool(guards) and response<int(outcome['breach_seconds'])
            attempt_seconds=response if detected else int(outcome['breach_seconds'])
            outcome['alarm_response_seconds']=response
            outcome['success']=not detected
            outcome['guard_refs']=[str(g.get('person_id')) for g in guards if isinstance(g.get('person_id'),str)]

        time_plan,extra,target_time=self._timed_person_activity_plan(
            command,meta,current_time,person_refs=[command.actor_id],seconds=max(1,attempt_seconds),
            activity_ref=f'security:{command.request_id}',activity_kind='infiltration' if action=='infiltrate' else 'forced_entry',
            owner_ref=command.actor_id,location_ref=_effective_location(self, command.actor_id, actor),
            staged_records=self._scene_transition_records(at=str(current_time), reason='player_left'),
        )
        final_actor_roster=copy.deepcopy(dict(extra.get(arpath) or self._time_after_record(time_plan,arpath,aroster)))
        rows=final_actor_roster.get('people',[]) if isinstance(final_actor_roster,Mapping) else []
        if not isinstance(rows,list):raise CommandRejectedError('jianghu_roster_invalid')
        idx=next((i for i,p in enumerate(rows) if isinstance(p,Mapping) and p.get('person_id')==command.actor_id),None)
        if idx is None:raise CommandRejectedError('jianghu_person_unresolved')
        scene=copy.deepcopy(self._time_after_record(time_plan,self.scene_path,self.repository.read_json(self.scene_path)))
        if bool(outcome.get('success')):
            updated=copy.deepcopy(dict(rows[idx]));updated['location_ref']=target_site;rows[idx]=updated
            extra[arpath]=final_actor_roster
            if action=='force_entry':
                damaged=set_site_condition(target_faction,target_site,{'walls_gate_integrity_milli':0,'walls_gate_breached':True,'last_damage_at':str(target_time).removeprefix('SE-')})
                extra[tfpath]=compact_faction_state(damaged)
            scene['location_id']=target_site;scene['present_person_ids']=[command.actor_id];scene['visible_person_ids']=[command.actor_id]
            return self._combine_time_plan(command,time_plan,extra_records=extra,code='jianghu_security_entry_succeeded',result={'command_type':command.command_type,'action':action,'target_faction_ref':target_faction_ref,'outcome':outcome},scene_override=scene)

        # Every real on-duty guard remains part of the contact. The gate frontage
        # determines who can engage immediately; the rest arrive on the same
        # exact combat timeline rather than disappearing behind a hard cap.
        guard_refs=[str(g.get('person_id')) for g in guards if isinstance(g.get('person_id'),str)]
        if not guard_refs:
            return self._combine_time_plan(command,time_plan,extra_records=extra,code='jianghu_security_attempt_failed',result={'command_type':command.command_type,'action':action,'target_faction_ref':target_faction_ref,'outcome':outcome},scene_override=scene)
        final_target_roster=self._time_after_record(time_plan,trpath,troster)
        final_people=final_target_roster.get('people',[]) if isinstance(final_target_roster,Mapping) else []
        guard_map={str(p.get('person_id')):p for p in final_people if isinstance(p,Mapping) and str(p.get('person_id') or '') in guard_refs}
        actor_after=rows[idx]
        alive_guards=[r for r in guard_refs if r in guard_map and guard_map[r].get('health',{}).get('status') not in {'dead','incapacitated'}]
        if not alive_guards:
            return self._combine_time_plan(command,time_plan,extra_records=extra,code='jianghu_security_attempt_failed',result={'command_type':command.command_type,'action':action,'target_faction_ref':target_faction_ref,'outcome':outcome},scene_override=scene)
        combat_ref=f'security.{hashlib.sha256((command.request_id+target_faction_ref).encode()).hexdigest()[:20]}'
        combats_state=copy.deepcopy(self._time_after_record(time_plan,_COMBATS,self.repository.read_json(_COMBATS)))
        combats=combats_state.setdefault('combats',{})
        people={command.actor_id:actor_after,**{r:guard_map[r] for r in alive_guards}}
        immediate=max(1,min(len(alive_guards),max(2,int(env.get('frontage_m',8))//3)))
        wave=max(15000,alarm_response_seconds(target_buildings,alive_guards,infrastructure=target_infrastructure)*1000)
        reinforcement_delays={ref:max(0,(i-immediate+1))*wave for i,ref in enumerate(alive_guards) if i>=immediate}
        combat=initialize_combat(
            combat_ref=combat_ref,side_a_refs=[command.actor_id],side_b_refs=alive_guards,people=people,
            zone_ref=target_site,started_at=str(target_time).removeprefix('SE-'),objective={'kind':'escape_or_subdue','target_refs':alive_guards},
            awareness_mode='mutual',initial_range_band=1,equipment_ledger=self._time_after_record(time_plan,_EQUIPMENT,self.repository.read_json(_EQUIPMENT)),
            environment=env,reinforcement_delays_ms=reinforcement_delays,
        )
        combats[combat_ref]=combat;extra[_COMBATS]=combats_state
        visible_guards=alive_guards[:immediate]
        scene['location_id']=target_site;scene['active_combat_ref']=combat_ref;scene['present_person_ids']=[command.actor_id,*alive_guards];scene['visible_person_ids']=[command.actor_id,*visible_guards]
        return self._combine_time_plan(command,time_plan,extra_records=extra,code='jianghu_security_detected_contact',result={'command_type':command.command_type,'action':action,'target_faction_ref':target_faction_ref,'outcome':outcome,'combat_ref':combat_ref,'guard_refs':alive_guards,'immediate_guard_refs':visible_guards},scene_override=scene)

    def _jianghu_market_trade_resolution(self,command:CommandEnvelope,meta:Mapping[str,Any],current_time:CampaignTime):
        action=str(command.payload.get('action') or ''); item_ref=str(command.payload.get('item_ref') or ''); quantity=int(command.payload.get('quantity',0)); payer=str(command.payload.get('payer') or '')
        faction_ref=command.payload.get('faction_ref')
        if action not in {'buy','sell'} or quantity<=0 or payer not in {'personal','house'}:raise CommandRejectedError('jianghu_market_trade_payload_invalid')
        _actor_path,_actor_roster,_actor_ordinal,actor=self._person(command.actor_id); self._require_person_available_for_activity(command.actor_id)
        site_ref=_effective_location(self, command.actor_id, actor)
        sites_data=self.repository.read_json(_LOCAL_SITES); sites=sites_data.get('sites',{}) if isinstance(sites_data,Mapping) else {}; site=sites.get(site_ref) if isinstance(sites,Mapping) else None
        if not isinstance(site,Mapping) or str(site.get('site_type') or '') not in {'market','weapon_shop','apothecary'}:raise CommandRejectedError('jianghu_market_trade_requires_market_site')
        place_ref=str(site.get('parent_place_ref') or '')
        try:region=region_for_place(place_ref)
        except KeyError as exc:raise CommandRejectedError('jianghu_market_region_unresolved') from exc
        market_path=f'state/martial-world/markets/{region}.json'; market=self.repository.read_json(market_path)
        if int(market.get('stock',{}).get(item_ref,0))<0:raise CommandRejectedError('jianghu_market_item_invalid')
        equipment_catalog=self.repository.read_json(_EQUIPMENT_DATA); physical_item=resolve_equipment_item(equipment_catalog,item_ref)
        writes={}; ledger=hydrate_equipment_ledger(self.repository.read_json(_EQUIPMENT)); loadouts=ledger.setdefault('person_loadouts',{})
        if payer=='personal':
            if physical_item is None:raise CommandRejectedError('jianghu_personal_trade_item_not_portable')
            rpath,roster,ordinal,actor=self._person(command.actor_id); cash=max(0,int(actor.get('personal_cash',0))); load=copy.deepcopy(loadouts.get(command.actor_id,{'items':{},'condition_milli':{}})); items=load.setdefault('items',{}); cond=load.setdefault('condition_milli',{})
            held=max(0,int(items.get(item_ref,0)))
            try:
                if action=='buy':
                    result=execute_purchase(region,item_ref,quantity,market,buyer_cash=cash); actor['personal_cash']=int(result['buyer_cash_after']); items[item_ref]=held+quantity; cond.setdefault(item_ref,1000)
                else:
                    if personally_owned_quantity(ledger,command.actor_id,item_ref)<quantity:raise CommandRejectedError('jianghu_personal_trade_item_not_owned')
                    result=execute_sale(region,item_ref,quantity,market,seller_stock=held,seller_cash=cash); actor['personal_cash']=int(result['seller_cash_after']); remaining=int(result['seller_stock_after']);
                    if remaining>0:items[item_ref]=remaining
                    else:items.pop(item_ref,None);cond.pop(item_ref,None)
            except ValueError as exc:raise CommandRejectedError('jianghu_market_trade_unavailable') from exc
            loadouts[command.actor_id]=load; ledger['person_loadouts']=loadouts; roster=set_roster_person(roster,ordinal,actor)
            writes[rpath]=roster; writes[_EQUIPMENT]=compact_equipment_ledger(ledger); writes[market_path]=result['market_state_after']
            return self._simple_plan(command,meta,current_time,writes_records=writes,code=f'jianghu_market_{action}_completed',result={'command_type':command.command_type,'action':action,'payer':'personal','item_ref':item_ref,'quantity':quantity,'total_cash':int(result['quote']['total_price_cash']),'region_ref':region})

        # House-funded trade must be explicit and authorized.
        if not isinstance(faction_ref,str) or not faction_ref:raise CommandRejectedError('jianghu_house_trade_faction_required')
        _rp,_rr,_ro,actor=self._person(command.actor_id)
        if actor.get('faction_ref')!=faction_ref or not _office(actor,'leader','deputy_leader','chief_steward','treasurer','quartermaster'):raise CommandRejectedError('jianghu_house_trade_not_authorized')
        fpath,faction=read_faction(self.repository,faction_ref); ipath=_inventory_path(faction_ref); inv=copy.deepcopy(self.repository.read_json(ipath)); treasury=max(0,int(faction.get('treasury_cash',0)))
        if physical_item is not None:
            bucket=inv.setdefault('equipment',{}); held=max(0,int(bucket.get(item_ref,0)))
        elif item_ref=='food_ration_day':
            bucket=None; held=max(0,int(inv.get('food_ration_days',0)))
        else:
            bucket=inv.setdefault('raw_materials',{}); held=max(0,int(bucket.get(item_ref,0)))
        try:
            if action=='buy':
                result=execute_purchase(region,item_ref,quantity,market,buyer_cash=treasury); faction['treasury_cash']=int(result['buyer_cash_after']); newqty=held+quantity
            else:
                result=execute_sale(region,item_ref,quantity,market,seller_stock=held,seller_cash=treasury); faction['treasury_cash']=int(result['seller_cash_after']); newqty=int(result['seller_stock_after'])
        except ValueError as exc:raise CommandRejectedError('jianghu_market_trade_unavailable') from exc
        if item_ref=='food_ration_day':inv['food_ration_days']=newqty
        elif newqty>0:bucket[item_ref]=newqty
        else:bucket.pop(item_ref,None)
        if action=='buy':
            storage=storage_capacity_check(faction.get('buildings',{}),faction.get('infrastructure',{}),inv,equipment_catalog)
            if not storage['within_capacity']:raise CommandRejectedError('jianghu_storehouse_capacity_exceeded')
        writes[fpath]=faction;writes[ipath]=inv;writes[market_path]=result['market_state_after']
        return self._simple_plan(command,meta,current_time,writes_records=writes,code=f'jianghu_market_{action}_completed',result={'command_type':command.command_type,'action':action,'payer':'house','faction_ref':faction_ref,'item_ref':item_ref,'quantity':quantity,'total_cash':int(result['quote']['total_price_cash']),'region_ref':region})

    def _jianghu_property_transfer_resolution(self,command:CommandEnvelope,meta:Mapping[str,Any],current_time:CampaignTime):
        action=str(command.payload.get('action') or '')
        other_ref=str(command.payload.get('other_ref') or '')
        if action=='claim_extinct_estate':
            if not other_ref:
                raise CommandRejectedError('jianghu_estate_faction_ref_invalid')
            _arpath,_aroster,_aidx,actor=self._person(command.actor_id)
            claimant_ref=str(actor.get('faction_ref') or '')
            if not claimant_ref or not _office(actor,'leader','deputy_leader','field_commander','chief_steward'):
                raise CommandRejectedError('jianghu_estate_claim_not_authorized')
            try: target_path,target=read_faction(self.repository,other_ref)
            except (FileNotFoundError,KeyError,ValueError) as exc: raise CommandRejectedError('jianghu_estate_not_found') from exc
            if str(target.get('status') or '')!='extinct':
                raise CommandRejectedError('jianghu_estate_faction_not_extinct')
            if estate_claim_value_blockers(self.repository.read_json, other_ref):
                raise CommandRejectedError('jianghu_estate_claim_has_unresolved_value_obligations')
            registry=copy.deepcopy(self.repository.read_json('state/martial-world/faction-registry.json'))
            dormant=registry.get('dormant_estate_refs',[])
            if not isinstance(dormant,list) or other_ref not in dormant:
                raise CommandRejectedError('jianghu_estate_already_claimed')
            actor_location=_effective_location(self, command.actor_id, actor)
            estate_site=str(target.get('local_site_ref') or '')
            estate_place=str(target.get('headquarters') or '')
            sites=self.repository.read_json(_LOCAL_SITES).get('sites',{})
            actor_site=sites.get(actor_location) if isinstance(sites,Mapping) else None
            actor_place=str(actor_site.get('parent_place_ref') or actor_location) if isinstance(actor_site,Mapping) else actor_location
            if estate_site:
                if actor_location!=estate_site:
                    raise CommandRejectedError('jianghu_estate_claim_requires_physical_occupation')
            elif actor_place!=estate_place:
                raise CommandRejectedError('jianghu_estate_claim_requires_physical_occupation')
            claimant_path,claimant=read_faction(self.repository,claimant_ref)
            target_inv_path=_inventory_path(other_ref); claimant_inv_path=_inventory_path(claimant_ref)
            target_inv=copy.deepcopy(self.repository.read_json(target_inv_path)); claimant_inv=copy.deepcopy(self.repository.read_json(claimant_inv_path))
            captured_cash=max(0,int(target.get('treasury_cash',0)))
            target['treasury_cash']=0; claimant['treasury_cash']=max(0,int(claimant.get('treasury_cash',0)))+captured_cash
            target,claimant,moved_holdings=transfer_holdings(target,claimant)
            target=retire_organizational_scale(target)
            transferred={}
            food=max(0,int(target_inv.get('food_ration_days',0)))
            if food:
                claimant_inv['food_ration_days']=max(0,int(claimant_inv.get('food_ration_days',0)))+food
                target_inv['food_ration_days']=0; transferred['food_ration_days']=food
            for bucket_name in ('equipment','raw_materials','herbs','medicines','transport_capacity'):
                source=target_inv.get(bucket_name,{}) if isinstance(target_inv.get(bucket_name),Mapping) else {}
                dest=claimant_inv.setdefault(bucket_name,{})
                if not isinstance(dest,dict): raise CommandRejectedError('jianghu_estate_claim_inventory_invalid')
                moved=0
                for key,value in list(source.items()):
                    qty=max(0,int(value))
                    if qty:
                        dest[str(key)]=max(0,int(dest.get(str(key),0)))+qty; moved+=qty
                target_inv[bucket_name]={}
                if moved: transferred[bucket_name]=moved

            # A dormant institutional estate can include secondary compounds
            # acquired before extinction. Claimability is faction-wide, so the
            # final claim moves every physical estate exactly once rather than
            # removing the registry handle and orphaning remote property.
            estate_key=estate_site or estate_place
            incoming_estates:dict[str,dict[str,Any]]={}
            incoming_estates[estate_key]={
                'source_faction_ref':other_ref,
                'acquired_at':str(current_time).removeprefix('SE-'),
                'status':'occupied',
                'headquarters_place_ref':estate_place,
                'buildings':copy.deepcopy(dict(target.get('buildings',{}))) if isinstance(target.get('buildings'),Mapping) else {},
                'infrastructure':copy.deepcopy(dict(target.get('infrastructure',{}))) if isinstance(target.get('infrastructure'),Mapping) else {},
                'enterprises':copy.deepcopy(dict(target.get('enterprises',{}))) if isinstance(target.get('enterprises'),Mapping) else {},
            }
            target_secondary=target.get('controlled_estates',{}) if isinstance(target.get('controlled_estates'),Mapping) else {}
            for secondary_ref,secondary_row in target_secondary.items():
                if isinstance(secondary_ref,str) and secondary_ref and isinstance(secondary_row,Mapping):
                    incoming_estates[secondary_ref]=copy.deepcopy(dict(secondary_row))
            controlled=claimant.setdefault('controlled_estates',{})
            if not isinstance(controlled,dict):
                raise CommandRejectedError('jianghu_estate_claim_control_state_invalid')
            claimant_primary=str(claimant.get('local_site_ref') or '')
            collisions=[ref for ref in incoming_estates if ref==claimant_primary or ref in controlled]
            if collisions:
                raise CommandRejectedError('jianghu_estate_site_already_controlled')
            for incoming_ref in sorted(incoming_estates):
                controlled[incoming_ref]=incoming_estates[incoming_ref]
            target['buildings']={}; target['infrastructure']={}; target['enterprises']={}; target.pop('controlled_estates',None)

            target_conditions=target.get('site_conditions',{}) if isinstance(target.get('site_conditions'),Mapping) else {}
            claimant_conditions=claimant.setdefault('site_conditions',{}) if target_conditions else claimant.get('site_conditions')
            if target_conditions and not isinstance(claimant_conditions,dict):
                raise CommandRejectedError('jianghu_estate_claim_condition_state_invalid')
            remaining_conditions=copy.deepcopy(dict(target_conditions))
            if isinstance(claimant_conditions,dict):
                for incoming_ref in sorted(incoming_estates):
                    condition=target_conditions.get(incoming_ref) if isinstance(target_conditions,Mapping) else None
                    if not isinstance(condition,Mapping):
                        continue
                    if incoming_ref in claimant_conditions:
                        raise CommandRejectedError('jianghu_estate_claim_condition_state_invalid')
                    claimant_conditions[incoming_ref]=copy.deepcopy(dict(condition)); remaining_conditions.pop(incoming_ref,None)
            if remaining_conditions: target['site_conditions']=remaining_conditions
            else: target.pop('site_conditions',None)

            # Suspended physical work follows the site. No labor accrues while
            # the institution is extinct: the adoption frontier resets
            # last_progress_at and clears exact worker refs before the claimant
            # is allowed to restaff locally. Organizational scale projects do
            # not transfer because enterprise_scale itself is not physical loot.
            projects=copy.deepcopy(self.repository.read_json(_PROJECTS))
            project_rows=projects.get('projects',{}) if isinstance(projects,Mapping) else {}
            if not isinstance(project_rows,dict):
                raise CommandRejectedError('jianghu_estate_project_state_invalid')
            schedule=copy.deepcopy(self.repository.read_json(_SCHEDULE))
            one_off=schedule.get('one_off',{}) if isinstance(schedule,Mapping) else {}
            if not isinstance(one_off,dict):
                raise CommandRejectedError('jianghu_estate_project_schedule_invalid')
            at_iso=str(current_time).removeprefix('SE-')
            adopted_projects=[]; abandoned_organizational_projects=[]
            for project_ref,raw_project in list(project_rows.items()):
                if not isinstance(raw_project,Mapping) or str(raw_project.get('faction_ref') or '')!=other_ref or bool(raw_project.get('completed')):
                    continue
                ptype=str(raw_project.get('project_type') or '')
                event_id=f'autonomous_project_due:{project_ref}'
                if ptype=='enterprise_scale_expansion':
                    project_rows.pop(project_ref,None); one_off.pop(event_id,None); abandoned_organizational_projects.append(str(project_ref)); continue
                project=copy.deepcopy(dict(raw_project))
                site_ref=str(project.get('site_ref') or estate_key)
                if site_ref not in incoming_estates:
                    raise CommandRejectedError('jianghu_estate_project_site_invalid')
                for role in ('skilled','management','general'):
                    refs_key=f'{role}_worker_refs'; count_key=f'planned_{role}_worker_count'
                    refs=[str(x) for x in project.get(refs_key,[]) if isinstance(x,str) and x] if isinstance(project.get(refs_key),list) else []
                    project[count_key]=max(0,int(project.get(count_key,len(refs))))
                    project[refs_key]=[]
                project['faction_ref']=claimant_ref; project['site_ref']=site_ref; project['last_progress_at']=at_iso
                project['status']='staffing_required'; project.pop('suspended_reason',None); project.pop('suspended_at',None)
                project_rows[project_ref]=compact_project_state(project,project_ref=str(project_ref))
                one_off.pop(event_id,None)
                schedule=upsert_one_off_event(schedule,{
                    'event_id':event_id,'kind':'autonomous_project_due',
                    'due_at':(_dt(current_time)+timedelta(days=1)).isoformat(),
                    'owner_ref':str(project_ref),'requires_player_decision':False,
                })
                one_off=schedule.get('one_off',{})
                adopted_projects.append(str(project_ref))
            projects['projects']=project_rows; schedule['one_off']=one_off

            # Dormant-estate registry membership is the one claimability
            # authority. Provenance/control now lives on the claimant; the dead
            # institution needs no duplicate claimed-status receipt fields.
            try:
                property_transfer=transfer_faction_property_authority(
                    self.repository.read_json(_EQUIPMENT),source_faction_ref=other_ref,target_faction_ref=claimant_ref,
                )
            except ValueError as exc:
                raise CommandRejectedError('jianghu_estate_property_authority_conflict') from exc
            registry['dormant_estate_refs']=sorted(ref for ref in dormant if ref!=other_ref)
            writes={target_path:target,claimant_path:claimant,target_inv_path:target_inv,claimant_inv_path:claimant_inv,'state/martial-world/faction-registry.json':registry,_EQUIPMENT:property_transfer['equipment_ledger_after']}
            if adopted_projects or abandoned_organizational_projects:
                writes[_PROJECTS]=projects; writes[_SCHEDULE]=schedule
            return self._simple_plan(command,meta,current_time,writes_records=writes,code='jianghu_extinct_estate_claimed',result={
                'command_type':command.command_type,'estate_faction_ref':other_ref,'claimant_faction_ref':claimant_ref,
                'captured_cash':captured_cash,'transferred':transferred,'holdings_transfer':moved_holdings,
                'estate_site_ref':estate_key,'transferred_estate_site_refs':sorted(incoming_estates),
                'adopted_project_refs':sorted(adopted_projects),'abandoned_organizational_project_refs':sorted(abandoned_organizational_projects),
                'property_authority_transfer':{'claims':property_transfer['transferred_claim_count'],'recovery_demands':property_transfer['transferred_recovery_demand_count'],'policy_holders':property_transfer['materialized_policy_holder_count']},
                'captured_facility_count':sum(sum(1 for value in row.get('buildings',{}).values() if int(value)>0) for row in incoming_estates.values()),
            })
        if not other_ref or other_ref==command.actor_id:
            raise CommandRejectedError('jianghu_property_other_person_invalid')
        arpath,aroster,aidx,actor=self._person(command.actor_id)
        trpath,troster,tidx,target=self._person(other_ref)
        same_physical_place=self._same_effective_location(command.actor_id,other_ref)
        if not same_physical_place:
            raise CommandRejectedError('jianghu_property_target_not_physically_present')

        combats=self.repository.read_json(_COMBATS)
        custody=self.repository.read_json(_CUSTODY)
        custody_rows=custody.get('records',[]) if isinstance(custody,Mapping) and isinstance(custody.get('records'),list) else []
        basis=None
        if action.startswith('seize_'):
            basis=factual_restraint_basis(
                target=target,target_ref=other_ref,actor_ref=command.actor_id,
                combats=combats,existing_custody=custody_rows,
            )
            if not basis:
                raise CommandRejectedError('jianghu_property_seizure_requires_physical_control')

        if action in {'give_cash','seize_cash'}:
            amount=int(command.payload.get('cash',0) or 0)
            if amount<=0:
                raise CommandRejectedError('jianghu_property_cash_invalid')
            giver=actor if action=='give_cash' else target
            receiver=target if action=='give_cash' else actor
            if int(giver.get('personal_cash',0))<amount:
                raise CommandRejectedError('jianghu_property_cash_insufficient')
            giver['personal_cash']=int(giver.get('personal_cash',0))-amount
            receiver['personal_cash']=int(receiver.get('personal_cash',0))+amount
            if arpath==trpath:
                roster=copy.deepcopy(aroster)
                roster=set_roster_person(roster,aidx,actor)
                roster=set_roster_person(roster,tidx,target)
                writes={arpath:roster}
            else:
                writes={arpath:set_roster_person(copy.deepcopy(aroster),aidx,actor),trpath:set_roster_person(copy.deepcopy(troster),tidx,target)}
            result={'command_type':command.command_type,'action':action,'other_ref':other_ref,'cash':amount,'restraint_basis':basis}
            if action=='seize_cash':
                evidence_ref='property_event:'+hashlib.sha256(f'{command.request_id}|cash|{other_ref}|{amount}'.encode()).hexdigest()[:24]
                health=target.get('health',{}) if isinstance(target.get('health'),Mapping) else {}
                target_knows=health.get('status') not in {'dead','incapacitated'} and int(health.get('consciousness',100))>0
                result.update({'reportable_offense':'robbery','evidence_ref':evidence_ref,'witness_refs':[other_ref] if target_knows else []})
            return self._simple_plan(command,meta,current_time,writes_records=writes,code='jianghu_property_cash_transferred',result=result)

        if action not in {'give_item','seize_item','return_item'}:
            raise CommandRejectedError('jianghu_property_action_invalid')
        item_ref=str(command.payload.get('item_ref') or '')
        quantity=int(command.payload.get('quantity',0) or 0)
        if not item_ref or quantity<=0:
            raise CommandRejectedError('jianghu_property_item_invalid')
        catalog=self.repository.read_json(_EQUIPMENT_DATA)
        if resolve_equipment_item(catalog,item_ref) is None:
            raise CommandRejectedError('jianghu_property_item_not_physical')
        ledger=hydrate_equipment_ledger(self.repository.read_json(_EQUIPMENT))
        loads=ledger.setdefault('person_loadouts',{})
        from_ref=command.actor_id if action in {'give_item','return_item'} else other_ref
        to_ref=other_ref if action in {'give_item','return_item'} else command.actor_id
        from_load=copy.deepcopy(loads.get(from_ref,{'items':{},'condition_milli':{}}))
        to_load=copy.deepcopy(loads.get(to_ref,{'items':{},'condition_milli':{}}))
        from_items=from_load.setdefault('items',{}); to_items=to_load.setdefault('items',{})
        from_cond=from_load.setdefault('condition_milli',{}); to_cond=to_load.setdefault('condition_milli',{})
        held=max(0,int(from_items.get(item_ref,0)))
        if held<quantity:
            raise CommandRejectedError('jianghu_property_item_quantity_insufficient')

        claim=provenance_claim(ledger,from_ref,item_ref)
        claim_qty=max(0,int(claim.get('quantity',0))) if isinstance(claim,Mapping) else 0
        policy_qty=policy_owned_quantity(ledger,from_ref,item_ref)
        personal_qty=personally_owned_quantity(ledger,from_ref,item_ref)
        if action=='give_item':
            if personal_qty<quantity:
                raise CommandRejectedError('jianghu_property_gift_requires_personal_ownership')
        elif action=='return_item':
            if not isinstance(claim,Mapping) or claim_qty<quantity:
                raise CommandRejectedError('jianghu_property_return_requires_provenance')
            legal=str(claim.get('owner_ref') or '')
            target_faction=str(target.get('faction_ref') or '')
            if legal not in {other_ref,target_faction}:
                raise CommandRejectedError('jianghu_property_return_wrong_owner')
        else:
            ownership_classes=sum(1 for q in (claim_qty,policy_qty,personal_qty) if q>0)
            if ownership_classes>1 and quantity>max(claim_qty,policy_qty,personal_qty):
                raise CommandRejectedError('jianghu_property_mixed_ownership_split_required')

        remaining=held-quantity
        if remaining>0:from_items[item_ref]=remaining
        else:from_items.pop(item_ref,None);from_cond.pop(item_ref,None)
        to_items[item_ref]=max(0,int(to_items.get(item_ref,0)))+quantity
        if item_ref not in to_cond:to_cond[item_ref]=int(from_cond.get(item_ref,1000)) if item_ref in from_cond else 1000
        loads[from_ref]=from_load; loads[to_ref]=to_load; ledger['person_loadouts']=loads

        legal_owner=None
        evidence=None
        writes={}
        if action=='seize_item':
            if claim_qty>=quantity and isinstance(claim,Mapping):
                legal_owner=str(claim.get('owner_ref'))
            elif policy_qty>=quantity:
                policy_ref=assigned_policy(ledger,from_ref)
                policy=loadout_policy(policy_ref) if isinstance(policy_ref,str) else None
                legal_owner=str(policy.get('faction_ref')) if isinstance(policy,Mapping) and isinstance(policy.get('faction_ref'),str) else str(target.get('faction_ref') or from_ref)
            else:
                legal_owner=from_ref
            try:
                ledger=move_claim_after_seizure(ledger,from_holder=from_ref,to_holder=to_ref,item_ref=item_ref,quantity=quantity,original_owner_ref=legal_owner)
            except ValueError as exc:
                raise CommandRejectedError('jianghu_property_provenance_conflict') from exc
            evidence=property_evidence_ref(ledger,holder_ref=to_ref,item_ref=item_ref)
            health=target.get('health',{}) if isinstance(target.get('health'),Mapping) else {}
            target_knows=health.get('status') not in {'dead','incapacitated'} and int(health.get('consciousness',100))>0
            actor_faction=str(actor.get('faction_ref') or ''); target_faction=str(target.get('faction_ref') or '')
            if target_knows and actor_faction and target_faction and actor_faction!=target_faction:
                # A witnessed seizure creates one current recovery demand on
                # the same sparse property authority.  This is not a generic
                # crime-history record: returning/recovering the property
                # clears the demand.
                if evidence and legal_owner:
                    ledger=issue_recovery_demand(
                        ledger,owner_ref=str(legal_owner),holder_ref=to_ref,item_ref=item_ref,
                        quantity=quantity,issued_at=str(current_time).removeprefix('SE-'),
                        evidence_ref=evidence,
                        property_ref=(str(claim.get('property_ref')) if isinstance(claim,Mapping) and claim.get('property_ref') else None),
                    )
                relations=copy.deepcopy(self.repository.read_json(_RELATIONS)); edges=relations.setdefault('edges',[])
                prior=_relations_edge(relations,target_faction,actor_faction)
                changed=apply_relation_event(prior,from_faction=target_faction,to_faction=actor_faction,event_kind='property_seized')
                edges[:]=[e for e in edges if not (isinstance(e,Mapping) and e.get('from_faction')==target_faction and e.get('to_faction')==actor_faction)]
                edges.append(changed); writes[_RELATIONS]=relations
        elif action=='return_item':
            legal_owner=str(claim.get('owner_ref')) if isinstance(claim,Mapping) else ''
            remaining_claim=max(0,claim_qty-quantity)
            ledger=set_nonholder_claim(ledger,holder_ref=from_ref,item_ref=item_ref,owner_ref=legal_owner,quantity=remaining_claim,property_ref=(str(claim.get('property_ref')) if isinstance(claim,Mapping) and claim.get('property_ref') else None),status=str(claim.get('status') or 'seized') if isinstance(claim,Mapping) else 'seized')
            target_faction=str(target.get('faction_ref') or '')
            if legal_owner==target_faction:
                existing=provenance_claim(ledger,to_ref,item_ref)
                existing_qty=max(0,int(existing.get('quantity',0))) if isinstance(existing,Mapping) and existing.get('owner_ref')==legal_owner else 0
                ledger=set_nonholder_claim(ledger,holder_ref=to_ref,item_ref=item_ref,owner_ref=legal_owner,quantity=existing_qty+quantity,property_ref=(str(claim.get('property_ref')) if isinstance(claim,Mapping) and claim.get('property_ref') else None),status='recovered_custody')
            else:
                ledger=set_nonholder_claim(ledger,holder_ref=to_ref,item_ref=item_ref,owner_ref=to_ref,quantity=0)
            if legal_owner:
                ledger=clear_recovery_demand(ledger,owner_ref=legal_owner,holder_ref=from_ref,item_ref=item_ref)
            actor_faction=str(actor.get('faction_ref') or '')
            if actor_faction and target_faction and actor_faction!=target_faction:
                relations=copy.deepcopy(self.repository.read_json(_RELATIONS)); edges=relations.setdefault('edges',[])
                prior=_relations_edge(relations,target_faction,actor_faction)
                changed=apply_relation_event(prior,from_faction=target_faction,to_faction=actor_faction,event_kind='property_recovered')
                edges[:]=[e for e in edges if not (isinstance(e,Mapping) and e.get('from_faction')==target_faction and e.get('to_faction')==actor_faction)]
                edges.append(changed); writes[_RELATIONS]=relations
        else:
            legal_owner=to_ref
            ledger=set_nonholder_claim(ledger,holder_ref=to_ref,item_ref=item_ref,owner_ref=to_ref,quantity=0)

        writes[_EQUIPMENT]=compact_equipment_ledger(ledger)
        result={'command_type':command.command_type,'action':action,'other_ref':other_ref,'item_ref':item_ref,'quantity':quantity,'legal_owner_ref':legal_owner or to_ref,'restraint_basis':basis}
        if evidence:
            result.update({'reportable_offense':'theft','evidence_ref':evidence,'witness_refs':[other_ref] if target_knows else []})
            if target_knows:
                social_now=copy.deepcopy(self.repository.read_json(_SOCIAL))
                belief=record_belief(
                    social_now,observer_ref=other_ref,claim_ref=f'property-crime:{evidence}',
                    subject_ref=command.actor_id,claim_kind='property_crime_responsibility',
                    confidence_milli=950,stance='supports',source_ref=other_ref,evidence_ref=evidence,
                )
                writes[_SOCIAL]=belief['state_after']
            if target_knows and legal_owner:
                result['recovery_demand_ref']=f'recovery:{legal_owner}:{to_ref}:{item_ref}'
        return self._simple_plan(command,meta,current_time,writes_records=writes,code='jianghu_property_item_transferred',result=result)

    def _jianghu_production_resolution(self,command:CommandEnvelope,meta:Mapping[str,Any],current_time:CampaignTime):
        action=str(command.payload.get('action')); faction_ref=str(command.payload.get('faction_ref')); recipe_ref=str(command.payload.get('recipe_ref')); count=int(command.payload.get('count',0))
        if count<=0:raise CommandRejectedError('jianghu_production_count_invalid')
        _ap,_,_,actor=self._person(command.actor_id)
        if actor.get('faction_ref')!=faction_ref or not _office(actor,'leader','deputy_leader','chief_steward','treasurer','quartermaster','field_commander'):raise CommandRejectedError('jianghu_production_not_authorized')
        self._require_person_available_for_activity(command.actor_id)
        _fp,faction=read_faction(self.repository,faction_ref); invpath=_inventory_path(faction_ref); inv=copy.deepcopy(self.repository.read_json(invpath)); roster=self.repository.read_json(_roster_path(faction_ref)); people=roster.get('people',[])
        if not isinstance(people,list):raise CommandRejectedError('jianghu_roster_invalid')
        unavailable=self._unavailable_person_refs(); facility_site=str(faction.get('local_site_ref') or '')
        if facility_site and _effective_location(self, command.actor_id, actor)!=facility_site:raise CommandRejectedError('jianghu_production_requires_faction_facility')

        def qualified_workers(skill_key:str):
            rows=[]
            for raw in people:
                if not isinstance(raw,Mapping) or not isinstance(raw.get('person_id'),str):continue
                ref=str(raw['person_id'])
                if ref in unavailable:continue
                try:_rp,_rr,_ro,person=self._person(ref)
                except CommandRejectedError:continue
                health=person.get('health',{}) if isinstance(person.get('health'),Mapping) else {}
                if health.get('status') in {'dead','incapacitated'} or int(health.get('consciousness',100))<=0:continue
                if facility_site and _effective_location(self, ref, person)!=facility_site:continue
                rows.append((int(person.get('professional_skills',{}).get(skill_key,0)),ref,person))
            return sorted(rows,key=lambda row:(-row[0],row[1]))

        enterprises=faction.get('enterprises',{}) if isinstance(faction.get('enterprises'),Mapping) else {}
        enterprise_capacity=0
        enterprise_efficiency=1000

        if action=='workshop':
            enterprise_level=max(0,int(enterprises.get('crafting_workshop',0)))
            physical_slots=workshop_capacity(faction.get('buildings',{}),faction.get('infrastructure',{})).get('craft_workstations',0)
            organizational_slots=enterprise_scale_value(faction,'crafting_workshop') if enterprise_level>0 else 0
            enterprise_capacity=min(max(0,int(physical_slots)),max(0,int(organizational_slots)))
            if enterprise_capacity<=0:raise CommandRejectedError('jianghu_workshop_capacity_unavailable')
            enterprise_efficiency=max(500,enterprise_operating_efficiency_milli('crafting_workshop',enterprise_level))
            workers=qualified_workers('crafting')
            if not workers:raise CommandRejectedError('jianghu_workshop_worker_unavailable')
            level=int(faction.get('buildings',{}).get('armory_workshop',0))
            qualified=[]
            q=None
            for row in workers:
                try: candidate_q=workshop_quote(recipe_ref,workshop_level=level,crafting_skill=int(row[0]))
                except (KeyError,ValueError): continue
                if q is None:q=candidate_q
                qualified.append(row)
            if q is None or not qualified:raise CommandRejectedError('jianghu_workshop_recipe_unavailable')
            parallel=max(1,min(count,enterprise_capacity,len(qualified)))
            selected_workers=qualified[:parallel]; worker_refs=[str(row[1]) for row in selected_workers]; worker_ref=worker_refs[0]; worker=selected_workers[0][2]
            inputs={k:int(v)*count for k,v in q['inputs'].items()}
            try:inv['raw_materials']=consume_inputs(inv.get('raw_materials',{}),inputs)
            except ValueError as exc:raise CommandRejectedError('jianghu_workshop_materials_insufficient') from exc
            waves=(count+parallel-1)//parallel
            seconds=max(60,math.ceil(int(q['active_hours'])*waves*3600*1000/enterprise_efficiency)); output_item=str(q['output_item']); output_quantity=int(q['output_quantity'])*count
            activity_kind='workshop_production'
        elif action=='medicine':
            enterprise_level=max(0,int(enterprises.get('medicine_apothecary',0)))
            physical_slots=infirmary_capacity(faction.get('buildings',{}),faction.get('infrastructure',{})).get('apothecary_workstations',0)
            organizational_slots=enterprise_scale_value(faction,'medicine_apothecary') if enterprise_level>0 else 0
            enterprise_capacity=min(max(0,int(physical_slots)),max(0,int(organizational_slots)))
            if enterprise_capacity<=0:raise CommandRejectedError('jianghu_apothecary_capacity_unavailable')
            enterprise_efficiency=max(500,enterprise_operating_efficiency_milli('medicine_apothecary',enterprise_level))
            workers=qualified_workers('medicine')
            if not workers:raise CommandRejectedError('jianghu_apothecary_worker_unavailable')
            level=int(faction.get('buildings',{}).get('infirmary_apothecary',0))
            qualified=[]; q=None
            for row in workers:
                try:candidate_q=medicine_quote(recipe_ref,apothecary_level=level,medicine_skill=int(row[0]))
                except (KeyError,ValueError):continue
                if q is None:q=candidate_q
                qualified.append(row)
            if q is None or not qualified:raise CommandRejectedError('jianghu_medicine_recipe_unavailable')
            parallel=max(1,min(count,enterprise_capacity,len(qualified)))
            selected_workers=qualified[:parallel]; worker_refs=[str(row[1]) for row in selected_workers]; worker_ref=worker_refs[0]; worker=selected_workers[0][2]
            ingredients={k:int(v)*count for k,v in q['ingredients'].items()}
            try:inv['herbs']=consume_inputs(inv.get('herbs',{}),ingredients)
            except ValueError as exc:raise CommandRejectedError('jianghu_medicine_ingredients_insufficient') from exc
            waves=(count+parallel-1)//parallel
            seconds=max(60,math.ceil(int(q['labor_hours'])*waves*3600*1000/enterprise_efficiency)); output_item=recipe_ref; output_quantity=int(q['output_quantity'])*count
            activity_kind='medicine_production'
        elif action=='poison':
            enterprise_level=max(0,int(enterprises.get('medicine_apothecary',0)))
            physical_slots=infirmary_capacity(faction.get('buildings',{}),faction.get('infrastructure',{})).get('apothecary_workstations',0)
            organizational_slots=enterprise_scale_value(faction,'medicine_apothecary') if enterprise_level>0 else 0
            enterprise_capacity=min(max(0,int(physical_slots)),max(0,int(organizational_slots)))
            if enterprise_capacity<=0:raise CommandRejectedError('jianghu_apothecary_capacity_unavailable')
            enterprise_efficiency=max(500,enterprise_operating_efficiency_milli('medicine_apothecary',enterprise_level))
            # Poison production is a conserved apothecary capability, not a hard
            # faction prohibition.  House Tang/Sword Manor still does not teach
            # poison offense in its ordinary martial curriculum or auto-issue
            # poison, but an authorized apothecary may deliberately produce it.
            workers=qualified_workers('medicine')
            if not workers:raise CommandRejectedError('jianghu_apothecary_worker_unavailable')
            level=int(faction.get('buildings',{}).get('infirmary_apothecary',0))
            qualified=[]; q=None
            for row in workers:
                try:candidate_q=poison_quote(recipe_ref,apothecary_level=level,medicine_skill=int(row[0]))
                except (KeyError,ValueError):continue
                if q is None:q=candidate_q
                qualified.append(row)
            if q is None or not qualified:raise CommandRejectedError('jianghu_poison_recipe_unavailable')
            parallel=max(1,min(count,enterprise_capacity,len(qualified)))
            selected_workers=qualified[:parallel]; worker_refs=[str(row[1]) for row in selected_workers]; worker_ref=worker_refs[0]; worker=selected_workers[0][2]
            inputs={k:int(v)*count for k,v in q['inputs'].items()}
            try:inv['raw_materials']=consume_inputs(inv.get('raw_materials',{}),inputs)
            except ValueError as exc:raise CommandRejectedError('jianghu_poison_reagents_insufficient') from exc
            waves=(count+parallel-1)//parallel
            seconds=max(60,math.ceil(int(q['labor_hours'])*waves*3600*1000/enterprise_efficiency)); output_item=str(q['output_item']); output_quantity=int(q['output_quantity'])*count
            activity_kind='poison_production'
        else:raise CommandRejectedError('jianghu_production_action_invalid')

        time_plan,extra,_target=self._timed_person_activity_plan(
            command,meta,current_time,person_refs=worker_refs,seconds=seconds,
            activity_ref=f'production:{command.request_id}',activity_kind=activity_kind,
            owner_ref=faction_ref,location_ref=_effective_location(self, worker_ref, worker) or facility_site,
            staged_records={invpath:inv},
        )
        final_inv=copy.deepcopy(dict(extra.get(invpath,inv)))
        bucket='equipment' if action=='workshop' else ('poisons' if action=='poison' else 'medicines')
        stock=final_inv.setdefault(bucket,{})
        stock[output_item]=int(stock.get(output_item,0))+output_quantity
        storage=storage_capacity_check(faction.get('buildings',{}),faction.get('infrastructure',{}),final_inv,self.repository.read_json(_EQUIPMENT_DATA))
        if not storage['within_capacity']:raise CommandRejectedError('jianghu_storehouse_capacity_exceeded')
        extra[invpath]=final_inv
        return self._combine_time_plan(command,time_plan,extra_records=extra,code='jianghu_production_completed',result={'command_type':command.command_type,'faction_ref':faction_ref,'recipe_ref':recipe_ref,'worker_ref':worker_ref,'worker_refs':worker_refs,'parallel_workers':len(worker_refs),'output_item':output_item,'output_quantity':output_quantity,'enterprise_capacity':enterprise_capacity,'enterprise_efficiency_milli':enterprise_efficiency})

    def _jianghu_equipment_resolution(self,command:CommandEnvelope,meta:Mapping[str,Any],current_time:CampaignTime):
        action=str(command.payload.get('action')); subject_ref=str(command.payload.get('subject_ref')); item_ref=str(command.payload.get('item_ref')); quantity=int(command.payload.get('quantity',0))
        if quantity<=0:raise CommandRejectedError('jianghu_equipment_quantity_invalid')
        _ap,_,_,actor=self._person(command.actor_id); _rp,_rr,_ord,subject=self._person(subject_ref); faction_ref=str(subject.get('faction_ref'))
        if actor.get('faction_ref')!=faction_ref:raise CommandRejectedError('jianghu_equipment_not_authorized')
        if subject_ref!=command.actor_id and not _office(actor,'leader','deputy_leader','quartermaster','field_commander'):raise CommandRejectedError('jianghu_equipment_not_authorized')
        self._require_person_available_for_activity(command.actor_id)
        _fp,faction=read_faction(self.repository,faction_ref); facility_site=str(faction.get('local_site_ref') or '')
        if facility_site and _effective_location(self, command.actor_id, actor)!=facility_site:raise CommandRejectedError('jianghu_equipment_requires_faction_armory')
        if _effective_location(self, command.actor_id, actor)!=_effective_location(self, subject_ref, subject):raise CommandRejectedError('jianghu_equipment_subject_not_present')
        if action=='issue' and not self._person_available_for_activity(subject_ref):raise CommandRejectedError('jianghu_equipment_subject_unavailable')
        if action in {'return','repair'}:
            if subject_ref in self._active_combat_person_refs():raise CommandRejectedError('jianghu_equipment_subject_in_active_combat')
            if self._active_commitment_for_person(subject_ref) is not None and subject_ref!=command.actor_id:
                raise CommandRejectedError('jianghu_equipment_subject_unavailable')

        ledger=hydrate_equipment_ledger(self.repository.read_json(_EQUIPMENT)); invpath=_inventory_path(faction_ref); inv=copy.deepcopy(self.repository.read_json(invpath)); load=ledger.setdefault('person_loadouts',{}).setdefault(subject_ref,{'items':{},'condition_milli':{}}); items=load.setdefault('items',{}); cond=load.setdefault('condition_milli',{}); stock=inv.setdefault('poisons',{}) if item_ref.startswith('poison_') else inv.setdefault('equipment',{})
        materials=None
        if action=='issue':
            if int(stock.get(item_ref,0))<quantity:raise CommandRejectedError('jianghu_equipment_stock_insufficient')
            stock[item_ref]=int(stock.get(item_ref,0))-quantity; items[item_ref]=int(items.get(item_ref,0))+quantity; cond.setdefault(item_ref,1000)
            code='jianghu_equipment_issued'; seconds=60
            staged_ledger=compact_equipment_ledger(ledger)
        elif action=='return':
            if int(items.get(item_ref,0))<quantity:raise CommandRejectedError('jianghu_equipment_not_held')
            if int(cond.get(item_ref,1000))<1000:raise CommandRejectedError('jianghu_damaged_equipment_requires_repair_before_return')
            items[item_ref]=int(items.get(item_ref,0))-quantity; stock[item_ref]=int(stock.get(item_ref,0))+quantity
            if items[item_ref]<=0:items.pop(item_ref,None); cond.pop(item_ref,None)
            claim=provenance_claim(ledger,subject_ref,item_ref)
            if isinstance(claim,Mapping) and str(claim.get('owner_ref') or '')==faction_ref:
                claim_qty=max(0,int(claim.get('quantity',0)))
                ledger=set_nonholder_claim(ledger,holder_ref=subject_ref,item_ref=item_ref,owner_ref=faction_ref,quantity=max(0,claim_qty-quantity),property_ref=(str(claim.get('property_ref')) if claim.get('property_ref') else None),status=str(claim.get('status') or 'recovered_custody'))
            code='jianghu_equipment_returned'; seconds=60
            staged_ledger=compact_equipment_ledger(ledger)
        elif action=='repair':
            if workshop_capacity(faction.get('buildings',{}),faction.get('infrastructure',{})).get('repair_bays',0)<=0:raise CommandRejectedError('jianghu_repair_bay_unavailable')
            held=int(items.get(item_ref,0))
            if held<quantity:raise CommandRejectedError('jianghu_equipment_not_held')
            if quantity!=held:raise CommandRejectedError('jianghu_repair_requires_whole_condition_group')
            before=int(cond.get(item_ref,1000)); crafter=int(actor.get('professional_skills',{}).get('crafting',0))
            try:q=lifecycle_repair_quote(integrity_milli=before,target_integrity_milli=1000,crafting_skill=crafter)
            except ValueError as exc:raise CommandRejectedError('jianghu_repair_skill_insufficient') from exc
            if int(q['integrity_restored_milli'])<=0:raise CommandRejectedError('jianghu_item_already_full_integrity')
            try:materials=repair_material_requirements(item_ref=item_ref,integrity_restored_milli=int(q['integrity_restored_milli']),quantity=quantity)
            except KeyError as exc:raise CommandRejectedError('jianghu_repair_recipe_unavailable') from exc
            try:inv['raw_materials']=consume_inputs(inv.get('raw_materials',{}),materials)
            except ValueError as exc:raise CommandRejectedError('jianghu_repair_materials_insufficient') from exc
            code='jianghu_equipment_repaired'; seconds=max(3600,int(q['crafting_hours'])*quantity*3600)
            staged_ledger=compact_equipment_ledger(ledger)
        else:raise CommandRejectedError('jianghu_equipment_action_invalid')

        activity_people=list(dict.fromkeys([command.actor_id,subject_ref])) if action=='issue' else [command.actor_id]
        time_plan,extras,_target=self._timed_person_activity_plan(
            command,meta,current_time,person_refs=activity_people,seconds=seconds,
            activity_ref=f'equipment:{command.request_id}',activity_kind='equipment_service',
            owner_ref=faction_ref,location_ref=_effective_location(self, command.actor_id, actor),
            staged_records={_EQUIPMENT:staged_ledger,invpath:inv},
        )
        if action=='repair':
            final_ledger=hydrate_equipment_ledger(extras.get(_EQUIPMENT,staged_ledger)); final_load=final_ledger.setdefault('person_loadouts',{}).setdefault(subject_ref,{'items':{},'condition_milli':{}})
            if int(final_load.setdefault('items',{}).get(item_ref,0))<quantity:raise CommandRejectedError('jianghu_repair_item_changed_during_activity')
            final_load.setdefault('condition_milli',{})[item_ref]=1000; extras[_EQUIPMENT]=compact_equipment_ledger(final_ledger)
        result={'command_type':command.command_type,'action':action,'subject_ref':subject_ref,'item_ref':item_ref,'quantity':quantity}
        if materials is not None:result['materials_consumed']=materials
        return self._combine_time_plan(command,time_plan,extra_records=extras,code=code,result=result)

    def _jianghu_diplomacy_resolution(self,command:CommandEnvelope,meta:Mapping[str,Any],current_time:CampaignTime):
        target=str(command.payload.get('target_faction_ref')); proposal=str(command.payload.get('proposal_kind')); value=int(command.payload.get('value_cash',0)); cost=int(command.payload.get('cost_cash',0))
        source_captive_refs=[str(x) for x in command.payload.get('source_captive_refs',()) if isinstance(x,str) and x] if isinstance(command.payload.get('source_captive_refs'),(list,tuple)) else []
        target_captive_refs=[str(x) for x in command.payload.get('target_captive_refs',()) if isinstance(x,str) and x] if isinstance(command.payload.get('target_captive_refs'),(list,tuple)) else []
        authorization_ref=str(command.payload.get('institutional_operation_ref') or '')
        _ap,_,_,actor=self._person(command.actor_id); source=str(actor.get('faction_ref'))
        self._require_person_available_for_activity(command.actor_id, allow_commitment_kinds={'deployment'})
        offices={str(x).split(':',1)[0] for x in actor.get('standing_offices',[]) if isinstance(x,str)}
        treaty_kinds={'non_aggression','mutual_defense','alliance','truce'}
        settlement_kinds={'silver_exchange','restitution','tribute','prisoner_exchange'}
        required_offices={'leader','deputy_leader'} if proposal in treaty_kinds or proposal=='prisoner_exchange' else {'leader','deputy_leader','chief_steward'}
        delegated_row=None
        if not (offices & required_offices):
            if not authorization_ref:
                raise CommandRejectedError('jianghu_diplomacy_not_authorized')
            operations=self.repository.read_json(OPERATIONS_PATH)
            active_operations=operations.get('active',{}) if isinstance(operations,Mapping) else {}
            delegated_row=active_operations.get(authorization_ref) if isinstance(active_operations,Mapping) else None
            if not isinstance(delegated_row,Mapping) or delegated_row.get('phase')!='approved' or delegated_row.get('mission_kind')!='diplomacy':
                raise CommandRejectedError('jianghu_diplomacy_delegation_invalid')
            if str(delegated_row.get('faction_ref') or '')!=source or str(delegated_row.get('target_faction_ref') or '')!=target:
                raise CommandRejectedError('jianghu_diplomacy_delegation_target_invalid')
            if str(delegated_row.get('commander_ref') or '')!=command.actor_id or command.actor_id not in delegated_row.get('participant_refs',[]):
                raise CommandRejectedError('jianghu_diplomacy_delegation_actor_invalid')
            approval_ref=str(delegated_row.get('approved_by_ref') or '')
            try:
                _xp,_xr,_xo,approver=self._person(approval_ref)
            except (KeyError,FileNotFoundError,TypeError,ValueError) as exc:
                raise CommandRejectedError('jianghu_diplomacy_delegation_approver_invalid') from exc
            approver_offices={str(x).split(':',1)[0] for x in approver.get('standing_offices',[]) if isinstance(x,str)}
            if str(approver.get('faction_ref') or '')!=source or not (approver_offices & {'leader','deputy_leader'}):
                raise CommandRejectedError('jianghu_diplomacy_delegation_approver_invalid')
            terms=delegated_row.get('diplomacy_authorization') if isinstance(delegated_row.get('diplomacy_authorization'),Mapping) else {}
            if str(terms.get('proposal_kind') or '')!=proposal or int(terms.get('value_cash',0) or 0)!=value or int(terms.get('cost_cash',0) or 0)!=cost:
                raise CommandRejectedError('jianghu_diplomacy_delegation_terms_mismatch')
            if sorted(str(x) for x in terms.get('source_captive_refs',[]) if isinstance(x,str) and x)!=sorted(source_captive_refs) or sorted(str(x) for x in terms.get('target_captive_refs',[]) if isinstance(x,str) and x)!=sorted(target_captive_refs):
                raise CommandRejectedError('jianghu_diplomacy_delegation_terms_mismatch')
        elif authorization_ref:
            operations=self.repository.read_json(OPERATIONS_PATH)
            active_operations=operations.get('active',{}) if isinstance(operations,Mapping) else {}
            delegated_row=active_operations.get(authorization_ref) if isinstance(active_operations,Mapping) else None
            if not isinstance(delegated_row,Mapping) or str(delegated_row.get('faction_ref') or '')!=source:
                raise CommandRejectedError('jianghu_diplomacy_delegation_invalid')
        if target==source:raise CommandRejectedError('jianghu_diplomacy_self_target')
        if not proposal_kind_supported(proposal):raise CommandRejectedError('jianghu_diplomacy_proposal_unsupported')
        if value<0 or cost<0:raise CommandRejectedError('jianghu_diplomacy_cash_term_invalid')
        relations=copy.deepcopy(self.repository.read_json(_RELATIONS)); edge=_relations_edge(relations,source,target) or {'from_faction':source,'to_faction':target,'trust':0,'respect':0,'hostility':0,'obligation':0}
        source_path,source_state=read_faction(self.repository,source); target_path,target_state=read_faction(self.repository,target)
        if value>int(source_state.get('treasury_cash',0)):raise CommandRejectedError('jianghu_diplomacy_source_cash_unavailable')
        if cost>int(target_state.get('treasury_cash',0)):raise CommandRejectedError('jianghu_diplomacy_target_cash_unavailable')
        custody_state=None; exchange_records=[]
        if proposal=='prisoner_exchange':
            if not source_captive_refs and not target_captive_refs:
                raise CommandRejectedError('jianghu_diplomacy_prisoner_exchange_empty')
            if len(set(source_captive_refs+target_captive_refs))!=len(source_captive_refs)+len(target_captive_refs):
                raise CommandRejectedError('jianghu_diplomacy_prisoner_exchange_duplicate')
            custody_state=copy.deepcopy(self.repository.read_json(_CUSTODY)); records=custody_state.get('records',[])
            if not isinstance(records,list):raise CommandRejectedError('jianghu_custody_state_invalid')
            for captive_ref,holder,owner in [(r,source,target) for r in source_captive_refs]+[(r,target,source) for r in target_captive_refs]:
                record=next((row for row in records if isinstance(row,Mapping) and str(row.get('person_ref') or '')==captive_ref and row.get('status') not in {'released','escaped','rescued','executed'}),None)
                if not isinstance(record,Mapping) or str(record.get('holder_faction_ref') or '')!=holder:
                    raise CommandRejectedError('jianghu_diplomacy_prisoner_exchange_custody_invalid')
                _pp,_pr,_po,captive=self._person(captive_ref)
                if str(captive.get('faction_ref') or '')!=owner:
                    raise CommandRejectedError('jianghu_diplomacy_prisoner_exchange_owner_invalid')
                exchange_records.append((captive_ref,record))
        fit=100-int(target_state.get('autonomy_policy',{}).get('external_aggression',50)); risk=max(0,int(edge.get('hostility',0)))
        if proposal=='truce': fit+=max(0,risk)
        elif proposal in {'mutual_defense','alliance'}: fit+=max(0,int(edge.get('trust',0)))
        elif proposal=='prisoner_exchange': fit+=min(120,25*len(exchange_records))+max(0,risk//2)
        elif proposal=='restitution': fit+=max(0,risk//3)
        decision=evaluate_proposal(edge,proposal_value_cash=value,proposal_cost_cash=cost,strategic_fit=fit,risk=risk)
        pid='proposal:'+hashlib.sha256(f'{source}|{target}|{proposal}|{current_time}'.encode()).hexdigest()[:20]
        terms=[]
        if value:terms.append({'kind':'silver_transfer','from_faction':source,'to_faction':target,'cash':value})
        if cost:terms.append({'kind':'silver_transfer','from_faction':target,'to_faction':source,'cash':cost})
        if proposal in treaty_kinds: terms.append({'kind':'treaty','treaty_kind':proposal,'party_faction_refs':sorted((source,target))})
        if proposal=='prisoner_exchange': terms.append({'kind':'prisoner_exchange','source_releases':source_captive_refs,'target_releases':target_captive_refs})
        record={'proposal_id':pid,'from_faction':source,'to_faction':target,'kind':proposal,'terms':terms,'created_at':str(current_time).removeprefix('SE-'),'decision':'accepted' if decision['accept'] else ('counteroffer' if decision['counteroffer'] else 'rejected'),'score':int(decision['score'])}
        writes={}
        if decision['accept']:
            source_state['treasury_cash']=int(source_state.get('treasury_cash',0))-value+cost
            target_state['treasury_cash']=int(target_state.get('treasury_cash',0))+value-cost
            writes[source_path]=source_state; writes[target_path]=target_state
            if proposal in treaty_kinds:
                relations=stage_treaty(relations,a=source,b=target,kind=proposal,at_iso=str(current_time).removeprefix('SE-'))
                writes[_RELATIONS]=relations
            if proposal=='prisoner_exchange' and isinstance(custody_state,dict):
                released=set(source_captive_refs+target_captive_refs)
                custody_state['records']=[row for row in custody_state.get('records',[]) if not (isinstance(row,Mapping) and str(row.get('person_ref') or '') in released and row.get('status') not in {'released','escaped','rescued','executed'})]
                writes[_CUSTODY]=custody_state
        if authorization_ref and isinstance(delegated_row,Mapping):
            decision_label=str(record.get('decision') or 'rejected')
            closed=close_institutional_operation(
                read_json=self.repository.read_json,writes=writes,operation_ref=authorization_ref,
                at_iso=str(current_time).removeprefix('SE-'),success=(decision_label=='accepted'),
                closure_reason=f'diplomacy_{decision_label}',returned_refs=[command.actor_id],
                extra_report={
                    'diplomacy_target_faction_ref':target,'diplomacy_proposal_kind':proposal,
                    'diplomacy_decision':decision_label,'diplomacy_score':int(record.get('score',0)),
                    'value_cash':value,'cost_cash':cost,
                    'source_captive_refs':sorted(source_captive_refs),'target_captive_refs':sorted(target_captive_refs),
                },
            )
            if closed is None:
                raise CommandRejectedError('jianghu_diplomacy_delegation_close_failed')
        result={'command_type':command.command_type,'proposal':record}
        if authorization_ref: result['institutional_operation_ref']=authorization_ref
        return self._simple_plan(command,meta,current_time,writes_records=writes,code='jianghu_diplomacy_resolved',result=result)

    def _jianghu_family_resolution(self,command:CommandEnvelope,meta:Mapping[str,Any],current_time:CampaignTime):
        action=str(command.payload.get('action')); other_ref=str(command.payload.get('other_ref')); _ap,_,_,actor=self._person(command.actor_id); _op,_,_,other=self._person(other_ref)
        if other_ref==command.actor_id:raise CommandRejectedError('jianghu_family_self_invalid')
        if not self._person_available_for_activity(command.actor_id) or not self._person_available_for_activity(other_ref):raise CommandRejectedError('jianghu_family_party_unavailable')
        if not self._same_effective_location(command.actor_id,other_ref):raise CommandRejectedError('jianghu_family_parties_not_colocated')
        social=copy.deepcopy(self.repository.read_json(_SOCIAL)); family=copy.deepcopy(self.repository.read_json(_FAMILY)); rel_ab=_person_relation(social,command.actor_id,other_ref); rel_ba=_person_relation(social,other_ref,command.actor_id)
        age_a=max(0,current_time.year-int(actor.get('birth_year',current_time.year))); age_b=max(0,current_time.year-int(other.get('birth_year',current_time.year)))
        pair='|'.join(sorted((command.actor_id,other_ref)))
        marriages=family.get('marriages',{}) if isinstance(family.get('marriages'),Mapping) else {}
        courtships=social.get('courtships',{}) if isinstance(social.get('courtships'),Mapping) else {}
        pair_refs={command.actor_id,other_ref}
        married_partners={command.actor_id:[],other_ref:[]}
        for marriage_ref,raw in marriages.items():
            if not isinstance(raw,Mapping) or raw.get('status')!='married':continue
            refs={str(x) for x in raw.get('spouse_refs',[]) if isinstance(x,str)}
            for ref in pair_refs & refs:
                married_partners[ref].append(str(marriage_ref))
        existing_marriage=next((m for m in marriages.values() if isinstance(m,Mapping) and m.get('status')=='married' and set(m.get('spouse_refs',[]))==pair_refs),None)
        if existing_marriage is not None:raise CommandRejectedError('jianghu_family_pair_already_married')
        if any(married_partners.values()):raise CommandRejectedError('jianghu_family_party_already_married')
        other_active_courtships=[]
        for courtship_ref,raw in courtships.items():
            if not isinstance(raw,Mapping) or raw.get('status')!='active' or str(courtship_ref)==pair:continue
            refs={str(x) for x in raw.get('person_refs',[]) if isinstance(x,str)}
            if pair_refs & refs:other_active_courtships.append(str(courtship_ref))
        if other_active_courtships:raise CommandRejectedError('jianghu_family_party_already_courting')
        if action=='courtship':
            existing_court=courtships.get(pair) if isinstance(courtships,Mapping) else None
            if isinstance(existing_court,Mapping) and existing_court.get('status')=='active':raise CommandRejectedError('jianghu_courtship_already_active')
            if not courtship_eligible(age_a=age_a,age_b=age_b,affection_ab=rel_ab['affection'],affection_ba=rel_ba['affection'],trust_ab=rel_ab['trust'],trust_ba=rel_ba['trust']):raise CommandRejectedError('jianghu_courtship_requirements_not_met')
            social.setdefault('courtships',{})[pair]={'person_refs':sorted((command.actor_id,other_ref)),'status':'active','started_at':str(current_time).removeprefix('SE-')}; code='jianghu_courtship_started'
        elif action=='marriage':
            court=social.get('courtships',{}).get(pair) if isinstance(social.get('courtships'),Mapping) else None
            if not isinstance(court,Mapping) or court.get('status')!='active':raise CommandRejectedError('jianghu_marriage_requires_courtship')
            try: court_started_at=datetime.fromisoformat(str(court.get('started_at') or '').removeprefix('SE-'))
            except ValueError: raise CommandRejectedError('jianghu_marriage_courtship_time_invalid')
            if court_started_at>=_dt(current_time):raise CommandRejectedError('jianghu_marriage_requires_elapsed_courtship')
            if not marriage_eligible(age_a=age_a,age_b=age_b,mutual_consent=min(rel_ab['trust'],rel_ba['trust'])>=25 and min(rel_ab['affection'],rel_ba['affection'])>=30,relationship_stage='courtship'):raise CommandRejectedError('jianghu_marriage_requirements_not_met')
            mid='marriage:'+hashlib.sha256(pair.encode()).hexdigest()[:20]
            actor_faction=str(actor.get('faction_ref') or ''); other_faction=str(other.get('faction_ref') or '')
            marriage={'spouse_refs':sorted((command.actor_id,other_ref)),'started_at':str(current_time).removeprefix('SE-'),'status':'married'}
            if actor_faction and actor_faction==other_faction:
                marriage['faction_ref']=actor_faction
                households=family.setdefault('households',{})
                if isinstance(households,dict):
                    memberships={ref:[hid for hid,row in households.items() if isinstance(row,Mapping) and row.get('status')=='active' and ref in row.get('member_refs',[])] for ref in (command.actor_id,other_ref)}
                    actor_hh=memberships[command.actor_id][0] if len(memberships[command.actor_id])==1 else None
                    other_hh=memberships[other_ref][0] if len(memberships[other_ref])==1 else None
                    if actor_hh and not other_hh:
                        row=copy.deepcopy(dict(households[actor_hh])); row['member_refs']=sorted(set(row.get('member_refs',[]))|{other_ref}); households[actor_hh]=row
                    elif other_hh and not actor_hh:
                        row=copy.deepcopy(dict(households[other_hh])); row['member_refs']=sorted(set(row.get('member_refs',[]))|{command.actor_id}); households[other_hh]=row
                    elif not actor_hh and not other_hh:
                        hid='household:'+hashlib.sha256((actor_faction+'|'+pair).encode()).hexdigest()[:20]
                        residence=_effective_location(self, command.actor_id, actor) or _effective_location(self, other_ref, other)
                        households.setdefault(hid,{'faction_ref':actor_faction,'head_ref':command.actor_id,'member_refs':sorted((command.actor_id,other_ref)),'residence_ref':residence,'status':'active'})
            else:
                marriage['faction_refs']=sorted(x for x in {actor_faction,other_faction} if x)
            family.setdefault('marriages',{})[mid]=marriage
            # Marriage is the current relationship authority.  The temporary
            # courtship owner is consumed rather than retained as relationship
            # history beside the marriage.
            if isinstance(social.get('courtships'),dict): social['courtships'].pop(pair,None)
            code='jianghu_marriage_committed'
            # A marriage creates an institutional relation only when both
            # spouses actually belong to different factions. An independent or
            # civic spouse must never create a synthetic empty-faction edge.
            if actor_faction and other_faction and actor_faction!=other_faction:
                relations=copy.deepcopy(self.repository.read_json(_RELATIONS)); edges=list(relations.get('edges',[]))
                for a,b in ((actor_faction,other_faction),(other_faction,actor_faction)):
                    old=_relations_edge(relations,a,b); new=apply_relation_event(old,from_faction=a,to_faction=b,event_kind='marriage_tie'); edges=[e for e in edges if not (isinstance(e,Mapping) and e.get('from_faction')==a and e.get('to_faction')==b)];edges.append(new)
                relations['edges']=sorted(edges,key=lambda e:(str(e.get('from_faction','')),str(e.get('to_faction','')))); return self._simple_plan(command,meta,current_time,writes_records={_FAMILY:family,_SOCIAL:social,_RELATIONS:relations},code=code,result={'command_type':command.command_type,'other_ref':other_ref})
        else:raise CommandRejectedError('jianghu_family_action_invalid')
        return self._simple_plan(command,meta,current_time,writes_records={_FAMILY:family,_SOCIAL:social},code=code,result={'command_type':command.command_type,'other_ref':other_ref})

    def _jianghu_custody_resolution(self,command:CommandEnvelope,meta:Mapping[str,Any],current_time:CampaignTime):
        action=str(command.payload.get('action')); person_ref=str(command.payload.get('person_ref'))
        if action not in {'restrain','release','escape_attempt','rescue','deliver_to_government'}:
            raise CommandRejectedError('jianghu_custody_action_invalid')
        state=copy.deepcopy(self.repository.read_json(_CUSTODY)); records=state.get('records',[])
        if not isinstance(records,list):
            raise CommandRejectedError('jianghu_custody_state_invalid')
        _target_path,_target_roster,_target_ordinal,target=self._person(person_ref)
        _actor_path,_actor_roster,_actor_ordinal,actor=self._person(command.actor_id)
        writes={_CUSTODY:state}
        if action=='restrain':
            # Restraint is not a teleport or an instant combat substitute. The
            # captor must itself be physically available, the target must be in
            # the current scene, and a factual surrender/incapacity/restraint
            # basis must already exist in authoritative combat/current state.
            if not self._person_available_for_activity(command.actor_id,allow_commitment_kinds=('deployment',)):
                raise CommandRejectedError('jianghu_custody_actor_unavailable')
            if not self._same_effective_location(command.actor_id,person_ref):raise CommandRejectedError('jianghu_custody_target_not_present')
            combats=self.repository.read_json(_COMBATS)
            actual_basis=factual_restraint_basis(target=target,target_ref=person_ref,actor_ref=command.actor_id,combats=combats,existing_custody=records)
            if actual_basis is None:raise CommandRejectedError('jianghu_custody_requires_surrender_incapacity_or_physical_restraint')
            target_location=_effective_location(self, person_ref, target) or _effective_location(self, command.actor_id, actor)
            try:record=create_custody_record(person_ref=person_ref,captor_ref=command.actor_id,at=str(current_time).removeprefix('SE-'),location_ref=target_location,basis=actual_basis)
            except ValueError as exc:raise CommandRejectedError('jianghu_custody_invalid') from exc
            if any(isinstance(r,Mapping) and r.get('person_ref')==person_ref and r.get('status') not in {'released','escaped','rescued','executed'} for r in records):raise CommandRejectedError('jianghu_custody_already_active')
            records=list(records)+[record]
            # Custody is its own availability authority. Settle the target's
            # institutional clock exactly now and pause future catch-up without
            # duplicating custody into the commitment registry.
            fpath, paused_faction, rpath, paused_roster = self._pause_institutional_training_now([person_ref], current_time)
            writes[fpath] = paused_faction; writes[rpath] = paused_roster
        else:
            candidates=[r for r in records if isinstance(r,Mapping) and r.get('person_ref')==person_ref and r.get('status') not in {'released','escaped','rescued','executed'}]
            if not candidates:raise CommandRejectedError('jianghu_custody_record_not_found')
            record=candidates[-1]
            if action=='deliver_to_government':
                if str(record.get('captor_ref') or '')!=command.actor_id:
                    raise CommandRejectedError('jianghu_custody_delivery_requires_current_captor')
                if _effective_location(self, command.actor_id,actor)!=_effective_location(self, person_ref,target):
                    raise CommandRejectedError('jianghu_custody_target_not_present')
                site_ref=_effective_location(self, command.actor_id, actor)
                sites=self.repository.read_json(_LOCAL_SITES).get('sites',{})
                site=sites.get(site_ref) if isinstance(sites,Mapping) else None
                if not isinstance(site,Mapping) or str(site.get('site_type') or '')!='magistrate_office':
                    raise CommandRejectedError('jianghu_custody_delivery_requires_magistrate_office')
                place_ref=str(site.get('parent_place_ref') or '')
                try: jurisdiction=region_for_place(place_ref)
                except KeyError as exc: raise CommandRejectedError('jianghu_custody_delivery_jurisdiction_unresolved') from exc
                government=copy.deepcopy(self.repository.read_json(_GOVERNMENT))
                warrants=government.setdefault('warrants',{})
                warrant_ref='warrant:'+person_ref
                warrant=warrants.get(warrant_ref) if isinstance(warrants,Mapping) else None
                if not isinstance(warrant,Mapping) or str(warrant.get('status') or '') not in {'active','pursuing'}:
                    raise CommandRejectedError('jianghu_custody_delivery_requires_active_warrant')
                if str(warrant.get('jurisdiction_ref') or '')!=jurisdiction:
                    raise CommandRejectedError('jianghu_custody_delivery_wrong_jurisdiction')
                at_iso=str(current_time).removeprefix('SE-')
                government_record=create_government_custody_record(
                    person_ref=person_ref,jurisdiction_ref=jurisdiction,at=at_iso,
                    detention_site_ref=site_ref,basis=f'public_bounty_delivery:{warrant_ref}',
                    offense=str(warrant.get('offense') or 'assault'),guard_strength=max(1,int(government.get('regional_capacity',{}).get(jurisdiction,{}).get('standard',1))),
                )
                records=[r for r in records if r is not record]+[government_record]
                state['records']=records; writes[_CUSTODY]=state
                payout=max(0,int(warrant.get('bounty_escrow_cash',0)))
                rpath,actor_roster,ordinal,actor=self._person(command.actor_id)
                actor=copy.deepcopy(dict(actor)); actor['personal_cash']=max(0,int(actor.get('personal_cash',0)))+payout
                writes[rpath]=set_roster_person(actor_roster,ordinal,actor)
                warrants.pop(warrant_ref,None); government['warrants']=warrants; writes[_GOVERNMENT]=government
                schedule=upsert_one_off_event(self.repository.read_json(_SCHEDULE),{
                    'event_id':f"government_custody_release:{government_record['custody_id']}",
                    'kind':'government_custody_release_due','due_at':str(government_record['sentence_release_at']),
                    'owner_ref':str(government_record['custody_id']),'person_ref':person_ref,'requires_player_decision':False,
                })
                writes[_SCHEDULE]=schedule
                return self._simple_plan(command,meta,current_time,writes_records=writes,code='jianghu_bounty_delivery_completed',result={'command_type':command.command_type,'person_ref':person_ref,'jurisdiction_ref':jurisdiction,'bounty_paid_cash':payout,'custody':government_record})
            if action in {'escape_attempt','rescue'}:
                # Escape/rescue are physical-state consequences, never menu
                # buttons.  A rescue additionally allows a third party to free
                # the captive after defeating or otherwise removing the current
                # custodian presence at the captive's real location.
                if _effective_location(self, command.actor_id,actor)!=_effective_location(self, person_ref,target):
                    raise CommandRejectedError('jianghu_custody_target_not_present')
                captor_ref=str(record.get('captor_ref') or '')
                holder_faction_ref=str(record.get('holder_faction_ref') or '')
                holder_kind=str(record.get('holder_kind') or '')
                opening=False
                if holder_kind=='government':
                    # Government custody is institutional, not one absent exact
                    # guard. Escape/rescue must defeat the registered detention
                    # security rather than treating ``government:<region>`` as a
                    # missing person and granting an automatic opening.
                    if action=='escape_attempt':
                        attempt_actor=target
                    else:
                        _ap,_ar,_ao,attempt_actor=self._person(command.actor_id)
                    infiltration=government_rescue_infiltration(
                        actor=attempt_actor,guard_strength=max(1,int(record.get('guard_strength',1))),
                        hour=_dt(current_time).hour,
                    )
                    opening=bool(infiltration.get('success'))
                else:
                    try:
                        _cp,_cr,_co,captor=self._person(captor_ref)
                        same_location=_effective_location(self, captor_ref, captor)==_effective_location(self, person_ref, target)
                        opening=(not same_location) or (not is_living_and_conscious(captor))
                    except CommandRejectedError:
                        opening=True
                if holder_faction_ref:
                    # Institutional custody survives one absent named guard for
                    # both escape and rescue. The acting person needs a real
                    # local opening: no usable custodian-faction member remains
                    # present, or a resolved exact combat put that actor on the
                    # winning side against the custodian faction.
                    local_guard=False
                    try:
                        guard_roster = self.repository.read_json(_roster_path(holder_faction_ref))
                    except (FileNotFoundError, KeyError, TypeError, ValueError):
                        guard_roster = {}
                    guard_people = guard_roster.get('people', []) if isinstance(guard_roster, Mapping) else []
                    for guard in guard_people if isinstance(guard_people, list) else []:
                        if not isinstance(guard, Mapping):
                            continue
                        ref = str(guard.get('person_id') or '')
                        if not ref or ref in {person_ref, command.actor_id}:
                            continue
                        # Stored faction rosters compact default home location away.
                        # Rehydrate the exact guard through the normal person authority
                        # before applying the universal physical co-presence check.
                        try:
                            _gp, _gr, _go, exact_guard = self._person(ref)
                        except CommandRejectedError:
                            continue
                        if not is_living_and_conscious(exact_guard):
                            continue
                        if not same_effective_location(
                            self.repository.read_json, person_ref, ref, left_person=target, right_person=exact_guard,
                        ):
                            continue
                        local_guard=True;break
                    if local_guard:
                        opening=False
                    combats=self.repository.read_json(_COMBATS)
                    rows=combats.get('combats',{}) if isinstance(combats,Mapping) else {}
                    for combat in rows.values() if isinstance(rows,Mapping) else []:
                        if not isinstance(combat,Mapping) or combat.get('status')!='resolved':continue
                        sides=combat.get('sides',{}) if isinstance(combat.get('sides'),Mapping) else {}
                        actor_side=next((side for side,refs in sides.items() if isinstance(refs,list) and command.actor_id in refs),None)
                        if not actor_side or combat.get('winner_side')!=actor_side:continue
                        opposing=[]
                        for side,refs in sides.items():
                            if side!=actor_side and isinstance(refs,list):opposing.extend(str(x) for x in refs if isinstance(x,str))
                        defeated_custodian=False
                        for ref in opposing:
                            try:
                                _op,_or,_oo,other=self._person(ref)
                            except CommandRejectedError:
                                continue
                            if ref==captor_ref or str(other.get('faction_ref') or '')==holder_faction_ref:
                                defeated_custodian=True;break
                        if defeated_custodian:
                            opening=True;break
                if not opening:
                    code='jianghu_custody_rescue_requires_physical_opening' if action=='rescue' else 'jianghu_custody_escape_requires_physical_opening'
                    raise CommandRejectedError(code)
            try:new=custody_transition(record,action=action,at=str(current_time).removeprefix('SE-'),actor_ref=command.actor_id)
            except (KeyError,ValueError) as exc:raise CommandRejectedError('jianghu_custody_transition_invalid') from exc
            records=[r for r in records if r is not record]
            record=new
            if action=='rescue' and command.actor_id!=person_ref:
                social_before=copy.deepcopy(self.repository.read_json(_SOCIAL))
                rescue_social=apply_relationship_event(
                    social_before,observer_ref=person_ref,subject_ref=command.actor_id,
                    event_kind='rescue',observer_knows=True,severity_milli=1200,
                    protected_player_ref=str(meta.get('player_id') or 'pc_wei_tang'),
                )
                social_after=rescue_social['state_after']
                # A rescue can repay an existing debt/promise. If it does, do
                # not manufacture a reciprocal life debt merely because the
                # rescued person is grateful; that would create endless debt
                # ping-pong instead of resolving the current obligation.
                repayable=[
                    row for row in obligations_for_actor(social_after,command.actor_id)
                    if str(row.get('counterparty_ref') or '')==person_ref
                    and str(row.get('kind') or '') in {'life_debt','promise_aid','promise_protect'}
                ]
                if repayable:
                    chosen=max(repayable,key=lambda row:(int(row.get('strength',0)),str(row.get('kind') or '')))
                    ref=personal_obligation_ref(command.actor_id,person_ref,str(chosen.get('kind') or ''))
                    social_after=resolve_personal_obligation(social_after,obligation_ref_value=ref)['state_after']
                else:
                    social_after=add_personal_obligation(
                        social_after,actor_ref=person_ref,counterparty_ref=command.actor_id,
                        kind='life_debt',strength=80,created_at=str(current_time).removeprefix('SE-'),
                    )['state_after']
                writes[_SOCIAL]=social_after
            # Release/escape clears the custody pause only when no separate
            # conserved activity still owns the same person's availability.
            commitments=derived_commitment_state(self.repository.read_json)
            index=commitments.get('person_index',{}) if isinstance(commitments,Mapping) else {}
            def _released_read(path: str):
                return state if path == _CUSTODY else self.repository.read_json(path)
            blocked = set(str(ref) for ref in index) if isinstance(index, Mapping) else set()
            blocked.update(physical_unavailable_person_refs(_released_read))
            if person_ref not in blocked:
                fpath,resumed_faction,rpath,resumed_roster=self._resume_institutional_training_now([person_ref],current_time)
                writes[fpath]=resumed_faction; writes[rpath]=resumed_roster
        state['records']=records; writes[_CUSTODY]=state
        return self._simple_plan(command,meta,current_time,writes_records=writes,code='jianghu_custody_updated',result={'command_type':command.command_type,'custody':record})

    def _jianghu_crime_report_resolution(self,command:CommandEnvelope,meta:Mapping[str,Any],current_time:CampaignTime):
        subject_ref=str(command.payload.get('subject_ref') or '').strip(); offense=str(command.payload.get('offense') or '').strip(); confidence=int(command.payload.get('confidence',0)); evidence_ref=str(command.payload.get('evidence_ref') or '').strip()
        if not subject_ref or not offense or not evidence_ref:raise CommandRejectedError('jianghu_crime_report_invalid')
        if confidence<=0 or confidence>100:raise CommandRejectedError('jianghu_crime_confidence_invalid')
        try:self._person(subject_ref)
        except CommandRejectedError as exc:raise CommandRejectedError('jianghu_crime_subject_unresolved') from exc
        if offense!='theft':
            raise CommandRejectedError('jianghu_crime_report_requires_registered_evidence_path')
        if offense=='theft':
            ledger=hydrate_equipment_ledger(self.repository.read_json(_EQUIPMENT))
            claim=validate_property_evidence(ledger,evidence_ref,holder_ref=subject_ref)
            if not isinstance(claim,Mapping):
                raise CommandRejectedError('jianghu_crime_property_evidence_invalid')
            owner_ref=str(claim.get('owner_ref') or '')
            _rp,_rr,_ro,reporter=self._person(command.actor_id)
            reporter_faction=str(reporter.get('faction_ref') or '')
            if owner_ref!=command.actor_id and owner_ref!=reporter_faction:
                raise CommandRejectedError('jianghu_crime_property_reporter_not_owner')
        gov=copy.deepcopy(self.repository.read_json(_GOVERNMENT))
        evidence_hash=hashlib.sha256(evidence_ref.encode('utf-8')).hexdigest()[:32]
        consumed=gov.get('consumed_evidence_hashes',[])
        if not isinstance(consumed,list):raise CommandRejectedError('jianghu_government_state_invalid')
        if evidence_hash in set(str(x) for x in consumed):raise CommandRejectedError('jianghu_crime_evidence_already_consumed')
        attention_rows=gov.setdefault('attention',{})
        prior_row=attention_rows.get(subject_ref,{}) if isinstance(attention_rows,Mapping) else {}
        prior=int(prior_row.get('prior_offenses',0)) if isinstance(prior_row,Mapping) else 0
        try:calc=crime_attention(offense=offense,confidence=confidence,publicly_delivered=True,prior_offenses=prior)
        except KeyError as exc:raise CommandRejectedError('jianghu_crime_offense_invalid') from exc
        prior_attention=max(0,int(prior_row.get('attention',0))) if isinstance(prior_row,Mapping) else 0
        prior_bounty=max(0,int(prior_row.get('bounty_cash',0))) if isinstance(prior_row,Mapping) else 0
        attention=min(300,prior_attention+max(0,int(calc['attention'])))
        bounty=max(prior_bounty,max(0,int(calc['bounty_cash'])))
        attention_rows[subject_ref]=compact_attention_row(attention=attention,bounty_cash=bounty,prior_offenses=prior+1)
        gov['consumed_evidence_hashes']=sorted(set(str(x) for x in consumed)|{evidence_hash})
        subject={}; place=''; jurisdiction=''
        # The regional-economy partition is also the jurisdictional authority
        # used by government response. Resolve it through the single mapping
        # helper rather than duplicating geography-field knowledge here.
        try:
            _sp,_sr,_so,subject=self._person(subject_ref)
            sites=self.repository.read_json(_LOCAL_SITES).get('sites',{}); subject_location=_effective_location(self, subject_ref, subject); site=sites.get(subject_location) if isinstance(sites,Mapping) else None
            place=str(site.get('parent_place_ref') or '') if isinstance(site,Mapping) else subject_location
            jurisdiction=region_for_place(place)
        except (KeyError,ValueError,FileNotFoundError):
            subject={}; place=''; jurisdiction=''
        extra_writes={}
        funded_bounty=0
        if bounty>0:
            warrants=gov.setdefault('warrants',{}); warrant_ref='warrant:'+subject_ref
            existing=warrants.get(warrant_ref,{}) if isinstance(warrants,Mapping) else {}
            existing_status=str(existing.get('status') or '') if isinstance(existing,Mapping) else ''
            existing_escrow=max(0,int(existing.get('bounty_escrow_cash',0))) if isinstance(existing,Mapping) else 0
            funded_bounty=existing_escrow
            if jurisdiction:
                market_path=f'state/martial-world/markets/{jurisdiction}.json'
                market=copy.deepcopy(self.repository.read_json(market_path))
                funding=fund_bounty_escrow(market,existing_warrant=existing,desired_cash=bounty)
                funded_bounty=int(funding['escrow_cash'])
                if int(funding['escrow_added_cash']):
                    extra_writes[market_path]=funding['market_after']
            warrant={'subject_ref':subject_ref,'offense':offense,'bounty_cash':funded_bounty,'bounty_escrow_cash':funded_bounty,'status':existing_status if existing_status in {'active','pursuing'} else 'active','evidence_ref':evidence_ref,'issued_at':str(existing.get('issued_at') or str(current_time).removeprefix('SE-')) if isinstance(existing,Mapping) else str(current_time).removeprefix('SE-')}
            if jurisdiction:warrant['jurisdiction_ref']=jurisdiction
            warrants[warrant_ref]=warrant
            attention_rows[subject_ref]['bounty_cash']=funded_bounty
        # The report makes the subject known to this government audience, not
        # to the whole world. If the subject's faction is known from the exact
        # person owner, that same delivered evidence can affect government
        # awareness/opinion of the faction without becoming public gossip.
        rep=copy.deepcopy(self.repository.read_json(_REPUTATION))
        audience=f'government:{jurisdiction or place or "unknown"}'
        rep=apply_personal_fame_evidence(rep,audience_ref=audience,person_ref=subject_ref,evidence_kind='government_crime_report',delivered=True,reliability_milli=confidence*10)
        subject_faction=str(subject.get('faction_ref') or '') if isinstance(subject,Mapping) else ''
        if subject_faction:
            rep=apply_faction_awareness_evidence(rep,audience_ref=audience,faction_ref=subject_faction,evidence_kind='government_report',delivered=True,reliability_milli=confidence*10)
            rep=apply_faction_reputation_evidence(rep,audience_ref=audience,faction_ref=subject_faction,axis_deltas={'criminal_notoriety':max(1,attention//12),'trustworthiness':-max(1,attention//30)},delivered=True,reliability_milli=confidence*10)
        writes={_GOVERNMENT:gov,_REPUTATION:rep,**extra_writes}
        return self._simple_plan(command,meta,current_time,writes_records=writes,code='jianghu_crime_report_committed',result={'command_type':command.command_type,'subject_ref':subject_ref,'attention':attention,'bounty_cash':funded_bounty,'government_audience_ref':audience})

    def _jianghu_combat_core_resolution(self,command:CommandEnvelope,meta:Mapping[str,Any],current_time:CampaignTime):
        action=str(command.payload.get('action')); state=copy.deepcopy(self.repository.read_json(_COMBATS)); combats=state.setdefault('combats',{})
        if action=='start':
            combat_ref=str(command.payload.get('combat_ref')); side_a=list(command.payload.get('side_a_refs') or []); side_b=list(command.payload.get('side_b_refs') or []); objective=command.payload.get('objective'); awareness=str(command.payload.get('awareness_mode')); band=int(command.payload.get('initial_range_band',1)); mounted_refs=list(command.payload.get('mounted_refs') or [])
            if combat_ref in combats:raise CommandRejectedError('jianghu_combat_ref_exists')
            if not side_a or not side_b or len(set(side_a))!=len(side_a) or len(set(side_b))!=len(side_b) or set(side_a)&set(side_b):raise CommandRejectedError('jianghu_combat_participants_invalid')
            refs=list(dict.fromkeys(side_a+side_b)); people={}; participant_routes={}; locations=set()
            if len(set(str(x) for x in mounted_refs))!=len(mounted_refs) or any(str(x) not in refs for x in mounted_refs):raise CommandRejectedError('jianghu_combat_mount_assignments_invalid')
            for existing_ref,existing in combats.items():
                if existing_ref==combat_ref or not isinstance(existing,Mapping) or existing.get('status')!='active':continue
                occupied={str(x) for side in existing.get('sides',{}).values() if isinstance(side,list) for x in side if isinstance(x,str)}
                if occupied&set(refs):raise CommandRejectedError('jianghu_combat_participant_already_in_combat')
            for ref in refs:
                if not self._person_available_for_activity(str(ref),allow_commitment_kinds=('deployment',)):raise CommandRejectedError('jianghu_combat_participant_unavailable')
                _p,_r,_o,person=self._person(str(ref)); people[str(ref)]=person; participant_routes[str(ref)]=(_p,_r,_o); locations.add(_effective_location(self, str(ref), person))
            if len(locations)!=1 or command.actor_id not in refs:raise CommandRejectedError('jianghu_combat_participants_not_colocated')
            actor_faction=str(people[command.actor_id].get('faction_ref') or '')
            mount_assignments={}
            requested_by_faction:dict[str,int]={}
            for raw_ref in mounted_refs:
                ref=str(raw_ref); owner=str(people[ref].get('faction_ref') or '')
                if not owner or owner!=actor_faction:raise CommandRejectedError('jianghu_combat_mount_authority_invalid')
                requested_by_faction[owner]=requested_by_faction.get(owner,0)+1
                mount_assignments[ref]={'owner_faction_ref':owner}
            for owner,count in requested_by_faction.items():
                try:inv=self.repository.read_json(_inventory_path(owner))
                except FileNotFoundError as exc:raise CommandRejectedError('jianghu_combat_mount_inventory_unresolved') from exc
                available=faction_available_capacity(inv,self.repository.read_json(_ROUTE_OPERATIONS),faction_ref=owner) if isinstance(inv,Mapping) else {'rider_slots':0}
                stock=max(0,int(available.get('rider_slots',0)))
                allocated=active_mount_allocations(state,faction_ref=owner)
                if stock-allocated<count:raise CommandRejectedError('jianghu_combat_rider_capacity_unavailable')
            # Active combat owns participant physiology. Settle and detach each
            # participant's sparse body wake at the exact combat-start timestamp
            # so the scheduler cannot advance the same body in parallel.
            schedule_after=copy.deepcopy(self.repository.read_json(_SCHEDULE)); physiology_carries={}
            detached_rosters={}
            for ref in refs:
                detached=detach_person_physiology_wake(schedule_after,person_ref=ref,person=people[ref],at=str(current_time).removeprefix('SE-'))
                schedule_after=detached['schedule_after']; people[ref]=copy.deepcopy(dict(detached['person_after']))
                physiology_carries[ref]=(int(detached.get('recovery_carry_minutes',0)),int(detached.get('poison_clearance_carry_minutes',0)))
                path,base_roster,ordinal=participant_routes[ref]
                current_roster=detached_rosters.get(path,base_roster)
                detached_rosters[path]=set_roster_person(current_roster,ordinal,people[ref])
            equipment_ledger=self.repository.read_json(_EQUIPMENT)
            zone_ref=str(next(iter(locations)))
            sites_doc=self.repository.read_json(_LOCAL_SITES); sites=sites_doc.get('sites',{}) if isinstance(sites_doc,Mapping) else {}
            site=sites.get(zone_ref) if isinstance(sites,Mapping) else None
            geography=self.repository.read_json(_GEOGRAPHY); place_rows=geography.get('places',{}) if isinstance(geography,Mapping) else {}
            parent_place=str(site.get('parent_place_ref') or '') if isinstance(site,Mapping) else zone_ref
            place=place_rows.get(parent_place,{}) if isinstance(place_rows,Mapping) else {}
            terrain=site_combat_terrain(site if isinstance(site,Mapping) else None,place if isinstance(place,Mapping) else None)
            try:weather=weather_snapshot(world_seed=str(meta.get('world_seed') or 'jianghu-world'),at=_dt(current_time),place_id=parent_place) if parent_place else {}
            except (KeyError,TypeError,ValueError):weather={}
            env=combat_environment(terrain=terrain,zone_ref=zone_ref,seed_ref=f'{combat_ref}|{zone_ref}|{current_time.year}-{current_time.month}-{current_time.day}',weather=weather)
            try:combat=initialize_combat(combat_ref=combat_ref,side_a_refs=side_a,side_b_refs=side_b,people=people,zone_ref=zone_ref,started_at=str(current_time).removeprefix('SE-'),objective=objective if isinstance(objective,Mapping) else {'kind':'eliminate','target_refs':side_b},awareness_mode=awareness,initial_range_band=band,equipment_ledger=equipment_ledger,mount_assignments=mount_assignments,environment=env)
            except ValueError as exc:raise CommandRejectedError('jianghu_combat_start_invalid') from exc
            for ref,(recovery_carry,poison_carry) in physiology_carries.items():
                cstate=combat.get('combatants',{}).get(ref) if isinstance(combat.get('combatants'),Mapping) else None
                if isinstance(cstate,dict):
                    cstate['physiology_recovery_carry_minutes']=max(0,int(recovery_carry))%60
                    cstate['poison_clearance_carry_minutes']=max(0,int(poison_carry))%60
            combats[combat_ref]=combat; scene=copy.deepcopy(self.repository.read_json(self.scene_path));scene['active_combat_ref']=combat_ref;scene['present_person_ids']=refs;scene['visible_person_ids']=refs if awareness=='mutual' else list(scene.get('visible_person_ids',[]))
            start_records={_COMBATS:state,_SCHEDULE:schedule_after,**detached_rosters}
            start_records.update(self._scene_transition_records(at=str(current_time), reason='hard_interruption'))
            return self._simple_plan(command,meta,current_time,writes_records=start_records,code='jianghu_combat_started',result={'command_type':command.command_type,'combat_ref':combat_ref,'positions':combat['positions'],'mounted_refs':sorted(str(x) for x in mounted_refs)},scene=scene)
        combat_ref=str(command.payload.get('combat_ref')); combat=combats.get(combat_ref)
        if not isinstance(combat,Mapping) or combat.get('status') not in {'active','resolved'}:raise CommandRejectedError('jianghu_combat_unresolved')
        if action=='exchange':
            if combat.get('status')!='active':raise CommandRejectedError('jianghu_combat_already_resolved')
            refs=[r for side in combat.get('sides',{}).values() for r in side]; people={}; routes={}
            for ref in refs:
                path,roster,ordinal,person=self._person(ref);people[ref]=person;routes[ref]=(path,roster,ordinal)
            factions={str(p.get('faction_ref')) for p in people.values()};doctrines={}
            for fid in factions:
                try:_fp,fstate=read_faction(self.repository,fid);doctrines[fid]=fstate.get('doctrine',{})
                except FileNotFoundError:doctrines[fid]={}
            ledger=copy.deepcopy(self.repository.read_json(_EQUIPMENT))
            social_current=copy.deepcopy(self.repository.read_json(_SOCIAL))
            player_retinue_context=None
            deployments_doc=self.repository.read_json(_DEPLOYMENTS)
            deployment_rows=deployments_doc.get('deployments',{}) if isinstance(deployments_doc,Mapping) else {}
            if isinstance(deployment_rows,Mapping):
                for retinue_ref,row in deployment_rows.items():
                    if not isinstance(row,Mapping):continue
                    if row.get('operation_kind')!='standing_retinue' or row.get('status')!='active':continue
                    if row.get('leader_ref')!=command.actor_id:continue
                    player_retinue_context={
                        'retinue_ref':str(retinue_ref),'leader_ref':command.actor_id,
                        'member_refs':[str(x) for x in row.get('member_refs',[]) if isinstance(x,str)],
                        'member_roles':copy.deepcopy(dict(row.get('member_roles',{}))) if isinstance(row.get('member_roles'),Mapping) else {},
                        'combat_doctrine_ref':row.get('combat_doctrine_ref'),
                    }
                    break
            # Route missions preserve whether an escort is part of Wei's drilled
            # standing retinue or temporary mission manpower. Only the latter
            # may be conditionally admitted by the retinue doctrine. Older
            # movement rows are inferred conservatively from escort membership.
            if isinstance(player_retinue_context,dict):
                objective=combat.get('objective',{}) if isinstance(combat.get('objective'),Mapping) else {}
                movement_ref=objective.get('movement_ref')
                if isinstance(movement_ref,str) and movement_ref:
                    route_ops=self.repository.read_json(_ROUTE_OPERATIONS)
                    movements=route_ops.get('movements',{}) if isinstance(route_ops,Mapping) else {}
                    movement=movements.get(movement_ref,{}) if isinstance(movements,Mapping) else {}
                    if isinstance(movement,Mapping):
                        temporary=movement.get('temporary_mission_escort_refs')
                        if not isinstance(temporary,list):
                            standing={command.actor_id,*player_retinue_context.get('member_refs',[])}
                            temporary=[
                                str(ref) for ref in movement.get('escort_refs',[])
                                if isinstance(ref,str) and ref not in standing
                            ]
                        player_retinue_context['temporary_member_refs']=[
                            str(ref) for ref in temporary if isinstance(ref,str)
                        ]
            raw_target_ref=str(command.payload.get('target_ref') or 'auto')
            raw_action_kind=str(command.payload.get('action_kind') or 'attack')
            raw_weapon_ref=str(command.payload.get('weapon_ref') or 'auto')
            resolved_hit_zone=str(command.payload.get('hit_zone') or 'auto')
            target_structure_ref=(
                str(command.payload.get('target_structure_ref'))
                if command.payload.get('target_structure_ref') not in (None,'') else None
            )
            player_doctrine_ref=(
                str(people[command.actor_id].get('combat_doctrine_ref'))
                if isinstance(people.get(command.actor_id),Mapping) and people[command.actor_id].get('combat_doctrine_ref')
                else None
            )
            resolved_player_targeting_intent=(
                str(command.payload.get('targeting_intent'))
                if command.payload.get('targeting_intent') not in (None,'')
                else combat_default_targeting_intent(combat,doctrine_ref=player_doctrine_ref)
            )

            poison_supplied='poison_ref' in command.payload
            raw_poison=command.payload.get('poison_ref')
            poison_auto=(not poison_supplied) or str(raw_poison or '').lower()=='auto'
            explicit_poison=(
                None if str(raw_poison or '').lower() in {'','none','auto'} else str(raw_poison)
            )
            improvised_prop_state=None
            improvised_prop_fact_ref=command.payload.get('improvised_prop_fact_ref')
            if improvised_prop_fact_ref not in (None,''):
                try:
                    improvised_prop_state=_improvised_prop_state_for_combat(
                        self,combat=combat,actor_ref=command.actor_id,fact_ref=str(improvised_prop_fact_ref),
                    )
                except (FileNotFoundError,KeyError,TypeError,ValueError) as exc:
                    raise CommandRejectedError('jianghu_combat_improvised_prop_invalid') from exc
                if explicit_poison is not None:
                    raise CommandRejectedError('jianghu_combat_improvised_prop_poison_requires_durable_application')
                # A transient scene prop has no durable poison-coating owner.
                # Qi may still improve Wei's body/movement through the ordinary
                # capability path, but automatic poison must not materialize on
                # the object merely because combat doctrine permits poison.
                poison_auto=False
            qi_supplied='qi_allocation_milli' in command.payload
            explicit_qi=command.payload.get('qi_allocation_milli') if qi_supplied else None
            if qi_supplied and not isinstance(explicit_qi,Mapping):
                raise CommandRejectedError('jianghu_combat_qi_allocation_invalid')

            exchange_count=None
            if 'exchange_count' in command.payload:
                raw_count=command.payload.get('exchange_count')
                if isinstance(raw_count,bool) or not isinstance(raw_count,int) or raw_count<=0:
                    raise CommandRejectedError('jianghu_combat_exchange_count_invalid')
                exchange_count=int(raw_count)
            duration_seconds=None
            if 'duration_seconds' in command.payload:
                raw_duration=command.payload.get('duration_seconds')
                if isinstance(raw_duration,bool) or not isinstance(raw_duration,int) or raw_duration<=0:
                    raise CommandRejectedError('jianghu_combat_duration_invalid')
                duration_seconds=int(raw_duration)
            until_resolution=False
            if 'until_resolution' in command.payload:
                raw_until=command.payload.get('until_resolution')
                if not isinstance(raw_until,bool):
                    raise CommandRejectedError('jianghu_combat_until_resolution_invalid')
                until_resolution=bool(raw_until)
            if sum((exchange_count is not None,duration_seconds is not None,until_resolution))>1:
                raise CommandRejectedError('jianghu_combat_scope_conflict')

            try:
                resolved=_resolve_player_combat_span(
                    combat=combat,people=people,equipment_ledger=ledger,doctrines=doctrines,
                    player_ref=command.actor_id,social_state=social_current,
                    player_retinue_context=player_retinue_context,
                    raw_target_ref=raw_target_ref,raw_action_kind=raw_action_kind,raw_weapon_ref=raw_weapon_ref,
                    hit_zone=resolved_hit_zone,target_structure_ref=target_structure_ref,
                    targeting_intent=(resolved_player_targeting_intent if 'targeting_intent' in command.payload else None),
                    explicit_poison_ref=explicit_poison,poison_auto=poison_auto,
                    explicit_qi_allocation_milli=(explicit_qi if qi_supplied else None),qi_auto=not qi_supplied,
                    exchange_count=exchange_count,duration_seconds=duration_seconds,until_resolution=until_resolution,
                    player_improvised_weapon_state=improvised_prop_state,
                )
            except ValueError as exc:
                raise CommandRejectedError('jianghu_combat_exchange_invalid') from exc
            # The exchange result becomes true at the end of the elapsed
            # combat interval, not at its start. Keep the existing active combat
            # visible while quiet scheduler frontiers settle, then rebase the
            # exact combat deltas onto those post-time after-images.
            delta=max(1,(int(resolved['combat_after'].get('elapsed_ms',0))-int(combat.get('elapsed_ms',0))+999)//1000)
            death_time=current_time.add_seconds(delta)
            time_plan=self._time_plan_exact(command,meta,current_time,seconds=delta)

            combat_after=copy.deepcopy(dict(resolved['combat_after']))
            losses_by_faction:dict[str,int]={}
            after_states=combat_after.get('combatants',{}) if isinstance(combat_after.get('combatants'),Mapping) else {}
            for cstate in after_states.values():
                mount=cstate.get('mount') if isinstance(cstate,Mapping) and isinstance(cstate.get('mount'),dict) else None
                if not isinstance(mount,dict) or not bool(mount.get('service_loss_pending')) or bool(mount.get('inventory_debited')):continue
                owner=str(mount.get('owner_faction_ref') or '')
                if not owner:raise CommandRejectedError('jianghu_combat_mount_owner_unresolved')
                losses_by_faction[owner]=losses_by_faction.get(owner,0)+1
                mount['inventory_debited']=True; mount['service_loss_pending']=False

            mount_inventory_records:dict[str,Any]={}
            for owner,count in losses_by_faction.items():
                invpath=_inventory_path(owner)
                try:base_inv=self.repository.read_json(invpath)
                except FileNotFoundError as exc:raise CommandRejectedError('jianghu_combat_mount_inventory_unresolved') from exc
                inv_after=self._time_after_record(time_plan,invpath,base_inv)
                transport=inv_after.setdefault('transport_capacity',{})
                if not isinstance(transport,dict):raise CommandRejectedError('jianghu_combat_mount_inventory_invalid')
                stock=max(0,int(transport.get('rider_slots',0)))
                if stock<count:raise CommandRejectedError('jianghu_combat_mount_conservation_failure')
                transport['rider_slots']=stock-count
                mount_inventory_records[invpath]=inv_after

            post_combats=self._time_after_record(time_plan,_COMBATS,state)
            post_rows=post_combats.setdefault('combats',{})
            if not isinstance(post_rows,dict):raise CommandRejectedError('jianghu_combat_state_invalid')
            post_rows[combat_ref]=combat_after
            final_records:dict[str,Any]={_COMBATS:post_combats,**mount_inventory_records}

            # Fighting changes relationships only for people who lawfully know
            # the exchange occurred.  Attackers know their intended target; a
            # target reacts to an attacker only when combat awareness currently
            # includes that attacker.  This avoids omniscient social updates.
            social_after=self._time_after_record(time_plan,_SOCIAL,social_current)
            seen_pairs:set[tuple[str,str]]=set()
            combatants_after=combat_after.get('combatants',{}) if isinstance(combat_after.get('combatants'),Mapping) else {}
            for ev in resolved.get('events',[]):
                if not isinstance(ev,Mapping):continue
                a=str(ev.get('actor_ref') or ''); b=str(ev.get('intended_ref') or '')
                if not a or not b or a==b or ev.get('result') in {'invalid_target','friendly_target_rejected','target_unavailable','action_rejected'}:continue
                if (a,b) not in seen_pairs:
                    social_after=apply_relationship_event(social_after,observer_ref=a,subject_ref=b,event_kind='fighting',observer_knows=True,severity_milli=700,protected_player_ref=str(meta.get('player_id') or 'pc_wei_tang'))['state_after']; seen_pairs.add((a,b))
                bstate=combatants_after.get(b,{}) if isinstance(combatants_after,Mapping) else {}
                observed=set(str(x) for x in bstate.get('observed_refs',[]) if isinstance(x,str)) if isinstance(bstate,Mapping) else set()
                if a in observed and (b,a) not in seen_pairs:
                    social_after=apply_relationship_event(social_after,observer_ref=b,subject_ref=a,event_kind='fighting',observer_knows=True,severity_milli=700,protected_player_ref=str(meta.get('player_id') or 'pc_wei_tang'))['state_after']; seen_pairs.add((b,a))
            side_by_ref={
                str(ref):str(side)
                for side,refs in (combat_after.get('sides',{}) if isinstance(combat_after.get('sides'),Mapping) else {}).items()
                if isinstance(refs,list) for ref in refs if isinstance(ref,str)
            }
            # Pairwise opponent adaptation is a duel/small-fight mechanic. A
            # player can still fight in a larger melee, but one battlefield
            # must not materialize a permanent familiarity matrix between
            # everybody who happened to share its frontage.
            existing_martial_refs={str(ref) for ref in social_after.get('martial_familiarity',{})} if isinstance(social_after.get('martial_familiarity'),Mapping) else set()
            social_after=apply_martial_events(social_after,resolved.get('events',[]),side_by_ref=side_by_ref)
            # New durable opponent familiarity belongs to duels and genuinely
            # small fights. Larger melees may deepen an already-established
            # rivalry, but one multi-person exchange must not materialize a
            # fresh pairwise matrix.
            keep_martial_refs=None if len(side_by_ref)<=4 else existing_martial_refs
            if len(side_by_ref)>4 or str(combat_after.get('status') or '')=='resolved':
                social_after=prune_incidental_martial_familiarity(social_after,keep_refs=keep_martial_refs)

            # Personal commitments constrain autonomous choices, but they do
            # not magically block the player's declared action. Resolve breaches
            # against the actual player event targets so a doctrine-driven span
            # may lawfully retarget after an opponent falls without attributing
            # every later strike to the first target.
            broken_vow_refs:list[str]=[]; broken_obligation_refs:list[str]=[]
            rejected_player_results={
                'invalid_target','friendly_target_rejected','target_unavailable','action_rejected',
                'target_not_observed','no_lawfully_known_target',
            }
            for ev in resolved.get('events',[]):
                if not isinstance(ev,Mapping) or str(ev.get('actor_ref') or '')!=command.actor_id:continue
                if str(ev.get('result') or '') in rejected_player_results:continue
                event_target_ref=str(ev.get('intended_ref') or '')
                if not event_target_ref:continue
                player_target=(
                    people.get(event_target_ref,{})
                    if isinstance(people.get(event_target_ref),Mapping)
                    else resolved.get('people_after',{}).get(event_target_ref,{})
                )
                breached=breach_hostile_commitments(
                    social_after,actor_ref=command.actor_id,target_ref=event_target_ref,
                    target_faction_ref=str(player_target.get('faction_ref') or '') if isinstance(player_target,Mapping) else '',
                    targeting_intent=str(ev.get('targeting_intent') or resolved_player_targeting_intent),
                    poison_ref=str(ev.get('poison_ref') or ''),
                )
                social_after=breached['state_after']
                new_vows=[str(ref) for ref in breached.get('broken_vow_refs',[]) if str(ref) not in broken_vow_refs]
                new_obligations=[str(ref) for ref in breached.get('broken_obligation_refs',[]) if str(ref) not in broken_obligation_refs]
                broken_vow_refs.extend(new_vows); broken_obligation_refs.extend(new_obligations)
                if new_obligations:
                    social_after=apply_relationship_event(
                        social_after,observer_ref=event_target_ref,subject_ref=command.actor_id,
                        event_kind='oath_breach',observer_knows=True,severity_milli=1000,
                        protected_player_ref=str(meta.get('player_id') or 'pc_wei_tang'),
                    )['state_after']
            final_records[_SOCIAL]=social_after

            # Combat may change exact custody/condition of a participant's held
            # equipment. Merge only those participant loadouts onto the
            # scheduler's post-time ledger so unrelated maintenance or other
            # same-frontier equipment writes survive.
            post_ledger=hydrate_equipment_ledger(self._time_after_record(time_plan,_EQUIPMENT,ledger))
            resolved_ledger=hydrate_equipment_ledger(resolved['equipment_ledger_after'])
            post_loadouts=post_ledger.setdefault('person_loadouts',{})
            resolved_loadouts=resolved_ledger.get('person_loadouts',{})
            if not isinstance(post_loadouts,dict) or not isinstance(resolved_loadouts,Mapping):
                raise CommandRejectedError('jianghu_equipment_state_invalid')
            for ref in refs:
                if ref in resolved_loadouts and isinstance(resolved_loadouts[ref],Mapping):
                    post_loadouts[ref]=copy.deepcopy(dict(resolved_loadouts[ref]))
                else:
                    post_loadouts.pop(ref,None)
            final_records[_EQUIPMENT]=compact_equipment_ledger(post_ledger)

            # Rebase exact injury/health changes onto each post-time roster.
            by_path:dict[str,list[tuple[int,str]]]={}
            for ref,(path,_original,ordinal) in routes.items():
                by_path.setdefault(path,[]).append((ordinal,ref))
            for path,entries in by_path.items():
                original=routes[entries[0][1]][1]
                current=self._time_after_record(time_plan,path,original)
                rows=current.get('people',[]) if isinstance(current,Mapping) else []
                if not isinstance(rows,list):raise CommandRejectedError('jianghu_roster_invalid')
                index={str(row.get('person_id')):i for i,row in enumerate(rows) if isinstance(row,Mapping) and isinstance(row.get('person_id'),str)}
                for _ordinal,ref in entries:
                    idx=index.get(ref)
                    if idx is None:raise CommandRejectedError('jianghu_combat_participant_missing_after_time')
                    rows[idx]=copy.deepcopy(dict(resolved['people_after'][ref]))
                final_records[path]=current

            touched_factions={str(person.get('faction_ref')) for person in resolved['people_after'].values() if isinstance(person,Mapping) and person.get('faction_ref')}
            for fid in touched_factions:
                rpath=canonical_roster_path(fid)
                if rpath in final_records:
                    fpath,faction=read_faction(self.repository,fid)
                    faction_after=hydrate_faction_state(self._time_after_record(time_plan,fpath,faction))
                    roster_after=hydrate_roster_state(final_records[rpath],faction=faction_after)
                    faction_after=reconcile_faction_population(faction_after,roster_after)
                    final_records[fpath]=faction_after
                    final_records[rpath]=compact_roster_state(roster_after,faction=faction_after)

            if combat_after.get('status')=='resolved':
                schedule_after=self._time_after_record(time_plan,_SCHEDULE,self.repository.read_json(_SCHEDULE))
                for ref in refs:
                    cstate=combat_after.get('combatants',{}).get(ref,{}) if isinstance(combat_after.get('combatants'),Mapping) else {}
                    schedule_after=attach_person_physiology_wake(
                        schedule_after,person_ref=ref,person=resolved['people_after'][ref],now=str(death_time).removeprefix('SE-'),
                        recovery_carry_minutes=max(0,int(cstate.get('physiology_recovery_carry_minutes',0))) if isinstance(cstate,Mapping) else 0,
                        poison_clearance_carry_minutes=max(0,int(cstate.get('poison_clearance_carry_minutes',0))) if isinstance(cstate,Mapping) else 0,
                    )
                final_records[_SCHEDULE]=schedule_after

            scene=None
            if combat_after.get('status')=='resolved':
                scene=self._time_after_record(time_plan,self.scene_path,self.repository.read_json(self.scene_path));scene.pop('active_combat_ref',None)

            newly_dead={
                str(ref) for ref,after in resolved['people_after'].items()
                if isinstance(after,Mapping)
                and after.get('health',{}).get('status')=='dead'
                and people.get(str(ref),{}).get('health',{}).get('status')!='dead'
            }
            vengeance_created:list[str]=[]
            if newly_dead:
                # Only a close relative who was actually in this combat and had
                # awareness of the killer receives exact personal vengeance.
                # No family-wide inherited grievance matrix is materialized.
                family_after=self._time_after_record(time_plan,_FAMILY,self.repository.read_json(_FAMILY))
                for dead_ref in sorted(newly_dead):
                    killer_ref=''
                    for ev in resolved.get('events',[]):
                        if not isinstance(ev,Mapping):continue
                        if str(ev.get('actual_ref') or '')!=dead_ref:continue
                        if str(ev.get('result') or '') not in {'contact','physical_contact_no_wound'}:continue
                        actor=str(ev.get('actor_ref') or '')
                        if actor and actor!=dead_ref:killer_ref=actor
                    if not killer_ref:continue
                    for relative_ref in sorted(close_family_refs(family_after,dead_ref)):
                        if relative_ref not in refs or relative_ref==killer_ref:continue
                        after_person=resolved['people_after'].get(relative_ref,{})
                        health=after_person.get('health',{}) if isinstance(after_person,Mapping) and isinstance(after_person.get('health'),Mapping) else {}
                        if health.get('status') in {'dead','incapacitated'}:continue
                        cstate=combatants_after.get(relative_ref,{}) if isinstance(combatants_after,Mapping) else {}
                        observed={str(x) for x in cstate.get('observed_refs',[]) if isinstance(x,str)} if isinstance(cstate,Mapping) else set()
                        if killer_ref not in observed:continue
                        added=add_personal_obligation(
                            social_after,actor_ref=relative_ref,counterparty_ref=killer_ref,
                            kind='vengeance',strength=85,created_at=str(death_time).removeprefix('SE-'),
                        )
                        social_after=added['state_after']; vengeance_created.append(str(added['obligation_ref']))
                final_records[_SOCIAL]=social_after
                final_records=self._cleanup_command_deaths(final_records,newly_dead,death_time)
            return self._combine_time_plan(command,time_plan,extra_records=final_records,code='jianghu_combat_exchange_resolved',result={'command_type':command.command_type,'combat_ref':combat_ref,'events':resolved['events'],'combat_status':combat_after.get('status'),'exchanges_resolved':int(resolved.get('exchanges_resolved',1)),'scope_stop_reason':str(resolved.get('scope_stop_reason') or 'scope_complete'),'continuation_required':bool(resolved.get('continuation_required',False)),'mount_service_losses':sum(losses_by_faction.values()),'broken_vow_refs':broken_vow_refs,'broken_obligation_refs':broken_obligation_refs,'vengeance_created_count':len(vengeance_created)},scene_override=scene)
        if action=='disengage':
            refs=[r for side in combat.get('sides',{}).values() if isinstance(side,list) for r in side if isinstance(r,str)]
            people={}
            for ref in refs:
                try:_p,_r,_o,person=self._person(ref);people[ref]=person
                except CommandRejectedError:continue
            try:r=attempt_disengage(combat=combat,actor_ref=command.actor_id,people=people,equipment_ledger=self.repository.read_json(_EQUIPMENT))
            except (KeyError,ValueError) as exc:raise CommandRejectedError('jianghu_disengage_invalid') from exc
            after=copy.deepcopy(dict(r['combat_after']))
            if r.get('escaped'):
                active_by_side={}
                for side in ('side_a','side_b'):
                    active=[]
                    for ref in after.get('sides',{}).get(side,[]):
                        person=people.get(ref,{}) if isinstance(people.get(ref,{}),Mapping) else {}
                        health=person.get('health',{}) if isinstance(person.get('health'),Mapping) else {}
                        cstate=after.get('combatants',{}).get(ref,{}) if isinstance(after.get('combatants'),Mapping) else {}
                        statuses=set(cstate.get('status_families',[])) if isinstance(cstate,Mapping) else set()
                        if health.get('status') not in {'dead','incapacitated'} and int(health.get('consciousness',100))>0 and not ({'dead','unconscious','incapacitated','escaped'}&statuses):active.append(ref)
                    active_by_side[side]=active
                if not active_by_side['side_a'] or not active_by_side['side_b']:
                    after['status']='resolved'
                    after['winner_side']='side_a' if active_by_side['side_a'] else 'side_b' if active_by_side['side_b'] else 'none'
            delta=max(1,(int(after.get('elapsed_ms',0))-int(combat.get('elapsed_ms',0))+999)//1000)
            time_plan=self._time_plan_exact(command,meta,current_time,seconds=delta)
            post=self._time_after_record(time_plan,_COMBATS,state); rows=post.setdefault('combats',{})
            if not isinstance(rows,dict):raise CommandRejectedError('jianghu_combat_state_invalid')
            rows[combat_ref]=after
            scene=None; extra_records={_COMBATS:post}
            if after.get('status')=='resolved':
                scene=self._time_after_record(time_plan,self.scene_path,self.repository.read_json(self.scene_path));scene.pop('active_combat_ref',None)
                social_after=self._time_after_record(time_plan,_SOCIAL,self.repository.read_json(_SOCIAL))
                social_after=prune_incidental_martial_familiarity(social_after)
                extra_records[_SOCIAL]=social_after
                schedule_after=self._time_after_record(time_plan,_SCHEDULE,self.repository.read_json(_SCHEDULE))
                for ref,person in people.items():
                    cstate=after.get('combatants',{}).get(ref,{}) if isinstance(after.get('combatants'),Mapping) else {}
                    schedule_after=attach_person_physiology_wake(schedule_after,person_ref=ref,person=person,now=str(current_time.add_seconds(delta)).removeprefix('SE-'),recovery_carry_minutes=max(0,int(cstate.get('physiology_recovery_carry_minutes',0))) if isinstance(cstate,Mapping) else 0,poison_clearance_carry_minutes=max(0,int(cstate.get('poison_clearance_carry_minutes',0))) if isinstance(cstate,Mapping) else 0)
                extra_records[_SCHEDULE]=schedule_after
            return self._combine_time_plan(command,time_plan,extra_records=extra_records,code='jianghu_disengage_resolved',result={'command_type':command.command_type,'combat_ref':combat_ref,'escaped':bool(r.get('escaped')),'reason':r.get('reason'),'combat_status':after.get('status')},scene_override=scene)
        if action=='end':
            if combat.get('status')!='resolved':raise CommandRejectedError('jianghu_combat_not_resolved')
            objective=combat.get('objective',{}) if isinstance(combat.get('objective'),Mapping) else {}
            objective_kind=str(objective.get('kind') or '')
            if objective_kind=='tournament_match':raise CommandRejectedError('jianghu_combat_pending_tournament_resolution')
            if objective_kind=='protect_cargo':raise CommandRejectedError('jianghu_combat_pending_route_resolution')
            refs=[r for side in combat.get('sides',{}).values() if isinstance(side,list) for r in side if isinstance(r,str)]
            schedule_after=copy.deepcopy(self.repository.read_json(_SCHEDULE))
            for ref in refs:
                try:_p,_r,_o,person=self._person(ref)
                except CommandRejectedError:continue
                cstate=combat.get('combatants',{}).get(ref,{}) if isinstance(combat.get('combatants'),Mapping) else {}
                schedule_after=attach_person_physiology_wake(schedule_after,person_ref=ref,person=person,now=str(current_time).removeprefix('SE-'),recovery_carry_minutes=max(0,int(cstate.get('physiology_recovery_carry_minutes',0))) if isinstance(cstate,Mapping) else 0,poison_clearance_carry_minutes=max(0,int(cstate.get('poison_clearance_carry_minutes',0))) if isinstance(cstate,Mapping) else 0)
            combats.pop(combat_ref,None)
            social_after=prune_incidental_martial_familiarity(self.repository.read_json(_SOCIAL))
            scene=copy.deepcopy(self.repository.read_json(self.scene_path));scene.pop('active_combat_ref',None);return self._simple_plan(command,meta,current_time,writes_records={_COMBATS:state,_SOCIAL:social_after,_SCHEDULE:schedule_after},code='jianghu_combat_closed',result={'command_type':command.command_type,'combat_ref':combat_ref},scene=scene)
        raise CommandRejectedError('jianghu_combat_action_invalid')
