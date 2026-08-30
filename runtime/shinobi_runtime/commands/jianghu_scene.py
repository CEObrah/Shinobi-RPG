"""Player-safe reversible scene/session command reducers."""
from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.martial_world.physical_presence import active_combat_for_person, effective_person_presence, same_effective_location
from shinobi_runtime.martial_world.scene_sessions import (
    ATTEMPT_LEDGER_PATH, CLOSE_REASONS, INTERACTION_ACTIONS, RESPONSE_BEARING_ACTIONS, SCENE_FACT_KINDS, SESSION_PATH, SESSION_KINDS, SPEECH_KINDS, _expects_response,
    abandon_session_threads, active_scene_session, append_attributed_speech, append_scene_history_record, bounded_text,
    close_session_record, interaction_ledger, new_session_record, normalize_improvised_prop, resolve_thread, safe_ref,
    scene_history_record, trim_interaction_ledger,
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


def _combat_thread_open(row: Mapping[str, Any], combat_ref: str, actor_ref: str) -> bool:
    """Return whether one player conversational move is still live against this combat side.

    The ``not_applicable`` status is accepted only for the short-lived legacy
    shape written before combat-side response-bearing moves became first-class
    threads. New response-bearing moves are persisted as ``open``.
    """
    action = str(row.get("action") or "")
    expects_response = _expects_response(action, row.get("expects_response"))
    return (
        row.get("actor_ref") == actor_ref
        and row.get("target_ref") == combat_ref
        and row.get("target_kind") == "opposing_combat_side"
        and expects_response
        and isinstance(row.get("player_statement"), str)
        and bool(row.get("player_statement"))
        and row.get("resolved_at") is None
        and row.get("response_ref") is None
        and row.get("thread_status") in {"open", "not_applicable"}
    )


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

        # During exact combat the active combat ref is a player-safe way to
        # address the opposing side without exposing or requiring a hidden enemy
        # person ID. This records only the player's reversible speech attempt.
        # It does not pause combat, reveal the opposing roster, establish that a
        # particular enemy heard or answered, or create surrender/truce terms.
        active_combat = active_combat_for_person(self.repository.read_json, command.actor_id)
        combat_side_target = active_combat is not None and target_ref == str(active_combat[0])
        if combat_side_target:
            combat = active_combat[1]
            sides = combat.get("sides", {}) if isinstance(combat, Mapping) else {}
            actor_side = None
            if isinstance(sides, Mapping):
                for side_ref, members in sides.items():
                    if isinstance(members, list) and command.actor_id in members:
                        actor_side = str(side_ref)
                        break
            opposing_members = [
                ref for side_ref, members in sides.items()
                if isinstance(sides, Mapping) and str(side_ref) != actor_side and isinstance(members, list)
                for ref in members if isinstance(ref, str)
            ] if isinstance(sides, Mapping) and actor_side is not None else []
            if actor_side is None or not opposing_members:
                raise CommandRejectedError("jianghu_interaction_combat_target_invalid")
            target_kind = "opposing_combat_side"
        else:
            self._require_established_scene_person(target_ref, command.actor_id)
            actor_person, _target_person = self._colocated_person(command.actor_id, target_ref)
            actor_presence = effective_person_presence(
                self.repository.read_json, command.actor_id, person=actor_person,
            )
            conversation_location = str(actor_presence.get("location_ref") or "")
            if not conversation_location:
                raise CommandRejectedError("jianghu_scene_person_location_invalid")
            target_kind = "person"

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
        expects_response_raw = command.payload.get("expects_response")
        if expects_response_raw is not None and not isinstance(expects_response_raw, bool):
            raise CommandRejectedError("jianghu_interaction_expects_response_invalid")
        ledger = interaction_ledger(self.repository.read_json)
        attempt_ref = f"interaction_attempt_{command.digest[:24]}"
        if any(isinstance(x, Mapping) and x.get("attempt_ref") == attempt_ref for x in ledger.get("attempts", [])):
            return self._simple_plan(command, meta, current_time, writes_records={}, code="jianghu_interaction_duplicate", result={"command_type":command.command_type,"attempt_ref":attempt_ref})
        session = active_scene_session(self.repository.read_json)
        session_ref = None
        is_question = action == "ask" and bool(statement)
        expects_response = _expects_response(action, expects_response_raw)
        auto_opened_session = None
        if target_kind == "person":
            if isinstance(session, Mapping) and target_ref in set(str(x) for x in session.get("participant_refs", []) if isinstance(x, str)):
                session_ref = str(session.get("session_ref"))
            elif (
                isinstance(session, Mapping)
                and bool(statement)
                and expects_response
                and str(session.get("location_ref") or "") == conversation_location
            ):
                # An active conversation is not a permanently frozen cast.
                # The target has already passed exact visibility + co-location
                # validation above, so admitting them here only preserves
                # reversible conversational continuity. It creates no physical
                # presence or access that did not already exist.
                scene_after = copy.deepcopy(dict(session))
                participants = [str(x) for x in scene_after.get("participant_refs", []) if isinstance(x, str)]
                if command.actor_id in participants:
                    participants.append(target_ref)
                    scene_after["participant_refs"] = list(dict.fromkeys(participants))
                    scene_after["last_updated_at"] = str(current_time)
                    auto_opened_session = scene_after
                    session_ref = str(scene_after.get("session_ref") or "")
            elif session is None and bool(statement) and expects_response:
                # A normal human request/question should not require a separate
                # player-facing setup command just to preserve conversational
                # continuity. The session is authority:false and is created in
                # the same transaction as the player's attempt.
                session_ref = f"scene_session_{command.digest[:24]}"
                try:
                    auto_opened_session = new_session_record(
                        session_ref=session_ref,
                        kind="conversation",
                        location_ref=conversation_location,
                        participant_refs=[command.actor_id, target_ref],
                        at=str(current_time),
                        process_ref=process_ref if isinstance(process_ref, str) else None,
                        purpose=topic or "ongoing conversation",
                    )
                except ValueError as exc:
                    raise CommandRejectedError(str(exc)) from exc
        is_thread = bool(statement) and expects_response and bool(session_ref or target_kind == "opposing_combat_side")
        row = {
            "attempt_ref": attempt_ref, "at": str(current_time), "surface_digest": command.digest,
            "actor_ref": command.actor_id, "target_ref": target_ref, "target_kind": target_kind, "action": action,
            "process_ref": process_ref, "player_statement": statement, "posture": posture,
            "topic": topic, "scopes": scopes, "world_response_status": "not_established_by_attempt",
            "world_response_status_scope": "hard_consequence_only",
            "ordinary_scene_response_rule": "co_located_or_authorized_scene_may_respond_reversibly_without_bespoke_mechanic",
            "scene_session_ref": session_ref, "expects_response": expects_response if statement else False,
            "thread_kind": "question" if is_question else ("conversation" if is_thread else None),
            "thread_status": "open" if is_thread else "not_applicable",
            "resolved_at": None, "response_ref": None,
        }
        ledger["attempts"] = [*ledger.get("attempts", []), row]; ledger["total_recorded"] = int(ledger.get("total_recorded",0))+1
        ledger = trim_interaction_ledger(ledger)
        writes = {ATTEMPT_LEDGER_PATH: ledger}
        if isinstance(auto_opened_session, Mapping) and session_ref:
            writes[SESSION_PATH] = copy.deepcopy(dict(auto_opened_session))
        if is_thread and session_ref:
            source_session = auto_opened_session if isinstance(auto_opened_session, Mapping) else session
            if isinstance(source_session, Mapping):
                scene_after = copy.deepcopy(dict(source_session))
                refs=[str(x) for x in scene_after.get("open_thread_refs",scene_after.get("open_question_refs",[])) if isinstance(x,str)]
                if attempt_ref not in refs: refs.append(attempt_ref)
                scene_after["open_thread_refs"] = refs
                if is_question:
                    qrefs=[str(x) for x in scene_after.get("open_question_refs",[]) if isinstance(x,str)]
                    if attempt_ref not in qrefs: qrefs.append(attempt_ref)
                    scene_after["open_question_refs"] = qrefs
                scene_after["last_updated_at"] = str(current_time); writes[SESSION_PATH] = scene_after
        return self._simple_plan(command, meta, current_time, writes_records=writes, code="jianghu_interaction_recorded", result={"command_type":command.command_type,"attempt_ref":attempt_ref,"target_kind":target_kind,"scene_session_ref":session_ref,"world_response_status":"not_established_by_attempt","world_response_status_scope":"hard_consequence_only","ordinary_scene_response_rule":"co_located_or_authorized_scene_may_respond_reversibly_without_bespoke_mechanic"})

    def _record_combat_side_speech(self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime, combat_ref: str):
        """Persist one reversible opposing-side line without exposing an enemy ID."""
        session_ref = str(command.payload.get("session_ref") or "")
        speaker_ref = str(command.payload.get("speaker_ref") or "")
        if session_ref != combat_ref or speaker_ref != combat_ref:
            raise CommandRejectedError("jianghu_combat_parley_speaker_invalid")
        speech_kind = str(command.payload.get("speech_kind") or "")
        if speech_kind not in SPEECH_KINDS:
            raise CommandRejectedError("jianghu_scene_speech_kind_invalid")
        try:
            statement = bounded_text(command.payload.get("statement"), "jianghu_scene_statement_invalid", 2500)
        except ValueError as exc:
            raise CommandRejectedError(str(exc)) from exc
        basis_refs = _safe_list(command.payload.get("basis_refs", []), "jianghu_scene_basis_invalid", maximum_transport=32)
        ledger = interaction_ledger(self.repository.read_json)
        open_thread_refs = {
            str(row.get("attempt_ref"))
            for row in ledger.get("attempts", [])
            if isinstance(row, Mapping) and _combat_thread_open(row, combat_ref, command.actor_id)
            and isinstance(row.get("attempt_ref"), str)
        }
        allowed_basis = {combat_ref, command.actor_id, *open_thread_refs}
        for ref in basis_refs:
            if ref in allowed_basis:
                continue
            if not isinstance(scene_history_record(self.repository.read_json, ref, session_ref=combat_ref), Mapping):
                raise CommandRejectedError("jianghu_scene_basis_not_session_visible")
        thread_ref = command.payload.get("resolves_thread_ref") or command.payload.get("resolves_question_ref")
        if thread_ref is not None:
            try:
                thread_ref = safe_ref(thread_ref, "jianghu_scene_thread_invalid")
            except ValueError as exc:
                raise CommandRejectedError(str(exc)) from exc
            if thread_ref not in open_thread_refs:
                raise CommandRejectedError("jianghu_scene_thread_not_open")
        speech_ref = f"scene_speech_{_digest(command.digest, speaker_ref, str(current_time), str(statement))}"
        speech = {
            "speech_ref": speech_ref, "at": str(current_time), "session_ref": combat_ref,
            "speaker_ref": combat_ref, "speech_kind": speech_kind, "statement": statement,
            "basis_refs": basis_refs, "resolves_thread_ref": thread_ref,
            "resolves_question_ref": thread_ref if any(isinstance(row, Mapping) and row.get("attempt_ref") == thread_ref and row.get("action") == "ask" for row in ledger.get("attempts", [])) else None,
            "truth_status": "attributed_statement", "authority": False,
            "mechanical_consequence_authority": False,
        }
        writes = dict(append_attributed_speech(self.repository.read_json, row=speech))
        if thread_ref is not None:
            resolved = False
            rows = []
            for raw in ledger.get("attempts", []):
                if not isinstance(raw, Mapping):
                    continue
                row = copy.deepcopy(dict(raw))
                if row.get("attempt_ref") == thread_ref and _combat_thread_open(row, combat_ref, command.actor_id):
                    row["thread_status"] = "answered" if row.get("action") == "ask" else "responded"
                    row["resolved_at"] = str(current_time)
                    row["response_ref"] = speech_ref
                    resolved = True
                rows.append(row)
            if not resolved:
                raise CommandRejectedError("jianghu_scene_question_not_open")
            ledger["attempts"] = rows
            writes[ATTEMPT_LEDGER_PATH] = trim_interaction_ledger(ledger)
        return self._simple_plan(
            command, meta, current_time, writes_records=writes,
            code="jianghu_combat_parley_speech_recorded",
            result={
                "command_type": command.command_type, "session_ref": combat_ref,
                "speaker_ref": combat_ref, "speaker_kind": "opposing_combat_side",
                "speech_ref": speech_ref, "resolves_thread_ref": thread_ref,
                "resolves_question_ref": thread_ref if any(isinstance(row, Mapping) and row.get("attempt_ref") == thread_ref and row.get("action") == "ask" for row in ledger.get("attempts", [])) else None,
                "truth_status": "attributed_statement", "mechanical_consequence_authority": False,
            },
        )

    def _jianghu_scene_session_resolution(self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime):
        action = str(command.payload.get("action") or "")
        active_combat = active_combat_for_person(self.repository.read_json, command.actor_id)
        if action == "record_speech" and active_combat is not None:
            combat_ref = str(active_combat[0])
            if (
                str(command.payload.get("session_ref") or "") == combat_ref
                and str(command.payload.get("speaker_ref") or "") == combat_ref
            ):
                return self._record_combat_side_speech(command, meta, current_time, combat_ref)

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
                ledger=interaction_ledger(self.repository.read_json); ledger,_=abandon_session_threads(ledger,session_ref=str(current.get("session_ref")),at=str(current_time)); writes[ATTEMPT_LEDGER_PATH]=ledger
            return self._simple_plan(command,meta,current_time,writes_records=writes,code="jianghu_scene_opened",result={"command_type":command.command_type,"session_ref":session_ref,"mechanical_consequence_authority":False})

        if not isinstance(current, Mapping): raise CommandRejectedError("jianghu_scene_not_active")
        session_ref=str(command.payload.get("session_ref") or "")
        if session_ref != str(current.get("session_ref") or ""): raise CommandRejectedError("jianghu_scene_ref_not_active")
        if action == "close":
            reason=str(command.payload.get("close_reason") or "")
            if reason not in CLOSE_REASONS: raise CommandRejectedError("jianghu_scene_close_reason_invalid")
            ledger=interaction_ledger(self.repository.read_json); ledger,abandoned=abandon_session_threads(ledger,session_ref=session_ref,at=str(current_time))
            closed=close_session_record(current,at=str(current_time),reason=reason)
            return self._simple_plan(command,meta,current_time,writes_records={SESSION_PATH:closed,ATTEMPT_LEDGER_PATH:ledger},code="jianghu_scene_closed",result={"command_type":command.command_type,"session_ref":session_ref,"abandoned_thread_count":abandoned,"abandoned_question_count":abandoned})
        if action == "record_fact":
            if active_combat is not None:
                raise CommandRejectedError("jianghu_scene_fact_requires_combat_authority")
            actor_ref=str(command.payload.get("actor_ref") or "")
            participants=set(str(x) for x in current.get("participant_refs",[]) if isinstance(x,str))
            if actor_ref not in participants:
                raise CommandRejectedError("jianghu_scene_fact_actor_not_present")
            self._colocated_person(command.actor_id,actor_ref)
            fact_kind=str(command.payload.get("fact_kind") or "")
            if fact_kind not in SCENE_FACT_KINDS:
                raise CommandRejectedError("jianghu_scene_fact_kind_invalid")
            try:
                description=bounded_text(command.payload.get("description"),"jianghu_scene_fact_description_invalid",1500)
            except ValueError as exc:
                raise CommandRejectedError(str(exc)) from exc
            fact_participants=_safe_list(command.payload.get("participant_refs",[]),"jianghu_scene_fact_participants_invalid",maximum_transport=32)
            if any(ref not in participants for ref in fact_participants):
                raise CommandRejectedError("jianghu_scene_fact_participant_not_present")
            for ref in fact_participants:
                self._colocated_person(command.actor_id,ref)
            basis_refs=_safe_list(command.payload.get("basis_refs",[]),"jianghu_scene_basis_invalid",maximum_transport=32)
            open_threads=set(str(x) for x in current.get("open_thread_refs",current.get("open_question_refs",[])) if isinstance(x,str))
            allowed_basis=participants|open_threads
            if isinstance(current.get("process_ref"),str): allowed_basis.add(str(current.get("process_ref")))
            history_basis:dict[str,dict[str,Any]]={}
            for ref in basis_refs:
                if ref in allowed_basis:
                    continue
                row=scene_history_record(self.repository.read_json,ref,session_ref=session_ref)
                if not isinstance(row,Mapping):
                    raise CommandRejectedError("jianghu_scene_basis_not_session_visible")
                history_basis[ref]=dict(row)
            try:
                improvised_prop=normalize_improvised_prop(command.payload.get("improvised_prop"))
            except ValueError as exc:
                raise CommandRejectedError(str(exc)) from exc
            source_object_fact_ref=None
            if improvised_prop is not None:
                if fact_kind != "object_state":
                    raise CommandRejectedError("jianghu_scene_improvised_prop_requires_object_state")
                prior_object_facts=[
                    row for row in history_basis.values()
                    if row.get("fact_kind")=="object_state"
                    and row.get("truth_status")=="observed_reversible_scene_fact"
                    and row.get("mechanical_consequence_authority") is False
                ]
                # A first observation may type the already-visible mundane
                # object, but that descriptor alone is not combat authority.
                # If later object-state history is cited, one cited prior object
                # must carry the exact same descriptor so a bowl cannot be
                # silently swapped into a different heavy/sharp object.
                if prior_object_facts:
                    matching_sources=[
                        history_row for history_row in prior_object_facts
                        if isinstance(history_row.get("improvised_prop"),Mapping)
                        and dict(history_row.get("improvised_prop"))==dict(improvised_prop)
                    ]
                    if not matching_sources:
                        raise CommandRejectedError("jianghu_scene_improvised_prop_descriptor_mismatch")
                    source_object_fact_ref=str(matching_sources[0].get("fact_ref") or "") or None
            digest=_digest(command.digest,actor_ref,fact_kind,str(current_time),str(description))
            fact_ref=f"scene_fact_{digest}"
            fact={
                "fact_ref":fact_ref,"at":str(current_time),"session_ref":session_ref,
                "actor_ref":actor_ref,"fact_kind":fact_kind,"summary":description,
                "participant_refs":fact_participants,"basis_refs":basis_refs,
                "truth_status":"observed_reversible_scene_fact","scope":"scene_local_history_only",
                "authority":False,"mechanical_consequence_authority":False,
            }
            if improvised_prop is not None:
                fact["improvised_prop"]=improvised_prop
            if source_object_fact_ref is not None:
                fact["source_object_fact_ref"]=source_object_fact_ref
            writes=dict(append_scene_history_record(self.repository.read_json,row=fact))
            session_after=copy.deepcopy(dict(current)); session_after["last_updated_at"]=str(current_time); writes[SESSION_PATH]=session_after
            return self._simple_plan(
                command,meta,current_time,writes_records=writes,code="jianghu_scene_fact_recorded",
                result={"command_type":command.command_type,"session_ref":session_ref,"fact_ref":fact_ref,"truth_status":"observed_reversible_scene_fact","mechanical_consequence_authority":False},
            )
        if action != "record_speech": raise CommandRejectedError("jianghu_scene_action_invalid")
        speaker_ref=str(command.payload.get("speaker_ref") or "")
        if speaker_ref not in set(str(x) for x in current.get("participant_refs",[]) if isinstance(x,str)): raise CommandRejectedError("jianghu_scene_speaker_not_present")
        self._colocated_person(command.actor_id,speaker_ref)
        speech_kind=str(command.payload.get("speech_kind") or "")
        if speech_kind not in SPEECH_KINDS: raise CommandRejectedError("jianghu_scene_speech_kind_invalid")
        try: statement=bounded_text(command.payload.get("statement"),"jianghu_scene_statement_invalid",2500)
        except ValueError as exc: raise CommandRejectedError(str(exc)) from exc
        basis_refs=_safe_list(command.payload.get("basis_refs",[]),"jianghu_scene_basis_invalid",maximum_transport=32)
        open_threads=set(str(x) for x in current.get("open_thread_refs",current.get("open_question_refs",[])) if isinstance(x,str))
        allowed_basis=set(str(x) for x in current.get("participant_refs",[]) if isinstance(x,str))|open_threads
        if isinstance(current.get("process_ref"),str): allowed_basis.add(str(current.get("process_ref")))
        for ref in basis_refs:
            if ref in allowed_basis:
                continue
            if not isinstance(scene_history_record(self.repository.read_json,ref,session_ref=session_ref),Mapping):
                raise CommandRejectedError("jianghu_scene_basis_not_session_visible")
        thread_ref=command.payload.get("resolves_thread_ref") or command.payload.get("resolves_question_ref")
        if thread_ref is not None:
            try: thread_ref=safe_ref(thread_ref,"jianghu_scene_thread_invalid")
            except ValueError as exc: raise CommandRejectedError(str(exc)) from exc
            if thread_ref not in open_threads:
                raise CommandRejectedError("jianghu_scene_thread_not_open")
        speech_ref=f"scene_speech_{_digest(command.digest,speaker_ref,str(current_time),str(statement))}"
        is_question_thread = any(isinstance(row, Mapping) and row.get("attempt_ref") == thread_ref and row.get("action") == "ask" for row in interaction_ledger(self.repository.read_json).get("attempts", [])) if thread_ref else False
        speech={"speech_ref":speech_ref,"at":str(current_time),"session_ref":session_ref,"speaker_ref":speaker_ref,"speech_kind":speech_kind,"statement":statement,"basis_refs":basis_refs,"resolves_thread_ref":thread_ref,"resolves_question_ref":thread_ref if is_question_thread else None,"truth_status":"attributed_statement","authority":False,"mechanical_consequence_authority":False}
        writes=dict(append_attributed_speech(self.repository.read_json,row=speech))
        session_after=copy.deepcopy(dict(current))
        ledger=interaction_ledger(self.repository.read_json)
        if thread_ref is not None:
            q=str(thread_ref)
            session_after["open_thread_refs"]=[x for x in session_after.get("open_thread_refs",session_after.get("open_question_refs",[])) if x!=q]
            session_after["open_question_refs"]=[x for x in session_after.get("open_question_refs",[]) if x!=q]
            ledger,_=resolve_thread(ledger,thread_ref=q,response_ref=speech_ref,at=str(current_time)); writes[ATTEMPT_LEDGER_PATH]=ledger
        session_after["last_updated_at"]=str(current_time); writes[SESSION_PATH]=session_after
        return self._simple_plan(command,meta,current_time,writes_records=writes,code="jianghu_scene_speech_recorded",result={"command_type":command.command_type,"session_ref":session_ref,"speech_ref":speech_ref,"truth_status":"attributed_statement","mechanical_consequence_authority":False})
