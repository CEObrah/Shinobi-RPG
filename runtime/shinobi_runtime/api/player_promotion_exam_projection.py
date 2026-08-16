"""Player-safe rehydration for active promotion examination cycles.

The career pipeline remains the sole mutable exam-administration owner. This
module only projects active cycle state and exact player-led-team registration
actionability into fresh play context so a transaction-time handoff cannot vanish
on the next read.
"""
from __future__ import annotations

import copy
from functools import wraps
from typing import Any, Mapping

from shinobi_runtime.api.models import validate_bounded_json
from shinobi_runtime.api.operations import CampaignOperations, OperationError
from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.promotion_exam_scheduler import (
    _person_matches_profile,
    active_promotion_exam_cycles,
    promotion_exam_profiles,
    registered_candidate_refs,
)
from shinobi_runtime.membership_routes import team_refs_for_member

_INSTALLED = False
_MAX_HANDOFFS = 8
_MAX_CANDIDATES = 16
_REGISTRATION_PRESSURE = "Konoha has an active Chunin Examination registration window for eligible members of your team."
_REGISTRATION_REPORT = "The Academy is accepting Chunin Examination registrations from your eligible team members."


def _promotion_exam_handoffs(
    operations: CampaignOperations,
    *,
    player_id: str,
) -> list[dict[str, Any]]:
    try:
        pipeline = operations.repository.read_json("state/reg/shinobi-career-pipeline.json")
        profiles = promotion_exam_profiles(operations.repository)
        active = active_promotion_exam_cycles(pipeline, profiles)
        team_refs = team_refs_for_member(operations.repository, player_id)
    except (FileNotFoundError, TypeError, ValueError, CommandRejectedError) as exc:
        raise OperationError(503, "promotion_exam_context_invalid") from exc

    profile_by_id = {
        profile.get("id"): profile
        for profile in profiles
        if isinstance(profile.get("id"), str)
    }
    handoffs: list[dict[str, Any]] = []
    for cycle in active:
        cycle_id = cycle.get("cycle_id")
        profile_ref = cycle.get("profile_ref")
        phase = cycle.get("phase")
        profile = profile_by_id.get(profile_ref)
        if (
            not isinstance(cycle_id, str)
            or not isinstance(profile_ref, str)
            or not isinstance(phase, str)
            or not isinstance(profile, Mapping)
        ):
            raise OperationError(503, "promotion_exam_context_invalid")
        institution_ref = profile.get("institution_ref")
        if not isinstance(institution_ref, str):
            raise OperationError(503, "promotion_exam_context_invalid")
        try:
            registered = set(registered_candidate_refs(pipeline, cycle_id))
        except CommandRejectedError as exc:
            raise OperationError(503, "promotion_exam_context_invalid") from exc

        for team_ref in team_refs:
            try:
                _path, team = operations._owner_record(team_ref)
            except OperationError:
                continue
            members = team.get("member_refs") if isinstance(team, Mapping) else None
            if (
                team.get("schema") != "exact-team"
                or team.get("status") != "active"
                or team.get("leader_ref") != player_id
                or team.get("assignment_authority_ref") != institution_ref
                or not isinstance(members, list)
                or any(not isinstance(ref, str) or not ref for ref in members)
            ):
                continue

            eligible_refs: list[str] = []
            for member_ref in members:
                if member_ref == player_id:
                    continue
                try:
                    _person_path, person = operations._owner_record(member_ref)
                except OperationError:
                    continue
                if isinstance(person, Mapping) and _person_matches_profile(person, profile):
                    eligible_refs.append(member_ref)
            eligible_refs = sorted(set(eligible_refs))[:_MAX_CANDIDATES]
            registered_refs = [ref for ref in eligible_refs if ref in registered]
            unregistered_refs = [ref for ref in eligible_refs if ref not in registered]
            handoffs.append(
                {
                    "cycle_id": cycle_id,
                    "profile_ref": profile_ref,
                    "phase": phase,
                    "institution_ref": institution_ref,
                    "team_ref": team_ref,
                    "registration_open": phase == "registration",
                    "eligible_candidate_refs": eligible_refs,
                    "registered_candidate_refs": registered_refs,
                    "unregistered_candidate_refs": unregistered_refs,
                }
            )
            if len(handoffs) >= _MAX_HANDOFFS:
                return handoffs
    return handoffs


def _install_api_projection() -> None:
    original_play_context = CampaignOperations.play_context
    if getattr(original_play_context, "_player_promotion_exam_projection", False):
        return

    @wraps(original_play_context)
    def play_context(self: CampaignOperations) -> Mapping[str, Any]:
        response = copy.deepcopy(original_play_context(self))
        campaign = response.get("campaign") if isinstance(response, Mapping) else None
        player_id = campaign.get("player_id") if isinstance(campaign, Mapping) else None
        if not isinstance(player_id, str):
            return response
        handoffs = _promotion_exam_handoffs(self, player_id=player_id)
        scene = response.get("scene") if isinstance(response, dict) else None
        if isinstance(scene, dict):
            scene["promotion_exam_handoffs"] = handoffs
            actionable = [
                row
                for row in handoffs
                if row.get("registration_open") is True
                and bool(row.get("unregistered_candidate_refs"))
            ]
            pressures = scene.get("observable_pressures")
            if isinstance(pressures, list):
                pressures = [value for value in pressures if value != _REGISTRATION_PRESSURE]
                if actionable and _REGISTRATION_PRESSURE not in pressures:
                    pressures.append(_REGISTRATION_PRESSURE)
                scene["observable_pressures"] = pressures[:12]
            narrative = scene.get("narrative")
            if isinstance(narrative, dict):
                reports = narrative.get("available_reports")
                if isinstance(reports, list):
                    reports = [value for value in reports if value != _REGISTRATION_REPORT]
                    if actionable and _REGISTRATION_REPORT not in reports:
                        reports.append(_REGISTRATION_REPORT)
                    narrative["available_reports"] = reports[-6:]
        validate_bounded_json(response, label="play context", allow_float=True)
        return response

    play_context._player_promotion_exam_projection = True
    CampaignOperations.play_context = play_context


def install_player_promotion_exam_projection() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_api_projection()
    _INSTALLED = True


__all__ = ["install_player_promotion_exam_projection"]
