from __future__ import annotations

import pytest

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.runtime_stability import RuntimeStabilityMixin


class _SemanticEventBase:
    def _append_semantic_event(self, registry, *args, **kwargs):
        event_id = "event.test.semantic"
        registry["events"].append(
            {
                "id": event_id,
                "kind": kwargs["kind"],
                "status": "resolved",
                "host_refs": list(kwargs.get("host_refs", ())),
                "actor_refs": list(kwargs.get("actor_refs", ())),
                "place_refs": list(kwargs.get("place_refs", ())),
                "affected_owner_refs": list(kwargs.get("affected_owner_refs", ())),
                "material_consequence_refs": list(kwargs.get("material_consequence_refs", ())),
            }
        )
        return event_id


class _Harness(RuntimeStabilityMixin, _SemanticEventBase):
    def _resolve_covered_owner(self, ref):
        if ref == "team.blackhound":
            return "state/team/blackhound.json", "digest"
        raise CommandRejectedError("owner_unresolved")


def _registry():
    return {"events": [], "__pending_archive_writes__": {}}


def test_semantic_event_derives_missing_owner_from_host() -> None:
    registry = _registry()
    event_id = _Harness()._append_semantic_event(
        registry,
        kind="player_led_team_checkin_handled",
        host_refs=("team.blackhound",),
        actor_refs=("pc_wei_tang",),
        affected_owner_refs=(),
        material_consequence_refs=("team_checkin_handling:acknowledge:team_checkin.test",),
    )

    event = next(row for row in registry["events"] if row["id"] == event_id)
    assert event["affected_owner_refs"] == ["state/team/blackhound.json"]


def test_semantic_event_still_fails_closed_when_host_owner_cannot_resolve() -> None:
    registry = _registry()
    with pytest.raises(CommandRejectedError) as excinfo:
        _Harness()._append_semantic_event(
            registry,
            kind="player_led_team_checkin_handled",
            host_refs=("team.unknown",),
            actor_refs=("pc_wei_tang",),
            affected_owner_refs=(),
            material_consequence_refs=("team_checkin_handling:acknowledge:team_checkin.test",),
        )

    assert excinfo.value.code == "world_event_missing_affected_owner__player_led_team_checkin_handled"
