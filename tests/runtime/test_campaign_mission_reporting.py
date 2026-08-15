from shinobi_runtime.commands.campaign_mission_reporting import (
    _eligible_synthesis_claim,
    _mission_report_material_ref,
    _objective_report_event_matches,
)
from shinobi_runtime.commands.specs import COMMAND_SPECS


def _case():
    return {
        "mission_ref": "mission.offer.example",
        "objective_id": "objective.example",
        "status": "examined",
        "revealed_observations": [
            {
                "role": "records",
                "claim_ref": "claim.investigation.records",
            },
            {
                "role": "synthesis",
                "claim_ref": "claim.investigation.synthesis",
            },
        ],
    }


def test_report_accepts_only_examined_objective_synthesis_claim():
    case = _case()
    assert _eligible_synthesis_claim(
        case,
        mission_ref="mission.offer.example",
        objective_id="objective.example",
        claim_id="claim.investigation.synthesis",
    )
    assert not _eligible_synthesis_claim(
        case,
        mission_ref="mission.offer.example",
        objective_id="objective.example",
        claim_id="claim.investigation.records",
    )
    assert not _eligible_synthesis_claim(
        case,
        mission_ref="mission.offer.example",
        objective_id="objective.other",
        claim_id="claim.investigation.synthesis",
    )


def test_report_command_does_not_accept_recipient_or_outcome():
    spec = COMMAND_SPECS["mission_report_resolution"]
    accepted = set(spec.required_fields) | set(spec.optional_fields)
    assert "recipient_ref" not in accepted
    assert "target_status" not in accepted
    assert "outcome" not in accepted


def test_report_material_is_objective_specific():
    assert _mission_report_material_ref(
        "mission.offer.example",
        "objective.example",
        "claim.investigation.synthesis",
    ) == (
        "mission_report:mission.offer.example:objective.example:"
        "claim.investigation.synthesis"
    )


def test_objective_report_match_accepts_normalized_immutable_sequences():
    event = {
        "kind": "information_delivered",
        "causal_refs": (
            "mission.offer.example",
            "objective.example",
            "investigation.case.example",
            "claim.investigation.synthesis",
        ),
        "material_consequence_refs": (
            "delivery.example",
            "mission_report:mission.offer.example:objective.example:"
            "claim.investigation.synthesis",
        ),
    }
    assert _objective_report_event_matches(
        event,
        mission_ref="mission.offer.example",
        objective_id="objective.example",
    )


def test_objective_report_match_rejects_generic_delivery():
    event = {
        "kind": "information_delivered",
        "causal_refs": ("mission.offer.example", "objective.example"),
        "material_consequence_refs": ("delivery.example",),
    }
    assert not _objective_report_event_matches(
        event,
        mission_ref="mission.offer.example",
        objective_id="objective.example",
    )
