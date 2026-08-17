from __future__ import annotations

import copy

from shinobi_runtime.api.player_house_status_projection import _outreach_status
from shinobi_runtime.commands.external_house_intake_origin import _consume_outreach_commitment
from shinobi_runtime.commands.paths import COMMITMENT_REGISTRY_PATH
from shinobi_runtime.sim.events import CampaignTime


class _Repository:
    def __init__(self, records):
        self.registry = {"records": copy.deepcopy(records)}

    def read_json(self, path: str):
        assert path == COMMITMENT_REGISTRY_PATH
        return copy.deepcopy(self.registry)


class _Operations:
    def __init__(self, records):
        self.repository = _Repository(records)


def _row(status: str = "overdue"):
    return {
        "id": "commitment.outreach.test.00",
        "kind": "promise",
        "subject_ref": "pc_wei_tang",
        "target_ref": "pool.iwa.civilian_general",
        "host_ref": "house.tang",
        "created_at": "SE-0061-07-01T19:30:00",
        "due_at": "SE-0061-07-15T19:30:00",
        "status": status,
        "authority_basis": (
            "institution_recruitment_outreach:recruitment.sword_manor_outreach:"
            "project:recruitment.sword_manor_outreach"
        ),
    }


def test_review_ready_outreach_is_explicitly_actionable_for_its_player_requester() -> None:
    status = _outreach_status(_Operations([_row()]), "pc_wei_tang")
    assert status["status"] == "review_ready"
    assert status["player_review_required"] is True
    assert status["review_owner_ref"] == "pc_wei_tang"
    assert status["next_command_type"] == "institution_intake_resolution"
    assert status["review_ready_source_pool_refs"] == ["pool.iwa.civilian_general"]


def test_successful_intake_consumes_mature_outreach_window() -> None:
    repository = _Repository([_row()])
    before, after = _consume_outreach_commitment(
        repository,
        _row(),
        current_time=CampaignTime.parse("SE-0061-08-05T07:29:58"),
        accepted_count=4,
        source_pool_id="pool.iwa.civilian_general",
    )
    assert before["records"][0]["status"] == "overdue"
    assert after["records"][0]["status"] == "completed"
    assert "4 voluntary applicant(s)" in after["records"][0]["resolution_summary"]

    settled = _outreach_status(_Operations(after["records"]), "pc_wei_tang")
    assert settled["status"] == "settled"
    assert settled["player_review_required"] is False
    assert settled["review_ready_source_pool_refs"] == []
    assert settled["completed_source_pool_refs"] == ["pool.iwa.civilian_general"]
