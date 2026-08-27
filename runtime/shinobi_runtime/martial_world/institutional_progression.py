"""World-side House mission offers, settlement and service recognition.

This module is deliberately orchestration-only. It never resolves travel,
combat, custody or diplomacy itself; it reacts to those owners' current
results and keeps the institutional mission layer closed end to end.
"""
from __future__ import annotations

import copy
import hashlib
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from shinobi_runtime.api.contract_visibility import contract_is_player_visible
from .faction_relations import conflict_stage
from .faction_state import compact_faction_state, read_faction
from .handoffs import classify_handoff
from .agency import office_roots
from .institutional_operations import (
    OPERATIONS_PATH, close_linked_contract_operation, stage_house_assignment_offer,
)
from .live_state import roster_person, set_roster_person
from .membership import grade_eligibility
from .character_rules import martial_discipline_keys
from .strategic_autonomy import stable_permille

_META = "state/meta.json"
_RELATIONS = "state/martial-world/faction-relations.json"
_CONTRACTS = "state/martial-world/contracts/index.json"


class _View:
    def __init__(self, read_json: Callable[[str], Any], writes: Mapping[str, Any]):
        self._read_json = read_json
        self._writes = writes

    def read_json(self, path: str) -> Any:
        if path in self._writes:
            return copy.deepcopy(self._writes[path])
        return copy.deepcopy(self._read_json(path))


def _operations(view: _View) -> dict[str, Any]:
    try:
        raw = view.read_json(OPERATIONS_PATH)
    except FileNotFoundError:
        raw = {"schema": "jianghu-institutional-operations-state-1.0", "active": {}, "archive": {}}
    out = copy.deepcopy(dict(raw)) if isinstance(raw, Mapping) else {}
    out.setdefault("schema", "jianghu-institutional-operations-state-1.0")
    out.setdefault("active", {})
    out.setdefault("archive", {})
    return out


def _player_context(view: _View) -> tuple[str, str, Mapping[str, Any]]:
    try:
        meta = view.read_json(_META)
        player_ref = str(meta.get("player_id") or "") if isinstance(meta, Mapping) else ""
        if not player_ref:
            return "", "", {}
        _path, _roster, _ordinal, player = roster_person(view, player_ref)
        return player_ref, str(player.get("faction_ref") or ""), player
    except (FileNotFoundError, KeyError, TypeError, ValueError):
        return "", "", {}


def _issuer_for(view: _View, faction_ref: str, mission_kind: str, player_ref: str) -> str:
    try:
        _fpath, faction = read_faction(view, faction_ref)
        roster = view.read_json(f"state/martial-world/people/{faction_ref}.json")
    except (FileNotFoundError, KeyError, TypeError, ValueError):
        return ""
    people = roster.get("people", []) if isinstance(roster, Mapping) else []
    if not isinstance(people, list):
        return ""
    high = mission_kind in {"raid", "war_strike", "reinforcement"}
    ranked: list[tuple[int, str]] = []
    for person in people:
        if not isinstance(person, Mapping):
            continue
        ref = str(person.get("person_id") or "")
        if not ref or ref == player_ref:
            continue
        health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
        if health.get("status") == "dead" or int(health.get("consciousness", 100)) <= 0:
            continue
        offices = office_roots(person)
        if high:
            if not offices & {"leader", "deputy_leader"}:
                continue
        elif not offices & {"leader", "deputy_leader", "field_commander", "deputy_field_commander"}:
            continue
        priority = 0
        if "leader" in offices: priority = 5
        elif "deputy_leader" in offices: priority = 4
        elif "field_commander" in offices: priority = 3
        elif "deputy_field_commander" in offices: priority = 2
        ranked.append((-priority, ref))
    return sorted(ranked)[0][1] if ranked else ""


def _relation_edge(relations: Mapping[str, Any], source: str, target: str) -> Mapping[str, Any]:
    rows = relations.get("edges", []) if isinstance(relations, Mapping) else []
    if not isinstance(rows, list):
        return {}
    return next((row for row in rows if isinstance(row, Mapping) and str(row.get("from_faction") or "") == source and str(row.get("to_faction") or "") == target), {})


def _offer_ref(*parts: str) -> str:
    token = hashlib.sha256("|".join(str(x) for x in parts).encode("utf-8")).hexdigest()[:20]
    return f"mission:house:{token}"


def _open_assignment_for(state: Mapping[str, Any], player_ref: str) -> bool:
    active = state.get("active", {}) if isinstance(state, Mapping) else {}
    return any(
        isinstance(row, Mapping) and row.get("mission_source") == "house_assignment"
        and str(row.get("assignee_ref") or "") == player_ref
        for row in active.values()
    ) if isinstance(active, Mapping) else False


def _append_offer_handoff(handoffs: list[dict[str, Any]], row: Mapping[str, Any]) -> None:
    notice = {
        "kind": "house_assignment_offer",
        "event_id": f"house_assignment_offer:{row.get('operation_ref')}",
        "operation_ref": str(row.get("operation_ref") or ""),
        "issuer_ref": str(row.get("issuer_ref") or ""),
        "mission_kind": str(row.get("mission_kind") or ""),
        "objective": str(row.get("objective") or ""),
        "reward_cash": max(0, int(row.get("reward_cash", 0) or 0)),
        "delivered_to_player": True,
        "requires_player_decision": True,
    }
    handoffs.append({**notice, "handoff": classify_handoff(notice)})


def _stage_offer(
    *, read_json: Callable[[str], Any], writes: dict[str, Any], handoffs: list[dict[str, Any]],
    player_ref: str, faction_ref: str, mission_kind: str, at: datetime, objective: str,
    target_faction_ref: str = "", target_site_ref: str = "", target_person_ref: str = "",
    linked_contract_ref: str = "", trigger_ref: str = "", briefing: Mapping[str, Any] | None = None,
) -> str:
    view = _View(read_json, writes)
    state = _operations(view)
    if _open_assignment_for(state, player_ref):
        return ""
    issuer_ref = _issuer_for(view, faction_ref, mission_kind, player_ref)
    if not issuer_ref:
        return ""
    rewards = {"reconnaissance": 250, "rescue": 600, "raid": 450, "war_strike": 900, "reinforcement": 500}
    try:
        _fp, faction = read_faction(view, faction_ref)
        treasury = max(0, int(faction.get("treasury_cash", 0)))
    except (FileNotFoundError, KeyError, TypeError, ValueError):
        treasury = 0
    reward = min(rewards.get(mission_kind, 0), max(0, treasury // 100))
    op_ref = _offer_ref(faction_ref, player_ref, mission_kind, target_faction_ref, target_person_ref, trigger_ref or at.date().isoformat())
    if not stage_house_assignment_offer(
        read_json=view.read_json, writes=writes, operation_ref=op_ref, faction_ref=faction_ref,
        issuer_ref=issuer_ref, assignee_ref=player_ref, mission_kind=mission_kind,
        objective=objective, at_iso=at.isoformat(), target_faction_ref=target_faction_ref,
        target_site_ref=target_site_ref, target_person_ref=target_person_ref, linked_contract_ref=linked_contract_ref,
        reward_cash=reward, reward_mode="commander", briefing=briefing, trigger_ref=trigger_ref,
    ):
        return ""
    final = _operations(_View(read_json, writes))
    row = final.get("active", {}).get(op_ref) if isinstance(final.get("active"), Mapping) else None
    if isinstance(row, Mapping):
        _append_offer_handoff(handoffs, row)
    return op_ref


def stage_house_assignment_offers(
    *, read_json: Callable[[str], Any], writes: dict[str, Any], handoffs: list[dict[str, Any]],
    reviews: Sequence[Mapping[str, Any]], at: datetime,
) -> list[str]:
    """Turn real House needs into at most one protected player assignment offer."""
    view = _View(read_json, writes)
    player_ref, faction_ref, _player = _player_context(view)
    if not player_ref or not faction_ref:
        return []
    if _open_assignment_for(_operations(view), player_ref):
        return []
    added: list[str] = []

    # 1) Known captivity is the strongest House duty. The autonomy layer marks
    # the need but explicitly does not mobilize the player's faction.
    for review in reviews:
        if not isinstance(review, Mapping) or review.get("kind") != "faction_review" or str(review.get("faction_ref") or "") != faction_ref:
            continue
        actions = review.get("executed_actions", []) if isinstance(review.get("executed_actions"), list) else []
        for action in actions:
            if not isinstance(action, Mapping) or action.get("action") != "respond_known_captivity" or action.get("result") != "player_decision_required":
                continue
            captive = str(action.get("person_ref") or "")
            holder = str(action.get("holder_faction_ref") or "")
            custody_ref = str(action.get("custody_ref") or "")
            ref = _stage_offer(
                read_json=read_json, writes=writes, handoffs=handoffs, player_ref=player_ref,
                faction_ref=faction_ref, mission_kind="rescue", at=at,
                objective=f"Recover House member {captive} from known captivity.",
                target_faction_ref=holder, target_person_ref=captive, trigger_ref=custody_ref,
                briefing={"knowledge_basis": "confirmed_house_custody_report", "captive_ref": captive, "holder_faction_ref": holder},
            )
            if ref:
                return [ref]

    # 2) A detected attack on a defense-treaty partner becomes a hard House
    # decision rather than silently consuming the player's fighters.
    for review in reviews:
        if not isinstance(review, Mapping) or review.get("kind") != "defensive_call_to_arms":
            continue
        if review.get("result") != "player_decision_required" or str(review.get("ally_faction_ref") or "") != faction_ref:
            continue
        defended = str(review.get("defended_faction_ref") or "")
        attacker = str(review.get("attacker_faction_ref") or "")
        attack_ref = str(review.get("attack_ref") or "")
        ref = _stage_offer(
            read_json=read_json, writes=writes, handoffs=handoffs, player_ref=player_ref,
            faction_ref=faction_ref, mission_kind="reinforcement", at=at,
            objective=f"Answer the defense call and reinforce allied faction {defended} against the detected attack.",
            target_faction_ref=defended, trigger_ref=attack_ref,
            briefing={"knowledge_basis": "detected_allied_call_to_arms", "defended_faction_ref": defended, "attacker_faction_ref": attacker},
        )
        if ref:
            return [ref]

    # 3) A real funded escort commission can become a House assignment. The
    # external reward still belongs to the House treasury; the dossier carries
    # only a modest internal service bonus.
    if any(isinstance(r, Mapping) and r.get("kind") == "faction_review" and str(r.get("faction_ref") or "") == faction_ref for r in reviews):
        try:
            contracts = view.read_json(_CONTRACTS)
        except FileNotFoundError:
            contracts = {}
        active_contracts = contracts.get("active", {}) if isinstance(contracts, Mapping) else {}
        candidates: list[tuple[int, str, Mapping[str, Any]]] = []
        if isinstance(active_contracts, Mapping):
            for cref, contract in active_contracts.items():
                if not isinstance(cref, str) or not isinstance(contract, Mapping) or contract.get("status") != "offered" or contract.get("contract_type") != "escort":
                    continue
                if not contract_is_player_visible(contract, player_id=player_ref, faction_ref=faction_ref, world_time=at.isoformat()):
                    continue
                try:
                    if datetime.fromisoformat(str(contract.get("expires_at") or "")) <= at:
                        continue
                except ValueError:
                    continue
                candidates.append((-max(0, int(contract.get("reward_cash", 0))), cref, contract))
        if candidates and stable_permille("house-escort-assignment", faction_ref, at.year, at.month) < 300:
            _reward_sort, cref, contract = sorted(candidates, key=lambda x: (x[0], x[1]))[0]
            objective_row = contract.get("objective", {}) if isinstance(contract.get("objective"), Mapping) else {}
            source = str(objective_row.get("source_place_ref") or "")
            destination = str(objective_row.get("destination_place_ref") or "")
            objective = f"Take House responsibility for funded escort commission {cref} from {source} to {destination}."
            ref = _stage_offer(
                read_json=read_json, writes=writes, handoffs=handoffs, player_ref=player_ref,
                faction_ref=faction_ref, mission_kind="escort", at=at, objective=objective,
                linked_contract_ref=cref, trigger_ref=cref,
                briefing={
                    "knowledge_basis": "public_funded_contract", "contract_ref": cref,
                    "source_place_ref": source, "destination_place_ref": destination,
                    "contract_reward_cash": max(0, int(contract.get("reward_cash", 0))),
                    "expires_at": str(contract.get("expires_at") or ""),
                },
            )
            if ref:
                return [ref]

    # 4) Routine strategic work is sparse and relation-driven. It creates a
    # proposal-worthy House assignment, not random quest spam.
    if not any(isinstance(r, Mapping) and r.get("kind") == "faction_review" and str(r.get("faction_ref") or "") == faction_ref for r in reviews):
        return []
    try:
        relations = view.read_json(_RELATIONS)
    except FileNotFoundError:
        return []
    edges = relations.get("edges", []) if isinstance(relations, Mapping) else []
    candidates: list[tuple[int, str, Mapping[str, Any]]] = []
    if isinstance(edges, list):
        for edge in edges:
            if not isinstance(edge, Mapping) or str(edge.get("from_faction") or "") != faction_ref:
                continue
            target = str(edge.get("to_faction") or "")
            hostility = max(0, int(edge.get("hostility", 0)))
            if target and target != faction_ref and hostility >= 35:
                candidates.append((-hostility, target, edge))
    if not candidates:
        return []
    _neg, target, edge = sorted(candidates, key=lambda x: (x[0], x[1]))[0]
    hostility = -_neg
    stage = conflict_stage(edge)
    roll = stable_permille("house-assignment-review", faction_ref, target, at.year, at.month)
    if roll >= 420:
        return []
    if stage == "war" or hostility >= 80:
        mission_kind = "war_strike"
        objective = f"Plan and execute a bounded House strike against hostile faction {target}."
    elif hostility >= 60 and roll < 180:
        mission_kind = "raid"
        objective = f"Conduct a limited punitive operation against hostile faction {target}."
    else:
        mission_kind = "reconnaissance"
        objective = f"Reconnoiter hostile faction {target} and return with an operational report."
    try:
        _tfp, target_faction = read_faction(view, target)
        target_site = str(target_faction.get("local_site_ref") or "")
    except (FileNotFoundError, KeyError, TypeError, ValueError):
        target_site = ""
    ref = _stage_offer(
        read_json=read_json, writes=writes, handoffs=handoffs, player_ref=player_ref,
        faction_ref=faction_ref, mission_kind=mission_kind, at=at, objective=objective,
        target_faction_ref=target, target_site_ref=target_site,
        trigger_ref=f"relation-review:{at.year:04d}-{at.month:02d}:{target}",
        briefing={"knowledge_basis": "house_relation_records", "target_faction_ref": target, "hostility": hostility, "conflict_stage": stage},
    )
    if ref:
        added.append(ref)
    return added



def close_expired_contract_dossiers(
    *, read_json: Callable[[str], Any], writes: dict[str, Any], handoffs: list[dict[str, Any]],
    reviews: Sequence[Mapping[str, Any]], at: datetime,
) -> list[str]:
    """Close institutional dossiers whose funded contract expired this frontier.

    The contract owner remains authoritative for escrow/refund. This function only
    closes the linked House/public mission record so accepted-but-never-started or
    offered House escort assignments cannot become orphaned dossiers.
    """
    closed: list[str] = []
    seen: set[str] = set()
    for review in reviews:
        if not isinstance(review, Mapping) or str(review.get("kind") or "") != "contract_expiry":
            continue
        refs = review.get("contract_refs", []) if isinstance(review.get("contract_refs"), list) else []
        for contract_ref in refs:
            if not isinstance(contract_ref, str) or not contract_ref or contract_ref in seen:
                continue
            seen.add(contract_ref)
            owner = close_linked_contract_operation(
                read_json=read_json, writes=writes, contract_ref=contract_ref,
                at_iso=at.isoformat(), success=False, closure_reason="contract_expired",
                extra_report={"contract_ref": contract_ref},
            )
            if not isinstance(owner, Mapping):
                continue
            op_ref = str(owner.get("operation_ref") or "")
            if op_ref:
                closed.append(op_ref)
            notice = {
                "kind": "institutional_mission_closed",
                "event_id": f"institutional_mission_closed:{op_ref}:{at.isoformat()}",
                "operation_ref": op_ref,
                "success": False,
                "closure_reason": "contract_expired",
                "delivered_to_player": True,
                "requires_player_decision": False,
            }
            handoffs.append({**notice, "handoff": classify_handoff(notice)})
    return closed


_GRADES = ("probationary", "junior", "full", "senior", "elite", "elder")

def _chronological_service_days(person: Mapping[str, Any], year: int) -> int:
    birth = int(person.get("birth_year", year))
    joined = int(person.get("joined_year", birth + 16))
    return max(0, (int(year) - max(birth, joined)) * 365)

def _primary_discipline(faction: Mapping[str, Any]) -> str:
    training = faction.get("training", {}) if isinstance(faction.get("training"), Mapping) else {}
    keys = tuple(martial_discipline_keys())
    return max(keys, key=lambda key: (int(training.get(key, 0)), -keys.index(key))) if keys else "unarmed"

def _stage_player_grade_offer(
    *, view: _View, state: dict[str, Any], row: dict[str, Any], player_ref: str,
    faction_ref: str, at: datetime, handoffs: list[dict[str, Any]],
) -> bool:
    """Stage a protected one-grade promotion offer when existing rules permit it.

    Mission service only triggers the review. Eligibility remains the established
    chronological/capability rule, and accepting a grade never grants stats.
    """
    if not player_ref or player_ref not in set(row.get("service_credit", {}).get("credited_refs", [])):
        return False
    for archived in state.get("archive", {}).values():
        if not isinstance(archived, Mapping):
            continue
        offer = archived.get("career_offer") if isinstance(archived.get("career_offer"), Mapping) else {}
        if str(offer.get("candidate_ref") or "") == player_ref and str(offer.get("status") or "") == "offered":
            return False
    try:
        _ppath, roster, _ordinal, player = roster_person(view, player_ref)
        _fpath, faction = read_faction(view, faction_ref)
    except (FileNotFoundError, KeyError, TypeError, ValueError):
        return False
    current = str(player.get("membership_grade") or "probationary")
    if current not in _GRADES or current == "elder":
        return False
    target = _GRADES[_GRADES.index(current) + 1]
    people = roster.get("people", []) if isinstance(roster.get("people"), list) else []
    living = [p for p in people if isinstance(p, Mapping) and (p.get("health", {}) if isinstance(p.get("health"), Mapping) else {}).get("status") != "dead"]
    elder_cap = max(1, len(living) // 50) if len(living) >= 25 else 0
    elder_count = sum(1 for p in living if str(p.get("membership_grade") or "") == "elder")
    check = grade_eligibility(
        player, target_grade=target, service_days=_chronological_service_days(player, at.year),
        primary_discipline=_primary_discipline(faction), discipline_clean=True,
        elder_open_seat=(elder_count < elder_cap),
    )
    if not bool(check.get("eligible")):
        return False
    authority_ref = _issuer_for(view, faction_ref, "war_strike", player_ref)
    if not authority_ref:
        return False
    row["career_offer"] = {
        "kind": "membership_grade", "candidate_ref": player_ref,
        "from_grade": current, "to_grade": target, "status": "offered",
        "offered_at": at.isoformat(), "authorized_by_ref": authority_ref,
        "stat_changes": {},
    }
    notice = {
        "kind": "house_career_offer",
        "event_id": f"house_career_offer:{row.get('operation_ref')}:{target}:{at.isoformat()}",
        "operation_ref": str(row.get("operation_ref") or ""),
        "candidate_ref": player_ref, "from_grade": current, "to_grade": target,
        "authorized_by_ref": authority_ref, "delivered_to_player": True,
        "requires_player_decision": True,
    }
    handoffs.append({**notice, "handoff": classify_handoff(notice)})
    return True

def settle_closed_mission_records(
    *, read_json: Callable[[str], Any], writes: dict[str, Any], handoffs: list[dict[str, Any]], at: datetime,
) -> list[dict[str, Any]]:
    """Pay authorized rewards and post service credit exactly once after closure."""
    view = _View(read_json, writes)
    state = _operations(view)
    archive = state.get("archive", {})
    if not isinstance(archive, dict):
        return []
    player_ref, player_faction, _player = _player_context(view)
    settled: list[dict[str, Any]] = []
    state_changed = False

    for op_ref in sorted(list(archive)):
        raw = archive.get(op_ref)
        if not isinstance(raw, Mapping):
            continue
        row = copy.deepcopy(dict(raw))
        faction_ref = str(row.get("faction_ref") or "")
        if not faction_ref:
            continue
        report = row.get("after_action_report") if isinstance(row.get("after_action_report"), Mapping) else {}
        operation_result: dict[str, Any] = {"operation_ref": op_ref}

        # Reward is a real treasury transfer. If the House cannot currently pay,
        # leave it pending for a later frontier rather than minting money.
        reward = copy.deepcopy(dict(row.get("reward_settlement", {}))) if isinstance(row.get("reward_settlement"), Mapping) else {}
        if reward.get("status") == "pending":
            amount = max(0, int(reward.get("authorized_cash", 0) or 0))
            mode = str(reward.get("mode") or "none")
            try:
                fpath, faction = read_faction(view, faction_ref)
            except (FileNotFoundError, KeyError, TypeError, ValueError):
                faction = {}; fpath = ""
            returned = [str(x) for x in report.get("returned_refs", []) if isinstance(x, str) and x]
            commander = str(row.get("commander_ref") or "")
            recipients = [commander] if mode == "commander" and commander in returned else (returned if mode == "equal_returned" else [])
            recipients = sorted(dict.fromkeys(recipients))
            if amount > 0 and recipients and fpath and max(0, int(faction.get("treasury_cash", 0))) >= amount:
                base, rem = divmod(amount, len(recipients))
                paid: dict[str, int] = {}
                for idx, ref in enumerate(recipients):
                    try:
                        ppath, roster, ordinal, person = roster_person(view, ref)
                    except (FileNotFoundError, KeyError, TypeError, ValueError):
                        continue
                    if str(person.get("faction_ref") or "") != faction_ref:
                        continue
                    payout = base + (1 if idx < rem else 0)
                    updated = copy.deepcopy(dict(person))
                    updated["personal_cash"] = max(0, int(updated.get("personal_cash", 0))) + payout
                    writes[ppath] = set_roster_person(roster, ordinal, updated)
                    paid[ref] = payout
                    view = _View(read_json, writes)
                actual = sum(paid.values())
                if actual > 0:
                    faction["treasury_cash"] = max(0, int(faction.get("treasury_cash", 0))) - actual
                    writes[fpath] = compact_faction_state(faction)
                    reward["status"] = "settled"; reward["settled_at"] = at.isoformat(); reward["paid"] = paid
                    row["reward_settlement"] = reward
                    operation_result["paid_cash"] = actual
                    operation_result["paid_refs"] = sorted(paid)
                    state_changed = True
                    view = _View(read_json, writes)

        service = copy.deepcopy(dict(row.get("service_credit", {}))) if isinstance(row.get("service_credit"), Mapping) else {}
        if not bool(service.get("reviewed")):
            credited = [str(x) for x in service.get("credited_refs", []) if isinstance(x, str) and x]
            days = max(1, int(service.get("service_days", 1) or 1))
            success = bool(service.get("success"))
            reviewed_refs: list[str] = []
            for ref in credited:
                try:
                    ppath, roster, ordinal, person = roster_person(view, ref)
                except (FileNotFoundError, KeyError, TypeError, ValueError):
                    continue
                if str(person.get("faction_ref") or "") != faction_ref:
                    continue
                updated = copy.deepcopy(dict(person))
                record = copy.deepcopy(dict(updated.get("institutional_service", {}))) if isinstance(updated.get("institutional_service"), Mapping) else {}
                record["completed_missions"] = max(0, int(record.get("completed_missions", 0))) + 1
                if success:
                    record["successful_missions"] = max(0, int(record.get("successful_missions", 0))) + 1
                record["service_days"] = max(0, int(record.get("service_days", 0))) + days
                if ref == str(row.get("commander_ref") or ""):
                    record["commands_completed"] = max(0, int(record.get("commands_completed", 0))) + 1
                record["last_review_at"] = at.isoformat()
                updated["institutional_service"] = record
                writes[ppath] = set_roster_person(roster, ordinal, updated)
                reviewed_refs.append(ref)
                view = _View(read_json, writes)
            service["reviewed"] = True; service["reviewed_at"] = at.isoformat()
            row["service_credit"] = service
            operation_result["service_refs"] = reviewed_refs
            state_changed = True

        if player_ref and faction_ref == player_faction and _stage_player_grade_offer(
            view=_View(read_json, writes), state=state, row=row, player_ref=player_ref,
            faction_ref=faction_ref, at=at, handoffs=handoffs,
        ):
            operation_result["career_offer"] = copy.deepcopy(row.get("career_offer"))
            state_changed = True
        archive[op_ref] = row
        if len(operation_result) > 1:
            settled.append(operation_result)
            if faction_ref == player_faction or player_ref in operation_result.get("service_refs", []) or player_ref in operation_result.get("paid_refs", []):
                notice = {
                    "kind": "institutional_mission_settled",
                    "event_id": f"institutional_mission_settled:{op_ref}:{at.isoformat()}",
                    "operation_ref": op_ref,
                    "success": bool(report.get("success")),
                    "paid_cash": max(0, int(operation_result.get("paid_cash", 0))),
                    "delivered_to_player": True,
                    "requires_player_decision": False,
                }
                handoffs.append({**notice, "handoff": classify_handoff(notice)})

    if state_changed:
        state["archive"] = archive
        writes[OPERATIONS_PATH] = state
    return settled


__all__ = ["stage_house_assignment_offers", "close_expired_contract_dossiers", "settle_closed_mission_records"]
