"""Physical treaty support for strategic faction operations.

Treaties never grant combat modifiers. If an ally answers a call, exact members
are committed at their own headquarters, travel over registered routes, fight
under their own faction identity, and return physically afterward.
"""
from __future__ import annotations

import copy
import hashlib
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping

from .commitments import derived_commitment_state, reserve_resources
from .faction_relations import active_treaty_kinds
from .faction_registry import current_faction_refs
from .faction_state import compact_faction_state, faction_path, hydrate_faction_state, roster_path, with_derived_population
from .manpower import combat_ready_members
from .live_state import roster_person
from .person_state import compact_roster_state, hydrate_roster_state
from .scheduler import upsert_one_off_event
from .strategic_autonomy import stable_permille
from .training import institutional_training_pause_refs, settle_and_reset_faction_training_cycle
from .travel import travel_plan

_DEPLOYMENTS = "state/martial-world/deployments.json"
_RELATIONS = "state/martial-world/faction-relations.json"
_LOCAL_SITES = "game/data/martial-world/local-sites.json"
_META = "state/meta.json"


class _View:
    def __init__(self, read_json: Callable[[str], Any], writes: Mapping[str, Any]):
        self.read_json_fn = read_json; self.writes = writes
    def __call__(self, path: str) -> Any:
        if path in self.writes: return copy.deepcopy(self.writes[path])
        return copy.deepcopy(self.read_json_fn(path))
    def read_json(self, path: str) -> Any: return self(path)


def _faction(view: _View, fid: str) -> dict[str, Any]:
    faction = hydrate_faction_state(view(faction_path(fid)))
    return with_derived_population(faction, view(roster_path(fid)))


def _roster(view: _View, fid: str, faction: Mapping[str, Any]) -> dict[str, Any]:
    return hydrate_roster_state(view(roster_path(fid)), faction=faction)


def _person_place(person: Mapping[str, Any], faction: Mapping[str, Any], sites: Mapping[str, Any]) -> str:
    location = str(person.get("location_ref") or faction.get("local_site_ref") or faction.get("headquarters") or "")
    site = sites.get(location) if isinstance(sites, Mapping) else None
    return str(site.get("parent_place_ref") or location) if isinstance(site, Mapping) else location


def _player_faction_ref(view: _View) -> str:
    try:
        meta = view(_META)
        player_ref = str(meta.get("player_id") or "") if isinstance(meta, Mapping) else ""
        if not player_ref:
            return ""
        _path, _roster, _ordinal, person = roster_person(view, player_ref)
        return str(person.get("faction_ref") or "")
    except (FileNotFoundError, KeyError, TypeError, ValueError):
        return ""


def operation_warning_detected(*, operation_ref: str, operation: Mapping[str, Any], attacker: Mapping[str, Any], defender: Mapping[str, Any], at: datetime) -> bool:
    count = len([x for x in operation.get("participant_refs", []) if isinstance(x, str)])
    same_settlement = str(attacker.get("headquarters") or "") == str(defender.get("headquarters") or "")
    doctrine = attacker.get("doctrine", {}) if isinstance(attacker.get("doctrine"), Mapping) else {}
    stealth = max(0, min(100, int(doctrine.get("ambush_emphasis", 40))))
    kind = str(operation.get("operation_kind") or "")
    visibility = 170 + min(420, count * 14) + (360 if same_settlement else 0) + (180 if kind == "faction_war_strike" else 0) - stealth * 2
    visibility = max(50, min(980, visibility))
    roll = stable_permille("strategic-warning", operation_ref, str(operation.get("faction_ref") or ""), str(operation.get("target_faction_ref") or ""), at.isoformat())
    return roll < visibility


def _allied_parties(relations: Mapping[str, Any], defender_fid: str) -> list[str]:
    rows = relations.get("treaties", {}) if isinstance(relations, Mapping) else {}
    if not isinstance(rows, Mapping): return []
    allies: set[str] = set()
    for row in rows.values():
        if not isinstance(row, Mapping) or row.get("status", "active") != "active" or str(row.get("kind") or "") not in {"mutual_defense", "alliance"}:
            continue
        parties = [str(x) for x in row.get("party_faction_refs", []) if isinstance(x, str) and x]
        if len(parties) == 2 and defender_fid in parties:
            allies.add(parties[0] if parties[1] == defender_fid else parties[1])
    return sorted(allies)


def stage_defensive_calls_to_arms(
    *, read_json: Callable[[str], Any], writes: dict[str, Any], deployments: dict[str, Any], schedule: Mapping[str, Any],
    attack_ref: str, attack: Mapping[str, Any], at: datetime, world_seed: str,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Mobilize exact allied detachments if a defender actually detects an attack."""
    view = _View(read_json, writes)
    fid = str(attack.get("faction_ref") or ""); target = str(attack.get("target_faction_ref") or "")
    if not fid or not target or fid == target or str(attack.get("operation_kind") or "") not in {"faction_raid", "faction_war_strike"}:
        return deployments, copy.deepcopy(dict(schedule)), []
    try:
        attacker = _faction(view, fid); defender = _faction(view, target); relations = view(_RELATIONS)
    except (FileNotFoundError, KeyError, TypeError, ValueError):
        return deployments, copy.deepcopy(dict(schedule)), []
    if not operation_warning_detected(operation_ref=attack_ref, operation=attack, attacker=attacker, defender=defender, at=at):
        return deployments, copy.deepcopy(dict(schedule)), [{"kind":"defensive_call_to_arms","attack_ref":attack_ref,"result":"attack_not_detected"}]
    try:
        sites_doc = view(_LOCAL_SITES); sites = sites_doc.get("sites", {}) if isinstance(sites_doc, Mapping) else {}
    except FileNotFoundError:
        sites = {}
    schedule_out = copy.deepcopy(dict(schedule))
    rows = deployments.setdefault("deployments", {})
    if not isinstance(rows, dict): return deployments, schedule_out, []
    commitments = derived_commitment_state(view)
    busy = set(str(x) for x in commitments.get("person_index", {}) if isinstance(x, str)) if isinstance(commitments.get("person_index"), Mapping) else set()
    reviews: list[dict[str, Any]] = []
    target_place = str(defender.get("headquarters") or attack.get("target_place_ref") or "")
    target_site = str(defender.get("local_site_ref") or attack.get("target_site_ref") or "")
    projected_attack_arrival = str(attack.get("arrival_at") or at.isoformat())
    try: projected_dt = datetime.fromisoformat(projected_attack_arrival)
    except ValueError: projected_dt = at + timedelta(days=1)
    player_faction = _player_faction_ref(view)

    for ally in _allied_parties(relations, target):
        if ally in {fid, target}: continue
        if ally == player_faction:
            reviews.append({
                "kind": "defensive_call_to_arms", "attack_ref": attack_ref,
                "ally_faction_ref": ally, "defended_faction_ref": target,
                "attacker_faction_ref": fid, "result": "player_decision_required",
                "detected_at": at.isoformat(),
            })
            continue
        support_ref = "operation:allied_defense:" + hashlib.sha256(f"{attack_ref}|{ally}|{target}".encode()).hexdigest()[:20]
        if support_ref in rows:
            reviews.append({"kind":"defensive_call_to_arms","attack_ref":attack_ref,"ally_faction_ref":ally,"result":"already_mobilized"}); continue
        try:
            af = _faction(view, ally); ar = _roster(view, ally, af)
        except (FileNotFoundError, KeyError, TypeError, ValueError):
            continue
        source_place = str(af.get("headquarters") or "")
        if not source_place or not target_place:
            continue
        ready = combat_ready_members([p for p in ar.get("people", []) if isinstance(p, Mapping)], year=at.year, unavailable_refs=sorted(busy), minimum_age=16)
        ready = [p for p in ready if _person_place(p, af, sites) == source_place and not bool(p.get("retired_from_field", False))]
        ready.sort(key=lambda p: (-max([int(v) for v in (p.get("martial_skills", {}) or {}).values() if isinstance(v, (int,float))] or [0]), str(p.get("person_id") or "")))
        if not ready:
            reviews.append({"kind":"defensive_call_to_arms","attack_ref":attack_ref,"ally_faction_ref":ally,"result":"no_available_fighters"}); continue
        # Commit a doctrine-sized fraction, preserving a substantial home reserve.
        treaty = active_treaty_kinds(relations, ally, target)
        fraction_milli = 360 if "alliance" in treaty else 260
        desired = max(1, min(len(ready), (len(ready) * fraction_milli + 999) // 1000))
        if len(ready) > 2: desired = min(desired, len(ready) - max(1, len(ready)//2))
        participants = [str(p.get("person_id")) for p in ready[:desired] if isinstance(p.get("person_id"), str)]
        if not participants: continue
        try:
            reserve_resources(commitments, resources=[("person", ref, ally) for ref in participants], actor_ref=participants[0], owner_ref=ally, activity_ref=support_ref, activity_kind="allied_defense_reinforcement", started_at=at.isoformat(), location_ref=source_place)
        except ValueError:
            reviews.append({"kind":"defensive_call_to_arms","attack_ref":attack_ref,"ally_faction_ref":ally,"result":"fighters_became_unavailable"}); continue
        prep_roll = stable_permille("allied-defense-prep", support_ref, at.isoformat())
        departure = at + timedelta(hours=2 + prep_roll * 10 // 999)
        try:
            plan = travel_plan(world_seed=world_seed, start_at=departure, start=source_place, end=target_place, mode="foot") if source_place != target_place else {"edges": [], "travel_hours": 2.0, "arrival_at": (departure+timedelta(hours=2)).isoformat()}
        except (KeyError, ValueError):
            continue
        arrival_at = str(plan.get("arrival_at") or "")
        hold_until = (max(projected_dt, datetime.fromisoformat(arrival_at)) + timedelta(days=2)).isoformat()
        rows[support_ref] = {
            "faction_ref": ally, "target_faction_ref": target,
            "operation_kind": "allied_defense_reinforcement", "participant_refs": participants,
            "commander_ref": participants[0], "source_place_ref": source_place,
            "source_site_ref": str(af.get("local_site_ref") or ""), "target_place_ref": target_place,
            "target_site_ref": target_site, "started_at": at.isoformat(), "departure_at": departure.isoformat(),
            "arrival_at": arrival_at, "travel_hours": float(plan.get("travel_hours",0.0)), "route_refs": list(plan.get("edges",[])),
            "status": "mobilizing", "arrival_event_kind": "faction_operation_arrival",
            "supporting_operation_ref": attack_ref, "support_target_faction_ref": target, "hold_until": hold_until,
            "mobilization_basis": "treaty_exact_detachment", "targeting_intent": "disable", "operation_intent": "mutual_defense",
        }
        busy.update(participants)
        paused = institutional_training_pause_refs(af, [p for p in ar.get("people",[]) if isinstance(p,Mapping)], unavailable_refs=sorted(busy))
        af, ar, _ = settle_and_reset_faction_training_cycle(af, ar, at_iso=at.isoformat(), paused_refs=paused)
        writes[faction_path(ally)] = compact_faction_state(af); writes[roster_path(ally)] = compact_roster_state(ar, faction=af)
        schedule_out = upsert_one_off_event(schedule_out, {"event_id":f"operation_departure:{support_ref}","kind":"faction_operation_departure","due_at":departure.isoformat(),"owner_ref":support_ref,"direction":"outbound","arrival_event_kind":"faction_operation_arrival","requires_player_decision":False})
        reviews.append({"kind":"defensive_call_to_arms","attack_ref":attack_ref,"ally_faction_ref":ally,"support_operation_ref":support_ref,"participant_count":len(participants),"result":"mobilizing"})
    deployments["deployments"] = rows
    return deployments, schedule_out, reviews



def _directed_edge(relations: Mapping[str, Any], source: str, target: str) -> Mapping[str, Any]:
    edges = relations.get("edges", []) if isinstance(relations, Mapping) else []
    if not isinstance(edges, list):
        return {}
    return next((row for row in edges if isinstance(row, Mapping) and str(row.get("from_faction") or "") == source and str(row.get("to_faction") or "") == target), {})


def stage_offensive_support_request(
    *, read_json: Callable[[str], Any], writes: dict[str, Any], deployments: dict[str, Any], schedule: Mapping[str, Any],
    parent_operation_ref: str, parent_operation: Mapping[str, Any], requester_faction_ref: str,
    ally_faction_ref: str, at: datetime, world_seed: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Evaluate one alliance request and, if accepted, mobilize a separate physical allied strike.

    An alliance makes an offensive request possible, not compulsory. Acceptance
    is deterministic from the current relationship and risk. The allied party
    remains its own deployment owner and never owns/closes the requester's
    institutional mission dossier.
    """
    view = _View(read_json, writes)
    schedule_out = copy.deepcopy(dict(schedule))
    rows = deployments.setdefault("deployments", {})
    if not isinstance(rows, dict):
        return deployments, schedule_out, {"status": "refused", "reason": "deployment_registry_invalid"}
    try:
        relations = view(_RELATIONS)
    except FileNotFoundError:
        return deployments, schedule_out, {"status": "refused", "reason": "relations_unavailable"}
    if "alliance" not in active_treaty_kinds(relations, requester_faction_ref, ally_faction_ref):
        return deployments, schedule_out, {"status": "refused", "reason": "no_alliance"}
    target = str(parent_operation.get("target_faction_ref") or "")
    if not target or target in {requester_faction_ref, ally_faction_ref}:
        return deployments, schedule_out, {"status": "refused", "reason": "invalid_target"}
    try:
        af = _faction(view, ally_faction_ref)
        ar = _roster(view, ally_faction_ref, af)
        target_faction = _faction(view, target)
    except (FileNotFoundError, KeyError, TypeError, ValueError):
        return deployments, schedule_out, {"status": "refused", "reason": "faction_unresolved"}
    edge = _directed_edge(relations, ally_faction_ref, requester_faction_ref)
    toward_target = _directed_edge(relations, ally_faction_ref, target)
    trust = int(edge.get("trust", 0)); obligation = int(edge.get("obligation", 0)); respect = int(edge.get("respect", 0))
    target_hostility = int(toward_target.get("hostility", 0))
    external_aggression = int((af.get("autonomy_policy", {}) if isinstance(af.get("autonomy_policy"), Mapping) else {}).get("external_aggression", 50))
    accept_milli = max(80, min(940, 310 + trust * 5 + obligation * 3 + respect * 2 + target_hostility * 3 + external_aggression * 2))
    roll = stable_permille("offensive-alliance-response", parent_operation_ref, requester_faction_ref, ally_faction_ref, target, at.isoformat())
    if roll >= accept_milli:
        return deployments, schedule_out, {"status": "refused", "reason": "strategic_refusal", "accept_milli": accept_milli}
    try:
        sites_doc = view(_LOCAL_SITES); sites = sites_doc.get("sites", {}) if isinstance(sites_doc, Mapping) else {}
    except FileNotFoundError:
        sites = {}
    source_place = str(af.get("headquarters") or "")
    target_place = str(target_faction.get("headquarters") or parent_operation.get("target_place_ref") or "")
    target_site = str(target_faction.get("local_site_ref") or parent_operation.get("target_site_ref") or "")
    commitments = derived_commitment_state(view)
    busy = set(str(x) for x in commitments.get("person_index", {}) if isinstance(x, str)) if isinstance(commitments.get("person_index"), Mapping) else set()
    ready = combat_ready_members([p for p in ar.get("people", []) if isinstance(p, Mapping)], year=at.year, unavailable_refs=sorted(busy), minimum_age=16)
    ready = [p for p in ready if _person_place(p, af, sites) == source_place and not bool(p.get("retired_from_field", False))]
    ready.sort(key=lambda p: (-max([int(v) for v in (p.get("martial_skills", {}) or {}).values() if isinstance(v, (int, float))] or [0]), str(p.get("person_id") or "")))
    if not ready or not source_place or not target_place:
        return deployments, schedule_out, {"status": "refused", "reason": "no_available_force"}
    parent_count = len([x for x in parent_operation.get("participant_refs", []) if isinstance(x, str) and x])
    desired = max(1, min(len(ready), max(1, parent_count // 2)))
    if len(ready) > 2:
        desired = min(desired, len(ready) - max(1, len(ready) // 2))
    participants = [str(p.get("person_id")) for p in ready[:desired] if isinstance(p.get("person_id"), str)]
    support_ref = "operation:allied_offense:" + hashlib.sha256(f"{parent_operation_ref}|{ally_faction_ref}|{target}".encode()).hexdigest()[:20]
    if support_ref in rows:
        return deployments, schedule_out, {"status": "accepted", "support_operation_ref": support_ref, "participant_count": len(rows[support_ref].get("participant_refs", [])), "result": "already_mobilized"}
    try:
        reserve_resources(commitments, resources=[("person", ref, ally_faction_ref) for ref in participants], actor_ref=participants[0], owner_ref=ally_faction_ref, activity_ref=support_ref, activity_kind=str(parent_operation.get("operation_kind") or "faction_raid"), started_at=at.isoformat(), location_ref=source_place)
    except ValueError:
        return deployments, schedule_out, {"status": "refused", "reason": "fighters_became_unavailable"}
    prep_roll = stable_permille("allied-offense-prep", support_ref, at.isoformat())
    departure = at + timedelta(hours=4 + prep_roll * 20 // 999)
    try:
        plan = travel_plan(world_seed=world_seed, start_at=departure, start=source_place, end=target_place, mode="foot") if source_place != target_place else {"edges": [], "travel_hours": 2.0, "arrival_at": (departure + timedelta(hours=2)).isoformat()}
    except (KeyError, ValueError):
        return deployments, schedule_out, {"status": "refused", "reason": "route_unavailable"}
    kind = str(parent_operation.get("operation_kind") or "faction_raid")
    if kind not in {"faction_raid", "faction_war_strike"}:
        kind = "faction_raid"
    rows[support_ref] = {
        "faction_ref": ally_faction_ref, "target_faction_ref": target, "operation_kind": kind,
        "participant_refs": participants, "commander_ref": participants[0], "source_place_ref": source_place,
        "source_site_ref": str(af.get("local_site_ref") or ""), "target_place_ref": target_place,
        "target_site_ref": target_site, "started_at": at.isoformat(), "departure_at": departure.isoformat(),
        "arrival_at": str(plan.get("arrival_at") or ""), "travel_hours": float(plan.get("travel_hours", 0.0)),
        "route_refs": list(plan.get("edges", [])), "status": "mobilizing", "arrival_event_kind": "faction_operation_arrival",
        "supporting_institutional_operation_ref": str(parent_operation.get("institutional_operation_ref") or parent_operation_ref),
        "supporting_operation_ref": parent_operation_ref, "mobilization_basis": "alliance_exact_detachment",
        "targeting_intent": str(parent_operation.get("targeting_intent") or "disable"), "operation_intent": "allied_offensive_support",
    }
    schedule_out = upsert_one_off_event(schedule_out, {"event_id": f"operation_departure:{support_ref}", "kind": "faction_operation_departure", "due_at": departure.isoformat(), "owner_ref": support_ref, "direction": "outbound", "arrival_event_kind": "faction_operation_arrival", "requires_player_decision": False})
    paused = institutional_training_pause_refs(af, [p for p in ar.get("people", []) if isinstance(p, Mapping)], unavailable_refs=sorted(busy | set(participants)))
    af, ar, _ = settle_and_reset_faction_training_cycle(af, ar, at_iso=at.isoformat(), paused_refs=paused)
    writes[faction_path(ally_faction_ref)] = compact_faction_state(af); writes[roster_path(ally_faction_ref)] = compact_roster_state(ar, faction=af)
    deployments["deployments"] = rows
    return deployments, schedule_out, {"status": "accepted", "support_operation_ref": support_ref, "participant_count": len(participants), "departure_at": departure.isoformat(), "arrival_at": str(plan.get("arrival_at") or "")}


__all__ = ["operation_warning_detected", "stage_defensive_calls_to_arms", "stage_offensive_support_request"]
