"""Monthly regional market and lawful government-response frontier."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Sequence

from .crime_custody import create_government_custody_record
from .calendar_modifiers import government_attention_milli
from .government import allocate_response, decay_attention_rows, mobilization
from .government_finance import fund_bounty_escrow, refund_bounty_escrow
from .handoffs import classify_handoff
from .npc_judgment import build_npc_judgment_event, judgment_handoff
from .regional_economy import settle_cycles

_GOVERNMENT_PATH = "state/martial-world/government.json"
_CUSTODY_PATH = "state/martial-world/custody.json"


def settle_regional_frontier(
    *,
    events: Sequence[Mapping[str, Any]],
    at_iso: str,
    player_ref: str,
    government_state: dict[str, Any],
    government_troops: Mapping[str, Any],
    custody_state: dict[str, Any],
    writes: dict[str, Any],
    reviews: list[dict[str, Any]],
    handoffs: list[dict[str, Any]],
    market_cache: dict[str, tuple[str, dict[str, Any]]],
    load_market: Callable[[str], tuple[str, dict[str, Any]]],
    load_person_ref: Callable[[str], tuple[str, str, dict[str, Any], int, dict[str, Any]]],
    unavailable_person_refs: Callable[[], set[str]],
    pause_people_for_commitment: Callable[[str, Sequence[str]], None],
    person_combat_index: Callable[[Mapping[str, Any]], int],
    site_rows: Mapping[str, Any],
    place_region: Mapping[str, str],
    pending_one_off_events: list[dict[str, Any]] | None = None,
    resume_people_training: Callable[[Sequence[str]], None] | None = None,
) -> None:
    if pending_one_off_events is None:
        pending_one_off_events = []
    if resume_people_training is None:
        resume_people_training = lambda _refs: None


    # Consequential enforcement policy is authored by the GM from an objective
    # evidence/attention envelope.  This reducer revalidates jurisdiction,
    # current attention and public funds before a warrant/bounty becomes truth.
    for event in events:
        if event.get("kind") != "npc_judgment_committed" or event.get("judgment_kind") != "government_enforcement_policy":
            continue
        choice = event.get("selected_option")
        if not isinstance(choice, Mapping):
            raise ValueError("government enforcement judgment invalid")
        intent = str(choice.get("intent") or "")
        subject_ref = str(choice.get("subject_ref") or "")
        region = str(choice.get("jurisdiction_ref") or "")
        if intent in {"no_action", "maintain"}:
            reviews.append({"kind":"government_enforcement_policy","subject_ref":subject_ref,"region_ref":region,"result":intent,"decision_origin":"gm_npc_judgment"})
            continue
        if intent != "issue_warrant" or not subject_ref or not region:
            raise ValueError("government enforcement judgment intent invalid")
        attention_rows = government_state.setdefault("attention", {})
        warrants = government_state.setdefault("warrants", {})
        if not isinstance(attention_rows, dict) or not isinstance(warrants, dict):
            raise ValueError("jianghu government state invalid")
        attention_row = attention_rows.get(subject_ref, {}) if isinstance(attention_rows.get(subject_ref), Mapping) else {}
        attention = max(0, int(attention_row.get("attention", 0)))
        if attention <= 0:
            reviews.append({"kind":"government_enforcement_policy","subject_ref":subject_ref,"region_ref":region,"result":"attention_no_longer_supports_action"})
            continue
        warrant_ref = f"warrant:{subject_ref}"
        existing = warrants.get(warrant_ref, {}) if isinstance(warrants.get(warrant_ref), Mapping) else {}
        desired = max(0, int(choice.get("bounty_cash", 0) or 0))
        # The authored option is bounded when created.  Revalidation prevents a
        # stale decision from escalating beyond current objective attention.
        maximum = max(500, attention * 25)
        if desired > maximum:
            raise ValueError("government enforcement bounty exceeds current envelope")
        existing_escrow = max(0, int(existing.get("bounty_escrow_cash", 0))) if isinstance(existing, Mapping) else 0
        funded_bounty = existing_escrow
        if desired > existing_escrow:
            mpath, market = load_market(region)
            funding = fund_bounty_escrow(market, existing_warrant=existing, desired_cash=desired)
            funded_bounty = int(funding["escrow_cash"])
            if int(funding["escrow_added_cash"]):
                market = funding["market_after"]
                market_cache[region] = (mpath, market); writes[mpath] = market
        existing_status = str(existing.get("status") or "") if isinstance(existing, Mapping) else ""
        warrants[warrant_ref] = {
            "subject_ref": subject_ref, "offense": str(choice.get("offense") or existing.get("offense") or "public_offense"),
            "bounty_cash": funded_bounty, "bounty_escrow_cash": funded_bounty,
            "status": existing_status if existing_status in {"active", "pursuing"} else "active",
            "evidence_ref": str(choice.get("evidence_ref") or existing.get("evidence_ref") or ""),
            "issued_at": str(existing.get("issued_at") or at_iso), "jurisdiction_ref": region,
            "decision_origin": "gm_npc_judgment",
        }
        attention_row = copy.deepcopy(dict(attention_row)); attention_row["bounty_cash"] = funded_bounty; attention_rows[subject_ref] = attention_row
        writes[_GOVERNMENT_PATH] = government_state
        reviews.append({
            "kind":"government_enforcement_policy","subject_ref":subject_ref,"region_ref":region,
            "result":"warrant_issued","bounty_cash":funded_bounty,"decision_origin":"gm_npc_judgment",
        })

    # Once government responders actually reach an NPC, surrender/refusal is a
    # voluntary human choice.  Apply only an exact committed response; force
    # ratios are advisory context and never author surrender in Python.
    for event in events:
        if event.get("kind") != "npc_judgment_committed" or event.get("judgment_kind") != "government_contact_response_policy":
            continue
        choice = event.get("selected_option")
        if not isinstance(choice, Mapping):
            raise ValueError("government contact response judgment invalid")
        intent = str(choice.get("intent") or "")
        subject_ref = str(choice.get("subject_ref") or event.get("actor_ref") or "")
        warrant_ref = str(choice.get("warrant_ref") or event.get("owner_ref") or "")
        region = str(choice.get("jurisdiction_ref") or "")
        warrants = government_state.setdefault("warrants", {})
        if not isinstance(warrants, dict):
            raise ValueError("jianghu government warrants state invalid")
        raw = warrants.get(warrant_ref)
        if not isinstance(raw, Mapping) or str(raw.get("subject_ref") or "") != subject_ref:
            reviews.append({"kind":"government_contact_response_policy","subject_ref":subject_ref,"warrant_ref":warrant_ref,"result":"warrant_unavailable"})
            continue
        warrant = copy.deepcopy(dict(raw))
        region = region or str(warrant.get("jurisdiction_ref") or "")
        try:
            fid, _rpath, _roster, _ordinal, subject = load_person_ref(subject_ref)
        except (KeyError, ValueError, FileNotFoundError):
            subject = None; fid = ""
        unavailable = subject_ref in unavailable_person_refs()
        site = site_rows.get(str(subject.get("location_ref"))) if isinstance(subject, Mapping) else None
        place = str(site.get("parent_place_ref") or "") if isinstance(site, Mapping) else ""
        present_in_region = isinstance(subject, Mapping) and not unavailable and place_region.get(place) == region
        if not present_in_region:
            warrant["status"] = "pursuing"; warrants[warrant_ref] = warrant; writes[_GOVERNMENT_PATH] = government_state
            reviews.append({"kind":"government_contact_response_policy","subject_ref":subject_ref,"warrant_ref":warrant_ref,"result":"contact_no_longer_current"})
            continue
        if intent == "resist":
            warrant["status"] = "pursuing"; warrants[warrant_ref] = warrant; writes[_GOVERNMENT_PATH] = government_state
            reviews.append({
                "kind":"government_contact_response_policy","subject_ref":subject_ref,"warrant_ref":warrant_ref,
                "result":"custody_refused","decision_origin":"gm_npc_judgment",
            })
            continue
        if intent != "surrender":
            raise ValueError("government contact response intent invalid")
        active_custody = [
            row for row in custody_state.get("records", [])
            if isinstance(row, Mapping) and row.get("person_ref") == subject_ref
            and row.get("status") not in {"released", "escaped", "rescued", "executed"}
        ]
        if not active_custody:
            deployment = choice.get("deployment", {}) if isinstance(choice.get("deployment"), Mapping) else {}
            government_record = create_government_custody_record(
                person_ref=subject_ref, jurisdiction_ref=region, at=at_iso,
                detention_site_ref=str(subject.get("location_ref") or ""), basis=f"active_warrant:{warrant_ref}",
                offense=str(warrant.get("offense") or "theft"), guard_strength=max(1, int(deployment.get("exact_headcount", 1))),
            )
            custody_state["records"].append(government_record)
            pending_one_off_events.append({
                "event_id": f"government_custody_release_due:{government_record['custody_id']}",
                "kind": "government_custody_release_due", "due_at": str(government_record["sentence_release_at"]),
                "owner_ref": str(government_record["custody_id"]), "requires_player_decision": False,
            })
            if fid:
                pause_people_for_commitment(fid, [subject_ref])
        writes[_CUSTODY_PATH] = custody_state
        escrow = max(0, int(warrant.get("bounty_escrow_cash", 0)))
        if escrow:
            mpath, refund_market = load_market(region)
            refund = refund_bounty_escrow(refund_market, warrant)
            refund_market = refund["market_after"]
            market_cache[region] = (mpath, refund_market); writes[mpath] = refund_market
        warrants.pop(warrant_ref, None); writes[_GOVERNMENT_PATH] = government_state
        reviews.append({
            "kind":"government_contact_response_policy","subject_ref":subject_ref,"warrant_ref":warrant_ref,
            "result":"surrendered_into_custody","decision_origin":"gm_npc_judgment",
        })

    # Government sentence expiry is a one-off causal frontier, not monthly polling.
    for event in events:
        if event.get("kind") != "government_custody_release_due":
            continue
        custody_id = str(event.get("owner_ref") or "")
        released_ref = ""; next_rows = []
        for raw in custody_state.get("records", []):
            if not isinstance(raw, Mapping) or str(raw.get("custody_id") or "") != custody_id:
                next_rows.append(raw); continue
            if str(raw.get("holder_kind") or "") != "government" or raw.get("status") in {"released","escaped","rescued","executed"}:
                next_rows.append(raw); continue
            released_ref = str(raw.get("person_ref") or "")
        if released_ref:
            custody_state["records"] = next_rows; writes[_CUSTODY_PATH] = custody_state
            resume_people_training([released_ref])
            reviews.append({"kind":"government_custody_release","person_ref":released_ref,"custody_id":custody_id,"result":"sentence_completed"})

    # A monthly review may authorize a response, but authorization is not
    # physical contact.  Government responders must first mobilize for the
    # registered duration.  Only this one-off frontier may deliver a summons or
    # detain a subject, and the subject is re-located at contact time rather
    # than assumed to remain where the warrant was reviewed.
    for event in events:
        if event.get("kind") != "government_contact_due":
            continue
        warrant_ref = str(event.get("warrant_ref") or event.get("owner_ref") or "")
        warrants = government_state.setdefault("warrants", {})
        if not isinstance(warrants, dict):
            raise ValueError("jianghu government warrants state invalid")
        raw = warrants.get(warrant_ref)
        if not isinstance(raw, Mapping) or raw.get("status") != "mobilizing":
            continue
        subject_ref = str(raw.get("subject_ref") or event.get("subject_ref") or "")
        region = str(raw.get("jurisdiction_ref") or event.get("jurisdiction_ref") or "")
        deployment_raw = event.get("deployment", {})
        deployment = dict(deployment_raw) if isinstance(deployment_raw, Mapping) else {}
        if not subject_ref or not region or int(deployment.get("exact_headcount", 0)) <= 0:
            warrant = copy.deepcopy(dict(raw))
            warrant["status"] = "pursuing"
            warrants[warrant_ref] = warrant
            writes[_GOVERNMENT_PATH] = government_state
            continue
        try:
            fid, _rpath, _roster, _ordinal, subject = load_person_ref(subject_ref)
        except (KeyError, ValueError, FileNotFoundError):
            subject = None
            fid = ""
        unavailable = subject_ref in unavailable_person_refs()
        site = site_rows.get(str(subject.get("location_ref"))) if isinstance(subject, Mapping) else None
        place = str(site.get("parent_place_ref") or "") if isinstance(site, Mapping) else ""
        present_in_region = isinstance(subject, Mapping) and not unavailable and place_region.get(place) == region
        if not present_in_region:
            warrant = copy.deepcopy(dict(raw))
            warrant["status"] = "pursuing"
            warrants[warrant_ref] = warrant
            writes[_GOVERNMENT_PATH] = government_state
            reviews.append({
                "kind": "government_response", "region_ref": region, "warrant_ref": warrant_ref,
                "contacts": 0, "detentions": 0, "result": "contact_missed_after_mobilization",
            })
            continue
        warrant = copy.deepcopy(dict(raw))
        warrant["status"] = "pursuing"
        if subject_ref == player_ref:
            warrants[warrant_ref] = warrant
            writes[_GOVERNMENT_PATH] = government_state
            notice = {
                "kind": "government_summons", "warrant_ref": warrant_ref, "subject_ref": subject_ref,
                "region_ref": region, "deployed_headcount": int(deployment["exact_headcount"]),
                "requires_player_decision": True, "delivered_to_player": True,
            }
            handoff = classify_handoff(notice)
            handoffs.append({**notice, "handoff": handoff})
            reviews.append({
                "kind": "government_response", "region_ref": region, "warrant_ref": warrant_ref,
                "contacts": 1, "detentions": 0, "result": "player_contacted_after_mobilization",
            })
            continue
        resolution_cfg = government_troops.get("contact_resolution", {}) if isinstance(government_troops, Mapping) else {}
        power_by_tier = {
            tier: max(1, int(resolution_cfg.get(f"{tier}_power", {"militia": 35, "standard": 65, "elite": 95}[tier])))
            for tier in ("militia", "standard", "elite")
        }
        advantage = max(1000, int(resolution_cfg.get("detention_advantage_milli", 1800)))
        response_power = sum(int(deployment.get(tier, 0)) * power_by_tier[tier] for tier in power_by_tier)
        target_power = person_combat_index(subject)
        response_ratio_milli = response_power * 1000 // max(1, target_power)
        contact_judgment = build_npc_judgment_event(
            judgment_kind="government_contact_response_policy", at_iso=at_iso,
            actor_ref=subject_ref, owner_ref=warrant_ref,
            options=[
                {"intent":"surrender","subject_ref":subject_ref,"warrant_ref":warrant_ref,"jurisdiction_ref":region,"deployment":copy.deepcopy(deployment)},
                {"intent":"resist","subject_ref":subject_ref,"warrant_ref":warrant_ref,"jurisdiction_ref":region,"deployment":copy.deepcopy(deployment)},
            ],
            decision_context={
                "subject_ref":subject_ref,"warrant_ref":warrant_ref,"jurisdiction_ref":region,
                "response_power":response_power,"target_combat_index":target_power,
                "response_power_ratio_milli":response_ratio_milli,"detention_advantage_milli":advantage,
                "deployed_headcount":int(deployment.get("exact_headcount",0)),
            },
            source_event_id=str(event.get("event_id") or ""),
        )
        pending_one_off_events.append(contact_judgment); handoffs.append(judgment_handoff(contact_judgment))
        warrant["status"] = "contact_pending"; warrants[warrant_ref] = warrant
        writes[_GOVERNMENT_PATH] = government_state
        reviews.append({
            "kind": "government_response", "region_ref": region, "warrant_ref": warrant_ref,
            "contacts": 1, "detentions": 0, "result": "npc_contact_judgment_required",
            "decision_ref":contact_judgment["decision_ref"],
        })

    # Government attention is a global compact current accumulator. The
    # recurring regional class may be sharded across multiple same-timestamp
    # frontiers, so decay exactly once by anchoring it to the lexicographically
    # first real region rather than once per processed chunk.
    monthly_regions = sorted({str(value) for value in place_region.values() if isinstance(value, str) and value})
    monthly_event_regions = {
        str(event.get('owner_ref')) for event in events
        if isinstance(event, Mapping) and event.get('kind') == 'regional_market_cycle' and isinstance(event.get('owner_ref'), str)
    }
    if monthly_regions and monthly_regions[0] in monthly_event_regions:
        attention_rows = government_state.setdefault('attention', {})
        if not isinstance(attention_rows, dict):
            raise ValueError('jianghu government attention state invalid')
        government_state['attention'] = decay_attention_rows(attention_rows)
        writes[_GOVERNMENT_PATH] = government_state

    settled_regions: set[str] = set()
    for event in events:
        if event.get("kind") != "regional_market_cycle":
            continue
        region = event.get("owner_ref")
        if not isinstance(region, str) or region in settled_regions:
            continue
        path, market = load_market(region)
        after = settle_cycles(market, cycles=1)
        writes[path] = after
        market_cache[region] = (path, after)
        settled_regions.add(region)
        reviews.append({"kind": "regional_market_cycle", "event_id": event.get("event_id"), "region_id": region})

    # Warrants are jurisdictional information, not omniscient pursuit. A subject
    # can be contacted only if physically present and not owned by a conflicting
    # route/deployment/construction commitment at this frontier.
    government_regions: set[str] = set()
    for event in events:
        if event.get("kind") != "regional_market_cycle":
            continue
        region = event.get("owner_ref")
        if not isinstance(region, str) or region in government_regions:
            continue
        government_regions.add(region)
        capacities = government_state.setdefault("regional_capacity", {})
        warrants = government_state.setdefault("warrants", {})
        attention_rows = government_state.setdefault("attention", {})
        if not all(isinstance(x, dict) for x in (capacities, warrants, attention_rows)):
            raise ValueError("jianghu government state invalid")
        defaults = government_troops.get("default_regional_capacity", {}) if isinstance(government_troops, Mapping) else {}
        recovery = government_troops.get("monthly_reconstitution", {}) if isinstance(government_troops, Mapping) else {}
        current = capacities.get(region, {}) if isinstance(capacities.get(region), Mapping) else {}
        capacity = {
            tier: min(
                max(0, int(defaults.get(tier, 0))),
                max(0, int(current.get(tier, defaults.get(tier, 0)))) + max(0, int(recovery.get(tier, 0))),
            )
            for tier in ("militia", "standard", "elite")
        }
        mobilized = 0
        for warrant_ref in sorted(warrants):
            raw = warrants.get(warrant_ref)
            if not isinstance(raw, Mapping) or raw.get("status") not in {"active", "pursuing"} or raw.get("jurisdiction_ref") != region:
                continue
            subject_ref = raw.get("subject_ref")
            if not isinstance(subject_ref, str):
                continue
            try:
                _fid, _rpath, _roster, _ordinal, subject = load_person_ref(subject_ref)
            except (KeyError, ValueError, FileNotFoundError):
                continue
            if subject_ref in unavailable_person_refs():
                continue
            site = site_rows.get(str(subject.get("location_ref")))
            place = str(site.get("parent_place_ref") or "") if isinstance(site, Mapping) else ""
            if place_region.get(place) != region:
                continue
            att = attention_rows.get(subject_ref, {}) if isinstance(attention_rows.get(subject_ref), Mapping) else {}
            attention = max(0, int(att.get("attention", 0)))
            effective_attention = attention * government_attention_milli(datetime.fromisoformat(at_iso), review_window_days=30) // 1000
            allocated = allocate_response(effective_attention, capacity)
            deployment = allocated["allocated"]
            if int(deployment.get("exact_headcount", 0)) <= 0:
                continue
            capacity = dict(allocated["capacity_after"])
            mobilized += 1
            warrant = copy.deepcopy(dict(raw))
            warrant["status"] = "mobilizing"
            warrants[warrant_ref] = warrant
            mobilization_hours = max(0, int(mobilization(effective_attention, distance_hours=0).get("mobilization_hours", 0)))
            due_at = datetime.fromisoformat(at_iso) + timedelta(hours=mobilization_hours)
            pending_one_off_events.append({
                "event_id": f"government_contact_due:{warrant_ref}",
                "kind": "government_contact_due",
                "due_at": due_at.isoformat(),
                "owner_ref": warrant_ref,
                "warrant_ref": warrant_ref,
                "subject_ref": subject_ref,
                "jurisdiction_ref": region,
                "deployment": copy.deepcopy(deployment),
                "mobilized_from_location_ref": str(subject.get("location_ref") or ""),
                "requires_player_decision": False,
            })
        capacities[region] = capacity
        writes[_GOVERNMENT_PATH] = government_state
        if mobilized:
            reviews.append({
                "kind": "government_response_mobilized", "region_ref": region,
                "mobilizations": mobilized, "contacts": 0, "detentions": 0,
            })


__all__ = ["settle_regional_frontier"]
