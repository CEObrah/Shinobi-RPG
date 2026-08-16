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
                        "evaluation_stages": {
                            "qualification": {"threshold": 78, "components": [{"path": "attributes.intelligence", "weight": 1}]},
                            "field_evaluation": {"threshold": 82, "components": [{"path": "attributes.awareness", "weight": 1}]},
                            "finals": {"threshold": 86, "components": [{"path": "attributes.composure", "weight": 1}]},
                        },
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
        "attributes": {"intelligence": 90, "awareness": 90, "composure": 90},
    }


def test_fresh_exam_handoff_projects_eligible_registered_and_unregistered(monkeypatch):
    monkeypatch.setattr(module, "team_refs_for_member", lambda repository, player_id: ("team.konoha.fujin",))
    rows = module._promotion_exam_handoffs(FakeOperations(), player_id="pc_wei_tang")
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
            "evaluation_open": False,
            "stage_candidate_refs": [],
            "evaluated_candidate_refs": [],
            "unevaluated_candidate_refs": [],
            "evaluation_results": [],
        }
    ]


def test_field_evaluation_projects_unresolved_and_durable_results(monkeypatch):
    operations = FakeOperations()
    history = operations.repository.records["state/reg/shinobi-career-pipeline.json"]["history"]
    history[0]["phase"] = "field_evaluation"
    history[1]["candidate_refs"] = ["char.kai", "char.mei_arakawa"]
    history.append(
        {
            "kind": "promotion_exam_evaluation",
            "at": "SE-0061-07-13T07:00:00",
            "cycle_id": CYCLE_ID,
            "profile_ref": PROFILE_ID,
            "phase": "field_evaluation",
            "team_ref": "team.konoha.fujin",
            "evaluator_ref": "canon_hiruzen",
            "candidate_ref": "char.kai",
            "score": 91,
            "threshold": 82,
            "outcome": "pass",
            "canon_status": "campaign_institutional_not_future_canon",
        }
    )
    monkeypatch.setattr(module, "team_refs_for_member", lambda repository, player_id: ("team.konoha.fujin",))
    row = module._promotion_exam_handoffs(operations, player_id="pc_wei_tang")[0]
    assert row["evaluation_open"] is True
    assert row["stage_candidate_refs"] == ["char.kai", "char.mei_arakawa"]
    assert row["evaluated_candidate_refs"] == ["char.kai"]
    assert row["unevaluated_candidate_refs"] == ["char.mei_arakawa"]
    assert row["evaluation_results"] == [
        {"candidate_ref": "char.kai", "score": 91, "threshold": 82, "outcome": "pass"}
    ]


def test_non_leader_team_is_not_projected(monkeypatch):
    operations = FakeOperations()
    operations.owners["team.konoha.fujin"]["leader_ref"] = "char.other"
    monkeypatch.setattr(module, "team_refs_for_member", lambda repository, player_id: ("team.konoha.fujin",))
    assert module._promotion_exam_handoffs(operations, player_id="pc_wei_tang") == []
