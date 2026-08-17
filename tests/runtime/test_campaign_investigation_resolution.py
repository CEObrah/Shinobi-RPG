from shinobi_runtime.commands.campaign_investigation import _select_case_truth
from shinobi_runtime.commands.specs import COMMAND_SPECS


def test_case_truth_is_stable_and_request_independent():
    keys = ("clerical", "criminal", "hostile")
    first = _select_case_truth(
        "mission.offer.example",
        "investigation.cargo_transfer_discrepancy",
        keys,
    )
    second = _select_case_truth(
        "mission.offer.example",
        "investigation.cargo_transfer_discrepancy",
        keys,
    )
    assert first == second
    assert first in keys


def test_investigation_command_never_accepts_caller_owned_outcome_fields():
    spec = COMMAND_SPECS["investigation_resolution"]
    assert spec.variants is not None
    forbidden = {"cause", "culprit", "clue", "result", "outcome", "evidence_result", "truth_key"}
    for variant in spec.variants.values():
        accepted = set(variant.required_fields) | set(variant.optional_fields)
        assert not (accepted & forbidden)


def test_investigation_has_two_causal_stages():
    spec = COMMAND_SPECS["investigation_resolution"]
    assert set(spec.variants or {}) == {"locate_scene", "examine_scene"}
    assert "investigator_refs" in spec.variants["locate_scene"].required_fields
    assert "assignments" in spec.variants["examine_scene"].required_fields
