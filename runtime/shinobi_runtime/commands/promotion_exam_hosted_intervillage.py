"""Install hosted inter-village Chunin Examination behavior."""
from __future__ import annotations

from functools import wraps
from typing import Any

from shinobi_runtime.commands import promotion_exam_attendance as attendance
from shinobi_runtime.commands import promotion_exam_integrity as integrity
from shinobi_runtime.commands import promotion_exam_scheduler as scheduler
from shinobi_runtime.commands.specs import COMMAND_SPECS, CommandSpec
from shinobi_runtime.commands.promotion_exam_hosted_policy import (
    bind_originals,
    eligible_hosted_registrations,
    person_matches_hosted_profile,
    stage_hosted_finalists,
)
from shinobi_runtime.commands.promotion_exam_hosted_lifecycle import install_promotion_exam_hosted_lifecycle
from shinobi_runtime.commands.promotion_exam_hosted_returns import install_promotion_exam_hosted_returns

_INSTALLED = False


def install_promotion_exam_hosted_intervillage() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    bind_originals(
        eligible=integrity.eligible_npc_team_registrations,
        stage_finalists=attendance.stage_npc_finalists,
    )
    integrity._person_matches_profile = person_matches_hosted_profile
    scheduler._person_matches_profile = person_matches_hosted_profile
    integrity.eligible_npc_team_registrations = eligible_hosted_registrations
    attendance.stage_npc_finalists = stage_hosted_finalists
    try:
        from shinobi_runtime.api import player_promotion_exam_projection as projection
        projection._person_matches_profile = person_matches_hosted_profile
    except ImportError:
        pass
    try:
        from shinobi_runtime.commands import campaign_promotion_exam_participation_repair as repair
        repair.eligible_npc_team_registrations = eligible_hosted_registrations
        COMMAND_SPECS["campaign_promotion_exam_participation_repair"] = CommandSpec(
            ("cycle_id",),
            (),
            "Reconcile omitted non-player team or home-village delegation Chunin Exam participation for an active finals phase only when no finals bout has settled, preserving the original phase chronology.",
            {"cycle_id": "promotion_exam_cycle.<id>"},
            availability="ooc_dev_guarded_repair_only",
        )
    except ImportError:
        pass
    try:
        from shinobi_runtime.commands import campaign_promotion_exam_attendance_repair as attendance_repair

        @wraps(stage_hosted_finalists)
        def repair_stage(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            kwargs["allow_cross_country_reconciliation"] = True
            return stage_hosted_finalists(*args, **kwargs)

        attendance_repair.stage_npc_finalists = repair_stage
    except ImportError:
        pass
    install_promotion_exam_hosted_lifecycle()
    install_promotion_exam_hosted_returns()
    _INSTALLED = True


__all__ = ["install_promotion_exam_hosted_intervillage"]
