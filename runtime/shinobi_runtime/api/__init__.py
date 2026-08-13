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

# Contracts import CommandEnvelope through shinobi_runtime.commands. Install
# command-domain extensions only after the contracts module is fully initialized
# so direct API imports cannot form an api.contracts <-> commands cycle.
from shinobi_runtime.commands.academy_career_sync import install_academy_career_sync as _install_academy_career_sync
from shinobi_runtime.commands.shinobi_career_progression import install_shinobi_career_progression as _install_shinobi_career_progression

_install_academy_career_sync()
_install_shinobi_career_progression()

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
