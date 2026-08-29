"""Player-safe travel, combat-observation, and public-place play-context projection.

Mechanical physical presence remains owned by the exact route/custody/combat
resolvers. This module enriches the read projection so exact co-travelers,
arrived friendly combatants, observer-specific combat knowledge, and
already-derived public attendance become usable scene handoffs without turning
presentation state into mechanical authority.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

from shinobi_runtime.api.models import validate_bounded_json
from shinobi_runtime.api.operations import CampaignOperations, OperationError
from shinobi_runtime.martial_world.physical_presence import (
    active_combat_for_person,
    active_route_for_person,
    combat_person_arrived,
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
    if limit:
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


def combat_observation_scene_projection(
    *,
    read_json: Callable[[str], Any],
    player_id: str,
    ally_limit: int = 16,
) -> dict[str, Any] | None:
    """Project arrived friendly cast and observer-specific hostile counts safely.

    Exact combat persists each combatant's ``observed_refs`` and may register
    future reinforcements before they reach the local geometry. The projection
    exposes only friendly members whose reinforcement clock has arrived. Enemy
    identities and hidden opposing roster size remain private, and an ally's
    observations remain that ally's knowledge until communicated in-scene.
    """
    active = active_combat_for_person(read_json, player_id)
    if active is None:
        return None
    combat_ref, combat = active
    if not isinstance(combat, Mapping):
        return None

    sides = combat.get("sides", {})
    combatants = combat.get("combatants", {})
    if not isinstance(sides, Mapping) or not isinstance(combatants, Mapping):
        return None

    player_side_ref: str | None = None
    player_side_members: list[str] = []
    enemy_refs: list[str] = []
    for side_ref, raw_members in sides.items():
        members = _unique_person_refs(raw_members)
        if player_id in members:
            player_side_ref = str(side_ref)
            player_side_members = members
            break
    if player_side_ref is None:
        return None
    for side_ref, raw_members in sides.items():
        if str(side_ref) == player_side_ref:
            continue
        for ref in _unique_person_refs(raw_members):
            if ref not in enemy_refs:
                enemy_refs.append(ref)
    enemy_set = set(enemy_refs)
    friendly_present = [
        ref for ref in player_side_members if combat_person_arrived(combat, ref)
    ]
    if player_id not in friendly_present:
        # The active-combat resolver itself requires the player to have arrived,
        # so this is defensive against malformed legacy state only.
        friendly_present.insert(0, player_id)

    def observer_summary(observer_ref: str) -> dict[str, Any]:
        state = combatants.get(observer_ref, {})
        observed = _unique_person_refs(state.get("observed_refs")) if isinstance(state, Mapping) else []
        confirmed_count = sum(1 for ref in observed if ref in enemy_set)
        return {
            "observer_person_id": observer_ref,
            "confirmed_observed_hostile_count": confirmed_count,
        }

    player_observation = observer_summary(player_id)
    limit = max(0, min(24, int(ally_limit)))
    ally_observers: list[dict[str, Any]] = []
    if limit:
        for ref in friendly_present:
            if ref == player_id:
                continue
            ally_observers.append(observer_summary(ref))
            if len(ally_observers) >= limit:
                break

    return {
        "combat_ref": combat_ref,
        "friendly_participant_person_ids": friendly_present,
        "friendly_participant_count": len(friendly_present),
        "friendly_presence_semantics": "arrived_exact_combat_participants_only",
        "player_observation": player_observation,
        "ally_observer_summaries": ally_observers,
        "knowledge_semantics": "observer_specific_not_automatically_shared",
        "count_semantics": "confirmed_observed_hostiles_not_total_force",
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
    """Campaign operations with route-party, combat-presence and public-place context."""

    def play_context(self) -> Mapping[str, Any]:
        # Public-site attendance is already part of the base snapshot, so it can
        # be summarized without another state read. Route and combat projections
        # are read under the same revision/root check as the base context.
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
                    combat_observation = combat_observation_scene_projection(
                        read_json=self.repository.read_json,
                        player_id=player_id,
                    )
                    self._require_read_only(before, "play_context_travel_projection_mutated_campaign")
            except OperationError:
                raise
            except (FileNotFoundError, KeyError, TypeError, ValueError):
                return _validate_play_context(base)

            scene = dict(base.get("scene", {})) if isinstance(base.get("scene"), Mapping) else {}
            person_reads = dict(base.get("person_reads", {})) if isinstance(base.get("person_reads"), Mapping) else {}
            suggested = _unique_person_refs(person_reads.get("suggested_owner_ids"))

            if combat_observation is not None:
                scene["combat_observation_context"] = combat_observation
                combat_present = _unique_person_refs(
                    combat_observation.get("friendly_participant_person_ids")
                )
                present: list[str] = []
                existing_present = scene.get("present_person_ids", [])
                for ref in ([*existing_present] if isinstance(existing_present, list) else []) + combat_present:
                    if isinstance(ref, str) and ref and ref not in present:
                        present.append(ref)
                scene["present_person_ids"] = present
                scene["combat_present_person_ids"] = combat_present
                for ref in combat_present:
                    if ref not in suggested:
                        suggested.append(ref)
                person_reads["combat_participant_use"] = (
                    "combat_present_person_ids are exact friendly members whose combat-arrival clock has fired. "
                    "Use them as the current friendly battle cast; registered future reinforcements are not co-present yet."
                )
                person_reads["combat_observer_use"] = (
                    "Ally observer counts are that ally's exact stored combat observation, not automatically "
                    "Wei's knowledge. If a co-present ally reports what they saw, use the confirmed observed count "
                    "without treating it as the total hostile force."
                )

            if movement is not None:
                ids = _unique_person_refs(movement.get("participant_person_ids"))
                present = []
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
                for ref in ids:
                    if ref not in suggested:
                        suggested.append(ref)

            base["scene"] = scene
            person_reads["suggested_owner_ids"] = suggested
            base["person_reads"] = person_reads
            return _validate_play_context(base)

        raise OperationError(503, "play_context_state_changed_during_travel_projection")


__all__ = [
    "TravelAwareCampaignOperations",
    "combat_observation_scene_projection",
    "movement_scene_projection",
    "public_site_scene_projection",
]
