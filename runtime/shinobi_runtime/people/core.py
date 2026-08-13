"""Representation-neutral stable identity cores."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Optional, Tuple

from shinobi_runtime.sim import CampaignDate, CampaignTime


_EXACT_LOGICAL_LIFE_STATUS = {
    "active": "alive",
    "ill": "alive",
    "alive": "alive",
    "dead": "dead",
    "missing": "missing",
    "unknown": "unknown",
}


def _strings(values: Any, name: str) -> Tuple[str, ...]:
    if values is None:
        return ()
    if not isinstance(values, (list, tuple)) or any(
        not isinstance(value, str) or not value for value in values
    ):
        raise ValueError(f"{name} must contain non-empty strings")
    result = tuple(values)
    if len(result) != len(set(result)):
        raise ValueError(f"{name} contains duplicates")
    return result


@dataclass(frozen=True)
class PersonCore:
    person_id: str
    display_name: str
    birth_date: Optional[CampaignDate]
    birth_date_source: Optional[str]
    origin: Optional[str]
    life_status: str
    affiliation: Optional[str]
    rank_or_status: Optional[str]
    roles: Tuple[str, ...]
    duties: Tuple[str, ...]
    current_host_ref: Optional[str]
    cohort_ref: Optional[str]
    placement_ref: Optional[str]
    source_ref: str
    source_ordinal: Optional[int]
    deterministic_seed: str
    representation: str
    component_refs: Tuple[str, ...] = ()
    aliases: Tuple[str, ...] = ()
    resolved_through: Optional[CampaignTime] = None
    coverage_ref: Optional[str] = None
    identity_cues: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field in ("person_id", "display_name", "source_ref", "deterministic_seed"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field} must be non-empty")
        if self.life_status not in ("alive", "dead", "missing", "unknown"):
            raise ValueError("unsupported life status")
        if self.representation not in ("exact", "rostered_cohort"):
            raise ValueError("person core cannot use aggregate representation")
        if self.source_ordinal is not None and (
            isinstance(self.source_ordinal, bool)
            or not isinstance(self.source_ordinal, int)
            or self.source_ordinal < 0
        ):
            raise ValueError("source ordinal must be a non-negative integer or null")
        for field in ("roles", "duties", "component_refs", "aliases"):
            object.__setattr__(self, field, _strings(getattr(self, field), field))
        cues = dict(self.identity_cues)
        if any(
            not isinstance(key, str)
            or not key
            or not isinstance(value, str)
            or not value
            for key, value in cues.items()
        ):
            raise ValueError("identity cues must map non-empty names to non-empty text")
        object.__setattr__(self, "identity_cues", MappingProxyType(cues))

    def to_record(self) -> Mapping[str, Any]:
        return {
            "person_id": self.person_id,
            "display_name": self.display_name,
            "aliases": list(self.aliases),
            "birth_date": None if self.birth_date is None else str(self.birth_date),
            "birth_date_source": self.birth_date_source,
            "origin": self.origin,
            "life_status": self.life_status,
            "affiliation": self.affiliation,
            "rank_or_status": self.rank_or_status,
            "roles": list(self.roles),
            "duties": list(self.duties),
            "current_host_ref": self.current_host_ref,
            "cohort_ref": self.cohort_ref,
            "placement_ref": self.placement_ref,
            "source_ref": self.source_ref,
            "source_ordinal": self.source_ordinal,
            "deterministic_seed": self.deterministic_seed,
            "representation": self.representation,
            "component_refs": list(self.component_refs),
            "resolved_through": (
                None if self.resolved_through is None else str(self.resolved_through)
            ),
            "coverage_ref": self.coverage_ref,
            "identity_cues": dict(self.identity_cues),
        }


@dataclass(frozen=True)
class PersonSheet:
    core: Mapping[str, Any]
    cohort_baseline: Mapping[str, Any]
    components: Mapping[str, Mapping[str, Any]]

    def to_record(self) -> Mapping[str, Any]:
        return {
            "core": dict(self.core),
            "cohort_baseline": dict(self.cohort_baseline),
            "components": {
                key: dict(value) for key, value in sorted(self.components.items())
            },
        }


def assemble_sheet(
    core: PersonCore,
    *,
    cohort_baseline: Optional[Mapping[str, Any]] = None,
    components: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> PersonSheet:
    component_map = dict(components or {})
    if any(not isinstance(name, str) or not name for name in component_map):
        raise ValueError("component namespaces must be non-empty")
    if set(component_map) != set(core.component_refs):
        raise ValueError("loaded components do not match the core component references")
    if core.representation == "rostered_cohort" and cohort_baseline is None:
        raise ValueError("rostered cohort person requires a cohort baseline")
    return PersonSheet(
        core=core.to_record(),
        cohort_baseline=dict(cohort_baseline or {}),
        components=component_map,
    )


def core_from_exact(
    record: Mapping[str, Any], *, component_ref: str, source_ref: str
) -> PersonCore:
    if record.get("schema") != "shinobi_character":
        raise ValueError("exact adapter requires a shinobi_character owner")
    birth = record.get("birth_date")
    background = record.get("background", {})
    if not isinstance(background, Mapping):
        background = {}
    goal_state = record.get("goal_state", {})
    if not isinstance(goal_state, Mapping):
        goal_state = {}
    raw_life_status = record.get("life_status", "unknown")
    if (
        not isinstance(raw_life_status, str)
        or raw_life_status not in _EXACT_LOGICAL_LIFE_STATUS
    ):
        raise ValueError(f"unsupported exact life status: {raw_life_status!r}")
    return PersonCore(
        person_id=record.get("owner_id"),
        display_name=record.get("name"),
        birth_date=None if birth is None else CampaignDate.parse(birth),
        birth_date_source=record.get("birth_date_source"),
        origin=background.get("origin"),
        life_status=_EXACT_LOGICAL_LIFE_STATUS[raw_life_status],
        affiliation=record.get("village_or_affiliation"),
        rank_or_status=record.get("official_rank_or_status"),
        roles=_strings(record.get("roles", []), "roles"),
        duties=_strings(goal_state.get("institutional_duties", []), "duties"),
        current_host_ref=record.get("current_location_id"),
        cohort_ref=None,
        placement_ref=record.get("team_status"),
        source_ref=source_ref,
        source_ordinal=None,
        deterministic_seed="existing:" + record.get("owner_id"),
        representation="exact",
        component_refs=(component_ref,),
    )


def core_from_person(
    record: Mapping[str, Any], *, component_ref: str, source_ref: str
) -> PersonCore:
    if record.get("schema") != "person":
        raise ValueError("person adapter requires a person owner")
    birth = record.get("birth_date")
    resolved = record.get("resolved_through")
    return PersonCore(
        person_id=record.get("id"),
        display_name=record.get("name"),
        birth_date=None if birth is None else CampaignDate.parse(birth),
        birth_date_source=record.get("birth_date_source"),
        origin=record.get("origin"),
        life_status="alive" if record.get("health", {}).get("status") != "dead" else "dead",
        affiliation=record.get("owner"),
        rank_or_status=record.get("rank"),
        roles=_strings(record.get("offices", []), "roles"),
        duties=_strings(record.get("duties", []), "duties"),
        current_host_ref=record.get("loc"),
        cohort_ref=record.get("cohort_ref"),
        placement_ref=record.get("assignment"),
        source_ref=source_ref,
        source_ordinal=None,
        deterministic_seed="existing:" + record.get("id"),
        representation="exact",
        component_refs=(component_ref,),
        resolved_through=None if resolved is None else CampaignTime.parse(resolved),
        coverage_ref=record.get("coverage_ref"),
    )


def core_from_registry(
    registry: Mapping[str, Any], *, person_id: str, source_ref: str
) -> PersonCore:
    """Load one stable identity from a bounded roster registry.

    The returned core is a full logical person, but shared mechanical state
    remains at ``cohort_ref`` until an explicitly referenced component diverges.
    """

    if registry.get("schema") != "person-core-registry":
        raise ValueError("registry adapter requires a person-core-registry owner")
    people = registry.get("people")
    if not isinstance(people, Mapping) or person_id not in people:
        raise KeyError(person_id)
    record = people[person_id]
    if not isinstance(record, Mapping) or record.get("id") != person_id:
        raise ValueError("person-core registry key/id mismatch")
    component_refs = record.get("component_refs")
    if not isinstance(component_refs, Mapping):
        raise ValueError("person core component_refs must be an object")
    identity_cues = record.get("identity_cues")
    if not isinstance(identity_cues, Mapping):
        raise ValueError("person core identity_cues must be an object")
    return PersonCore(
        person_id=person_id,
        display_name=record.get("name"),
        birth_date=(None if record.get("birth_date") is None else CampaignDate.parse(record.get("birth_date"))),
        birth_date_source=record.get("birth_date_source"),
        origin=record.get("origin"),
        life_status=record.get("life_status"),
        affiliation=record.get("affiliation_ref") or registry.get("owner_ref"),
        rank_or_status=None,
        roles=((record.get("role_profile_ref"),) if isinstance(record.get("role_profile_ref"), str) and record.get("role_profile_ref") else ()),
        duties=_strings(record.get("duty_tags", []), "duty_tags"),
        current_host_ref=record.get("location_ref"),
        cohort_ref=record.get("cohort_ref"),
        placement_ref=record.get("role_profile_ref"),
        source_ref=source_ref,
        source_ordinal=record.get("cohort_slot"),
        deterministic_seed=f"{registry.get('id')}:{person_id}",
        representation="rostered_cohort",
        component_refs=tuple(sorted(component_refs)),
        aliases=_strings(record.get("aliases", []), "aliases"),
        resolved_through=CampaignTime.parse(record.get("resolved_through")),
        coverage_ref=record.get("coverage_ref"),
        identity_cues=identity_cues,
    )
