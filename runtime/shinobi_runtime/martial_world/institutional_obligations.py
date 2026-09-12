"""Derived blockers for faction ownership transitions.

These rows are not persisted. They answer one narrow question from the current
mechanical owners: may an institution or exact member change ownership right
now without stranding an unresolved obligation?
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

_PROJECTS = "state/martial-world/projects.json"
_DEPLOYMENTS = "state/martial-world/deployments.json"
_ROUTES = "state/martial-world/route-operations.json"
_CUSTODY = "state/martial-world/custody.json"
_CONTRACTS = "state/martial-world/contracts/index.json"
_TOURNAMENTS = "state/martial-world/tournaments.json"

_TERMINAL_CONTRACT = {"settled", "failed", "expired"}
_TERMINAL_CUSTODY = {"released", "escaped", "rescued", "executed"}
_TERMINAL_ROUTE = {"completed", "failed", "settled", "cancelled", "closed"}
_TERMINAL_DEPLOYMENT = {"completed", "returned", "failed", "cancelled", "disbanded", "closed"}


def _read(read_json: Callable[[str], Any], path: str) -> Mapping[str, Any]:
    try:
        row = read_json(path)
    except FileNotFoundError:
        return {}
    return row if isinstance(row, Mapping) else {}


def _add(rows: list[dict[str, str]], kind: str, ref: str, reason: str) -> None:
    item = {"kind": kind, "ref": ref, "reason": reason}
    if item not in rows:
        rows.append(item)


def faction_retirement_blockers(read_json: Callable[[str], Any], faction_ref: str) -> list[dict[str, str]]:
    """Return unresolved owners that require this institution to keep existing."""
    fid = str(faction_ref or "")
    out: list[dict[str, str]] = []
    if not fid:
        return out

    projects = _read(read_json, _PROJECTS).get("projects", {})
    if isinstance(projects, Mapping):
        for ref, row in projects.items():
            if isinstance(row, Mapping) and not bool(row.get("completed")) and str(row.get("faction_ref") or "") == fid:
                _add(out, "project", str(ref), "project_owner")

    deployments = _read(read_json, _DEPLOYMENTS).get("deployments", {})
    if isinstance(deployments, Mapping):
        for ref, row in deployments.items():
            if not isinstance(row, Mapping) or str(row.get("status") or "") in _TERMINAL_DEPLOYMENT:
                continue
            if fid in {str(row.get("faction_ref") or ""), str(row.get("target_faction_ref") or "")}:
                _add(out, "deployment", str(ref), "deployment_involves_faction")

    movements = _read(read_json, _ROUTES).get("movements", {})
    if isinstance(movements, Mapping):
        for ref, row in movements.items():
            if not isinstance(row, Mapping) or str(row.get("status") or "") in _TERMINAL_ROUTE:
                continue
            owner_values = {
                str(row.get("beneficiary_ref") or ""), str(row.get("faction_ref") or ""),
                str(row.get("owner_ref") or ""), str(row.get("source_faction_ref") or ""),
                str(row.get("target_faction_ref") or ""),
            }
            if fid in owner_values:
                _add(out, "route_movement", str(ref), "route_involves_faction")

    custody = _read(read_json, _CUSTODY).get("records", [])
    if isinstance(custody, list):
        for row in custody:
            if not isinstance(row, Mapping) or str(row.get("status") or "") in _TERMINAL_CUSTODY:
                continue
            if str(row.get("holder_faction_ref") or "") == fid:
                _add(out, "custody", str(row.get("custody_id") or row.get("person_ref") or ""), "institutional_custody")

    contracts = _read(read_json, _CONTRACTS).get("active", {})
    if isinstance(contracts, Mapping):
        for ref, row in contracts.items():
            if not isinstance(row, Mapping) or str(row.get("status") or "") in _TERMINAL_CONTRACT:
                continue
            if fid == str(row.get("beneficiary_ref") or ""):
                _add(out, "contract", str(ref), "contract_beneficiary")
            if fid == str(row.get("issuer_ref") or ""):
                _add(out, "contract", str(ref), "contract_issuer")

    tournaments = _read(read_json, _TOURNAMENTS).get("tournaments", {})
    if isinstance(tournaments, Mapping):
        for ref, tournament in tournaments.items():
            if not isinstance(tournament, Mapping) or str(tournament.get("status") or "") == "completed":
                continue
            registrations = tournament.get("registrations", [])
            if isinstance(registrations, list) and any(
                isinstance(row, Mapping) and str(row.get("faction_ref") or "") == fid for row in registrations
            ):
                _add(out, "tournament", str(ref), "tournament_registration")
            delegations = tournament.get("delegations", {})
            if isinstance(delegations, Mapping):
                drow = delegations.get(fid)
                if isinstance(drow, Mapping) or any(
                    isinstance(row, Mapping) and str(row.get("faction_ref") or "") == fid for row in delegations.values()
                ):
                    _add(out, "tournament", str(ref), "tournament_delegation")
    return out


def _member_transition_bindings(read_json: Callable[[str], Any]) -> dict[str, list[dict[str, str]]]:
    """Map exact people to finite institutional obligations that bind affiliation.

    These bindings are broader than time occupancy. Accepted contract
    principals and registered tournament entrants remain free to train and
    conduct ordinary life before departure or match day, but they cannot
    silently change institution while the obligation still names the old owner.
    """
    by_person: dict[str, list[dict[str, str]]] = {}

    def bind(person_ref: str, kind: str, ref: str, reason: str) -> None:
        person = str(person_ref or "")
        if not person:
            return
        rows = by_person.setdefault(person, [])
        item = {"kind": kind, "ref": str(ref), "reason": reason}
        if item not in rows:
            rows.append(item)

    projects = _read(read_json, _PROJECTS).get("projects", {})
    if isinstance(projects, Mapping):
        for ref, row in projects.items():
            if not isinstance(row, Mapping) or bool(row.get("completed")):
                continue
            for key in ("skilled_worker_refs", "management_worker_refs", "general_worker_refs", "worker_refs"):
                values = row.get(key, [])
                if isinstance(values, list):
                    for person_ref in values:
                        if isinstance(person_ref, str):
                            bind(person_ref, "project", str(ref), "moving_member_is_project_worker")

    deployments = _read(read_json, _DEPLOYMENTS).get("deployments", {})
    if isinstance(deployments, Mapping):
        for ref, row in deployments.items():
            if not isinstance(row, Mapping) or str(row.get("status") or "") in _TERMINAL_DEPLOYMENT:
                continue
            refs: set[str] = set()
            ref_keys = ["participant_refs", "member_refs"]
            # Standing-retinue chooser refs are assignment provenance/authority,
            # not people physically deployed with the team. Once assignment is
            # active they must not bind Tang Zhu/Ling (or any chooser) as if they
            # were traveling with Wei. Pending assignment can still retain the
            # chooser obligation until its one-off review is settled.
            if not (row.get("operation_kind") == "standing_retinue" and str(row.get("status") or "") == "active"):
                ref_keys.append("chooser_refs")
            for key in ref_keys:
                values = row.get(key, [])
                if isinstance(values, list):
                    refs.update(str(x) for x in values if isinstance(x, str) and x)
            for key in ("leader_ref", "commander_ref", "deputy_ref"):
                value = row.get(key)
                if isinstance(value, str) and value:
                    refs.add(value)
            structure = row.get("structure") if isinstance(row.get("structure"), Mapping) else {}
            values = structure.get("member_refs", []) if isinstance(structure, Mapping) else []
            if isinstance(values, list):
                refs.update(str(x) for x in values if isinstance(x, str) and x)
            for key in ("commander_ref", "deputy_ref"):
                value = structure.get(key) if isinstance(structure, Mapping) else None
                if isinstance(value, str) and value:
                    refs.add(value)
            for person_ref in refs:
                bind(person_ref, "deployment", str(ref), "moving_member_is_deployed")

    movements = _read(read_json, _ROUTES).get("movements", {})
    if isinstance(movements, Mapping):
        for ref, row in movements.items():
            if not isinstance(row, Mapping) or str(row.get("status") or "") in _TERMINAL_ROUTE:
                continue
            refs: set[str] = set()
            for key in ("participant_refs", "escort_refs", "raider_refs", "contact_attacker_refs", "captive_refs", "protected_person_refs"):
                values = row.get(key, [])
                if isinstance(values, list):
                    refs.update(str(x) for x in values if isinstance(x, str) and x)
            leader = row.get("leader_ref")
            if isinstance(leader, str) and leader:
                refs.add(leader)
            for person_ref in refs:
                bind(person_ref, "route_movement", str(ref), "moving_member_is_on_route")

    custody = _read(read_json, _CUSTODY).get("records", [])
    if isinstance(custody, list):
        for row in custody:
            if not isinstance(row, Mapping) or str(row.get("status") or "") in _TERMINAL_CUSTODY:
                continue
            ref = str(row.get("custody_id") or row.get("person_ref") or "")
            for key in ("person_ref", "captor_ref"):
                person_ref = row.get(key)
                if isinstance(person_ref, str) and person_ref:
                    bind(person_ref, "custody", ref, "moving_member_in_custody_authority")

    contracts = _read(read_json, _CONTRACTS).get("active", {})
    if isinstance(contracts, Mapping):
        for ref, row in contracts.items():
            if not isinstance(row, Mapping) or str(row.get("status") or "") in _TERMINAL_CONTRACT:
                continue
            values = row.get("participants", [])
            if isinstance(values, list):
                for person_ref in values:
                    if isinstance(person_ref, str) and person_ref:
                        bind(person_ref, "contract", str(ref), "moving_member_is_contract_principal")

    tournaments = _read(read_json, _TOURNAMENTS).get("tournaments", {})
    if isinstance(tournaments, Mapping):
        for ref, tournament in tournaments.items():
            if not isinstance(tournament, Mapping) or str(tournament.get("status") or "") == "completed":
                continue
            registrations = tournament.get("registrations", [])
            if isinstance(registrations, list):
                for row in registrations:
                    entrant = row.get("entrant_ref") if isinstance(row, Mapping) else None
                    if isinstance(entrant, str) and entrant:
                        bind(entrant, "tournament", str(ref), "moving_member_is_registered_entrant")
            delegations = tournament.get("delegations", {})
            if isinstance(delegations, Mapping):
                for drow in delegations.values():
                    if not isinstance(drow, Mapping):
                        continue
                    for key in ("entrant_refs", "spectator_refs", "leader_refs", "senior_refs"):
                        values = drow.get(key, [])
                        if isinstance(values, list):
                            for person_ref in values:
                                if isinstance(person_ref, str) and person_ref:
                                    bind(person_ref, "tournament", str(ref), "moving_member_is_tournament_delegate")
    return by_person


def member_transition_bound_person_refs(read_json: Callable[[str], Any]) -> set[str]:
    """Exact people who may be time-free but cannot change institution yet."""
    return set(_member_transition_bindings(read_json))


def member_transition_blockers(
    read_json: Callable[[str], Any], member_refs: Sequence[str], *,
    source_faction_ref: str = "", moving_site_refs: Sequence[str] = (),
) -> list[dict[str, str]]:
    """Return finite owners that prevent exact people/sites changing institution."""
    moving = {str(x) for x in member_refs if isinstance(x, str) and x}
    sites = {str(x) for x in moving_site_refs if isinstance(x, str) and x}
    source = str(source_faction_ref or "")
    out: list[dict[str, str]] = []
    if not moving and not sites:
        return out

    projects = _read(read_json, _PROJECTS).get("projects", {})
    if isinstance(projects, Mapping) and sites:
        for ref, row in projects.items():
            if not isinstance(row, Mapping) or bool(row.get("completed")):
                continue
            if str(row.get("faction_ref") or "") == source and str(row.get("site_ref") or "") in sites:
                _add(out, "project", str(ref), "project_bound_to_moving_site")

    bindings = _member_transition_bindings(read_json)
    for person_ref in sorted(moving):
        for row in bindings.get(person_ref, []):
            _add(out, row["kind"], row["ref"], row["reason"])
    return out



def estate_claim_value_blockers(read_json: Callable[[str], Any], faction_ref: str) -> list[dict[str, str]]:
    """Return unresolved owners that can still credit value to a dormant estate.

    Physical projects are intentionally excluded because a lawful estate claim
    adopts them at their actual sites. Tournament registrations have already
    paid into tournament escrow and resolve by participation/forfeit. What must
    block a final estate transfer are external owners that may still return cash,
    cargo or provisions to the extinct faction after the claim frontier.
    """
    fid = str(faction_ref or "")
    out: list[dict[str, str]] = []
    if not fid:
        return out

    contracts = _read(read_json, _CONTRACTS).get("active", {})
    if isinstance(contracts, Mapping):
        for ref, row in contracts.items():
            if not isinstance(row, Mapping) or str(row.get("status") or "") in _TERMINAL_CONTRACT:
                continue
            if fid in {str(row.get("issuer_ref") or ""), str(row.get("beneficiary_ref") or "")} :
                _add(out, "contract", str(ref), "contract_can_credit_dormant_estate")

    movements = _read(read_json, _ROUTES).get("movements", {})
    if isinstance(movements, Mapping):
        for ref, row in movements.items():
            if not isinstance(row, Mapping) or str(row.get("status") or "") in _TERMINAL_ROUTE:
                continue
            value_return_owners = {
                str(row.get("beneficiary_ref") or ""),
                str(row.get("faction_ref") or ""),
                str(row.get("owner_ref") or ""),
                str(row.get("source_faction_ref") or ""),
            }
            if fid in value_return_owners:
                _add(out, "route_movement", str(ref), "route_can_credit_dormant_estate")
    return out

__all__ = ["estate_claim_value_blockers", "faction_retirement_blockers", "member_transition_blockers", "member_transition_bound_person_refs"]
