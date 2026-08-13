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

from shinobi_runtime.commands.academy_career_sync import install_academy_career_sync as _install_academy_career_sync
from shinobi_runtime.commands.shinobi_career_progression import install_shinobi_career_progression as _install_shinobi_career_progression
from shinobi_runtime.commands.promotion_exam_scheduler import install_promotion_exam_scheduler as _install_promotion_exam_scheduler
from shinobi_runtime.commands.world_front_progression import install_world_front_progression as _install_world_front_progression
from shinobi_runtime.commands.downtime_until_event import install_downtime_until_event as _install_downtime_until_event
from shinobi_runtime.commands.downtime_vitality import install_downtime_vitality as _install_downtime_vitality

_install_academy_career_sync()
_install_shinobi_career_progression()
_install_promotion_exam_scheduler()
_install_world_front_progression()
_install_downtime_until_event()
_install_downtime_vitality()

from .ooc import RepositoryOocAudit
from .vitality_audit import install_playability_vitality_audit as _install_playability_vitality_audit

_install_playability_vitality_audit(RepositoryOocAudit)


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
