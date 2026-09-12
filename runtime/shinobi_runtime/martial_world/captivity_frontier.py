"""Captivity, ransom, rescue-response and released-captive travel frontier.

Custody is one authoritative owner. This reducer reacts only to information that
has physically reached a plausible responder, transfers real money for ransom,
and moves released captives through normal finite commitments rather than
teleporting them home.
"""
from __future__ import annotations

import copy
import hashlib
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Sequence

from .captivity_lifecycle import family_household_faction
from .commitments import release_resources, reserve_resources
from .crime_custody import mark_custody_informed
from .escort_living_world import principal_ransom_value_cash
from .handoffs import classify_handoff
from .npc_judgment import build_npc_judgment_event, judgment_handoff
from .repatriation import build_repatriation_operation, repatriation_travel_fit
from .travel import travel_plan

_CUSTODY_PATH = "state/martial-world/custody.json"
_DEPLOYMENTS_PATH = "state/martial-world/deployments.json"


def settle_captivity_frontier(
    *,
    events: Sequence[Mapping[str, Any]],
    at: datetime,
    world_seed: str,
    player_ref: str,
    family_state: Mapping[str, Any],
    custody_state: dict[str, Any],
    deployments_state: dict[str, Any],
    writes: dict[str, Any],
    reviews: list[dict[str, Any]],
    handoffs: list[dict[str, Any]],
    pending_one_off_events: list[dict[str, Any]],
    load_faction: Callable[[str], tuple[str, dict[str, Any]]],
    load_person_ref: Callable[[str], tuple[str, str, dict[str, Any], int, dict[str, Any]]],
    start_custody_rescue_operation: Callable[[str, Mapping[str, Any], Mapping[str, Any]], dict[str, Any]],
    apply_directed_relation_event: Callable[[str, str, str], None],
    faction_cache: dict[str, tuple[str, dict[str, Any]]],
    get_commitments_state: Callable[[], Mapping[str, Any]],
    set_commitments_state: Callable[[Mapping[str, Any]], None],
) -> None:
    at_iso = at.isoformat()
    try:
        player_faction_ref = str(load_person_ref(player_ref)[0] or "") if player_ref else ""
    except (KeyError, FileNotFoundError, TypeError, ValueError):
        player_faction_ref = ""

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
        if player_faction_ref and responder_fid == player_faction_ref:
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
        if treasury < demand:
            return {"result": "ransom_unaffordable", "treasury_cash": treasury}
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

    def stage_ransom_message(record: dict[str, Any], *, holder_fid: str, recipient_fid: str) -> str:
        """Persist physical delivery of an already-authored ransom demand."""
        custody_id = str(record.get("custody_id") or "")
        captive_ref = str(record.get("person_ref") or "")
        try:
            _hfpath, holder = load_faction(holder_fid)
            _rfpath, recipient = load_faction(recipient_fid)
        except (KeyError, FileNotFoundError, TypeError, ValueError):
            return "endpoints_unresolved"
        holder_place = str(holder.get("headquarters") or "")
        recipient_place = str(recipient.get("headquarters") or "")
        if not holder_place or not recipient_place:
            return "endpoints_unresolved"
        if holder_place == recipient_place:
            message_due = at + timedelta(hours=4)
            status = "local_delivery_pending"
        else:
            try:
                message_plan = travel_plan(
                    world_seed=world_seed, start_at=at, start=holder_place, end=recipient_place, mode="foot",
                )
                message_due = datetime.fromisoformat(str(message_plan.get("arrival_at")))
                status = "in_transit"
            except (KeyError, ValueError, TypeError):
                return "route_unavailable"
        pending_one_off_events.append({
            "event_id": f"custody_response:{custody_id}:{recipient_fid}",
            "kind": "custody_response_due", "due_at": message_due.isoformat(),
            "owner_ref": custody_id, "person_ref": captive_ref,
            "holder_faction_ref": holder_fid, "responder_faction_ref": recipient_fid,
            "information_source": "ransom_message", "information_route_status": status,
            "message_origin_location_ref": holder_place, "message_target_location_ref": recipient_place,
            "message_reroute_count": 0, "requires_player_decision": False,
        })
        return status

    # Apply exact GM-authored captivity policy.  The selected option has already
    # been bound to the durable decision envelope; this domain still revalidates
    # custody, ownership, money and physical feasibility before any hard write.
    for judgment_event in events:
        if str(judgment_event.get("kind") or "") != "npc_judgment_committed":
            continue
        judgment_kind = str(judgment_event.get("judgment_kind") or "")
        if judgment_kind not in {"captor_ransom_policy", "captive_response_policy"}:
            continue
        choice = judgment_event.get("selected_option")
        context = judgment_event.get("decision_context")
        if not isinstance(choice, Mapping) or not isinstance(context, Mapping):
            raise ValueError("captivity npc judgment invalid")
        custody_id = str(context.get("custody_id") or judgment_event.get("owner_ref") or "")
        captive_ref = str(context.get("captive_ref") or "")
        record = active_custody(custody_id, person_ref=captive_ref)
        if not isinstance(record, dict):
            reviews.append({"kind": judgment_kind, "decision_ref": judgment_event.get("decision_ref"), "result": "custody_not_active"})
            continue
        if judgment_kind == "captor_ransom_policy":
            holder_fid = str(context.get("holder_faction_ref") or "")
            if str(record.get("holder_faction_ref") or "") != holder_fid:
                raise ValueError("captor ransom holder changed")
            intent = str(choice.get("intent") or "")
            if intent == "hold":
                reviews.append({"kind":"captor_ransom_policy","decision_ref":judgment_event.get("decision_ref"),"result":"held_without_ransom"})
                continue
            if intent != "demand":
                raise ValueError("captor ransom intent invalid")
            demand = max(0, int(choice.get("ransom_cash", 0)))
            recipient_fid = str(context.get("recipient_faction_ref") or "")
            if demand <= 0 or not recipient_fid or recipient_fid == holder_fid:
                raise ValueError("captor ransom terms invalid")
            if int(record.get("ransom_demand_cash", 0)) > 0:
                raise ValueError("ransom already demanded")
            record["ransom_demand_cash"] = demand
            record["ransom_demanded_at"] = at_iso
            record["ransom_recipient_faction_ref"] = recipient_fid
            status = stage_ransom_message(record, holder_fid=holder_fid, recipient_fid=recipient_fid)
            writes[_CUSTODY_PATH] = custody_state
            reviews.append({
                "kind":"captor_ransom_policy","decision_ref":judgment_event.get("decision_ref"),
                "person_ref":captive_ref,"holder_faction_ref":holder_fid,"ransom_demand_cash":demand,
                "message_route_status":status,"result":"ransom_demanded" if status not in {"route_unavailable","endpoints_unresolved"} else "ransom_demanded_message_route_unavailable",
            })
            continue

        responder_fid = str(context.get("responder_faction_ref") or "")
        if not responder_fid or responder_fid == str(record.get("holder_faction_ref") or ""):
            raise ValueError("custody responder invalid")
        intent = str(choice.get("intent") or "")
        if intent == "defer":
            reviews.append({"kind":"captive_response_policy","decision_ref":judgment_event.get("decision_ref"),"responder_faction_ref":responder_fid,"result":"response_deferred"})
            continue
        if intent == "rescue":
            response = start_custody_rescue_operation(responder_fid, record, choice)
            reviews.append({"kind":"captive_response_policy","decision_ref":judgment_event.get("decision_ref"),"responder_faction_ref":responder_fid,**copy.deepcopy(dict(response))})
            continue
        if intent == "pay_ransom":
            demand = max(0, int(record.get("ransom_demand_cash", 0)))
            payment = ransom_repatriation(responder_fid, record, demand_cash=demand)
            reviews.append({"kind":"captive_response_policy","decision_ref":judgment_event.get("decision_ref"),"responder_faction_ref":responder_fid,**copy.deepcopy(dict(payment))})
            continue
        raise ValueError("custody response intent invalid")

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
        # Price bands are bounded negotiation envelopes, not a selected demand.
        # ChatGPT may choose to hold the captive without demanding money or pick
        # one currently feasible ransom term; Python never turns valuation/risk
        # scores into the captor's human decision.
        amounts = sorted({
            max(1_000, ((base_value * milli // 1000) + 99) // 100 * 100)
            for milli in (750, 1000, 1250, 1500)
        })
        judgment = build_npc_judgment_event(
            judgment_kind="captor_ransom_policy", at_iso=at_iso,
            actor_ref=holder_fid, owner_ref=custody_id,
            options=[{"intent":"hold"}] + [{"intent":"demand","ransom_cash":amount} for amount in amounts],
            decision_context={
                "custody_id":custody_id,"captive_ref":str(record.get("person_ref") or ""),
                "holder_faction_ref":holder_fid,"recipient_faction_ref":recipient_fid,
                "captive_value_cash":base_value,"holder_risk_tolerance":risk,
            },
            source_event_id=str(event.get("event_id") or ""),
        )
        pending_one_off_events.append(judgment)
        handoffs.append(judgment_handoff(judgment))
        reviews.append({
            "kind":"custody_captor_review","event_id":event.get("event_id"),
            "person_ref":str(record.get("person_ref") or ""),"holder_faction_ref":holder_fid,
            "result":"ransom_judgment_required","decision_ref":judgment["decision_ref"],
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

        # A physically dispatched ransom message is addressed to the recipient's
        # headquarters as it existed at dispatch.  Reaching that old address is
        # not permission to grant the faction knowledge if its headquarters has
        # moved in the meantime.  Legacy/manual response events without a saved
        # target retain their historical semantics; new routed messages fail
        # closed and chase the current endpoint.
        information_source = str(event.get("information_source") or "")
        planned_target = str(event.get("message_target_location_ref") or "")
        if information_source == "ransom_message" and planned_target and explicit:
            try:
                _rfpath, responder = load_faction(explicit)
                current_target = str(responder.get("headquarters") or "")
            except (KeyError, FileNotFoundError, TypeError, ValueError):
                current_target = ""
            if not current_target:
                reviews.append({
                    "kind": "custody_response_due",
                    "event_id": event.get("event_id"),
                    "person_ref": person_ref,
                    "responder_faction_ref": explicit,
                    "result": "ransom_message_endpoint_unresolved",
                })
                continue
            if current_target != planned_target:
                try:
                    reroute_plan = travel_plan(
                        world_seed=world_seed,
                        start_at=at,
                        start=planned_target,
                        end=current_target,
                        mode="foot",
                    )
                    reroute_due = datetime.fromisoformat(str(reroute_plan.get("arrival_at")))
                except (KeyError, ValueError, TypeError):
                    reviews.append({
                        "kind": "custody_response_due",
                        "event_id": event.get("event_id"),
                        "person_ref": person_ref,
                        "responder_faction_ref": explicit,
                        "from_location_ref": planned_target,
                        "to_location_ref": current_target,
                        "result": "ransom_message_reroute_unavailable",
                    })
                    continue
                reroute_count = max(0, int(event.get("message_reroute_count", 0))) + 1
                pending_one_off_events.append({
                    "event_id": f"custody_response:{custody_id}:{explicit}:reroute:{reroute_count}",
                    "kind": "custody_response_due",
                    "due_at": reroute_due.isoformat(),
                    "owner_ref": custody_id,
                    "person_ref": person_ref,
                    "holder_faction_ref": str(record.get("holder_faction_ref") or ""),
                    "responder_faction_ref": explicit,
                    "information_source": "ransom_message",
                    "information_route_status": "rerouted_in_transit",
                    "message_origin_location_ref": planned_target,
                    "message_target_location_ref": current_target,
                    "message_reroute_count": reroute_count,
                    "requires_player_decision": False,
                })
                reviews.append({
                    "kind": "custody_response_due",
                    "event_id": event.get("event_id"),
                    "person_ref": person_ref,
                    "responder_faction_ref": explicit,
                    "from_location_ref": planned_target,
                    "to_location_ref": current_target,
                    "result": "ransom_message_rerouted",
                })
                continue

        response_rows: list[dict[str, Any]] = []
        for responder_fid in responder_candidates:
            if not responder_fid or responder_fid == str(record.get("holder_faction_ref") or ""):
                continue
            known_before = {str(x) for x in record.get("informed_faction_refs", []) if isinstance(x, str) and x}
            if responder_fid not in known_before:
                informed = mark_custody_informed(record, faction_ref=responder_fid)
                record.clear(); record.update(informed)
                apply_directed_relation_event(responder_fid, str(record.get("holder_faction_ref") or ""), "member_abducted")
            demand = max(0, int(record.get("ransom_demand_cash", 0)))
            if player_faction_ref and responder_fid == player_faction_ref:
                notice = {
                    "kind":"ransom_demand_received" if demand > 0 else "custody_report_received",
                    "custody_id":custody_id,"captive_ref":person_ref,
                    "holder_faction_ref":str(record.get("holder_faction_ref") or ""),
                    **({"ransom_cash":demand} if demand > 0 else {}),
                    "delivered_to_player":True,"requires_player_decision":True,
                }
                handoffs.append({**notice,"handoff":classify_handoff(notice)})
                response_rows.append({"responder_faction_ref":responder_fid,"result":"player_decision_required"})
                continue
            try:
                _rfpath, responder = load_faction(responder_fid)
                treasury = max(0, int(responder.get("treasury_cash", 0)))
                policy = responder.get("autonomy_policy", {}) if isinstance(responder.get("autonomy_policy"), Mapping) else {}
                risk = max(0, min(100, int(policy.get("risk_tolerance", 50))))
            except (KeyError, FileNotFoundError, TypeError, ValueError):
                response_rows.append({"responder_faction_ref":responder_fid,"result":"responder_unresolved"})
                continue
            options = [{"intent":"defer","responder_faction_ref":responder_fid}]
            for force_scale in ("limited", "measured", "maximum"):
                for selection_policy in ("kin_priority", "combat_readiness"):
                    options.append({
                        "intent":"rescue", "responder_faction_ref":responder_fid,
                        "force_scale":force_scale, "selection_policy":selection_policy,
                    })
            if demand > 0:
                options.append({"intent":"pay_ransom","responder_faction_ref":responder_fid,"ransom_cash":demand})
            judgment = build_npc_judgment_event(
                judgment_kind="captive_response_policy", at_iso=at_iso,
                actor_ref=responder_fid, owner_ref=custody_id, options=options,
                decision_context={
                    "custody_id":custody_id,"captive_ref":person_ref,
                    "holder_faction_ref":str(record.get("holder_faction_ref") or ""),
                    "responder_faction_ref":responder_fid,"ransom_cash":demand,
                    "responder_treasury_cash":treasury,"risk_tolerance":risk,
                },
                source_event_id=str(event.get("event_id") or ""),
            )
            pending_one_off_events.append(judgment)
            handoffs.append(judgment_handoff(judgment))
            response_rows.append({
                "responder_faction_ref":responder_fid,"result":"response_judgment_required",
                "decision_ref":judgment["decision_ref"],
            })
        writes[_CUSTODY_PATH] = custody_state
        reviews.append({
            "kind":"custody_response_due","event_id":event.get("event_id"),"person_ref":person_ref,
            "holder_faction_ref":str(record.get("holder_faction_ref") or ""),
            "result":response_rows[0]["result"] if response_rows else "no_responder_known",
            "responses":response_rows,
        })



__all__ = ["settle_captivity_frontier"]
