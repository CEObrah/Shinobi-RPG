"""Private authenticated FastAPI surface for one campaign runtime."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, FrozenSet, Mapping, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Path as ApiPath, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import JSONResponse

from shinobi_runtime.api.contracts import (
    CommandPlanner,
    OocAuditProvider,
    PersonSheetResolver,
    basic_ooc_audit,
)
from shinobi_runtime.api.middleware import BodySizeLimitMiddleware
from shinobi_runtime.api.models import (
    CampaignSnapshotResponse,
    CommandEnvelopeRequest,
    CommandPreviewResponse,
    CommandReceiptResponse,
    GameObjectResponse,
    OocAuditRequest,
    OocAuditResponse,
    PERSON_ID_PATTERN,
    PlayContextResponse,
    PersonSheetResponse,
)
from shinobi_runtime.api.ooc import RepositoryOocAudit
from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.api.operations import CampaignOperations
from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.commands.campaign_planner import CampaignCommandPlanner
from shinobi_runtime.deployment_freshness import inspect_deployment_freshness
from shinobi_runtime.people import RepositoryPersonSheetResolver
from shinobi_runtime.store import RepositoryStore
from shinobi_runtime.tx import (
    GitRemoteDurability,
    GitStager,
    ReceiptStore,
    TransactionCoordinator,
    WriteAheadLog,
)
from shinobi_runtime.tx.errors import (
    DirtyRepositoryError,
    LockUnavailableError,
)


@dataclass(frozen=True)
class ServiceSettings:
    auth_token: str = field(repr=False)
    allowed_actor_ids: FrozenSet[str]
    max_body_bytes: int = 128 * 1024
    lock_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.auth_token, str)
            or len(self.auth_token) < 32
            or self.auth_token != self.auth_token.strip()
            or any(
                character in self.auth_token
                for character in ("\x00", "\r", "\n")
            )
        ):
            raise ValueError("service bearer token must contain at least 32 characters")
        actors = frozenset(self.allowed_actor_ids)
        if not actors or any(
            not isinstance(actor_id, str)
            or not actor_id
            or any(character in actor_id for character in ("\x00", "\r", "\n"))
            for actor_id in actors
        ):
            raise ValueError("allowed_actor_ids must contain at least one actor")
        object.__setattr__(self, "allowed_actor_ids", actors)
        if (
            isinstance(self.max_body_bytes, bool)
            or not isinstance(self.max_body_bytes, int)
            or self.max_body_bytes <= 0
            or self.max_body_bytes > 1024 * 1024
        ):
            raise ValueError("max_body_bytes must be in 1..1048576")
        if (
            isinstance(self.lock_timeout_seconds, bool)
            or not isinstance(self.lock_timeout_seconds, (int, float))
            or self.lock_timeout_seconds < 0
        ):
            raise ValueError("lock timeout must be non-negative")

    @classmethod
    def from_env(cls, repository: RepositoryStore) -> "ServiceSettings":
        # ChatGPT authenticates the MCP surface through OAuth. The private REST
        # token is optional; when absent, an unreported per-process value keeps
        # those non-MCP routes inaccessible.
        token = os.environ.get("SHINOBI_API_TOKEN") or secrets.token_urlsafe(48)
        player_id = repository.read_json("state/meta.json").get("player_id")
        if not isinstance(player_id, str) or not player_id:
            raise RuntimeError("campaign meta has no player_id for API actor binding")
        try:
            body_limit = int(os.environ.get("SHINOBI_MAX_BODY_BYTES", str(128 * 1024)))
            lock_timeout = float(os.environ.get("SHINOBI_LOCK_TIMEOUT_SECONDS", "5"))
        except ValueError as exc:
            raise RuntimeError("invalid numeric service environment setting") from exc
        return cls(
            auth_token=token,
            allowed_actor_ids=frozenset((player_id,)),
            max_body_bytes=body_limit,
            lock_timeout_seconds=lock_timeout,
        )


def _http_error(status: int, code: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code})


def _domain_command(request: CommandEnvelopeRequest) -> CommandEnvelope:
    try:
        return CommandEnvelope(**request.model_dump())
    except (TypeError, ValueError) as exc:
        raise _http_error(422, "invalid_command_envelope") from exc


def create_app(
    *,
    repository: RepositoryStore,
    coordinator: TransactionCoordinator,
    command_planner: CommandPlanner,
    sheet_resolver: PersonSheetResolver,
    settings: ServiceSettings,
    audit_provider: OocAuditProvider = basic_ooc_audit,
) -> FastAPI:
    """Create one private campaign API from explicit runtime dependencies."""

    if repository.root != coordinator.repository.root:
        raise ValueError("API repository and coordinator repository differ")
    if not callable(getattr(command_planner, "preview", None)) or not callable(
        getattr(command_planner, "plan", None)
    ):
        raise TypeError("command_planner must implement preview and plan")
    if not callable(sheet_resolver) or not callable(audit_provider):
        raise TypeError("sheet and audit resolvers must be callable")

    app = FastAPI(
        title="Shinobi Runtime Private API",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_body_bytes=settings.max_body_bytes,
    )
    bearer = HTTPBearer(auto_error=False)
    operations = CampaignOperations(
        repository=repository,
        coordinator=coordinator,
        command_planner=command_planner,
        sheet_resolver=sheet_resolver,
        audit_provider=audit_provider,
        allowed_actor_ids=settings.allowed_actor_ids,
        lock_timeout_seconds=settings.lock_timeout_seconds,
    )
    app.state.campaign_operations = operations

    @app.exception_handler(LockUnavailableError)
    async def lock_unavailable(
        request: Request, exc: LockUnavailableError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": {"code": "campaign_writer_busy"}},
        )

    @app.exception_handler(DirtyRepositoryError)
    async def dirty_repository(
        request: Request, exc: DirtyRepositoryError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": {"code": "campaign_repository_dirty"}},
        )

    @app.exception_handler(OperationError)
    async def operation_error(
        request: Request, exc: OperationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": {"code": exc.code}},
        )

    async def authenticate(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    ) -> None:
        if (
            credentials is None
            or credentials.scheme.lower() != "bearer"
            or not secrets.compare_digest(credentials.credentials, settings.auth_token)
        ):
            raise HTTPException(
                status_code=401,
                detail={"code": "unauthorized"},
                headers={"WWW-Authenticate": "Bearer"},
            )

    @app.get("/health", operation_id="health")
    def health() -> Any:
        freshness = inspect_deployment_freshness(repository.root)
        # Preserve the existing compact local/core health contract. Railway
        # processes additionally prove that the immutable build image covers all
        # non-state source/config changes in the persistent campaign checkout.
        if not freshness.production:
            return {"status": "ok"}
        payload = {
            "status": "ok" if freshness.healthy else "unhealthy",
            "deployment_freshness": freshness.status,
            "source_revision": freshness.source_revision,
            "checkout_revision": freshness.checkout_revision,
            "reason": freshness.reason,
            "non_state_delta_count": len(freshness.non_state_paths),
        }
        if not freshness.healthy:
            return JSONResponse(status_code=503, content=payload)
        return payload

    router = APIRouter(dependencies=[Depends(authenticate)])

    @router.get(
        "/v1/campaign",
        response_model=CampaignSnapshotResponse,
        operation_id="campaignSnapshot",
    )
    def campaign_snapshot() -> Mapping[str, Any]:
        return operations.campaign_snapshot()

    @router.get(
        "/v1/play/context",
        response_model=PlayContextResponse,
        operation_id="playContext",
    )
    def play_context() -> Mapping[str, Any]:
        return operations.play_context()

    @router.get(
        "/v1/person/{person_id}/sheet",
        response_model=PersonSheetResponse,
        operation_id="personSheet",
    )
    def person_sheet(
        person_id: str = ApiPath(
            min_length=1,
            max_length=128,
            pattern=PERSON_ID_PATTERN,
        ),
    ) -> Mapping[str, Any]:
        return operations.person_sheet(person_id)

    @router.get(
        "/v1/object/{object_ref}",
        response_model=GameObjectResponse,
        operation_id="inspectGameObject",
    )
    def inspect_game_object(
        object_ref: str = ApiPath(
            min_length=1,
            max_length=160,
            pattern=PERSON_ID_PATTERN,
        ),
    ) -> Mapping[str, Any]:
        return operations.inspect_game_object(object_ref)

    @router.post(
        "/v1/commands/preview",
        response_model=CommandPreviewResponse,
        operation_id="previewCommand",
    )
    def preview_command(request: CommandEnvelopeRequest) -> Mapping[str, Any]:
        command = _domain_command(request)
        return operations.preview_command(command)

    @router.post(
        "/v1/commands/execute",
        response_model=CommandReceiptResponse,
        operation_id="executeCommand",
    )
    def execute_command(request: CommandEnvelopeRequest) -> Mapping[str, Any]:
        command = _domain_command(request)
        return operations.execute_command(command)

    @router.post(
        "/v1/ooc/audit",
        response_model=OocAuditResponse,
        operation_id="oocAudit",
    )
    def ooc_audit(request: OocAuditRequest) -> Mapping[str, Any]:
        return operations.ooc_audit(request.focus, request.observations)

    app.include_router(router)
    return app


def create_app_from_env() -> FastAPI:
    """Build the conservative Railway entrypoint from environment paths."""

    campaign_root = os.environ.get("SHINOBI_CAMPAIGN_ROOT")
    runtime_root = os.environ.get("SHINOBI_RUNTIME_ROOT")
    if not campaign_root or not runtime_root:
        raise RuntimeError(
            "SHINOBI_CAMPAIGN_ROOT and SHINOBI_RUNTIME_ROOT are required"
        )
    repository = RepositoryStore(Path(campaign_root))
    settings = ServiceSettings.from_env(repository)
    runtime = Path(runtime_root)
    git = GitStager(repository.root)
    remote_durability = GitRemoteDurability.from_env(git)
    coordinator = TransactionCoordinator(
        repository,
        git,
        WriteAheadLog(runtime / "wal"),
        ReceiptStore(runtime / "receipts"),
        lock_path=runtime / "writer.lock",
        lock_timeout=settings.lock_timeout_seconds,
        remote_durability=remote_durability,
    )
    # Railway bootstrap deliberately preserves a clean local-ahead checkout
    # when a process died after committing but before pushing/receipting.  Do
    # not serve even the health endpoint until WAL recovery has either safely
    # finalized that transaction or failed startup closed.
    coordinator.recover()
    app = create_app(
        repository=repository,
        coordinator=coordinator,
        command_planner=CampaignCommandPlanner(repository),
        sheet_resolver=RepositoryPersonSheetResolver(repository),
        audit_provider=RepositoryOocAudit(repository, runtime),
        settings=settings,
    )
    mcp_environment = (
        "SHINOBI_MCP_PUBLIC_URL",
        "SHINOBI_OAUTH_ISSUER_URL",
        "SHINOBI_OAUTH_JWKS_URL",
        "SHINOBI_OAUTH_AUDIENCE",
        "SHINOBI_OAUTH_ALLOWED_SUBJECTS",
        "SHINOBI_MCP_PREVIEW_SECRET",
    )
    if any(os.environ.get(name) for name in mcp_environment):
        # Keep local/core installations importable without the optional MCP
        # dependency.  A partial remote configuration still fails startup.
        from shinobi_runtime.api.mcp import (
            McpOAuthSettings,
            create_mcp_server,
            mount_mcp,
        )

        oauth = McpOAuthSettings.from_env()
        mount_mcp(
            app,
            create_mcp_server(app.state.campaign_operations, oauth),
            oauth,
            max_request_body_size=settings.max_body_bytes,
        )
    return app
