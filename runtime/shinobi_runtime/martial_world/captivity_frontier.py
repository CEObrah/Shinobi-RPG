"""Captivity, ransom, rescue-response and released-captive travel frontier.

Custody is one authoritative owner. This reducer reacts only to information that
has physically reached a plausible responder, transfers real money for ransom,
and moves released captives through normal finite commitments rather than
teleporting them home.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Sequence

from .captivity_lifecycle import close_kin_refs, family_household_faction, should_pay_ransom
from .commitments import release_resources, reserve_resources
from .crime_custody import mark_custody_informed
from .escort_living_world import principal_ransom_value_cash
from .handoffs import classify_handoff
from .repatriation import build_repatriation_operation, repatriation_travel_fit
from .strategic_autonomy import stable_permille
from .travel import travel_plan

_CUSTODY_PATH = "state/martial-world/custody.json"
_DEPLOYMENTS_PATH = "state/martial-world/deployments.json"


def settle_captivity_frontier(
    *,
    events: Sequence[Mapping[str, Any]],
    at: datetime,
    world_seed: str,
    family_state: Mapping[str, Any],
    custody_state: dict[str, Any],
    deployments_state: dict[str, Any],
    writes: dict[str, Any],
    reviews: list[dict[str, Any]],
    handoffs: list[dict[str, Any]],
    pending_one_off_events: list[dict[str, Any]],
    load_faction: Callable[[str], tuple[str, dict[str, Any]]],
    load_person_ref: Callable[[str], tuple[str, str, dict[str, Any], int, dict[str, Any]]],
    start_custody_rescue_operation: Callable[[str, Mapping[str, Any]], dict[str, Any]],
    apply_directed_relation_event: Callable[[str, str, str], None],
    faction_cache: dict[str, tuple[str, dict[str, Any]]],
    get_commitments_state: Callable[[], Mapping[str, Any]],
    set_commitments_state: Callable[[Mapping[str, Any]], None],
) -> None:
    at_iso = at.isoformat()

    def active_custody(custody_id: str, *, person_ref: str = "") -> dict[str, Any] | None:
        return next((
            row for row in custody_state.get("records", [])
            if isinstance(row, dict)
            and (not custody_id or str(row.get("custody_id") or "") == custody_id)
            and (not person_ref or str(row.get("person_ref") or "") == person_ref)
            and row.get("status") not in {"released", "escaped", "rescued", "executed"}
        ), None)

    def ransom_repatriation(
        responder_fid: str, record: Mapping[str, Any], *, demand_cash: int,
    ) -> dict[str, Any]:
        if responder_fid == "house_tang":
            return {"result": "player_ransom_decision_protected"}
        holder_fid = str(record.get("holder_faction_ref") or "")
        captive_ref = str(record.get("person_ref") or "")
        custody_id = str(record.get("custody_id") or "")
        demand = max(0, int(demand_cash))
        if not responder_fid or not holder_fid or responder_fid == holder_fid or not captive_ref or demand <= 0:
            return {"result": "ransom_invalid"}
        try:
            rfpath, responder = load_faction(responder_fid)
            hfpath, holder = load_faction(holder_fid)
            captive_owner_fid, _ppath, _powner, _ordinal, captive = load_person_ref(captive_ref)
        except (KeyError, FileNotFoundError, TypeError, ValueError):
            return {"result": "ransom_owner_unresolved"}
        treasury = max(0, int(responder.get("treasury_cash", 0)))
        autonomy = responder.get("autonomy_policy", {}) if isinstance(responder.get("autonomy_policy"), Mapping) else {}
        risk = max(0, min(100, int(autonomy.get("risk_tolerance", 50))))
        kin_refs = close_kin_refs(family_state, captive_ref)
        value = principal_ransom_value_cash(captive)
        if not should_pay_ransom(
            captive_value_cash=value,
            ransom_cash=demand,
            treasury_cash=treasury,
            close_kin_count=len(kin_refs),
            risk_tolerance=risk,
        ):
            return {"result": "ransom_rejected"}
        # Paying does not magically create transportation. If the released
        # person cannot safely self-travel yet, keep both cash and custody
        # unchanged and revisit after recovery rather than spawning a helpless
        # solo foot traveler.
        if not repatriation_travel_fit(captive):
            pending_one_off_events.append({
                "event_id": f"custody_response_recheck:{custody_id}:{responder_fid}:{at.date().isoformat()}",
                "kind": "custody_response_due",
                "due_at": (at + timedelta(days=1)).isoformat(),
                "owner_ref": custody_id,
                "person_ref": captive_ref,
                "holder_faction_ref": holder_fid,
                "responder_faction_ref": responder_fid,
                "information_source": "ransom_recovery_recheck",
                "requires_player_decision": False,
            })
            return {"result": "ransom_waiting_for_recovery"}

        holder_place = str(holder.get("headquarters") or "")
        responder_home = str(responder.get("headquarters") or "")
        if not holder_place or not responder_home:
            return {"result": "ransom_route_unavailable"}
        # Prove the physical route before transferring money/custody. The actual
        # journey will be materialized by the shared operation-departure reducer.
        if holder_place != responder_home:
            try:
                travel_plan(
                    world_seed=world_seed, start_at=at + timedelta(hours=2),
                    start=holder_place, end=responder_home, mode="foot",
                )
            except (KeyError, ValueError):
                return {"result": "ransom_route_unavailable"}
        try:
            op_ref, repatriation, departure_event = build_repatriation_operation(
                person_ref=captive_ref,
                owner_faction_ref=captive_owner_fid or responder_fid,
                origin_place_ref=holder_place,
                home_place_ref=responder_home,
                at=at,
                cause_ref=custody_id,
                counterparty_faction_ref=holder_fid,
            )
        except ValueError:
            return {"result": "ransom_route_unavailable"}
        deployments = deployments_state.setdefault("deployments", {})
        if not isinstance(deployments, dict):
            raise ValueError("jianghu deployments invalid")
        if op_ref in deployments:
            return {"result": "repatriation_already_active", "operation_ref": op_ref}

        next_commitments = release_resources(get_commitments_state(), activity_ref=custody_id)
        try:
            next_commitments = reserve_resources(
                next_commitments,
                resources=[("person", captive_ref, captive_owner_fid or responder_fid)],
                actor_ref=captive_ref,
                owner_ref=captive_owner_fid or responder_fid,
                activity_ref=op_ref,
                activity_kind="captive_repatriation",
                started_at=at_iso,
                location_ref=holder_place,
            )
        except ValueError:
            return {"result": "repatriation_person_unavailable"}

        responder["treasury_cash"] = treasury - demand
        holder["treasury_cash"] = max(0, int(holder.get("treasury_cash", 0))) + demand
        set_commitments_state(next_commitments)
        custody_state["records"] = [row for row in custody_state.get("records", []) if row is not record]
        deployments[op_ref] = repatriation
        writes[rfpath] = responder
        faction_cache[responder_fid] = (rfpath, responder)
        writes[hfpath] = holder
        faction_cache[holder_fid] = (hfpath, holder)
        writes[_CUSTODY_PATH] = custody_state
        writes[_DEPLOYMENTS_PATH] = deployments_state
        pending_one_off_events.append(departure_event)
        return {"result": "ransom_paid", "cash_paid": demand, "operation_ref": op_ref}

    for event in events:
        if event.get("kind") != "custody_captor_review":
            continue
        custody_id = str(event.get("owner_ref") or "")
        record = active_custody(custody_id, person_ref=str(event.get("person_ref") or ""))
        if not isinstance(record, dict):
            reviews.append({"kind": "custody_captor_review", "event_id": event.get("event_id"), "result": "custody_not_active"})
            continue
        holder_fid = str(record.get("holder_faction_ref") or "")
        try:
            _hfpath, holder = load_faction(holder_fid)
            owner_fid, _ppath, _powner, _ordinal, captive = load_person_ref(str(record.get("person_ref") or ""))
        except (KeyError, FileNotFoundError, TypeError, ValueError):
            reviews.append({"kind": "custody_captor_review", "event_id": event.get("event_id"), "result": "custody_owner_unresolved"})
            continue
        recipient_fid = family_household_faction(family_state, str(record.get("person_ref") or "")) or owner_fid
        if not recipient_fid or recipient_fid == holder_fid:
            reviews.append({"kind": "custody_captor_review", "event_id": event.get("event_id"), "result": "no_ransom_recipient"})
            continue
        if int(record.get("ransom_demand_cash", 0)) > 0:
            reviews.append({"kind": "custody_captor_review", "event_id": event.get("event_id"), "result": "ransom_already_demanded"})
            continue
        base_value = max(1_000, principal_ransom_value_cash(captive))
        holder_policy = holder.get("autonomy_policy", {}) if isinstance(holder.get("autonomy_policy"), Mapping) else {}
        risk = max(0, min(100, int(holder_policy.get("risk_tolerance", 50))))
        roll = stable_permille("custody-ransom-demand", custody_id, holder_fid, at.date().isoformat())
        multiplier_milli = 850 + risk * 3 + roll * 350 // 999
        demand = max(1_000, ((base_value * multiplier_milli // 1000) + 99) // 100 * 100)
        record["ransom_demand_cash"] = demand
        record["ransom_demanded_at"] = at_iso
        record["ransom_recipient_faction_ref"] = recipient_fid
        writes[_CUSTODY_PATH] = custody_state
        holder_place = str(holder.get("headquarters") or "")
        try:
            _rfpath, recipient = load_faction(recipient_fid)
            recipient_place = str(recipient.get("headquarters") or "")
        except (KeyError, FileNotFoundError, TypeError, ValueError):
            recipient_place = ""
        if holder_place and recipient_place:
            if holder_place == recipient_place:
                message_due = at + timedelta(hours=4)
            else:
                try:
                    message_plan = travel_plan(
                        world_seed=world_seed,
                        start_at=at,
                        start=holder_place,
                        end=recipient_place,
                        mode="foot",
                    )
                    message_due = datetime.fromisoformat(str(message_plan.get("arrival_at")))
                except (KeyError, ValueError, TypeError):
                    message_due = at + timedelta(days=3)
            pending_one_off_events.append({
                "event_id": f"custody_response:{custody_id}:{recipient_fid}",
                "kind": "custody_response_due",
                "due_at": message_due.isoformat(),
                "owner_ref": custody_id,
                "person_ref": str(record.get("person_ref") or ""),
                "holder_faction_ref": holder_fid,
                "responder_faction_ref": recipient_fid,
                "information_source": "ransom_message",
                "requires_player_decision": False,
            })
        reviews.append({
            "kind": "custody_captor_review",
            "event_id": event.get("event_id"),
            "person_ref": str(record.get("person_ref") or ""),
            "holder_faction_ref": holder_fid,
            "ransom_demand_cash": demand,
            "result": "ransom_demanded",
        })

    for event in events:
        if event.get("kind") != "custody_response_due":
            continue
        custody_id = str(event.get("owner_ref") or "")
        captive_ref = str(event.get("person_ref") or "")
        holder_fid = str(event.get("holder_faction_ref") or "")
        record = active_custody(custody_id, person_ref=captive_ref)
        if not isinstance(record, dict):
            reviews.append({"kind": "custody_response_due", "event_id": event.get("event_id"), "result": "custody_not_active"})
            continue
        if holder_fid and str(record.get("holder_faction_ref") or "") != holder_fid:
            reviews.append({
                "kind": "custody_response_due",
                "event_id": event.get("event_id"),
                "person_ref": str(record.get("person_ref") or ""),
                "result": "custody_holder_changed",
            })
            continue

        responder_candidates: list[str] = []
        explicit = str(event.get("responder_faction_ref") or record.get("ransom_recipient_faction_ref") or "")
        if explicit:
            responder_candidates.append(explicit)
        person_ref = str(record.get("person_ref") or "")
        family_fid = family_household_faction(family_state, person_ref) or ""
        if family_fid and family_fid not in responder_candidates:
            responder_candidates.append(family_fid)
        try:
            owner_fid, _ppath, _powner, _ordinal, _captive = load_person_ref(person_ref)
        except (KeyError, FileNotFoundError, TypeError, ValueError):
            owner_fid = ""
        if owner_fid and owner_fid not in responder_candidates:
            responder_candidates.append(owner_fid)

        response_rows: list[dict[str, Any]] = []
        dispatched: dict[str, Any] | None = None
        for responder_fid in responder_candidates:
            if not responder_fid or responder_fid == str(record.get("holder_faction_ref") or ""):
                continue
            known_before = {str(x) for x in record.get("informed_faction_refs", []) if isinstance(x, str) and x}
            if responder_fid not in known_before:
                informed = mark_custody_informed(record, faction_ref=responder_fid)
                record.clear()
                record.update(informed)
                apply_directed_relation_event(responder_fid, str(record.get("holder_faction_ref") or ""), "member_abducted")
            response = start_custody_rescue_operation(responder_fid, record)
            response_row = {
                "responder_faction_ref": responder_fid,
                "result": str(response.get("result") or "response_deferred"),
                **({"operation_ref": str(response.get("operation_ref"))} if response.get("operation_ref") else {}),
            }
            response_rows.append(response_row)
            if response.get("result") in {"rescue_dispatched", "rescue_already_active"}:
                dispatched = response
                break
            demand = max(0, int(record.get("ransom_demand_cash", 0)))
            if demand > 0:
                payment = ransom_repatriation(responder_fid, record, demand_cash=demand)
                response_row["ransom_result"] = str(payment.get("result") or "ransom_deferred")
                if payment.get("operation_ref"):
                    response_row["repatriation_operation_ref"] = str(payment.get("operation_ref"))
                if payment.get("result") == "ransom_paid":
                    break
                if payment.get("result") == "player_ransom_decision_protected":
                    notice = {
                        "kind": "ransom_demand_received",
                        "custody_id": custody_id,
                        "captive_ref": person_ref,
                        "holder_faction_ref": str(record.get("holder_faction_ref") or ""),
                        "ransom_cash": demand,
                        "delivered_to_player": True,
                        "requires_player_decision": True,
                    }
                    handoff = classify_handoff(notice)
                    handoffs.append({**notice, "handoff": handoff})
        writes[_CUSTODY_PATH] = custody_state
        first_result = response_rows[0].get("result") if response_rows else "no_responder_known"
        paid_row = next((row for row in response_rows if row.get("ransom_result") == "ransom_paid"), None)
        protected_row = next((row for row in response_rows if row.get("ransom_result") == "player_ransom_decision_protected"), None)
        resolved_result = (
            str(dispatched.get("result")) if dispatched else
            "ransom_paid" if paid_row else
            "player_ransom_decision_required" if protected_row else
            str(first_result)
        )
        reviews.append({
            "kind": "custody_response_due",
            "event_id": event.get("event_id"),
            "person_ref": person_ref,
            "holder_faction_ref": str(record.get("holder_faction_ref") or ""),
            "result": resolved_result,
            **({"operation_ref": str(dispatched.get("operation_ref"))} if dispatched and dispatched.get("operation_ref") else {}),
            "responses": response_rows,
        })


__all__ = ["settle_captivity_frontier"]
