from __future__ import annotations

import pytest

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands import academy_pipeline_transfer_ids as compat
from shinobi_runtime.commands.domains.autonomy import AutonomyCommandsMixin
from shinobi_runtime.commands.domains.time import TimeCommandsMixin
from shinobi_runtime.commands.paths import POPULATION_REGISTRY_PATH


def test_shared_population_owner_is_inserted_by_identity() -> None:
    shared = {"schema": "population-registry", "pools": {}}
    token = compat._SHARED_POPULATION.set(shared)
    try:
        writes = {}
        compat._bind_shared_population_owner(writes)
        assert writes[POPULATION_REGISTRY_PATH] is shared
    finally:
        compat._SHARED_POPULATION.reset(token)


def test_shared_population_owner_rejects_two_mutable_copies() -> None:
    shared = {"schema": "population-registry", "pools": {}}
    duplicate = {"schema": "population-registry", "pools": {}}
    token = compat._SHARED_POPULATION.set(shared)
    try:
        with pytest.raises(
            CommandRejectedError,
            match="population_owner_transaction_alias_conflict",
        ):
            compat._bind_shared_population_owner(
                {POPULATION_REGISTRY_PATH: duplicate}
            )
    finally:
        compat._SHARED_POPULATION.reset(token)


def test_no_shared_population_owner_leaves_autonomy_writes_untouched() -> None:
    token = compat._SHARED_POPULATION.set(None)
    try:
        writes = {}
        compat._bind_shared_population_owner(writes)
        assert writes == {}
    finally:
        compat._SHARED_POPULATION.reset(token)


def test_campaign_installer_marks_time_and_academy_population_bridges() -> None:
    compat.install_academy_pipeline_transfer_ids()
    assert getattr(
        TimeCommandsMixin._advance_time,
        "_academy_population_owner_bridge",
        False,
    ) is True
    assert getattr(
        TimeCommandsMixin._settle_governed_civil_economies,
        "_academy_population_owner_bridge",
        False,
    ) is True
    assert getattr(
        AutonomyCommandsMixin._apply_institution_autonomy_review,
        "_academy_population_owner_bridge",
        False,
    ) is True
