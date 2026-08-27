"""Persistent compact House mission dossiers.

This owner records institutional intent, authority, briefing snapshots and compact
closure reports. It never duplicates combat, travel, custody, contract, inventory
or deployment mechanics; those remain authoritative in their own owners.
"""
from __future__ import annotations

import copy
from typing import Any, Callable, Mapping, Sequence

OPERATIONS_PATH = "state/martial-world/institutional-operations.json"


def _state(read_json: Callable[[str], Any], writes: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(writes, Mapping) and isinstance(writes.get(OPERATIONS_PATH), Mapping):
        raw = writes[OPERATIONS_PATH]
    else:
        try:
            raw = read_json(OPERATIONS_PATH)
        except FileNotFoundError:
            raw = {"schema": "jianghu-institutional-operations-state-1.0", "active": {}, "archive": {}}
    out = copy.deepcopy(dict(raw)) if isinstance(raw, Mapping) else {}
    out.setdefault("schema", "jianghu-institutional-operations-state-1.0")
    if not isinstance(out.get("active"), dict):
        out["active"] = {}
    if not isinstance(out.get("archive"), dict):
        out["archive"] = {}
    return out


def stage_institutional_phase(
    *, read_json: Callable[[str], Any], writes: dict[str, Any], operation_ref: str,
    phase: str, at_iso: str, details: Mapping[str, Any] | None = None,
) -> bool:
    state = _state(read_json, writes)
    row = state["active"].get(str(operation_ref))
    if not isinstance(row, Mapping):
        return False
    current = copy.deepcopy(dict(row))
    current["phase"] = str(phase)
    current["updated_at"] = str(at_iso)
    # Hidden opponent information is never copied into the player-facing dossier.
    hidden = {"hidden_enemy_reaction", "defender_response", "defender_alert_milli", "enemy_reinforcement_refs"}
    for key, value in (details or {}).items():
        if key not in hidden:
            current[str(key)] = copy.deepcopy(value)
    state["active"][str(operation_ref)] = current
    writes[OPERATIONS_PATH] = state
    return True


def _living_return_refs(owner: Mapping[str, Any], returned_refs: Sequence[str] | None) -> list[str]:
    assigned = {str(x) for x in owner.get("participant_refs", []) if isinstance(x, str) and x}
    if returned_refs is not None:
        returned = {str(x) for x in returned_refs if isinstance(x, str) and x}
        return sorted(returned & assigned if assigned else returned)
    return sorted(assigned)


def close_institutional_operation(
    *, read_json: Callable[[str], Any], writes: dict[str, Any], operation_ref: str,
    at_iso: str, success: bool, closure_reason: str,
    physical_operation: Mapping[str, Any] | None = None,
    returned_refs: Sequence[str] | None = None,
    casualties: Sequence[str] | None = None,
    missing_refs: Sequence[str] | None = None,
    equipment_recovered: Mapping[str, Any] | None = None,
    equipment_lost_or_consumed: Mapping[str, Any] | None = None,
    extra_report: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    state = _state(read_json, writes)
    row = state["active"].get(str(operation_ref))
    if not isinstance(row, Mapping):
        return None
    owner = copy.deepcopy(dict(row))
    physical = physical_operation if isinstance(physical_operation, Mapping) else {}
    assigned = sorted({str(x) for x in owner.get("participant_refs", []) if isinstance(x, str) and x})
    returned = _living_return_refs(owner, returned_refs)
    casualty_set = sorted({str(x) for x in (casualties or physical.get("casualty_refs", []) or []) if isinstance(x, str) and x})
    missing = sorted({str(x) for x in (missing_refs or []) if isinstance(x, str) and x})
    if not missing and assigned:
        accounted = set(returned) | set(casualty_set)
        missing = sorted(ref for ref in assigned if ref not in accounted)
    report = {
        "reported_at": str(at_iso),
        "closure_reason": str(closure_reason),
        "success": bool(success),
        "mission_kind": str(owner.get("mission_kind") or ""),
        "operation_kind": str(owner.get("operation_kind") or physical.get("operation_kind") or ""),
        "commander_ref": str(owner.get("commander_ref") or physical.get("commander_ref") or ""),
        "assigned_count": len(assigned),
        "returned_count": len(returned),
        "returned_refs": returned,
        "casualty_refs": casualty_set,
        "missing_refs": missing,
    }
    for key in ("battle_outcome", "battle_winner_side", "allied_defender_count", "rescue_success", "rescued_captive_ref"):
        if key in physical:
            report[key] = copy.deepcopy(physical[key])
    cash = max(0, int(physical.get("seized_cash", physical.get("cash_secured", 0)) or 0))
    quantity = max(0, int(physical.get("seized_quantity", physical.get("cargo_secured", 0)) or 0))
    captives = [str(x) for x in physical.get("captive_refs", []) if isinstance(x, str) and x]
    if cash: report["cash_secured"] = cash
    if quantity: report["cargo_secured"] = quantity
    if captives: report["captive_refs"] = sorted(set(captives))
    if equipment_recovered: report["equipment_recovered"] = copy.deepcopy(dict(equipment_recovered))
    if equipment_lost_or_consumed: report["equipment_lost_or_consumed"] = copy.deepcopy(dict(equipment_lost_or_consumed))
    intelligence = owner.get("intelligence_report")
    if isinstance(intelligence, Mapping):
        report["intelligence_report"] = copy.deepcopy(dict(intelligence))
    if max(0, int(owner.get("contract_revenue_cash", 0) or 0)):
        report["contract_revenue_cash"] = max(0, int(owner.get("contract_revenue_cash", 0) or 0))
    if isinstance(extra_report, Mapping):
        for key, value in extra_report.items():
            if key not in {"hidden_enemy_reaction", "defender_response", "defender_alert_milli", "enemy_reinforcement_refs"}:
                report[str(key)] = copy.deepcopy(value)
    reward_cash = max(0, int(owner.get("reward_cash", 0) or 0))
    reward_mode = str(owner.get("reward_mode") or "none")
    owner["phase"] = "closed"
    owner["closed_at"] = str(at_iso)
    owner["outcome"] = "success" if success else str(closure_reason or "failed")
    owner["after_action_report"] = report
    owner["reward_settlement"] = {
        "authorized_cash": reward_cash if success else 0,
        "mode": reward_mode,
        "status": "pending" if success and reward_cash > 0 and reward_mode != "none" else "not_due",
    }
    credited = returned
    owner["service_credit"] = {
        "credited_refs": credited,
        "success": bool(success),
        "service_days": max(1, min(90, int(owner.get("estimated_service_days", 1) or 1))),
        "reviewed": False,
    }
    state["active"].pop(str(operation_ref), None)
    state["archive"][str(operation_ref)] = owner
    # Keep bounded consequential history rather than an unbounded event log.
    if len(state["archive"]) > 256:
        ranked = sorted(state["archive"].items(), key=lambda kv: (str(kv[1].get("closed_at") or ""), kv[0]))
        for old_ref, _ in ranked[:len(state["archive"]) - 256]:
            state["archive"].pop(old_ref, None)
    writes[OPERATIONS_PATH] = state
    return owner


def close_linked_contract_operation(
    *, read_json: Callable[[str], Any], writes: dict[str, Any], contract_ref: str,
    at_iso: str, success: bool, closure_reason: str, returned_refs: Sequence[str] | None = None,
    extra_report: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    state = _state(read_json, writes)
    match = next((ref for ref, row in state["active"].items()
                  if isinstance(row, Mapping) and str(row.get("linked_contract_ref") or "") == str(contract_ref)), None)
    if not match:
        return None
    return close_institutional_operation(
        read_json=read_json, writes=writes, operation_ref=str(match), at_iso=at_iso,
        success=success, closure_reason=closure_reason, returned_refs=returned_refs,
        extra_report=extra_report,
    )



def ensure_contract_dossier(
    *, read_json: Callable[[str], Any], writes: dict[str, Any], contract_ref: str,
    faction_ref: str, actor_ref: str, at_iso: str, phase: str,
    participant_refs: Sequence[str] | None = None, commander_ref: str = "",
    objective: str = "", issuer_ref: str = "",
) -> str:
    """Create or advance the one compact institutional dossier for an escort contract."""
    state = _state(read_json, writes)
    match = next((ref for ref, row in state["active"].items()
                  if isinstance(row, Mapping) and str(row.get("linked_contract_ref") or "") == str(contract_ref)), None)
    ref = str(match or f"mission:contract:{contract_ref}")
    if match:
        row = copy.deepcopy(dict(state["active"][ref]))
    else:
        row = {
            "operation_ref": ref, "faction_ref": str(faction_ref), "mission_source": "public_contract",
            "issuer_ref": str(issuer_ref or "public_contract"), "assignee_ref": str(actor_ref),
            "mission_kind": "escort", "objective": str(objective or f"Fulfill escort contract {contract_ref}")[:500],
            "linked_contract_ref": str(contract_ref), "created_at": str(at_iso),
            "reward_cash": 0, "reward_mode": "none",
        }
    row["phase"] = str(phase); row["updated_at"] = str(at_iso)
    if participant_refs is not None:
        row["participant_refs"] = list(dict.fromkeys(str(x) for x in participant_refs if isinstance(x, str) and x))
    if commander_ref:
        row["commander_ref"] = str(commander_ref)
    state["active"][ref] = row
    writes[OPERATIONS_PATH] = state
    return ref



def stage_house_assignment_offer(
    *, read_json: Callable[[str], Any], writes: dict[str, Any], operation_ref: str,
    faction_ref: str, issuer_ref: str, assignee_ref: str, mission_kind: str,
    objective: str, at_iso: str, target_faction_ref: str = "", target_site_ref: str = "",
    target_person_ref: str = "", linked_contract_ref: str = "", reward_cash: int = 0, reward_mode: str = "commander",
    briefing: Mapping[str, Any] | None = None, trigger_ref: str = "",
) -> bool:
    """Create one lawful House-originated offer without spoofing an NPC command.

    The scheduler/institutional review is the issuer of the state transition; the
    named NPC is the in-world authority recorded in the dossier. Player agency is
    preserved because the operation remains ``offered`` until accepted/declined.
    """
    state = _state(read_json, writes)
    if operation_ref in state["active"] or operation_ref in state["archive"]:
        return False
    # Keep only one unresolved House assignment for one assignee at a time.
    if any(
        isinstance(row, Mapping) and row.get("mission_source") == "house_assignment"
        and str(row.get("assignee_ref") or "") == str(assignee_ref)
        and str(row.get("phase") or "") not in {"closed", "declined", "cancelled"}
        for row in state["active"].values()
    ):
        return False
    row: dict[str, Any] = {
        "operation_ref": str(operation_ref), "faction_ref": str(faction_ref),
        "mission_source": "house_assignment", "issuer_ref": str(issuer_ref),
        "assignee_ref": str(assignee_ref), "mission_kind": str(mission_kind),
        "objective": str(objective)[:500], "phase": "offered",
        "created_at": str(at_iso), "updated_at": str(at_iso),
        "reward_cash": max(0, int(reward_cash)),
        "reward_mode": str(reward_mode if reward_cash > 0 else "none"),
    }
    optional = {
        "target_faction_ref": target_faction_ref, "target_site_ref": target_site_ref,
        "target_person_ref": target_person_ref, "linked_contract_ref": linked_contract_ref, "trigger_ref": trigger_ref,
    }
    for key, value in optional.items():
        if value:
            row[key] = str(value)
    if isinstance(briefing, Mapping):
        row["briefing"] = copy.deepcopy(dict(briefing))
    state["active"][str(operation_ref)] = row
    writes[OPERATIONS_PATH] = state
    return True


def stage_linked_contract_phase(
    *, read_json: Callable[[str], Any], writes: dict[str, Any], contract_ref: str,
    phase: str, at_iso: str, details: Mapping[str, Any] | None = None,
) -> bool:
    """Advance the active dossier linked to a funded contract, if one exists."""
    state = _state(read_json, writes)
    match = next((ref for ref, row in state["active"].items()
                  if isinstance(row, Mapping) and str(row.get("linked_contract_ref") or "") == str(contract_ref)), None)
    if not match:
        return False
    return stage_institutional_phase(
        read_json=read_json, writes=writes, operation_ref=str(match), phase=phase,
        at_iso=at_iso, details=details,
    )

# Export is repeated intentionally at module end so new helpers remain public.
__all__ = ["OPERATIONS_PATH", "stage_institutional_phase", "close_institutional_operation", "close_linked_contract_operation", "ensure_contract_dossier", "stage_house_assignment_offer", "stage_linked_contract_phase"]
