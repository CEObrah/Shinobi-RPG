"""Mechanically consequential public information with sparse current beliefs."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.martial_world.escort import materialize_civilian_identities
from shinobi_runtime.martial_world.escort_living_world import (
    best_route_observer, interception_decision, interception_force_size, observed_escort_strength,
    person_combat_index, private_interception_decision_context,
)
from shinobi_runtime.martial_world.faction_registry import current_faction_refs_at_place
from shinobi_runtime.martial_world.civic import hydrate_civic_person
from shinobi_runtime.martial_world.commitments import derived_commitment_state
from shinobi_runtime.martial_world.faction_state import read_faction, resolved_faction_type, roster_path
from shinobi_runtime.martial_world.person_state import hydrate_roster_state
from shinobi_runtime.martial_world.physical_presence import effective_person_presence, same_effective_location
from shinobi_runtime.martial_world.manpower import is_faction_member
from shinobi_runtime.martial_world.public_observation import disclosure_credibility_milli, hears_public_disclosure
from shinobi_runtime.martial_world.scheduler import route_ids_needing_service, sync_route_activity
from shinobi_runtime.martial_world.social_presence import person_attends_site, person_settlement
from shinobi_runtime.martial_world.social_causality import record_belief
from shinobi_runtime.martial_world.strategic_autonomy import stable_permille
from shinobi_runtime.martial_world.training import institutional_training_pause_refs, settle_and_reset_faction_training_cycle
from shinobi_runtime.sim.events import CampaignTime

_ROUTE_OPS="state/martial-world/route-operations.json"
_SCHEDULE="state/martial-world/scheduler.json"
_RELATIONS="state/martial-world/faction-relations.json"
_LOCAL_SITES="game/data/martial-world/local-sites.json"
_CIVILIANS="state/martial-world/civilian-populations.json"
_CIVIC="state/martial-world/civic-people.json"
_SOCIAL="state/martial-world/social.json"


def _at(value: CampaignTime) -> datetime:
    return datetime(value.year,value.month,value.day,value.hour,value.minute,value.second)


def _edge(registry: Mapping[str,Any], a: str, b: str) -> Mapping[str,Any] | None:
    rows=registry.get("edges",[]) if isinstance(registry,Mapping) else []
    if not isinstance(rows,list): return None
    return next((row for row in rows if isinstance(row,Mapping) and row.get("from_faction")==a and row.get("to_faction")==b),None)


class JianghuInformationCommandsMixin:
    def _jianghu_public_disclosure_resolution(self, command: CommandEnvelope, meta: Mapping[str,Any], current_time: CampaignTime):
        movement_ref=str(command.payload.get("movement_ref") or "")
        claim_kind=str(command.payload.get("claim_kind") or "")
        claimed_value=max(0,int(command.payload.get("claimed_value_cash") or 0))
        if claim_kind not in {"cargo_value","principal_value"} or not movement_ref or claimed_value <= 0:
            raise CommandRejectedError("jianghu_public_disclosure_invalid")
        route_ops=copy.deepcopy(self.repository.read_json(_ROUTE_OPS)); movements=route_ops.get("movements",{})
        movement=movements.get(movement_ref) if isinstance(movements,dict) else None
        if not isinstance(movement,Mapping) or command.actor_id not in movement.get("participant_refs",[]):
            raise CommandRejectedError("jianghu_public_disclosure_movement_unresolved")
        if str(movement.get("status") or "active") not in {"active","lodging_rest","field_rest","returning"}:
            raise CommandRejectedError("jianghu_public_disclosure_movement_inactive")
        _ap, _ar, _ao, actor_person = self._person(command.actor_id)
        actor_presence = effective_person_presence(self.repository.read_json, command.actor_id, person=actor_person)
        site_ref=str(actor_presence.get("location_ref") or "")
        scene=self.repository.read_json(self.scene_path)
        sites_data=self.repository.read_json(_LOCAL_SITES); sites=sites_data.get("sites",{}) if isinstance(sites_data,Mapping) else {}
        site=sites.get(site_ref) if isinstance(sites,Mapping) else None
        if not isinstance(site,Mapping) or str(site.get("site_type") or "") not in {"inn","tea_house","wine_shop","market","caravan_yard","guild_hall","gambling_house","stable"}:
            raise CommandRejectedError("jianghu_public_disclosure_not_public_site")
        settlement=str(site.get("parent_place_ref") or "")
        if not settlement:
            raise CommandRejectedError("jianghu_public_disclosure_location_invalid")
        status=str(movement.get("status") or "active")
        if status in {"lodging_rest","field_rest"}:
            if str(movement.get("rest_place_ref") or "") != site_ref:
                raise CommandRejectedError("jianghu_public_disclosure_party_not_at_site")
        else:
            physical_origin=str(movement.get("segment_origin_place_ref") or movement.get("origin_place_ref") or "")
            if settlement != physical_origin:
                raise CommandRejectedError("jianghu_public_disclosure_party_not_at_site")
        now=_at(current_time); at_iso=now.isoformat(); disclosure_ref=f"{movement_ref}|{command.actor_id}|{claim_kind}|{claimed_value}|{at_iso}"
        party_refs=[str(x) for x in movement.get("participant_refs",[]) if isinstance(x,str)]
        escort_refs=[str(x) for x in movement.get("escort_refs",party_refs) if isinstance(x,str)]
        escorts=[]
        for ref in escort_refs:
            try: _p,_r,_o,person=self._person(ref)
            except CommandRejectedError: continue
            health=person.get("health",{}) if isinstance(person.get("health"),Mapping) else {}
            if health.get("status") != "dead": escorts.append(person)
        if not escorts:
            raise CommandRejectedError("jianghu_public_disclosure_no_travel_party")
        beneficiary=str(movement.get("beneficiary_ref") or "")
        target_factions=[]
        if beneficiary: target_factions.append(beneficiary)
        for ref in [str(x) for x in movement.get("protected_person_refs",[]) if isinstance(x,str)]:
            try: _p,_r,_o,person=self._person(ref)
            except CommandRejectedError: continue
            if not same_effective_location(
                self.repository.read_json, command.actor_id, ref, left_person=actor_person, right_person=person,
            ):
                continue
            fid=str(person.get("faction_ref") or "")
            if fid and fid not in target_factions: target_factions.append(fid)
        relations=self.repository.read_json(_RELATIONS)
        unavailable=self._unavailable_person_refs()
        local_fids=current_faction_refs_at_place(
            self.repository.read_json,place_ref=settlement,sites=sites,
        )
        # Explicitly present visitors can also hear the disclosure even if their
        # institution is based elsewhere. Aggregate civilians remain aggregate at
        # this point. We may derive one transient potential listener below, but
        # no civilian identity is persisted merely because speech occurred.
        visitor_by_faction: dict[str,list[Mapping[str,Any]]]={}
        for ref in set(scene.get("present_person_ids",[]))|set(scene.get("visible_person_ids",[])):
            if not isinstance(ref,str) or ref in party_refs: continue
            try: _p,_r,_o,person=self._person(ref)
            except CommandRejectedError: continue
            if not same_effective_location(
                self.repository.read_json, command.actor_id, ref, left_person=actor_person, right_person=person,
            ):
                continue
            fid=str(person.get("faction_ref") or "")
            if fid: visitor_by_faction.setdefault(fid,[]).append(person)
            if fid and fid not in local_fids: local_fids.append(fid)

        # A busy public venue can contain unnamed aggregate civilians without
        # pre-generating thousands of identities. Derive one deterministic
        # potential informant from the population cursor, and attach that
        # ephemeral listener to one locally present outlaw organization. The
        # generated after-images are written only if the tip causes a real
        # pursuit below; otherwise the candidate never becomes save state.
        transient_civic_ref=""; transient_civic_after=None; transient_civilians_after=None
        outlaw_tip_targets=[]
        for fid in sorted(set(local_fids)):
            if not fid or fid in target_factions:
                continue
            try:
                _fp,_f=read_faction(self.repository,fid)
            except (FileNotFoundError,KeyError,TypeError,ValueError):
                continue
            if resolved_faction_type(_f)=="outlaw_faction":
                outlaw_tip_targets.append(fid)
        if outlaw_tip_targets:
            try:
                civilians_before=self.repository.read_json(_CIVILIANS)
                civic_before=self.repository.read_json(_CIVIC)
                preview=materialize_civilian_identities(
                    civilians_before,civic_before,world_seed=str(meta.get("world_seed") or "jianghu"),
                    source_place_ref=settlement,count=1,current_year=now.year,civilian_party_kind=None,
                )
                refs=[str(x) for x in preview.get("person_refs",[]) if isinstance(x,str)]
                rows=preview.get("civic_state",{}).get("people",[]) if isinstance(preview.get("civic_state"),Mapping) else []
                if refs and isinstance(rows,list):
                    raw=next((row for row in rows if isinstance(row,Mapping) and row.get("person_id")==refs[0]),None)
                    if isinstance(raw,Mapping):
                        transient_civic_ref=refs[0]
                        transient_civic_after=preview["civic_state"]
                        transient_civilians_after=preview["civilian_state"]
                        transient_listener=hydrate_civic_person(raw)
                        choice=stable_permille("civilian-tip-target",disclosure_ref,settlement,len(outlaw_tip_targets))%len(outlaw_tip_targets)
                        visitor_by_faction.setdefault(outlaw_tip_targets[choice],[]).append(transient_listener)
            except (FileNotFoundError,KeyError,TypeError,ValueError):
                pass

        writes: dict[str,Any]={}; hidden_actions=0
        social_before=self.repository.read_json(_SOCIAL); social_after=copy.deepcopy(social_before)
        existing_pursuits={
            (str(row.get("beneficiary_ref") or ""),str(row.get("target_movement_ref") or ""))
            for row in movements.values() if isinstance(row,Mapping) and row.get("movement_kind")=="route_pursuit"
        } if isinstance(movements,Mapping) else set()
        for fid in sorted(set(local_fids)):
            if not fid or fid in target_factions or (fid,movement_ref) in existing_pursuits: continue
            try: fpath,faction=read_faction(self.repository,fid); roster_raw=self.repository.read_json(roster_path(fid)); roster=hydrate_roster_state(roster_raw,faction=faction)
            except (FileNotFoundError,KeyError,TypeError,ValueError): continue
            people=roster.get("people",[]) if isinstance(roster,Mapping) else []
            if not isinstance(people,list): continue
            listeners=list(visitor_by_faction.get(fid,[]))
            hq=str(faction.get("headquarters") or "")
            for person in people:
                if not isinstance(person,Mapping): continue
                pid=str(person.get("person_id") or "")
                if not pid or pid in party_refs or pid in unavailable: continue
                if person_attends_site(person,site_ref=site_ref,site=site,faction_headquarters=hq,sites=sites,at=now,unavailable_refs=unavailable):
                    listeners.append(person)
            unique={str(p.get("person_id")):p for p in listeners if isinstance(p,Mapping) and isinstance(p.get("person_id"),str)}
            heard=[p for p in unique.values() if hears_public_disclosure(p,speaker_ref=command.actor_id,site_type=str(site.get("site_type") or ""),at=now,disclosure_ref=disclosure_ref)]
            observer=best_route_observer(heard)
            if not isinstance(observer,Mapping): continue
            attacker_type=resolved_faction_type(faction)
            relation_options=[_edge(relations,fid,target) for target in target_factions if target and target != fid]
            relation_options=[row for row in relation_options if isinstance(row,Mapping)]
            relation=max(relation_options,key=lambda row:(max(0,int(row.get("hostility",0))),-int(row.get("trust",0))),default=None)
            hostility=max(0,int(relation.get("hostility",0))) if isinstance(relation,Mapping) else 0
            if attacker_type != "outlaw_faction" and hostility < 55: continue
            observed=observed_escort_strength(observer=observer,escorts=escorts,world_seed=str(meta.get("world_seed") or "jianghu"),observation_ref=disclosure_ref+"|"+fid)
            credibility=disclosure_credibility_milli(observer,speaker_ref=command.actor_id,claimed_value_cash=claimed_value,at=now,disclosure_ref=disclosure_ref)
            observer_ref=str(observer.get('person_id') or '')
            claim_ref=f'public-disclosure:{movement_ref}:{claim_kind}'
            # One exact decision-maker retains one current belief.  The row is
            # also the authority for the value estimate used below, so this is
            # not a write-only rumor record. Aggregate/transient listeners are
            # persisted only if the resulting pursuit materializes them.
            belief_row=None
            if observer_ref and observer_ref!=transient_civic_ref:
                recorded=record_belief(
                    social_after,observer_ref=observer_ref,claim_ref=claim_ref,subject_ref=movement_ref,
                    claim_kind=claim_kind,confidence_milli=credibility,stance=('supports' if credibility>=300 else 'uncertain'),
                    source_ref=command.actor_id,value_cash=claimed_value,
                )
                social_after=recorded['state_after']; belief_row=recorded['belief']
            believed=claimed_value*int((belief_row or {'confidence_milli':credibility}).get('confidence_milli',credibility))//1000
            # Assemble only physically local, currently free people. Hearing the
            # claim does not teleport the rest of a faction into the city.
            available=[]
            for person in people:
                if not isinstance(person,Mapping) or not is_faction_member(person): continue
                pid=str(person.get("person_id") or "")
                if not pid or pid in unavailable: continue
                health=person.get("health",{}) if isinstance(person.get("health"),Mapping) else {}
                if health.get("status") in {"dead","incapacitated"} or int(health.get("consciousness",100))<=0: continue
                if person_settlement(person,faction_headquarters=hq,sites=sites) != settlement: continue
                available.append(person)
            available.sort(key=lambda p:(-person_combat_index(p),str(p.get("person_id") or "")))
            desired=max(2,int(observed.get("visible_escort_count",len(escort_refs)))*2+1)
            if believed >= 50_000: desired += 2
            criminal_scale=0
            enterprises=faction.get("enterprise_scale",{}) if isinstance(faction.get("enterprise_scale"),Mapping) else {}
            criminal=enterprises.get("criminal_enterprise")
            if isinstance(criminal,Mapping): criminal_scale=max(0,int(criminal.get("scale",criminal.get("level",0))))
            force_size=interception_force_size(
                available_count=len(available), observed_escort_count=max(1,int(observed.get("visible_escort_count",len(escort_refs)))),
                hostility=hostility, criminal_scale=criminal_scale, risk_tolerance=max(0,int((faction.get("autonomy_policy",{}) or {}).get("risk_tolerance",50))) if isinstance(faction.get("autonomy_policy",{}),Mapping) else 50,
                known_value_cash=believed, attacker_faction_type=attacker_type,
            )
            attackers=available[:force_size]
            if not attackers: continue
            own_index=max(1,sum(person_combat_index(p) for p in attackers)//len(attackers))
            autonomy=faction.get("autonomy_policy",{}) if isinstance(faction.get("autonomy_policy"),Mapping) else {}
            policy=faction.get("outlaw_policy",{}) if isinstance(faction.get("outlaw_policy"),Mapping) else {}
            decision=interception_decision(
                attacker_faction_type=attacker_type,relation=relation,own_available_martial=len(attackers),own_combat_index=own_index,
                observed_escort_count=max(1,int(observed.get("visible_escort_count",len(escort_refs)))),observed_escort_combat_index=max(1,int(observed.get("estimated_combat_index",1))),
                cargo_value_cash=believed if claim_kind=="cargo_value" else 0,ransom_value_cash=believed if claim_kind=="principal_value" else 0,
                risk_tolerance=max(0,int(autonomy.get("risk_tolerance",50))),government_risk_milli=150,
                minimum_attack_advantage_milli=max(650,int(policy.get("minimum_attack_advantage_milli",1100))),
                civilian_restraint=max(0,int((faction.get("doctrine",{}) or {}).get("civilian_restraint",0))) if isinstance(faction.get("doctrine",{}),Mapping) else 0,
            )
            if not decision.get("attack"): continue
            # Materialize training only for a faction that actually changes the
            # world by mobilizing. This keeps mere overhearing ephemeral.
            current_busy=sorted(self._unavailable_person_refs())
            paused_now=institutional_training_pause_refs(
                faction,[p for p in roster.get("people",[]) if isinstance(p,Mapping)],unavailable_refs=current_busy,
            )
            faction_after,roster_after,_=settle_and_reset_faction_training_cycle(
                faction,roster,at_iso=at_iso,paused_refs=paused_now,
            )
            writes[fpath]=faction_after; writes[roster_path(fid)]=roster_after
            attacker_refs=[str(p.get("person_id")) for p in attackers if isinstance(p.get("person_id"),str)]
            pursuit_ref=f"pursuit:{movement_ref}:{fid}:{now.strftime('%Y%m%d%H%M%S')}"
            ready_hours=2+stable_permille("public-disclosure-pursuit",pursuit_ref,disclosure_ref)*7//999
            target_route=str(movement.get("route_ref") or "")
            if not target_route: continue
            movements[pursuit_ref]={
                "movement_kind":"route_pursuit","target_movement_ref":movement_ref,"route_ref":target_route,
                "origin_place_ref":settlement,"destination_place_ref":str(movement.get("segment_destination_place_ref") or movement.get("destination_place_ref") or ""),
                "beneficiary_ref":fid,"participant_refs":attacker_refs,"escort_refs":attacker_refs,
                "started_at":at_iso,"ready_at":(now+timedelta(hours=ready_hours)).isoformat(),"status":"pursuing",
                "contact_intent":str(decision.get("intent") or "hostile_interception"),
                "motive_kind":str(decision.get("motive_kind") or ""),
                "gm_private_decision_context":private_interception_decision_context(decision),
            }
            if transient_civic_ref and str(observer.get("person_id") or "")==transient_civic_ref:
                # Consequential action is the promotion boundary: the informant
                # now has an exact body and can independently act or be acted on.
                if isinstance(transient_civilians_after,Mapping) and isinstance(transient_civic_after,Mapping):
                    writes[_CIVILIANS]=transient_civilians_after
                    writes[_CIVIC]=transient_civic_after
                    movements[pursuit_ref]["source_observer_ref"]=transient_civic_ref
                    recorded=record_belief(
                        social_after,observer_ref=transient_civic_ref,claim_ref=claim_ref,subject_ref=movement_ref,
                        claim_kind=claim_kind,confidence_milli=credibility,stance=('supports' if credibility>=300 else 'uncertain'),
                        source_ref=command.actor_id,value_cash=claimed_value,
                    )
                    social_after=recorded['state_after']
            hidden_actions += 1
        if social_after != social_before:
            writes[_SOCIAL]=social_after
        if hidden_actions:
            writes[_ROUTE_OPS]=route_ops
            schedule=copy.deepcopy(self.repository.read_json(_SCHEDULE))
            active_routes=route_ids_needing_service(movements)
            writes[_SCHEDULE]=sync_route_activity(schedule,active_route_ids=active_routes,now=now)
        # Deliberately do not return hidden listeners, factions or pursuits. The
        # player's knowledge is the words they chose to say, not backend intent.
        return self._simple_plan(
            command,meta,current_time,writes_records=writes,code="jianghu_public_disclosure_spoken",
            result={"command_type":command.command_type,"movement_ref":movement_ref,"claim_kind":claim_kind,"claimed_value_cash":claimed_value},
        )


__all__=["JianghuInformationCommandsMixin"]
