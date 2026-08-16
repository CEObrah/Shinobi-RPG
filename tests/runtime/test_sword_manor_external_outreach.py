import json
from pathlib import Path

import pytest

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.external_house_intake_origin import (
    _OutreachRepository,
    _require_mature_outreach,
)
from shinobi_runtime.sim.events import CampaignTime


ROOT = Path(__file__).resolve().parents[2]


class FakeRepository:
    def __init__(self, due_at="SE-0061-07-15T19:30:00"):
        base_rules = json.loads(
            (ROOT / "game/rules/recruitment/policies.json").read_text()
        )
        outreach = json.loads(
            (ROOT / "game/rules/recruitment/sword-manor-outreach.json").read_text()
        )
        self.records = {
            "game/rules/recruitment/policies.json": base_rules,
            "game/rules/recruitment/sword-manor-outreach.json": outreach,
            "state/reg/commitments.json": {
                "records": [
                    {
                        "id": "commitment.outreach.test.00",
                        "kind": "promise",
                        "subject_ref": "pc_wei_tang",
                        "target_ref": "pool.iwa.civilian_general",
                        "host_ref": "house.tang",
                        "created_at": "SE-0061-07-01T19:30:00",
                        "due_at": due_at,
                        "status": "active",
                        "summary": "external outreach",
                        "visibility": "public",
                        "authority_basis": "institution_recruitment_outreach:recruitment.sword_manor_outreach:commitment.growth.test",
                    }
                ]
            },
        }

    def read_json(self, path):
        return self.records[path]


def test_external_policy_is_voluntary_house_only_and_excludes_active_service():
    rule = json.loads(
        (ROOT / "game/rules/recruitment/sword-manor-outreach.json").read_text()
    )
    assert rule["eligible_source_owner_refs"] == [
        "faction_iwa",
        "faction_kiri",
        "faction_kumo",
        "faction_suna",
    ]
    assert rule["eligible_source_categories"] == [
        "civilian_general",
        "support_service",
    ]
    assert set(rule["outreach_modes"]) == {"letters", "open_tryouts"}
    assert rule["destination_service_status"] == "house_tang_private_personnel_not_konoha_shinobi"
    assert "shinobi" in rule["source_sovereignty_rule"].lower()


def test_outreach_policy_inherits_existing_sword_manor_intake_safeguards():
    repository = FakeRepository()
    merged = _OutreachRepository(repository).read_json(
        "game/rules/recruitment/policies.json"
    )["policies"]["recruitment.sword_manor_outreach"]
    assert merged["destination_owner_ref"] == "house.tang"
    assert merged["oath_required"] is True
    assert merged["max_intake_per_batch"] == 12
    assert merged["max_applicants_per_batch"] == 24
    assert merged["cooldown_days"] == 30
    assert merged["eligible_source_categories"] == [
        "civilian_general",
        "support_service",
    ]


def test_foreign_intake_is_blocked_until_outreach_response_window_matures():
    repository = FakeRepository()
    with pytest.raises(
        CommandRejectedError,
        match="institution_recruitment_outreach_response_window_not_mature",
    ):
        _require_mature_outreach(
            repository,
            actor_ref="pc_wei_tang",
            institution_ref="house.tang",
            policy_ref="recruitment.sword_manor_outreach",
            source_pool_id="pool.iwa.civilian_general",
            current_time=CampaignTime.parse("SE-0061-07-08T19:30:00"),
        )

    row = _require_mature_outreach(
        repository,
        actor_ref="pc_wei_tang",
        institution_ref="house.tang",
        policy_ref="recruitment.sword_manor_outreach",
        source_pool_id="pool.iwa.civilian_general",
        current_time=CampaignTime.parse("SE-0061-07-15T19:30:00"),
    )
    assert row["id"] == "commitment.outreach.test.00"
