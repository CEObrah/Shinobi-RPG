"""Settle foreign delegation return travel after a hosted Chunin Exam closes."""
from __future__ import annotations

import copy
import json
from functools import wraps
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _OwnerResolutionCache, _json_bytes
from shinobi_runtime.commands.domains.time import TimeCommandsMixin
from shinobi_runtime.commands import promotion_exam_attendance as attendance
from shinobi_runtime.commands import promotion_exam_scheduler as scheduler
from shinobi_runtime.commands.promotion_exam_hosted_lifecycle import _BaseOverlay, _foreign_registered, _pipeline_after
from shinobi_runtime.commands.promotion_exam_hosted_policy import append_location, candidate_home_location, hosted_config, minimum_route_days
from shinobi_runtime.sim.events import CampaignTime

_INSTALLED = False


def _closed_cycles(pipeline: Mapping[str, Any], profiles: tuple[Mapping[str, Any], ...]) -> list[tuple[Mapping[str, Any], str, CampaignTime]]:
    history = pipeline.get("history")
    if not isinstance(history, list):
        raise CommandRejectedError("shinobi_career_pipeline_invalid")
    profile_by_id = {row.get("id"): row for row in profiles if isinstance(row.get("id"), str)}
    latest: dict[str, Mapping[str, Any]] = {}
    for row in history:
        if not (
            isinstance(row, Mapping)
            and row.get("kind") == "promotion_exam_cycle_phase"
            and isinstance(row.get("cycle_id"), str)
            and isinstance(row.get("profile_ref"), str)
            and isinstance(row.get("phase"), str)
            and isinstance(row.get("at"), str)
        ):
            continue
        latest[str(row["cycle_id"])] = row
    result: list[tuple[Mapping[str, Any], str, CampaignTime]] = []
    for cycle_id, row in latest.items():
        if row.get("phase") != "closed":
            continue
        profile = profile_by_id.get(row.get("profile_ref"))
        if not isinstance(profile, Mapping) or hosted_config(profile) is None:
            continue
        try:
            closed_at = CampaignTime.parse(str(row["at"]))
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("promotion_exam_cycle_state_invalid") from exc
        result.append((profile, cycle_id, closed_at))
    return result


def install_promotion_exam_hosted_returns() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    original = TimeCommandsMixin._advance_time
    if getattr(original, "_promotion_exam_hosted_returns", False):
        _INSTALLED = True
        return

    @wraps(original)
    def wrapped(self: Any, command: Any, meta: Mapping[str, Any], current_time: CampaignTime) -> _BuiltPlan:
        base = original(self, command, meta, current_time)
        reached_raw = base.result.get("world_time") if isinstance(base.result, Mapping) else None
        if not isinstance(reached_raw, str):
            return base
        try:
            reached = CampaignTime.parse(reached_raw)
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("promotion_exam_cycle_state_invalid") from exc
        pipeline = _pipeline_after(self.repository, base.writes)
        profiles = scheduler.promotion_exam_profiles(self.repository)
        cache = _OwnerResolutionCache()
        staged: dict[str, dict[str, Any]] = {}
        returns: list[dict[str, Any]] = []
        for profile, cycle_id, closed_at in _closed_cycles(pipeline, profiles):
            config = hosted_config(profile)
            assert config is not None
            host_anchor = attendance._place_anchor(self.repository, str(config["host_arrival_place_ref"]))
            for candidate_ref, delegation in _foreign_registered(pipeline, profile, cycle_id).items():
                try:
                    path, _digest, view = self._resolve_covered_owner_view(candidate_ref, cache=cache)
                except CommandRejectedError as exc:
                    raise CommandRejectedError("promotion_exam_candidate_unresolved") from exc
                subject = staged.get(path)
                if subject is None:
                    raw = base.writes.get(path)
                    subject = json.loads(raw.decode("utf-8")) if raw is not None else copy.deepcopy(dict(view)) if isinstance(view, Mapping) else None
                if not isinstance(subject, dict):
                    continue
                home = candidate_home_location(subject)
                current = subject.get("current_location_id")
                if not isinstance(home, str) or not isinstance(current, str):
                    raise CommandRejectedError("promotion_exam_candidate_location_invalid")
                if current == home:
                    continue
                if attendance._place_anchor(self.repository, current) != host_anchor:
                    continue
                route_days = minimum_route_days(self.repository, current, home)
                if route_days is None:
                    raise CommandRejectedError("promotion_exam_delegation_return_route_unavailable")
                return_due = closed_at.add_seconds(int(route_days * 24 * 60 * 60))
                if return_due > reached:
                    continue
                append_location(
                    subject,
                    at=return_due,
                    location_ref=home,
                    reason=f"hosted Chunin Examination delegation return completed to {delegation['service_village']}",
                )
                staged[path] = subject
                returns.append(
                    {
                        "candidate_ref": candidate_ref,
                        "from_location_ref": current,
                        "to_location_ref": home,
                        "minimum_route_days": route_days,
                        "completed_at": str(return_due),
                    }
                )
        if not returns:
            return base
        world_events = self._world_events_after(base)
        event_ids: list[str] = []
        for index, row in enumerate(returns):
            event_ids.append(
                self._append_internal_event(
                    world_events,
                    command=command,
                    identity=f"promotion-exam-hosted-return:{row['candidate_ref']}:{row['completed_at']}:{index}",
                    kind="promotion_exam_hosted_delegation_returned",
                    at=CampaignTime.parse(row["completed_at"]),
                    host_refs=(row["to_location_ref"],),
                    actor_refs=(row["candidate_ref"],),
                    affected_owner_refs=tuple(sorted(staged)),
                    material_consequence_refs=(f"hosted_exam_return:{row['candidate_ref']}:{row['to_location_ref']}",),
                    classification="public",
                    audience_refs=(command.actor_id,),
                    source_refs=(row["from_location_ref"], row["to_location_ref"]),
                    reducer_ref="shinobi_runtime.commands.promotion_exam_hosted_returns",
                )
            )
        writes = dict(base.writes)
        writes.update({path: _json_bytes(record) for path, record in staged.items()})
        writes.update(self._world_event_writes(world_events))
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))
        base_writes = dict(base.writes)
        original_validator = base.validator
        expected_records = copy.deepcopy(staged)

        def validate(overlay: Any, manifest: Any) -> None:
            if original_validator is not None:
                original_validator(_BaseOverlay(overlay, base_writes), manifest)
            if overlay.changed_paths != expected_paths:
                raise ValueError("hosted promotion exam return write set changed after planning")
            for path, expected in expected_records.items():
                if overlay.read_json(path) != expected:
                    raise ValueError("hosted promotion exam return after-image differs from plan")

        result = dict(base.result)
        result["promotion_exam_hosted_delegation_returns"] = returns
        result["promotion_exam_hosted_delegation_return_event_ids"] = event_ids
        return _BuiltPlan(code=base.code, affected_refs=expected_paths, writes=writes, result=result, validator=validate)

    wrapped._promotion_exam_hosted_returns = True  # type: ignore[attr-defined]
    TimeCommandsMixin._advance_time = wrapped
    _INSTALLED = True


__all__ = ["install_promotion_exam_hosted_returns"]
