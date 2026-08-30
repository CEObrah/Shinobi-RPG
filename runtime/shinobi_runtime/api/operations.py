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
from shinobi_runtime.martial_world.regional_economy import region_for_place
from shinobi_runtime.martial_world.physical_presence import (
    active_combat_for_person, effective_person_presence, physical_unavailable_person_refs, same_effective_location,
)
from shinobi_runtime.martial_world.scene_sessions import (
    active_scene_session, active_scene_thread_page, inspect_history_object, interaction_ledger, recent_scene_history, session_projection,
)

from shinobi_runtime.api.command_discovery import compact_command_family
from shinobi_runtime.api.contracts import (
    CommandPlan, CommandPlanner, CommandPreview, CommandRejectedError,
    OocAuditProvider, OocAuditResult, PersonSheetResolver, PlannerUnavailableError,
)
from shinobi_runtime.api.contract_visibility import compact_contract_discovery_rows, contract_is_player_visible, player_visible_contract_rows
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


def _project_active_session_presence(
    read_json,
    sheet_resolver,
    session: Mapping[str, Any] | None,
    *,
    player_id: str,
    player_sheet: Mapping[str, Any],
    player_location: str,
    live_combat: object,
) -> dict[str, Any] | None:
    """Keep a live multi-person scene when only some participants depart.

    Session membership is continuity metadata, not physical authority.  Fresh
    context therefore revalidates every participant and exposes only people
    still co-located with Wei.  The durable session remains untouched; exact
    speech/fact commands independently revalidate co-location before writes.
    """
    if live_combat is not None or not isinstance(session, Mapping):
        return None
    if not player_location or str(session.get('location_ref') or '') != player_location:
        return None
    original=[str(x) for x in session.get('participant_refs',[]) if isinstance(x,str) and x]
    if player_id not in original:
        return None
    present=[player_id]
    absent=[]
    for ref in original:
        if ref==player_id:
            continue
        try:
            other=sheet_resolver(ref)
            colocated=isinstance(other,Mapping) and same_effective_location(
                read_json,player_id,ref,left_person=player_sheet,right_person=other,
            )
        except (FileNotFoundError,KeyError,TypeError,ValueError):
            colocated=False
        if colocated: present.append(ref)
        else: absent.append(ref)
    if len(present)<2:
        return None
    projected=dict(session)
    projected['participant_refs']=present
    projected['participant_count']=len(present)
    if absent:
        projected['physically_absent_participant_refs']=absent
        projected['physically_absent_participant_count']=len(absent)
        projected['participant_projection_rule']='fresh read-only physical-presence projection; durable session membership remains continuity-only'
    return projected


def _hot_active_scene_thread_rows(
    attempt_state: Mapping[str, Any], active_session: Mapping[str, Any] | None,
) -> list[Mapping[str, Any]]:
    """Return unresolved threads whose target is in the fresh live cast.

    Durable session membership intentionally survives a participant walking out
    so an unanswered request can resume if they return.  That continuity must
    not make the departed person immediately answerable in the current room.
    """
    if not isinstance(active_session, Mapping):
        return []
    session_ref = active_session.get('session_ref')
    present_targets = {
        str(ref) for ref in active_session.get('participant_refs', [])
        if isinstance(ref, str) and ref
    }
    return [
        row for row in reversed(attempt_state.get('attempts', []))
        if isinstance(row, Mapping)
        and row.get('thread_status') == 'open'
        and row.get('scene_session_ref') == session_ref
        and (not present_targets or str(row.get('target_ref') or '') in present_targets)
    ]


def _gm_private_relationship_cognition(relationship_to_player: Mapping[str, Any]) -> dict[str, Any]:
    """Bounded private social context for NPC performance, never player knowledge.

    Shinobi deliberately keeps most exact people lightweight rather than storing a
    prose personality dossier for every martial identity. The directional social
    edge is nevertheless real campaign state and is useful to the AI when deciding
    whether a present NPC sounds warm, guarded, deferential, irritated, trusting,
    or skeptical. Exposing it only inside a clearly private cognition envelope
    avoids starving ordinary dialogue without turning those values into facts Wei
    automatically knows.
    """
    if not isinstance(relationship_to_player, Mapping):
        return {}
    vector = {
        key: int(value)
        for key, value in relationship_to_player.items()
        if key in {"trust", "affection", "respect", "familiarity"}
        and isinstance(value, int) and not isinstance(value, bool)
    }
    if not vector:
        return {}
    return {
        "relationship_to_player": vector,
        "privacy": "gm_private_cognition_not_player_knowledge",
        "use_rule": (
            "Use this directional relationship only as qualitative decision/performance context for the NPC. "
            "Never quote the scores, expose them as Wei's knowledge, convert them into a guaranteed emotion or response, "
            "or use them to establish a hard mechanical consequence. The AI still authors the actual human performance."
        ),
    }


def _bounded_private_value(value: Any, *, depth: int = 0) -> Any:
    """Bound private cognition payloads without converting them to player facts."""
    if depth >= 3:
        return None
    if isinstance(value, str):
        return value[:600]
    if isinstance(value, (int, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        out = []
        for item in value[:16]:
            bounded = _bounded_private_value(item, depth=depth + 1)
            if bounded is not None:
                out.append(bounded)
        return out
    if isinstance(value, Mapping):
        out = {}
        for key in sorted(str(k) for k in value.keys())[:24]:
            bounded = _bounded_private_value(value.get(key), depth=depth + 1)
            if bounded is not None:
                out[key] = bounded
        return out
    return None


def _gm_private_person_cognition(person: Mapping[str, Any], relationship_to_player: Mapping[str, Any]) -> dict[str, Any]:
    """Current-scene private character truth for coherent AI direction.

    This packet is intentionally richer than player knowledge. If future person
    owners store private goals, memories, motives, knowledge or autonomy policy,
    the GM may use them to decide how that person behaves. The boundary is at
    disclosure: hidden entries cannot simply be narrated to Wei.
    """
    out = _gm_private_relationship_cognition(relationship_to_player)
    for key in (
        "secret_notes", "hidden_goals", "private_knowledge", "autonomy_private",
        "goal_state", "memories", "personality", "temperament", "internal_state",
    ):
        if key not in person:
            continue
        bounded = _bounded_private_value(person.get(key))
        if bounded not in (None, [], {}):
            out[key] = bounded
    if out:
        out["privacy"] = "gm_private_cognition_not_player_knowledge"
        out["use_rule"] = (
            "Use this private character truth to keep the NPC's choices, lies, omissions, priorities and emotional performance coherent. "
            "Do not state hidden entries as Wei's knowledge or reveal them through narration/choices unless they become perceptible or the NPC lawfully discloses them. "
            "Hard consequences still require their mechanical authority."
        )
    return out




def _gm_private_person_scene_truth(person: Mapping[str, Any]) -> dict[str, Any]:
    """Compact exact person truth for bounded GM scene direction."""
    out: dict[str, Any] = {}
    for key in (
        "person_id", "name", "faction_ref", "membership_grade", "standing_offices",
        "health", "fatigue", "attributes", "martial_skills", "professional_skills",
        "qi", "qi_control", "goal_state", "current_equipment_state",
    ):
        value = person.get(key)
        if value in (None, "", [], {}):
            continue
        bounded = _bounded_private_value(value)
        if bounded not in (None, [], {}):
            out[key] = bounded
    if not out:
        return {}
    out["privacy"] = "gm_private_scene_bounded_omniscient_truth_not_player_knowledge"
    out["mechanical_authority"] = False
    return out

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


def _bounded_social_commitment_rows(rows: list[dict[str, Any]], ref_key: str, *, limit: int = 64) -> tuple[list[dict[str, Any]], int, bool]:
    """Bound one player-visible social history without lying about its size."""
    ordered = sorted(rows, key=lambda row: str(row.get(ref_key) or ''))
    bounded = ordered[:max(1, int(limit))]
    return bounded, len(ordered), len(ordered) > len(bounded)


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

    def _gm_private_scene_director_context(
        self, meta: Mapping[str, Any], authored_scene: Mapping[str, Any], scene_view: Mapping[str, Any], player_sheet: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Bounded backstage context for exact established scene participants.

        This is deliberately available before a formal conversation session so
        the AI can direct spontaneous NPC initiative. It is never player
        knowledge and never grants mechanical consequence authority.
        """
        player_id = str(meta.get("player_id") or "")
        refs: list[str] = []

        def prioritize(ref: object) -> None:
            if isinstance(ref, str) and ref != player_id and ref not in refs:
                refs.append(ref)

        # A live conversation/council participant is more important to NPC
        # direction than an older authored-cast ordering.  Put the strongest
        # current presence signals first, then fill from the remaining exact
        # established cast.  This keeps a bounded packet without making the
        # first eight IDs an accidental characterization whitelist.
        for field in ("scene_session_person_ids", "event_present_person_ids", "present_person_ids", "derived_present_person_ids"):
            values = scene_view.get(field, [])
            if not isinstance(values, list):
                continue
            for ref in values:
                prioritize(ref)
        for ref in self._validated_scene_person_ids(meta, authored_scene):
            prioritize(ref)
        if not refs:
            return {}
        try:
            social = self.repository.read_json("state/martial-world/social.json")
        except (FileNotFoundError, KeyError, TypeError, ValueError):
            social = {}
        relationships = social.get("relationships", {}) if isinstance(social, Mapping) else {}
        if not isinstance(relationships, Mapping):
            relationships = {}
        people: list[dict[str, Any]] = []
        accepted: list[str] = []
        for ref in refs:
            try:
                person = self.sheet_resolver(ref)
                if not isinstance(person, Mapping) or not same_effective_location(
                    self.repository.read_json, player_id, ref, left_person=player_sheet, right_person=person
                ):
                    continue
            except (FileNotFoundError, KeyError, TypeError, ValueError):
                continue
            incoming = relationships.get(f"{ref}|{player_id}", {})
            row = {"person_ref": ref}
            truth = _gm_private_person_scene_truth(person)
            cognition = _gm_private_person_cognition(person, incoming if isinstance(incoming, Mapping) else {})
            if truth:
                row["character_truth"] = truth
            if cognition:
                row["cognition"] = cognition
            people.append(row)
            accepted.append(ref)
            if len(people) >= 16:
                break
        if not people:
            return {}
        allowed = {player_id, *accepted}
        edges: list[dict[str, Any]] = []
        for edge_ref, edge in sorted(relationships.items(), key=lambda item: str(item[0])):
            if not isinstance(edge_ref, str) or "|" not in edge_ref or not isinstance(edge, Mapping):
                continue
            left, right = edge_ref.split("|", 1)
            if left not in allowed or right not in allowed or left == right:
                continue
            vector = {
                key: int(edge[key]) for key in ("trust", "affection", "respect", "familiarity")
                if isinstance(edge.get(key), int) and not isinstance(edge.get(key), bool)
            }
            if vector:
                edges.append({"source_ref": left, "target_ref": right, "dimensions": vector})
            if len(edges) >= 24:
                break
        return {
            "privacy": "gm_private_scene_bounded_omniscient_truth_not_player_knowledge",
            "scope": "exact_established_scene_participants_only",
            "present_people": people,
            "candidate_present_people_count": len(refs),
            "present_people_context_count": len(people),
            "present_people_context_truncated": len(refs) > len(people),
            "selection_rule": "active scene-session and event participants first; remaining exact present/derived/authored cast fill the bounded packet",
            "relationship_edges": edges,
            "mechanical_consequence_authority": False,
            "director_rule": (
                "Use this backstage truth to let present NPCs initiate, react, interrupt, joke, disagree, lie, omit, leave, or speak to one another coherently even before a formal conversation session. "
                "Do not expose hidden motives, numeric private state, or undisclosed knowledge as Wei's knowledge; render only perception, lawful inference, or what an NPC actually discloses."
            ),
        }

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
        active_session=_project_active_session_presence(
            self.repository.read_json,self.sheet_resolver,session_projection(self.repository.read_json),
            player_id=player_id,player_sheet=player_sheet,player_location=str(player_presence.get('location_ref') or ''),
            live_combat=live_combat,
        )
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
        director_context=self._gm_private_scene_director_context(meta,scene,scene_view,player_sheet)
        if director_context:
            scene_view['gm_private_director_context']=director_context
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
        command_surface={
            'supported_command_types': supported,
            'limits': {
                'one_semantic_command_per_write': True,
                'preview_before_execute': True,
                'unsupported_mechanical_consequence_fails_closed': True,
                'ordinary_reversible_scene_actions_need_no_command': True,
                'command_catalog_role':'mechanical_consequence_registry_not_action_whitelist',
            },
        }
        active_thread_rows=_hot_active_scene_thread_rows(attempt_state,active_session)
        active_thread_count=len(active_thread_rows)
        active_threads=[
            {k:row.get(k) for k in ('attempt_ref','at','action','target_ref','player_statement','topic','scopes','scene_session_ref') if row.get(k) not in (None,'',[])}
            for row in active_thread_rows[:16]
        ]
        result={
          'campaign':{'campaign_id':meta.get('campaign_id'),'revision':meta.get('revision'),'world_time':meta.get('time'),'state_root':before[1],'player_id':meta.get('player_id'),'game':'jianghu'},
          'scene':scene_view,
          'player':player,
          'person_reads':{'suggested_owner_ids':self._validated_scene_person_ids(meta,scene_view),'roster_query_available':True,'use':'Use list_people for bounded pageable roster discovery, then load exact person sheets when capability matters.'},
          'object_reads':{'supported_ref_prefixes':['faction:','inventory:','contract:','mission:','deployment:','project:','tournament:','market:','site:','scene_history_','scene_history_head','scene_open_threads','relations','government'],'use':'Inspect one exact Jianghu owner when its current player-permitted state matters. Prefix support is routing syntax, not permission to read unrelated mutable world truth; sensitive owners are knowledge/authority projected.'},
          'contract_reads':{'available_contracts':visible_contracts,'use':'Inspect an advertised contract object_ref for exact current terms before accepting it.'},
          'mission_reads':{'active_missions':mission_rows,'use':'Inspect a mission object_ref for the current House briefing, council/authorization, participants, physical-operation linkage and compact report state.'},
          'world_events':{'active':active_events,'rule':'Calendar events are real systemic conditions or interactable gatherings. Exact NPC attendees are derived only when locally observed; aggregate crowds remain aggregate.'},
          'commands':command_surface,
          'active_scene_session':active_session,
          'recent_scene_history':recent_speech,
          'active_threads':active_threads,
          'active_thread_count':active_thread_count,
          'active_threads_truncated':active_thread_count > len(active_threads),
          'narration':{
              'setting':'Chinese Jianghu/Murim',
              'rule':'Use GM-private scene truth to understand what is actually happening, but present hard facts to the player only when committed and observable/known to Wei. Physical mechanics determine consequential outcomes. Ordinary participant dialogue and reversible scene life may be authored by the AI inside the scene boundary; binding consequences still require their mechanical authority.',
              'runtime_records_are_source_material_not_dialogue_scripts':True,
              'ai_authors_human_performance':True,
              'avoid_field_by_field_paraphrase':True,
              'gm_may_receive_hidden_scene_truth':True,
              'hidden_truth_is_not_player_knowledge':True,
              'player_output_boundary':'Only Wei perception, lawful knowledge, disclosed speech, reasonable inference, and mechanically established observable consequences may be narrated as known fact.',
          },
          'context_policy':{
              'bounded_reads':True,'pageable_rosters':True,'derived_person_lookup':True,'aggregate_civilians':True,
              'gm_private_scene_bounded_omniscience':True,
              'omniscience_scope_rule':'Provide hidden truth when materially relevant to the current scene/combat/cognition; do not dump unrelated world state every turn.',
              'player_output_remains_epistemically_bounded':True,
          },
          'causal_freshness':{'settled_through':self.repository.read_json('state/martial-world/scheduler.json').get('settled_through')},
        }
        result['active_questions']=[row for row in result.get('active_threads',[]) if row.get('action')=='ask']
        if result['active_threads_truncated']:
            result['read_hints']={
                'scene_open_threads':{
                    'tool':'inspect_game_object','object_ref':'scene_open_threads',
                    'rule':'The hot scene window omits older unresolved player-authored threads. Inspect this read-only object and follow next_object_ref only when older live conversation matters.',
                }
            }
        try: validate_bounded_json(result,label='play context',allow_float=True)
        except ValueError as exc: raise OperationError(503,'play_context_out_of_bounds') from exc
        return result

    def command_family(self,family:str)->Mapping[str,Any]:
        if not isinstance(family,str) or not family or len(family)>64:
            raise OperationError(422,'command_family_invalid')
        surface={
            'supported_command_types': sorted(getattr(self.command_planner,'COMMAND_TYPES',())),
            'limits': {
                'one_semantic_command_per_write': True,
                'preview_before_execute': True,
                'unsupported_mechanical_consequence_fails_closed': True,
            },
        }
        try:
            return compact_command_family(surface,family)
        except KeyError as exc:
            raise OperationError(404,'command_family_not_found') from exc

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
        scene_visible = person_id in self._validated_scene_person_ids(meta, scene)
        if person_id!=player_id and (session_visible or scene_visible):
            scene_focus = {
                'kind': session.get('kind') if isinstance(session,Mapping) and session_visible else 'established_scene',
                'process_ref': session.get('process_ref') if isinstance(session,Mapping) and session_visible else scene.get('process_ref'),
                'purpose': session.get('purpose') if isinstance(session,Mapping) and session_visible else scene.get('summary'),
                'agenda':[str(x) for x in session.get('agenda',[]) if isinstance(x,str)][:12] if isinstance(session,Mapping) and session_visible else [],
            }
            envelope={
                'speaker_ref':person_id,
                'role':safe.get('standing_offices') or safe.get('membership_grade'),
                'social_titles':list(social_titles),
                'scene_focus':scene_focus,
                'performance_cues_rule':'use established role, social titles, lawful relationship state, observed history, and scene focus as qualitative GM-private guidance; never quote relationship scores or invent private motives/hidden personality facts',
                'may_is_non_exhaustive':True,
                'reversible_dialogue_is_open_ended':True,
                'dialogue_authoring_rule':'the runtime supplies facts, constraints, relationship/role context and any explicitly marked private decision support; the AI GM authors the actual human line and must not recite runtime fields as a script',
                'subjective_characterization_latitude':True,
                'subjective_characterization_rule':'the AI may choose momentary nonbinding emotion, tone, hesitation, humor, warmth, irritation, conversational tactics and ordinary opinion consistent with established role, relationship, history and pressure; these choices do not create hidden factual motives, durable relationship changes or hard outcomes',
                'may':['acknowledge','clarify_player_safe_facts','answer_from_known_facts','disclose_existing_private_fact_when_the_speaker_lawfully_knows_and_chooses_to_reveal_it','lie_or_withhold_when_supported_by_private_cognition','react','ask_followup','offer_nonbinding_advice','object','disagree','correct','coordinate','teach_or_explain','bargain_nonbinding','express_supported_emotion','joke_or_tease_if_supported','defer_or_decline_to_answer','speculate_from_known_evidence'],
                'must_preserve_uncertainty':True,
                'factual_basis':'player_safe_runtime_context_plus_explicit_gm_private_cognition_for_decision_generation',
                'cannot_establish':['invented_secret_fact','formal_authority','resource_transfer','movement','relationship_change','contract_or_oath','mechanical_acceptance_or_refusal'],
                'disclosure_rule':'An NPC may disclose an already-existing private fact contained in lawful speaker cognition. The resulting speech establishes that Wei heard the attributed statement; it does not by itself verify objective truth or create a previously nonexistent secret.',
                'private_motives_may_be_gm_private_but_are_not_player_knowledge':True,
                'mechanical_consequence_authority':False,
            }
            private_cognition=_gm_private_person_cognition(sheet, relationship_to_player)
            if private_cognition: envelope['gm_private_cognition']=private_cognition
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
                if rows:
                    bounded,total,truncated=_bounded_social_commitment_rows(rows,'obligation_ref')
                    commitments['obligations']=bounded; commitments['obligations_count']=total; commitments['obligations_truncated']=truncated
            vows=social.get('vows',{}) if isinstance(social,Mapping) else {}
            if isinstance(vows,Mapping):
                rows=[]
                for ref,row in vows.items():
                    if not isinstance(ref,str) or not isinstance(row,Mapping) or str(row.get('person_ref') or '')!=str(player_id):continue
                    if person_id==player_id or str(row.get('subject_ref') or '')==person_id:
                        rows.append({'vow_ref':ref,**dict(row)})
                if rows:
                    bounded,total,truncated=_bounded_social_commitment_rows(rows,'vow_ref')
                    commitments['vows']=bounded; commitments['vows_count']=total; commitments['vows_truncated']=truncated
            beliefs=social.get('beliefs',{}) if isinstance(social,Mapping) else {}
            if isinstance(beliefs,Mapping):
                rows=[]
                for ref,row in beliefs.items():
                    if not isinstance(ref,str) or not isinstance(row,Mapping) or str(row.get('observer_ref') or '')!=str(player_id):continue
                    if person_id==player_id or str(row.get('subject_ref') or '')==person_id:
                        rows.append({'belief_ref':ref,**dict(row)})
                if rows:
                    bounded,total,truncated=_bounded_social_commitment_rows(rows,'belief_ref')
                    commitments['beliefs']=bounded; commitments['beliefs_count']=total; commitments['beliefs_truncated']=truncated
            familiarity_rows=social.get('martial_familiarity',{}) if isinstance(social,Mapping) else {}
            if isinstance(familiarity_rows,Mapping):
                rows=[]
                for ref,row in familiarity_rows.items():
                    if not isinstance(ref,str) or not isinstance(row,Mapping) or str(row.get('observer_ref') or '')!=str(player_id):continue
                    if person_id==player_id or str(row.get('opponent_ref') or '')==person_id:
                        rows.append({'martial_ref':ref,**dict(row)})
                if rows:
                    bounded,total,truncated=_bounded_social_commitment_rows(rows,'martial_ref')
                    commitments['martial_familiarity']=bounded; commitments['martial_familiarity_count']=total; commitments['martial_familiarity_truncated']=truncated
            if commitments:result['social_commitments']=commitments
        except (FileNotFoundError,KeyError,TypeError,ValueError):
            pass
        return result

    def _player_safe_inspection_object(
        self, *, object_ref: str, obj: Mapping[str, Any], view: str,
        meta: Mapping[str, Any], player: Mapping[str, Any],
    ) -> tuple[dict[str, Any], str]:
        """Project one inspectable owner through Wei's actual knowledge/authority.

        ``object_reads.supported_ref_prefixes`` advertises routing syntax, not a
        universal permission to dump every matching mutable owner.  Exact world
        owners often contain treasury, deployment, relationship, government,
        training, or logistics truth that the AI GM may need mechanically but
        Wei has not lawfully learned.  Keep useful player-owned/current-public
        reads while failing closed on unrelated mutable state.
        """
        row = dict(obj)
        player_id = str(meta.get("player_id") or "")
        player_faction = str(player.get("faction_ref") or "")

        if view == "faction_summary":
            faction_ref = str(row.get("faction_id") or object_ref.split(":", 1)[-1])
            if faction_ref == player_faction:
                return row, "player_faction_state"
            identity = faction_presentation_identity(faction_ref, row)
            if not identity:
                raise OperationError(404, "object_not_found")
            public = {"faction_id": faction_ref, **dict(identity)}
            # These are durable public identity/location anchors, not mutable
            # military/economic capability.  Never expose treasury, training,
            # enterprises, holdings, infrastructure, or recruitment state here.
            for key in ("headquarters", "local_site_ref"):
                value = row.get(key)
                if isinstance(value, str) and value:
                    public[key] = value
            return public, "public_faction_identity"

        if view == "inventory_summary":
            faction_ref = object_ref.split(":", 1)[-1]
            if faction_ref != player_faction:
                raise OperationError(404, "object_not_found")
            return row, "player_faction_inventory"

        if view == "deployment_summary":
            participants = {
                str(x) for key in ("participant_refs", "escort_refs", "protected_person_refs")
                for x in (row.get(key, []) if isinstance(row.get(key), list) else [])
                if isinstance(x, str)
            }
            owns = str(row.get("faction_ref") or "") == player_faction
            leads = str(row.get("leader_ref") or row.get("commander_ref") or "") == player_id
            if not (owns or leads or player_id in participants):
                raise OperationError(404, "object_not_found")
            return row, "player_relevant_deployment"

        if view == "project_summary":
            workers = {
                str(x) for key in ("worker_refs", "skilled_worker_refs", "general_worker_refs", "management_worker_refs")
                for x in (row.get(key, []) if isinstance(row.get(key), list) else [])
                if isinstance(x, str)
            }
            if str(row.get("faction_ref") or "") != player_faction and player_id not in workers:
                raise OperationError(404, "object_not_found")
            return row, "player_relevant_project"

        if view == "market_summary":
            region_id = str(row.get("region_id") or object_ref.split(":", 1)[-1])
            presence = effective_person_presence(self.repository.read_json, player_id, person=player)
            location_ref = str(presence.get("location_ref") or "")
            place_ref = location_ref
            if location_ref.startswith("site."):
                sites_doc = self.repository.read_json("game/data/martial-world/local-sites.json")
                sites = sites_doc.get("sites", {}) if isinstance(sites_doc, Mapping) else {}
                site = sites.get(location_ref) if isinstance(sites, Mapping) else None
                place_ref = str(site.get("parent_place_ref") or "") if isinstance(site, Mapping) else ""
            try:
                current_region = region_for_place(place_ref) if place_ref else ""
            except KeyError:
                current_region = ""
            if not current_region or region_id != current_region:
                raise OperationError(404, "object_not_found")
            # Regional stock is the finite player-relevant availability surface
            # used by ordinary buying/equipment decisions.  ``cash_pool`` is a
            # conserved internal aggregate-liquidity owner used by autonomous
            # economic settlement; physical presence in a market does not make
            # that exact bookkeeping balance observable to Wei.
            return {
                "schema": row.get("schema"),
                "region_id": region_id,
                "stock": dict(row.get("stock", {})) if isinstance(row.get("stock"), Mapping) else {},
            }, "current_region_market_stock"

        if view == "relations_summary":
            edges = row.get("edges", []) if isinstance(row.get("edges"), list) else []
            visible = [
                dict(edge) for edge in edges if isinstance(edge, Mapping)
                and player_faction in {str(edge.get("from_faction") or ""), str(edge.get("to_faction") or "")}
            ]
            return {"schema": row.get("schema"), "edges": visible}, "player_faction_relations"

        if view == "government_summary":
            attention = row.get("attention", {}) if isinstance(row.get("attention"), Mapping) else {}
            warrants = row.get("warrants", {}) if isinstance(row.get("warrants"), Mapping) else {}
            safe_attention = {player_id: dict(attention[player_id])} if player_id in attention and isinstance(attention[player_id], Mapping) else {}
            safe_warrants: dict[str, Any] = {}
            for warrant_ref, warrant in warrants.items():
                if not isinstance(warrant_ref, str) or not isinstance(warrant, Mapping):
                    continue
                subject_refs = {
                    str(warrant.get(key) or "")
                    for key in ("person_ref", "subject_ref", "target_ref", "target_person_ref", "faction_ref", "target_faction_ref")
                }
                if player_id in subject_refs or player_faction in subject_refs:
                    safe_warrants[warrant_ref] = dict(warrant)
            return {"schema": row.get("schema"), "attention": safe_attention, "warrants": safe_warrants}, "player_relevant_government"

        # Sites are static world reference; tournament state is public once an
        # exact tournament ref is known; scene history is already observed-only.
        return row, view

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
                if not isinstance(obj, Mapping) or not isinstance(player, Mapping):
                    raise OperationError(404, 'object_not_found')
                obj,view=self._player_safe_inspection_object(object_ref=object_ref,obj=obj,view=view,meta=meta,player=player)
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
        if ref=='scene_open_threads' or ref.startswith('scene_open_threads:'):
            cursor=ref.split(':',1)[1] if ':' in ref else None
            try: page=active_scene_thread_page(self.repository.read_json,cursor=cursor)
            except ValueError as exc: raise OperationError(404,'scene_open_threads_unavailable') from exc
            if isinstance(page.get('next_cursor'),str): page['next_object_ref']=f"scene_open_threads:{page['next_cursor']}"
            return page,'active_scene_threads'
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
