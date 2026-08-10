"""Framework-independent service injection contracts."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional, Protocol, Tuple

from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.store.paths import normalize_relative_path
from shinobi_runtime.tx.canonical import freeze_json
from shinobi_runtime.tx.manifest import TransactionManifest
from shinobi_runtime.store.overlay import StagedOverlay


MAX_PLAN_WRITE_PATHS = 64
MAX_PLAN_WRITE_BYTES = 4 * 1024 * 1024


class PlannerUnavailableError(RuntimeError):
    """The service is healthy but no gameplay planner is configured."""


class CommandRejectedError(ValueError):
    """A typed player command cannot be planned without clarification."""

    def __init__(self, code: str) -> None:
        if not isinstance(code, str) or not code or len(code) > 128:
            raise ValueError("command rejection code must be bounded text")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CommandPreview:
    status: str
    code: str
    target_revision: int
    affected_refs: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in ("ready", "needs_clarification", "rejected"):
            raise ValueError("unsupported command preview status")
        if not isinstance(self.code, str) or not self.code or len(self.code) > 128:
            raise ValueError("command preview code must be bounded text")
        if (
            isinstance(self.target_revision, bool)
            or not isinstance(self.target_revision, int)
            or self.target_revision < 0
        ):
            raise ValueError("preview target revision must be non-negative")
        refs = tuple(sorted(self.affected_refs))
        if len(refs) > 64 or len(refs) != len(set(refs)):
            raise ValueError("preview affected refs must be unique and bounded")
        if any(
            not isinstance(ref, str)
            or not ref
            or len(ref) > 256
            or any(character in ref for character in ("\x00", "\r", "\n"))
            for ref in refs
        ):
            raise ValueError("preview contains an invalid affected ref")
        object.__setattr__(self, "affected_refs", refs)


OverlayValidator = Callable[[StagedOverlay, TransactionManifest], None]


@dataclass(frozen=True)
class CommandPlan:
    transaction_id: str
    created_at: str
    writes: Mapping[str, Optional[bytes]]
    result: Mapping[str, Any]
    validator: OverlayValidator

    def __post_init__(self) -> None:
        for field in ("transaction_id", "created_at"):
            value = getattr(self, field)
            if (
                not isinstance(value, str)
                or not value
                or len(value) > 160
                or any(character in value for character in ("\x00", "\r", "\n"))
            ):
                raise ValueError(f"{field} must be bounded text")
        if not isinstance(self.writes, Mapping) or not self.writes:
            raise ValueError("command plan requires an explicit write map")
        if len(self.writes) > MAX_PLAN_WRITE_PATHS:
            raise ValueError("command plan exceeds the write-path limit")
        normalized = {}
        total_bytes = 0
        for path, content in self.writes.items():
            normalized_path = normalize_relative_path(path)
            if len(normalized_path) > 256:
                raise ValueError("command plan path exceeds 256 characters")
            if content is not None and not isinstance(content, bytes):
                raise TypeError("command plan values must be bytes or null")
            if content is not None:
                total_bytes += len(content)
            normalized[normalized_path] = content
        if total_bytes > MAX_PLAN_WRITE_BYTES:
            raise ValueError("command plan exceeds the total write-byte limit")
        if not isinstance(self.result, Mapping):
            raise TypeError("command plan result must be a JSON object")
        if not callable(self.validator):
            raise TypeError("command plan requires an overlay validator")
        object.__setattr__(self, "writes", MappingProxyType(normalized))
        object.__setattr__(self, "result", freeze_json(self.result))


class CommandPlanner(Protocol):
    def preview(self, command: CommandEnvelope) -> CommandPreview:
        ...

    def plan(self, command: CommandEnvelope) -> CommandPlan:
        ...


class PersonSheetResolver(Protocol):
    def __call__(self, person_id: str) -> Optional[Mapping[str, Any]]:
        ...


@dataclass(frozen=True)
class OocAuditResult:
    diagnostics: Tuple[str, ...] = ()
    suggestions: Tuple[str, ...] = ()
    write_plan: Optional[Mapping[str, Any]] = None

    def __post_init__(self) -> None:
        for field in ("diagnostics", "suggestions"):
            values = tuple(getattr(self, field))
            if len(values) > 64:
                raise ValueError(f"OOC {field} exceeds 64 entries")
            if any(
                not isinstance(value, str)
                or not value
                or len(value) > 2048
                or "\x00" in value
                for value in values
            ):
                raise ValueError(f"OOC {field} contains invalid text")
            object.__setattr__(self, field, values)
        if self.write_plan is not None and not isinstance(self.write_plan, Mapping):
            raise TypeError("OOC write_plan must be an object or null")


class OocAuditProvider(Protocol):
    def __call__(
        self,
        focus: Optional[str],
        observations: Tuple[str, ...],
    ) -> OocAuditResult:
        ...


class UnavailableCommandPlanner:
    def preview(self, command: CommandEnvelope) -> CommandPreview:
        raise PlannerUnavailableError("command planner is not configured")

    def plan(self, command: CommandEnvelope) -> CommandPlan:
        raise PlannerUnavailableError("command planner is not configured")


def unresolved_sheet(person_id: str) -> Optional[Mapping[str, Any]]:
    return None


def basic_ooc_audit(
    focus: Optional[str], observations: Tuple[str, ...]
) -> OocAuditResult:
    return OocAuditResult(
        diagnostics=("command_planner_not_configured",),
        suggestions=("connect_a_reviewed_command_planner_before_gameplay",),
    )

