"""Player-safe travel-aware play-context projection.

Mechanical physical presence remains owned by the exact route/custody/combat
resolvers. This module only enriches the read projection so people already
owned by the same exact player route movement are not lost merely because the
presentation scene did not previously list them.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

from shinobi_runtime.api.models import validate_bounded_json
from shinobi_runtime.api.operations import CampaignOperations, OperationError
from shinobi_runtime.martial_world.physical_presence import (
    active_route_for_person,
    same_effective_location,
)


def _unique_person_refs(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for value in values:
        if isinstance(value, str) and value and value not in out:
            out.append(value)
    return out


def movement_scene_projection(
    *,
    read_json: Callable[[str], Any],
    sheet_resolver: Callable[[str], Mapping[str, Any]],
    player_id: str,
    player_sheet: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Project exact co-traveling participants for the player's route owner.

    Sharing a route identifier is insufficient. A person is returned only when
    they are explicitly a participant in the same active route movement and the
    universal physical-presence resolver confirms the same exact movement space.
    """
    active = active_route_for_person(read_json, player_id)
    if active is None:
        return None
    movement_ref, movement = active
    participant_refs = _unique_person_refs(movement.get("participant_refs"))
    if player_id not in participant_refs:
        participant_refs.insert(0, player_id)

    present: list[str] = []
    for ref in participant_refs:
        try:
            other = player_sheet if ref == player_id else sheet_resolver(ref)
        except (FileNotFoundError, KeyError, TypeError, ValueError):
            continue
        if not isinstance(other, Mapping):
            continue
        if same_effective_location(
            read_json,
            player_id,
            ref,
            left_person=player_sheet,
            right_person=other,
        ):
            present.append(ref)

    if player_id not in present:
        present.insert(0, player_id)

    context: dict[str, Any] = {
        "movement_ref": movement_ref,
        "participant_person_ids": present,
        "participant_count": len(present),
    }
    for key in (
        "movement_kind",
        "status",
        "route_ref",
        "source_place_ref",
        "destination_place_ref",
        "started_at",
        "last_progress_at",
        "rest_place_ref",
    ):
        value = movement.get(key)
        if isinstance(value, str) and value:
            context[key] = value

    elapsed = movement.get("elapsed_seconds")
    required = movement.get("required_seconds")
    if isinstance(elapsed, int) and not isinstance(elapsed, bool) and elapsed >= 0:
        context["elapsed_seconds"] = elapsed
    if isinstance(required, int) and not isinstance(required, bool) and required > 0:
        context["required_seconds"] = required
        if isinstance(elapsed, int) and not isinstance(elapsed, bool) and elapsed >= 0:
            context["progress_milli"] = min(1000, elapsed * 1000 // required)

    return context


class TravelAwareCampaignOperations(CampaignOperations):
    """Campaign operations with exact route-party context added to play reads."""

    def play_context(self) -> Mapping[str, Any]:
        # Keep the existing authoritative assembly untouched, then enrich it
        # only when the campaign has not changed between the base snapshot and
        # the exact movement read. A moving revision fails closed rather than
        # splicing two campaign moments into one player context.
        for _attempt in range(2):
            base = dict(super().play_context())
            campaign = base.get("campaign")
            if not isinstance(campaign, Mapping):
                return base
            player_id = str(campaign.get("player_id") or "")
            if not player_id:
                return base
            try:
                with self._locked():
                    self.coordinator.git.assert_pristine()
                    before = self._read_fingerprint()
                    meta = self.repository.read_json("state/meta.json")
                    if (
                        not isinstance(meta, Mapping)
                        or int(meta.get("revision", -1)) != int(campaign.get("revision", -2))
                        or str(meta.get("campaign_id") or "") != str(campaign.get("campaign_id") or "")
                        or before[1] != str(campaign.get("state_root") or "")
                    ):
                        continue
                    player_sheet = self.sheet_resolver(player_id)
                    if not isinstance(player_sheet, Mapping):
                        return base
                    movement = movement_scene_projection(
                        read_json=self.repository.read_json,
                        sheet_resolver=self.sheet_resolver,
                        player_id=player_id,
                        player_sheet=player_sheet,
                    )
                    self._require_read_only(before, "play_context_travel_projection_mutated_campaign")
            except OperationError:
                raise
            except (FileNotFoundError, KeyError, TypeError, ValueError):
                return base

            if movement is None:
                return base

            ids = _unique_person_refs(movement.get("participant_person_ids"))
            scene = dict(base.get("scene", {})) if isinstance(base.get("scene"), Mapping) else {}
            present: list[str] = []
            existing_present = scene.get("present_person_ids", [])
            for ref in ([*existing_present] if isinstance(existing_present, list) else []) + ids:
                if isinstance(ref, str) and ref and ref not in present:
                    present.append(ref)
            scene["present_person_ids"] = present
            # Exact movement ownership establishes co-presence, not line of
            # sight. Keep the narrower existing visible projection unchanged;
            # scouts or convoy elements may share a movement while out of view.
            scene["movement_present_person_ids"] = ids
            scene["movement_context"] = movement
            base["scene"] = scene

            person_reads = dict(base.get("person_reads", {})) if isinstance(base.get("person_reads"), Mapping) else {}
            suggested: list[str] = []
            existing_suggested = person_reads.get("suggested_owner_ids", [])
            for ref in ([*existing_suggested] if isinstance(existing_suggested, list) else []) + ids:
                if isinstance(ref, str) and ref and ref not in suggested:
                    suggested.append(ref)
            person_reads["suggested_owner_ids"] = suggested
            base["person_reads"] = person_reads
            try:
                validate_bounded_json(base, label="play context", allow_float=True)
            except ValueError as exc:
                raise OperationError(503, "play_context_out_of_bounds") from exc
            return base

        raise OperationError(503, "play_context_state_changed_during_travel_projection")


__all__ = ["TravelAwareCampaignOperations", "movement_scene_projection"]
