"""Player-safe immediate-family discovery from authoritative kinship routes."""
from __future__ import annotations

import copy
from functools import wraps
from typing import Any, Mapping

from shinobi_runtime.api.models import validate_bounded_json
from shinobi_runtime.api.operations import CampaignOperations, OperationError
from shinobi_runtime.commands.paths import KINSHIP_INDEX_PATH

_INSTALLED = False
_MAX_REFS = 32


def _string_refs(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str) and item}


def _player_family_context(
    operations: CampaignOperations,
    *,
    player_id: str,
) -> Mapping[str, Any]:
    try:
        kinship = operations.repository.read_json(KINSHIP_INDEX_PATH)
        permitted = set(operations._permitted_person_lookup_ids(player_id=player_id))
    except (FileNotFoundError, ValueError, OperationError) as exc:
        raise OperationError(503, "family_context_invalid") from exc
    links = kinship.get("person_links") if isinstance(kinship, Mapping) else None
    if not isinstance(links, Mapping):
        raise OperationError(503, "family_context_invalid")
    player_links = links.get(player_id, {})
    if not isinstance(player_links, Mapping):
        raise OperationError(503, "family_context_invalid")

    def visible(field: str) -> list[str]:
        return sorted(_string_refs(player_links.get(field)).intersection(permitted))[:_MAX_REFS]

    parents = visible("parents")
    siblings: set[str] = set()
    for parent_ref in _string_refs(player_links.get("parents")):
        parent_links = links.get(parent_ref)
        if not isinstance(parent_links, Mapping):
            continue
        siblings.update(_string_refs(parent_links.get("children")))
    siblings.discard(player_id)
    siblings.intersection_update(permitted)

    return {
        "parent_refs": parents,
        "sibling_refs": sorted(siblings)[:_MAX_REFS],
        "spouse_refs": visible("spouses"),
        "former_spouse_refs": visible("former_spouses"),
        "child_refs": visible("children"),
        "guardian_refs": visible("guardians"),
        "ward_refs": visible("wards"),
        "household_refs": sorted(_string_refs(player_links.get("households")))[:_MAX_REFS],
        "basis": "authoritative_kinship_links_intersected_with_current_player_person_access",
    }


def install_player_family_projection() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    original_play_context = CampaignOperations.play_context
    if getattr(original_play_context, "_player_family_projection", False):
        _INSTALLED = True
        return

    @wraps(original_play_context)
    def play_context(self: CampaignOperations) -> Mapping[str, Any]:
        response = copy.deepcopy(original_play_context(self))
        campaign = response.get("campaign") if isinstance(response, Mapping) else None
        player_id = campaign.get("player_id") if isinstance(campaign, Mapping) else None
        if not isinstance(player_id, str):
            return response
        if isinstance(response, dict):
            response["family_context"] = _player_family_context(self, player_id=player_id)
        validate_bounded_json(response, label="play context", allow_float=True)
        return response

    play_context._player_family_projection = True  # type: ignore[attr-defined]
    CampaignOperations.play_context = play_context
    _INSTALLED = True


__all__ = ["install_player_family_projection", "_player_family_context"]
