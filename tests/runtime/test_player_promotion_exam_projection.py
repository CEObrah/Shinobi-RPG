from shinobi_runtime.api import player_promotion_exam_projection as module


CYCLE_ID = "promotion_exam_cycle.promotion_exam.konoha.chunin.0061-07"
PROFILE_ID = "promotion_exam.konoha.chunin"


class FakeRepository:
    def __init__(self):
        self.records = {
            "state/reg/shinobi-career-pipeline.json": {
                "schema": "shinobi-career-pipeline",
                "version": 1,
                "history": [
                    {
                        "kind": "promotion_exam_cycle_phase",
                        "at": "SE-0061-07-01T07:00:00",
                        "cycle_id": CYCLE_ID,
                        "profile_ref": PROFILE_ID,
                        "phase": "registration",
                        "canon_status": "campaign_institutional_not_future_canon",
                        "authority_ref": "canon_hiruzen",
                    },
                    {
                        "kind": "promotion_exam_registration",
                        "at": "SE-0061-07-01T07:01:00",
                        "cycle_id": CYCLE_ID,
                        "profile_ref": PROFILE_ID,
                        "team_ref": "team.konoha.fujin",
                        "instructor_ref": "pc_wei_tang",
                        "candidate_refs": ["char.kai"],
                        "canon_status": "campaign_institutional_not_future_canon",
                    },
                ],
            },
            "game/rules/career/promotion-exams.json": {
                "schema": "promotion-exam-rules",
                "version": 2,
                "profiles": {
                    PROFILE_ID: {
                        "id": PROFILE_ID,
                        "enabled": True,
                        "world_arc_kind": "institutional_promotion_cycle",
                        "canon_status": "campaign_institutional_not_future_canon",
                        "institution_ref": "institution.konoha.academy",
                        "authority_ref": "canon_hiruzen",
                        "service_village": "konoha",
                        "source_rank": "Genin",
                        "target_rank": "Chunin",
                        "registration_authority": "active_team_leader",
                        "cycle_start_months": [1, 7],
                        "phases": [
                            "registration",
                            "qualification",
                            "field_evaluation",
                            "finals",
                            "promotion_review",
                            "closed",
                        ],
                    }
                },
            },
        }

    def read_json(self, path):
        return self.records[path]


class FakeOperations:
    def __init__(self):
        self.repository = FakeRepository()
        self.owners = {
            "team.konoha.fujin": {
                "schema": "exact-team",
                "id": "team.konoha.fujin",
                "status": "active",
                "assignment_authority_ref": "institution.konoha.academy",
                "leader_ref": "pc_wei_tang",
                "member_refs": [
                    "pc_wei_tang",
                    "char.kai",
                    "char.mei_arakawa",
                    "char.riku_hyuga",
                ],
            },
            "char.kai": person("char.kai", eligible=True),
            "char.mei_arakawa": person("char.mei_arakawa", eligible=True),
            "char.riku_hyuga": person("char.riku_hyuga", eligible=False),
        }

    def _owner_record(self, owner_id):
        return f"state/{owner_id}.json", self.owners[owner_id]


def person(owner_id, *, eligible):
    return {
        "schema": "shinobi_character",
        "owner_id": owner_id,
        "life_status": "alive",
        "village_or_affiliation": "Konohagakure",
        "official_rank_or_status": "Genin",
        "career_state": {"promotion_eligible": eligible},
    }


def test_fresh_exam_handoff_projects_eligible_registered_and_unregistered(monkeypatch):
    monkeypatch.setattr(
        module,
        "team_refs_for_member",
        lambda repository, player_id: ("team.konoha.fujin",),
    )
    rows = module._promotion_exam_handoffs(
        FakeOperations(),
        player_id="pc_wei_tang",
    )
    assert rows == [
        {
            "cycle_id": CYCLE_ID,
            "profile_ref": PROFILE_ID,
            "phase": "registration",
            "institution_ref": "institution.konoha.academy",
            "team_ref": "team.konoha.fujin",
            "registration_open": True,
            "eligible_candidate_refs": ["char.kai", "char.mei_arakawa"],
            "registered_candidate_refs": ["char.kai"],
            "unregistered_candidate_refs": ["char.mei_arakawa"],
        }
    ]


def test_non_leader_team_is_not_projected(monkeypatch):
    operations = FakeOperations()
    operations.owners["team.konoha.fujin"]["leader_ref"] = "char.other"
    monkeypatch.setattr(
        module,
        "team_refs_for_member",
        lambda repository, player_id: ("team.konoha.fujin",),
    )
    assert module._promotion_exam_handoffs(
        operations,
        player_id="pc_wei_tang",
    ) == []
