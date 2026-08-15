"""Player-safe scene-cast and presentation-vitality helpers.

This module does not create campaign truth. It projects already-authoritative
locality and scene relevance into bounded routing cues, then tells the GM what
kind of nonpersistent scene motion is safe to add without a gameplay write.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

_MAX_CAST_IDS = 24
_MAX_INTERACTION_IDS = 16


def _location_id(record: Mapping[str, Any]) -> str | None:
    for key in ("current_location_id", "location_ref", "location_id"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    life_course = record.get("life_course_state")
    if isinstance(life_course, Mapping):
        deployment = life_course.get("deployment")
        if isinstance(deployment, Mapping):
            for key in ("current_location_id", "location_ref", "location_id"):
                value = deployment.get(key)
                if isinstance(value, str) and value:
                    return value
    return None


def _clean_id_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _bounded(values: Sequence[str], limit: int = _MAX_CAST_IDS) -> tuple[list[str], bool]:
    unique = list(dict.fromkeys(values))
    return unique[:limit], len(unique) > limit


def build_scene_cast(
    *,
    scene: Mapping[str, Any],
    player_id: str,
    permitted_person_ids: Sequence[str],
    person_records: Mapping[str, Mapping[str, Any]],
    team_records: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any]:
    """Build a bounded cast without treating generic relevance as presence."""

    location_id = scene.get("location_id")
    permitted = {
        ref for ref in permitted_person_ids
        if isinstance(ref, str) and ref and ref != player_id
    }

    explicit_present: list[str] = []
    for field in ("present_person_ids", "visible_person_ids"):
        explicit_present.extend(
            ref for ref in _clean_id_list(scene.get(field)) if ref in permitted
        )

    nearby: list[str] = []
    basis: dict[str, list[str]] = {}
    if isinstance(location_id, str) and location_id:
        for person_id, record in person_records.items():
            if person_id not in permitted or not isinstance(record, Mapping):
                continue
            if _location_id(record) == location_id:
                nearby.append(person_id)
                basis.setdefault(person_id, []).append("exact_person_location")

        for team_ref, team in team_records.items():
            if not isinstance(team, Mapping) or _location_id(team) != location_id:
                continue
            members = team.get("member_refs")
            if not isinstance(members, list):
                continue
            for person_id in members:
                if isinstance(person_id, str) and person_id in permitted:
                    nearby.append(person_id)
                    basis.setdefault(person_id, []).append(f"team_location:{team_ref}")

    loaded = _clean_id_list(scene.get("loaded_owner_ids"))
    referenced = [ref for ref in loaded if ref in permitted]
    for person_id in explicit_present:
        basis.setdefault(person_id, []).append("scene_present")
    for person_id in referenced:
        basis.setdefault(person_id, []).append("scene_reference")

    present_ids, present_truncated = _bounded(explicit_present)
    present_set = set(present_ids)
    nearby_ids, nearby_truncated = _bounded(
        [ref for ref in nearby if ref not in present_set]
    )
    occupied = present_set | set(nearby_ids)
    referenced_ids, referenced_truncated = _bounded(
        [ref for ref in referenced if ref not in occupied]
    )

    return {
        "present_people": present_ids,
        "visible_people": list(present_ids),
        "nearby_people": nearby_ids,
        "referenced_people": referenced_ids,
        "present_count": len(list(dict.fromkeys(explicit_present))),
        "nearby_count": len(list(dict.fromkeys(ref for ref in nearby if ref not in present_set))),
        "referenced_count": len(list(dict.fromkeys(ref for ref in referenced if ref not in occupied))),
        "present_truncated": present_truncated,
        "nearby_truncated": nearby_truncated,
        "referenced_truncated": referenced_truncated,
        "basis": {
            person_id: list(dict.fromkeys(reasons))
            for person_id, reasons in basis.items()
            if person_id in present_set or person_id in nearby_ids or person_id in referenced_ids
        },
        "semantics": {
            "present_people": "Mechanically explicit immediate-scene presence when the scene owner records it.",
            "nearby_people": "Established player-accessible people at the same live site or on a co-located exact team; not automatically in the room or conversation.",
            "referenced_people": "Current scene relevance only; not physical-presence evidence.",
        },
    }


def apply_scene_vitality_handoff(
    payload: Mapping[str, Any],
    *,
    scene_cast: Mapping[str, Any],
) -> dict[str, Any]:
    projected = dict(payload)

    # Read-side guard for stale scene projections. An open time-passage
    # surface is not a protected decision merely because a prior time reducer
    # left decision_required populated. This changes only the player-safe
    # projection; the next non-interrupting time write normalizes persisted state.
    scene = projected.get("scene")
    if (
        isinstance(scene, Mapping)
        and scene.get("time_passage_allowed") is True
        and scene.get("decision_required") is not None
    ):
        updated_scene = dict(scene)
        updated_scene["decision_required"] = None
        projected["scene"] = updated_scene

    cast = dict(scene_cast)
    projected["scene_cast"] = cast

    candidates: list[str] = []
    for field in ("present_people", "nearby_people", "referenced_people"):
        for person_id in cast.get(field, []):
            if (
                isinstance(person_id, str)
                and person_id
                and person_id not in candidates
                and len(candidates) < _MAX_INTERACTION_IDS
            ):
                candidates.append(person_id)

    projected["scene_vitality"] = {
        "ephemeral_motion_allowed": True,
        "reversible_scene_local_interaction_allowed": True,
        "attempt_is_not_world_reaction": True,
        "nearby_entry_exit_may_be_ephemeral": True,
        "ordinary_background_roles_may_be_ephemeral": True,
        "interaction_candidate_ids": candidates,
        "scope": (
            "The GM may add nonpersistent background activity, incidental movement, brief greetings, "
            "conversation openings, and other ordinary scene motion that is plausible for the confirmed "
            "place, time, and cast. Inside an already-established interaction, routine acknowledgements, "
            "clarifying or follow-up questions, objections, examiner prompts, gestures, and procedural directions "
            "may continue when reversible. A nearby established person may enter or leave the immediate interaction "
            "when spatially plausible. A player attempt does not establish a world reaction. These presentation "
            "choices must not create or settle durable game facts."
        ),
        "durable_state_requires_runtime": [
            "mechanical outcomes",
            "new knowledge or disclosures",
            "relationship changes",
            "resources or money",
            "injury or recovery",
            "authority or office",
            "new access, acceptance, refusal, or institutional judgment",
            "commitments or promises",
            "mission state",
            "travel completion",
            "persistent location changes",
        ],
    }

    context_policy = projected.get("context_policy")
    if isinstance(context_policy, Mapping):
        updated = dict(context_policy)
        raw = updated.get("truncated_fields", [])
        truncated = [value for value in raw if isinstance(value, str)]
        for field in ("present", "nearby", "referenced"):
            if cast.get(f"{field}_truncated") is True:
                truncated.append(f"scene_cast.{field}_people")
        updated["truncated_fields"] = sorted(set(truncated))
        projected["context_policy"] = updated
    return projected


__all__ = [
    "_location_id",
    "apply_scene_vitality_handoff",
    "build_scene_cast",
]
