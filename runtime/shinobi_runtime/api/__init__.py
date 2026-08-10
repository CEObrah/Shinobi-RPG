"""Optional HTTP service boundary for the deterministic runtime.

Importing :mod:`shinobi_runtime` or this package does not require FastAPI.
Calling :func:`create_app` loads the optional service dependencies lazily.
"""

from .contracts import (
    CommandPlan,
    CommandPlanner,
    CommandPreview,
    CommandRejectedError,
    OocAuditResult,
    PlannerUnavailableError,
)
from .ooc import RepositoryOocAudit


class ServiceDependencyError(RuntimeError):
    pass


def create_app(*args, **kwargs):
    try:
        from .app import create_app as factory
    except ModuleNotFoundError as exc:
        if exc.name in ("fastapi", "pydantic", "starlette"):
            raise ServiceDependencyError(
                "install the 'service' optional dependencies to create the API"
            ) from exc
        raise
    return factory(*args, **kwargs)


__all__ = [
    "CommandPlan",
    "CommandPlanner",
    "CommandPreview",
    "CommandRejectedError",
    "OocAuditResult",
    "PlannerUnavailableError",
    "RepositoryOocAudit",
    "ServiceDependencyError",
    "create_app",
]
