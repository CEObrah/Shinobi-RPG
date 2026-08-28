"""Bounded campaign operations for the single Jianghu runtime."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, FrozenSet, Mapping, Optional, Sequence

from shinobi_runtime.martial_world.faction_state import faction_presentation_identity, read_faction, roster_path as faction_roster_path
from shinobi_runtime.martial_world.faction_registry import current_faction_refs_at_place
from shinobi_runtime.martial_world.commitments import derived_commitment_state
from shinobi_runtime.martial_world.inventory_state import hydrate_inventory_state
from shinobi_runtime.martial_world.person_state import hydrate_roster_state
from shinobi_runtime.martial_world.exact_combat import capability_from_person
from shinobi_runtime.martial_world.money import format_copper
from shinobi_runtime.martial_world.escort import hydrate_contract_escort_objective
from shinobi_runtime.martial_world.route_intelligence import journey_intelligence_brief, route_intelligence_brief
from shinobi_runtime.martial_world.physical_presence import (
    active_combat_for_person, effective_person_presence, physical_unavailable_person_refs, same_effective_location,
)
from shinobi_runtime.martial_world.scene_sessions import (
    active_scene_session, inspect_history_object, interaction_ledger, recent_scene_history, session_projection,
)

from shinobi_runtime.api.contracts import (
    CommandPlan, CommandPlanner, CommandPreview, CommandRejectedError,
    OocAuditProvider, OocAuditResult, PersonSheetResolver, PlannerUnavailableError,
)
from shinobi_runtime.api.contract_visibility import compact_contract_discovery_rows, contract_is_player_visible, player_visible_contract_rows
from shinobi_runtime.api.command_discovery import compact_commands
from shinobi_runtime.api.models import validate_bounded_json
from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.martial_world.live_state import player_view_from_person
from shinobi_runtime.martial_world.appearance import appearance_profile
from shinobi_runtime.martial_world.recognition import recognition_assessment
from shinobi_runtime.martial_world.social_presence import derived_site_attendance
from shinobi_runtime.martial_world.calendar_participation import (
    active_event_opportunities, derived_calendar_event_attendance, occurrence_for_ref,
)
from shinobi_runtime.martial_world.civic import civic_people
from shinobi_runtime.martial_world.titles import derive_social_titles
from shinobi_runtime.martial_world.equipment_state import effective_person_loadout
from shinobi_runtime.commands.specs import COMMAND_SPECS
from shinobi_runtime.store import CommittedContentRootCache, RegisteredSchemaValidator, RegisteredTemplateValidator, RepositoryStore
from shinobi_runtime.tx import TransactionCoordinator
from shinobi_runtime.tx.canonical import thaw_json
from shinobi_runtime.tx.errors import DirtyRepositoryError, IdempotencyConflictError, LockUnavailableError, RecoveryError, StaleRevisionError, TransactionError
from shinobi_runtime.tx.locking import SingleWriterLock


@dataclass(frozen=True)
class OperationError(RuntimeError):
    status_code:int
    code:str
    def __post_init__(self):
        if self.status_code<400 or not self.code: raise ValueError('operation errors require HTTP failure status and code')
        RuntimeError.__init__(self,self.code)


class PlanStateChangedError(RuntimeError): pass


def _campaign_year(value: Any) -> int | None:
    text=str(value or '').removeprefix('SE-')
    try:return int(text.split('-',1)[0])
    except (TypeError,ValueError):return None


def _combat_score(person: Mapping[str, Any]) -> int:
    martial = person.get('martial_skills', {}) if isinstance(person.get('martial_skills'), Mapping) else {}
    disciplines = [str(k) for k, v in martial.items() if isinstance(v, int) and not isinstance(v, bool) and int(v) > 0]
    if not disciplines:
        disciplines = ['unarmed']
    best = 0
    for discipline in disciplines:
        try:
            profile = capability_from_person(person, action_skill=discipline)
        except (KeyError, TypeError, ValueError):
            continue
        score = (int(profile.offense) + int(profile.defense) + int(profile.control) + int(profile.mobility) + int(profile.reaction)) // 5
        best = max(best, score)
    return best


class CampaignOperations:
    def __init__(self,*,repository:RepositoryStore,coordinator:TransactionCoordinator,command_planner:CommandPlanner,sheet_resolver:PersonSheetResolver,audit_provider:OocAuditProvider,allowed_actor_ids:FrozenSet[str],lock_timeout_seconds:float)->None:
        if repository.root!=coordinator.repository.root: raise ValueError('operations repository and coordinator differ')
        self.repository=repository; self.coordinator=coordinator; self.command_planner=command_planner; self.sheet_resolver=sheet_resolver; self.audit_provider=audit_provider
        self.allowed_actor_ids=frozenset(allowed_actor_ids); self.lock_timeout_seconds=lock_timeout_seconds
        self.state_roots=CommittedContentRootCache(repository.root,include_roots=('state',),tracked_only=True)
        self.schema_validator=RegisteredSchemaValidator.optional(repository); self.template_validator=RegisteredTemplateValidator.optional(repository)
    def _locked(self): return SingleWriterLock(self.coordinator.lock_path,timeout=self.lock_timeout_seconds)
    def _read_fingerprint(self):
        head=self.coordinator.git.head(); return head,self.state_roots.read(head).root_sha256
    def _require_read_only(self,before,code):
        self.coordinator.git.assert_pristine()
        if self._read_fingerprint()!=before: raise OperationError(503,code)
    def _require_command_base(self,command:CommandEnvelope):
        if command.mode!='gameplay': raise OperationError(403,'public_gameplay_mode_required')
        if command.actor_id not in self.allowed_actor_ids: raise OperationError(403,'actor_not_allowed')

    def campaign_snapshot(self)->Mapping[str,Any]:
        try:
            with self._locked():
                self.coordinator.git.assert_pristine(); before=self._read_fingerprint(); meta=self.repository.read_json('state/meta.json'); self._require_read_only(before,'campaign_snapshot_mutated_campaign')
        except (LockUnavailableError,DirtyRepositoryError) as exc: raise OperationError(503,'campaign_unavailable') from exc
        if not isinstance(meta,Mapping) or meta.get('game')!='jianghu': raise OperationError(503,'campaign_meta_invalid')
        return {'campaign_id':meta['campaign_id'],'revision':int(meta['revision']),'world_time':meta['time'],'state_root':before[1]}

    def _present_ids(self,scene:Mapping[str,Any])->list[str]:
        out=[]
        for field in ('present_person_ids','visible_person_ids','derived_present_person_ids'):
            rows=scene.get(field,[])
            if isinstance(rows,list):
                for ref in rows:
                    if isinstance(ref,str) and ref not in out: out.append(ref)
        return out

    def _validated_scene_person_ids(self, meta: Mapping[str,Any], scene: Mapping[str,Any], *, visible_only: bool=False) -> list[str]:
        """Revalidate presentation cast against exact physical presence.

        Scene JSON may remember who was in the last narrated beat, but it never
        grants current co-presence.  Only exact people still at Wei's effective
        physical location survive this projection.
        """
        player_id=str(meta.get('player_id') or '')
        try: player=self.sheet_resolver(player_id)
        except (FileNotFoundError,KeyError,ValueError): return []
        if not isinstance(player,Mapping): return []
        player_presence=effective_person_presence(self.repository.read_json,player_id,person=player)
        location=player_presence.get('location_ref')
        if not location:return []
        fields=('visible_person_ids',) if visible_only else ('present_person_ids','visible_person_ids')
        candidates=[]
        for field in fields:
            values=scene.get(field,[])
            if isinstance(values,list):
                for ref in values:
                    if isinstance(ref,str) and ref not in candidates:candidates.append(ref)
        out=[]
        for ref in candidates:
            try: person=self.sheet_resolver(ref)
            except (FileNotFoundError,KeyError,ValueError): continue
            if not isinstance(person,Mapping):continue
            if same_effective_location(
                self.repository.read_json, player_id, ref, left_person=player, right_person=person,
            ):
                out.append(ref)
        if player_id and player_id not in out:out.insert(0,player_id)
        return out

    def _derived_public_presence(self, meta: Mapping[str,Any], scene: Mapping[str,Any]) -> list[str]:
        player_id=str(meta.get('player_id') or '')
        player=self.sheet_resolver(player_id)
        presence=effective_person_presence(self.repository.read_json,player_id,person=player) if isinstance(player,Mapping) else {}
        site_ref=str(presence.get('location_ref') or '')
        if not site_ref.startswith('site.'):
            return []
        try:
            site_data=self.repository.read_json('game/data/martial-world/local-sites.json')
            sites=site_data.get('sites',{}) if isinstance(site_data,Mapping) else {}
            site=sites.get(site_ref) if isinstance(sites,Mapping) else None
            if not isinstance(site,Mapping):return []
            parent=str(site.get('parent_place_ref') or '')
            if not parent:return []
            faction_refs=current_faction_refs_at_place(self.repository.read_json,place_ref=parent,sites=sites)
            unavailable=physical_unavailable_person_refs(self.repository.read_json)
            try:
                c=derived_commitment_state(self.repository.read_json)
                idx=c.get('person_index',{}) if isinstance(c,Mapping) else {}
                if isinstance(idx,Mapping):unavailable.update(str(x) for x in idx)
            except FileNotFoundError:pass
            faction_people=[]; headquarters={}
            for fid in sorted(str(x) for x in faction_refs if isinstance(x,str)):
                try:
                    _fp,faction=read_faction(self.repository,fid)
                    roster=hydrate_roster_state(self.repository.read_json(faction_roster_path(fid)), faction=faction)
                except (FileNotFoundError,KeyError,ValueError):
                    continue
                people=roster.get('people',[]) if isinstance(roster,Mapping) else []
                if isinstance(people,list):faction_people.append((fid,[p for p in people if isinstance(p,Mapping)]))
                headquarters[fid]=str(faction.get('headquarters') or parent)
            text=str(meta.get('time') or '').removeprefix('SE-')
            at=datetime.fromisoformat(text)
            exclude=set(self._validated_scene_person_ids(meta,scene))
            return derived_site_attendance(site_ref=site_ref,site=site,faction_people=faction_people,faction_headquarters=headquarters,sites=sites,at=at,unavailable_refs=unavailable,exclude_refs=exclude,limit=None,civic_people=civic_people(self.repository))
        except (FileNotFoundError,KeyError,TypeError,ValueError):
            return []

    def _active_calendar_context(self, meta: Mapping[str,Any], scene: Mapping[str,Any], player_sheet: Mapping[str,Any]) -> list[dict[str,Any]]:
        """Project active systemic/gathering events and exact observed attendees."""
        player_id=str(meta.get('player_id') or '')
        presence=effective_person_presence(self.repository.read_json,player_id,person=player_sheet)
        site_ref=str(presence.get('location_ref') or '')
        if not site_ref.startswith('site.'):
            return []
        try:
            site_data=self.repository.read_json('game/data/martial-world/local-sites.json')
            sites=site_data.get('sites',{}) if isinstance(site_data,Mapping) else {}
            if not isinstance(sites,Mapping) or not isinstance(sites.get(site_ref),Mapping):
                return []
            text=str(meta.get('time') or '').removeprefix('SE-')
            at=datetime.fromisoformat(text)
            faction_ref=str(player_sheet.get('faction_ref') or '')
            faction_hq=''
            if faction_ref:
                try:
                    _fpath,faction=read_faction(self.repository,faction_ref)
                    faction_hq=str(faction.get('local_site_ref') or '')
                except (FileNotFoundError,KeyError,ValueError):
                    faction_hq=''
            opportunities=active_event_opportunities(
                at=at,player_site_ref=site_ref,player_faction_ref=faction_ref,
                player_faction_headquarters=faction_hq,sites=sites,
            )
            local=[row for row in opportunities if row.get('local_available') and row.get('calendar_event_command_available')]
            if not local:
                return opportunities
            parent=str(sites[site_ref].get('parent_place_ref') or '')
            faction_refs=current_faction_refs_at_place(self.repository.read_json,place_ref=parent,sites=sites)
            unavailable=physical_unavailable_person_refs(self.repository.read_json)
            try:
                commitments=derived_commitment_state(self.repository.read_json)
                index=commitments.get('person_index',{}) if isinstance(commitments,Mapping) else {}
                if isinstance(index,Mapping): unavailable.update(str(x) for x in index)
            except FileNotFoundError:
                pass
            faction_people=[]; headquarters={}
            for fid in sorted(str(x) for x in faction_refs if isinstance(x,str)):
                try:
                    _fp,faction=read_faction(self.repository,fid)
                    roster=hydrate_roster_state(self.repository.read_json(faction_roster_path(fid)),faction=faction)
                except (FileNotFoundError,KeyError,ValueError):
                    continue
                people=roster.get('people',[]) if isinstance(roster,Mapping) else []
                if isinstance(people,list): faction_people.append((fid,[p for p in people if isinstance(p,Mapping)]))
                headquarters[fid]=str(faction.get('headquarters') or parent)
            civic=civic_people(self.repository)
            excluded={str(meta.get('player_id') or '')}
            for row in opportunities:
                if not row.get('local_available') or not row.get('calendar_event_command_available'):
                    continue
                occurrence=occurrence_for_ref(str(row.get('event_ref') or ''))
                if not isinstance(occurrence,Mapping):
                    continue
                attendee_refs=derived_calendar_event_attendance(
                    occurrence=occurrence,site_ref=site_ref,site=sites[site_ref],
                    faction_people=faction_people,faction_headquarters=headquarters,sites=sites,at=at,
                    unavailable_refs=unavailable,exclude_refs=excluded,player_faction_ref=faction_ref,
                    civic_people=civic,limit=24,
                )
                if attendee_refs:
                    row['exact_attendee_person_ids']=attendee_refs
            return opportunities
        except (FileNotFoundError,KeyError,TypeError,ValueError):
            return []

    def play_context(self)->Mapping[str,Any]:
        try:
            with self._locked():
                self.coordinator.git.assert_pristine(); before=self._read_fingerprint()
                meta=self.repository.read_json('state/meta.json'); scene=self.repository.read_json('state/scene.json'); player_sheet=self.sheet_resolver(str(meta.get('player_id') or '')); contract_index=self.repository.read_json('state/martial-world/contracts/index.json'); institutional_operations=self.repository.read_json('state/martial-world/institutional-operations.json')
                self._require_read_only(before,'play_context_mutated_campaign')
        except (LockUnavailableError,DirtyRepositoryError) as exc: raise OperationError(503,'campaign_unavailable') from exc
        if not all(isinstance(x,Mapping) for x in (meta,scene,player_sheet,contract_index,institutional_operations)) or meta.get('game')!='jianghu': raise OperationError(503,'campaign_state_invalid')
        player_id=str(meta.get('player_id') or '')
        player_presence=effective_person_presence(self.repository.read_json,player_id,person=player_sheet)
        live_combat=active_combat_for_person(self.repository.read_json,player_id)
        active_session=session_projection(self.repository.read_json)
        if isinstance(active_session,Mapping):
            session_location=str(active_session.get('location_ref') or '')
            player_location=str(player_presence.get('location_ref') or '')
            participants=[str(x) for x in active_session.get('participant_refs',[]) if isinstance(x,str) and x]
            physically_coherent=bool(participants and player_id in participants)
            if physically_coherent:
                for ref in participants:
                    if ref==player_id:
                        continue
                    try:
                        other=self.sheet_resolver(ref)
                        if not isinstance(other,Mapping) or not same_effective_location(
                            self.repository.read_json,player_id,ref,left_person=player_sheet,right_person=other,
                        ):
                            physically_coherent=False; break
                    except (FileNotFoundError,KeyError,TypeError,ValueError):
                        physically_coherent=False; break
            if live_combat is not None or not session_location or session_location!=player_location or not physically_coherent:
                active_session=None
        recent_speech=recent_scene_history(self.repository.read_json,8)
        attempt_state=interaction_ledger(self.repository.read_json)
        scene_view=dict(scene)
        scene_view['world_time']=meta.get('time')
        scene_view['location_id']=player_presence.get('location_ref')
        scene_view['presence_source']='exact_physical_presence_projection'
        scene_view['mechanical_authority']=False
        scene_view['active_combat_ref']=live_combat[0] if live_combat is not None else None
        scene_view['active_combat']=live_combat is not None
        scene_view['time_passage_allowed']=live_combat is None
        scene_view['freeform_actions_allowed']=True
        scene_view['present_person_ids']=self._validated_scene_person_ids(meta,scene)
        scene_view['visible_person_ids']=self._validated_scene_person_ids(meta,scene,visible_only=True)
        if isinstance(active_session,Mapping):
            session_present=[]
            for ref in active_session.get('participant_refs',[]):
                if not isinstance(ref,str):
                    continue
                try:
                    if ref==player_id:
                        session_present.append(ref)
                        continue
                    other=self.sheet_resolver(ref)
                    if isinstance(other,Mapping) and same_effective_location(
                        self.repository.read_json,player_id,ref,left_person=player_sheet,right_person=other,
                    ):
                        session_present.append(ref)
                except (FileNotFoundError,KeyError,TypeError,ValueError):
                    continue
            for key in ('present_person_ids','visible_person_ids'):
                merged=[]
                for ref in list(scene_view.get(key,[]))+session_present:
                    if isinstance(ref,str) and ref not in merged:
                        merged.append(ref)
                scene_view[key]=merged
            scene_view['scene_session_person_ids']=session_present
        derived_present=self._derived_public_presence(meta,scene_view)
        if derived_present:
            scene_view['derived_present_person_ids']=derived_present
            merged=[]
            for ref in list(scene_view.get('present_person_ids',[]))+derived_present:
                if isinstance(ref,str) and ref not in merged:merged.append(ref)
            scene_view['present_person_ids']=merged
        active_events=self._active_calendar_context(meta,scene,player_sheet)
        event_present=[]
        for event in active_events:
            rows=event.get('exact_attendee_person_ids',[]) if isinstance(event,Mapping) else []
            if isinstance(rows,list):
                for ref in rows:
                    if isinstance(ref,str) and ref not in event_present:event_present.append(ref)
        if event_present:
            merged=[]
            for ref in list(scene_view.get('present_person_ids',[]))+event_present:
                if isinstance(ref,str) and ref not in merged:merged.append(ref)
            scene_view['present_person_ids']=merged
            scene_view['event_present_person_ids']=event_present
        player=player_view_from_person(player_sheet)
        player['current_location_id']=player_presence.get('location_ref')
        player['physical_presence']={k:player_presence.get(k) for k in ('location_ref','presence_kind','owner_ref','available_for_site_activity')}
        player['appearance_profile']=appearance_profile(player_sheet,current_year=_campaign_year(meta.get('time')),health=player_sheet.get('health'))
        player['personal_cash_display']=format_copper(player_sheet.get('personal_cash',0))
        visible_contracts=compact_contract_discovery_rows(player_visible_contract_rows(
            contract_index,
            player_id=str(meta.get('player_id') or ''),
            faction_ref=str(player_sheet.get('faction_ref') or ''),
            world_time=str(meta.get('time') or ''),
            read_json=self.repository.read_json,
            include_route_intelligence=False,
        ))
        player_faction_ref=str(player_sheet.get('faction_ref') or '')
        mission_rows=[]
        active_missions=institutional_operations.get('active',{}) if isinstance(institutional_operations,Mapping) else {}
        if isinstance(active_missions,Mapping):
            for mission_ref,row in sorted(active_missions.items(),key=lambda item:str(item[0])):
                if not isinstance(mission_ref,str) or not isinstance(row,Mapping):continue
                participants={str(x) for x in row.get('participant_refs',[]) if isinstance(x,str)} if isinstance(row.get('participant_refs'),list) else set()
                if str(row.get('faction_ref') or '')!=player_faction_ref and str(row.get('assignee_ref') or '')!=str(meta.get('player_id') or '') and str(meta.get('player_id') or '') not in participants:continue
                compact={
                    'object_ref':mission_ref,'operation_ref':mission_ref,'source':str(row.get('mission_source') or ''),
                    'phase':str(row.get('phase') or ''),'mission_kind':str(row.get('mission_kind') or ''),
                    'objective':str(row.get('objective') or '')[:300],'issuer_ref':str(row.get('issuer_ref') or ''),
                    'assignee_ref':str(row.get('assignee_ref') or ''),'commander_ref':str(row.get('commander_ref') or ''),
                }
                for key in ('target_faction_ref','target_site_ref','target_person_ref','linked_contract_ref','physical_operation_ref'):
                    if row.get(key):compact[key]=str(row.get(key))
                if max(0,int(row.get('reward_cash',0) or 0)):
                    compact['reward_display']=format_copper(row.get('reward_cash',0))
                mission_rows.append(compact)
        supported=sorted(getattr(self.command_planner,'COMMAND_TYPES',()))
        command_surface=compact_commands({
            'supported_command_types': supported,
            'limits': {
                'one_semantic_command_per_write': True,
                'preview_before_execute': True,
                'unsupported_intent_fails_closed': True,
            },
        })
        result={
          'campaign':{'campaign_id':meta.get('campaign_id'),'revision':meta.get('revision'),'world_time':meta.get('time'),'state_root':before[1],'player_id':meta.get('player_id'),'game':'jianghu'},
          'scene':scene_view,
          'player':player,
          'person_reads':{'suggested_owner_ids':self._validated_scene_person_ids(meta,scene_view),'roster_query_available':True,'use':'Use list_people for bounded pageable roster discovery, then load exact person sheets when capability matters.'},
          'object_reads':{'supported_ref_prefixes':['faction:','inventory:','contract:','mission:','deployment:','project:','tournament:','market:','site:','scene_history_','scene_history_head','relations','government'],'use':'Inspect one exact Jianghu owner when its current state matters.'},
          'contract_reads':{'available_contracts':visible_contracts,'use':'Inspect an advertised contract object_ref for exact current terms before accepting it.'},
          'mission_reads':{'active_missions':mission_rows,'use':'Inspect a mission object_ref for the current House briefing, council/authorization, participants, physical-operation linkage and compact report state.'},
          'world_events':{'active':active_events,'rule':'Calendar events are real systemic conditions or interactable gatherings. Exact NPC attendees are derived only when locally observed; aggregate crowds remain aggregate.'},
          'commands':command_surface,
          'active_scene_session':active_session,
          'recent_scene_history':recent_speech,
          'active_questions':[
              {k:row.get(k) for k in ('attempt_ref','at','target_ref','player_statement','topic','scopes','scene_session_ref') if row.get(k) not in (None,'',[])}
              for row in reversed(attempt_state.get('attempts',[])) if isinstance(row,Mapping)
              and row.get('thread_status')=='open'
              and isinstance(active_session,Mapping)
              and row.get('scene_session_ref')==active_session.get('session_ref')
          ][:8],
          'narration':{'setting':'Chinese Jianghu/Murim','rule':'Narrate only committed/player-visible truth; physical mechanics determine consequential outcomes. Ordinary participant dialogue may be realized inside the active scene envelope; binding consequences still require their mechanical command.'},
          'context_policy':{'bounded_reads':True,'pageable_rosters':True,'derived_person_lookup':True,'aggregate_civilians':True,'no_omniscient_hidden_state':True},
          'causal_freshness':{'settled_through':self.repository.read_json('state/martial-world/scheduler.json').get('settled_through')},
        }
        try: validate_bounded_json(result,label='play context',allow_float=True)
        except ValueError as exc: raise OperationError(503,'play_context_out_of_bounds') from exc
        return result

    def command_contract(self,command_type:str)->Mapping[str,Any]:
        spec=COMMAND_SPECS.get(command_type)
        if spec is None or command_type not in getattr(self.command_planner,'COMMAND_TYPES',()): raise OperationError(404,'command_contract_not_found')
        return {'command_type':command_type,**spec.public_descriptor()}

    def list_people(
        self, *, faction_ref: str | None = None, site_ref: str | None = None,
        sort_by: str = 'combat', limit: int = 25, cursor: str | None = None,
    ) -> Mapping[str, Any]:
        """Page through player-authorized persistent people without inventing IDs.

        Page size is a transport concern only. Total roster size and simulation
        participation are never truncated by this read API.
        """
        if isinstance(limit,bool) or not isinstance(limit,int) or limit < 1 or limit > 1000:
            raise OperationError(422,'people_limit_invalid')
        try:
            offset = 0 if cursor in (None,'') else int(str(cursor))
        except ValueError as exc:
            raise OperationError(422,'people_cursor_invalid') from exc
        if offset < 0:
            raise OperationError(422,'people_cursor_invalid')
        allowed_sorts={'combat','name','age','grade','sword','medicine','administration','commerce','crafting','instruction'}
        if sort_by not in allowed_sorts:
            raise OperationError(422,'people_sort_invalid')
        try:
            with self._locked():
                self.coordinator.git.assert_pristine(); before=self._read_fingerprint()
                meta=self.repository.read_json('state/meta.json')
                player=self.sheet_resolver(str(meta.get('player_id') or ''))
                if not isinstance(player,Mapping): raise OperationError(503,'campaign_state_invalid')
                player_faction=str(player.get('faction_ref') or '')
                target_faction=str(faction_ref or player_faction)
                if not target_faction or target_faction != player_faction:
                    raise OperationError(404,'people_roster_not_player_visible')
                _fpath,faction=read_faction(self.repository,target_faction)
                roster=hydrate_roster_state(self.repository.read_json(faction_roster_path(target_faction)), faction=faction)
                sites_data=self.repository.read_json('game/data/martial-world/local-sites.json')
                self._require_read_only(before,'people_list_mutated_campaign')
        except OperationError: raise
        except (LockUnavailableError,DirtyRepositoryError) as exc: raise OperationError(503,'campaign_unavailable') from exc
        except (FileNotFoundError,KeyError,TypeError,ValueError) as exc: raise OperationError(404,'people_roster_not_found') from exc
        people=[dict(p) for p in roster.get('people',[]) if isinstance(p,Mapping)] if isinstance(roster.get('people'),list) else []
        player_id=str(meta.get('player_id') or '')
        player_presence=effective_person_presence(self.repository.read_json,player_id,person=player)
        player_site=str(player_presence.get('location_ref') or '')
        if site_ref:
            sites=sites_data.get('sites',{}) if isinstance(sites_data,Mapping) else {}
            site=sites.get(site_ref) if isinstance(sites,Mapping) else None
            if not isinstance(site,Mapping): raise OperationError(404,'people_site_not_found')
            if site_ref != player_site:
                raise OperationError(404,'people_site_not_currently_observable')
            people=[p for p in people if effective_person_presence(self.repository.read_json,str(p.get('person_id') or ''),person=p).get('location_ref')==site_ref]
        year=_campaign_year(meta.get('time')) or 0
        grade_order={'elder':6,'elite':5,'senior':4,'full':3,'junior':2,'probationary':1}
        def key(p:Mapping[str,Any]):
            pid=str(p.get('person_id') or '')
            age=max(0,year-int(p.get('birth_year',year)))
            if sort_by=='combat': return (-_combat_score(p),pid)
            if sort_by=='name': return (str(p.get('name') or ''),pid)
            if sort_by=='age': return (-age,pid)
            if sort_by=='grade': return (-grade_order.get(str(p.get('membership_grade') or ''),0),pid)
            if sort_by in {'sword'}:
                skills=p.get('martial_skills',{}) if isinstance(p.get('martial_skills'),Mapping) else {}
                return (-max(0,int(skills.get(sort_by,0))),pid)
            skills=p.get('professional_skills',{}) if isinstance(p.get('professional_skills'),Mapping) else {}
            return (-max(0,int(skills.get(sort_by,0))),pid)
        people.sort(key=key)
        page=people[offset:offset+limit]
        rows=[]
        for p in page:
            martial=p.get('martial_skills',{}) if isinstance(p.get('martial_skills'),Mapping) else {}
            rows.append({
                'person_id':str(p.get('person_id') or ''),
                'name':str(p.get('name') or ''),
                'age':max(0,year-int(p.get('birth_year',year))),
                'sex':p.get('sex'),
                'membership_grade':p.get('membership_grade'),
                'standing_offices':[str(x) for x in p.get('standing_offices',[]) if isinstance(x,str)] if isinstance(p.get('standing_offices'),list) else [],
                'location_ref':player_site if effective_person_presence(self.repository.read_json,str(p.get('person_id') or ''),person=p).get('location_ref')==player_site else None,
                'location_visibility':'observed_current_site' if effective_person_presence(self.repository.read_json,str(p.get('person_id') or ''),person=p).get('location_ref')==player_site else 'not_live_tracked',
                'combat_capability_score':_combat_score(p),
                'peak_martial_skill':max([max(0,int(v)) for v in martial.values() if isinstance(v,int) and not isinstance(v,bool)] or [0]),
                'qi':max(0,int(p.get('qi',0))),
                'qi_control':max(0,int(p.get('qi_control',0))),
            })
        next_offset=offset+len(page)
        return {
            'faction_ref':target_faction,'site_ref':site_ref,'sort_by':sort_by,
            'total_matching':len(people),'offset':offset,'page_size':len(rows),
            'next_cursor':str(next_offset) if next_offset < len(people) else None,
            'people':rows,
            'causal_freshness':{'settled_through':self.repository.read_json('state/martial-world/scheduler.json').get('settled_through')},
        }

    def person_sheet(self,person_id:str)->Mapping[str,Any]:
        try:
            with self._locked():
                self.coordinator.git.assert_pristine(); before=self._read_fingerprint(); meta=self.repository.read_json('state/meta.json'); scene=self.repository.read_json('state/scene.json'); player=self.sheet_resolver(str(meta.get('player_id') or '')); sheet=self.sheet_resolver(person_id); self._require_read_only(before,'person_sheet_mutated_campaign')
        except (LockUnavailableError,DirtyRepositoryError) as exc: raise OperationError(503,'campaign_unavailable') from exc
        if sheet is None: raise OperationError(404,'person_not_found')
        player_id=meta.get('player_id'); same_faction=sheet.get('faction_ref')==player.get('faction_ref'); visible=person_id in (self._validated_scene_person_ids(meta,scene)+self._derived_public_presence(meta,scene))
        session=active_scene_session(self.repository.read_json)
        session_visible=False
        if isinstance(session,Mapping) and person_id in set(str(x) for x in session.get('participant_refs',[]) if isinstance(x,str)):
            try:
                session_visible = bool(str(player_id)==person_id or same_effective_location(
                    self.repository.read_json,str(player_id),person_id,left_person=player,right_person=sheet,
                ))
                visible=visible or session_visible
            except (FileNotFoundError,KeyError,TypeError,ValueError):
                session_visible=False
        if person_id!=player_id and not same_faction and not visible: raise OperationError(404,'person_not_player_visible')
        view='player_full_logical_sheet' if person_id==player_id else 'player_visible_identity'
        safe=dict(sheet)
        for key in ('secret_notes','hidden_goals','private_knowledge','autonomy_private'): safe.pop(key,None)
        exact_presence=effective_person_presence(self.repository.read_json,person_id,person=sheet)
        if person_id==player_id or visible:
            safe['location_ref']=exact_presence.get('location_ref')
            safe['physical_presence']={k:exact_presence.get(k) for k in ('location_ref','presence_kind','owner_ref','available_for_site_activity')}
        else:
            # Same-House roster identity is not a live tracking channel.
            safe.pop('location_ref',None)
            safe.pop('current_location_id',None)
        safe['appearance_profile']=appearance_profile(sheet,current_year=_campaign_year(meta.get('time')),health=sheet.get('health'))
        safe['personal_cash_display']=format_copper(sheet.get('personal_cash',0))
        recognition=None; social_titles=[]; familiarity=0; relationship_to_player={}
        if visible and person_id!=player_id:
            try:
                ledger=self.repository.read_json('state/martial-world/equipment-ledger.json')
                equipment=self.repository.read_json('game/data/martial-world/equipment.json')
                loadout=effective_person_loadout(ledger,person_id)
                social=self.repository.read_json('state/martial-world/social.json')
                relationships=social.get('relationships',{}) if isinstance(social.get('relationships'),Mapping) else {}
                edge=relationships.get(f'{player_id}|{person_id}',{}) if isinstance(relationships,Mapping) else {}
                familiarity=int(edge.get('familiarity',0)) if isinstance(edge,Mapping) else 0
                incoming=relationships.get(f'{person_id}|{player_id}',{}) if isinstance(relationships,Mapping) else {}
                if isinstance(incoming,Mapping):
                    relationship_to_player={
                        key:max(0,min(100,int(incoming.get(key,0)))) if key=='familiarity' else max(-100,min(100,int(incoming.get(key,0))))
                        for key in ('trust','affection','respect','familiarity')
                        if isinstance(incoming.get(key),int) and not isinstance(incoming.get(key),bool)
                    }
                recognition=recognition_assessment(observer=player,target=sheet,target_items=loadout.get('items',{}),equipment_catalog=equipment,familiarity=familiarity)
            except (FileNotFoundError,KeyError,TypeError,ValueError):
                recognition=None; relationship_to_player={}
        try:
            faction_ref=str(sheet.get('faction_ref') or '')
            current_faction={}
            if faction_ref:
                try:
                    _fpath,current_faction=read_faction(self.repository,faction_ref)
                except (FileNotFoundError,KeyError,ValueError):
                    current_faction={}
            identity=faction_presentation_identity(faction_ref,current_faction)
            family=self.repository.read_json('state/martial-world/family.json')
            knows_identity=bool(person_id==player_id or same_faction or familiarity>=35 or (isinstance(recognition,Mapping) and recognition.get('recognized')))
            knows_faction=bool(same_faction or (knows_identity and faction_ref))
            knows_office=knows_identity
            social_titles=derive_social_titles(sheet,faction_identity=identity,family_state=family,observer_knows_identity=knows_identity,observer_knows_office=knows_office,observer_knows_faction=knows_faction)
        except (FileNotFoundError,KeyError,TypeError,ValueError):
            social_titles=[]
        result={'person_id':person_id,'view':view,'sheet':safe,'causal_freshness':{'settled_through':self.repository.read_json('state/martial-world/scheduler.json').get('settled_through')}}
        if person_id!=player_id and session_visible and isinstance(session,Mapping):
            envelope={
                'speaker_ref':person_id,
                'role':safe.get('standing_offices') or safe.get('membership_grade'),
                'social_titles':list(social_titles),
                'scene_focus':{
                    'kind':session.get('kind'),
                    'process_ref':session.get('process_ref'),
                    'purpose':session.get('purpose'),
                    'agenda':[str(x) for x in session.get('agenda',[]) if isinstance(x,str)][:12],
                },
                'performance_cues_rule':'use established role, social titles, lawful relationship state, observed history, and scene focus as qualitative GM-private guidance; never quote relationship scores or invent private motives/hidden personality facts',
                'may_is_non_exhaustive':True,
                'reversible_dialogue_is_open_ended':True,
                'may':['acknowledge','clarify_player_safe_facts','answer_from_known_facts','react','ask_followup','offer_nonbinding_advice','object','disagree','correct','coordinate','teach_or_explain','bargain_nonbinding','express_supported_emotion','joke_or_tease_if_supported','defer_or_decline_to_answer','speculate_from_known_evidence'],
                'must_preserve_uncertainty':True,
                'factual_basis':'player_safe_runtime_context_only',
                'cannot_establish':['new_secret_fact','formal_authority','resource_transfer','movement','relationship_change','contract_or_oath','mechanical_acceptance_or_refusal'],
                'private_motives_excluded':True,
                'mechanical_consequence_authority':False,
            }
            if relationship_to_player: envelope['relationship_to_player']=relationship_to_player
            result['npc_response_envelope']=envelope
        if recognition is not None:result['recognition']=recognition
        if social_titles:result['social_titles']=social_titles
        try:
            social=self.repository.read_json('state/martial-world/social.json')
            commitments:dict[str,Any]={}
            obligations=social.get('obligations',{}) if isinstance(social,Mapping) else {}
            if isinstance(obligations,Mapping):
                rows=[]
                for ref,row in obligations.items():
                    if not isinstance(ref,str) or not isinstance(row,Mapping):continue
                    actor=str(row.get('actor_ref') or ''); other=str(row.get('counterparty_ref') or '')
                    if person_id==player_id:
                        visible_row=player_id in {actor,other}
                    else:
                        visible_row={actor,other}=={str(player_id),person_id}
                    if visible_row: rows.append({'obligation_ref':ref,**dict(row)})
                if rows:commitments['obligations']=sorted(rows,key=lambda row:str(row.get('obligation_ref') or ''))[:64]
            vows=social.get('vows',{}) if isinstance(social,Mapping) else {}
            if isinstance(vows,Mapping):
                rows=[]
                for ref,row in vows.items():
                    if not isinstance(ref,str) or not isinstance(row,Mapping) or str(row.get('person_ref') or '')!=str(player_id):continue
                    if person_id==player_id or str(row.get('subject_ref') or '')==person_id:
                        rows.append({'vow_ref':ref,**dict(row)})
                if rows:commitments['vows']=sorted(rows,key=lambda row:str(row.get('vow_ref') or ''))[:64]
            beliefs=social.get('beliefs',{}) if isinstance(social,Mapping) else {}
            if isinstance(beliefs,Mapping):
                rows=[]
                for ref,row in beliefs.items():
                    if not isinstance(ref,str) or not isinstance(row,Mapping) or str(row.get('observer_ref') or '')!=str(player_id):continue
                    if person_id==player_id or str(row.get('subject_ref') or '')==person_id:
                        rows.append({'belief_ref':ref,**dict(row)})
                if rows:commitments['beliefs']=sorted(rows,key=lambda row:str(row.get('belief_ref') or ''))[:64]
            familiarity_rows=social.get('martial_familiarity',{}) if isinstance(social,Mapping) else {}
            if isinstance(familiarity_rows,Mapping):
                rows=[]
                for ref,row in familiarity_rows.items():
                    if not isinstance(ref,str) or not isinstance(row,Mapping) or str(row.get('observer_ref') or '')!=str(player_id):continue
                    if person_id==player_id or str(row.get('opponent_ref') or '')==person_id:
                        rows.append({'martial_ref':ref,**dict(row)})
                if rows:commitments['martial_familiarity']=sorted(rows,key=lambda row:str(row.get('martial_ref') or ''))[:64]
            if commitments:result['social_commitments']=commitments
        except (FileNotFoundError,KeyError,TypeError,ValueError):
            pass
        return result

    def inspect_game_object(self,object_ref:str)->Mapping[str,Any]:
        try:
            with self._locked():
                self.coordinator.git.assert_pristine(); before=self._read_fingerprint(); meta=self.repository.read_json('state/meta.json'); player=self.sheet_resolver(str(meta.get('player_id') or '')); obj,view=self._object(object_ref)
                if object_ref.startswith('contract:'):
                    if not isinstance(player,Mapping) or not contract_is_player_visible(
                        obj,
                        player_id=str(meta.get('player_id') or ''),
                        faction_ref=str(player.get('faction_ref') or ''),
                        world_time=str(meta.get('time') or ''),
                    ):
                        raise OperationError(404,'object_not_found')
                if object_ref.startswith('mission:'):
                    participants={str(x) for x in obj.get('participant_refs',[]) if isinstance(x,str)} if isinstance(obj,Mapping) and isinstance(obj.get('participant_refs'),list) else set()
                    player_id=str(meta.get('player_id') or '')
                    player_faction=str(player.get('faction_ref') or '') if isinstance(player,Mapping) else ''
                    if not isinstance(obj,Mapping) or (str(obj.get('faction_ref') or '')!=player_faction and str(obj.get('assignee_ref') or '')!=player_id and player_id not in participants):
                        raise OperationError(404,'object_not_found')
                self._require_read_only(before,'object_inspection_mutated_campaign')
        except OperationError: raise
        except (LockUnavailableError,DirtyRepositoryError) as exc: raise OperationError(503,'campaign_unavailable') from exc
        except (FileNotFoundError,KeyError,TypeError,ValueError) as exc: raise OperationError(404,'object_not_found') from exc
        if object_ref.startswith('contract:') and isinstance(obj,Mapping):
            obj=dict(obj); obj['reward_display']=format_copper(obj.get('reward_cash',0)); obj['escrow_display']=format_copper(obj.get('escrow_cash',0))
            objective=obj.get('objective',{}) if isinstance(obj.get('objective'),Mapping) else {}
            escort_objective=str(obj.get('contract_type') or '') == 'escort' or str(objective.get('kind') or '').startswith('escort_')
            if escort_objective:
                try:
                    hydrated=hydrate_contract_escort_objective(objective)
                except (KeyError,TypeError,ValueError) as exc:
                    raise OperationError(409,'invalid_contract_state') from exc
                obj['objective']=hydrated
                route_refs=[str(x) for x in hydrated.get('route_refs',[]) if isinstance(x,str)] if isinstance(hydrated.get('route_refs'),list) else []
                route_ref=str(hydrated.get('route_ref') or objective.get('route_ref') or '')
                if len(route_refs) > 1:
                    intelligence=journey_intelligence_brief(route_refs,source_place_ref=str(hydrated.get("source_place_ref") or ""),destination_place_ref=str(hydrated.get("destination_place_ref") or ""),read_json=self.repository.read_json)
                    if intelligence:obj['route_intelligence']=intelligence
                elif route_ref:
                    intelligence=route_intelligence_brief(route_ref, source_place_ref=str(hydrated.get("source_place_ref") or "") or None, destination_place_ref=str(hydrated.get("destination_place_ref") or "") or None, read_json=self.repository.read_json)
                    if intelligence:obj['route_intelligence']=intelligence
        return {'object_ref':object_ref,'view':view,'object':obj,'causal_freshness':{'settled_through':self.repository.read_json('state/martial-world/scheduler.json').get('settled_through')}}

    def _object(self,ref:str):
        if ref.startswith('faction:'):
            fid=ref.split(':',1)[1]; _path,row=read_faction(self.repository,fid); return row,'faction_summary'
        if ref.startswith('inventory:'):
            fid=ref.split(':',1)[1]; return hydrate_inventory_state(self.repository.read_json(f'state/martial-world/inventories/{fid}.json')),'inventory_summary'
        if ref.startswith('contract:'):
            cid=ref.split(':',1)[1]; index=self.repository.read_json('state/martial-world/contracts/index.json'); row=index.get('active',{}).get(cid) or index.get('archive',{}).get(cid); return dict(row),'contract_summary'
        if ref.startswith('mission:'):
            state=self.repository.read_json('state/martial-world/institutional-operations.json')
            row=state.get('active',{}).get(ref) if isinstance(state.get('active'),Mapping) else None
            if row is None and isinstance(state.get('archive'),Mapping):row=state.get('archive',{}).get(ref)
            if not isinstance(row,Mapping):raise OperationError(404,'object_not_found')
            return dict(row),'institutional_mission_summary'
        if ref.startswith('deployment:'):
            did=ref.split(':',1)[1]; row=self.repository.read_json('state/martial-world/deployments.json').get('deployments',{}).get(did); return dict(row),'deployment_summary'
        if ref.startswith('project:'):
            pid=ref.split(':',1)[1]; row=self.repository.read_json('state/martial-world/projects.json').get('projects',{}).get(pid); return dict(row),'project_summary'
        if ref.startswith('tournament:'):
            tid=ref.split(':',1)[1]; row=self.repository.read_json('state/martial-world/tournaments.json').get('tournaments',{}).get(tid); return dict(row),'tournament_summary'
        if ref.startswith('market:'):
            rid=ref.split(':',1)[1]; return dict(self.repository.read_json(f'state/martial-world/markets/{rid}.json')),'market_summary'
        if ref.startswith('site:'):
            data=self.repository.read_json('game/data/martial-world/local-sites.json'); row=data.get('sites',{}).get(ref) if isinstance(data.get('sites'),Mapping) else None
            if row is None and isinstance(data.get('sites'),list): row=next((x for x in data['sites'] if isinstance(x,Mapping) and x.get('id')==ref),None)
            return dict(row),'place_summary'
        if ref=='scene_history_head' or ref.startswith('scene_history_'):
            row=inspect_history_object(self.repository.read_json,ref)
            if not isinstance(row,Mapping): raise OperationError(404,'object_not_found')
            return dict(row),'attributed_scene_history'
        if ref=='relations': return dict(self.repository.read_json('state/martial-world/faction-relations.json')),'relations_summary'
        if ref=='government': return dict(self.repository.read_json('state/martial-world/government.json')),'government_summary'
        raise OperationError(404,'object_not_found')

    def preview_command(self,command:CommandEnvelope)->Mapping[str,Any]:
        self._require_command_base(command)
        try:
            with self._locked():
                self.coordinator.git.assert_pristine(); before=self._read_fingerprint(); preview=self.command_planner.preview(command); self._require_read_only(before,'preview_mutated_campaign')
        except StaleRevisionError as exc: raise OperationError(409,'stale_revision') from exc
        except PlannerUnavailableError as exc: raise OperationError(503,'planner_unavailable') from exc
        except CommandRejectedError as exc: raise OperationError(422,exc.code) from exc
        except (LockUnavailableError,DirtyRepositoryError) as exc: raise OperationError(503,'campaign_unavailable') from exc
        if not isinstance(preview,CommandPreview): raise OperationError(503,'planner_preview_invalid')
        return {'status':preview.status,'code':preview.code,'target_revision':preview.target_revision,'affected_refs':list(preview.affected_refs)}

    @staticmethod
    def _receipt_response(status,receipt):
        return {'status':status,'request_id':receipt.request_id,'transaction_id':receipt.transaction_id,'campaign_id':receipt.campaign_id,'committed_revision':receipt.committed_revision,'committed_at':receipt.committed_at,'result':thaw_json(receipt.result)}

    def execute_command(self,command:CommandEnvelope)->Mapping[str,Any]:
        self._require_command_base(command)
        try:
            existing=self.coordinator.lookup_receipt(command)
            if existing is not None:return self._receipt_response('duplicate',existing)
            with self._locked():
                self.coordinator.git.assert_pristine(); before=self._read_fingerprint(); plan=self.command_planner.plan(command); self._require_read_only(before,'planner_mutated_campaign'); planned_head,planned_root=before
            def guarded(overlay,manifest):
                if self.coordinator.git.head()!=planned_head or self.state_roots.read(planned_head).root_sha256!=planned_root: raise PlanStateChangedError()
                if self.schema_validator is not None:self.schema_validator.validate_overlay(overlay,manifest.paths)
                if self.template_validator is not None:self.template_validator.validate_overlay(overlay,manifest.paths)
                plan.validator(overlay,manifest)
            execution=self.coordinator.execute(command,transaction_id=plan.transaction_id,created_at=plan.created_at,writes=plan.writes,result=thaw_json(plan.result),validator=guarded)
        except CommandRejectedError as exc: raise OperationError(422,exc.code) from exc
        except StaleRevisionError as exc: raise OperationError(409,'stale_revision') from exc
        except IdempotencyConflictError as exc: raise OperationError(409,'idempotency_conflict') from exc
        except PlanStateChangedError as exc: raise OperationError(409,'planned_state_changed') from exc
        except (LockUnavailableError,DirtyRepositoryError,RecoveryError) as exc: raise OperationError(503,'campaign_unavailable') from exc
        except TransactionError as exc: raise OperationError(409,'transaction_rejected') from exc
        return self._receipt_response(execution.status,execution.receipt)

    def lookup_command_receipt(self,command:CommandEnvelope)->Optional[Mapping[str,Any]]:
        self._require_command_base(command)
        try: existing=self.coordinator.lookup_receipt(command)
        except IdempotencyConflictError as exc: raise OperationError(409,'idempotency_conflict') from exc
        return None if existing is None else self._receipt_response('duplicate',existing)

    def ooc_audit(self,focus:Optional[str],observations:Sequence[str])->Mapping[str,Any]:
        try:
            with self._locked():
                self.coordinator.git.assert_pristine(); before=self._read_fingerprint(); report=self.audit_provider(focus,tuple(observations)); self._require_read_only(before,'ooc_audit_mutated_campaign')
        except (LockUnavailableError,DirtyRepositoryError) as exc: raise OperationError(503,'campaign_unavailable') from exc
        if not isinstance(report,OocAuditResult) or report.write_plan is not None: raise OperationError(503,'ooc_audit_invalid')
        return {'diagnostics':list(report.diagnostics),'suggestions':list(report.suggestions)}


__all__=['CampaignOperations','OperationError','PlanStateChangedError']
