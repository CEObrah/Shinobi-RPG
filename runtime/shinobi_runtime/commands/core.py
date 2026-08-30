"""Shared deterministic command-planning primitives.

This module contains only domain-neutral helpers used by the planner and domain
mixins. Keeping them here avoids circular imports when command domains are split
out of the orchestration module.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.specs import COMMAND_SPECS
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest

_STABLE_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    """Stable readable owner bytes; no model- or wall-clock-dependent values.

    A reducer producing a non-JSON after-image is a planner/domain defect, not
    malformed caller input.  Keep that classification stable so MCP does not
    collapse an internal serialization failure into ``command_*_input_invalid``.
    """
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8") + b"\n"
    except (TypeError, ValueError) as exc:
        raise CommandRejectedError("owner_json_encoding_invalid") from exc


def _campaign_datetime(value: CampaignTime) -> datetime:
    return datetime(value.year, value.month, value.day, value.hour, value.minute, value.second)


def _exact_payload(payload: Mapping[str, Any], keys: Sequence[str], command_type: str) -> None:
    expected = frozenset(keys)
    spec = COMMAND_SPECS.get(command_type)
    if spec is None:
        raise RuntimeError(f"missing command spec for {command_type}")
    if spec.optional_fields or tuple(keys) != spec.required_fields:
        raise RuntimeError(f"command spec drift for {command_type}")
    actual = frozenset(payload)
    if actual != expected:
        raise CommandRejectedError(f"{command_type}_payload_fields_invalid")


def _declared_payload(payload: Mapping[str, Any], command_type: str) -> None:
    """Validate one non-variant public payload against required/optional fields.

    Optional public fields may be omitted entirely. Reducers should use ``get``
    for those fields rather than forcing callers to send meaningless nulls.
    """
    spec = COMMAND_SPECS.get(command_type)
    if spec is None or spec.variants:
        raise RuntimeError(f"command spec drift for {command_type}")
    required = frozenset(spec.required_fields)
    allowed = required | frozenset(spec.optional_fields)
    actual = frozenset(payload)
    if not required.issubset(actual) or actual - allowed:
        raise CommandRejectedError(f"{command_type}_payload_fields_invalid")


def _stable_id(value: object, field_code: str, *, prefix: Optional[str] = None) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 128
        or not _STABLE_ID.fullmatch(value)
        or (prefix is not None and not value.startswith(prefix))
    ):
        raise CommandRejectedError(field_code)
    return value


@dataclass(frozen=True)
class _BuiltPlan:
    code: str
    affected_refs: Tuple[str, ...]
    writes: Mapping[str, bytes]
    result: Mapping[str, Any]
    validator: Callable[[StagedOverlay, TransactionManifest], None]


@dataclass
class _OwnerResolutionCache:
    """One planner-call cache for derived owner-index routes and owner bytes."""
    prefix_index: Optional[Mapping[str, Any]] = None
    shards: Dict[str, Mapping[str, Any]] = field(default_factory=dict)
    records: Dict[str, Tuple[Mapping[str, Any], str]] = field(default_factory=dict)
