"""Require exact shinobi rank changes to use the subject's home-village authority.

Hosted examinations may evaluate foreign candidates, but the host institution must
never acquire authority to assign another village's official shinobi rank.  This
extension preserves the shared career_status_resolution owner while adding a
service-village scope check for exact Genin/Chunin/Jonin promotions and demotions.
"""
from __future__ import annotations

import re
from functools import wraps
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _OwnerResolutionCache
from shinobi_runtime.commands.domains.social import SocialCommandsMixin
from shinobi_runtime.commands.promotion_exam_cycle import (
    _career_pipeline,
    _rank_key,
    _service_village,
)
from shinobi_runtime.sim.events import CampaignTime

_INSTALLED = False


def _authority_service_village(
    institution_ref: object,
    pipeline: Mapping[str, Any],
) -> str:
    villages = pipeline.get("villages")
    if not isinstance(institution_ref, str) or not institution_ref or not isinstance(villages, Mapping):
        raise CommandRejectedError("career_service_authority_unresolved")
    tokens = {
        token
        for token in re.split(r"[^a-z0-9]+", institution_ref.lower())
        if token
    }
    matches = [
        village
        for village in villages
        if isinstance(village, str) and village.lower() in tokens
    ]
    if len(matches) != 1:
        raise CommandRejectedError("career_service_authority_unresolved")
    return matches[0]


def assert_home_village_rank_authority(
    record: Mapping[str, Any],
    *,
    institution_ref: object,
    pipeline: Mapping[str, Any],
) -> str:
    service_village = _service_village(record, pipeline)
    authority_village = _authority_service_village(institution_ref, pipeline)
    if authority_village != service_village:
        raise CommandRejectedError("career_service_authority_mismatch")
    return service_village


def install_shinobi_career_service_authority() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    original = SocialCommandsMixin._career_status_resolution
    if getattr(original, "_shinobi_career_service_authority", False):
        _INSTALLED = True
        return

    @wraps(original)
    def wrapped(
        self: Any,
        command: Any,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        action = command.payload.get("action")
        subject_ref = command.payload.get("subject_ref")
        target_rank = _rank_key(command.payload.get("target_rank_or_status"))
        if action in ("promote", "demote") and isinstance(subject_ref, str) and target_rank is not None:
            _path, _digest, record = self._resolve_covered_owner_view(
                subject_ref,
                cache=_OwnerResolutionCache(),
            )
            if (
                isinstance(record, Mapping)
                and record.get("schema") == "shinobi_character"
                and _rank_key(record.get("official_rank_or_status")) is not None
            ):
                pipeline = _career_pipeline(self.repository, {})
                assert_home_village_rank_authority(
                    record,
                    institution_ref=command.payload.get("institution_ref"),
                    pipeline=pipeline,
                )
        return original(self, command, meta, current_time)

    wrapped._shinobi_career_service_authority = True  # type: ignore[attr-defined]
    SocialCommandsMixin._career_status_resolution = wrapped
    _INSTALLED = True


__all__ = [
    "assert_home_village_rank_authority",
    "install_shinobi_career_service_authority",
]
