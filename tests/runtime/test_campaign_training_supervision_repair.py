import pytest

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.campaign_repair_training_supervision import (
    _NEW_ORDER_1,
    _NEW_ORDER_3,
    _OLD_ORDER_1,
    _OLD_ORDER_2,
    _OLD_ORDER_3,
    repair_training_supervision_records,
)
from shinobi_runtime.sim.events import CampaignTime


def _records():
    black_hound = {
        "schema": "exact-team",
        "id": "team.blackhound",
        "training": {
            "instructor_refs": ["pc_wei_tang", "canon_hayama_shirakumo"],
            "facility_refs": ["place.sword_manor"],
            "recent_sessions": [{"session_ref": "historic.blackhound"}],
        },
    }
    black_hound_doctrine = {
        "schema": "team-doctrine",
        "id": "team.blackhound.doctrine",
        "effective_from": "SE-0061-02-22T17:11:42",
        "approved_by": "pc_wei_tang",
        "familiarity": {"pc_wei_tang": 8, "canon_hayama_shirakumo": 100},
        "training": {
            "lead_instructors": ["pc_wei_tang", "canon_hayama_shirakumo"],
            "shared_drills": ["team coordination"],
        },
    }
    fujin = {
        "schema": "exact-team",
        "id": "team.konoha.fujin",
        "training": {
            "instructor_refs": ["pc_wei_tang", "char.zhu", "char.linh"],
            "facility_refs": ["place.sword_manor"],
            "recent_sessions": [{"session_ref": "historic.fujin"}],
        },
    }
    fujin_doctrine = {
        "schema": "team-doctrine",
        "id": "team.konoha.fujin.doctrine",
        "effective_from": "SE-0061-02-24T18:36:42",
        "approved_by": "pc_wei_tang",
        "familiarity": {"pc_wei_tang": 100, "char.mei_arakawa": 100},
        "training": {
            "lead_instructors": ["pc_wei_tang", "char.zhu", "char.linh"],
            "shared_drills": ["layered control"],
        },
    }
    player = {
        "owner_id": "pc_wei_tang",
        "goal_state": {
            "current_orders": [_OLD_ORDER_1, _OLD_ORDER_2, _OLD_ORDER_3],
        },
    }
    return black_hound, black_hound_doctrine, fujin, fujin_doctrine, player


def test_training_supervision_repair_preserves_historical_training_and_familiarity():
    records = _records()
    repaired = repair_training_supervision_records(
        *records,
        current_time=CampaignTime.parse("SE-0061-06-20T07:00:00"),
    )

    black_hound = repaired["state/team/blackhound.json"]
    black_hound_doctrine = repaired["state/team/doctrine/black-hound.json"]
    fujin = repaired["state/team/fujin.json"]
    fujin_doctrine = repaired["state/team/doctrine/team-konoha-fujin.json"]
    player = repaired["state/player.json"]

    assert black_hound["training"]["instructor_refs"] == ["char.zhu", "char.linh"]
    assert fujin["training"]["instructor_refs"] == ["char.zhu", "char.linh"]
    assert black_hound["training"]["facility_refs"] == ["place.sword_manor"]
    assert fujin["training"]["facility_refs"] == ["place.sword_manor"]
    assert black_hound["training"]["recent_sessions"] == [{"session_ref": "historic.blackhound"}]
    assert fujin["training"]["recent_sessions"] == [{"session_ref": "historic.fujin"}]
    assert black_hound_doctrine["training"]["lead_instructors"] == ["char.zhu", "char.linh"]
    assert fujin_doctrine["training"]["lead_instructors"] == ["char.zhu", "char.linh"]
    assert black_hound_doctrine["familiarity"] == {"pc_wei_tang": 8, "canon_hayama_shirakumo": 100}
    assert fujin_doctrine["familiarity"] == {"pc_wei_tang": 100, "char.mei_arakawa": 100}
    assert black_hound_doctrine["effective_from"] == "SE-0061-06-20T07:00:00"
    assert fujin_doctrine["effective_from"] == "SE-0061-06-20T07:00:00"
    assert player["goal_state"]["current_orders"] == [_NEW_ORDER_1, _OLD_ORDER_2, _NEW_ORDER_3]


def test_training_supervision_repair_fails_closed_if_black_hound_policy_already_changed():
    records = list(_records())
    records[0]["training"]["instructor_refs"] = ["char.zhu", "char.linh"]

    with pytest.raises(CommandRejectedError, match="campaign_repair_black_hound_training_changed"):
        repair_training_supervision_records(
            *records,
            current_time=CampaignTime.parse("SE-0061-06-20T07:00:00"),
        )
