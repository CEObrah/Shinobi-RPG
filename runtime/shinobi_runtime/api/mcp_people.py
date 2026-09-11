"""Player-safe pageable persistent-person discovery for the MCP surface."""
from __future__ import annotations

import re
from typing import Any, Optional

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict

from shinobi_runtime.api.operations import CampaignOperations, OperationError


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class _Failure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    retryable: bool
    refresh_context: bool


class PeopleToolOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    result: Optional[dict[str, Any]] = None
    error: Optional[_Failure] = None


def _failure(exc: OperationError) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": exc.code,
            "retryable": exc.status_code >= 500,
            "refresh_context": exc.status_code == 409,
        },
    }


def register_people_tool(server: MCPServer, operations: CampaignOperations, read_scope: str) -> None:
    """Register one discover-then-drill-down roster tool.

    ``limit`` is a page size only. The result reports total matching people and
    a cursor; it never truncates the simulated roster or combat force.
    """
    annotations = ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    security = {"securitySchemes": [{"type": "oauth2", "scopes": [read_scope]}]}

    @server.tool(
        name="list_people",
        title="List persistent people",
        description=(
            "Page through player-authorized persistent people without guessing IDs. "
            "Supports the player's faction, optional exact site filtering, deterministic "
            "sorting such as combat/age/skill, and cursors. Page size is transport only, "
            "not a population or combat limit. Use returned IDs with get_person_sheet."
        ),
        annotations=annotations,
        meta=security,
        structured_output=True,
    )
    def list_people(
        faction_ref: Optional[str] = None,
        site_ref: Optional[str] = None,
        sort_by: str = "combat",
        limit: int = 25,
        cursor: Optional[str] = None,
    ) -> PeopleToolOutput:
        for value in (faction_ref, site_ref):
            if value is not None and (
                not isinstance(value, str) or len(value) > 160 or not _SAFE_ID.fullmatch(value)
            ):
                return _failure(OperationError(422, "people_filter_invalid"))
        if (
            not isinstance(sort_by, str) or len(sort_by) > 32
            or isinstance(limit, bool) or not isinstance(limit, int)
            or limit < 1 or limit > 1000
            or (cursor is not None and (not isinstance(cursor, str) or len(cursor) > 20 or not cursor.isdigit()))
        ):
            return _failure(OperationError(422, "people_query_invalid"))
        try:
            result = operations.list_people(
                faction_ref=faction_ref,
                site_ref=site_ref,
                sort_by=sort_by,
                limit=limit,
                cursor=cursor,
            )
        except OperationError as exc:
            return _failure(exc)
        except Exception:
            return _failure(OperationError(503, "runtime_internal_error"))
        return {"ok": True, "result": dict(result)}


__all__ = ["PeopleToolOutput", "register_people_tool"]
