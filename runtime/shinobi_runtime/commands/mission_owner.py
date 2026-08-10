"""Persisted executable-mission authority around the pure mission reducer.

The reducer intentionally owns only lifecycle mechanics.  This adapter adds the
minimum temporal and institutional references a mutable campaign owner needs;
it does not duplicate issuer, participant, operation, account, or inventory
facts from their owning systems.
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
            "state",
            "participant_refs",
            "objectives",
            "settlement_terms",
            "terminal_reason_ref",
            "settlement",
        )
    )

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
        if actual != cls.RECORD_FIELDS:
            raise ValueError(
                "mission owner fields differ: missing=%s unknown=%s"
                % (sorted(cls.RECORD_FIELDS - actual), sorted(actual - cls.RECORD_FIELDS))
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


__all__ = ["MissionOwner", "mission_owner_path"]
