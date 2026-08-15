"""Pure deterministic mission, objective, and settlement lifecycle.

This module stores only typed state and evidence references.  It contains no
narration fields, clock reads, repository access, representation-specific
participant logic, or model inference.  Callers must persist its returned record
inside the surrounding transaction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, FrozenSet, Mapping, Optional, Sequence, Tuple


MISSION_STATES: FrozenSet[str] = frozenset(
    (
        "offered",
        "accepted",
        "active",
        "resolving",
        "succeeded",
        "failed",
        "aborted",
        "expired",
    )
)
TERMINAL_MISSION_STATES: FrozenSet[str] = frozenset(
    ("succeeded", "failed", "aborted", "expired")
)
LEGAL_MISSION_TRANSITIONS: Mapping[str, FrozenSet[str]] = MappingProxyType(
    {
        "offered": frozenset(("accepted", "expired")),
        "accepted": frozenset(("active", "aborted", "expired")),
        "active": frozenset(("resolving", "aborted", "expired")),
        "resolving": frozenset(
            ("succeeded", "failed", "aborted", "expired")
        ),
        "succeeded": frozenset(),
        "failed": frozenset(),
        "aborted": frozenset(),
        "expired": frozenset(),
    }
)

OBJECTIVE_KINDS: FrozenSet[str] = frozenset(
    (
        "reach",
        "observe",
        "identify",
        "investigate",
        "intercept",
        "recover",
        "deliver",
        "secure",
        "protect",
        "preserve",
        "escort",
        "rescue",
        "capture",
        "restrain",
        "defeat",
        "hold",
        "destroy",
        "sabotage",
        "extract",
        "escape",
        "survive",
        "negotiate",
        "conceal",
        "prevent",
    )
)
OBJECTIVE_STATUSES: FrozenSet[str] = frozenset(
    ("pending", "in_progress", "succeeded", "failed")
)
TERMINAL_OBJECTIVE_STATUSES: FrozenSet[str] = frozenset(("succeeded", "failed"))
LEGAL_OBJECTIVE_TRANSITIONS: Mapping[str, FrozenSet[str]] = MappingProxyType(
    {
        "pending": frozenset(("in_progress", "succeeded", "failed")),
        "in_progress": frozenset(("succeeded", "failed")),
        "succeeded": frozenset(),
        "failed": frozenset(),
    }
)

_STABLE_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")


class MissionTransitionError(ValueError):
    """Requested mission or objective state transition is illegal."""


class ObjectiveDependencyError(MissionTransitionError):
    """An objective cannot advance until all dependencies succeeded."""


class SettlementConflictError(ValueError):
    """A terminal mission was already settled with a different token."""


def _stable_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not _STABLE_ID.fullmatch(value):
        raise ValueError(f"{field} must be a stable lowercase semantic ID")
    return value


def _reference(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or any(character in value for character in ("\x00", "\r", "\n"))
    ):
        raise ValueError(f"{field} must be a non-empty reference")
    return value


def _optional_reference(value: object, field: str) -> Optional[str]:
    if value is None:
        return None
    return _reference(value, field)


def _exact_fields(record: Mapping[str, Any], expected: FrozenSet[str], label: str) -> None:
    if not isinstance(record, Mapping):
        raise TypeError(f"{label} record must be an object")
    actual = frozenset(record)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ValueError(f"{label} fields differ: missing={missing} unknown={unknown}")


@dataclass(frozen=True)
class MissionObjective:
    objective_id: str
    kind: str
    required: bool
    dependencies: Tuple[str, ...] = ()
    status: str = "pending"
    progress_milli: int = 0
    resolution_ref: Optional[str] = None

    RECORD_FIELDS = frozenset(
        (
            "objective_id",
            "kind",
            "required",
            "dependencies",
            "status",
            "progress_milli",
            "resolution_ref",
        )
    )

    def __post_init__(self) -> None:
        _stable_id(self.objective_id, "objective_id")
        if self.kind not in OBJECTIVE_KINDS:
            raise ValueError(f"unsupported objective kind: {self.kind}")
        if not isinstance(self.required, bool):
            raise TypeError("objective required flag must be boolean")
        dependencies = tuple(sorted(self.dependencies))
        for dependency in dependencies:
            _stable_id(dependency, "objective dependency")
        if len(dependencies) != len(set(dependencies)):
            raise ValueError("objective dependencies must be unique")
        if self.objective_id in dependencies:
            raise ValueError("objective may not depend on itself")
        object.__setattr__(self, "dependencies", dependencies)
        if self.status not in OBJECTIVE_STATUSES:
            raise ValueError(f"unsupported objective status: {self.status}")
        if (
            isinstance(self.progress_milli, bool)
            or not isinstance(self.progress_milli, int)
            or not 0 <= self.progress_milli <= 1000
        ):
            raise ValueError("objective progress_milli must be in 0..1000")
        if self.status == "pending" and self.progress_milli != 0:
            raise ValueError("pending objective progress must be zero")
        if self.status == "in_progress" and self.progress_milli >= 1000:
            raise ValueError("in-progress objective must remain below 1000")
        if self.status == "succeeded" and self.progress_milli != 1000:
            raise ValueError("succeeded objective progress must equal 1000")
        if self.status in TERMINAL_OBJECTIVE_STATUSES:
            _reference(self.resolution_ref, "objective resolution_ref")
        elif self.resolution_ref is not None:
            raise ValueError("nonterminal objective may not have resolution_ref")

    def to_record(self) -> Mapping[str, Any]:
        return {
            "objective_id": self.objective_id,
            "kind": self.kind,
            "required": self.required,
            "dependencies": list(self.dependencies),
            "status": self.status,
            "progress_milli": self.progress_milli,
            "resolution_ref": self.resolution_ref,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "MissionObjective":
        _exact_fields(record, cls.RECORD_FIELDS, "mission objective")
        dependencies = record["dependencies"]
        if not isinstance(dependencies, list):
            raise TypeError("objective dependencies must be an array")
        return cls(
            objective_id=record["objective_id"],
            kind=record["kind"],
            required=record["required"],
            dependencies=tuple(dependencies),
            status=record["status"],
            progress_milli=record["progress_milli"],
            resolution_ref=record["resolution_ref"],
        )


@dataclass(frozen=True)
class SettlementTerm:
    term_id: str
    direction: str
    account_ref: str
    asset_ref: str
    quantity: int
    applies_on: Tuple[str, ...]
    objective_id: Optional[str] = None
    objective_status: Optional[str] = None

    RECORD_FIELDS = frozenset(
        (
            "term_id",
            "direction",
            "account_ref",
            "asset_ref",
            "quantity",
            "applies_on",
            "objective_id",
            "objective_status",
        )
    )

    def __post_init__(self) -> None:
        _stable_id(self.term_id, "settlement term_id")
        if self.direction not in ("reward", "cost"):
            raise ValueError("settlement direction must be reward or cost")
        _reference(self.account_ref, "settlement account_ref")
        _reference(self.asset_ref, "settlement asset_ref")
        if (
            isinstance(self.quantity, bool)
            or not isinstance(self.quantity, int)
            or self.quantity <= 0
        ):
            raise ValueError("settlement quantity must be a positive integer")
        applies_on = tuple(sorted(self.applies_on))
        if not applies_on or len(applies_on) != len(set(applies_on)):
            raise ValueError("settlement applies_on must be non-empty and unique")
        if not set(applies_on) <= TERMINAL_MISSION_STATES:
            raise ValueError("settlement applies_on contains a nonterminal state")
        object.__setattr__(self, "applies_on", applies_on)
        if (self.objective_id is None) != (self.objective_status is None):
            raise ValueError(
                "objective_id and objective_status must either both be set or both be null"
            )
        if self.objective_id is not None:
            _stable_id(self.objective_id, "settlement objective_id")
            if self.objective_status not in TERMINAL_OBJECTIVE_STATUSES:
                raise ValueError(
                    "settlement objective_status must be succeeded or failed"
                )

    def to_record(self) -> Mapping[str, Any]:
        return {
            "term_id": self.term_id,
            "direction": self.direction,
            "account_ref": self.account_ref,
            "asset_ref": self.asset_ref,
            "quantity": self.quantity,
            "applies_on": list(self.applies_on),
            "objective_id": self.objective_id,
            "objective_status": self.objective_status,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "SettlementTerm":
        _exact_fields(record, cls.RECORD_FIELDS, "settlement term")
        applies_on = record["applies_on"]
        if not isinstance(applies_on, list):
            raise TypeError("settlement applies_on must be an array")
        return cls(
            term_id=record["term_id"],
            direction=record["direction"],
            account_ref=record["account_ref"],
            asset_ref=record["asset_ref"],
            quantity=record["quantity"],
            applies_on=tuple(applies_on),
            objective_id=record["objective_id"],
            objective_status=record["objective_status"],
        )


@dataclass(frozen=True)
class MissionSettlement:
    mission_id: str
    outcome: str
    settlement_token: str
    reward_term_ids: Tuple[str, ...]
    cost_term_ids: Tuple[str, ...]

    RECORD_FIELDS = frozenset(
        (
            "mission_id",
            "outcome",
            "settlement_token",
            "reward_term_ids",
            "cost_term_ids",
        )
    )

    def __post_init__(self) -> None:
        _stable_id(self.mission_id, "settlement mission_id")
        if self.outcome not in TERMINAL_MISSION_STATES:
            raise ValueError("settlement outcome must be terminal")
        _reference(self.settlement_token, "settlement_token")
        rewards = tuple(sorted(self.reward_term_ids))
        costs = tuple(sorted(self.cost_term_ids))
        for term_id in rewards + costs:
            _stable_id(term_id, "settlement term reference")
        if len(rewards) != len(set(rewards)) or len(costs) != len(set(costs)):
            raise ValueError("settlement term references must be unique")
        if set(rewards) & set(costs):
            raise ValueError("one settlement term cannot be both reward and cost")
        object.__setattr__(self, "reward_term_ids", rewards)
        object.__setattr__(self, "cost_term_ids", costs)

    def to_record(self) -> Mapping[str, Any]:
        return {
            "mission_id": self.mission_id,
            "outcome": self.outcome,
            "settlement_token": self.settlement_token,
            "reward_term_ids": list(self.reward_term_ids),
            "cost_term_ids": list(self.cost_term_ids),
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "MissionSettlement":
        _exact_fields(record, cls.RECORD_FIELDS, "mission settlement")
        rewards = record["reward_term_ids"]
        costs = record["cost_term_ids"]
        if not isinstance(rewards, list) or not isinstance(costs, list):
            raise TypeError("settlement term IDs must be arrays")
        return cls(
            mission_id=record["mission_id"],
            outcome=record["outcome"],
            settlement_token=record["settlement_token"],
            reward_term_ids=tuple(rewards),
            cost_term_ids=tuple(costs),
        )


def _has_dependency_cycle(objectives: Mapping[str, MissionObjective]) -> bool:
    visiting = set()
    visited = set()

    def visit(objective_id: str) -> bool:
        if objective_id in visiting:
            return True
        if objective_id in visited:
            return False
        visiting.add(objective_id)
        if any(visit(dependency) for dependency in objectives[objective_id].dependencies):
            return True
        visiting.remove(objective_id)
        visited.add(objective_id)
        return False

    return any(visit(objective_id) for objective_id in sorted(objectives))


def _term_applies(
    term: SettlementTerm,
    state: str,
    objectives: Mapping[str, MissionObjective],
) -> bool:
    if state not in term.applies_on:
        return False
    if term.objective_id is None:
        return True
    return objectives[term.objective_id].status == term.objective_status


@dataclass(frozen=True)
class Mission:
    mission_id: str
    state: str
    participant_refs: Tuple[str, ...]
    objectives: Tuple[MissionObjective, ...]
    settlement_terms: Tuple[SettlementTerm, ...] = ()
    terminal_reason_ref: Optional[str] = None
    settlement: Optional[MissionSettlement] = None

    RECORD_FIELDS = frozenset(
        (
            "mission_id",
            "state",
            "participant_refs",
            "objectives",
            "settlement_terms",
            "terminal_reason_ref",
            "settlement",
        )
    )

    def __post_init__(self) -> None:
        _stable_id(self.mission_id, "mission_id")
        if self.state not in MISSION_STATES:
            raise ValueError(f"unsupported mission state: {self.state}")
        participants = tuple(sorted(self.participant_refs))
        for participant_ref in participants:
            _reference(participant_ref, "participant_ref")
        if len(participants) != len(set(participants)):
            raise ValueError("mission participant_refs must be unique")
        object.__setattr__(self, "participant_refs", participants)

        objectives = tuple(sorted(self.objectives, key=lambda item: item.objective_id))
        if not objectives:
            raise ValueError("mission must contain at least one objective")
        objective_by_id = {objective.objective_id: objective for objective in objectives}
        if len(objective_by_id) != len(objectives):
            raise ValueError("mission objective IDs must be unique")
        if not any(objective.required for objective in objectives):
            raise ValueError("mission must contain at least one required objective")
        for objective in objectives:
            missing = set(objective.dependencies) - set(objective_by_id)
            if missing:
                raise ValueError(
                    "objective %s has missing dependencies: %s"
                    % (objective.objective_id, sorted(missing))
                )
        if _has_dependency_cycle(objective_by_id):
            raise ValueError("mission objective dependencies must be acyclic")
        object.__setattr__(self, "objectives", objectives)

        terms = tuple(sorted(self.settlement_terms, key=lambda item: item.term_id))
        term_by_id = {term.term_id: term for term in terms}
        if len(term_by_id) != len(terms):
            raise ValueError("mission settlement term IDs must be unique")
        for term in terms:
            if term.objective_id is not None and term.objective_id not in objective_by_id:
                raise ValueError(
                    f"settlement term {term.term_id} references an unknown objective"
                )
        object.__setattr__(self, "settlement_terms", terms)

        if self.state in ("aborted", "expired"):
            _reference(self.terminal_reason_ref, "terminal_reason_ref")
        elif self.terminal_reason_ref is not None:
            raise ValueError("only aborted/expired missions use terminal_reason_ref")
        required = tuple(objective for objective in objectives if objective.required)
        if self.state == "succeeded" and not all(
            objective.status == "succeeded" for objective in required
        ):
            raise ValueError("succeeded mission requires every required objective")
        if self.state == "failed" and not any(
            objective.status == "failed" for objective in required
        ):
            raise ValueError("failed mission requires a failed required objective")

        if self.settlement is not None:
            if self.state not in TERMINAL_MISSION_STATES:
                raise ValueError("nonterminal mission may not be settled")
            if self.settlement.mission_id != self.mission_id:
                raise ValueError("settlement mission ID mismatch")
            if self.settlement.outcome != self.state:
                raise ValueError("settlement outcome mismatch")
            applicable = tuple(
                term
                for term in terms
                if _term_applies(term, self.state, objective_by_id)
            )
            expected_rewards = tuple(
                term.term_id for term in applicable if term.direction == "reward"
            )
            expected_costs = tuple(
                term.term_id for term in applicable if term.direction == "cost"
            )
            if self.settlement.reward_term_ids != expected_rewards:
                raise ValueError("settlement reward terms do not match mission outcome")
            if self.settlement.cost_term_ids != expected_costs:
                raise ValueError("settlement cost terms do not match mission outcome")

    @property
    def objective_by_id(self) -> Mapping[str, MissionObjective]:
        return MappingProxyType(
            {objective.objective_id: objective for objective in self.objectives}
        )

    def to_record(self) -> Mapping[str, Any]:
        return {
            "mission_id": self.mission_id,
            "state": self.state,
            "participant_refs": list(self.participant_refs),
            "objectives": [objective.to_record() for objective in self.objectives],
            "settlement_terms": [term.to_record() for term in self.settlement_terms],
            "terminal_reason_ref": self.terminal_reason_ref,
            "settlement": None if self.settlement is None else self.settlement.to_record(),
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "Mission":
        _exact_fields(record, cls.RECORD_FIELDS, "mission")
        participants = record["participant_refs"]
        objectives = record["objectives"]
        terms = record["settlement_terms"]
        settlement = record["settlement"]
        if not isinstance(participants, list):
            raise TypeError("mission participant_refs must be an array")
        if not isinstance(objectives, list):
            raise TypeError("mission objectives must be an array")
        if not isinstance(terms, list):
            raise TypeError("mission settlement_terms must be an array")
        if settlement is not None and not isinstance(settlement, Mapping):
            raise TypeError("mission settlement must be an object or null")
        return cls(
            mission_id=record["mission_id"],
            state=record["state"],
            participant_refs=tuple(participants),
            objectives=tuple(
                MissionObjective.from_record(objective) for objective in objectives
            ),
            settlement_terms=tuple(SettlementTerm.from_record(term) for term in terms),
            terminal_reason_ref=record["terminal_reason_ref"],
            settlement=(
                None
                if settlement is None
                else MissionSettlement.from_record(settlement)
            ),
        )


@dataclass(frozen=True)
class SettlementResult:
    mission: Mission
    settlement: MissionSettlement
    applied: bool


def transition_mission(
    mission: Mission,
    target_state: str,
    *,
    reason_ref: Optional[str] = None,
) -> Mission:
    """Apply a legal non-derived transition or explicit abort/expiry."""

    if target_state not in MISSION_STATES:
        raise MissionTransitionError(f"unsupported mission state: {target_state}")
    if target_state == mission.state:
        if reason_ref == mission.terminal_reason_ref:
            return mission
        raise MissionTransitionError("same-state transition changes terminal reason")
    if target_state in ("succeeded", "failed"):
        raise MissionTransitionError(
            "mission success/failure must be derived from objective outcomes"
        )
    if target_state not in LEGAL_MISSION_TRANSITIONS[mission.state]:
        raise MissionTransitionError(
            f"illegal mission transition: {mission.state} -> {target_state}"
        )
    if target_state in ("aborted", "expired"):
        _reference(reason_ref, "reason_ref")
        return replace(
            mission,
            state=target_state,
            terminal_reason_ref=reason_ref,
        )
    if reason_ref is not None:
        raise MissionTransitionError("nonterminal transition may not have reason_ref")
    return replace(mission, state=target_state)


def update_objective(
    mission: Mission,
    objective_id: str,
    target_status: str,
    *,
    progress_milli: Optional[int] = None,
    resolution_ref: Optional[str] = None,
) -> Mission:
    """Advance one objective while enforcing dependency and terminal gates."""

    if mission.state not in ("active", "resolving"):
        raise MissionTransitionError(
            "objectives may change only while a mission is active or resolving"
        )
    _stable_id(objective_id, "objective_id")
    objective_by_id = dict(mission.objective_by_id)
    try:
        current = objective_by_id[objective_id]
    except KeyError as exc:
        raise KeyError(f"unknown mission objective: {objective_id}") from exc
    if target_status not in OBJECTIVE_STATUSES:
        raise MissionTransitionError(
            f"unsupported objective status: {target_status}"
        )

    if target_status == current.status:
        intended_progress = (
            current.progress_milli if progress_milli is None else progress_milli
        )
        intended_resolution = (
            current.resolution_ref
            if resolution_ref is None and current.status in TERMINAL_OBJECTIVE_STATUSES
            else resolution_ref
        )
        if (
            intended_progress == current.progress_milli
            and intended_resolution == current.resolution_ref
        ):
            return mission
        if current.status in TERMINAL_OBJECTIVE_STATUSES:
            raise MissionTransitionError("terminal objective is immutable")
    elif target_status not in LEGAL_OBJECTIVE_TRANSITIONS[current.status]:
        raise MissionTransitionError(
            f"illegal objective transition: {current.status} -> {target_status}"
        )

    if target_status in ("in_progress", "succeeded"):
        unsatisfied = tuple(
            dependency
            for dependency in current.dependencies
            if objective_by_id[dependency].status != "succeeded"
        )
        if unsatisfied:
            raise ObjectiveDependencyError(
                f"objective {objective_id} has unsatisfied dependencies: {unsatisfied}"
            )

    if progress_milli is None:
        if target_status == "succeeded":
            next_progress = 1000
        else:
            next_progress = current.progress_milli
    else:
        next_progress = progress_milli
    if (
        isinstance(next_progress, bool)
        or not isinstance(next_progress, int)
        or next_progress < current.progress_milli
    ):
        raise MissionTransitionError("objective progress must be a nondecreasing integer")

    next_objective = MissionObjective(
        objective_id=current.objective_id,
        kind=current.kind,
        required=current.required,
        dependencies=current.dependencies,
        status=target_status,
        progress_milli=next_progress,
        resolution_ref=resolution_ref,
    )
    updated = tuple(
        next_objective if objective.objective_id == objective_id else objective
        for objective in mission.objectives
    )
    return replace(mission, objectives=updated)


def derive_mission_outcome(mission: Mission) -> Mission:
    """Derive success/failure solely from required objective terminals."""

    if mission.state in TERMINAL_MISSION_STATES:
        return mission
    if mission.state != "resolving":
        raise MissionTransitionError(
            "mission outcome may be derived only from resolving state"
        )
    required = tuple(
        objective for objective in mission.objectives if objective.required
    )
    if any(objective.status == "failed" for objective in required):
        return replace(mission, state="failed")
    if all(objective.status == "succeeded" for objective in required):
        return replace(mission, state="succeeded")
    return mission


def settle_mission(mission: Mission, settlement_token: str) -> SettlementResult:
    """Select declared reward/cost terms once for a terminal mission outcome."""

    _reference(settlement_token, "settlement_token")
    if mission.state not in TERMINAL_MISSION_STATES:
        raise MissionTransitionError("only terminal missions may be settled")
    if mission.settlement is not None:
        if mission.settlement.settlement_token != settlement_token:
            raise SettlementConflictError(
                "mission was already settled with a different token"
            )
        return SettlementResult(mission, mission.settlement, applied=False)

    objective_by_id = dict(mission.objective_by_id)
    applicable = tuple(
        term
        for term in mission.settlement_terms
        if _term_applies(term, mission.state, objective_by_id)
    )
    settlement = MissionSettlement(
        mission_id=mission.mission_id,
        outcome=mission.state,
        settlement_token=settlement_token,
        reward_term_ids=tuple(
            term.term_id for term in applicable if term.direction == "reward"
        ),
        cost_term_ids=tuple(
            term.term_id for term in applicable if term.direction == "cost"
        ),
    )
    settled_mission = replace(mission, settlement=settlement)
    return SettlementResult(settled_mission, settlement, applied=True)
