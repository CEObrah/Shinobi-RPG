"""Player-facing House missions and delegated strategic operations.

This module owns institutional intent only. Physical people, travel, combat,
custody, equipment, provisions and faction treasuries remain owned by their
existing Jianghu authorities.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.martial_world.agency import can_command_faction_members, office_roots
from shinobi_runtime.martial_world.allied_support import stage_offensive_support_request
from shinobi_runtime.martial_world.commitments import derived_commitment_state, reserve_resources
from shinobi_runtime.martial_world.contracts import transition as contract_transition
from shinobi_runtime.martial_world.faction_relations import active_treaty_kinds, conflict_stage, proposal_kind_supported, treaty_forbids_hostilities
from shinobi_runtime.martial_world.faction_state import inventory_path, read_faction
from shinobi_runtime.martial_world.membership import grade_eligibility
from shinobi_runtime.martial_world.character_rules import martial_discipline_keys
from shinobi_runtime.martial_world.institutional_operations import (
    OPERATIONS_PATH,
    close_institutional_operation,
    ensure_contract_dossier,
)
from shinobi_runtime.martial_world.live_state import set_roster_person
from shinobi_runtime.martial_world.scheduler import upsert_one_off_event
from shinobi_runtime.martial_world.scene_sessions import (
    ATTEMPT_LEDGER_PATH, SESSION_PATH, abandon_session_questions, active_scene_session,
    interaction_ledger, new_session_record,
)
from shinobi_runtime.martial_world.travel import travel_plan

_DEPLOYMENTS = "state/martial-world/deployments.json"
_SCHEDULE = "state/martial-world/scheduler.json"
_CUSTODY = "state/martial-world/custody.json"
_CONTRACTS = "state/martial-world/contracts/index.json"
_LOCAL_SITES = "game/data/martial-world/local-sites.json"

_SUPPORTED_MISSIONS = frozenset({"escort", "reconnaissance", "rescue", "raid", "war_strike", "reinforcement", "diplomacy"})
_OPERATION_BY_MISSION = {
    "reconnaissance": "faction_reconnaissance",
    "rescue": "custody_rescue",
    "raid": "faction_raid",
    "war_strike": "faction_war_strike",
    "reinforcement": "allied_defense_reinforcement",
    "diplomacy": "diplomatic_mission",
}
_HIGH_AUTHORITY = frozenset({"leader", "deputy_leader"})
_FIELD_AUTHORITY = frozenset({"leader", "deputy_leader", "field_commander", "deputy_field_commander"})


def _iso(time: Any) -> str:
    return str(time).removeprefix("SE-")


def _operation_state(repository: Any) -> dict[str, Any]:
    try:
        raw = repository.read_json(OPERATIONS_PATH)
    except FileNotFoundError:
        raw = {"schema": "jianghu-institutional-operations-state-1.0", "active": {}, "archive": {}}
    out = copy.deepcopy(dict(raw)) if isinstance(raw, Mapping) else {}
    out.setdefault("schema", "jianghu-institutional-operations-state-1.0")
    out.setdefault("active", {})
    out.setdefault("archive", {})
    return out


def _authorized_for(person: Mapping[str, Any], mission_kind: str) -> bool:
    offices = office_roots(person)
    required = _HIGH_AUTHORITY if mission_kind in {"raid", "war_strike", "reinforcement", "diplomacy"} else _FIELD_AUTHORITY
    return bool(offices & required)


def _site_place(sites: Mapping[str, Any], site_ref: str) -> str:
    row = sites.get(site_ref) if isinstance(sites, Mapping) else None
    if not isinstance(row, Mapping):
        return ""
    return str(row.get("parent_place_ref") or "")



def _directed_relation(relations: Mapping[str, Any], source: str, target: str) -> Mapping[str, Any]:
    rows = relations.get("edges", []) if isinstance(relations, Mapping) else []
    if not isinstance(rows, list):
        return {}
    return next((row for row in rows if isinstance(row, Mapping) and str(row.get("from_faction") or "") == source and str(row.get("to_faction") or "") == target), {})


def _council_views(*, attendee_rows: list[tuple[str, Mapping[str, Any]]], mission_kind: str, target_faction: str, relations: Mapping[str, Any], treasury_cash: int, reward_cash: int) -> list[dict[str, Any]]:
    edge = _directed_relation(relations, str(attendee_rows[0][1].get("faction_ref") or "") if attendee_rows else "", target_faction) if target_faction else {}
    hostility = max(0, int(edge.get("hostility", 0)))
    views: list[dict[str, Any]] = []
    for ref, person in attendee_rows:
        offices = office_roots(person)
        stance = "neutral"
        concerns: list[str] = []
        if "scout_leader" in offices:
            if mission_kind == "reconnaissance": stance = "support"
            elif mission_kind in {"raid", "war_strike"}: concerns.append("current_intelligence")
        if "chief_physician" in offices and mission_kind in {"raid", "war_strike", "rescue", "reinforcement"}:
            concerns.append("casualty_risk")
            if stance == "neutral": stance = "caution"
        if offices & {"quartermaster", "treasurer", "chief_steward"}:
            if reward_cash > max(500, treasury_cash // 20): concerns.append("reward_cost")
            concerns.append("provisions_and_equipment")
        if offices & {"leader", "deputy_leader"}:
            if mission_kind == "rescue" or (mission_kind in {"raid", "war_strike"} and hostility >= 45): stance = "support"
            elif mission_kind == "diplomacy": concerns.append("binding_house_terms")
            elif mission_kind in {"raid", "war_strike"} and hostility < 25: stance = "oppose"
        views.append({"person_ref": ref, "stance": stance, "concerns": sorted(set(concerns))})
    return views


class JianghuInstitutionalCommandsMixin:
    def _institutional_person(self, ref: str) -> tuple[str, Mapping[str, Any], int, dict[str, Any]]:
        return self._person(ref)

    def _institutional_effective_location(self, ref: str, person: Mapping[str, Any], faction: Mapping[str, Any]) -> str:
        """Use exact physical ownership, falling back only for sparse at-home records."""
        location = self._effective_person_location(ref, person)
        return str(location or faction.get("local_site_ref") or faction.get("headquarters") or "")

    def _institutional_effective_place(
        self,
        ref: str,
        person: Mapping[str, Any],
        faction: Mapping[str, Any],
        sites_doc: Mapping[str, Any],
    ) -> str:
        location = self._institutional_effective_location(ref, person, faction)
        sites = sites_doc.get("sites", {}) if isinstance(sites_doc, Mapping) else {}
        return _site_place(sites, location) or location

    def _jianghu_institutional_operation_resolution(self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: Any):
        self._require_jianghu(meta)
        action = str(command.payload.get("action") or "")
        op_ref = str(command.payload.get("operation_ref") or "")
        if not op_ref or len(op_ref) > 160 or any(ch in op_ref for ch in "\r\n\x00"):
            raise CommandRejectedError("jianghu_institutional_operation_ref_invalid")
        escort_specialization = getattr(self, "_institutional_escort_operation_specialization", None)
        if callable(escort_specialization):
            specialized = escort_specialization(command, meta, current_time)
            if specialized is not None:
                return specialized
        state = _operation_state(self.repository)
        active = state.get("active")
        archive = state.get("archive")
        if not isinstance(active, dict) or not isinstance(archive, dict):
            raise CommandRejectedError("jianghu_institutional_operation_state_invalid")
        now_iso = _iso(current_time)
        actor_path, actor_roster, _actor_ordinal, actor = self._institutional_person(command.actor_id)
        actor_faction = str(actor.get("faction_ref") or "")
        if not actor_faction:
            raise CommandRejectedError("jianghu_institutional_faction_required")

        if action == "propose":
            if op_ref in active or op_ref in archive:
                raise CommandRejectedError("jianghu_institutional_operation_exists")
            mission_kind = str(command.payload.get("mission_kind") or "")
            if mission_kind not in _SUPPORTED_MISSIONS:
                raise CommandRejectedError("jianghu_institutional_mission_kind_unsupported")
            objective = str(command.payload.get("objective") or "").strip()
            if not objective or len(objective) > 500:
                raise CommandRejectedError("jianghu_institutional_objective_invalid")
            target_faction = str(command.payload.get("target_faction_ref") or "")
            target_site = str(command.payload.get("target_site_ref") or "")
            target_person = str(command.payload.get("target_person_ref") or "")
            linked_contract = str(command.payload.get("linked_contract_ref") or "")
            reward_cash = max(0, int(command.payload.get("reward_cash", 0) or 0))
            reward_mode = str(command.payload.get("reward_mode") or "none")
            if reward_mode not in {"none", "commander", "equal_returned"}:
                raise CommandRejectedError("jianghu_institutional_reward_mode_invalid")
            if reward_cash and reward_mode == "none":
                raise CommandRejectedError("jianghu_institutional_reward_mode_invalid")
            if mission_kind == "escort" and not linked_contract:
                raise CommandRejectedError("jianghu_institutional_escort_requires_contract")
            if mission_kind in {"raid", "war_strike", "reconnaissance", "reinforcement"} and not target_faction:
                raise CommandRejectedError("jianghu_institutional_target_faction_required")
            if mission_kind == "rescue" and not target_person:
                raise CommandRejectedError("jianghu_institutional_rescue_target_required")
            # Gameplay commands are player-authored by contract. House-issued
            # offers are generated by the autonomous institutional review path,
            # never by spoofing an NPC through the player command API.
            source = "player_proposal"
            row = {
                "operation_ref": op_ref,
                "faction_ref": actor_faction,
                "mission_source": source,
                "issuer_ref": command.actor_id,
                "assignee_ref": command.actor_id,
                "mission_kind": mission_kind,
                "objective": objective,
                "target_faction_ref": target_faction,
                "target_site_ref": target_site,
                "target_person_ref": target_person,
                "linked_contract_ref": linked_contract,
                "reward_cash": reward_cash,
                "reward_mode": reward_mode,
                "phase": "proposed",
                "created_at": now_iso,
                "updated_at": now_iso,
            }
            # Empty optional refs are not durable state.
            row = {k: v for k, v in row.items() if v not in ("", None)}
            active[op_ref] = row
            return self._simple_plan(
                command, meta, current_time, writes_records={OPERATIONS_PATH: state},
                code="jianghu_institutional_operation_proposed",
                result={"command_type": command.command_type, "action": action, "operation_ref": op_ref, "phase": row["phase"], "mission_kind": mission_kind},
            )

        row = active.get(op_ref)
        if not isinstance(row, Mapping):
            if action in {"settle_reward", "service_review", "accept_career_offer", "decline_career_offer"} and isinstance(archive.get(op_ref), Mapping):
                row = archive[op_ref]
            else:
                raise CommandRejectedError("jianghu_institutional_operation_unresolved")
        row = copy.deepcopy(dict(row))
        if str(row.get("faction_ref") or "") != actor_faction:
            raise CommandRejectedError("jianghu_institutional_wrong_faction")

        if action == "accept_assignment":
            if row.get("mission_source") != "house_assignment" or row.get("phase") != "offered":
                raise CommandRejectedError("jianghu_institutional_assignment_not_open")
            if command.actor_id != str(row.get("assignee_ref") or ""):
                raise CommandRejectedError("jianghu_institutional_assignment_wrong_assignee")
            writes: dict[str, Any] = {OPERATIONS_PATH: state}
            linked_contract = str(row.get("linked_contract_ref") or "")
            if str(row.get("mission_kind") or "") == "escort":
                if not linked_contract:
                    raise CommandRejectedError("jianghu_institutional_escort_requires_contract")
                index = copy.deepcopy(self.repository.read_json(_CONTRACTS))
                contract_rows = index.get("active", {}) if isinstance(index, Mapping) else {}
                contract = contract_rows.get(linked_contract) if isinstance(contract_rows, dict) else None
                if not isinstance(contract, Mapping) or contract.get("contract_type") != "escort" or contract.get("status") != "offered" or contract.get("beneficiary_ref") not in {None, ""}:
                    raise CommandRejectedError("jianghu_institutional_escort_contract_not_offered")
                try:
                    if datetime.fromisoformat(str(contract.get("expires_at") or "")) <= datetime.fromisoformat(now_iso):
                        raise CommandRejectedError("jianghu_institutional_escort_contract_expired")
                except ValueError as exc:
                    raise CommandRejectedError("jianghu_institutional_escort_contract_expiry_invalid") from exc
                try:
                    accepted_contract = contract_transition(
                        contract, at=now_iso, to_status="accepted", actor_ref=command.actor_id,
                        participants=[command.actor_id],
                    )
                except ValueError as exc:
                    raise CommandRejectedError("jianghu_institutional_escort_contract_transition_invalid") from exc
                accepted_contract["beneficiary_ref"] = actor_faction
                contract_rows[linked_contract] = accepted_contract
                writes[_CONTRACTS] = index
            row["phase"] = "accepted"
            row["accepted_at"] = now_iso
            row["updated_at"] = now_iso
            active[op_ref] = row
            writes[OPERATIONS_PATH] = state
            if linked_contract:
                ensure_contract_dossier(
                    read_json=self.repository.read_json, writes=writes, contract_ref=linked_contract,
                    faction_ref=actor_faction, actor_ref=command.actor_id, at_iso=now_iso, phase="accepted",
                    participant_refs=[command.actor_id], objective=str(row.get("objective") or "Fulfill House escort assignment"),
                    issuer_ref=str(row.get("issuer_ref") or ""),
                )
            return self._simple_plan(command, meta, current_time, writes_records=writes, code="jianghu_institutional_assignment_accepted", result={"command_type": command.command_type, "operation_ref": op_ref, "phase": "accepted", **({"contract_ref": linked_contract} if linked_contract else {})})

        if action == "convene":
            if row.get("phase") not in {"proposed", "offered", "accepted", "briefed"}:
                raise CommandRejectedError("jianghu_institutional_council_phase_invalid")
            if row.get("mission_source") == "house_assignment" and row.get("phase") == "offered" and command.actor_id == row.get("assignee_ref"):
                raise CommandRejectedError("jianghu_institutional_assignment_requires_accept_or_decline")
            attendee_refs = list(dict.fromkeys(str(x) for x in command.payload.get("attendee_refs", []) if isinstance(x, str) and x))
            if command.actor_id not in attendee_refs:
                attendee_refs.insert(0, command.actor_id)
            _fpath, faction = read_faction(self.repository, actor_faction)
            sites_doc = self.repository.read_json(_LOCAL_SITES)
            sites = sites_doc.get("sites", {}) if isinstance(sites_doc, Mapping) else {}
            actor_location = self._institutional_effective_location(command.actor_id, actor, faction)
            actor_place = self._institutional_effective_place(command.actor_id, actor, faction, sites_doc)
            authorities: list[tuple[int, str]] = []
            attendee_names: dict[str, str] = {}
            attendee_rows: list[tuple[str, Mapping[str, Any]]] = []
            for ref in attendee_refs:
                _path, _roster, _ordinal, person = self._institutional_person(ref)
                if str(person.get("faction_ref") or "") != actor_faction:
                    raise CommandRejectedError("jianghu_institutional_council_wrong_faction")
                effective_ref = self._institutional_effective_location(ref, person, faction)
                place = self._institutional_effective_place(ref, person, faction, sites_doc)
                # A live House council is a physical scene, not merely a city-level
                # institutional record. Exact presence owners must place every
                # attendee in the same physical space as Wei.
                if not self._same_effective_location(command.actor_id, ref):
                    raise CommandRejectedError("jianghu_institutional_council_attendee_not_present")
                health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
                if health.get("status") in {"dead", "incapacitated"} or int(health.get("consciousness", 100)) <= 0:
                    raise CommandRejectedError("jianghu_institutional_council_attendee_unavailable")
                offices = office_roots(person)
                priority = 0
                if "leader" in offices: priority = 5
                elif "deputy_leader" in offices: priority = 4
                elif "field_commander" in offices: priority = 3
                elif "deputy_field_commander" in offices: priority = 2
                if _authorized_for(person, str(row.get("mission_kind") or "")):
                    authorities.append((priority, ref))
                attendee_names[ref] = str(person.get("name") or ref)
                attendee_rows.append((ref, person))
            try:
                relations = self.repository.read_json("state/martial-world/faction-relations.json")
            except FileNotFoundError:
                relations = {}
            row["council"] = {
                "convened_at": now_iso,
                "location_ref": actor_location,
                "place_ref": actor_place,
                "attendee_refs": attendee_refs,
                "attendee_names": attendee_names,
                "views": _council_views(
                    attendee_rows=attendee_rows, mission_kind=str(row.get("mission_kind") or ""),
                    target_faction=str(row.get("target_faction_ref") or ""), relations=relations,
                    treasury_cash=max(0, int(faction.get("treasury_cash", 0))), reward_cash=max(0, int(row.get("reward_cash", 0))),
                ),
                "approval_authority_ref": sorted(authorities, key=lambda x: (-x[0], x[1]))[0][1] if authorities else "",
            }
            row["phase"] = "briefed"
            row["updated_at"] = now_iso
            active[op_ref] = row
            session_ref = f"scene_session:council:{op_ref}"
            session = new_session_record(
                session_ref=session_ref, kind="house_council", location_ref=actor_location,
                participant_refs=attendee_refs, at=now_iso, process_ref=op_ref,
                purpose=str(row.get("objective") or f"House council for {row.get('mission_kind') or 'operation'}"),
                agenda=(
                    "known intelligence and objective", "command and participant assignment",
                    "provisions and equipment", "authority and launch conditions",
                ),
            )
            writes = {OPERATIONS_PATH: state, SESSION_PATH: session}
            prior_session = active_scene_session(self.repository.read_json)
            if isinstance(prior_session, Mapping):
                ledger = interaction_ledger(self.repository.read_json)
                ledger, _ = abandon_session_questions(
                    ledger, session_ref=str(prior_session.get("session_ref") or ""), at=now_iso
                )
                writes[ATTEMPT_LEDGER_PATH] = ledger
            return self._simple_plan(command, meta, current_time, writes_records=writes, code="jianghu_institutional_council_convened", result={"command_type": command.command_type, "operation_ref": op_ref, "scene_session_ref": session_ref, "attendee_refs": attendee_refs, "approval_authority_ref": row["council"].get("approval_authority_ref")})

        if action == "submit_plan":
            if row.get("phase") not in {"briefed", "accepted", "proposed", "revision_requested"}:
                raise CommandRejectedError("jianghu_institutional_plan_phase_invalid")
            commander_ref = str(command.payload.get("commander_ref") or "")
            member_refs = list(dict.fromkeys(str(x) for x in command.payload.get("member_refs", []) if isinstance(x, str) and x))
            operation_kind = str(command.payload.get("operation_kind") or "")
            doctrine = str(command.payload.get("doctrine") or "standard").strip() or "standard"
            expected_kind = _OPERATION_BY_MISSION.get(str(row.get("mission_kind") or ""), "")
            if row.get("mission_kind") == "escort":
                expected_kind = "escort_contract"
            if operation_kind != expected_kind:
                raise CommandRejectedError("jianghu_institutional_operation_kind_mismatch")
            if not commander_ref or commander_ref not in member_refs:
                raise CommandRejectedError("jianghu_institutional_commander_must_be_member")
            if not member_refs:
                raise CommandRejectedError("jianghu_institutional_members_required")
            _fpath, faction = read_faction(self.repository, actor_faction)
            sites_doc = self.repository.read_json(_LOCAL_SITES)
            locations: set[str] = set()
            all_people = actor_roster.get("people", []) if isinstance(actor_roster.get("people"), list) else []
            for ref in member_refs:
                _path, _roster, _ordinal, person = self._institutional_person(ref)
                if str(person.get("faction_ref") or "") != actor_faction:
                    raise CommandRejectedError("jianghu_institutional_member_wrong_faction")
                self._require_person_available_for_activity(ref, "jianghu_institutional_member_unavailable")
                # A mission roster departs as one physical party. Sharing the
                # same city/parent place is not co-presence: people in a manor,
                # inn and market cannot be teleported into the commander's site
                # when dispatch is committed. Exact local-site ownership is the
                # muster authority.
                locations.add(self._institutional_effective_location(ref, person, faction))
            if len(locations) != 1:
                raise CommandRejectedError("jianghu_institutional_members_not_colocated")
            council = row.get("council") if isinstance(row.get("council"), Mapping) else {}
            approval_ref = str(council.get("approval_authority_ref") or "")
            if not approval_ref:
                # A lawful player officeholder can approve directly. Everyone
                # else needs a real authority recorded at a council.
                if _authorized_for(actor, str(row.get("mission_kind") or "")):
                    approval_ref = command.actor_id
                else:
                    raise CommandRejectedError("jianghu_institutional_plan_not_authorized")
            mission_kind = str(row.get("mission_kind") or "")
            target_faction = str(row.get("target_faction_ref") or "")
            if mission_kind == "diplomacy":
                proposal_kind = str(command.payload.get("proposal_kind") or "")
                if not proposal_kind_supported(proposal_kind):
                    raise CommandRejectedError("jianghu_institutional_diplomacy_proposal_unsupported")
                value_cash = int(command.payload.get("value_cash") or 0)
                cost_cash = int(command.payload.get("cost_cash") or 0)
                if value_cash < 0 or cost_cash < 0:
                    raise CommandRejectedError("jianghu_institutional_diplomacy_cash_term_invalid")
                source_captives = sorted(dict.fromkeys(str(x) for x in (command.payload.get("source_captive_refs") or []) if isinstance(x, str) and x))
                target_captives = sorted(dict.fromkeys(str(x) for x in (command.payload.get("target_captive_refs") or []) if isinstance(x, str) and x))
                if proposal_kind == "prisoner_exchange" and not (source_captives or target_captives):
                    raise CommandRejectedError("jianghu_institutional_prisoner_exchange_empty")
                if len(set(source_captives + target_captives)) != len(source_captives) + len(target_captives):
                    raise CommandRejectedError("jianghu_institutional_prisoner_exchange_duplicate")
                row["diplomacy_authorization"] = {
                    "proposal_kind": proposal_kind, "value_cash": value_cash, "cost_cash": cost_cash,
                    "source_captive_refs": source_captives, "target_captive_refs": target_captives,
                }
            try:
                relations = self.repository.read_json("state/martial-world/faction-relations.json")
            except FileNotFoundError:
                relations = {}
            edge = _directed_relation(relations, actor_faction, target_faction) if target_faction else {}
            treaties = active_treaty_kinds(relations, actor_faction, target_faction) if target_faction else set()
            reason = ""
            if mission_kind in {"raid", "war_strike"} and target_faction and treaty_forbids_hostilities(relations, actor_faction, target_faction):
                reason = "active_non_hostility_treaty"
            elif row.get("mission_source") != "house_assignment":
                hostility = max(0, int(edge.get("hostility", 0)))
                if mission_kind == "raid" and hostility < 35:
                    reason = "insufficient_strategic_cause"
                elif mission_kind == "war_strike" and conflict_stage(edge) != "war" and hostility < 75:
                    reason = "war_not_established"
                elif mission_kind == "reinforcement" and not (treaties & {"mutual_defense", "alliance"}):
                    reason = "no_defense_obligation"
            effective_location = next(iter(locations)) if locations else ""
            sites = sites_doc.get("sites", {}) if isinstance(sites_doc, Mapping) else {}
            effective_place = _site_place(sites, effective_location) or effective_location
            ready_home = 0
            physically_unavailable = self._physically_unavailable_person_refs()
            for person in all_people:
                if not isinstance(person, Mapping):
                    continue
                person_ref = str(person.get("person_id") or "")
                health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
                if (
                    not person_ref
                    or person_ref in physically_unavailable
                    or health.get("status") == "dead"
                    or int(health.get("consciousness", 100)) <= 0
                    or bool(person.get("retired_from_field", False))
                ):
                    continue
                if self._institutional_effective_place(person_ref, person, faction, sites_doc) == effective_place:
                    ready_home += 1
            if mission_kind in {"raid", "war_strike", "reinforcement"} and ready_home > 2:
                max_field = max(1, ready_home - max(1, (ready_home * 25 + 99) // 100))
                if len(member_refs) > max_field:
                    reason = "insufficient_home_reserve"
            reward_cash = max(0, int(row.get("reward_cash", 0)))
            if reward_cash > max(1000, int(faction.get("treasury_cash", 0)) // 20):
                reason = "reward_exceeds_house_authorization"
            if reason:
                row["phase"] = "revision_requested" if reason in {"insufficient_home_reserve", "reward_exceeds_house_authorization"} else "rejected"
                row["decision"] = {"decided_at": now_iso, "authority_ref": approval_ref, "result": row["phase"], "reason": reason}
                row["updated_at"] = now_iso
                active[op_ref] = row
                return self._simple_plan(command, meta, current_time, writes_records={OPERATIONS_PATH: state}, code="jianghu_institutional_plan_revision_requested" if row["phase"] == "revision_requested" else "jianghu_institutional_plan_rejected", result={"command_type": command.command_type, "operation_ref": op_ref, "phase": row["phase"], "reason": reason, "approved_by_ref": approval_ref})
            row["commander_ref"] = commander_ref
            row["participant_refs"] = member_refs
            row["operation_kind"] = operation_kind
            row["doctrine"] = doctrine[:240]
            row["approved_by_ref"] = approval_ref
            row["approved_at"] = now_iso
            row["decision"] = {"decided_at": now_iso, "authority_ref": approval_ref, "result": "approved"}
            row["phase"] = "approved"
            row["updated_at"] = now_iso
            active[op_ref] = row
            return self._simple_plan(command, meta, current_time, writes_records={OPERATIONS_PATH: state}, code="jianghu_institutional_plan_approved", result={"command_type": command.command_type, "operation_ref": op_ref, "commander_ref": commander_ref, "member_count": len(member_refs), "approved_by_ref": approval_ref})

        if action == "dispatch":
            if row.get("phase") != "approved":
                raise CommandRejectedError("jianghu_institutional_operation_not_approved")
            mission_kind = str(row.get("mission_kind") or "")
            if mission_kind == "escort":
                raise CommandRejectedError("jianghu_institutional_escort_dispatch_uses_contract_start")
            if mission_kind == "diplomacy":
                raise CommandRejectedError("jianghu_institutional_diplomacy_uses_diplomacy_resolution")
            member_refs = [str(x) for x in row.get("participant_refs", []) if isinstance(x, str) and x]
            commander_ref = str(row.get("commander_ref") or "")
            if not member_refs or commander_ref not in member_refs:
                raise CommandRejectedError("jianghu_institutional_plan_invalid")
            faction_path, faction = read_faction(self.repository, actor_faction)
            sites_doc = self.repository.read_json(_LOCAL_SITES)
            _cpath, _croster, _cordinal, commander = self._institutional_person(commander_ref)
            source_ref = self._institutional_effective_location(commander_ref, commander, faction)
            # Plans and dispatch can be separated by arbitrary play. Revalidate
            # every saved member against current faction, availability and exact
            # physical muster site before creating a movement owner.
            for ref in member_refs:
                self._require_person_available_for_activity(ref, "jianghu_institutional_member_unavailable")
                _mpath, _mroster, _mordinal, member = self._institutional_person(ref)
                if str(member.get("faction_ref") or "") != actor_faction:
                    raise CommandRejectedError("jianghu_institutional_member_wrong_faction")
                if self._institutional_effective_location(ref, member, faction) != source_ref:
                    raise CommandRejectedError("jianghu_institutional_members_not_colocated")
            source_place = _site_place(sites_doc.get("sites", {}) if isinstance(sites_doc, Mapping) else {}, source_ref) or source_ref
            source_site = source_ref if _site_place(sites_doc.get("sites", {}) if isinstance(sites_doc, Mapping) else {}, source_ref) else ""
            target_faction = str(row.get("target_faction_ref") or "")
            target_site = str(row.get("target_site_ref") or "")
            target_place = ""
            if mission_kind == "rescue":
                custody = self.repository.read_json(_CUSTODY)
                records = custody.get("records", []) if isinstance(custody, Mapping) else []
                captive_ref = str(row.get("target_person_ref") or "")
                record = next((r for r in records if isinstance(r, Mapping) and str(r.get("person_ref") or "") == captive_ref and r.get("status") not in {"released", "escaped", "rescued", "executed"}), None)
                if not isinstance(record, Mapping):
                    raise CommandRejectedError("jianghu_institutional_rescue_custody_unresolved")
                target_faction = str(record.get("holder_faction_ref") or "")
                target_site = str(record.get("location_ref") or "")
                row["target_faction_ref"] = target_faction
                row["target_site_ref"] = target_site
                row["custody_ref"] = str(record.get("custody_id") or "")
            if target_faction:
                _tfp, tf = read_faction(self.repository, target_faction)
                target_place = str(tf.get("headquarters") or "")
                if not target_site:
                    target_site = str(tf.get("local_site_ref") or "")
            if target_site:
                sites_doc = self.repository.read_json(_LOCAL_SITES)
                sites = sites_doc.get("sites", {}) if isinstance(sites_doc, Mapping) else {}
                site_place = _site_place(sites, target_site)
                if site_place:
                    target_place = site_place
            if not source_place or not target_place:
                raise CommandRejectedError("jianghu_institutional_route_unresolved")
            if target_faction and target_faction != actor_faction and mission_kind in {"raid", "war_strike"}:
                relations = self.repository.read_json("state/martial-world/faction-relations.json")
                if treaty_forbids_hostilities(relations, actor_faction, target_faction):
                    raise CommandRejectedError("jianghu_institutional_hostility_blocked_by_treaty")
            at = datetime.fromisoformat(now_iso)
            try:
                plan = travel_plan(world_seed=str(meta.get("world_seed") or "jianghu-world"), start_at=at, start=source_place, end=target_place, mode="foot") if source_place != target_place else {"edges": [], "travel_hours": 2.0, "arrival_at": (at + timedelta(hours=2)).isoformat()}
            except (KeyError, ValueError) as exc:
                raise CommandRejectedError("jianghu_institutional_route_unresolved") from exc
            try:
                inventory = self.repository.read_json(inventory_path(actor_faction))
            except FileNotFoundError:
                inventory = {}
            travel_days = max(1, int((float(plan.get("travel_hours", 0.0)) + 23) // 24))
            required_food = len(member_refs) * (travel_days + 1)
            if max(0, int(inventory.get("food_ration_days", 0))) < required_food:
                raise CommandRejectedError("jianghu_institutional_travel_provisions_insufficient")
            deployments = copy.deepcopy(self.repository.read_json(_DEPLOYMENTS))
            dep_rows = deployments.setdefault("deployments", {})
            physical_ref = f"operation:institutional:{op_ref}"
            if not isinstance(dep_rows, dict) or physical_ref in dep_rows:
                raise CommandRejectedError("jianghu_institutional_physical_operation_exists")
            commitments = derived_commitment_state(self.repository.read_json)
            try:
                reserve_resources(
                    commitments,
                    resources=[("person", ref, actor_faction) for ref in member_refs],
                    actor_ref=commander_ref, owner_ref=actor_faction, activity_ref=physical_ref,
                    activity_kind=str(row.get("operation_kind") or "institutional_operation"),
                    started_at=now_iso, location_ref=source_place,
                )
            except ValueError as exc:
                raise CommandRejectedError("jianghu_institutional_members_unavailable") from exc
            dep = {
                "faction_ref": actor_faction,
                "target_faction_ref": target_faction,
                "operation_kind": str(row.get("operation_kind") or ""),
                "participant_refs": member_refs,
                "commander_ref": commander_ref,
                "source_place_ref": source_place,
                "source_site_ref": source_site,
                "target_place_ref": target_place,
                "target_site_ref": target_site,
                "started_at": now_iso,
                "departure_at": now_iso,
                "arrival_at": str(plan.get("arrival_at") or ""),
                "travel_hours": float(plan.get("travel_hours", 0.0)),
                "route_refs": list(plan.get("edges", [])),
                "status": "mobilizing",
                "arrival_event_kind": "faction_operation_arrival",
                "operation_intent": str(row.get("objective") or "institutional_mission")[:300],
                "targeting_intent": "lethal" if mission_kind == "war_strike" else "disable",
                "mobilization_basis": "house_council_exact_roster",
                "institutional_operation_ref": op_ref,
            }
            if row.get("custody_ref"):
                dep["custody_ref"] = row["custody_ref"]
                dep["captive_ref"] = str(row.get("target_person_ref") or "")
            dep_rows[physical_ref] = dep
            schedule = copy.deepcopy(self.repository.read_json(_SCHEDULE))
            schedule = upsert_one_off_event(schedule, {
                "event_id": f"operation_departure:{physical_ref}", "kind": "faction_operation_departure",
                "due_at": now_iso, "owner_ref": physical_ref, "direction": "outbound",
                "arrival_event_kind": "faction_operation_arrival", "requires_player_decision": False,
            })
            row["physical_operation_ref"] = physical_ref
            row["phase"] = "dispatched"
            row["dispatched_at"] = now_iso
            row["updated_at"] = now_iso
            row["estimated_service_days"] = max(1, int((float(plan.get("travel_hours", 2.0)) * 2 + 23) // 24) + 1)
            active[op_ref] = row
            pause_fpath, paused_faction, pause_rpath, paused_roster = self._pause_institutional_training_now(member_refs, current_time)
            return self._simple_plan(
                command, meta, current_time,
                writes_records={OPERATIONS_PATH: state, _DEPLOYMENTS: deployments, _SCHEDULE: schedule, pause_fpath: paused_faction, pause_rpath: paused_roster},
                code="jianghu_institutional_operation_dispatched",
                result={"command_type": command.command_type, "operation_ref": op_ref, "physical_operation_ref": physical_ref, "commander_ref": commander_ref, "member_count": len(member_refs), "arrival_at": dep["arrival_at"]},
            )

        if action == "request_aid":
            if row.get("phase") != "dispatched" or str(row.get("mission_kind") or "") not in {"raid", "war_strike"}:
                raise CommandRejectedError("jianghu_institutional_aid_requires_dispatched_offensive")
            ally = str(command.payload.get("ally_faction_ref") or "")
            if not ally or ally == actor_faction:
                raise CommandRejectedError("jianghu_institutional_aid_target_invalid")
            relations = copy.deepcopy(self.repository.read_json("state/martial-world/faction-relations.json"))
            if "alliance" not in active_treaty_kinds(relations, actor_faction, ally):
                raise CommandRejectedError("jianghu_institutional_aid_no_applicable_treaty")
            requests = row.setdefault("aid_requests", {})
            if not isinstance(requests, dict):
                requests = {}; row["aid_requests"] = requests
            if ally in requests:
                raise CommandRejectedError("jianghu_institutional_aid_already_requested")
            physical_ref = str(row.get("physical_operation_ref") or "")
            deployments = copy.deepcopy(self.repository.read_json(_DEPLOYMENTS))
            dep_rows = deployments.get("deployments", {}) if isinstance(deployments, Mapping) else {}
            parent = dep_rows.get(physical_ref) if isinstance(dep_rows, Mapping) else None
            if not isinstance(parent, Mapping):
                raise CommandRejectedError("jianghu_institutional_physical_operation_unresolved")
            schedule = copy.deepcopy(self.repository.read_json(_SCHEDULE))
            writes: dict[str, Any] = {}
            deployments, schedule, response = stage_offensive_support_request(
                read_json=self.repository.read_json, writes=writes, deployments=deployments, schedule=schedule,
                parent_operation_ref=physical_ref, parent_operation=parent, requester_faction_ref=actor_faction,
                ally_faction_ref=ally, at=datetime.fromisoformat(now_iso), world_seed=str(meta.get("world_seed") or "jianghu-world"),
            )
            requests[ally] = {"requested_at": now_iso, "role": "offensive_support", **copy.deepcopy(response)}
            row["updated_at"] = now_iso
            active[op_ref] = row
            writes[OPERATIONS_PATH] = state; writes[_DEPLOYMENTS] = deployments; writes[_SCHEDULE] = schedule
            return self._simple_plan(command, meta, current_time, writes_records=writes, code="jianghu_institutional_aid_resolved", result={"command_type": command.command_type, "operation_ref": op_ref, "ally_faction_ref": ally, **response})

        if action == "decline_assignment":
            if row.get("mission_source") != "house_assignment" or row.get("phase") != "offered" or command.actor_id != str(row.get("assignee_ref") or ""):
                raise CommandRejectedError("jianghu_institutional_assignment_not_declineable")
            local_writes = {OPERATIONS_PATH: state}
            closed = close_institutional_operation(read_json=self.repository.read_json, writes=local_writes, operation_ref=op_ref, at_iso=now_iso, success=False, closure_reason="declined_assignment")
            if closed is None:
                raise CommandRejectedError("jianghu_institutional_operation_unresolved")
            return self._simple_plan(command, meta, current_time, writes_records={OPERATIONS_PATH: local_writes[OPERATIONS_PATH]}, code="jianghu_institutional_assignment_declined", result={"command_type": command.command_type, "operation_ref": op_ref})

        if action == "cancel":
            if row.get("phase") in {"dispatched", "in_field", "returning"}:
                raise CommandRejectedError("jianghu_institutional_in_field_cannot_paper_cancel")
            if not (_authorized_for(actor, str(row.get("mission_kind") or "")) or command.actor_id in {str(row.get("issuer_ref") or ""), str(row.get("assignee_ref") or "")}):
                raise CommandRejectedError("jianghu_institutional_cancel_unauthorized")
            local_writes = {OPERATIONS_PATH: state}
            if close_institutional_operation(read_json=self.repository.read_json, writes=local_writes, operation_ref=op_ref, at_iso=now_iso, success=False, closure_reason="cancelled") is None:
                raise CommandRejectedError("jianghu_institutional_operation_unresolved")
            return self._simple_plan(command, meta, current_time, writes_records={OPERATIONS_PATH: local_writes[OPERATIONS_PATH]}, code="jianghu_institutional_operation_cancelled", result={"command_type": command.command_type, "operation_ref": op_ref})


        if action in {"accept_career_offer", "decline_career_offer"}:
            archived = copy.deepcopy(dict(archive.get(op_ref) or {}))
            offer = copy.deepcopy(dict(archived.get("career_offer", {}))) if isinstance(archived.get("career_offer"), Mapping) else {}
            if offer.get("status") != "offered" or str(offer.get("candidate_ref") or "") != command.actor_id:
                raise CommandRejectedError("jianghu_institutional_career_offer_not_open")
            if action == "decline_career_offer":
                offer["status"] = "declined"; offer["resolved_at"] = now_iso
                archived["career_offer"] = offer; archive[op_ref] = archived
                return self._simple_plan(command, meta, current_time, writes_records={OPERATIONS_PATH: state}, code="jianghu_institutional_career_offer_declined", result={"command_type": command.command_type, "operation_ref": op_ref, "to_grade": offer.get("to_grade")})
            current_grade = str(actor.get("membership_grade") or "probationary")
            if current_grade != str(offer.get("from_grade") or ""):
                raise CommandRejectedError("jianghu_institutional_career_grade_changed")
            target_grade = str(offer.get("to_grade") or "")
            grades = ("probationary", "junior", "full", "senior", "elite", "elder")
            if current_grade not in grades or current_grade == "elder" or target_grade != grades[grades.index(current_grade) + 1]:
                raise CommandRejectedError("jianghu_institutional_career_offer_invalid")
            fpath, faction = read_faction(self.repository, actor_faction)
            training = faction.get("training", {}) if isinstance(faction.get("training"), Mapping) else {}
            keys = tuple(martial_discipline_keys())
            primary = max(keys, key=lambda key: (int(training.get(key, 0)), -keys.index(key))) if keys else "unarmed"
            people = actor_roster.get("people", []) if isinstance(actor_roster.get("people"), list) else []
            living = [p for p in people if isinstance(p, Mapping) and (p.get("health", {}) if isinstance(p.get("health"), Mapping) else {}).get("status") != "dead"]
            elder_cap = max(1, len(living) // 50) if len(living) >= 25 else 0
            elder_count = sum(1 for p in living if str(p.get("membership_grade") or "") == "elder")
            birth = int(actor.get("birth_year", current_time.year)); joined = int(actor.get("joined_year", birth + 16))
            service_days = max(0, (current_time.year - max(birth, joined)) * 365)
            check = grade_eligibility(actor, target_grade=target_grade, service_days=service_days, primary_discipline=primary, discipline_clean=True, elder_open_seat=(elder_count < elder_cap))
            if not bool(check.get("eligible")):
                raise CommandRejectedError("jianghu_institutional_career_no_longer_eligible")
            updated = copy.deepcopy(dict(actor)); updated["membership_grade"] = target_grade
            actor_roster_after = set_roster_person(actor_roster, _actor_ordinal, updated)
            offer["status"] = "accepted"; offer["resolved_at"] = now_iso
            archived["career_offer"] = offer; archive[op_ref] = archived
            return self._simple_plan(command, meta, current_time, writes_records={actor_path: actor_roster_after, OPERATIONS_PATH: state}, code="jianghu_institutional_career_offer_accepted", result={"command_type": command.command_type, "operation_ref": op_ref, "from_grade": current_grade, "to_grade": target_grade, "stat_changes": {}})

        if action == "settle_reward":
            archived = copy.deepcopy(dict(archive.get(op_ref) or {}))
            settlement = archived.get("reward_settlement") if isinstance(archived.get("reward_settlement"), Mapping) else {}
            if settlement.get("status") != "pending":
                raise CommandRejectedError("jianghu_institutional_reward_not_pending")
            amount = max(0, int(settlement.get("authorized_cash", 0) or 0))
            mode = str(settlement.get("mode") or "none")
            if amount <= 0 or mode == "none":
                raise CommandRejectedError("jianghu_institutional_reward_not_due")
            fpath, faction = read_faction(self.repository, actor_faction)
            if int(faction.get("treasury_cash", 0)) < amount:
                raise CommandRejectedError("jianghu_institutional_reward_treasury_insufficient")
            report = archived.get("after_action_report") if isinstance(archived.get("after_action_report"), Mapping) else {}
            returned = [str(x) for x in report.get("returned_refs", []) if isinstance(x, str) and x]
            commander = str(archived.get("commander_ref") or "")
            recipients = [commander] if mode == "commander" and commander else returned
            recipients = list(dict.fromkeys(ref for ref in recipients if ref))
            if not recipients:
                raise CommandRejectedError("jianghu_institutional_reward_no_recipient")
            base, rem = divmod(amount, len(recipients))
            writes: dict[str, Any] = {fpath: faction}
            roster_cache: dict[str, dict[str, Any]] = {}
            paid: dict[str, int] = {}
            for i, ref in enumerate(sorted(recipients)):
                ppath, raw_roster, ordinal, person = self._institutional_person(ref)
                if str(person.get("faction_ref") or "") != actor_faction:
                    continue
                roster = roster_cache.setdefault(ppath, copy.deepcopy(dict(raw_roster)))
                people = roster.get("people", [])
                if not isinstance(people, list) or ordinal >= len(people):
                    raise CommandRejectedError("jianghu_institutional_reward_recipient_unresolved")
                payout = base + (1 if i < rem else 0)
                updated = copy.deepcopy(dict(person)); updated["personal_cash"] = max(0, int(updated.get("personal_cash", 0))) + payout
                roster = set_roster_person(roster, ordinal, updated)
                roster_cache[ppath] = roster; paid[ref] = payout
            faction["treasury_cash"] = int(faction.get("treasury_cash", 0)) - sum(paid.values())
            writes[fpath] = faction
            writes.update(roster_cache)
            settlement = copy.deepcopy(dict(settlement)); settlement["status"] = "settled"; settlement["settled_at"] = now_iso; settlement["paid"] = paid
            archived["reward_settlement"] = settlement; archive[op_ref] = archived; writes[OPERATIONS_PATH] = state
            return self._simple_plan(command, meta, current_time, writes_records=writes, code="jianghu_institutional_reward_settled", result={"command_type": command.command_type, "operation_ref": op_ref, "paid_cash": sum(paid.values()), "recipients": paid})

        if action == "service_review":
            archived = copy.deepcopy(dict(archive.get(op_ref) or {}))
            service = archived.get("service_credit") if isinstance(archived.get("service_credit"), Mapping) else {}
            if service.get("reviewed"):
                raise CommandRejectedError("jianghu_institutional_service_already_reviewed")
            credited = [str(x) for x in service.get("credited_refs", []) if isinstance(x, str) and x]
            days = max(1, int(service.get("service_days", 1) or 1))
            success = bool(service.get("success"))
            writes: dict[str, Any] = {}
            roster_cache: dict[str, dict[str, Any]] = {}
            reviewed: list[str] = []
            for ref in credited:
                ppath, raw_roster, ordinal, person = self._institutional_person(ref)
                if str(person.get("faction_ref") or "") != actor_faction:
                    continue
                roster = roster_cache.setdefault(ppath, copy.deepcopy(dict(raw_roster)))
                people = roster.get("people", [])
                updated = copy.deepcopy(dict(person))
                record = copy.deepcopy(dict(updated.get("institutional_service", {}))) if isinstance(updated.get("institutional_service"), Mapping) else {}
                record["completed_missions"] = max(0, int(record.get("completed_missions", 0))) + 1
                if success:
                    record["successful_missions"] = max(0, int(record.get("successful_missions", 0))) + 1
                record["service_days"] = max(0, int(record.get("service_days", 0))) + days
                if ref == str(archived.get("commander_ref") or ""):
                    record["commands_completed"] = max(0, int(record.get("commands_completed", 0))) + 1
                record["last_review_at"] = now_iso
                updated["institutional_service"] = record
                roster = set_roster_person(roster, ordinal, updated)
                roster_cache[ppath] = roster; reviewed.append(ref)
            writes.update(roster_cache)
            service = copy.deepcopy(dict(service)); service["reviewed"] = True; service["reviewed_at"] = now_iso
            archived["service_credit"] = service; archive[op_ref] = archived; writes[OPERATIONS_PATH] = state
            return self._simple_plan(command, meta, current_time, writes_records=writes, code="jianghu_institutional_service_reviewed", result={"command_type": command.command_type, "operation_ref": op_ref, "reviewed_refs": reviewed, "automatic_promotion": False})

        raise CommandRejectedError("jianghu_institutional_action_invalid")
