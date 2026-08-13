"""Persisted executable-mission authority around the pure mission reducer.

The reducer intentionally owns only lifecycle mechanics. This adapter adds the
minimum temporal, institutional, and operational references a mutable campaign
owner needs; it does not duplicate issuer, participant, operation, account, or
inventory facts from their owning systems.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Mapping, Optional, Tuple

from shinobi_runtime.reducers.missions import Mission
from shinobi_runtime.sim.events import CampaignTime


_MISSION_ID = re.compile(r"^mission\.[a-z0-9][a-z0-9._:-]*$")
_TERMINAL_STATES = frozenset(("succeeded", "failed", "aborted", "expired"))
_STARTED_STATES = frozenset(("active", "resolving", "succeeded", "failed"))
_SUBJECT_KINDS = frozenset(("person", "asset", "place", "information"))


def _required_ref(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 256
        or any(character in value for character in ("\x00", "\r", "\n"))
    ):
        raise ValueError(f"{field} must be a bounded non-empty reference")
    return value


def _optional_ref(value: object, field: str) -> Optional[str]:
    if value is None:
        return None
    return _required_ref(value, field)


def _bounded_text(value: object, field: str, *, maximum: int = 1024) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or any(character in value for character in ("\x00", "\r"))
    ):
        raise ValueError(f"{field} must be bounded non-empty text")
    return value


def _time(value: object, field: str) -> CampaignTime:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a campaign timestamp")
    try:
        return CampaignTime.parse(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} is not a canonical campaign timestamp") from exc


def _optional_time(value: object, field: str) -> Optional[CampaignTime]:
    if value is None:
        return None
    return _time(value, field)


@dataclass(frozen=True)
class MissionBrief:
    """Bounded player-operational facts required to execute a mission.

    A brief carries only facts the issuing authority has actually committed to
    the assignment. Hidden opposition remains hidden; uncertainty is represented
    explicitly in threat/intelligence text instead of being invented by the GM.
    """

    briefing_id: str
    objective_kind: str
    subject_kind: str
    subject_ref: Optional[str]
    subject_label: str
    report_place_ref: str
    origin_place_ref: str
    destination_place_ref: Optional[str]
    route_id: Optional[str]
    threat_summary: str
    threat_source_ref: Optional[str]
    intelligence_constraints: Tuple[str, ...]
    report_at: Optional[CampaignTime]
    depart_by: Optional[CampaignTime]
    completion_condition: str

    RECORD_FIELDS = frozenset(
        (
            "briefing_id",
            "objective_kind",
            "subject_kind",
            "subject_ref",
            "subject_label",
            "report_place_ref",
            "origin_place_ref",
            "destination_place_ref",
            "route_id",
            "threat_summary",
            "threat_source_ref",
            "intelligence_constraints",
            "report_at",
            "depart_by",
            "completion_condition",
        )
    )

    def __post_init__(self) -> None:
        _required_ref(self.briefing_id, "briefing_id")
        _required_ref(self.objective_kind, "objective_kind")
        if self.subject_kind not in _SUBJECT_KINDS:
            raise ValueError("subject_kind must be person, asset, place, or information")
        _optional_ref(self.subject_ref, "subject_ref")
        _bounded_text(self.subject_label, "subject_label", maximum=512)
        _required_ref(self.report_place_ref, "report_place_ref")
        _required_ref(self.origin_place_ref, "origin_place_ref")
        _optional_ref(self.destination_place_ref, "destination_place_ref")
        _optional_ref(self.route_id, "route_id")
        _bounded_text(self.threat_summary, "threat_summary")
        _optional_ref(self.threat_source_ref, "threat_source_ref")
        if (
            not isinstance(self.intelligence_constraints, tuple)
            or len(self.intelligence_constraints) > 8
        ):
            raise ValueError("intelligence_constraints must contain at most 8 items")
        for index, value in enumerate(self.intelligence_constraints):
            _bounded_text(value, f"intelligence_constraints[{index}]", maximum=512)
        _bounded_text(self.completion_condition, "completion_condition")
        if self.report_at is not None and self.depart_by is not None and self.depart_by < self.report_at:
            raise ValueError("depart_by cannot precede report_at")
        if self.destination_place_ref is not None and self.route_id is None:
            raise ValueError("destination briefing requires route_id")

    def to_record(self) -> Mapping[str, Any]:
        return {
            "briefing_id": self.briefing_id,
            "objective_kind": self.objective_kind,
            "subject_kind": self.subject_kind,
            "subject_ref": self.subject_ref,
            "subject_label": self.subject_label,
            "report_place_ref": self.report_place_ref,
            "origin_place_ref": self.origin_place_ref,
            "destination_place_ref": self.destination_place_ref,
            "route_id": self.route_id,
            "threat_summary": self.threat_summary,
            "threat_source_ref": self.threat_source_ref,
            "intelligence_constraints": list(self.intelligence_constraints),
            "report_at": None if self.report_at is None else str(self.report_at),
            "depart_by": None if self.depart_by is None else str(self.depart_by),
            "completion_condition": self.completion_condition,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "MissionBrief":
        if not isinstance(record, Mapping):
            raise TypeError("mission briefing must be an object")
        actual = frozenset(record)
        if actual != cls.RECORD_FIELDS:
            raise ValueError(
                "mission briefing fields differ: missing=%s unknown=%s"
                % (sorted(cls.RECORD_FIELDS - actual), sorted(actual - cls.RECORD_FIELDS))
            )
        constraints = record["intelligence_constraints"]
        if not isinstance(constraints, list) or any(not isinstance(value, str) for value in constraints):
            raise ValueError("mission briefing intelligence constraints must be strings")
        return cls(
            briefing_id=_required_ref(record["briefing_id"], "briefing_id"),
            objective_kind=_required_ref(record["objective_kind"], "objective_kind"),
            subject_kind=str(record["subject_kind"]),
            subject_ref=_optional_ref(record["subject_ref"], "subject_ref"),
            subject_label=_bounded_text(record["subject_label"], "subject_label", maximum=512),
            report_place_ref=_required_ref(record["report_place_ref"], "report_place_ref"),
            origin_place_ref=_required_ref(record["origin_place_ref"], "origin_place_ref"),
            destination_place_ref=_optional_ref(record["destination_place_ref"], "destination_place_ref"),
            route_id=_optional_ref(record["route_id"], "route_id"),
            threat_summary=_bounded_text(record["threat_summary"], "threat_summary"),
            threat_source_ref=_optional_ref(record["threat_source_ref"], "threat_source_ref"),
            intelligence_constraints=tuple(constraints),
            report_at=_optional_time(record["report_at"], "report_at"),
            depart_by=_optional_time(record["depart_by"], "depart_by"),
            completion_condition=_bounded_text(record["completion_condition"], "completion_condition"),
        )


@dataclass(frozen=True)
class MissionOwner:
    mission: Mission
    issuer_ref: str
    authority_ref: str
    mission_rank: str
    funding_holder_ref: str
    escrow_holder_ref: Optional[str]
    opened_at: CampaignTime
    authorized_at: CampaignTime
    starts_at: Optional[CampaignTime]
    deadline_at: Optional[CampaignTime]
    next_due_at: Optional[CampaignTime]
    operation_ref: Optional[str]
    closed_at: Optional[CampaignTime]
    briefing: Optional[MissionBrief] = None

    SCHEMA = "mission-runtime"
    RECORD_FIELDS = frozenset(
        (
            "schema",
            "mission_id",
            "issuer_ref",
            "authority_ref",
            "mission_rank",
            "funding_holder_ref",
            "escrow_holder_ref",
            "opened_at",
            "authorized_at",
            "starts_at",
            "deadline_at",
            "next_due_at",
            "operation_ref",
            "closed_at",
            "briefing",
            "state",
            "participant_refs",
            "objectives",
            "settlement_terms",
            "terminal_reason_ref",
            "settlement",
        )
    )
    LEGACY_RECORD_FIELDS = RECORD_FIELDS - frozenset(("briefing",))

    def __post_init__(self) -> None:
        if not isinstance(self.mission, Mission):
            raise TypeError("mission owner requires a Mission reducer value")
        if not _MISSION_ID.fullmatch(self.mission.mission_id):
            raise ValueError("mission owner ID must begin with mission.")
        _required_ref(self.issuer_ref, "issuer_ref")
        _required_ref(self.authority_ref, "authority_ref")
        if self.mission_rank not in ("D", "C", "B", "A", "S"):
            raise ValueError("mission_rank must be D, C, B, A, or S")
        _required_ref(self.funding_holder_ref, "funding_holder_ref")
        _optional_ref(self.escrow_holder_ref, "escrow_holder_ref")
        _optional_ref(self.operation_ref, "operation_ref")
        if self.briefing is not None and not isinstance(self.briefing, MissionBrief):
            raise TypeError("briefing must be a MissionBrief or null")
        if self.authorized_at > self.opened_at:
            raise ValueError("mission authorization cannot follow opening")
        if self.starts_at is not None and self.starts_at < self.opened_at:
            raise ValueError("mission start cannot precede opening")
        if self.deadline_at is not None and self.deadline_at <= self.opened_at:
            raise ValueError("mission deadline must follow opening")
        if self.next_due_at is not None and self.next_due_at <= self.opened_at:
            raise ValueError("mission next boundary must follow opening")
        if self.closed_at is not None and self.closed_at < self.opened_at:
            raise ValueError("mission closure cannot precede opening")
        if not self.mission.participant_refs:
            raise ValueError("persisted mission requires at least one participant")
        if self.briefing is not None:
            objective_kinds = {objective.kind for objective in self.mission.objectives}
            if self.briefing.objective_kind not in objective_kinds:
                raise ValueError("mission briefing objective_kind is not a mission objective")
            if self.briefing.report_at is not None and self.briefing.report_at < self.opened_at:
                raise ValueError("mission briefing report_at cannot precede opening")
            if self.briefing.depart_by is not None and self.briefing.depart_by < self.opened_at:
                raise ValueError("mission briefing depart_by cannot precede opening")
            if self.deadline_at is not None:
                if self.briefing.report_at is not None and self.briefing.report_at > self.deadline_at:
                    raise ValueError("mission briefing report_at cannot follow mission deadline")
                if self.briefing.depart_by is not None and self.briefing.depart_by > self.deadline_at:
                    raise ValueError("mission briefing depart_by cannot follow mission deadline")

        terminal = self.mission.state in _TERMINAL_STATES
        if self.mission.state in _STARTED_STATES and self.starts_at is None:
            raise ValueError("started mission requires starts_at")
        if (
            self.mission.state not in _STARTED_STATES
            and self.mission.state not in ("aborted", "expired")
            and self.starts_at is not None
        ):
            raise ValueError("unstarted mission may not carry starts_at")
        if terminal:
            if self.closed_at is None:
                raise ValueError("terminal mission requires closed_at")
            if self.next_due_at is not None:
                raise ValueError("terminal mission may not carry next_due_at")
            if self.mission.settlement is None:
                raise ValueError("terminal mission requires exactly-once settlement")
        else:
            if self.closed_at is not None:
                raise ValueError("nonterminal mission may not carry closed_at")
            if self.mission.settlement is not None:
                raise ValueError("nonterminal mission may not be settled")
            if self.next_due_at is None and self.operation_ref is None:
                raise ValueError(
                    "nonterminal mission requires next_due_at or operation_ref"
                )

    @property
    def mission_id(self) -> str:
        return self.mission.mission_id

    def to_record(self) -> Mapping[str, Any]:
        core = self.mission.to_record()
        return {
            "schema": self.SCHEMA,
            "mission_id": self.mission_id,
            "issuer_ref": self.issuer_ref,
            "authority_ref": self.authority_ref,
            "mission_rank": self.mission_rank,
            "funding_holder_ref": self.funding_holder_ref,
            "escrow_holder_ref": self.escrow_holder_ref,
            "opened_at": str(self.opened_at),
            "authorized_at": str(self.authorized_at),
            "starts_at": None if self.starts_at is None else str(self.starts_at),
            "deadline_at": None if self.deadline_at is None else str(self.deadline_at),
            "next_due_at": None if self.next_due_at is None else str(self.next_due_at),
            "operation_ref": self.operation_ref,
            "closed_at": None if self.closed_at is None else str(self.closed_at),
            "briefing": None if self.briefing is None else self.briefing.to_record(),
            "state": core["state"],
            "participant_refs": core["participant_refs"],
            "objectives": core["objectives"],
            "settlement_terms": core["settlement_terms"],
            "terminal_reason_ref": core["terminal_reason_ref"],
            "settlement": core["settlement"],
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "MissionOwner":
        if not isinstance(record, Mapping):
            raise TypeError("mission owner must be an object")
        actual = frozenset(record)
        if actual not in (cls.RECORD_FIELDS, cls.LEGACY_RECORD_FIELDS):
            expected = cls.RECORD_FIELDS if "briefing" in actual else cls.LEGACY_RECORD_FIELDS
            raise ValueError(
                "mission owner fields differ: missing=%s unknown=%s"
                % (sorted(expected - actual), sorted(actual - expected))
            )
        if record["schema"] != cls.SCHEMA:
            raise ValueError("unsupported mission owner schema")
        mission = Mission.from_record(
            {
                "mission_id": record["mission_id"],
                "state": record["state"],
                "participant_refs": record["participant_refs"],
                "objectives": record["objectives"],
                "settlement_terms": record["settlement_terms"],
                "terminal_reason_ref": record["terminal_reason_ref"],
                "settlement": record["settlement"],
            }
        )
        raw_briefing = record.get("briefing")
        briefing = None if raw_briefing is None else MissionBrief.from_record(raw_briefing)
        return cls(
            mission=mission,
            issuer_ref=_required_ref(record["issuer_ref"], "issuer_ref"),
            authority_ref=_required_ref(record["authority_ref"], "authority_ref"),
            mission_rank=str(record["mission_rank"]),
            funding_holder_ref=_required_ref(record["funding_holder_ref"], "funding_holder_ref"),
            escrow_holder_ref=_optional_ref(record["escrow_holder_ref"], "escrow_holder_ref"),
            opened_at=_time(record["opened_at"], "opened_at"),
            authorized_at=_time(record["authorized_at"], "authorized_at"),
            starts_at=_optional_time(record["starts_at"], "starts_at"),
            deadline_at=_optional_time(record["deadline_at"], "deadline_at"),
            next_due_at=_optional_time(record["next_due_at"], "next_due_at"),
            operation_ref=_optional_ref(record["operation_ref"], "operation_ref"),
            closed_at=_optional_time(record["closed_at"], "closed_at"),
            briefing=briefing,
        )

    def with_mission(
        self,
        mission: Mission,
        *,
        effective_at: CampaignTime,
    ) -> "MissionOwner":
        if mission.mission_id != self.mission_id:
            raise ValueError("mission reducer changed stable mission identity")
        starts_at = self.starts_at
        if mission.state in _STARTED_STATES and starts_at is None:
            starts_at = effective_at
        terminal = mission.state in _TERMINAL_STATES
        return replace(
            self,
            mission=mission,
            starts_at=starts_at,
            closed_at=effective_at if terminal else None,
            next_due_at=None if terminal else self.next_due_at,
        )


def mission_owner_path(mission_id: object) -> str:
    if not isinstance(mission_id, str) or not _MISSION_ID.fullmatch(mission_id):
        raise ValueError("mission_id must be a stable mission.* ID")
    if len(mission_id) > 128:
        raise ValueError("mission_id exceeds 128 characters")
    return f"state/mission/{mission_id}.json"


__all__ = ["MissionBrief", "MissionOwner", "mission_owner_path"]
