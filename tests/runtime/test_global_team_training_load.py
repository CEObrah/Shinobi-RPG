from decimal import Decimal

import pytest

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.global_team_training_load import (
    assert_global_team_training_load,
    member_team_training_load,
)
from shinobi_runtime.sim.events import CampaignTime


class FakeRepository:
    def __init__(self):
        self.records = {
            "game/data/mechanics/training.json": {
                "models": {
                    "training.team": {
                        "schedule_limits": {
                            "cycle_length_days": 7,
                            "maximum_hours_per_member_per_week": "48",
                            "minimum_recovery_hours": 8,
                        }
                    }
                }
            },
            "state/index/owners/team.json": {
                "owners": {
                    "team.alpha": "state/team/alpha.json",
                    "team.beta": "state/team/beta.json",
                }
            },
            "state/team/alpha.json": team(
                "team.alpha",
                [
                    session(
                        "training.session.alpha",
                        "SE-0061-07-01T07:00:00",
                        "SE-0061-07-01T09:00:00",
                        2,
                    )
                ],
            ),
            "state/team/beta.json": team("team.beta", []),
        }

    def read_json(self, path):
        return self.records[path]


def session(session_ref, started_at, ended_at, active_hours):
    return {
        "session_ref": session_ref,
        "started_at": started_at,
        "ended_at": ended_at,
        "active_hours": str(active_hours),
        "instructor_ref": "char.teacher",
        "member_refs": ["pc_wei_tang", "char.other"],
        "targets": {"pc_wei_tang": "operational_skills.leadership"},
    }


def team(team_ref, recent_sessions):
    return {
        "schema": "exact-team",
        "id": team_ref,
        "training": {"recent_sessions": recent_sessions},
    }


def test_load_aggregates_session_from_another_exact_team():
    load = member_team_training_load(
        FakeRepository(),
        "pc_wei_tang",
        as_of=CampaignTime.parse("SE-0061-07-01T10:00:00"),
    )
    assert load["weekly_hours_used"] == Decimal("2")
    assert str(load["last_session_ended_at"]) == "SE-0061-07-01T09:00:00"
    assert str(load["recovery_ready_at"]) == "SE-0061-07-01T17:00:00"
    assert load["recovery_ready_now"] is False


def test_second_team_cannot_bypass_personal_recovery():
    with pytest.raises(CommandRejectedError, match="team_training_recovery_required"):
        assert_global_team_training_load(
            FakeRepository(),
            ("pc_wei_tang",),
            started_at=CampaignTime.parse("SE-0061-07-01T12:00:00"),
            ended_at=CampaignTime.parse("SE-0061-07-01T14:00:00"),
            active_hours=Decimal("2"),
        )


def test_staged_other_team_session_blocks_same_transaction_overlap():
    repository = FakeRepository()
    staged_beta = team(
        "team.beta",
        [
            session(
                "training.session.beta.staged",
                "SE-0061-07-01T10:00:00",
                "SE-0061-07-01T12:00:00",
                2,
            )
        ],
    )
    with pytest.raises(CommandRejectedError, match="team_training_recovery_required"):
        assert_global_team_training_load(
            repository,
            ("pc_wei_tang",),
            started_at=CampaignTime.parse("SE-0061-07-01T14:00:00"),
            ended_at=CampaignTime.parse("SE-0061-07-01T16:00:00"),
            active_hours=Decimal("2"),
            record_writes={"state/team/beta.json": staged_beta},
        )


def test_weekly_hours_sum_across_multiple_exact_teams():
    repository = FakeRepository()
    repository.records["state/team/beta.json"]["training"]["recent_sessions"] = [
        session(
            "training.session.beta",
            "SE-0061-07-02T09:00:00",
            "SE-0061-07-03T23:00:00",
            38,
        )
    ]
    load = member_team_training_load(
        repository,
        "pc_wei_tang",
        as_of=CampaignTime.parse("SE-0061-07-04T12:00:00"),
    )
    assert load["weekly_hours_used"] == Decimal("40")
    assert load["weekly_hours_remaining"] == Decimal("8")
    with pytest.raises(CommandRejectedError, match="team_training_weekly_limit_exceeded"):
        assert_global_team_training_load(
            repository,
            ("pc_wei_tang",),
            started_at=CampaignTime.parse("SE-0061-07-04T12:00:00"),
            ended_at=CampaignTime.parse("SE-0061-07-04T21:00:00"),
            active_hours=Decimal("9"),
        )
