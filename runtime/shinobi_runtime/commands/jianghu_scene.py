"""Player-safe reversible scene/session command reducers."""
from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.martial_world.physical_presence import effective_person_presence, same_effective_location
from shinobi_runtime.martial_world.scene_sessions import (
    ATTEMPT_LEDGER_PATH, CLOSE_REASONS, INTERACTION_ACTIONS, SESSION_PATH, SESSION_KINDS, SPEECH_KINDS,
    abandon_session_questions, active_scene_session, append_attributed_speech, bounded_text,
    close_session_record, interaction_ledger, new_session_record, resolve_question, safe_ref, trim_interaction_ledger,
)
from shinobi_runtime.sim.events import CampaignTime


def _digest(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]


def _safe_list(values: Any, code: str, *, maximum_transport: int | None = None) -> list[str]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        raise CommandRejectedError(code)
    if maximum_transport is not None and len(values) > maximum_transport:
        raise CommandRejectedError(code)
    try: rows = [safe_ref(x, code) for x in values]
    except ValueError as exc: raise CommandRejectedError(code) from exc
    if len(set(rows)) != len(rows): raise CommandRejectedError(code)
    return rows


class JianghuSceneCommandsMixin:
    def _established_scene_person_refs(self) -> set[str]:
        """Return identities already exposed by the current presentation/session.

        Exact physical co-location is checked separately. This set is only an
        anti-probing knowledge boundary so a caller cannot guess hidden IDs and
        use command acceptance as a live-location oracle.
        """
        refs: set[str] = set()
        try:
            scene = self.repository.read_json(self.scene_path)
        except (FileNotFoundError, KeyError, TypeError, ValueError):
            scene = {}
        if isinstance(scene, Mapping):
            for key in ("present_person_ids", "visible_person_ids"):
                values = scene.get(key, [])
                if isinstance(values, Sequence) and not isinstance(values, (str, bytes, bytearray)):
                    refs.update(str(x) for x in values if isinstance(x, str) and x)
        session = active_scene_session(self.repository.read_json)
        if isinstance(session, Mapping):
            refs.update(str(x) for x in session.get("participant_refs", []) if isinstance(x, str) and x)
        return refs

    def _require_established_scene_person(self, ref: str, actor_ref: str) -> None:
        if ref == actor_ref:
            return
        if ref not in self._established_scene_person_refs():
            raise CommandRejectedError("jianghu_scene_person_not_player_visible")

    def _colocated_person(self, actor_ref: str, other_ref: str) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
        _ap, _ar, _ao, actor = self._person(actor_ref)
        _op, _or, _oo, other = self._person(other_ref)
        if not same_effective_location(self.repository.read_json, actor_ref, other_ref, left_person=actor, right_person=other):
            raise CommandRejectedError("jianghu_scene_person_not_colocated")
        return actor, other

    def _jianghu_interaction_resolution(self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime):
        action = str(command.payload.get("action") or "")
        target_ref = str(command.payload.get("target_ref") or "")
        if action not in INTERACTION_ACTIONS or not target_ref:
            raise CommandRejectedError("jianghu_interaction_invalid")
        self._require_established_scene_person(target_ref, command.actor_id)
        self._colocated_person(command.actor_id, target_ref)
        process_ref = command.payload.get("process_ref")
        topic = command.payload.get("topic")
        statement = command.payload.get("player_statement")
        posture = command.payload.get("posture")
        try:
            if process_ref is not None: process_ref = safe_ref(process_ref, "jianghu_interaction_process_invalid")
            topic = bounded_text(topic, "jianghu_interaction_topic_invalid", 240, optional=True)
            statement = bounded_text(statement, "jianghu_interaction_statement_invalid", 2000, optional=True)
            posture = bounded_text(posture, "jianghu_interaction_posture_invalid", 500, optional=True)
        except ValueError as exc: raise CommandRejectedError(str(exc)) from exc
        scopes = _safe_list(command.payload.get("scopes", []), "jianghu_interaction_scopes_invalid", maximum_transport=32)
        ledger = interaction_ledger(self.repository.read_json)
        attempt_ref = f"interaction_attempt_{command.digest[:24]}"
        if any(isinstance(x, Mapping) and x.get("attempt_ref") == attempt_ref for x in ledger.get("attempts", [])):
            return self._simple_plan(command, meta, current_time, writes_records={}, code="jianghu_interaction_duplicate", result={"command_type":command.command_type,"attempt_ref":attempt_ref})
        session = active_scene_session(self.repository.read_json)
        session_ref = None
        if isinstance(session, Mapping) and target_ref in set(str(x) for x in session.get("participant_refs", []) if isinstance(x, str)):
            session_ref = str(session.get("session_ref"))
        is_question = action == "ask" and bool(statement)
        row = {
            "attempt_ref": attempt_ref, "at": str(current_time), "surface_digest": command.digest,
            "actor_ref": command.actor_id, "target_ref": target_ref, "action": action,
            "process_ref": process_ref, "player_statement": statement, "posture": posture,
            "topic": topic, "scopes": scopes, "world_response_status": "not_established_by_attempt",
            "scene_session_ref": session_ref,
            "thread_status": "open" if is_question and session_ref else "not_applicable",
            "resolved_at": None, "response_ref": None,
        }
        ledger["attempts"] = [*ledger.get("attempts", []), row]; ledger["total_recorded"] = int(ledger.get("total_recorded",0))+1
        ledger = trim_interaction_ledger(ledger)
        writes = {ATTEMPT_LEDGER_PATH: ledger}
        if is_question and session_ref and isinstance(session, Mapping):
            scene_after = copy.deepcopy(dict(session)); refs=[str(x) for x in scene_after.get("open_question_refs",[]) if isinstance(x,str)]
            if attempt_ref not in refs: refs.append(attempt_ref)
            scene_after["open_question_refs"] = refs; scene_after["last_updated_at"] = str(current_time); writes[SESSION_PATH] = scene_after
        return self._simple_plan(command, meta, current_time, writes_records=writes, code="jianghu_interaction_recorded", result={"command_type":command.command_type,"attempt_ref":attempt_ref,"scene_session_ref":session_ref,"world_response_status":"not_established_by_attempt"})

    def _jianghu_scene_session_resolution(self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime):
        action = str(command.payload.get("action") or "")
        current = active_scene_session(self.repository.read_json)
        if action == "open":
            kind = str(command.payload.get("kind") or "")
            if kind not in SESSION_KINDS: raise CommandRejectedError("jianghu_scene_kind_invalid")
            participants = _safe_list(command.payload.get("participant_refs", []), "jianghu_scene_participants_invalid", maximum_transport=128)
            if command.actor_id not in participants:
                if len(participants) >= 128:
                    raise CommandRejectedError("jianghu_scene_participants_invalid")
                participants.insert(0, command.actor_id)
            _ap, _ar, _ao, actor = self._person(command.actor_id)
            actor_presence = effective_person_presence(self.repository.read_json, command.actor_id, person=actor)
            location_ref = str(actor_presence.get("location_ref") or "")
            if not location_ref: raise CommandRejectedError("jianghu_scene_location_unresolved")
            for ref in participants:
                if ref == command.actor_id: continue
                self._require_established_scene_person(ref, command.actor_id)
                self._colocated_person(command.actor_id, ref)
            agenda_raw = command.payload.get("agenda", [])
            if not isinstance(agenda_raw, Sequence) or isinstance(agenda_raw,(str,bytes,bytearray)) or len(agenda_raw)>32: raise CommandRejectedError("jianghu_scene_agenda_invalid")
            try:
                agenda=[bounded_text(x,"jianghu_scene_agenda_invalid",500) for x in agenda_raw]
                purpose=bounded_text(command.payload.get("purpose"),"jianghu_scene_purpose_invalid",1000,optional=True)
                process_ref=command.payload.get("process_ref")
                if process_ref is not None: process_ref=safe_ref(process_ref,"jianghu_scene_process_invalid")
            except ValueError as exc: raise CommandRejectedError(str(exc)) from exc
            session_ref=f"scene_session_{command.digest[:24]}"
            session=new_session_record(session_ref=session_ref,kind=kind,location_ref=location_ref,participant_refs=participants,at=str(current_time),process_ref=process_ref,purpose=purpose,agenda=agenda)
            writes={SESSION_PATH:session}
            if isinstance(current,Mapping):
                ledger=interaction_ledger(self.repository.read_json); ledger,_=abandon_session_questions(ledger,session_ref=str(current.get("session_ref")),at=str(current_time)); writes[ATTEMPT_LEDGER_PATH]=ledger
            return self._simple_plan(command,meta,current_time,writes_records=writes,code="jianghu_scene_opened",result={"command_type":command.command_type,"session_ref":session_ref,"mechanical_consequence_authority":False})

        if not isinstance(current, Mapping): raise CommandRejectedError("jianghu_scene_not_active")
        session_ref=str(command.payload.get("session_ref") or "")
        if session_ref != str(current.get("session_ref") or ""): raise CommandRejectedError("jianghu_scene_ref_not_active")
        if action == "close":
            reason=str(command.payload.get("close_reason") or "")
            if reason not in CLOSE_REASONS: raise CommandRejectedError("jianghu_scene_close_reason_invalid")
            ledger=interaction_ledger(self.repository.read_json); ledger,abandoned=abandon_session_questions(ledger,session_ref=session_ref,at=str(current_time))
            closed=close_session_record(current,at=str(current_time),reason=reason)
            return self._simple_plan(command,meta,current_time,writes_records={SESSION_PATH:closed,ATTEMPT_LEDGER_PATH:ledger},code="jianghu_scene_closed",result={"command_type":command.command_type,"session_ref":session_ref,"abandoned_question_count":abandoned})
        if action != "record_speech": raise CommandRejectedError("jianghu_scene_action_invalid")
        speaker_ref=str(command.payload.get("speaker_ref") or "")
        if speaker_ref not in set(str(x) for x in current.get("participant_refs",[]) if isinstance(x,str)): raise CommandRejectedError("jianghu_scene_speaker_not_present")
        self._colocated_person(command.actor_id,speaker_ref)
        speech_kind=str(command.payload.get("speech_kind") or "")
        if speech_kind not in SPEECH_KINDS: raise CommandRejectedError("jianghu_scene_speech_kind_invalid")
        try: statement=bounded_text(command.payload.get("statement"),"jianghu_scene_statement_invalid",2500)
        except ValueError as exc: raise CommandRejectedError(str(exc)) from exc
        basis_refs=_safe_list(command.payload.get("basis_refs",[]),"jianghu_scene_basis_invalid",maximum_transport=32)
        allowed_basis=set(str(x) for x in current.get("participant_refs",[]) if isinstance(x,str))|set(str(x) for x in current.get("open_question_refs",[]) if isinstance(x,str))
        if isinstance(current.get("process_ref"),str): allowed_basis.add(str(current.get("process_ref")))
        if any(ref not in allowed_basis for ref in basis_refs): raise CommandRejectedError("jianghu_scene_basis_not_session_visible")
        question_ref=command.payload.get("resolves_question_ref")
        if question_ref is not None:
            try: question_ref=safe_ref(question_ref,"jianghu_scene_question_invalid")
            except ValueError as exc: raise CommandRejectedError(str(exc)) from exc
            if question_ref not in set(str(x) for x in current.get("open_question_refs",[]) if isinstance(x,str)):
                raise CommandRejectedError("jianghu_scene_question_not_open")
        speech_ref=f"scene_speech_{_digest(command.digest,speaker_ref,str(current_time),str(statement))}"
        speech={"speech_ref":speech_ref,"at":str(current_time),"session_ref":session_ref,"speaker_ref":speaker_ref,"speech_kind":speech_kind,"statement":statement,"basis_refs":basis_refs,"resolves_question_ref":question_ref,"truth_status":"attributed_statement","authority":False,"mechanical_consequence_authority":False}
        writes=dict(append_attributed_speech(self.repository.read_json,row=speech))
        session_after=copy.deepcopy(dict(current))
        ledger=interaction_ledger(self.repository.read_json)
        if question_ref is not None:
            q=str(question_ref); session_after["open_question_refs"]=[x for x in session_after.get("open_question_refs",[]) if x!=q]
            ledger,_=resolve_question(ledger,question_ref=q,response_ref=speech_ref,at=str(current_time)); writes[ATTEMPT_LEDGER_PATH]=ledger
        session_after["last_updated_at"]=str(current_time); writes[SESSION_PATH]=session_after
        return self._simple_plan(command,meta,current_time,writes_records=writes,code="jianghu_scene_speech_recorded",result={"command_type":command.command_type,"session_ref":session_ref,"speech_ref":speech_ref,"truth_status":"attributed_statement","mechanical_consequence_authority":False})
