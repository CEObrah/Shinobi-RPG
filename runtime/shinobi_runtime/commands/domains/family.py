"""Family, household, kinship, succession, and birth command domain."""

from __future__ import annotations

import copy
import hashlib
import re
from datetime import timedelta
from decimal import Decimal
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.core import _BuiltPlan, _OwnerResolutionCache, _STABLE_ID, _campaign_datetime, _exact_payload, _json_bytes, _stable_id
from shinobi_runtime.domain import LocationGraph
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest


from shinobi_runtime.commands.paths import (
    FAMILY_INDEX_PATH as _FAMILY_INDEX_PATH,
    KINSHIP_INDEX_PATH as _KINSHIP_INDEX_PATH,
    POPULATION_REGISTRY_PATH as _POPULATION_REGISTRY_PATH,
    PERSON_CONTINUITY_PATH as _PERSON_CONTINUITY_PATH,
    ROUTES_PATH as _ROUTES_PATH,
)


class FamilyCommandsMixin:
    @staticmethod
    def _family_person_entry(index: Dict[str, Any], person_ref: str) -> Dict[str, list[str]]:
        person_index = index.get("person_index")
        if not isinstance(person_index, dict):
            raise CommandRejectedError("family_index_invalid")
        entry = person_index.setdefault(person_ref, {
            "courtships": [], "proposals": [], "unions": [], "households": [],
            "kinships": [], "parenthoods": [], "parentage": [], "successions": [], "events": [],
        })
        if not isinstance(entry, dict):
            raise CommandRejectedError("family_index_invalid")
        for key in ("courtships", "proposals", "unions", "households", "kinships", "parenthoods", "parentage", "successions", "events"):
            values = entry.setdefault(key, [])
            if not isinstance(values, list):
                raise CommandRejectedError("family_index_invalid")
        return entry


    @staticmethod
    def _kinship_person_entry(index: Dict[str, Any], person_ref: str) -> Dict[str, list[str]]:
        person_links = index.get("person_links")
        if not isinstance(person_links, dict):
            raise CommandRejectedError("kinship_index_invalid")
        entry = person_links.setdefault(person_ref, {
            "spouses": [], "former_spouses": [], "parents": [], "children": [],
            "guardians": [], "wards": [], "households": [], "succession_claims": [], "kinships": [],
        })
        if not isinstance(entry, dict):
            raise CommandRejectedError("kinship_index_invalid")
        for key in ("spouses", "former_spouses", "parents", "children", "guardians", "wards", "households", "succession_claims", "kinships"):
            values = entry.setdefault(key, [])
            if not isinstance(values, list):
                raise CommandRejectedError("kinship_index_invalid")
        return entry


    @staticmethod
    def _append_unique(values: list[str], value: str) -> None:
        if value not in values:
            values.append(value)
            values.sort()


    def _family_indexes(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        try:
            family = copy.deepcopy(self.repository.read_json(_FAMILY_INDEX_PATH))
            kinship = copy.deepcopy(self.repository.read_json(_KINSHIP_INDEX_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("family_index_invalid") from exc
        if family.get("schema") != "family-index" or kinship.get("schema") != "kinship-index":
            raise CommandRejectedError("family_index_invalid")
        return family, kinship


    def _require_person_ref(self, person_ref: str, *, code: str = "family_person_unresolved") -> Mapping[str, Any]:
        try:
            _path, record = self._resolve_covered_owner(person_ref, cache=_OwnerResolutionCache())
        except CommandRejectedError as exc:
            raise CommandRejectedError(code) from exc
        schema = record.get("schema") if isinstance(record, Mapping) else None
        if schema not in ("shinobi_character", "person-core") and not (
            person_ref.startswith("person.") or person_ref.startswith("pc_") or person_ref.startswith("canon_")
        ):
            raise CommandRejectedError(code)
        return record


    def _family_event(
        self,
        *,
        command: CommandEnvelope,
        family_index: Dict[str, Any],
        event_type: str,
        at: CampaignTime,
        subject_refs: Sequence[str],
        source_refs: Sequence[str],
        suffix: str,
    ) -> Tuple[str, str, Dict[str, Any]]:
        event_id = f"family.event.{command.digest[:18]}.{suffix}"
        path = f"state/family/events/{event_id}.json"
        if self.repository.read_optional_bytes(path) is not None:
            raise CommandRejectedError("family_event_conflict")
        record = {
            "schema": "family-event",
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at": str(at),
            "authority": True,
            "subject_refs": sorted(set(subject_refs)),
            "source_refs": sorted(set(source_refs)),
        }
        events = family_index.get("events")
        counts = family_index.get("counts")
        if not isinstance(events, dict) or not isinstance(counts, dict):
            raise CommandRejectedError("family_index_invalid")
        events[event_id] = path
        counts["events"] = len(events)
        for person_ref in subject_refs:
            entry = self._family_person_entry(family_index, person_ref)
            self._append_unique(entry["events"], event_id)
        return event_id, path, record


    def _family_proposal_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _exact_payload(
            command.payload,
            ("action", "proposal_ref", "kind", "target_ref", "response", "summary", "visibility"),
            command.command_type,
        )
        action = command.payload["action"]
        if action not in ("propose", "respond", "withdraw"):
            raise CommandRejectedError("family_proposal_action_invalid")
        summary = command.payload["summary"]
        visibility = command.payload["visibility"]
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 1000:
            raise CommandRejectedError("family_summary_invalid")
        if visibility not in ("public", "restricted", "secret"):
            raise CommandRejectedError("family_visibility_invalid")
        family, kinship = self._family_indexes()
        proposals = family.get("proposals")
        counts = family.get("counts")
        if not isinstance(proposals, dict) or not isinstance(counts, dict):
            raise CommandRejectedError("family_index_invalid")
        writes: Dict[str, bytes] = {}
        family_event_refs: list[str] = []

        if action == "propose":
            if command.payload["proposal_ref"] is not None or command.payload["response"] is not None:
                raise CommandRejectedError("family_proposal_fields_invalid")
            kind = command.payload["kind"]
            target_ref = command.payload["target_ref"]
            if kind not in ("courtship_offer", "marriage_proposal", "betrothal_proposal", "political_match_offer", "household_proposal"):
                raise CommandRejectedError("family_proposal_kind_invalid")
            target_ref = _stable_id(target_ref, "family_target_invalid")
            if target_ref == command.actor_id:
                raise CommandRejectedError("family_target_invalid")
            self._require_person_ref(command.actor_id)
            self._require_person_ref(target_ref)
            proposal_id = f"family.proposal.{command.digest[:24]}"
            proposal_path = f"state/family/proposals/{proposal_id}.json"
            if proposal_id in proposals or self.repository.read_optional_bytes(proposal_path) is not None:
                raise CommandRejectedError("family_proposal_conflict")
            try:
                player_ref = self.repository.read_json("state/player.json").get("owner_id")
            except (FileNotFoundError, ValueError):
                player_ref = None
            proposal = {
                "schema": "family-proposal",
                "proposal_id": proposal_id,
                "kind": kind,
                "proposer_id": command.actor_id,
                "target_id": target_ref,
                "status": "pending",
                "authority": True,
                "proposed_at": str(current_time),
                "player_choice_required": target_ref == player_ref,
            }
            proposals[proposal_id] = proposal_path
            counts["proposals"] = len(proposals)
            for ref in (command.actor_id, target_ref):
                self._append_unique(self._family_person_entry(family, ref)["proposals"], proposal_id)
            event_id, event_path, event_record = self._family_event(
                command=command, family_index=family, event_type="proposal_made", at=current_time,
                subject_refs=(command.actor_id, target_ref), source_refs=(proposal_id,), suffix="proposal",
            )
            family_event_refs.append(event_id)
            writes[proposal_path] = _json_bytes(proposal)
            writes[event_path] = _json_bytes(event_record)
            result_status = "pending"
        else:
            if command.payload["kind"] is not None or command.payload["target_ref"] is not None:
                raise CommandRejectedError("family_proposal_fields_invalid")
            proposal_id = _stable_id(command.payload["proposal_ref"], "family_proposal_ref_invalid", prefix="family.proposal.")
            proposal_path = proposals.get(proposal_id)
            if not isinstance(proposal_path, str):
                raise CommandRejectedError("family_proposal_unresolved")
            try:
                proposal = copy.deepcopy(self.repository.read_json(proposal_path))
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("family_proposal_unresolved") from exc
            if proposal.get("status") != "pending":
                raise CommandRejectedError("family_proposal_not_pending")
            if action == "respond":
                response = command.payload["response"]
                if response not in ("accepted", "declined"):
                    raise CommandRejectedError("family_proposal_response_invalid")
                if proposal.get("target_id") != command.actor_id:
                    raise CommandRejectedError("family_proposal_response_not_authorized")
                proposal["status"] = response
            else:
                if command.payload["response"] is not None:
                    raise CommandRejectedError("family_proposal_fields_invalid")
                if proposal.get("proposer_id") != command.actor_id:
                    raise CommandRejectedError("family_proposal_withdraw_not_authorized")
                proposal["status"] = "withdrawn"
            result_status = proposal["status"]
            event_type = {
                "accepted": "proposal_accepted",
                "declined": "proposal_declined",
                "withdrawn": "proposal_withdrawn",
            }[result_status]
            event_id, event_path, event_record = self._family_event(
                command=command, family_index=family, event_type=event_type, at=current_time,
                subject_refs=tuple(
                    x for x in (proposal.get("proposer_id"), proposal.get("target_id")) if isinstance(x, str)
                ),
                source_refs=(proposal_id,), suffix="proposal-status",
            )
            family_event_refs.append(event_id)
            writes[proposal_path] = _json_bytes(proposal)
            writes[event_path] = _json_bytes(event_record)

        world_events = self._world_events()
        semantic_event_id = self._append_semantic_event(
            world_events, command=command, kind="family_proposal_changed", at=current_time,
            actor_refs=(command.actor_id,), affected_owner_refs=tuple(writes),
            material_consequence_refs=(proposal_id, f"proposal_status:{result_status}"),
            classification=visibility,
            audience_refs=tuple(x for x in (proposal.get("proposer_id"), proposal.get("target_id")) if isinstance(x, str)),
            reducer_ref="shinobi_runtime.commands.family_proposal_resolution",
        )
        writes[_FAMILY_INDEX_PATH] = _json_bytes(family)
        writes[_KINSHIP_INDEX_PATH] = _json_bytes(kinship)
        writes[self.meta_path] = _json_bytes(self._meta_after(meta, command, world_time=current_time))
        writes.update(self._world_event_writes(world_events))
        writes = self._prune_noop_writes(writes)
        expected = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected:
                raise ValueError("family proposal write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
            if overlay.read_json(proposal_path).get("status") != result_status:
                raise ValueError("family proposal status mismatch")

        return _BuiltPlan(
            code="family_proposal_resolution_ready", affected_refs=expected, writes=writes,
            result={"proposal_ref": proposal_id, "status": result_status, "family_event_refs": family_event_refs, "semantic_event_id": semantic_event_id},
            validator=validate,
        )


    def _family_lifecycle_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _exact_payload(
            command.payload,
            (
                "action", "record_ref", "proposal_ref", "participant_refs", "target_status",
                "child_ref", "parent_refs", "guardian_refs", "member_refs", "dependent_refs",
                "property_refs", "institution_refs", "subject_owner_ref", "candidate_order",
                "relation_kind", "recognition_basis", "summary", "visibility",
            ),
            command.command_type,
        )
        action = command.payload["action"]
        allowed_actions = (
            "courtship_start", "courtship_end", "union_form", "union_status",
            "household_form", "household_update", "parenthood_begin", "parenthood_end",
            "adoption", "guardianship", "kinship_record", "succession_set",
        )
        if action not in allowed_actions:
            raise CommandRejectedError("family_lifecycle_action_invalid")
        summary = command.payload["summary"]
        visibility = command.payload["visibility"]
        recognition_basis = command.payload["recognition_basis"]
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 1000:
            raise CommandRejectedError("family_summary_invalid")
        if visibility not in ("public", "restricted", "secret"):
            raise CommandRejectedError("family_visibility_invalid")
        if recognition_basis is not None and (not isinstance(recognition_basis, str) or not recognition_basis.strip() or len(recognition_basis) > 500):
            raise CommandRejectedError("family_recognition_invalid")

        def refs(field: str) -> list[str]:
            raw = command.payload[field]
            if raw is None:
                return []
            if not isinstance(raw, list) or any(not isinstance(x, str) or not _STABLE_ID.fullmatch(x) for x in raw):
                raise CommandRejectedError(f"family_{field}_invalid")
            if len(raw) != len(set(raw)) or len(raw) > 32:
                raise CommandRejectedError(f"family_{field}_invalid")
            return list(raw)

        participants = refs("participant_refs")
        parent_refs = refs("parent_refs")
        guardian_refs = refs("guardian_refs")
        member_refs = refs("member_refs")
        dependent_refs = refs("dependent_refs")
        property_refs = refs("property_refs")
        institution_refs = refs("institution_refs")
        family, kinship = self._family_indexes()
        counts = family.get("counts")
        if not isinstance(counts, dict):
            raise CommandRejectedError("family_index_invalid")
        writes: Dict[str, bytes] = {}
        family_subjects: list[str] = []
        source_refs: list[str] = []
        event_type: str
        result_ref: str

        if action == "courtship_start":
            if command.payload["record_ref"] is not None or len(participants) != 2:
                raise CommandRejectedError("family_courtship_fields_invalid")
            proposal_ref = _stable_id(command.payload["proposal_ref"], "family_proposal_ref_invalid", prefix="family.proposal.")
            proposal_path = family.get("proposals", {}).get(proposal_ref)
            if not isinstance(proposal_path, str):
                raise CommandRejectedError("family_proposal_unresolved")
            proposal = self.repository.read_json(proposal_path)
            if proposal.get("status") != "accepted" or proposal.get("kind") != "courtship_offer" or set(participants) != {proposal.get("proposer_id"), proposal.get("target_id")}:
                raise CommandRejectedError("family_courtship_requires_accepted_offer")
            if command.actor_id not in participants:
                raise CommandRejectedError("family_courtship_not_authorized")
            for ref in participants:
                self._require_person_ref(ref)
            result_ref = f"family.courtship.{command.digest[:24]}"
            path = f"state/family/courtships/{result_ref}.json"
            record = {"schema": "family-courtship", "courtship_id": result_ref, "participants": participants, "status": "active", "authority": True, "started_at": str(current_time), "intent_basis": recognition_basis or "accepted courtship offer"}
            family["courtships"][result_ref] = path
            counts["courtships"] = len(family["courtships"])
            for ref in participants:
                self._append_unique(self._family_person_entry(family, ref)["courtships"], result_ref)
            writes[path] = _json_bytes(record)
            event_type = "courtship_started"; family_subjects = participants; source_refs = [proposal_ref]

        elif action == "courtship_end":
            result_ref = _stable_id(command.payload["record_ref"], "family_courtship_ref_invalid", prefix="family.courtship.")
            path = family.get("courtships", {}).get(result_ref)
            if not isinstance(path, str): raise CommandRejectedError("family_courtship_unresolved")
            record = copy.deepcopy(self.repository.read_json(path)); participants = list(record.get("participants", []))
            if command.actor_id not in participants: raise CommandRejectedError("family_courtship_not_authorized")
            if record.get("status") not in ("active", "paused"): raise CommandRejectedError("family_courtship_not_active")
            record["status"] = "ended"; writes[path] = _json_bytes(record)
            event_type = "courtship_ended"; family_subjects = participants; source_refs = [result_ref]

        elif action == "union_form":
            if command.payload["record_ref"] is not None or len(participants) != 2:
                raise CommandRejectedError("family_union_fields_invalid")
            proposal_ref = _stable_id(command.payload["proposal_ref"], "family_proposal_ref_invalid", prefix="family.proposal.")
            proposal_path = family.get("proposals", {}).get(proposal_ref)
            if not isinstance(proposal_path, str): raise CommandRejectedError("family_proposal_unresolved")
            proposal = self.repository.read_json(proposal_path)
            if proposal.get("status") != "accepted" or proposal.get("kind") not in ("marriage_proposal", "betrothal_proposal", "political_match_offer") or set(participants) != {proposal.get("proposer_id"), proposal.get("target_id")}:
                raise CommandRejectedError("family_union_requires_accepted_proposal")
            if command.actor_id not in participants: raise CommandRejectedError("family_union_not_authorized")
            for ref in participants: self._require_person_ref(ref)
            union_status = "betrothed" if proposal.get("kind") in ("betrothal_proposal", "political_match_offer") else "married"
            result_ref = f"family.union.{command.digest[:24]}"; path = f"state/family/unions/{result_ref}.json"
            existing_unions = [u for ref in participants for u in self._family_person_entry(family, ref)["unions"]]
            record = {"schema":"family-union","union_id":result_ref,"participants":participants,"status":union_status,"authority":True,"formed_at":str(current_time),"date_precision":"exact","recognition":{"recognized":True,"basis":recognition_basis or "accepted union proposal"},"relationship_refs":[]}
            family["unions"][result_ref]=path; counts["unions"]=len(family["unions"])
            for ref in participants:
                self._append_unique(self._family_person_entry(family, ref)["unions"], result_ref)
            if union_status == "married":
                a,b=participants
                self._append_unique(self._kinship_person_entry(kinship,a)["spouses"],b); self._append_unique(self._kinship_person_entry(kinship,b)["spouses"],a)
            writes[path]=_json_bytes(record)
            event_type = "remarriage" if existing_unions and union_status == "married" else ("marriage_formed" if union_status == "married" else "betrothal_formed")
            family_subjects=participants; source_refs=[proposal_ref]

        elif action == "union_status":
            result_ref=_stable_id(command.payload["record_ref"],"family_union_ref_invalid",prefix="family.union.")
            path=family.get("unions",{}).get(result_ref)
            if not isinstance(path,str): raise CommandRejectedError("family_union_unresolved")
            record=copy.deepcopy(self.repository.read_json(path)); participants=list(record.get("participants",[]))
            if command.actor_id not in participants: raise CommandRejectedError("family_union_not_authorized")
            target_status=command.payload["target_status"]
            if target_status not in ("separated","divorced","annulled","widowed","dissolved"): raise CommandRejectedError("family_union_status_invalid")
            if target_status=="widowed":
                dead=False
                for ref in participants:
                    person=self._require_person_ref(ref)
                    if person.get("life_status")=="dead" or person.get("condition",{}).get("readiness")=="dead": dead=True
                if not dead: raise CommandRejectedError("family_widowhood_requires_death")
            record["status"]=target_status; writes[path]=_json_bytes(record)
            if target_status in ("divorced","annulled","widowed","dissolved") and len(participants)==2:
                a,b=participants
                for left,right in ((a,b),(b,a)):
                    entry=self._kinship_person_entry(kinship,left)
                    if right in entry["spouses"]: entry["spouses"].remove(right)
                    self._append_unique(entry["former_spouses"],right)
            event_type={"separated":"separation","divorced":"divorce","annulled":"annulment","widowed":"widowhood","dissolved":"divorce"}[target_status]
            family_subjects=participants; source_refs=[result_ref]

        elif action in ("household_form","household_update"):
            if not member_refs or command.actor_id not in member_refs:
                raise CommandRejectedError("family_household_not_authorized")
            for ref in [*member_refs,*dependent_refs]: self._require_person_ref(ref)
            if set(member_refs) & set(dependent_refs): raise CommandRejectedError("family_household_membership_invalid")
            if action=="household_form":
                if command.payload["record_ref"] is not None: raise CommandRejectedError("family_household_fields_invalid")
                result_ref=f"family.household.{command.digest[:24]}"; path=f"state/family/households/{result_ref}.json"
                record={"schema":"family-household","household_id":result_ref,"authority":True,"status":"active","member_refs":member_refs,"dependent_refs":dependent_refs,"property_refs":property_refs,"institution_refs":institution_refs}
                family["households"][result_ref]=path; counts["households"]=len(family["households"]); event_type="household_formed"
            else:
                result_ref=_stable_id(command.payload["record_ref"],"family_household_ref_invalid",prefix="family.household.")
                path=family.get("households",{}).get(result_ref)
                if not isinstance(path,str): raise CommandRejectedError("family_household_unresolved")
                record=copy.deepcopy(self.repository.read_json(path)); target_status=command.payload["target_status"]
                if target_status not in ("active","split_residence","dissolved"): raise CommandRejectedError("family_household_status_invalid")
                record.update({"status":target_status,"member_refs":member_refs,"dependent_refs":dependent_refs,"property_refs":property_refs,"institution_refs":institution_refs}); event_type="household_changed"
            for ref in [*member_refs,*dependent_refs]:
                self._append_unique(self._family_person_entry(family,ref)["households"],result_ref)
                self._append_unique(self._kinship_person_entry(kinship,ref)["households"],result_ref)
            writes[path]=_json_bytes(record); family_subjects=[*member_refs,*dependent_refs]; source_refs=[result_ref]

        elif action in ("parenthood_begin", "parenthood_end"):
            if action == "parenthood_begin":
                if command.payload["record_ref"] is not None or not 1 <= len(parent_refs) <= 2 or command.actor_id not in parent_refs:
                    raise CommandRejectedError("family_parenthood_not_authorized")
                for ref in parent_refs:
                    self._require_person_ref(ref)
                try:
                    player_ref = self.repository.read_json("state/player.json").get("owner_id")
                except (FileNotFoundError, ValueError):
                    player_ref = None
                if player_ref in parent_refs and command.actor_id != player_ref:
                    raise CommandRejectedError("family_player_parenthood_requires_player_choice")
                try:
                    mechanics = self.repository.read_json("game/data/mechanics/family.json")
                    gestation_days = mechanics["reproduction_and_birth"]["abstract_gestation_days"]
                except (FileNotFoundError, ValueError, KeyError, TypeError) as exc:
                    raise CommandRejectedError("family_mechanics_invalid") from exc
                if isinstance(gestation_days, bool) or not isinstance(gestation_days, int) or gestation_days <= 0:
                    raise CommandRejectedError("family_mechanics_invalid")
                expected_dt = _campaign_datetime(current_time) + timedelta(days=gestation_days)
                expected_at = CampaignTime(expected_dt.year, expected_dt.month, expected_dt.day, expected_dt.hour, expected_dt.minute, expected_dt.second)
                result_ref = f"family.parenthood.{command.digest[:24]}"
                path = f"state/family/parenthoods/{result_ref}.json"
                record = {
                    "schema": "family-parenthood", "parenthood_id": result_ref, "authority": True,
                    "parent_refs": parent_refs, "status": "expecting", "started_at": str(current_time),
                    "expected_at": str(expected_at), "source_basis": recognition_basis or "explicit parenthood choice",
                    "child_ref": None,
                }
                family["parenthoods"][result_ref] = path
                counts["parenthoods"] = len(family["parenthoods"])
                for ref in parent_refs:
                    self._append_unique(self._family_person_entry(family, ref)["parenthoods"], result_ref)
                writes[path] = _json_bytes(record)
                event_type = "parenthood_started"
                family_subjects = parent_refs
                source_refs = [result_ref]
            else:
                result_ref = _stable_id(command.payload["record_ref"], "family_parenthood_ref_invalid", prefix="family.parenthood.")
                path = family.get("parenthoods", {}).get(result_ref)
                if not isinstance(path, str):
                    raise CommandRejectedError("family_parenthood_unresolved")
                record = copy.deepcopy(self.repository.read_json(path))
                parents = record.get("parent_refs")
                if not isinstance(parents, list) or command.actor_id not in parents:
                    raise CommandRejectedError("family_parenthood_not_authorized")
                if record.get("status") != "expecting":
                    raise CommandRejectedError("family_parenthood_not_expecting")
                record["status"] = "ended"
                writes[path] = _json_bytes(record)
                event_type = "parenthood_ended"
                family_subjects = list(parents)
                source_refs = [result_ref]

        elif action in ("adoption","guardianship"):
            child_ref=_stable_id(command.payload["child_ref"],"family_child_invalid")
            self._require_person_ref(child_ref)
            result_ref = command.payload["record_ref"]
            if result_ref is None:
                result_ref=f"family.parentage.{hashlib.sha256(child_ref.encode()).hexdigest()[:20]}"; path=f"state/family/parentage/{result_ref}.json"
                record={"schema":"family-parentage","parentage_id":result_ref,"child_id":child_ref,"authority":True,"parent_links":[],"guardian_links":[]}
                family["parentage"][result_ref]=path; counts["parentage"]=len(family["parentage"])
            else:
                result_ref=_stable_id(result_ref,"family_parentage_ref_invalid",prefix="family.parentage.")
                path=family.get("parentage",{}).get(result_ref)
                if not isinstance(path,str): raise CommandRejectedError("family_parentage_unresolved")
                record=copy.deepcopy(self.repository.read_json(path))
                if record.get("child_id")!=child_ref: raise CommandRejectedError("family_parentage_child_mismatch")
            if action=="adoption":
                if not parent_refs or command.actor_id not in parent_refs: raise CommandRejectedError("family_adoption_not_authorized")
                for ref in parent_refs: self._require_person_ref(ref)
                links=record.get("parent_links");
                if not isinstance(links,list): raise CommandRejectedError("family_parentage_invalid")
                for ref in parent_refs:
                    if not any(isinstance(x,Mapping) and x.get("parent_id")==ref for x in links): links.append({"parent_id":ref,"kind":"adoptive"})
                    self._append_unique(self._kinship_person_entry(kinship,child_ref)["parents"],ref); self._append_unique(self._kinship_person_entry(kinship,ref)["children"],child_ref)
                event_type="adoption"; family_subjects=[child_ref,*parent_refs]
            else:
                if not guardian_refs or command.actor_id not in guardian_refs: raise CommandRejectedError("family_guardianship_not_authorized")
                for ref in guardian_refs: self._require_person_ref(ref)
                links=record.get("guardian_links");
                if not isinstance(links,list): raise CommandRejectedError("family_parentage_invalid")
                for ref in guardian_refs:
                    if not any(isinstance(x,Mapping) and x.get("guardian_id")==ref and x.get("status")=="active" for x in links): links.append({"guardian_id":ref,"status":"active","started_at":str(current_time)})
                    self._append_unique(self._kinship_person_entry(kinship,child_ref)["guardians"],ref); self._append_unique(self._kinship_person_entry(kinship,ref)["wards"],child_ref)
                event_type="guardianship_started"; family_subjects=[child_ref,*guardian_refs]
            self._append_unique(self._family_person_entry(family,child_ref)["parentage"],result_ref)
            for ref in [*parent_refs,*guardian_refs]: self._append_unique(self._family_person_entry(family,ref)["parentage"],result_ref)
            writes[path]=_json_bytes(record); source_refs=[result_ref]

        elif action == "kinship_record":
            if command.payload["record_ref"] is not None or len(participants)!=2 or command.actor_id not in participants:
                raise CommandRejectedError("family_kinship_not_authorized")
            relation_kind=command.payload["relation_kind"]
            if relation_kind not in ("sibling","grandparent_grandchild","in_law","extended_kin","recognized_kin"): raise CommandRejectedError("family_kinship_kind_invalid")
            for ref in participants: self._require_person_ref(ref)
            result_ref=f"family.kinship.{command.digest[:24]}"; path=f"state/family/kinships/{result_ref}.json"
            record={"schema":"family-kinship","kinship_id":result_ref,"authority":True,"participants":participants,"kinship_type":relation_kind,"relation_roles":{},"status":"active","recognition":{"recognized":True,"basis":recognition_basis or "recognized kinship"},"source_refs":[],"notes":[summary]}
            family["kinships"][result_ref]=path; counts["kinships"]=len(family["kinships"])
            for ref in participants:
                self._append_unique(self._family_person_entry(family,ref)["kinships"],result_ref); self._append_unique(self._kinship_person_entry(kinship,ref)["kinships"],result_ref)
            writes[path]=_json_bytes(record); event_type="kinship_recognized"; family_subjects=participants; source_refs=[result_ref]

        else:  # succession_set
            subject_owner_ref=_stable_id(command.payload["subject_owner_ref"],"family_succession_owner_invalid")
            decision=self._domain_authority(cache=_OwnerResolutionCache()).owner_leadership(holder_ref=command.actor_id,owner_ref=subject_owner_ref)
            if not decision.allowed: raise CommandRejectedError("family_succession_not_authorized")
            candidate_order=command.payload["candidate_order"]
            if not isinstance(candidate_order,list) or len(candidate_order)>32: raise CommandRejectedError("family_succession_candidates_invalid")
            normalized=[]
            for row in candidate_order:
                if not isinstance(row,Mapping) or set(row)!={"person_id","basis"}: raise CommandRejectedError("family_succession_candidates_invalid")
                person_id=_stable_id(row["person_id"],"family_succession_candidate_invalid"); self._require_person_ref(person_id)
                basis=row["basis"]
                if not isinstance(basis,str) or not basis.strip() or len(basis)>500: raise CommandRejectedError("family_succession_candidates_invalid")
                normalized.append({"person_id":person_id,"basis":basis.strip()})
            if len({x["person_id"] for x in normalized})!=len(normalized): raise CommandRejectedError("family_succession_candidates_invalid")
            target_status=command.payload["target_status"] or "active"
            if target_status not in ("active","disputed","vacant","resolved","suspended"): raise CommandRejectedError("family_succession_status_invalid")
            result_ref=command.payload["record_ref"]
            if result_ref is None:
                result_ref=f"family.succession.{command.digest[:24]}"; path=f"state/family/successions/{result_ref}.json"; current_holder=None
                family["successions"][result_ref]=path; counts["successions"]=len(family["successions"])
            else:
                result_ref=_stable_id(result_ref,"family_succession_ref_invalid",prefix="family.succession."); path=family.get("successions",{}).get(result_ref)
                if not isinstance(path,str): raise CommandRejectedError("family_succession_unresolved")
                existing=self.repository.read_json(path); current_holder=existing.get("current_holder_id")
            record={"schema":"family-succession","succession_id":result_ref,"authority":True,"subject_owner_id":subject_owner_ref,"status":target_status,"rule_basis":[recognition_basis or decision.basis],"current_holder_id":current_holder,"candidate_order":normalized}
            for row in normalized:
                ref=row["person_id"]; self._append_unique(self._family_person_entry(family,ref)["successions"],result_ref); self._append_unique(self._kinship_person_entry(kinship,ref)["succession_claims"],result_ref)
            writes[path]=_json_bytes(record); event_type="succession_change"; family_subjects=[x["person_id"] for x in normalized]; source_refs=[subject_owner_ref,result_ref]

        event_id,event_path,event_record=self._family_event(command=command,family_index=family,event_type=event_type,at=current_time,subject_refs=family_subjects,source_refs=source_refs,suffix=re.sub(r"[^a-z0-9]+","_",action))
        writes[event_path]=_json_bytes(event_record)
        world_events=self._world_events()
        semantic_event_id=self._append_semantic_event(world_events,command=command,kind="family_lifecycle_changed",at=current_time,
            actor_refs=(command.actor_id,),affected_owner_refs=tuple(writes),material_consequence_refs=(result_ref,event_id),classification=visibility,
            audience_refs=tuple(family_subjects),reducer_ref="shinobi_runtime.commands.family_lifecycle_resolution")
        writes[_FAMILY_INDEX_PATH]=_json_bytes(family); writes[_KINSHIP_INDEX_PATH]=_json_bytes(kinship); writes[self.meta_path]=_json_bytes(self._meta_after(meta,command,world_time=current_time)); writes.update(self._world_event_writes(world_events)); writes=self._prune_noop_writes(writes); expected=tuple(sorted(writes))
        def validate(overlay:StagedOverlay,manifest:TransactionManifest)->None:
            if overlay.changed_paths!=expected: raise ValueError("family lifecycle write set changed after planning")
            self._assert_meta(overlay,manifest,meta_path=self.meta_path,command=command,world_time=current_time)
            if overlay.read_json(event_path).get("event_type")!=event_type: raise ValueError("family event mismatch")
        return _BuiltPlan(code="family_lifecycle_resolution_ready",affected_refs=expected,writes=writes,result={"action":action,"record_ref":result_ref,"family_event_ref":event_id,"semantic_event_id":semantic_event_id},validator=validate)


    def _family_birth_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        """Resolve one due live birth without creating population from nowhere.

        Parenthood intent is established separately by ``parenthood_begin``.
        This reducer only resolves a due birth boundary, adds exactly one
        physical person to one aggregate civilian pool, materializes that same
        person as sparse exact identity, records parentage, and optionally adds
        the child to an existing household.  The exact child is therefore part
        of the aggregate population count rather than an extra body beside it.
        """

        _exact_payload(
            command.payload,
            (
                "parenthood_ref", "destination_pool_id", "gestational_parent_ref",
                "name", "pronouns", "origin", "location_ref", "household_ref",
                "summary", "visibility",
            ),
            command.command_type,
        )
        parenthood_ref = _stable_id(
            command.payload["parenthood_ref"],
            "family_parenthood_ref_invalid",
            prefix="family.parenthood.",
        )
        destination_pool_id = _stable_id(
            command.payload["destination_pool_id"],
            "family_birth_pool_invalid",
            prefix="pool.",
        )
        gestational_parent_ref = _stable_id(
            command.payload["gestational_parent_ref"],
            "family_gestational_parent_invalid",
        )
        name = command.payload["name"]
        pronouns = command.payload["pronouns"]
        origin = command.payload["origin"]
        location_ref = _stable_id(command.payload["location_ref"], "family_birth_location_invalid", prefix="place.")
        household_raw = command.payload["household_ref"]
        household_ref = None if household_raw is None else _stable_id(
            household_raw, "family_household_ref_invalid", prefix="family.household."
        )
        summary = command.payload["summary"]
        visibility = command.payload["visibility"]
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise CommandRejectedError("family_birth_identity_invalid")
        if not isinstance(pronouns, str) or not pronouns.strip() or len(pronouns) > 40:
            raise CommandRejectedError("family_birth_identity_invalid")
        if not isinstance(origin, str) or not origin.strip() or len(origin) > 160:
            raise CommandRejectedError("family_birth_identity_invalid")
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 1000:
            raise CommandRejectedError("family_summary_invalid")
        if visibility not in ("public", "restricted", "secret"):
            raise CommandRejectedError("family_visibility_invalid")

        family, kinship = self._family_indexes()
        parenthoods = family.get("parenthoods")
        counts = family.get("counts")
        if not isinstance(parenthoods, dict) or not isinstance(counts, dict):
            raise CommandRejectedError("family_index_invalid")
        parenthood_path = parenthoods.get(parenthood_ref)
        if not isinstance(parenthood_path, str):
            raise CommandRejectedError("family_parenthood_unresolved")
        try:
            parenthood = copy.deepcopy(self.repository.read_json(parenthood_path))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("family_parenthood_unresolved") from exc
        if parenthood.get("schema") != "family-parenthood" or parenthood.get("status") != "expecting":
            raise CommandRejectedError("family_parenthood_not_expecting")
        parent_refs = parenthood.get("parent_refs")
        if not isinstance(parent_refs, list) or not 1 <= len(parent_refs) <= 2 or any(not isinstance(x, str) for x in parent_refs):
            raise CommandRejectedError("family_parenthood_invalid")
        if command.actor_id not in parent_refs and command.mode != "autonomous":
            raise CommandRejectedError("family_birth_not_authorized")
        if gestational_parent_ref not in parent_refs:
            raise CommandRejectedError("family_gestational_parent_invalid")
        if parenthood.get("child_ref") is not None:
            raise CommandRejectedError("family_parenthood_already_completed")
        try:
            expected_at = CampaignTime.parse(parenthood.get("expected_at"))
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("family_parenthood_invalid") from exc
        if _campaign_datetime(current_time) < _campaign_datetime(expected_at):
            raise CommandRejectedError("family_birth_not_due")

        parents: Dict[str, Mapping[str, Any]] = {}
        for parent_ref in parent_refs:
            parents[parent_ref] = self._require_person_ref(parent_ref)
        gestational_parent = parents[gestational_parent_ref]
        parent_location = gestational_parent.get("current_location_id")
        if not isinstance(parent_location, str):
            parent_location = gestational_parent.get("location_ref")
        if parent_location != location_ref:
            raise CommandRejectedError("family_birth_parent_not_colocated")
        condition = gestational_parent.get("condition")
        if isinstance(condition, Mapping) and condition.get("readiness") == "dead":
            raise CommandRejectedError("family_birth_parent_deceased")
        if gestational_parent.get("life_status") == "dead":
            raise CommandRejectedError("family_birth_parent_deceased")

        try:
            graph = LocationGraph(self.repository.read_json(_ROUTES_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("family_birth_location_invalid") from exc
        if graph.place(location_ref) is None:
            raise CommandRejectedError("family_birth_location_invalid")
        medical_factor = self._medical_facility(location_ref, required=False)

        # Health is deterministic and intentionally coarse at birth.  The
        # newborn is a sparse person core, not a fully generated combat sheet.
        parent_health_milli = 1000
        resources = gestational_parent.get("resources")
        health = resources.get("health") if isinstance(resources, Mapping) else None
        if isinstance(health, Mapping):
            current = health.get("current")
            capacity = health.get("capacity")
            if (
                not isinstance(current, bool) and isinstance(current, int)
                and not isinstance(capacity, bool) and isinstance(capacity, int) and capacity > 0
            ):
                parent_health_milli = max(0, min(1000, current * 1000 // capacity))
        birth_support_milli = int((medical_factor * Decimal(1000)).to_integral_value())
        newborn_health = "fit" if parent_health_milli + birth_support_milli >= 1500 else "limited"

        try:
            population = copy.deepcopy(self.repository.read_json(_POPULATION_REGISTRY_PATH))
            core_registry = copy.deepcopy(self.repository.read_json("state/person-core/world.json"))
            person_index = copy.deepcopy(self.repository.read_json("state/index/owners/person.json"))
            owner_index = copy.deepcopy(self.repository.read_json("state/index/owners.json"))
            continuity = copy.deepcopy(self.repository.read_json(_PERSON_CONTINUITY_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("family_birth_registry_invalid") from exc
        pools = population.get("pools") if isinstance(population, dict) else None
        pool = pools.get(destination_pool_id) if isinstance(pools, dict) else None
        if not isinstance(pool, dict) or pool.get("status") != "active" or pool.get("category") != "civilian_general":
            raise CommandRejectedError("family_birth_pool_invalid")
        before_count = pool.get("count")
        representation = pool.get("representation")
        profile = pool.get("profile")
        if (
            isinstance(before_count, bool) or not isinstance(before_count, int) or before_count < 0
            or not isinstance(representation, dict) or not isinstance(profile, dict)
        ):
            raise CommandRejectedError("family_birth_pool_invalid")
        anonymous = representation.get("anonymous_count")
        rostered = representation.get("rostered_count")
        rostered_refs = representation.get("rostered_person_refs")
        if (
            isinstance(anonymous, bool) or not isinstance(anonymous, int) or anonymous < 0
            or isinstance(rostered, bool) or not isinstance(rostered, int) or rostered < 0
            or not isinstance(rostered_refs, list) or anonymous + rostered != before_count
        ):
            raise CommandRejectedError("family_birth_pool_invalid")

        child_ref = "person." + command.digest[:24]
        people = core_registry.get("people") if isinstance(core_registry, dict) else None
        indexed_people = person_index.get("owners") if isinstance(person_index, dict) else None
        continuity_people = continuity.get("people") if isinstance(continuity, dict) else None
        if not isinstance(people, dict) or not isinstance(indexed_people, dict) or not isinstance(continuity_people, dict):
            raise CommandRejectedError("family_birth_registry_invalid")
        if child_ref in people or child_ref in indexed_people or child_ref in continuity_people:
            raise CommandRejectedError("family_birth_identity_conflict")

        pool_owner = pool.get("owner_ref")
        if not isinstance(pool_owner, str) or not pool_owner:
            raise CommandRejectedError("family_birth_pool_invalid")
        birth_date = f"SE-{current_time.year:04d}-{current_time.month:02d}-{current_time.day:02d}"
        child = {
            "id": child_ref,
            "name": name.strip(),
            "aliases": [],
            "pronouns": pronouns.strip(),
            "birth_date": birth_date,
            "birth_date_source": "family_birth_resolution",
            "origin": origin.strip(),
            "life_status": "alive",
            "affiliation_ref": pool_owner,
            "location_ref": location_ref,
            "cohort_ref": destination_pool_id,
            "cohort_slot": len(people),
            "role_profile_ref": None,
            "duty_tags": ["dependent", "newborn"],
            "resolved_through": str(current_time),
            "identity_cues": {
                "appearance": "Newborn appearance remains undescribed until established in play.",
                "temperament": "Temperament is not inferred at birth.",
                "doctrine_expression": "No doctrine or trained capability exists at birth.",
            },
            "component_refs": {},
            "provenance": {
                "source_kind": "family_live_birth",
                "source_ref": parenthood_ref,
                "materialized_at": str(current_time),
                "selection_method": "exact_new_person",
            },
        }
        people[child_ref] = child
        indexed_people[child_ref] = "state/person-core/world.json"
        owner_count = owner_index.get("owner_count")
        if isinstance(owner_count, bool) or not isinstance(owner_count, int) or owner_count < 0:
            raise CommandRejectedError("family_birth_registry_invalid")
        owner_index["owner_count"] = owner_count + 1
        continuity_people[child_ref] = {
            "person_ref": child_ref,
            "host_ref": pool_owner,
            "cohort_ref": destination_pool_id,
            "resolved_through": str(current_time),
            "life_experience_days": 0,
            "review_count": 0,
            "career_review_cycles": 0,
        }

        # One physical birth increases the aggregate pool by one and makes that
        # same new body rostered, so exact and aggregate representations agree.
        pool["count"] = before_count + 1
        pool["last_changed_at"] = str(current_time)
        representation["rostered_count"] = rostered + 1
        representation["rostered_person_refs"] = sorted([*rostered_refs, child_ref])
        category_counts = profile.get("category_counts")
        dimensions = profile.get("dimension_counts")
        numeric = profile.get("numeric_distributions")
        if not isinstance(category_counts, dict) or not isinstance(dimensions, dict) or not isinstance(numeric, dict):
            raise CommandRejectedError("family_birth_pool_invalid")
        category = str(pool.get("category"))
        category_counts[category] = int(category_counts.get(category, 0)) + 1
        for dimension_name, preferred in (
            ("age_band", "child"),
            ("chakra_potential", "unassessed"),
            ("health", newborn_health),
        ):
            values = dimensions.get(dimension_name)
            if isinstance(values, dict):
                if preferred not in values:
                    raise CommandRejectedError("family_birth_pool_invalid")
                values[preferred] = int(values.get(preferred, 0)) + 1
        age_dist = numeric.get("age_years")
        if isinstance(age_dist, dict):
            dist_count = age_dist.get("count")
            mean = age_dist.get("mean")
            if isinstance(dist_count, bool) or not isinstance(dist_count, int) or dist_count != before_count or not isinstance(mean, (int, float)) or isinstance(mean, bool):
                raise CommandRejectedError("family_birth_pool_invalid")
            age_dist["count"] = before_count + 1
            age_dist["mean"] = round(float(mean) * before_count / (before_count + 1), 6)
            age_dist["min"] = 0

        parenthood["status"] = "completed"
        parenthood["child_ref"] = child_ref
        parenthood["completed_at"] = str(current_time)

        parentage_ref = f"family.parentage.{command.digest[:24]}"
        parentage_path = f"state/family/parentage/{parentage_ref}.json"
        parentage_bucket = family.get("parentage")
        if not isinstance(parentage_bucket, dict) or parentage_ref in parentage_bucket or self.repository.read_optional_bytes(parentage_path) is not None:
            raise CommandRejectedError("family_parentage_conflict")
        parentage = {
            "schema": "family-parentage",
            "parentage_id": parentage_ref,
            "child_id": child_ref,
            "authority": True,
            "parent_links": [
                {"parent_id": parent_ref, "kind": "biological", "recognized_at": str(current_time), "basis": parenthood_ref}
                for parent_ref in parent_refs
            ],
            "guardian_links": [],
        }
        parentage_bucket[parentage_ref] = parentage_path
        counts["parentage"] = len(parentage_bucket)
        self._append_unique(self._family_person_entry(family, child_ref)["parentage"], parentage_ref)
        self._append_unique(self._family_person_entry(family, child_ref)["parenthoods"], parenthood_ref)
        for parent_ref in parent_refs:
            self._append_unique(self._family_person_entry(family, parent_ref)["parentage"], parentage_ref)
            self._append_unique(self._kinship_person_entry(kinship, child_ref)["parents"], parent_ref)
            self._append_unique(self._kinship_person_entry(kinship, parent_ref)["children"], child_ref)

        household_path: Optional[str] = None
        household: Optional[Dict[str, Any]] = None
        if household_ref is not None:
            households = family.get("households")
            household_path = households.get(household_ref) if isinstance(households, dict) else None
            if not isinstance(household_path, str):
                raise CommandRejectedError("family_household_unresolved")
            try:
                household = copy.deepcopy(self.repository.read_json(household_path))
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("family_household_unresolved") from exc
            members = household.get("member_refs")
            dependents = household.get("dependent_refs")
            if household.get("status") == "dissolved" or not isinstance(members, list) or not isinstance(dependents, list):
                raise CommandRejectedError("family_household_invalid")
            if not any(parent in members for parent in parent_refs):
                raise CommandRejectedError("family_birth_household_parent_missing")
            self._append_unique(dependents, child_ref)
            self._append_unique(self._family_person_entry(family, child_ref)["households"], household_ref)
            self._append_unique(self._kinship_person_entry(kinship, child_ref)["households"], household_ref)

        event_id, family_event_path, family_event = self._family_event(
            command=command,
            family_index=family,
            event_type="birth",
            at=current_time,
            subject_refs=[*parent_refs, child_ref],
            source_refs=[parenthood_ref, parentage_ref],
            suffix="birth",
        )
        family_event["summary"] = summary.strip()
        family_event["visibility"] = visibility
        family_event["health_resolution"] = {
            "gestational_parent_ref": gestational_parent_ref,
            "parent_health_milli": parent_health_milli,
            "medical_support_milli": birth_support_milli,
            "newborn_health": newborn_health,
        }

        world_events = self._world_events()
        affected = [
            parenthood_path, parentage_path, _FAMILY_INDEX_PATH, _KINSHIP_INDEX_PATH,
            _POPULATION_REGISTRY_PATH, "state/person-core/world.json",
            "state/index/owners/person.json", "state/index/owners.json", _PERSON_CONTINUITY_PATH,
        ]
        if household_path is not None:
            affected.append(household_path)
        semantic_event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind="family_birth_resolved",
            at=current_time,
            actor_refs=(command.actor_id,),
            place_refs=(location_ref,),
            affected_owner_refs=tuple(affected),
            material_consequence_refs=(child_ref, parenthood_ref, parentage_ref, f"population:{destination_pool_id}:+1"),
            classification=visibility,
            audience_refs=tuple(parent_refs),
            reducer_ref="shinobi_runtime.commands.family_birth_resolution",
        )
        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            parenthood_path: _json_bytes(parenthood),
            parentage_path: _json_bytes(parentage),
            family_event_path: _json_bytes(family_event),
            _FAMILY_INDEX_PATH: _json_bytes(family),
            _KINSHIP_INDEX_PATH: _json_bytes(kinship),
            _POPULATION_REGISTRY_PATH: _json_bytes(population),
            "state/person-core/world.json": _json_bytes(core_registry),
            "state/index/owners/person.json": _json_bytes(person_index),
            "state/index/owners.json": _json_bytes(owner_index),
            _PERSON_CONTINUITY_PATH: _json_bytes(continuity),
            **self._world_event_writes(world_events),
        }
        if household_path is not None and household is not None:
            writes[household_path] = _json_bytes(household)
        writes = self._prune_noop_writes(writes)
        expected = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected:
                raise ValueError("family birth write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
            staged_population = overlay.read_json(_POPULATION_REGISTRY_PATH)
            staged_pool = staged_population.get("pools", {}).get(destination_pool_id, {})
            if staged_pool.get("count") != before_count + 1:
                raise ValueError("family birth population count mismatch")
            staged_representation = staged_pool.get("representation", {})
            if staged_representation.get("rostered_count") != rostered + 1 or child_ref not in staged_representation.get("rostered_person_refs", []):
                raise ValueError("family birth representation mismatch")
            if overlay.read_json(parenthood_path).get("child_ref") != child_ref:
                raise ValueError("family birth parenthood mismatch")
            staged_parentage = overlay.read_json(parentage_path)
            if staged_parentage.get("child_id") != child_ref:
                raise ValueError("family birth parentage mismatch")
            if child_ref not in overlay.read_json("state/person-core/world.json").get("people", {}):
                raise ValueError("family birth person missing")

        return _BuiltPlan(
            code="family_birth_resolution_ready",
            affected_refs=expected,
            writes=writes,
            result={
                "child_ref": child_ref,
                "parenthood_ref": parenthood_ref,
                "parentage_ref": parentage_ref,
                "destination_pool_id": destination_pool_id,
                "population_before": before_count,
                "population_after": before_count + 1,
                "newborn_health": newborn_health,
                "family_event_ref": event_id,
                "semantic_event_id": semantic_event_id,
            },
            validator=validate,
        )


