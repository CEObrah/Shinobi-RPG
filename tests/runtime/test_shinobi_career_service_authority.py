from __future__ import annotations

import pytest

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.shinobi_career_service_authority import (
    assert_home_village_rank_authority,
)


def _pipeline() -> dict:
    return {
        "schema": "shinobi-career-pipeline",
        "version": 1,
        "villages": {
            "konoha": {"rank_counts": {"genin": 10, "chunin": 5, "jonin": 2}},
            "suna": {"rank_counts": {"genin": 8, "chunin": 4, "jonin": 2}},
        },
        "history": [],
    }


def _person(village: str) -> dict:
    return {
        "schema": "shinobi_character",
        "village_or_affiliation": village,
        "official_rank_or_status": "genin",
        "career_state": {"promotion_eligible": True},
    }


def test_home_village_rank_authority_accepts_matching_scoped_owner() -> None:
    assert assert_home_village_rank_authority(
        _person("Suna"),
        institution_ref="faction_suna",
        pipeline=_pipeline(),
    ) == "suna"
    assert assert_home_village_rank_authority(
        _person("Konoha"),
        institution_ref="institution.konoha.academy",
        pipeline=_pipeline(),
    ) == "konoha"


def test_host_village_cannot_promote_foreign_shinobi() -> None:
    with pytest.raises(CommandRejectedError, match="career_service_authority_mismatch"):
        assert_home_village_rank_authority(
            _person("Suna"),
            institution_ref="institution.konoha.academy",
            pipeline=_pipeline(),
        )


def test_unscoped_rank_authority_is_rejected() -> None:
    with pytest.raises(CommandRejectedError, match="career_service_authority_unresolved"):
        assert_home_village_rank_authority(
            _person("Suna"),
            institution_ref="house_tang",
            pipeline=_pipeline(),
        )
