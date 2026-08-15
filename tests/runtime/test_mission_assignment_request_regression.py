from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.campaign_mission_assignment import CampaignCommandPlanner  # noqa: F401
from shinobi_runtime.commands.mission_assignment_requests import (
    filter_candidates_for_focus,
    normalize_acceptable_ranks,
    objective_matches_focus,
)
from shinobi_runtime.commands.specs import COMMAND_SPECS


def test_frozen_rank_sequence_is_accepted_and_canonicalized():
    assert normalize_acceptable_ranks(("S", "A")) == ("A", "S")


def test_duplicate_or_unknown_rank_is_rejected():
    for value in (("A", "A"), ("A", "X"), (), "A"):
        try:
            normalize_acceptable_ranks(value)
        except CommandRejectedError as exc:
            assert exc.code == "mission_assignment_request_ranks_invalid"
        else:
            raise AssertionError(f"expected invalid rank request: {value!r}")


def test_combat_focus_reuses_existing_objective_dimensions():
    candidates = (
        ("demand.investigate", "investigate"),
        ("demand.protect", "protect"),
        ("demand.capture", "capture"),
        ("demand.rescue", "rescue"),
    )
    assert filter_candidates_for_focus(candidates, "combat") == (
        ("demand.protect", "protect"),
        ("demand.capture", "capture"),
        ("demand.rescue", "rescue"),
    )
    assert objective_matches_focus("combat", "investigate") is False
    assert objective_matches_focus("combat", "capture") is True


def test_request_surface_cannot_author_mission_content():
    spec = COMMAND_SPECS["mission_assignment_request_resolution"]
    accepted = set(spec.required_fields) | set(spec.optional_fields)
    assert accepted == {"team_ref", "acceptable_ranks", "mission_focus"}
    for forbidden in (
        "mission_id",
        "target_ref",
        "objective_kind",
        "destination_ref",
        "reward",
        "threat_summary",
        "outcome",
    ):
        assert forbidden not in accepted
