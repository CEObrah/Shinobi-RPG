"""Deterministic small-scale Jianghu deployment command structures.

The Jianghu field hierarchy deliberately does not copy Sword & Banners' army
bureaucracy.  A deployment uses the smallest lawful top-level formation for its
actual conserved headcount.  A separate Field Force headquarters appears only
above Wing scale (or when a caller explicitly forms a joint force).

All commanders are existing deployed people.  Structure owns no bodies and
never grants combat statistics; it only establishes command routing.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence


FORMATION_BANDS = {
    "party": (1, 2, 2),
    "team": (3, 5, 8),
    "section": (9, 25, 30),
    "wing": (31, 100, 120),
    "field_force": (121, 0, None),
}


def formation_kind_for_headcount(headcount: int, *, joint_force: bool = False) -> str:
    if isinstance(headcount, bool) or not isinstance(headcount, int) or headcount < 1:
        raise ValueError("deployment headcount must be a positive integer")
    if joint_force and headcount >= 3:
        return "field_force"
    if headcount <= 2:
        return "party"
    if headcount <= 8:
        return "team"
    if headcount <= 30:
        return "section"
    if headcount <= 120:
        return "wing"
    return "field_force"


def _value(record: Mapping[str, Any], group: str, key: str) -> int:
    row = record.get(group)
    if not isinstance(row, Mapping):
        return 0
    raw = row.get(key, 0)
    return int(raw) if isinstance(raw, int) and not isinstance(raw, bool) else 0


def command_score(record: Mapping[str, Any]) -> int:
    """Selection score only; never a physical-stat modifier for subordinates."""
    return (
        _value(record, "martial_skills", "command") * 100
        + _value(record, "aptitudes", "leadership") * 15
        + _value(record, "attributes", "perception") * 8
        + _value(record, "attributes", "intelligence") * 6
        + _value(record, "professional_skills", "administration") * 4
    )


def _select(
    available: Sequence[str], records: Mapping[str, Mapping[str, Any]], *, preferred: str | None = None
) -> str:
    pool = [ref for ref in available if ref in records]
    if not pool:
        raise ValueError("no lawful deployed commander candidate")
    if isinstance(preferred, str) and preferred in pool:
        return preferred
    return max(pool, key=lambda ref: (command_score(records[ref]), ref))


def _balanced_groups(refs: Sequence[str], count: int) -> list[list[str]]:
    if count <= 0 or count > len(refs):
        raise ValueError("invalid deployment partition count")
    base, extra = divmod(len(refs), count)
    out: list[list[str]] = []
    cursor = 0
    for idx in range(count):
        size = base + (1 if idx < extra else 0)
        out.append(list(refs[cursor:cursor + size]))
        cursor += size
    return out


def _wing_child_groups(refs: Sequence[str]) -> list[list[str]]:
    """Partition a Wing's non-HQ people into Sections of 9..30 where possible."""
    if not refs:
        return []
    count = max(1, math.ceil(len(refs) / 30))
    while count > 1 and len(refs) // count < 9:
        count -= 1
    groups = _balanced_groups(refs, count)
    if any(len(group) > 30 for group in groups):
        raise ValueError("wing cannot be partitioned into lawful sections")
    return groups


def _field_force_child_groups(refs: Sequence[str]) -> list[list[str]]:
    """Partition a Field Force into balanced Wings, each at most 120 people."""
    if not refs:
        return []
    count = max(1, math.ceil(len(refs) / 120))
    groups = _balanced_groups(refs, count)
    if any(len(group) > 120 for group in groups):
        raise ValueError("field force wing partition overflow")
    return groups


def _build_node(
    refs: Sequence[str],
    records: Mapping[str, Mapping[str, Any]],
    *,
    kind: str,
    top_level: bool,
    preferred_commander_ref: str | None = None,
    preferred_deputy_ref: str | None = None,
) -> dict[str, Any]:
    refs = list(dict.fromkeys(refs))
    if not refs:
        raise ValueError("formation cannot be empty")

    available = list(refs)
    commander = _select(available, records, preferred=preferred_commander_ref)
    available.remove(commander)

    deputy: str | None = None
    # Small-scale rule: a top-level Section/Wing/Field Force has a deputy.
    # Nested elements normally have a single commander because the parent HQ
    # already supplies command redundancy. A detached child can be rebuilt as
    # a new top-level formation if it needs its own deputy.
    if top_level and kind in {"section", "wing", "field_force"} and available:
        deputy = _select(available, records, preferred=preferred_deputy_ref)
        available.remove(deputy)

    node: dict[str, Any] = {
        "kind": kind,
        "headcount": len(refs),
        "commander_ref": commander,
        "deputy_ref": deputy,
        "member_refs": list(refs),
        "child_formations": [],
    }

    if kind in {"party", "team", "section"}:
        return node

    if kind == "wing":
        for group in _wing_child_groups(available):
            child_kind = "section" if len(group) >= 9 else ("team" if len(group) >= 3 else "party")
            node["child_formations"].append(
                _build_node(group, records, kind=child_kind, top_level=False)
            )
        return node

    if kind == "field_force":
        for group in _field_force_child_groups(available):
            child_kind = formation_kind_for_headcount(len(group))
            # Any child above section scale becomes a nested wing; 1..30 bodies
            # remain a smaller direct detachment rather than inventing another HQ.
            if child_kind == "field_force":
                child_kind = "wing"
            node["child_formations"].append(
                _build_node(group, records, kind=child_kind, top_level=False)
            )
        return node

    raise ValueError(f"unknown formation kind: {kind}")


def build_deployment_structure(
    *,
    member_refs: Sequence[str],
    records: Mapping[str, Mapping[str, Any]],
    preferred_commander_ref: str | None = None,
    preferred_deputy_ref: str | None = None,
    standing_offices: Mapping[str, str] | None = None,
    joint_force: bool = False,
) -> dict[str, Any]:
    """Create a conserved temporary command tree from exact deployed people.

    ``standing_offices`` may provide ``field_commander`` and
    ``deputy_field_commander`` refs.  They are preferred for Wing/Field Force
    deployments when actually present among the deployed people, but office is
    not magical command ownership: absent office-holders cannot command a force
    they did not join.
    """
    refs = list(dict.fromkeys(str(ref) for ref in member_refs))
    if not refs:
        raise ValueError("deployment requires at least one person")
    if len(refs) != len(member_refs):
        raise ValueError("deployment member refs must be unique")
    missing = [ref for ref in refs if ref not in records]
    if missing:
        raise ValueError(f"deployment members missing records: {missing[:3]}")

    kind = formation_kind_for_headcount(len(refs), joint_force=joint_force)
    offices = standing_offices if isinstance(standing_offices, Mapping) else {}
    if preferred_commander_ref is None and kind in {"wing", "field_force"}:
        raw = offices.get("field_commander")
        if isinstance(raw, str) and raw in refs:
            preferred_commander_ref = raw
    if preferred_deputy_ref is None and kind in {"wing", "field_force"}:
        raw = offices.get("deputy_field_commander")
        if isinstance(raw, str) and raw in refs:
            preferred_deputy_ref = raw

    structure = _build_node(
        refs, records, kind=kind, top_level=True,
        preferred_commander_ref=preferred_commander_ref,
        preferred_deputy_ref=preferred_deputy_ref,
    )
    structure["deployment_headcount"] = len(refs)
    structure["creates_people"] = False
    structure["ownership_rule"] = "references_exact_deployed_members_only"
    structure["command_effect_rule"] = "coordination_only_no_physical_stat_bonus"
    return structure


def validate_deployment_structure(structure: Mapping[str, Any]) -> bool:
    """Fail if a person is duplicated or omitted anywhere in the command tree."""
    top_members = structure.get("member_refs")
    if not isinstance(top_members, Sequence) or isinstance(top_members, (str, bytes, bytearray)):
        raise ValueError("deployment structure member list invalid")
    expected = list(top_members)
    if len(expected) != len(set(expected)):
        raise ValueError("deployment structure duplicates top-level members")

    seen_staff: set[str] = set()

    def walk(node: Mapping[str, Any], *, root: bool) -> set[str]:
        refs = node.get("member_refs")
        if not isinstance(refs, Sequence) or isinstance(refs, (str, bytes, bytearray)):
            raise ValueError("formation member list invalid")
        refs_set = set(refs)
        commander = node.get("commander_ref")
        deputy = node.get("deputy_ref")
        for staff in (commander, deputy):
            if staff is None:
                continue
            if staff not in refs_set:
                raise ValueError("formation command staff not in formation")
            if staff in seen_staff:
                raise ValueError("person holds multiple simultaneous formation command slots")
            seen_staff.add(staff)
        children = node.get("child_formations", [])
        if not isinstance(children, Sequence) or isinstance(children, (str, bytes, bytearray)):
            raise ValueError("formation children invalid")
        child_people: set[str] = set()
        for child in children:
            if not isinstance(child, Mapping):
                raise ValueError("formation child invalid")
            child_set = walk(child, root=False)
            if child_people & child_set:
                raise ValueError("person duplicated across child formations")
            child_people |= child_set
        if children:
            staff = {x for x in (commander, deputy) if isinstance(x, str)}
            if refs_set != child_people | staff:
                raise ValueError("parent formation does not conserve child people plus staff")
        return refs_set

    root_set = walk(structure, root=True)
    if root_set != set(expected):
        raise ValueError("deployment structure does not conserve top-level members")
    return True
