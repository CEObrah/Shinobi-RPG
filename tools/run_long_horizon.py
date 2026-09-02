#!/usr/bin/env python3
"""Disposable resumable Jianghu long-horizon simulation.

The canonical repository is never mutated.  A checkpoint stores only a disposable
logical overlay plus the scheduler cursor so expensive release soaks can resume
across bounded execution windows without skipping any causal work.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import sys
import time
import os
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from shinobi_runtime.martial_world.scheduler import due_events, sync_faction_activity
from shinobi_runtime.martial_world.time_progression import settle_martial_world_frontier
from shinobi_runtime.martial_world.commitments import derived_commitment_state
from shinobi_runtime.martial_world.faction_politics import conflict_stage, faction_camp
from shinobi_runtime.martial_world.faction_registry import current_faction_refs
from shinobi_runtime.store import RepositoryStore, RegisteredSchemaValidator, RegisteredTemplateValidator

_CHECKPOINT_SCHEMA = "jianghu-long-horizon-checkpoint-1.2"
_CLOSED_CONTRACT = {"settled", "failed", "expired", "cancelled"}
_ACTIVE_CHECKPOINT_LOCKS: list[Any] = []


def _acquire_checkpoint_lock(path: Path | None) -> None:
    """Hold a non-blocking single-writer lock for one resumable checkpoint.

    The release verifier is intentionally resumable across bounded shell windows.
    If an outer runner returns before its child process actually exits, a second
    resume must fail closed rather than racing on the same checkpoint or its
    atomic temporary file.  The operating system releases the advisory lock on
    process exit, including abnormal termination.
    """
    if path is None:
        return
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise SystemExit(f"checkpoint already in use: {path}") from exc
    handle.seek(0)
    handle.truncate()
    handle.write(f"pid={os.getpid()}\n")
    handle.flush()
    _ACTIVE_CHECKPOINT_LOCKS.append(handle)


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


_VALIDATION_REPOSITORY = RepositoryStore(ROOT)
_SCHEMA_VALIDATOR = RegisteredSchemaValidator(_VALIDATION_REPOSITORY)
_TEMPLATE_VALIDATOR = RegisteredTemplateValidator(_VALIDATION_REPOSITORY)


def _validate_frontier_writes(writes: Mapping[str, Mapping[str, Any]]) -> None:
    """Fail the soak at the exact frontier that emits an inadmissible state owner.

    Production transactions enforce both registered JSON schemas and closed
    structural templates.  The disposable long-horizon runner must enforce the
    same admission contract or it can falsely certify a world after-image that
    production would refuse to commit.
    """
    for rel, doc in sorted(writes.items()):
        if not isinstance(rel, str) or not rel.startswith("state/") or not rel.endswith(".json"):
            continue
        if not isinstance(doc, Mapping):
            raise ValueError(f"long-horizon staged state owner is not an object: {rel}")
        schema_id = doc.get("schema")
        if not isinstance(schema_id, str) or schema_id not in _SCHEMA_VALIDATOR.validators:
            raise ValueError(f"long-horizon staged state owner has unregistered schema: {rel}")

        # Match production schema admission, including nested schema-bearing
        # records rather than validating only the top-level owner.
        stack: list[Any] = [doc]
        while stack:
            current = stack.pop()
            if isinstance(current, Mapping):
                nested_schema = current.get("schema")
                if nested_schema is not None:
                    if not isinstance(nested_schema, str) or nested_schema not in _SCHEMA_VALIDATOR.validators:
                        raise ValueError(f"long-horizon staged JSON uses an unregistered schema: {nested_schema!r}")
                    _SCHEMA_VALIDATOR.validators[nested_schema].validate(current)
                stack.extend(current.values())
            elif isinstance(current, list):
                stack.extend(current)

        template = _TEMPLATE_VALIDATOR.templates.get(schema_id)
        if template is None or _TEMPLATE_VALIDATOR.scopes.get(schema_id) != "mutable_state":
            raise ValueError(f"long-horizon staged state owner has no mutable template: {schema_id}")
        RegisteredTemplateValidator._validate_document(doc, template, label=rel)


def _write(path: Path, value: Any) -> None:
    """Atomically persist a disposable verifier artifact.

    Long-horizon checkpoints can be several megabytes.  The outer execution
    environment may terminate a verifier between frontiers, so never expose a
    partially-written checkpoint as resumable truth.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _state_digest(overlay: Mapping[str, Mapping[str, Any]]) -> str:
    h = hashlib.sha256()
    disk_paths = {p.relative_to(ROOT).as_posix() for p in (ROOT / "state").rglob("*.json")}
    paths = sorted(disk_paths | {p for p in overlay if p.startswith("state/") and p.endswith(".json")})
    for rel in paths:
        doc = overlay.get(rel)
        if doc is None:
            doc = _read(ROOT / rel)
        h.update(rel.encode("utf-8")); h.update(b"\0"); h.update(_canonical_bytes(doc)); h.update(b"\n")
    return h.hexdigest()


def _person_metrics(overlay: Mapping[str, Mapping[str, Any]]) -> dict[str, int]:
    count = 0
    training_state_records = 0
    training_carry_records = 0
    living_faction = 0
    dead_faction = 0
    living_independent = 0
    dead_independent = 0
    living_civic = 0
    dead_civic = 0
    ids: set[str] = set()
    duplicate_ids = 0
    for path in sorted((ROOT / "state/martial-world/people").glob("*.json")):
        rel = path.relative_to(ROOT).as_posix()
        doc = overlay.get(rel) or _read(path)
        for person in doc.get("people", []):
            if not isinstance(person, Mapping):
                continue
            count += 1
            health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
            if health.get("status") == "dead":
                dead_faction += 1
            else:
                living_faction += 1
            if "training_state" in person:
                training_state_records += 1
            if "training_carry_milli" in person:
                training_carry_records += 1
            ref = person.get("person_id")
            if isinstance(ref, str):
                if ref in ids:
                    duplicate_ids += 1
                ids.add(ref)
    independent_rel = "state/martial-world/independent-people.json"
    independent = overlay.get(independent_rel)
    if independent is None:
        independent = _read(ROOT / independent_rel)
    for person in independent.get("people", []) if isinstance(independent, Mapping) else []:
        if not isinstance(person, Mapping):
            continue
        count += 1
        health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
        if health.get("status") == "dead": dead_independent += 1
        else: living_independent += 1
        ref = person.get("person_id")
        if isinstance(ref, str):
            if ref in ids: duplicate_ids += 1
            ids.add(ref)

    civic_rel = "state/martial-world/civic-people.json"
    civic = overlay.get(civic_rel)
    if civic is None:
        civic = _read(ROOT / civic_rel)
    for person in civic.get("people", []) if isinstance(civic, Mapping) else []:
        if not isinstance(person, Mapping):
            continue
        count += 1
        health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
        if health.get("status") == "dead": dead_civic += 1
        else: living_civic += 1
        ref = person.get("person_id")
        if isinstance(ref, str):
            if ref in ids: duplicate_ids += 1
            ids.add(ref)
    return {
        "people": count,
        "living_faction_members": living_faction,
        "dead_faction_identities": dead_faction,
        "living_independents": living_independent,
        "dead_independent_identities": dead_independent,
        "living_civic_people": living_civic,
        "dead_civic_identities": dead_civic,
        "personal_training_state_records": training_state_records,
        "personal_training_carry_records": training_carry_records,
        "duplicate_person_ids": duplicate_ids,
    }


def _tracked_cash_metrics(overlay: Mapping[str, Mapping[str, Any]]) -> dict[str, int]:
    """Return the complete explicitly tracked silver authorities.

    Currency is conserved across faction treasuries, faction/independent/civic personal purses, regional
    market cash, government bounty escrow, contract escrow, tournament prize escrow, faction-sponsored
    competitor entry/host-city reserves, and delegation host-city/ticket reserves
    held by active travel deployments. Dead people still own their purse until a
    separate lawful estate transfer occurs.
    """
    def doc(rel: str) -> Mapping[str, Any]:
        row = overlay.get(rel)
        if isinstance(row, Mapping):
            return row
        return _read(ROOT / rel)

    faction_cash = 0
    for path in sorted((ROOT / "state/martial-world/factions").glob("*.json")):
        row = doc(path.relative_to(ROOT).as_posix())
        faction_cash += max(0, int(row.get("treasury_cash", 0)))

    faction_personal_cash = 0
    for path in sorted((ROOT / "state/martial-world/people").glob("*.json")):
        row = doc(path.relative_to(ROOT).as_posix())
        for person in row.get("people", []) if isinstance(row, Mapping) else []:
            if isinstance(person, Mapping):
                faction_personal_cash += max(0, int(person.get("personal_cash", 0)))
    independent_personal_cash = 0
    independent = doc("state/martial-world/independent-people.json")
    for person in independent.get("people", []) if isinstance(independent, Mapping) else []:
        if isinstance(person, Mapping):
            independent_personal_cash += max(0, int(person.get("personal_cash", 0)))
    civic_personal_cash = 0
    civic = doc("state/martial-world/civic-people.json")
    for person in civic.get("people", []) if isinstance(civic, Mapping) else []:
        if isinstance(person, Mapping):
            civic_personal_cash += max(0, int(person.get("personal_cash", 0)))
    personal_cash = faction_personal_cash + independent_personal_cash + civic_personal_cash

    market_cash = 0
    for path in sorted((ROOT / "state/martial-world/markets").glob("*.json")):
        row = doc(path.relative_to(ROOT).as_posix())
        market_cash += max(0, int(row.get("cash_pool", 0)))

    government_bounty_escrow = 0
    government = doc("state/martial-world/government.json")
    warrants = government.get("warrants", {}) if isinstance(government, Mapping) else {}
    if isinstance(warrants, Mapping):
        for row in warrants.values():
            if isinstance(row, Mapping):
                government_bounty_escrow += max(0, int(row.get("bounty_escrow_cash", 0)))

    contract_escrow = 0
    contracts = doc("state/martial-world/contracts/index.json")
    for row in contracts.get("active", {}).values() if isinstance(contracts, Mapping) else []:
        if isinstance(row, Mapping):
            contract_escrow += max(0, int(row.get("escrow_cash", 0)))

    tournament_prize_escrow = 0
    tournament_state = doc("state/martial-world/tournaments.json")
    for row in tournament_state.get("tournaments", {}).values() if isinstance(tournament_state, Mapping) else []:
        if isinstance(row, Mapping):
            tournament_prize_escrow += max(0, int(row.get("prize_escrow_cash", 0)))

    tournament_entry_reservations = 0
    tournament_host_spend_reservations = 0
    tournament_delegate_ticket_reservations = 0
    raid_cash_in_deployments = 0
    deployments = doc("state/martial-world/deployments.json")
    for row in deployments.get("deployments", {}).values() if isinstance(deployments, Mapping) else []:
        if not isinstance(row, Mapping):
            continue
        # A successful robbery/extortion first debits the target treasury and
        # carries that exact silver on the active strategic operation. It is
        # still conserved cash while the raiders prepare their physical return,
        # not an economic sink merely because it is between two treasuries.
        raid_cash_in_deployments += max(0, int(row.get("seized_cash", 0)))
        operation_kind = str(row.get("operation_kind") or "")
        if operation_kind == "tournament_travel":
            tournament_entry_reservations += max(0, int(row.get("entry_fee_reserved_cash", 0)))
            tournament_host_spend_reservations += max(0, int(row.get("host_spend_reserved_cash", 0)))
        elif operation_kind == "tournament_delegation":
            tournament_host_spend_reservations += max(0, int(row.get("host_spend_reserved_cash", 0)))
            tournament_delegate_ticket_reservations += max(0, int(row.get("delegate_ticket_reserved_cash", 0)))

    raid_cash_in_route = 0
    route_ops = doc("state/martial-world/route-operations.json")
    for row in route_ops.get("movements", {}).values() if isinstance(route_ops, Mapping) else []:
        if not isinstance(row, Mapping):
            continue
        # Once the return journey physically starts, strategic raid cash leaves
        # the deployment and becomes cargo on the route movement. Count that
        # current in-transit authority until arrival credits the beneficiary.
        raid_cash_in_route += max(0, int(row.get("cash_quantity", 0)))

    total = (
        faction_cash + personal_cash + market_cash + government_bounty_escrow + contract_escrow
        + tournament_prize_escrow + tournament_entry_reservations
        + tournament_host_spend_reservations
        + tournament_delegate_ticket_reservations
        + raid_cash_in_deployments + raid_cash_in_route
    )
    return {
        "faction_treasury": faction_cash,
        "personal_cash": personal_cash,
        "faction_personal_cash": faction_personal_cash,
        "independent_personal_cash": independent_personal_cash,
        "civic_personal_cash": civic_personal_cash,
        "market_cash": market_cash,
        "government_bounty_escrow": government_bounty_escrow,
        "contract_escrow": contract_escrow,
        "tournament_prize_escrow": tournament_prize_escrow,
        "tournament_entry_reservations": tournament_entry_reservations,
        "tournament_host_spend_reservations": tournament_host_spend_reservations,
        "tournament_delegate_ticket_reservations": tournament_delegate_ticket_reservations,
        "raid_cash_in_deployments": raid_cash_in_deployments,
        "raid_cash_in_route": raid_cash_in_route,
        "total": total,
    }


def _grade_counts(overlay: Mapping[str, Mapping[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for path in sorted((ROOT / "state/martial-world/people").glob("*.json")):
        rel = path.relative_to(ROOT).as_posix()
        row = overlay.get(rel) or _read(path)
        for person in row.get("people", []) if isinstance(row, Mapping) else []:
            if not isinstance(person, Mapping):
                continue
            health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
            if health.get("status") == "dead":
                continue
            grade = str(person.get("membership_grade") or "unknown")
            counts[grade] += 1
    return dict(sorted(counts.items()))


def _economic_health(overlay: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    def doc(rel: str) -> Mapping[str, Any]:
        row = overlay.get(rel)
        if isinstance(row, Mapping):
            return row
        return _read(ROOT / rel)

    zero_treasury = 0
    low_cash = 0
    no_food = 0
    under_30_food_days = 0
    cash_values: list[int] = []
    food_days: list[int] = []
    for path in sorted((ROOT / "state/martial-world/factions").glob("*.json")):
        rel = path.relative_to(ROOT).as_posix()
        row = doc(rel)
        fid = str(row.get("faction_id") or path.stem)
        cash = max(0, int(row.get("treasury_cash", 0)))
        cash_values.append(cash)
        if cash == 0:
            zero_treasury += 1
        if cash < 1000:
            low_cash += 1
        inv_rel = f"state/martial-world/inventories/{fid}.json"
        try:
            inv = doc(inv_rel)
        except FileNotFoundError:
            continue
        people_rel = f"state/martial-world/people/{fid}.json"
        try:
            people = doc(people_rel)
        except FileNotFoundError:
            continue
        living = sum(
            1 for person in people.get("people", [])
            if isinstance(person, Mapping)
            and not (isinstance(person.get("health"), Mapping) and person["health"].get("status") == "dead")
        )
        reserve_days = max(0, int(inv.get("food_ration_days", 0))) // max(1, living)
        food_days.append(reserve_days)
        if int(inv.get("food_ration_days", 0)) <= 0:
            no_food += 1
        if reserve_days < 30:
            under_30_food_days += 1

    def percentile(values: list[int], numerator: int, denominator: int = 100) -> int:
        if not values:
            return 0
        rows = sorted(values)
        idx = min(len(rows) - 1, max(0, (len(rows) - 1) * numerator // denominator))
        return int(rows[idx])

    return {
        "zero_treasury_factions": zero_treasury,
        "treasury_below_1000_factions": low_cash,
        "zero_food_factions": no_food,
        "food_reserve_under_30_days_factions": under_30_food_days,
        "treasury_cash_p10": percentile(cash_values, 10),
        "treasury_cash_median": percentile(cash_values, 50),
        "food_reserve_days_p10": percentile(food_days, 10),
        "food_reserve_days_median": percentile(food_days, 50),
    }


def _world_activity_metrics(
    overlay: Mapping[str, Mapping[str, Any]],
    *,
    review_kinds: Mapping[str, Any],
    activity_kinds: Mapping[str, Any],
) -> dict[str, Any]:
    def doc(rel: str) -> Mapping[str, Any]:
        row = overlay.get(rel)
        if isinstance(row, Mapping):
            return row
        return _read(ROOT / rel)

    routeops = doc("state/martial-world/route-operations.json")
    movement_kinds: Counter[str] = Counter()
    trade_legs: Counter[str] = Counter()
    for movement in routeops.get("movements", {}).values() if isinstance(routeops.get("movements"), Mapping) else []:
        if not isinstance(movement, Mapping):
            continue
        movement_kinds[str(movement.get("movement_kind") or "unknown")] += 1
        if movement.get("movement_kind") == "merchant_trade":
            trade_legs[str(movement.get("trade_leg") or "unknown")] += 1
    relations = doc("state/martial-world/faction-relations.json")
    pair_hostility: dict[tuple[str, str], int] = {}
    for edge in relations.get("edges", []) if isinstance(relations, Mapping) else []:
        if not isinstance(edge, Mapping):
            continue
        source = str(edge.get("from_faction") or ""); target = str(edge.get("to_faction") or "")
        if not source or not target or source == target:
            continue
        pair = tuple(sorted((source, target)))
        pair_hostility[pair] = max(pair_hostility.get(pair, 0), max(0, int(edge.get("hostility", 0))))
    conflict_stages: Counter[str] = Counter()
    war_camp_matchups: Counter[str] = Counter()
    for (a, b), hostility in pair_hostility.items():
        stage = conflict_stage({"hostility": hostility})
        conflict_stages[stage] += 1
        if stage == "war":
            ca = faction_camp(a) or "unclassified"; cb = faction_camp(b) or "unclassified"
            war_camp_matchups["|".join(sorted((ca, cb)))] += 1
    return {
        # Release telemetry is collected by this disposable verifier, not
        # written into the campaign.  Current owners remain the only gameplay
        # truth while the soak can still prove that the world is doing work.
        "review_kinds": dict(sorted((str(k), int(v)) for k, v in review_kinds.items() if isinstance(v, int))),
        "activity_kinds": dict(sorted((str(k), int(v)) for k, v in activity_kinds.items() if isinstance(v, int))),
        "semantic_activity_count": sum(int(v) for v in activity_kinds.values() if isinstance(v, int)),
        "active_route_movement_kinds": dict(sorted(movement_kinds.items())),
        "active_merchant_trade_legs": dict(sorted(trade_legs.items())),
        "conflict_stage_pairs": dict(sorted(conflict_stages.items())),
        "active_war_camp_matchups": dict(sorted(war_camp_matchups.items())),
    }


def _projected_state_bytes(overlay: Mapping[str, Mapping[str, Any]], before_bytes: int) -> int:
    total = int(before_bytes)
    for rel, doc in overlay.items():
        if not rel.startswith("state/") or not rel.endswith(".json"):
            continue
        old = (ROOT / rel).stat().st_size if (ROOT / rel).exists() else 0
        new = len((json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        total += new - old
    return total


def _nonnegative_tree(value: Any) -> bool:
    if isinstance(value, Mapping):
        return all(_nonnegative_tree(v) for v in value.values())
    if isinstance(value, list):
        return all(_nonnegative_tree(v) for v in value)
    if isinstance(value, bool):
        return True
    if isinstance(value, int):
        return value >= 0
    return True


def _conservation_checks(
    overlay: Mapping[str, Mapping[str, Any]],
    *,
    before_parentage: int,
    people_before: int,
    before_tracked_cash: int,
    civilian_recruits: int,
) -> dict[str, Any]:
    def doc(rel: str) -> Mapping[str, Any]:
        return overlay.get(rel) or _read(ROOT / rel)

    errors: list[str] = []
    treasury_negative: list[str] = []
    inventory_negative: list[str] = []
    market_negative: list[str] = []
    contract_negative: list[str] = []

    for p in sorted((ROOT / "state/martial-world/factions").glob("*.json")):
        rel = p.relative_to(ROOT).as_posix(); row = doc(rel)
        if int(row.get("treasury_cash", 0)) < 0:
            treasury_negative.append(str(row.get("faction_id") or rel))
    for p in sorted((ROOT / "state/martial-world/inventories").glob("*.json")):
        rel = p.relative_to(ROOT).as_posix(); row = doc(rel)
        if not _nonnegative_tree({k: v for k, v in row.items() if k not in {"schema", "faction_ref"}}):
            inventory_negative.append(rel)
    for p in sorted((ROOT / "state/martial-world/markets").glob("*.json")):
        rel = p.relative_to(ROOT).as_posix(); row = doc(rel)
        if int(row.get("cash_pool", 0)) < 0 or not _nonnegative_tree(row.get("stock", {})):
            market_negative.append(rel)

    contracts = doc("state/martial-world/contracts/index.json")
    for cid, row in contracts.get("active", {}).items():
        if isinstance(row, Mapping) and int(row.get("escrow_cash", 0)) < 0:
            contract_negative.append(str(cid))

    person_metrics = _person_metrics(overlay)
    family = doc("state/martial-world/family.json")
    parentage_after = len(family.get("parentage", {})) if isinstance(family.get("parentage"), Mapping) else 0
    # A new persistent identity has only two lawful creation paths in this
    # simulation: materialization from an aggregate civilian pool, or birth.
    # Birth itself is proved by the family owner adding the child's parentage
    # record, so the verifier does not need a gameplay history journal.
    births = max(0, parentage_after - int(before_parentage))
    civilian_recruits = max(0, int(civilian_recruits))
    expected_people_delta = civilian_recruits + births
    actual_people_delta = int(person_metrics["people"]) - int(people_before)
    if actual_people_delta != expected_people_delta:
        errors.append(f"people delta {actual_people_delta} != civilian recruitment + birth growth {expected_people_delta}")
    if person_metrics["duplicate_person_ids"]:
        errors.append(f"duplicate person ids: {person_metrics['duplicate_person_ids']}")
    if treasury_negative:
        errors.append(f"negative faction treasury: {treasury_negative[:8]}")
    if inventory_negative:
        errors.append(f"negative faction inventory: {inventory_negative[:8]}")
    if market_negative:
        errors.append(f"negative regional market stock/cash: {market_negative[:8]}")
    if contract_negative:
        errors.append(f"negative contract escrow: {contract_negative[:8]}")

    tracked_cash = _tracked_cash_metrics(overlay)
    tracked_cash_delta = int(tracked_cash["total"]) - int(before_tracked_cash)
    if tracked_cash_delta != 0:
        errors.append(f"tracked silver delta {tracked_cash_delta} (before={before_tracked_cash}, after={tracked_cash['total']})")

    return {
        "status": "PASS" if not errors else "FAIL",
        "people_delta_matches_materialization": actual_people_delta == expected_people_delta,
        "expected_people_delta_from_civilian_recruitment_and_births": expected_people_delta,
        "civilian_recruits": civilian_recruits,
        "births": births,
        "actual_people_delta": actual_people_delta,
        "duplicate_person_ids": person_metrics["duplicate_person_ids"],
        "negative_treasury_count": len(treasury_negative),
        "negative_inventory_count": len(inventory_negative),
        "negative_market_count": len(market_negative),
        "negative_contract_escrow_count": len(contract_negative),
        "tracked_cash_before": int(before_tracked_cash),
        "tracked_cash_after": tracked_cash,
        "tracked_cash_delta": tracked_cash_delta,
        "tracked_cash_conserved": tracked_cash_delta == 0,
        "errors": errors,
    }


def _new_checkpoint(days: int) -> dict[str, Any]:
    schedule = _read(ROOT / "state/martial-world/scheduler.json")
    start = datetime.fromisoformat(str(schedule["settled_through"]))
    family = _read(ROOT / "state/martial-world/family.json")
    people = _person_metrics({})
    return {
        "schema": _CHECKPOINT_SCHEMA,
        "days": int(days),
        "start": start.isoformat(),
        "target": (start + timedelta(days=max(0, int(days)))).isoformat(),
        "schedule": schedule,
        "overlay": {},
        "frontiers": 0,
        "write_operations": 0,
        "maximum_writes_per_frontier": 0,
        "event_kinds": {},
        "review_kinds": {},
        "handoffs": {},
        "errors": [],
        "elapsed_seconds": 0.0,
        "activity_kinds": {},
        "civilian_recruits_materialized": 0,
        "before_people": people["people"],
        "before_parentage": len(family.get("parentage", {})) if isinstance(family.get("parentage"), Mapping) else 0,
        "before_tracked_cash": _tracked_cash_metrics({}),
        "before_grade_counts": _grade_counts({}),
        "before_bytes": sum(p.stat().st_size for p in (ROOT / "state").rglob("*.json")),
    }


def _final_result(cp: Mapping[str, Any]) -> dict[str, Any]:
    overlay = cp.get("overlay", {}) if isinstance(cp.get("overlay"), Mapping) else {}
    schedule = cp["schedule"]

    def read_json(rel: str) -> Mapping[str, Any]:
        row = overlay.get(rel)
        if isinstance(row, Mapping):
            return row
        return _read(ROOT / rel)

    people = _person_metrics(overlay)
    after_bytes = _projected_state_bytes(overlay, int(cp["before_bytes"]))
    family = read_json("state/martial-world/family.json")
    contracts = read_json("state/martial-world/contracts/index.json")
    routeops = read_json("state/martial-world/route-operations.json")
    commitments = derived_commitment_state(read_json)
    conservation = _conservation_checks(
        overlay,
        before_parentage=int(cp.get("before_parentage", 0)),
        people_before=int(cp.get("before_people", 0)),
        before_tracked_cash=int((cp.get("before_tracked_cash") or {}).get("total", 0)),
        civilian_recruits=int(cp.get("civilian_recruits_materialized", 0)),
    )
    world_activity = _world_activity_metrics(
        overlay,
        review_kinds=cp.get("review_kinds", {}) if isinstance(cp.get("review_kinds"), Mapping) else {},
        activity_kinds=cp.get("activity_kinds", {}) if isinstance(cp.get("activity_kinds"), Mapping) else {},
    )
    grade_counts_after = _grade_counts(overlay)
    economic_health = _economic_health(overlay)
    errors = list(cp.get("errors", []))
    if conservation["status"] != "PASS":
        errors.extend({"error": msg, "kind": "conservation"} for msg in conservation["errors"])
    return {
        "status": "PASS" if not errors else "FAIL",
        "days": int(cp["days"]),
        "start": cp["start"],
        "target": cp["target"],
        "settled_through": schedule["settled_through"],
        "frontiers": int(cp["frontiers"]),
        "elapsed_seconds": round(float(cp.get("elapsed_seconds", 0.0)), 3),
        "write_operations": int(cp["write_operations"]),
        "unique_written_owners": len(overlay),
        "maximum_writes_per_frontier": int(cp["maximum_writes_per_frontier"]),
        "event_kinds": dict(sorted(cp.get("event_kinds", {}).items())),
        "review_kinds": dict(sorted(cp.get("review_kinds", {}).items())),
        "handoffs": dict(sorted(cp.get("handoffs", {}).items())),
        "people_before": int(cp["before_people"]),
        "people_after": people["people"],
        "people_change": people["people"] - int(cp["before_people"]),
        "living_faction_members_after": people["living_faction_members"],
        "dead_faction_identities_after": people["dead_faction_identities"],
        "living_independents_after": people["living_independents"],
        "personal_training_state_records": people["personal_training_state_records"],
        "personal_training_carry_records": people["personal_training_carry_records"],
        "state_bytes_before": int(cp["before_bytes"]),
        "projected_state_bytes_after": after_bytes,
        "projected_state_growth_bytes": after_bytes - int(cp["before_bytes"]),
        "projected_state_growth_percent": round((after_bytes - int(cp["before_bytes"])) * 100 / max(1, int(cp["before_bytes"])), 3),
        "family": {
            **{k: len(family.get(k, {})) for k in ("marriages", "parentage", "households", "succession_claims")},
            "active_pregnancies": sum(
                1 for row in family.get("marriages", {}).values()
                if isinstance(row, Mapping) and isinstance(row.get("pregnancy"), Mapping)
            ),
        },
        "grade_counts_before": dict(cp.get("before_grade_counts", {})),
        "grade_counts_after": grade_counts_after,
        "world_activity": world_activity,
        "semantic_activity_events": world_activity["semantic_activity_count"],
        "economic_health": economic_health,
        "active_contracts": sum(
            1 for row in contracts.get("active", {}).values()
            if isinstance(row, Mapping) and row.get("status") not in _CLOSED_CONTRACT
        ),
        "route_operations": {k: len(routeops.get(k, {})) for k in ("movements", "contacts")},
        "active_commitments": len(commitments.get("commitments", {})),
        "conservation": conservation,
        "substantive_state_sha256": _state_digest(overlay),
        "errors": errors,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--json", dest="out")
    ap.add_argument("--checkpoint")
    ap.add_argument("--reset-checkpoint", action="store_true")
    ap.add_argument("--frontier-budget", type=int, default=0, help="maximum frontiers this invocation; 0 means unlimited")
    ap.add_argument("--seconds-budget", type=float, default=0.0, help="soft execution budget; checked between complete frontiers")
    ap.add_argument("--max-frontiers", type=int, default=10000, help="hard total safety limit across resumed runs")
    ap.add_argument("--progress", action="store_true")
    ap.add_argument("--checkpoint-every", type=int, default=1, help="persist disposable checkpoint every N complete frontiers")
    args = ap.parse_args()

    checkpoint_path = Path(args.checkpoint).resolve() if args.checkpoint else None
    _acquire_checkpoint_lock(checkpoint_path)
    if checkpoint_path and args.reset_checkpoint and checkpoint_path.exists():
        checkpoint_path.unlink()
    if checkpoint_path and checkpoint_path.exists():
        cp = _read(checkpoint_path)
        if cp.get("schema") != _CHECKPOINT_SCHEMA:
            raise SystemExit("invalid long-horizon checkpoint schema")
        if int(cp.get("days", -1)) != int(args.days):
            raise SystemExit("checkpoint horizon does not match --days")
    else:
        cp = _new_checkpoint(args.days)

    overlay: dict[str, dict[str, Any]] = {
        str(k): dict(v) for k, v in cp.get("overlay", {}).items() if isinstance(v, Mapping)
    }
    schedule = dict(cp["schedule"])

    class _OverlayReader:
        """Disposable read overlay with a discoverable immutable base repository.

        The overlay stays authoritative for simulated writes, while the base
        repository pointer lets RAM-only person routing reuse the same cached
        roster index production uses. No routing index is persisted.
        """
        def __init__(self, rows: Mapping[str, Mapping[str, Any]]) -> None:
            self.rows = rows
            self.repository = _VALIDATION_REPOSITORY

        def read_json(self, rel: str) -> Mapping[str, Any]:
            row = self.rows.get(rel)
            if isinstance(row, Mapping):
                return row
            path = ROOT / rel
            if not path.is_file():
                raise FileNotFoundError(rel)
            return _read(path)

    overlay_reader = _OverlayReader(overlay)
    read_json = overlay_reader.read_json

    target = datetime.fromisoformat(str(cp["target"]))
    event_kinds = Counter(cp.get("event_kinds", {}))
    review_kinds = Counter(cp.get("review_kinds", {}))
    activity_kinds = Counter(cp.get("activity_kinds", {}))
    handoffs = Counter(cp.get("handoffs", {}))
    invocation_start = time.perf_counter()
    invocation_frontiers = 0
    complete = False

    stopped_for_budget = False
    while int(cp["frontiers"]) < int(args.max_frontiers):
        # Budget checks must happen before due-event discovery. The scheduler can
        # legitimately accumulate thousands of future one-off events during a
        # soak, so performing a full discovery scan after the caller's bounded
        # work is already complete defeats resumability and can overrun an outer
        # verification shell window.
        if args.frontier_budget > 0 and invocation_frontiers >= args.frontier_budget:
            stopped_for_budget = True
            break
        if args.seconds_budget > 0 and invocation_frontiers > 0 and time.perf_counter() - invocation_start >= args.seconds_budget:
            stopped_for_budget = True
            break
        after = datetime.fromisoformat(str(schedule["settled_through"]))
        schedule = sync_faction_activity(
            schedule, faction_ids=current_faction_refs(read_json), now=after,
        )
        events = due_events(schedule, after=after, through=target)
        if not events:
            complete = True
            break
        at = datetime.fromisoformat(str(events[0]["due_at"]))
        started = time.perf_counter()
        try:
            result = settle_martial_world_frontier(read_json=read_json, schedule=schedule, events=events, at=at)
        except Exception as exc:
            cp.setdefault("errors", []).append({
                "frontier": int(cp["frontiers"]),
                "at": at.isoformat(),
                "events": [e.get("kind") for e in events],
                "error": f"{type(exc).__name__}: {exc}",
            })
            break
        elapsed = time.perf_counter() - started
        if args.progress:
            owners = sorted({str(e.get("owner_ref")) for e in events if isinstance(e.get("owner_ref"), str)})
            owner_span = "" if not owners else f" owners={owners[0]}..{owners[-1]}"
            print(
                f"frontier={cp['frontiers']} at={at.isoformat()} class={events[0].get('schedule_class','calendar')} "
                f"events={len(events)} seconds={elapsed:.3f}{owner_span}",
                flush=True,
            )
        writes = result["writes"]
        try:
            _validate_frontier_writes(writes)
        except Exception as exc:
            cp.setdefault("errors", []).append({
                "frontier": int(cp["frontiers"]),
                "at": at.isoformat(),
                "events": [e.get("kind") for e in events],
                "kind": "state_admission",
                "error": f"{type(exc).__name__}: {exc}",
            })
            break
        overlay.update({str(k): dict(v) for k, v in writes.items()})
        schedule = dict(result["schedule_after"])
        cp["frontiers"] = int(cp["frontiers"]) + 1
        cp["write_operations"] = int(cp["write_operations"]) + len(writes)
        cp["maximum_writes_per_frontier"] = max(int(cp["maximum_writes_per_frontier"]), len(writes))
        event_kinds.update(str(e.get("kind")) for e in events)
        reviews = [r for r in result.get("reviews", []) if isinstance(r, Mapping)]
        review_kinds.update(str(r.get("kind")) for r in reviews)
        for review in reviews:
            review_kind = str(review.get("kind") or "unknown")
            result_kind = str(review.get("result") or "")
            activity_kinds[f"review:{review_kind}" + (f":{result_kind}" if result_kind else "")] += 1
            if review_kind != "faction_review":
                continue
            actions = review.get("executed_actions", [])
            if not isinstance(actions, list):
                continue
            for action in actions:
                if not isinstance(action, Mapping):
                    continue
                action_name = str(action.get("action") or "unknown")
                action_result = str(action.get("result") or "")
                activity_kinds[f"faction_action:{action_name}" + (f":{action_result}" if action_result else "")] += 1
                if action_name == "recruit" and action_result == "recruited":
                    external = action.get("recruited_external", [])
                    if isinstance(external, list):
                        cp["civilian_recruits_materialized"] = int(cp.get("civilian_recruits_materialized", 0)) + len(
                            [ref for ref in external if isinstance(ref, str) and ref]
                        )
        handoffs.update(str(h.get("kind")) for h in result.get("handoffs", []) if isinstance(h, Mapping))
        invocation_frontiers += 1
        # Persist only fully settled frontiers. Long release soaks can exceed an
        # outer execution window, so a killed verifier must resume from the last
        # complete deterministic transaction rather than replaying completed work.
        cp["elapsed_seconds"] = float(cp.get("elapsed_seconds", 0.0)) + elapsed
        cp["schedule"] = schedule
        cp["overlay"] = overlay
        cp["event_kinds"] = dict(event_kinds)
        cp["review_kinds"] = dict(review_kinds)
        cp["activity_kinds"] = dict(activity_kinds)
        cp["handoffs"] = dict(handoffs)
        if checkpoint_path and invocation_frontiers % max(1, int(args.checkpoint_every)) == 0:
            _write(checkpoint_path, cp)

    cp["schedule"] = schedule
    cp["overlay"] = overlay
    cp["event_kinds"] = dict(event_kinds)
    cp["review_kinds"] = dict(review_kinds)
    cp["activity_kinds"] = dict(activity_kinds)
    cp["handoffs"] = dict(handoffs)

    if (
        not complete
        and not stopped_for_budget
        and not cp.get("errors")
        and int(cp["frontiers"]) < int(args.max_frontiers)
    ):
        # Re-check only when execution stopped for a reason other than the
        # caller's explicit bounded budget. If a budget lands exactly on the
        # horizon, the next resumable invocation will detect completion before
        # doing any new causal work.
        after = datetime.fromisoformat(str(schedule["settled_through"]))
        schedule = sync_faction_activity(
            schedule, faction_ids=current_faction_refs(read_json), now=after,
        )
        complete = not bool(due_events(schedule, after=after, through=target))

    if checkpoint_path:
        _write(checkpoint_path, cp)

    if complete or cp.get("errors") or int(cp["frontiers"]) >= int(args.max_frontiers):
        result = _final_result(cp)
        if int(cp["frontiers"]) >= int(args.max_frontiers) and not complete and not cp.get("errors"):
            result["status"] = "FAIL"
            result["errors"].append({"error": "maximum total frontier safety limit reached"})
        if args.out:
            out = Path(args.out); out = out if out.is_absolute() else ROOT / out
            _write(out, result)
        print("LONG HORIZON SIMULATION", result["status"])
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "PASS" else 1

    progress = {
        "status": "INCOMPLETE",
        "days": int(cp["days"]),
        "frontiers": int(cp["frontiers"]),
        "settled_through": schedule["settled_through"],
        "target": cp["target"],
        "unique_written_owners": len(overlay),
        "checkpoint": str(checkpoint_path) if checkpoint_path else None,
        "errors": cp.get("errors", []),
    }
    print("LONG HORIZON SIMULATION INCOMPLETE")
    print(json.dumps(progress, indent=2))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
