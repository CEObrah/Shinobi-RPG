"""Current funded-project lifecycle and deterministic project completion frontier.

A project is one unresolved causal owner: sunk funding/materials, one physical
work site, intended staffing counts, current exact workers, remaining labor and
calendar obligations.  Worker loss never erases the funded project.  Living
institutions may deterministically restaff vacancies from genuinely free people
who are physically at the project site; extinct institutions suspend work until
a lawful estate claimant adopts it.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Sequence

from .commitments import release_resources, reserve_resources
from .faction_existence import faction_is_active
from .infrastructure import (
    advance_building_expansion,
    advance_building_upgrade,
    advance_enterprise_scale_expansion,
    advance_enterprise_upgrade,
    advance_estate_boundary_expansion,
    compact_project_state,
)
from .training import settle_and_reset_faction_training_cycle

_PROJECTS_PATH = "state/martial-world/projects.json"


def _worker_refs(project: Mapping[str, Any]) -> list[str]:
    refs: list[str] = []
    for key in ("skilled_worker_refs", "management_worker_refs", "general_worker_refs"):
        values = project.get(key, [])
        if isinstance(values, list):
            refs.extend(str(x) for x in values if isinstance(x, str) and x)
    return list(dict.fromkeys(refs))


def _ensure_planned_staffing(project: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(dict(project))
    for role in ("skilled", "management", "general"):
        refs_key = f"{role}_worker_refs"
        count_key = f"planned_{role}_worker_count"
        refs = [str(x) for x in out.get(refs_key, []) if isinstance(x, str) and x] if isinstance(out.get(refs_key), list) else []
        out[refs_key] = refs
        out[count_key] = max(0, int(out.get(count_key, len(refs))))
    return out


def _worker_ready(person: Mapping[str, Any]) -> bool:
    health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
    return str(health.get("status") or "ready") not in {"dead", "incapacitated"} and int(health.get("consciousness", 100)) > 0


def _person_at_project_site(person: Mapping[str, Any], faction: Mapping[str, Any], site_ref: str) -> bool:
    """Resolve sparse home location without inventing a second location authority."""
    if not site_ref:
        return False
    location = str(person.get("location_ref") or "")
    if location:
        if location == site_ref:
            return True
        # A sparse roster can use the strategic headquarters place while the
        # project names its local site. Treat that one authored home alias as the
        # same physical compound, but never alias a secondary captured estate.
        if site_ref == str(faction.get("local_site_ref") or "") and location == str(faction.get("headquarters") or ""):
            return True
        return False
    return site_ref == str(faction.get("local_site_ref") or faction.get("headquarters") or "")


def _skill(person: Mapping[str, Any], *keys: str) -> int:
    prof = person.get("professional_skills", {}) if isinstance(person.get("professional_skills"), Mapping) else {}
    martial = person.get("martial_skills", {}) if isinstance(person.get("martial_skills"), Mapping) else {}
    return max([0] + [int(prof.get(k, martial.get(k, 0)) or 0) for k in keys])


def _restaff_project(
    project: Mapping[str, Any], roster: Mapping[str, Any], commitments_state: Mapping[str, Any], *,
    faction_ref: str, project_ref: str, location_ref: str, faction: Mapping[str, Any],
    physically_unavailable_refs: set[str],
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Replace vacant exact workers without changing planned project headcount."""
    out = _ensure_planned_staffing(project)
    people_rows = roster.get("people", []) if isinstance(roster.get("people"), list) else []
    people = {
        str(row.get("person_id")): row for row in people_rows
        if isinstance(row, Mapping) and isinstance(row.get("person_id"), str) and row.get("person_id")
    }

    # This project's own current claim must not make its surviving workers look
    # externally busy. Every other finite owner remains a hard blocker.
    without_this = release_resources(commitments_state, activity_ref=project_ref)
    blocked = without_this.get("person_index", {}) if isinstance(without_this.get("person_index"), Mapping) else {}

    role_spec = (
        ("skilled_worker_refs", "planned_skilled_worker_count", ("crafting", "administration")),
        ("management_worker_refs", "planned_management_worker_count", ("administration", "commerce", "command")),
        ("general_worker_refs", "planned_general_worker_count", ()),
    )
    assigned: set[str] = set()
    added: list[str] = []

    for refs_key, count_key, score_keys in role_spec:
        desired = max(0, int(out.get(count_key, 0)))
        current = [str(x) for x in out.get(refs_key, []) if isinstance(x, str)] if isinstance(out.get(refs_key), list) else []
        kept: list[str] = []
        for ref in current:
            person = people.get(ref)
            if (
                ref in assigned or ref in blocked or ref in physically_unavailable_refs or not isinstance(person, Mapping)
                or not _worker_ready(person) or not _person_at_project_site(person, faction, location_ref)
            ):
                continue
            kept.append(ref); assigned.add(ref)
            if len(kept) >= desired:
                break

        missing = max(0, desired - len(kept))
        if missing:
            candidates = [
                p for ref, p in people.items()
                if ref not in assigned and ref not in blocked and ref not in physically_unavailable_refs and _worker_ready(p)
                and _person_at_project_site(p, faction, location_ref)
            ]
            if score_keys:
                candidates.sort(key=lambda p: (-_skill(p, *score_keys), str(p.get("person_id") or "")))
            else:
                candidates.sort(key=lambda p: str(p.get("person_id") or ""))
            for person in candidates[:missing]:
                ref = str(person.get("person_id") or "")
                if not ref:
                    continue
                kept.append(ref); assigned.add(ref); added.append(ref)
        out[refs_key] = kept

    current_workers = _worker_refs(out)
    if current_workers:
        activity_kind = "construction" if str(out.get("project_type") or "") in {
            "building_upgrade", "building_expansion", "estate_boundary_expansion"
        } else "enterprise_setup"
        after = reserve_resources(
            without_this,
            resources=[("person", ref, faction_ref) for ref in current_workers],
            actor_ref=current_workers[0], owner_ref=faction_ref, activity_ref=project_ref,
            activity_kind=activity_kind, started_at=str(out.get("started_at") or ""),
            location_ref=location_ref or None,
        )
    else:
        after = without_this
    return out, after, added


def _elapsed_work_days(project: Mapping[str, Any], at: datetime) -> tuple[int, str]:
    raw = str(project.get("last_progress_at") or project.get("started_at") or "")
    try:
        previous = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("project progress frontier invalid") from exc
    if at < previous:
        raise ValueError("project progress frontier reversed")
    days = int((at - previous).total_seconds() // 86400)
    settled = previous + timedelta(days=days)
    return days, settled.isoformat()


def _ceil_div(value: int, divisor: int) -> int:
    if value <= 0:
        return 0
    if divisor <= 0:
        return -1
    return (value + divisor - 1) // divisor


def _next_review_days(project: Mapping[str, Any]) -> int:
    """Schedule the next meaningful project frontier, not a daily polling loop."""
    ptype = str(project.get("project_type") or "")
    calendar = max(0, int(project.get("minimum_calendar_days", 0)) - int(project.get("elapsed_calendar_days", 0)))
    general = len(project.get("general_worker_refs", [])) if isinstance(project.get("general_worker_refs"), list) else 0
    if ptype in {"building_upgrade", "building_expansion", "estate_boundary_expansion"}:
        skilled = len(project.get("skilled_worker_refs", [])) if isinstance(project.get("skilled_worker_refs"), list) else 0
        gd = _ceil_div(max(0, int(project.get("general_labor_hours_remaining", 0))), general * 8)
        sd = _ceil_div(max(0, int(project.get("skilled_labor_hours_remaining", 0))), skilled * 6)
        if gd < 0 or sd < 0:
            return 7
        return max(1, calendar, gd, sd)
    managers = len(project.get("management_worker_refs", [])) if isinstance(project.get("management_worker_refs"), list) else 0
    gd = _ceil_div(max(0, int(project.get("general_setup_labor_hours_remaining", 0))), general * 4)
    md = _ceil_div(max(0, int(project.get("management_labor_hours_remaining", 0))), managers * 4)
    if gd < 0 or md < 0:
        return 7
    return max(1, calendar, gd, md)


def _advance_project(project: Mapping[str, Any], *, days: int) -> dict[str, Any]:
    ptype = str(project.get("project_type") or "")
    general = len(project.get("general_worker_refs", [])) if isinstance(project.get("general_worker_refs"), list) else 0
    skilled = len(project.get("skilled_worker_refs", [])) if isinstance(project.get("skilled_worker_refs"), list) else 0
    managers = len(project.get("management_worker_refs", [])) if isinstance(project.get("management_worker_refs"), list) else 0
    if ptype == "building_upgrade":
        return advance_building_upgrade(project, elapsed_calendar_days=days, general_labor_hours=general * 8 * days, skilled_labor_hours=skilled * 6 * days)
    if ptype == "building_expansion":
        return advance_building_expansion(project, elapsed_calendar_days=days, general_labor_hours=general * 8 * days, skilled_labor_hours=skilled * 6 * days)
    if ptype == "estate_boundary_expansion":
        return advance_estate_boundary_expansion(project, elapsed_calendar_days=days, general_labor_hours=general * 8 * days, skilled_labor_hours=skilled * 6 * days)
    if ptype == "enterprise_upgrade":
        return advance_enterprise_upgrade(project, elapsed_calendar_days=days, management_labor_hours=managers * 4 * days, general_setup_labor_hours=general * 4 * days)
    if ptype == "enterprise_scale_expansion":
        return advance_enterprise_scale_expansion(project, elapsed_calendar_days=days, management_labor_hours=managers * 4 * days, general_setup_labor_hours=general * 4 * days)
    raise ValueError("unsupported project")


def _apply_completion(faction: dict[str, Any], project: Mapping[str, Any]) -> None:
    """Apply physical work to its site; organizational scale remains faction-wide."""
    site_ref = str(project.get("site_ref") or "")
    primary = str(faction.get("local_site_ref") or faction.get("headquarters") or "")
    if not site_ref:
        raise ValueError("project physical site missing")
    estate: dict[str, Any] | None = None
    if site_ref != primary:
        controlled = faction.get("controlled_estates", {}) if isinstance(faction.get("controlled_estates"), Mapping) else {}
        raw = controlled.get(site_ref) if isinstance(controlled, Mapping) else None
        if not isinstance(raw, Mapping):
            raise ValueError("project site not controlled by owner")
        estate = copy.deepcopy(dict(raw))

    ptype = str(project.get("project_type") or "")
    physical = faction if estate is None else estate
    if ptype == "building_upgrade":
        physical.setdefault("buildings", {})[str(project["building_type"])] = int(project["target_level"])
    elif ptype == "building_expansion":
        facilities = physical.setdefault("infrastructure", {}).setdefault("facilities", {})
        facility = facilities.setdefault(str(project["building_type"]), {})
        facility["footprint_m2"] = max(0, int(facility.get("footprint_m2", 0))) + int(project["additional_footprint_m2"])
    elif ptype == "estate_boundary_expansion":
        infrastructure = physical.setdefault("infrastructure", {}); facilities = infrastructure.setdefault("facilities", {})
        walls = facilities.setdefault("walls_gate", {})
        old_perimeter = max(1, int(walls.get("defended_perimeter_m", project.get("old_perimeter_m", 1))))
        old_footprint = max(0, int(walls.get("footprint_m2", 0))); new_perimeter = max(old_perimeter, int(project["new_perimeter_m"]))
        new_footprint = (old_footprint * new_perimeter + old_perimeter - 1) // old_perimeter if old_footprint > 0 else new_perimeter * 2
        walls["defended_perimeter_m"] = new_perimeter; walls["footprint_m2"] = new_footprint
        walls["wall_height_m"] = max(1, int(project.get("wall_height_m", walls.get("wall_height_m", 4))))
        infrastructure["estate_area_m2"] = max(int(infrastructure.get("estate_area_m2", 0)), int(project["new_estate_area_m2"]))
    elif ptype == "enterprise_upgrade":
        physical.setdefault("enterprises", {})[str(project["enterprise_type"])] = int(project["target_level"])
    elif ptype == "enterprise_scale_expansion":
        # Operating scale is institutional organization, not captured physical
        # property. The project site constrains labor; completion mutates only
        # the current institution's scale authority.
        scales = faction.setdefault("enterprise_scale", {}); row = scales.setdefault(str(project["enterprise_type"]), {})
        basis = str(project["scale_basis"]); row[basis] = max(0, int(row.get(basis, 0))) + int(project["additional_scale"])
    else:
        raise ValueError("unsupported project completion")

    if estate is not None and ptype != "enterprise_scale_expansion":
        controlled = faction.setdefault("controlled_estates", {})
        controlled[site_ref] = estate


def settle_project_frontier(
    *, events: Sequence[Mapping[str, Any]], at: datetime, projects_state: dict[str, Any],
    commitments_state: Mapping[str, Any], writes: dict[str, Any], reviews: list[dict[str, Any]],
    pending_one_off_events: list[dict[str, Any]], faction_cache: dict[str, tuple[str, dict[str, Any]]],
    roster_cache: dict[str, tuple[str, dict[str, Any]]],
    load_faction: Callable[[str], tuple[str, dict[str, Any]]], load_roster: Callable[[str], tuple[str, dict[str, Any]]],
    settle_and_resume_people: Callable[..., Mapping[str, Any]],
    pause_people_for_commitment: Callable[[str, Sequence[str]], None],
    unavailable_person_refs: Callable[[], set[str]],
) -> Mapping[str, Any]:
    at_iso = at.isoformat()
    for event in events:
        if event.get("kind") != "autonomous_project_due":
            continue
        project_ref = str(event.get("owner_ref") or "")
        registry = projects_state.get("projects", {}) if isinstance(projects_state, Mapping) else {}
        row = registry.get(project_ref) if isinstance(registry, Mapping) else None
        if not isinstance(row, Mapping):
            reviews.append({"kind": "autonomous_project_due", "event_id": event.get("event_id"), "result": "project_missing"})
            continue
        project = _ensure_planned_staffing(row); fid = str(project.get("faction_ref") or "")
        if not fid:
            reviews.append({"kind": "autonomous_project_due", "event_id": event.get("event_id"), "result": "project_owner_missing"})
            continue
        fpath, faction = load_faction(fid)

        # Extinction preserves sunk physical work but ends institutional labor.
        # No future project wake is scheduled until an estate claimant adopts it.
        if not faction_is_active(faction):
            workers = _worker_refs(project)
            project["skilled_worker_refs"] = []; project["management_worker_refs"] = []; project["general_worker_refs"] = []
            project["status"] = "suspended_extinct"; project["suspended_reason"] = "faction_extinct"; project["suspended_at"] = at_iso
            commitments_state = settle_and_resume_people(workers, activity_ref=project_ref, commitments_state=commitments_state)
            registry[project_ref] = compact_project_state(project, project_ref=project_ref); writes[_PROJECTS_PATH] = projects_state
            reviews.append({"kind": "autonomous_project_due", "event_id": event.get("event_id"), "project_ref": project_ref, "faction_ref": fid, "result": "suspended_extinct"})
            continue

        rpath, roster = load_roster(fid)
        faction, roster, _training = settle_and_reset_faction_training_cycle(
            faction, roster, at_iso=at_iso, paused_refs=sorted(unavailable_person_refs()),
        )
        writes[fpath] = faction; writes[rpath] = roster; faction_cache[fid] = (fpath, faction); roster_cache[fid] = (rpath, roster)

        site_ref = str(project.get("site_ref") or faction.get("local_site_ref") or faction.get("headquarters") or "")
        if not site_ref:
            reviews.append({"kind": "autonomous_project_due", "event_id": event.get("event_id"), "project_ref": project_ref, "result": "project_site_missing"})
            continue
        project["site_ref"] = site_ref
        project, commitments_state, replacements = _restaff_project(
            project, roster, commitments_state, faction_ref=fid, project_ref=project_ref,
            location_ref=site_ref, faction=faction, physically_unavailable_refs=unavailable_person_refs(),
        )
        if replacements:
            pause_people_for_commitment(fid, replacements)
            fpath, faction = load_faction(fid); rpath, roster = load_roster(fid)

        try:
            days, settled_through = _elapsed_work_days(project, at)
            updated = _advance_project(project, days=days) if days else copy.deepcopy(project)
        except ValueError:
            reviews.append({"kind": "autonomous_project_due", "event_id": event.get("event_id"), "project_ref": project_ref, "result": "project_progress_invalid"})
            continue
        updated["last_progress_at"] = settled_through

        if not updated.get("completed"):
            missing = (
                (int(updated.get("planned_general_worker_count", 0)) > len(updated.get("general_worker_refs", [])))
                or (int(updated.get("planned_skilled_worker_count", 0)) > len(updated.get("skilled_worker_refs", [])))
                or (int(updated.get("planned_management_worker_count", 0)) > len(updated.get("management_worker_refs", [])))
            )
            if missing:
                updated["status"] = "staffing_required"
            else:
                updated.pop("status", None); updated.pop("suspended_reason", None); updated.pop("suspended_at", None)
            registry[project_ref] = compact_project_state(updated, project_ref=project_ref)
            pending_one_off_events.append({
                "event_id": f"autonomous_project_due:{project_ref}", "kind": "autonomous_project_due",
                "due_at": (at + timedelta(days=_next_review_days(updated))).isoformat(),
                "owner_ref": project_ref, "requires_player_decision": False,
            })
            writes[_PROJECTS_PATH] = projects_state
            reviews.append({
                "kind": "autonomous_project_due", "event_id": event.get("event_id"), "project_ref": project_ref,
                "result": "staffing_required" if missing else "work_remaining", "restaffed_count": len(replacements),
            })
            continue

        try:
            _apply_completion(faction, updated)
        except ValueError:
            reviews.append({"kind": "autonomous_project_due", "event_id": event.get("event_id"), "project_ref": project_ref, "result": "project_site_invalid"})
            continue
        writes[fpath] = faction; faction_cache[fid] = (fpath, faction)
        worker_refs = _worker_refs(updated)
        commitments_state = settle_and_resume_people(worker_refs, activity_ref=project_ref, commitments_state=commitments_state)
        registry.pop(project_ref, None); writes[_PROJECTS_PATH] = projects_state
        reviews.append({"kind": "autonomous_project_due", "event_id": event.get("event_id"), "project_ref": project_ref, "faction_ref": fid, "project_type": updated.get("project_type"), "result": "completed"})
    return commitments_state


__all__ = ["settle_project_frontier"]
