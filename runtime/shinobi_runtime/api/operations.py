"""Bounded campaign operations for the single Jianghu runtime."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, FrozenSet, Mapping, Optional, Sequence
from shinobi_runtime.martial_world.faction_state import read_faction, roster_path as faction_roster_path
from shinobi_runtime.martial_world.inventory_state import hydrate_inventory_state

from shinobi_runtime.api.contracts import (
    CommandPlan, CommandPlanner, CommandPreview, CommandRejectedError,
    OocAuditProvider, OocAuditResult, PersonSheetResolver, PlannerUnavailableError,
)
from shinobi_runtime.api.contract_visibility import contract_is_player_visible, player_visible_contract_rows
from shinobi_runtime.api.models import validate_bounded_json
from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.martial_world.live_state import player_view_from_person
from shinobi_runtime.martial_world.appearance import appearance_profile
from shinobi_runtime.martial_world.recognition import recognition_assessment
from shinobi_runtime.martial_world.social_presence import derived_site_attendance
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
        for field in ('present_person_ids','visible_person_ids'):
            rows=scene.get(field,[])
            if isinstance(rows,list):
                for ref in rows:
                    if isinstance(ref,str) and ref not in out: out.append(ref)
        return out[:64]

    def _derived_public_presence(self, meta: Mapping[str,Any], scene: Mapping[str,Any]) -> list[str]:
        site_ref=str(scene.get('location_id') or '')
        if not site_ref.startswith('site.'):
            return []
        try:
            site_data=self.repository.read_json('game/data/martial-world/local-sites.json')
            sites=site_data.get('sites',{}) if isinstance(site_data,Mapping) else {}
            site=sites.get(site_ref) if isinstance(sites,Mapping) else None
            if not isinstance(site,Mapping):return []
            parent=str(site.get('parent_place_ref') or '')
            if not parent:return []
            routing=self.repository.read_json('game/data/martial-world/settlement-faction-index.json')
            faction_refs=routing.get('by_place',{}).get(parent,[]) if isinstance(routing,Mapping) else []
            if not isinstance(faction_refs,list):return []
            unavailable=set()
            try:
                c=self.repository.read_json('state/martial-world/commitments.json')
                idx=c.get('person_index',{}) if isinstance(c,Mapping) else {}
                if isinstance(idx,Mapping):unavailable.update(str(x) for x in idx)
            except FileNotFoundError:pass
            try:
                c=self.repository.read_json('state/martial-world/custody.json')
                for row in c.get('records',[]) if isinstance(c,Mapping) else []:
                    if isinstance(row,Mapping) and row.get('status') not in {'released','escaped','executed'} and isinstance(row.get('person_ref'),str):unavailable.add(str(row['person_ref']))
            except FileNotFoundError:pass
            try:
                c=self.repository.read_json('state/martial-world/combats.json')
                for row in c.get('combats',{}).values() if isinstance(c,Mapping) and isinstance(c.get('combats'),Mapping) else []:
                    if not isinstance(row,Mapping) or row.get('status')!='active':continue
                    sides=row.get('sides',{}) if isinstance(row.get('sides'),Mapping) else {}
                    for side in ('side_a','side_b'):
                        members=sides.get(side,[]) if isinstance(sides,Mapping) else []
                        if isinstance(members,list):unavailable.update(str(x) for x in members if isinstance(x,str))
            except FileNotFoundError:pass
            faction_people=[]; headquarters={}
            for fid in sorted(str(x) for x in faction_refs if isinstance(x,str)):
                try:
                    _fp,faction=read_faction(self.repository,fid)
                    roster=self.repository.read_json(faction_roster_path(fid))
                except (FileNotFoundError,KeyError,ValueError):
                    continue
                people=roster.get('people',[]) if isinstance(roster,Mapping) else []
                if isinstance(people,list):faction_people.append((fid,[p for p in people if isinstance(p,Mapping)]))
                headquarters[fid]=str(faction.get('headquarters') or parent)
            text=str(meta.get('time') or '').removeprefix('SE-')
            at=datetime.fromisoformat(text)
            exclude=set(self._present_ids(scene))
            return derived_site_attendance(site_ref=site_ref,site=site,faction_people=faction_people,faction_headquarters=headquarters,sites=sites,at=at,unavailable_refs=unavailable,exclude_refs=exclude,limit=16,civic_people=civic_people(self.repository))
        except (FileNotFoundError,KeyError,TypeError,ValueError):
            return []

    def play_context(self)->Mapping[str,Any]:
        try:
            with self._locked():
                self.coordinator.git.assert_pristine(); before=self._read_fingerprint()
                meta=self.repository.read_json('state/meta.json'); scene=self.repository.read_json('state/scene.json'); player_sheet=self.sheet_resolver(str(meta.get('player_id') or '')); contract_index=self.repository.read_json('state/martial-world/contracts/index.json')
                self._require_read_only(before,'play_context_mutated_campaign')
        except (LockUnavailableError,DirtyRepositoryError) as exc: raise OperationError(503,'campaign_unavailable') from exc
        if not all(isinstance(x,Mapping) for x in (meta,scene,player_sheet,contract_index)) or meta.get('game')!='jianghu': raise OperationError(503,'campaign_state_invalid')
        scene_view=dict(scene); scene_view['world_time']=meta.get('time'); scene_view['active_combat']=bool(scene_view.get('active_combat_ref')); scene_view['time_passage_allowed']=not scene_view['active_combat']; scene_view['freeform_actions_allowed']=True
        derived_present=self._derived_public_presence(meta,scene)
        if derived_present:
            scene_view['derived_present_person_ids']=derived_present
            merged=[]
            for ref in list(scene_view.get('present_person_ids',[]))+derived_present:
                if isinstance(ref,str) and ref not in merged:merged.append(ref)
            scene_view['present_person_ids']=merged[:64]
        player=player_view_from_person(player_sheet)
        player['appearance_profile']=appearance_profile(player_sheet,current_year=_campaign_year(meta.get('time')),health=player_sheet.get('health'))
        visible_contracts=player_visible_contract_rows(
            contract_index,
            player_id=str(meta.get('player_id') or ''),
            faction_ref=str(player_sheet.get('faction_ref') or ''),
            world_time=str(meta.get('time') or ''),
        )
        supported=sorted(getattr(self.command_planner,'COMMAND_TYPES',()))
        commands={name:COMMAND_SPECS[name].public_descriptor() for name in supported if name in COMMAND_SPECS}
        result={
          'campaign':{'campaign_id':meta.get('campaign_id'),'revision':meta.get('revision'),'world_time':meta.get('time'),'state_root':before[1],'player_id':meta.get('player_id'),'game':'jianghu'},
          'scene':scene_view,
          'player':player,
          'person_reads':{'suggested_owner_ids':self._present_ids(scene_view),'use':'Load one person sheet when a person materially affects the current scene.'},
          'object_reads':{'supported_ref_prefixes':['faction:','inventory:','contract:','deployment:','project:','tournament:','market:','site:','relations','government'],'use':'Inspect one exact Jianghu owner when its current state matters.'},
          'contract_reads':{'available_contracts':visible_contracts,'use':'Inspect an advertised contract object_ref for exact current terms before accepting it.'},
          'commands':{'supported_command_types':supported,'command_types':commands,'limits':{'one_semantic_command_per_write':True,'preview_before_execute':True,'unsupported_intent_fails_closed':True}},
          'narration':{'setting':'Chinese Jianghu/Murim','rule':'Narrate only committed/player-visible truth; physical mechanics determine consequential outcomes.'},
          'context_policy':{'bounded_reads':True,'direct_person_routes':True,'aggregate_civilians':True,'no_omniscient_hidden_state':True},
          'causal_freshness':{'settled_through':self.repository.read_json('state/martial-world/scheduler.json').get('settled_through')},
        }
        try: validate_bounded_json(result,label='play context',allow_float=True)
        except ValueError as exc: raise OperationError(503,'play_context_out_of_bounds') from exc
        return result

    def command_contract(self,command_type:str)->Mapping[str,Any]:
        spec=COMMAND_SPECS.get(command_type)
        if spec is None or command_type not in getattr(self.command_planner,'COMMAND_TYPES',()): raise OperationError(404,'command_contract_not_found')
        return {'command_type':command_type,**spec.public_descriptor()}

    def person_sheet(self,person_id:str)->Mapping[str,Any]:
        try:
            with self._locked():
                self.coordinator.git.assert_pristine(); before=self._read_fingerprint(); meta=self.repository.read_json('state/meta.json'); scene=self.repository.read_json('state/scene.json'); player=self.sheet_resolver(str(meta.get('player_id') or '')); sheet=self.sheet_resolver(person_id); self._require_read_only(before,'person_sheet_mutated_campaign')
        except (LockUnavailableError,DirtyRepositoryError) as exc: raise OperationError(503,'campaign_unavailable') from exc
        if sheet is None: raise OperationError(404,'person_not_found')
        player_id=meta.get('player_id'); same_faction=sheet.get('faction_ref')==player.get('faction_ref'); visible=person_id in (self._present_ids(scene)+self._derived_public_presence(meta,scene))
        if person_id!=player_id and not same_faction and not visible: raise OperationError(404,'person_not_player_visible')
        view='player_full_logical_sheet' if person_id==player_id else 'player_visible_identity'
        safe=dict(sheet)
        # Private behavioral/hidden-knowledge fields are never exposed even if later added.
        for key in ('secret_notes','hidden_goals','private_knowledge','autonomy_private'): safe.pop(key,None)
        safe['appearance_profile']=appearance_profile(sheet,current_year=_campaign_year(meta.get('time')),health=sheet.get('health'))
        recognition=None; social_titles=[]; familiarity=0
        if visible and person_id!=player_id:
            try:
                ledger=self.repository.read_json('state/martial-world/equipment-ledger.json')
                equipment=self.repository.read_json('game/data/martial-world/equipment.json')
                loadout=effective_person_loadout(ledger,person_id)
                social=self.repository.read_json('state/martial-world/social.json')
                edge=social.get('relationships',{}).get(f'{player_id}|{person_id}',{}) if isinstance(social.get('relationships'),Mapping) else {}
                familiarity=int(edge.get('familiarity',0)) if isinstance(edge,Mapping) else 0
                recognition=recognition_assessment(observer=player,target=sheet,target_items=loadout.get('items',{}),equipment_catalog=equipment,familiarity=familiarity)
            except (FileNotFoundError,KeyError,TypeError,ValueError):
                recognition=None
        # Contextual titles are derived only after lawful identity knowledge.
        # Merely seeing House Tang clothing is not enough to call a stranger
        # Young Master Tang, because clothing provenance is not membership.
        try:
            faction_ref=str(sheet.get('faction_ref') or '')
            identities=self.repository.read_json('game/data/martial-world/faction-identities.json').get('identities',{})
            identity=identities.get(faction_ref,{}) if faction_ref and isinstance(identities,Mapping) else {}
            family=self.repository.read_json('state/martial-world/family.json')
            knows_identity=bool(person_id==player_id or same_faction or familiarity>=35 or (isinstance(recognition,Mapping) and recognition.get('recognized')))
            knows_faction=bool(same_faction or (knows_identity and faction_ref))
            knows_office=knows_identity
            social_titles=derive_social_titles(sheet,faction_identity=identity,family_state=family,observer_knows_identity=knows_identity,observer_knows_office=knows_office,observer_knows_faction=knows_faction)
        except (FileNotFoundError,KeyError,TypeError,ValueError):
            social_titles=[]
        result={'person_id':person_id,'view':view,'sheet':safe,'causal_freshness':{'settled_through':self.repository.read_json('state/martial-world/scheduler.json').get('settled_through')}}
        if recognition is not None:result['recognition']=recognition
        if social_titles:result['social_titles']=social_titles
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
                self._require_read_only(before,'object_inspection_mutated_campaign')
        except OperationError: raise
        except (LockUnavailableError,DirtyRepositoryError) as exc: raise OperationError(503,'campaign_unavailable') from exc
        except (FileNotFoundError,KeyError,TypeError,ValueError) as exc: raise OperationError(404,'object_not_found') from exc
        return {'object_ref':object_ref,'view':view,'object':obj,'causal_freshness':{'settled_through':self.repository.read_json('state/martial-world/scheduler.json').get('settled_through')}}

    def _object(self,ref:str):
        if ref.startswith('faction:'):
            fid=ref.split(':',1)[1]; _path,row=read_faction(self.repository,fid); return row,'faction_summary'
        if ref.startswith('inventory:'):
            fid=ref.split(':',1)[1]; return hydrate_inventory_state(self.repository.read_json(f'state/martial-world/inventories/{fid}.json')),'inventory_summary'
        if ref.startswith('contract:'):
            cid=ref.split(':',1)[1]; index=self.repository.read_json('state/martial-world/contracts/index.json'); row=index.get('active',{}).get(cid) or index.get('archive',{}).get(cid); return dict(row),'contract_summary'
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
