"""Strict bounded FastAPI request and response schemas."""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
PERSON_ID_PATTERN = r"^[a-z0-9][a-z0-9._:-]*$"
MAX_JSON_NODES = 2048
# ``play context`` is the trusted internal assembly envelope. Campaign-specific
# projections are composed before the public transport view is compacted, so
# using the wire limit during assembly can make optional detail brick every
# live turn before compaction has a chance to run.
MAX_PLAY_CONTEXT_ASSEMBLY_JSON_NODES = 32768
MAX_PLAY_CONTEXT_JSON_NODES = 4096
MAX_JSON_DEPTH = 12
MAX_JSON_STRING = 8192
MAX_CONTAINER_ITEMS = 256
MAX_JSON_UTF8_BYTES = 256 * 1024
MAX_PLAY_CONTEXT_ASSEMBLY_UTF8_BYTES = 1024 * 1024


def validate_bounded_json(
    value: Any,
    *,
    label: str = "payload",
    allow_float: bool = False,
) -> Any:
    if label == "play context":
        node_limit = MAX_PLAY_CONTEXT_ASSEMBLY_JSON_NODES
        byte_limit = MAX_PLAY_CONTEXT_ASSEMBLY_UTF8_BYTES
    elif label == "compact play context":
        node_limit = MAX_PLAY_CONTEXT_JSON_NODES
        byte_limit = MAX_JSON_UTF8_BYTES
    else:
        node_limit = MAX_JSON_NODES
        byte_limit = MAX_JSON_UTF8_BYTES
    stack = [(value, 0)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > node_limit:
            raise ValueError(f"{label} exceeds {node_limit} JSON nodes")
        if depth > MAX_JSON_DEPTH:
            raise ValueError(f"{label} exceeds nesting depth {MAX_JSON_DEPTH}")
        if current is None or isinstance(current, bool):
            continue
        if isinstance(current, int):
            if not -(2**63) <= current < 2**63:
                raise ValueError(f"{label} integer exceeds signed 64-bit range")
            continue
        if isinstance(current, float):
            if not allow_float or not math.isfinite(current):
                raise ValueError(f"{label} contains an invalid floating-point value")
            continue
        if isinstance(current, str):
            if len(current) > MAX_JSON_STRING:
                raise ValueError(f"{label} string exceeds {MAX_JSON_STRING} characters")
            continue
        if isinstance(current, list):
            if len(current) > MAX_CONTAINER_ITEMS:
                raise ValueError(f"{label} array exceeds {MAX_CONTAINER_ITEMS} items")
            stack.extend((item, depth + 1) for item in current)
            continue
        if isinstance(current, dict):
            if len(current) > MAX_CONTAINER_ITEMS:
                raise ValueError(f"{label} object exceeds {MAX_CONTAINER_ITEMS} keys")
            for key, item in current.items():
                if not isinstance(key, str) or not key or len(key) > 128:
                    raise ValueError(f"{label} contains an invalid object key")
                stack.append((item, depth + 1))
            continue
        raise ValueError(f"{label} contains unsupported JSON data")
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", errors="strict")
    except (
        OverflowError,
        TypeError,
        UnicodeEncodeError,
        ValueError,
        RecursionError,
    ) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON") from exc
    if len(serialized) > byte_limit:
        raise ValueError(
            f"{label} exceeds {byte_limit} serialized UTF-8 bytes"
        )
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class HealthResponse(StrictModel):
    status: Literal["ok"]


class CampaignSnapshotResponse(StrictModel):
    campaign_id: str
    revision: int
    world_time: str
    state_root: str


class PlayContextResponse(StrictModel):
    campaign: Dict[str, Any]
    scene: Dict[str, Any]
    player: Dict[str, Any]
    person_reads: Dict[str, Any]
    object_reads: Dict[str, Any]
    commands: Dict[str, Any]
    narration: Dict[str, Any]
    context_policy: Dict[str, Any]
    causal_freshness: Optional[Dict[str, Any]] = None

    @field_validator(
        "campaign",
        "scene",
        "player",
        "person_reads",
        "object_reads",
        "commands",
        "narration",
        "context_policy",
        "causal_freshness",
    )
    @classmethod
    def bounded_context_section(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        return validate_bounded_json(
            value,
            label="play context",
            allow_float=True,
        )


class PersonSheetResponse(StrictModel):
    person_id: str
    view: Literal["player_full_logical_sheet", "player_visible_identity"]
    sheet: Dict[str, Any]
    causal_freshness: Optional[Dict[str, Any]] = None

    @field_validator("sheet")
    @classmethod
    def bounded_sheet(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        return validate_bounded_json(
            value,
            label="person sheet",
            allow_float=True,
        )


class GameObjectResponse(StrictModel):
    object_ref: str
    causal_freshness: Optional[Dict[str, Any]] = None
    view: Literal[
        "exact_team", "force_summary", "formation_summary", "mission_owner", "place_summary",
        "conflict_summary", "custody_summary", "combat_operation_summary",
        "family_record", "reputation_summary", "project_summary", "contract_summary",
        "commitment_summary", "asset_summary", "relationship_summary", "inventory_summary",
        "public_item_price", "public_service_price", "authorized_finance_summary",
        "promotion_exam_results_page",
        "faction_summary", "deployment_summary", "tournament_summary", "market_summary",
        "relations_summary", "government_summary",
    ]
    object: Dict[str, Any]

    @field_validator("object")
    @classmethod
    def bounded_object(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        return validate_bounded_json(
            value,
            label="game object projection",
            allow_float=True,
        )


class CommandEnvelopeRequest(StrictModel):
    campaign_id: str = Field(min_length=1, max_length=128, pattern=SAFE_ID_PATTERN)
    request_id: str = Field(min_length=1, max_length=128, pattern=SAFE_ID_PATTERN)
    actor_id: str = Field(min_length=1, max_length=128, pattern=SAFE_ID_PATTERN)
    command_type: str = Field(min_length=1, max_length=96, pattern=SAFE_ID_PATTERN)
    expected_revision: int = Field(ge=0)
    submitted_at: str = Field(min_length=1, max_length=64)
    payload: Dict[str, Any]
    mode: Literal["gameplay"] = "gameplay"

    @field_validator("payload")
    @classmethod
    def bounded_payload(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        return validate_bounded_json(value)


class CommandPreviewResponse(StrictModel):
    status: Literal["ready", "needs_clarification", "rejected"]
    code: str
    target_revision: int
    affected_refs: List[str]


class CommandReceiptResponse(StrictModel):
    status: Literal["committed", "duplicate"]
    request_id: str
    transaction_id: str
    campaign_id: str
    committed_revision: int
    committed_at: str
    result: Dict[str, Any]

    @field_validator("result")
    @classmethod
    def bounded_result(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        return validate_bounded_json(value, label="command result")


class OocAuditRequest(StrictModel):
    focus: Optional[str] = Field(default=None, min_length=1, max_length=512)
    observations: List[str] = Field(default_factory=list, max_length=64)

    @field_validator("observations")
    @classmethod
    def bounded_observations(cls, values: List[str]) -> List[str]:
        if any(not value or len(value) > 2048 or "\x00" in value for value in values):
            raise ValueError("observations must contain bounded non-empty text")
        return values


class OocAuditResponse(StrictModel):
    diagnostics: List[str]
    suggestions: List[str]
