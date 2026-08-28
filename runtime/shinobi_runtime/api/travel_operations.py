"""Player-safe travel and public-place play-context projection.

Mechanical physical presence remains owned by the exact route/custody/combat
resolvers. This module enriches the read projection so exact co-travelers and
already-derived public attendance become usable scene handoffs without turning
presentation state into mechanical authority.
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


def public_site_scene_projection(scene: Mapping[str, Any], *, sample_limit: int = 8) -> dict[str, Any] | None:
    """Summarize deterministic public-site attendance into a bounded GM handoff.

    ``derived_present_person_ids`` is already a player-safe read-time attendance
    projection. It can be large, so expose its exact count plus a small,
    deterministic, namespace-diverse sample for progressive person reads. Site
    attendance proves shared public venue presence only; it does not establish
    close adjacency, line of sight, conversation, private knowledge or combat
    access.
    """
    site_ref = scene.get("location_id")
    if not isinstance(site_ref, str) or not site_ref.startswith("site."):
        return None
    attendees = _unique_person_refs(scene.get("derived_present_person_ids"))
    if not attendees:
        return None

    limit = max(0, min(16, int(sample_limit)))
    samples: list[str] = []
    seen_namespaces: set[str] = set()
    for ref in attendees:
        namespace = ref.rsplit(".", 1)[0] if "." in ref else ref
        if namespace in seen_namespaces:
            continue
        seen_namespaces.add(namespace)
        samples.append(ref)
        if len(samples) >= limit:
            break
    if len(samples) < limit:
        for ref in attendees:
            if ref in samples:
                continue
            samples.append(ref)
            if len(samples) >= limit:
                break

    return {
        "site_ref": site_ref,
        "derived_attendee_count": len(attendees),
        "sample_person_ids": samples,
        "presence_semantics": "shared_public_site_only",
    }


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


def _enrich_public_site_context(base: dict[str, Any]) -> None:
    scene = dict(base.get("scene", {})) if isinstance(base.get("scene"), Mapping) else {}
    public_context = public_site_scene_projection(scene)
    if public_context is None:
        return
    scene["public_site_context"] = public_context
    base["scene"] = scene

    person_reads = dict(base.get("person_reads", {})) if isinstance(base.get("person_reads"), Mapping) else {}
    suggested = _unique_person_refs(person_reads.get("suggested_owner_ids"))
    for ref in _unique_person_refs(public_context.get("sample_person_ids")):
        if ref not in suggested:
            suggested.append(ref)
    person_reads["suggested_owner_ids"] = suggested
    person_reads["public_site_sample_use"] = (
        "Sample IDs are deterministic player-safe public attendees for progressive reads; "
        "attendance does not imply direct interaction or combat adjacency."
    )
    base["person_reads"] = person_reads


def _validate_play_context(base: dict[str, Any]) -> dict[str, Any]:
    try:
        validate_bounded_json(base, label="play context", allow_float=True)
    except ValueError as exc:
        raise OperationError(503, "play_context_out_of_bounds") from exc
    return base


class TravelAwareCampaignOperations(CampaignOperations):
    """Campaign operations with route-party and public-place read context."""

    def play_context(self) -> Mapping[str, Any]:
        # Public-site attendance is already part of the base snapshot, so it can
        # be summarized without another state read. This works even when there
        # is no active route movement.
        for _attempt in range(2):
            base = dict(super().play_context())
            _enrich_public_site_context(base)
            campaign = base.get("campaign")
            if not isinstance(campaign, Mapping):
                return _validate_play_context(base)
            player_id = str(campaign.get("player_id") or "")
            if not player_id:
                return _validate_play_context(base)
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
                        return _validate_play_context(base)
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
                return _validate_play_context(base)

            if movement is None:
                return _validate_play_context(base)

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
            suggested = _unique_person_refs(person_reads.get("suggested_owner_ids"))
            for ref in ids:
                if ref not in suggested:
                    suggested.append(ref)
            person_reads["suggested_owner_ids"] = suggested
            base["person_reads"] = person_reads
            return _validate_play_context(base)

        raise OperationError(503, "play_context_state_changed_during_travel_projection")


__all__ = [
    "TravelAwareCampaignOperations",
    "movement_scene_projection",
    "public_site_scene_projection",
]
