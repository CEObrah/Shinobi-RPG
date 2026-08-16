from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.semantic_event_integrity import _append_semantic_event
from shinobi_runtime.sim.events import CampaignTime


class _Planner:
    def _world_event_by_id(self, event_id, *, registry=None):
        return None

    def _roll_world_events(self, registry, *, at):
        del at
        events = registry["events"]
        if not events:
            return
        pending = registry.setdefault("__pending_archive_writes__", {})
        archive = pending.setdefault(
            "state/history/events/test-roll.json",
            {"events": []},
        )
        archive["events"].extend(events)
        events.clear()


def _command():
    return CommandEnvelope(
        campaign_id="shinobi-test",
        request_id="same-command-multiple-boundaries",
        actor_id="pc_wei_tang",
        command_type="advance_time",
        expected_revision=7,
        submitted_at="2026-08-16T16:30:00Z",
        payload={"target_time": "SE-0061-07-16T00:00:00"},
    )


def _registry():
    return {
        "events": [],
        "archive_refs": [],
        "__pending_archive_writes__": {},
    }


def test_same_command_same_kind_distinct_events_get_stable_unique_ids_after_archive_roll():
    planner = _Planner()
    command = _command()
    registry = _registry()
    at = CampaignTime.parse("SE-0061-07-15T18:00:00")

    first = _append_semantic_event(
        planner,
        registry,
        command=command,
        kind="institution_review_settled",
        at=at,
        material_consequence_refs=("review:first",),
    )
    first_replay = _append_semantic_event(
        planner,
        registry,
        command=command,
        kind="institution_review_settled",
        at=at,
        material_consequence_refs=("review:first",),
    )
    second = _append_semantic_event(
        planner,
        registry,
        command=command,
        kind="institution_review_settled",
        at=at,
        material_consequence_refs=("review:second",),
    )
    second_replay = _append_semantic_event(
        planner,
        registry,
        command=command,
        kind="institution_review_settled",
        at=at,
        material_consequence_refs=("review:second",),
    )

    assert first_replay == first
    assert second_replay == second
    assert first != second
    assert second.startswith(first + ".")

    archived = registry["__pending_archive_writes__"]["state/history/events/test-roll.json"]["events"]
    assert [row["id"] for row in archived] == [first, second]
