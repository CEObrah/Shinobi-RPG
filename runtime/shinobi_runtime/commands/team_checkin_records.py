"""Player-safe reads for durable player-led team check-in events."""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional
from urllib.parse import quote, unquote

from shinobi_runtime.autonomy import AutonomousPolicyBook

_READY_PREFIX = "event.player_led_team_checkin_ready."
_CHECKIN_PREFIX = "team_checkin."
_LABEL_PREFIX = "team_checkin_label:"
_TOPIC_PREFIX = "team_checkin_topic:"
_OWNERSHIP_PREFIX = "team_checkin_ownership:"
_CONTACT_MODE_PREFIX = "team_checkin_contact_mode:"
_HANDLING_PREFIX = "team_checkin_handling:"


def checkin_ref_for_event(event_id: str) -> str:
    if not isinstance(event_id, str) or not event_id.startswith(_READY_PREFIX):
        raise ValueError("team_checkin_event_invalid")
    return _CHECKIN_PREFIX + event_id[len(_READY_PREFIX):]


def event_id_for_checkin(checkin_ref: str) -> str:
    if not isinstance(checkin_ref, str) or not checkin_ref.startswith(_CHECKIN_PREFIX):
        raise ValueError("team_checkin_ref_invalid")
    digest = checkin_ref[len(_CHECKIN_PREFIX):]
    if not digest:
        raise ValueError("team_checkin_ref_invalid")
    return _READY_PREFIX + digest


def snapshot_refs(
    team_name: str,
    topic_cues: Iterable[str],
    *,
    ownership_cues: Iterable[str] = (),
    contact_mode: str | None = None,
) -> tuple[str, ...]:
    """Encode one immutable player-facing check-in snapshot in world-event refs."""

    label = team_name if isinstance(team_name, str) and team_name else "team"
    refs = [_LABEL_PREFIX + quote(label, safe="")]
    refs.extend(
        _TOPIC_PREFIX + quote(value, safe="")
        for value in topic_cues
        if isinstance(value, str) and value
    )
    refs.extend(
        _OWNERSHIP_PREFIX + quote(value, safe="")
        for value in ownership_cues
        if isinstance(value, str) and value
    )
    if isinstance(contact_mode, str) and contact_mode:
        refs.append(_CONTACT_MODE_PREFIX + quote(contact_mode, safe=""))
    return tuple(refs)


def _event_sources(repository: Any) -> list[Mapping[str, Any]]:
    try:
        registry = repository.read_json("state/reg/world-events.json")
    except (FileNotFoundError, ValueError):
        return []
    if not isinstance(registry, Mapping):
        return []
    sources: list[Mapping[str, Any]] = [registry]
    archive_refs = registry.get("archive_refs")
    if isinstance(archive_refs, list):
        for path in reversed([value for value in archive_refs if isinstance(value, str)]):
            try:
                archive = repository.read_json(path)
            except (FileNotFoundError, ValueError):
                continue
            if isinstance(archive, Mapping):
                sources.append(archive)
    return sources


def iter_world_events(repository: Any) -> Iterable[Mapping[str, Any]]:
    for source in _event_sources(repository):
        events = source.get("events")
        if not isinstance(events, list):
            continue
        for event in reversed(events):
            if isinstance(event, Mapping):
                yield event


def _player_can_see(event: Mapping[str, Any], player_id: str) -> bool:
    visibility = event.get("visibility")
    if not isinstance(visibility, Mapping):
        return False
    audience = visibility.get("audience_refs")
    witnesses = visibility.get("witness_refs")
    return player_id in (audience or ()) or player_id in (witnesses or ())


def _snapshot_from_event(
    event: Mapping[str, Any],
) -> tuple[Optional[str], list[str], list[str], Optional[str]]:
    material = event.get("material_consequence_refs")
    if not isinstance(material, list):
        return None, [], [], None
    label: Optional[str] = None
    topics: list[str] = []
    ownership: list[str] = []
    contact_mode: Optional[str] = None
    for ref in material:
        if not isinstance(ref, str):
            continue
        if ref.startswith(_LABEL_PREFIX):
            value = unquote(ref[len(_LABEL_PREFIX):])
            if value:
                label = value
        elif ref.startswith(_TOPIC_PREFIX):
            value = unquote(ref[len(_TOPIC_PREFIX):])
            if value and value not in topics:
                topics.append(value)
        elif ref.startswith(_OWNERSHIP_PREFIX):
            value = unquote(ref[len(_OWNERSHIP_PREFIX):])
            if value and value not in ownership:
                ownership.append(value)
        elif ref.startswith(_CONTACT_MODE_PREFIX):
            value = unquote(ref[len(_CONTACT_MODE_PREFIX):])
            if value:
                contact_mode = value
    return label, topics[:3], ownership[:3], contact_mode


def _legacy_snapshot(repository: Any, team_ref: str, source_event: Mapping[str, Any]) -> tuple[str, list[str]]:
    candidates: list[str] = []
    affected = source_event.get("affected_owner_refs")
    if isinstance(affected, list):
        candidates.extend(value for value in affected if isinstance(value, str) and value)
    candidates.append(team_ref)
    team: Optional[Mapping[str, Any]] = None
    for path in candidates:
        try:
            loaded = repository.read_json(path)
        except (FileNotFoundError, ValueError):
            continue
        if isinstance(loaded, Mapping) and loaded.get("id") == team_ref:
            team = loaded
            break
    if team is None:
        raise ValueError("team_checkin_team_unavailable")
    team_type = team.get("team_type")
    if not isinstance(team_type, str) or not team_type:
        raise ValueError("team_checkin_team_invalid")
    try:
        profile = AutonomousPolicyBook(repository).team_profile(team_type)
    except (TypeError, ValueError) as exc:
        raise ValueError("team_checkin_policy_invalid") from exc
    training_focus = profile.get("training_focus", [])
    topics = [value for value in training_focus if isinstance(value, str) and value][:2]
    if isinstance(team.get("current_assignment_ref"), str):
        topics.append("current assignment readiness")
    else:
        topics.append("readiness, equipment, and the next training block")
    name = team.get("name")
    return (name if isinstance(name, str) and name else team_ref), topics[:3]


def _handling_event(repository: Any, source_event_id: str, player_id: str) -> Optional[Mapping[str, Any]]:
    for event in iter_world_events(repository):
        if event.get("kind") != "player_led_team_checkin_handled" or not _player_can_see(event, player_id):
            continue
        causal = event.get("causal_refs")
        if isinstance(causal, list) and source_event_id in causal:
            return event
    return None


def project_team_checkin(repository: Any, checkin_ref: str, player_id: str) -> Mapping[str, Any]:
    source_event_id = event_id_for_checkin(checkin_ref)
    source: Optional[Mapping[str, Any]] = None
    for event in iter_world_events(repository):
        if event.get("id") == source_event_id:
            source = event
            break
    if source is None or source.get("kind") != "player_led_team_checkin_ready" or not _player_can_see(source, player_id):
        raise ValueError("team_checkin_not_player_visible")
    hosts = source.get("host_refs")
    actors = source.get("actor_refs")
    team_ref = next((value for value in hosts or () if isinstance(value, str) and value.startswith("team.")), None)
    contact_ref = next((value for value in actors or () if isinstance(value, str) and value != player_id), None)
    if not isinstance(team_ref, str) or not isinstance(contact_ref, str):
        raise ValueError("team_checkin_event_invalid")
    team_name, topics, ownership, contact_mode = _snapshot_from_event(source)
    snapshot_basis = "event_snapshot"
    if not team_name or not topics:
        team_name, topics = _legacy_snapshot(repository, team_ref, source)
        ownership = []
        contact_mode = None
        snapshot_basis = "legacy_reconstructed"
    handling = _handling_event(repository, source_event_id, player_id)
    handling_value: Optional[str] = None
    handled_event_ref: Optional[str] = None
    handled_at: Optional[str] = None
    if isinstance(handling, Mapping):
        handled_event_ref = handling.get("id") if isinstance(handling.get("id"), str) else None
        timing = handling.get("timing")
        if isinstance(timing, Mapping) and isinstance(timing.get("occurred_at"), str):
            handled_at = timing.get("occurred_at")
        material = handling.get("material_consequence_refs")
        if isinstance(material, list):
            for ref in material:
                if isinstance(ref, str) and ref.startswith(_HANDLING_PREFIX):
                    parts = ref[len(_HANDLING_PREFIX):].split(":", 1)
                    if parts and parts[0]:
                        handling_value = parts[0]
                        break
    timing = source.get("timing")
    ready_at = timing.get("occurred_at") if isinstance(timing, Mapping) else None
    return {
        "checkin_ref": checkin_ref,
        "source_event_ref": source_event_id,
        "team_ref": team_ref,
        "team_name": team_name,
        "contact_actor_ref": contact_ref,
        "ready_at": ready_at,
        "topic_cues": topics,
        "ownership_cues": ownership,
        "contact_mode": contact_mode,
        "handled": handling is not None,
        "handling": handling_value,
        "handled_event_ref": handled_event_ref,
        "handled_at": handled_at,
        "snapshot_basis": snapshot_basis,
    }


def player_team_checkins(repository: Any, player_id: str, *, limit: int = 8) -> list[Mapping[str, Any]]:
    ready_ids: list[str] = []
    for event in iter_world_events(repository):
        event_id = event.get("id")
        if (
            event.get("kind") == "player_led_team_checkin_ready"
            and isinstance(event_id, str)
            and _player_can_see(event, player_id)
            and event_id not in ready_ids
        ):
            ready_ids.append(event_id)
    projected: list[Mapping[str, Any]] = []
    for event_id in reversed(ready_ids[: max(1, limit)]):
        try:
            projected.append(project_team_checkin(repository, checkin_ref_for_event(event_id), player_id))
        except ValueError:
            continue
    return projected


__all__ = [
    "checkin_ref_for_event",
    "event_id_for_checkin",
    "iter_world_events",
    "player_team_checkins",
    "project_team_checkin",
    "snapshot_refs",
]
