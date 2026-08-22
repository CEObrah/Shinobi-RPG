"""Strategic faction mobilization and local-frontage battle settlement.

A deployment owns the full exact force. Local exact combat resolves only the
people physically contacting at one frontage; reserves remain real people in the
same deployment rather than disappearing behind an arbitrary fighter cap.
"""
from __future__ import annotations

import copy
import math
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Sequence

from .combat_simulation import simulate_exact_combat
from .commitments import extend_commitment_resources, release_resources, remove_people_from_commitments
from .equipment_state import compact_equipment_ledger, hydrate_equipment_ledger
from .faction_politics import faction_camp
from .faction_relations import apply_relation_event
from .faction_state import compact_faction_state, faction_path, hydrate_faction_state, inventory_path, roster_path
from .handoffs import classify_handoff
from .inventory_state import compact_inventory_state, hydrate_inventory_state
from .manpower import combat_readiness_score, combat_ready_members
from .person_state import compact_roster_state, hydrate_roster_state, reconcile_faction_population
from .scheduler import upsert_one_off_event
from .training import advance_faction_training_epoch, apply_institutional_training
from .world_history import record_event

_DEPLOYMENTS = "state/martial-world/deployments.json"
_COMMITMENTS = "state/martial-world/commitments.json"
_EQUIPMENT = "state/martial-world/equipment-ledger.json"
_RELATIONS = "state/martial-world/faction-relations.json"
_HISTORY = "state/martial-world/world-history.json"
_SOCIAL = "state/martial-world/social.json"
_CUSTODY = "state/martial-world/custody.json"
_FAMILY = "state/martial-world/family.json"
_LOCAL_SITES = "game/data/martial-world/local-sites.json"
_META = "state/meta.json"
_SCHEDULER = "state/martial-world/scheduler.json"


class _View:
    def __init__(self, read_json: Callable[[str], Any], writes: Mapping[str, Any]) -> None:
        self._read_json = read_json
        self._writes = writes

    def __call__(self, path: str) -> Any:
        if path in self._writes:
            return copy.deepcopy(self._writes[path])
        return copy.deepcopy(self._read_json(path))


def _load_faction(view: Callable[[str], Any], faction_ref: str) -> dict[str, Any]:
    return hydrate_faction_state(view(faction_path(faction_ref)))


def _load_roster(view: Callable[[str], Any], faction_ref: str, faction: Mapping[str, Any]) -> dict[str, Any]:
    return hydrate_roster_state(view(roster_path(faction_ref)), faction=faction)


def _load_inventory(view: Callable[[str], Any], faction_ref: str) -> dict[str, Any]:
    return hydrate_inventory_state(view(inventory_path(faction_ref)))


def _site_rows(view: Callable[[str], Any]) -> Mapping[str, Any]:
    try:
        data = view(_LOCAL_SITES)
    except FileNotFoundError:
        return {}
    rows = data.get("sites", {}) if isinstance(data, Mapping) else {}
    return rows if isinstance(rows, Mapping) else {}


def _person_place(person: Mapping[str, Any], faction: Mapping[str, Any], sites: Mapping[str, Any]) -> str:
    home = str(faction.get("headquarters") or "")
    loc = str(person.get("location_ref") or "")
    if not loc or loc == str(faction.get("local_site_ref") or ""):
        return home
    site = sites.get(loc)
    if isinstance(site, Mapping) and site.get("parent_place_ref"):
        return str(site.get("parent_place_ref"))
    return loc


def _arrival_site(sites: Mapping[str, Any], place_ref: str, fallback: str = "") -> str:
    rows = sorted(
        str(ref) for ref, row in sites.items()
        if isinstance(ref, str) and isinstance(row, Mapping)
        and str(row.get("parent_place_ref") or "") == place_ref
    )
    if rows:
        public = [
            ref for ref in rows
            if str((sites.get(ref) or {}).get("public_access", "public"))
            not in {"restricted_by_faction_policy", "private"}
        ]
        return (public or rows)[0]
    return fallback or place_ref


def local_frontage_count(site: Mapping[str, Any] | None) -> int:
    """Return physically contacting bodies per side from local spatial scale.

    This bounds one exact contact patch, never the force or battle population.
    Open grounds expose more simultaneous frontage; interior compounds expose
    less. A larger deployment is resolved through additional local contacts.
    """
    row = site if isinstance(site, Mapping) else {}
    capacity = max(1, int(row.get("capacity", 25)))
    linear = max(1, math.isqrt(capacity))
    site_type = str(row.get("site_type") or "")
    if site_type in {"tournament_ground", "training_grounds", "market", "caravan_yard", "open_ground"}:
        linear *= 2
    elif site_type in {"inn", "tea_house", "clinic", "library", "government_office", "magistrate_office"}:
        linear = max(1, (linear + 1) // 2)
    return max(1, linear)


def _relation_hostility(state: Mapping[str, Any], source: str, target: str) -> int:
    rows = state.get("edges", []) if isinstance(state, Mapping) else []
    if not isinstance(rows, list):
        return 0
    values = [
        max(0, int(row.get("hostility", 0))) for row in rows
        if isinstance(row, Mapping)
        and {str(row.get("from_faction") or ""), str(row.get("to_faction") or "")} == {source, target}
    ]
    return max(values, default=0)


def _apply_relation(state: Mapping[str, Any], source: str, target: str, kind: str) -> dict[str, Any]:
    out = copy.deepcopy(dict(state)); rows = out.setdefault("edges", [])
    if not isinstance(rows, list):
        raise ValueError("jianghu faction relations invalid")
    found = None
    for i, row in enumerate(rows):
        if isinstance(row, Mapping) and row.get("from_faction") == source and row.get("to_faction") == target:
            found = i; break
    before = rows[found] if found is not None else None
    after = apply_relation_event(before, from_faction=source, to_faction=target, event_kind=kind)
    if found is None: rows.append(after)
    else: rows[found] = after
    return out


def _pause_people(
    faction: dict[str, Any], roster: dict[str, Any], refs: Sequence[str], *, at_iso: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    selected = {str(x) for x in refs if isinstance(x, str)}
    if not selected:
        return faction, roster
    faction, _ = advance_faction_training_epoch(faction, roster, at_iso=at_iso, refresh_environment=False)
    people = roster.get("people", []) if isinstance(roster.get("people"), list) else []
    snapshot = [copy.deepcopy(dict(x)) for x in people if isinstance(x, Mapping)]
    after: list[Any] = []
    for raw in people:
        if not isinstance(raw, Mapping) or str(raw.get("person_id") or "") not in selected:
            after.append(raw); continue
        person = apply_institutional_training(raw, faction=faction, roster_people=snapshot)
        ts = copy.deepcopy(dict(person.get("training_state", {}))) if isinstance(person.get("training_state"), Mapping) else {}
        ts["institutional_paused"] = True
        person["training_state"] = ts
        after.append(person)
    roster["people"] = after
    return faction, roster


def _living(person: Mapping[str, Any]) -> bool:
    health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
    return health.get("status") != "dead"


def _combat_active(person: Mapping[str, Any], *, year: int) -> bool:
    return _living(person) and combat_readiness_score(person, year=year) > 0


def expand_new_strategic_mobilizations(
    *, read_json: Callable[[str], Any], writes: dict[str, Any], at: datetime,
) -> list[dict[str, Any]]:
    """Expand newly-created raid/war seed deployments to lawful physical musters."""
    try:
        before = read_json(_DEPLOYMENTS)
    except FileNotFoundError:
        before = {"deployments": {}}
    after = writes.get(_DEPLOYMENTS)
    if not isinstance(after, Mapping):
        return []
    before_rows = before.get("deployments", {}) if isinstance(before, Mapping) else {}
    rows = copy.deepcopy(dict(after)).setdefault("deployments", {})
    if not isinstance(rows, dict) or not isinstance(before_rows, Mapping):
        return []
    view = _View(read_json, writes)
    try:
        commitments = copy.deepcopy(dict(view(_COMMITMENTS)))
    except FileNotFoundError:
        commitments = {"schema":"jianghu-commitment-state-1.0","commitments":{},"person_index":{}}
    try:
        relations = view(_RELATIONS)
    except FileNotFoundError:
        relations = {"edges": []}
    try:
        history = copy.deepcopy(dict(view(_HISTORY)))
    except FileNotFoundError:
        history = {"recent": [], "counters": {}, "totals": {}}
    sites = _site_rows(view)
    reviews: list[dict[str, Any]] = []
    changed_deployments = False

    for op_ref in sorted(str(x) for x in rows if isinstance(x, str)):
        if op_ref in before_rows:
            continue
        raw = rows.get(op_ref)
        if not isinstance(raw, Mapping) or raw.get("status") != "traveling_outbound":
            continue
        kind = str(raw.get("operation_kind") or "")
        if kind not in {"faction_raid", "faction_war_strike"}:
            continue
        fid = str(raw.get("faction_ref") or ""); target = str(raw.get("target_faction_ref") or "")
        if not fid or not target:
            continue
        faction = _load_faction(view, fid); roster = _load_roster(view, fid, faction); inventory = _load_inventory(view, fid)
        existing = [str(x) for x in raw.get("participant_refs", []) if isinstance(x, str)]
        if not existing:
            continue
        index = commitments.get("person_index", {}) if isinstance(commitments.get("person_index"), Mapping) else {}
        blocked = {str(x) for x in index if isinstance(x, str)} - set(existing)
        ready = combat_ready_members(
            [p for p in roster.get("people", []) if isinstance(p, Mapping)],
            year=at.year, unavailable_refs=blocked, minimum_age=16,
        )
        home = str(faction.get("headquarters") or "")
        ready = [p for p in ready if _person_place(p, faction, sites) == home]
        ordered = [str(p.get("person_id")) for p in ready if isinstance(p.get("person_id"), str)]
        ordered = list(dict.fromkeys(existing + ordered))
        total = len(ordered)
        if total <= len(existing):
            continue
        policy = faction.get("autonomy_policy", {}) if isinstance(faction.get("autonomy_policy"), Mapping) else {}
        risk = max(0, min(100, int(policy.get("risk_tolerance", 50))))
        hostility = _relation_hostility(relations, fid, target)
        if kind == "faction_raid":
            # Stealth/cohesion is a real constraint, so raid size grows
            # sublinearly with available manpower instead of using a hard cap.
            coordination = max(2, 2 + risk // 20)
            desired = max(len(existing), math.isqrt(max(1, total * coordination)))
        else:
            # Declared war commits a risk/hostility-driven share while retaining
            # a home/security reserve. The percentage is doctrine, not a headcount cap.
            mobilize_permille = min(900, 450 + risk * 3 + max(0, hostility - 50) * 2)
            reserve_permille = max(100, 300 - risk * 2)
            home_reserve = max(1, (total * reserve_permille + 999) // 1000)
            desired = min(max(0, total - home_reserve), (total * mobilize_permille + 999) // 1000)
            desired = max(len(existing), desired)
        desired = min(total, desired)
        per_person_food = max(1, ((int(float(raw.get("travel_hours", 24))) + 23) // 24) * 2 + 1)
        food = max(0, int(inventory.get("food_ration_days", 0)))
        home_food_reserve = max(0, int(faction.get("population", total))) * 14
        affordable_extra = max(0, food - home_food_reserve) // per_person_food
        extra_count = min(max(0, desired - len(existing)), affordable_extra)
        if extra_count <= 0:
            continue
        extras = [ref for ref in ordered if ref not in existing][:extra_count]
        if not extras:
            continue
        try:
            commitments = extend_commitment_resources(
                commitments, activity_ref=op_ref,
                resources=[("person", ref, fid) for ref in extras],
            )
        except ValueError:
            continue
        inventory["food_ration_days"] = food - len(extras) * per_person_food
        faction, roster = _pause_people(faction, roster, extras, at_iso=at.isoformat())
        current = copy.deepcopy(dict(raw)); current["participant_refs"] = existing + extras
        current["mobilized_force_count"] = len(current["participant_refs"])
        current["mobilization_basis"] = "stealth_coordination" if kind == "faction_raid" else "risk_hostility_fraction_with_home_reserve"
        rows[op_ref] = current; changed_deployments = True
        writes[faction_path(fid)] = compact_faction_state(faction)
        writes[roster_path(fid)] = compact_roster_state(roster, faction=faction)
        writes[inventory_path(fid)] = compact_inventory_state(inventory)
        history = record_event(
            history, at=at.isoformat(), kind="strategic_mobilization_expanded",
            faction_ref=fid, target_faction_ref=target, operation_ref=op_ref,
            operation_kind=kind, seed_count=len(existing), added_count=len(extras),
            mobilized_count=len(existing)+len(extras),
        )
        reviews.append({
            "kind":"strategic_mobilization_expanded","operation_ref":op_ref,
            "operation_kind":kind,"seed_count":len(existing),"added_count":len(extras),
            "mobilized_count":len(existing)+len(extras),
        })
    if changed_deployments:
        out = copy.deepcopy(dict(after)); out["deployments"] = rows; writes[_DEPLOYMENTS] = out
        writes[_COMMITMENTS] = commitments; writes[_HISTORY] = history
    return reviews


def _clear_dead_current_state(
    *, view: Callable[[str], Any], writes: dict[str, Any], dead: set[str],
    involved_factions: Sequence[str], at: datetime,
) -> None:
    if not dead:
        return
    try:
        commitments = copy.deepcopy(dict(view(_COMMITMENTS)))
        writes[_COMMITMENTS] = remove_people_from_commitments(commitments, person_refs=sorted(dead))
    except FileNotFoundError:
        pass
    try:
        social = copy.deepcopy(dict(view(_SOCIAL)))
        courtships = social.get("courtships", {}) if isinstance(social.get("courtships"), Mapping) else {}
        if isinstance(courtships, dict):
            for key in list(courtships):
                row = courtships.get(key); refs = row.get("person_refs", []) if isinstance(row, Mapping) else []
                if any(str(ref) in dead for ref in refs): courtships.pop(key, None)
        relationships = social.get("relationships", {}) if isinstance(social.get("relationships"), Mapping) else {}
        if isinstance(relationships, dict):
            for key in list(relationships):
                if any(part in dead for part in str(key).split("|")): relationships.pop(key, None)
        writes[_SOCIAL] = social
    except FileNotFoundError:
        pass
    try:
        custody = copy.deepcopy(dict(view(_CUSTODY))); records = custody.get("records", [])
        if isinstance(records, list):
            custody["records"] = [row for row in records if isinstance(row, Mapping) and str(row.get("person_ref")) not in dead and str(row.get("captor_ref")) not in dead]
            writes[_CUSTODY] = custody
    except FileNotFoundError:
        pass
    # Family death/succession remains handled by the existing annual/current
    # lifecycle. Offices are cleared immediately below so no dead person retains
    # current institutional authority between frontiers.
    for fid in sorted(set(str(x) for x in involved_factions if isinstance(x, str) and x)):
        faction = _load_faction(view, fid); roster = _load_roster(view, fid, faction)
        people = roster.get("people", []) if isinstance(roster.get("people"), list) else []
        changed = False; out_people: list[Any] = []
        for raw in people:
            if isinstance(raw, Mapping) and str(raw.get("person_id")) in dead and raw.get("standing_offices"):
                person = copy.deepcopy(dict(raw)); person["standing_offices"] = []; out_people.append(person); changed = True
            else: out_people.append(raw)
        if changed:
            roster["people"] = out_people
        faction = reconcile_faction_population(faction, roster)
        faction, _ = advance_faction_training_epoch(faction, roster, at_iso=at.isoformat(), refresh_environment=True)
        writes[faction_path(fid)] = compact_faction_state(faction)
        writes[roster_path(fid)] = compact_roster_state(roster, faction=faction)


def settle_faction_operation_arrivals(
    *, read_json: Callable[[str], Any], writes: dict[str, Any],
    events: Sequence[Mapping[str, Any]], at: datetime, schedule_after: Mapping[str, Any],
) -> dict[str, Any]:
    due = [row for row in events if isinstance(row, Mapping) and row.get("kind") == "faction_operation_arrival"]
    if not due:
        return {"writes":{},"reviews":[],"handoffs":[],"schedule_after":copy.deepcopy(dict(schedule_after))}
    view = _View(read_json, writes)
    deployments = copy.deepcopy(dict(view(_DEPLOYMENTS))); rows = deployments.setdefault("deployments", {})
    commitments = copy.deepcopy(dict(view(_COMMITMENTS)))
    equipment = hydrate_equipment_ledger(view(_EQUIPMENT))
    try: relations = copy.deepcopy(dict(view(_RELATIONS)))
    except FileNotFoundError: relations = {"edges":[]}
    try: history = copy.deepcopy(dict(view(_HISTORY)))
    except FileNotFoundError: history = {"recent":[],"counters":{},"totals":{}}
    sites = _site_rows(view)
    try:
        meta = view(_META); player_ref = str(meta.get("player_id") or "") if isinstance(meta, Mapping) else ""
    except FileNotFoundError: player_ref = ""
    schedule = copy.deepcopy(dict(schedule_after))
    reviews: list[dict[str, Any]] = []; handoffs: list[dict[str, Any]] = []
    frontier_used: set[str] = set()

    for event in sorted(due, key=lambda row: (str(row.get("owner_ref") or ""), str(row.get("event_id") or ""))):
        op_ref = str(event.get("owner_ref") or ""); op = rows.get(op_ref) if isinstance(rows, Mapping) else None
        if not isinstance(op, Mapping) or op.get("status") != "traveling_outbound":
            reviews.append({"kind":"faction_operation_arrival","event_id":event.get("event_id"),"result":"operation_not_active"}); continue
        fid = str(op.get("faction_ref") or ""); target_fid = str(op.get("target_faction_ref") or ""); kind = str(op.get("operation_kind") or "")
        if not fid or not target_fid or kind not in {"formal_challenge","faction_raid","faction_war_strike"}:
            reviews.append({"kind":"faction_operation_arrival","event_id":event.get("event_id"),"result":"operation_invalid"}); continue
        source_faction = _load_faction(view, fid); target_faction = _load_faction(view, target_fid)
        source_roster = _load_roster(view, fid, source_faction); target_roster = _load_roster(view, target_fid, target_faction)
        target_place = str(op.get("target_place_ref") or target_faction.get("headquarters") or "")
        target_site = _arrival_site(sites, target_place, str(target_faction.get("local_site_ref") or target_place))
        site = sites.get(target_site) if isinstance(sites, Mapping) else None
        participant_refs = [str(x) for x in op.get("participant_refs", []) if isinstance(x, str)]
        source_people = [copy.deepcopy(dict(p)) for p in source_roster.get("people", []) if isinstance(p, Mapping)]
        target_people = [copy.deepcopy(dict(p)) for p in target_roster.get("people", []) if isinstance(p, Mapping)]
        source_map = {str(p.get("person_id")):p for p in source_people if isinstance(p.get("person_id"),str)}
        target_map = {str(p.get("person_id")):p for p in target_people if isinstance(p.get("person_id"),str)}
        attacker_refs = [ref for ref in participant_refs if ref in source_map and _living(source_map[ref])]
        for ref in attacker_refs: source_map[ref]["location_ref"] = target_site
        index = commitments.get("person_index", {}) if isinstance(commitments.get("person_index"), Mapping) else {}
        blocked = {str(x) for x in index if isinstance(x,str)} | frontier_used | set(attacker_refs)
        if player_ref: blocked.add(player_ref)
        defenders = combat_ready_members(target_people, year=at.year, unavailable_refs=blocked, minimum_age=16)
        defenders = [p for p in defenders if _person_place(p,target_faction,sites)==target_place]
        defender_refs = [str(p.get("person_id")) for p in defenders if isinstance(p.get("person_id"),str)]
        master: dict[str, dict[str, Any]] = {ref:copy.deepcopy(person) for ref,person in source_map.items() if ref in attacker_refs}
        master.update({ref:copy.deepcopy(target_map[ref]) for ref in defender_refs})
        frontage = 1 if kind=="formal_challenge" else local_frontage_count(site if isinstance(site,Mapping) else None)
        doctrines = {
            fid: source_faction.get("doctrine",{}) if isinstance(source_faction.get("doctrine"),Mapping) else {},
            target_fid: target_faction.get("doctrine",{}) if isinstance(target_faction.get("doctrine"),Mapping) else {},
        }
        engaged: set[str] = set(); newly_dead: set[str] = set(); exchanges=0; contacts=0; outcome="uncontested" if not defender_refs else "contact"
        aggregate_winner: str|None = "side_a" if attacker_refs and not defender_refs else None
        while True:
            active_a=[ref for ref in attacker_refs if ref in master and _combat_active(master[ref],year=at.year)]
            active_b=[ref for ref in defender_refs if ref in master and _combat_active(master[ref],year=at.year)]
            active_a.sort(key=lambda ref:(-combat_readiness_score(master[ref],year=at.year),ref)); active_b.sort(key=lambda ref:(-combat_readiness_score(master[ref],year=at.year),ref))
            if not active_a: aggregate_winner="side_b" if active_b else aggregate_winner; outcome="attack_broken"; break
            if not active_b: aggregate_winner="side_a"; outcome="defense_broken"; break
            wave_a=active_a[:frontage]; wave_b=active_b[:frontage]; contacts+=1; engaged.update(wave_a); engaged.update(wave_b)
            before_dead={ref for ref,p in master.items() if not _living(p)}
            result=simulate_exact_combat(
                combat_ref=f"combat:{op_ref}:contact:{contacts}",side_a_refs=wave_a,side_b_refs=wave_b,
                people={ref:copy.deepcopy(master[ref]) for ref in wave_a+wave_b},equipment_ledger=equipment,doctrines=doctrines,
                zone_ref=target_site,started_at=at.isoformat(),
                objective={"kind":kind,"source_faction_ref":fid,"target_faction_ref":target_fid,"operation_ref":op_ref,"contact_index":contacts},
                targeting_intent="disable" if kind=="formal_challenge" else "lethal",
                max_exchanges=160 if kind=="formal_challenge" else (128 if kind=="faction_war_strike" else 96),
            )
            equipment=copy.deepcopy(dict(result["equipment_ledger_after"])); exchanges+=max(0,int(result.get("exchanges",0)))
            for ref,person in result.get("people_after",{}).items():
                if isinstance(ref,str) and isinstance(person,Mapping): master[ref]=copy.deepcopy(dict(person))
            newly_dead.update(ref for ref,p in master.items() if not _living(p) and ref not in before_dead)
            winner=result.get("winner_side")
            if not bool(result.get("resolved")):
                aggregate_winner=None; outcome="local_stalemate"; break
            if winner=="side_b": aggregate_winner="side_b"; outcome="attack_broken"; break
            if kind in {"formal_challenge","faction_raid"}:
                aggregate_winner=winner if isinstance(winner,str) else None; outcome="contact_complete"; break
            # War continues into another local contact only when the current
            # attacker frontage actually broke the defenders in front of it.
            if winner!="side_a": aggregate_winner=None; outcome="local_stalemate"; break

        for ref in attacker_refs:
            if ref in master: source_map[ref]=master[ref]
        for ref in defender_refs:
            if ref in master: target_map[ref]=master[ref]
        source_roster["people"]=[source_map.get(str(p.get("person_id")),p) if isinstance(p,Mapping) else p for p in source_people]
        target_roster["people"]=[target_map.get(str(p.get("person_id")),p) if isinstance(p,Mapping) else p for p in target_people]
        source_faction=reconcile_faction_population(source_faction,source_roster); target_faction=reconcile_faction_population(target_faction,target_roster)
        writes[faction_path(fid)]=compact_faction_state(source_faction); writes[roster_path(fid)]=compact_roster_state(source_roster,faction=source_faction)
        writes[faction_path(target_fid)]=compact_faction_state(target_faction); writes[roster_path(target_fid)]=compact_roster_state(target_roster,faction=target_faction)
        if newly_dead:
            commitments=remove_people_from_commitments(commitments,person_refs=sorted(newly_dead))
            post_view=_View(read_json,{**writes,_COMMITMENTS:commitments})
            _clear_dead_current_state(view=post_view,writes=writes,dead=newly_dead,involved_factions=(fid,target_fid),at=at)
            commitments=copy.deepcopy(dict(writes.get(_COMMITMENTS,commitments)))
            history=record_event(history,at=at.isoformat(),kind="combat_deaths",faction_ref=fid,target_faction_ref=target_fid,person_refs=sorted(newly_dead),count=len(newly_dead))
        relation_event="war_battle" if kind=="faction_war_strike" else ("armed_raid" if kind=="faction_raid" else "tournament_sportsmanship")
        relations=_apply_relation(relations,fid,target_fid,relation_event); relations=_apply_relation(relations,target_fid,fid,relation_event)
        if newly_dead:
            if any(ref in newly_dead for ref in attacker_refs): relations=_apply_relation(relations,fid,target_fid,"member_killed")
            if any(ref in newly_dead for ref in defender_refs): relations=_apply_relation(relations,target_fid,fid,"member_killed")
        surviving_attackers=[ref for ref in attacker_refs if ref not in newly_dead]
        if surviving_attackers:
            return_at=at+timedelta(hours=max(1.0,float(op.get("travel_hours",24.0))))
            current=copy.deepcopy(dict(op)); current["participant_refs"]=surviving_attackers; current["status"]="traveling_return"; current["return_arrival_at"]=return_at.isoformat()
            current["battle_force_count"]=len(attacker_refs); current["battle_defender_force_count"]=len(defender_refs); current["local_frontage_count"]=frontage
            rows[op_ref]=current
            schedule=upsert_one_off_event(schedule,{"event_id":f"operation_return:{op_ref}","kind":"faction_operation_return","due_at":return_at.isoformat(),"owner_ref":op_ref,"requires_player_decision":False})
        else:
            commitments=release_resources(commitments,activity_ref=op_ref); rows.pop(op_ref,None)
        history=record_event(
            history,at=at.isoformat(),kind=f"{kind}_contact",faction_ref=fid,target_faction_ref=target_fid,
            source_camp=faction_camp(fid) or "unclassified",target_camp=faction_camp(target_fid) or "unclassified",
            participant_count=len(attacker_refs),defender_force_count=len(defender_refs),engaged_count=len(engaged),
            local_frontage_count=frontage,contact_count=contacts,deaths=len(newly_dead),winner_side=aggregate_winner,
        )
        frontier_used.update(attacker_refs); frontier_used.update(defender_refs)
        review={"kind":"faction_operation_arrival","event_id":event.get("event_id"),"operation_ref":op_ref,"operation_kind":kind,"faction_ref":fid,"target_faction_ref":target_fid,"attacker_count":len(attacker_refs),"defender_force_count":len(defender_refs),"engaged_count":len(engaged),"local_frontage_count":frontage,"contact_count":contacts,"exchanges":exchanges,"winner_side":aggregate_winner,"deaths":len(newly_dead),"result":"returning" if surviving_attackers else "closed","battle_outcome":outcome}
        reviews.append(review)
        if target_fid=="house_tang":
            notice={**review,"kind":"faction_war_result" if kind=="faction_war_strike" else ("faction_attack_result" if kind=="faction_raid" else "faction_challenge_result"),"delivered_to_player":True,"requires_player_decision":False}
            handoffs.append({**notice,"handoff":classify_handoff(notice)})

    deployments["deployments"]=rows
    writes[_DEPLOYMENTS]=deployments; writes[_COMMITMENTS]=commitments; writes[_EQUIPMENT]=compact_equipment_ledger(equipment); writes[_RELATIONS]=relations; writes[_HISTORY]=history; writes[_SCHEDULER]=schedule
    return {"writes":writes,"reviews":reviews,"handoffs":handoffs,"schedule_after":schedule}


__all__=["expand_new_strategic_mobilizations","local_frontage_count","settle_faction_operation_arrivals"]
