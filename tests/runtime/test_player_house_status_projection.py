from __future__ import annotations

from shinobi_runtime.api.player_house_status_projection import (
    _house_roster_status,
    _outreach_status,
)


class _Repo:
    def read_json(self, path: str):
        if path == "state/person-core/house-tang.json":
            return {
                "schema": "person-core-registry",
                "profiles": {
                    "ht.core.001": {
                        "institutional_progression": {
                            "standing": "junior_disciple",
                            "resolved_through": "SE-0061-07-01T07:00:00",
                        }
                    },
                    "ht.core.002": {
                        "institutional_progression": {
                            "standing": "senior_disciple",
                            "resolved_through": "SE-0061-07-01T07:00:00",
                        }
                    },
                },
            }
        if path == "state/reg/commitments.json":
            return {
                "records": [
                    {
                        "id": "commitment.outreach.test.00",
                        "kind": "promise",
                        "host_ref": "house.tang",
                        "target_ref": "pool.iwa.civilian_general",
                        "created_at": "SE-0061-06-15T07:00:00",
                        "due_at": "SE-0061-06-29T07:00:00",
                        "status": "overdue",
                        "authority_basis": "institution_recruitment_outreach:recruitment.sword_manor_outreach:test",
                    }
                ]
            }
        raise FileNotFoundError(path)


class _Ops:
    repository = _Repo()

    def _owner_record(self, ref: str):
        assert ref == "house.tang"
        return "state/house/house-tang.json", {
            "schema": "house",
            "id": "house.tang",
            "home": "place.sword_manor",
            "member_ids": ["pc_wei_tang", "ht.core.001", "ht.core.002"],
            "cohorts": [
                {
                    "id": "house.tang.cohort.junior",
                    "training": "junior_cycle",
                    "roster_refs": ["ht.core.001"],
                },
                {
                    "id": "house.tang.cohort.senior",
                    "training": "senior_cycle",
                    "roster_refs": ["ht.core.002"],
                },
            ],
            "operating_process": {
                "last_review": "SE-0061-07-01T07:00:00",
            },
        }


def test_house_status_reports_rostered_training_and_standings() -> None:
    status = _house_roster_status(_Ops())
    assert status["member_count"] == 3
    assert status["standing_counts"] == {
        "junior_disciple": 1,
        "senior_disciple": 1,
    }
    assert status["training_resolved_through_min"] == "SE-0061-07-01T07:00:00"
    assert status["training_resolved_through_max"] == "SE-0061-07-01T07:00:00"


def test_outreach_status_reports_mature_response_window() -> None:
    status = _outreach_status(_Ops())
    assert status["status"] == "review_ready"
    assert status["review_at"] == "SE-0061-06-29T07:00:00"
    assert status["review_ready_source_pool_refs"] == ["pool.iwa.civilian_general"]
