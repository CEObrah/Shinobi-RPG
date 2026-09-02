"""Monthly regional market and lawful government-response frontier."""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from .crime_custody import create_government_custody_record
from .calendar_modifiers import government_attention_milli
from .government import allocate_response, decay_attention_rows
from .government_finance import refund_bounty_escrow
from .handoffs import classify_handoff
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
        contacts = 0
        detained = 0
        resolution_cfg = government_troops.get("contact_resolution", {}) if isinstance(government_troops, Mapping) else {}
        power_by_tier = {
            tier: max(1, int(resolution_cfg.get(f"{tier}_power", {"militia": 35, "standard": 65, "elite": 95}[tier])))
            for tier in ("militia", "standard", "elite")
        }
        advantage = max(1000, int(resolution_cfg.get("detention_advantage_milli", 1800)))
        for warrant_ref in sorted(warrants):
            raw = warrants.get(warrant_ref)
            if not isinstance(raw, Mapping) or raw.get("status") not in {"active", "pursuing"} or raw.get("jurisdiction_ref") != region:
                continue
            subject_ref = raw.get("subject_ref")
            if not isinstance(subject_ref, str):
                continue
            try:
                fid, _rpath, _roster, _ordinal, subject = load_person_ref(subject_ref)
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
            effective_attention=attention * government_attention_milli(datetime.fromisoformat(at_iso),review_window_days=30) // 1000
            allocated = allocate_response(effective_attention, capacity)
            deployment = allocated["allocated"]
            if int(deployment.get("exact_headcount", 0)) <= 0:
                continue
            capacity = dict(allocated["capacity_after"])
            contacts += 1
            warrant = copy.deepcopy(dict(raw))
            warrant["status"] = "pursuing"
            if subject_ref == player_ref:
                warrants[warrant_ref] = warrant
                notice = {
                    "kind": "government_summons", "warrant_ref": warrant_ref, "subject_ref": subject_ref,
                    "region_ref": region, "deployed_headcount": int(deployment["exact_headcount"]),
                    "requires_player_decision": True, "delivered_to_player": True,
                }
                handoff = classify_handoff(notice)
                handoffs.append({**notice, "handoff": handoff})
                continue
            response_power = sum(int(deployment.get(tier, 0)) * power_by_tier[tier] for tier in power_by_tier)
            target_power = person_combat_index(subject)
            if response_power * 1000 >= target_power * advantage:
                active_custody = [
                    row for row in custody_state.get("records", [])
                    if isinstance(row, Mapping) and row.get("person_ref") == subject_ref
                    and row.get("status") not in {"released", "escaped", "rescued", "executed"}
                ]
                if not active_custody:
                    government_record=create_government_custody_record(
                        person_ref=subject_ref,jurisdiction_ref=region,at=at_iso,
                        detention_site_ref=str(subject.get("location_ref") or ""),basis=f"active_warrant:{warrant_ref}",
                        offense=str(warrant.get("offense") or "theft"),guard_strength=max(1,int(deployment.get("exact_headcount",0))),
                    )
                    custody_state["records"].append(government_record)
                    pending_one_off_events.append({"event_id":f"government_custody_release_due:{government_record['custody_id']}","kind":"government_custody_release_due","due_at":str(government_record["sentence_release_at"]),"owner_ref":str(government_record["custody_id"]),"requires_player_decision":False})
                    if fid: pause_people_for_commitment(fid,[subject_ref])
                writes[_CUSTODY_PATH] = custody_state
                escrow=max(0,int(warrant.get("bounty_escrow_cash",0)))
                if escrow:
                    mpath,refund_market=load_market(region)
                    refund=refund_bounty_escrow(refund_market,warrant)
                    refund_market=refund["market_after"]
                    market_cache[region]=(mpath,refund_market)
                    writes[mpath]=refund_market
                warrants.pop(warrant_ref, None)
                detained += 1
                continue
            warrants[warrant_ref] = warrant
        capacities[region] = capacity
        writes[_GOVERNMENT_PATH] = government_state
        if contacts or detained:
            reviews.append({"kind": "government_response", "region_ref": region, "contacts": contacts, "detentions": detained})


__all__ = ["settle_regional_frontier"]
