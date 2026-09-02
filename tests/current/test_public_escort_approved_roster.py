import copy

import pytest

from shinobi_runtime.martial_world.contract_escort_rosters import approved_contract_escort_roster
from shinobi_runtime.martial_world.institutional_operations import OPERATIONS_PATH


def _reader(owner):
    def read_json(path):
        if path == OPERATIONS_PATH:
            return copy.deepcopy(owner)
        raise FileNotFoundError(path)
    return read_json


def _owner(*, phase="approved", participants=None, commander="wei", operation_kind="escort_contract"):
    return {
        "schema": "jianghu-institutional-operations-state-1.0",
        "active": {
            "mission:contract:contract.test": {
                "operation_ref": "mission:contract:contract.test",
                "linked_contract_ref": "contract.test",
                "mission_source": "public_contract",
                "mission_kind": "escort",
                "operation_kind": operation_kind,
                "phase": phase,
                "participant_refs": participants or ["wei", "medic", "guard", "scout", "temp.a", "temp.b"],
                "commander_ref": commander,
            }
        },
        "archive": {},
    }


def _exact_roster(phase="approved"):
    return approved_contract_escort_roster(
        _reader(_owner(phase=phase)),
        contract_ref="contract.test",
        accepted_refs=["wei"],
        standing_party_refs=["wei", "medic", "guard", "scout"],
        minimum_escort_count=6,
    )


def test_approved_public_escort_roster_is_exact_and_preserves_commander():
    assert _exact_roster() == {
        "escort_refs": ["wei", "medic", "guard", "scout", "temp.a", "temp.b"],
        "core_escort_refs": ["wei", "medic", "guard", "scout"],
        "temporary_mission_escort_refs": ["temp.a", "temp.b"],
        "commander_ref": "wei",
    }


def test_mustering_public_escort_keeps_same_approved_exact_roster():
    assert _exact_roster("mustering") == _exact_roster("approved")


def test_approved_public_escort_roster_must_keep_contract_principals():
    with pytest.raises(ValueError, match="approved_roster_missing_principal"):
        approved_contract_escort_roster(
            _reader(_owner(participants=["medic", "guard", "scout", "temp.a", "temp.b", "temp.c"], commander="medic")),
            contract_ref="contract.test",
            accepted_refs=["wei"],
            standing_party_refs=["wei", "medic", "guard", "scout"],
            minimum_escort_count=6,
        )


def test_approved_public_escort_roster_cannot_fall_below_contract_minimum():
    with pytest.raises(ValueError, match="approved_roster_below_minimum"):
        approved_contract_escort_roster(
            _reader(_owner(participants=["wei", "medic", "guard", "scout"])),
            contract_ref="contract.test",
            accepted_refs=["wei"],
            standing_party_refs=["wei", "medic", "guard", "scout"],
            minimum_escort_count=6,
        )


def test_unapproved_public_escort_keeps_legacy_autofill_path():
    assert approved_contract_escort_roster(
        _reader(_owner(phase="accepted")),
        contract_ref="contract.test",
        accepted_refs=["wei"],
        standing_party_refs=["wei", "medic", "guard", "scout"],
        minimum_escort_count=6,
    ) is None
