import copy

from shinobi_runtime.martial_world.institutional_operations import (
    OPERATIONS_PATH,
    ensure_contract_dossier,
)

_DEPLOYMENTS = "state/martial-world/deployments.json"


def _reader(files):
    def read_json(path):
        if path not in files:
            raise FileNotFoundError(path)
        return copy.deepcopy(files[path])
    return read_json


def test_house_escort_accepted_dossier_syncs_active_retinue_members():
    operation_ref = "mission:house:escort-retinue-sync"
    contract_ref = "contract.retinue-sync"
    leader_ref = "pc_wei_tang"
    member_refs = ["char.han", "char.jiang", "char.fu"]
    files = {
        OPERATIONS_PATH: {
            "schema": "jianghu-institutional-operations-state-1.0",
            "active": {
                operation_ref: {
                    "operation_ref": operation_ref,
                    "faction_ref": "house_tang",
                    "mission_source": "house_assignment",
                    "issuer_ref": "char.zhu",
                    "assignee_ref": leader_ref,
                    "mission_kind": "escort",
                    "objective": "Fulfill the House escort assignment.",
                    "linked_contract_ref": contract_ref,
                    "phase": "offered",
                    "created_at": "0061-09-14T09:15:00",
                    "updated_at": "0061-09-14T09:15:00",
                }
            },
            "archive": {},
        },
        _DEPLOYMENTS: {
            "deployments": {
                "retinue.wei": {
                    "operation_kind": "standing_retinue",
                    "leader_ref": leader_ref,
                    "member_refs": member_refs,
                    "status": "active",
                },
                "retinue.inactive": {
                    "operation_kind": "standing_retinue",
                    "leader_ref": leader_ref,
                    "member_refs": ["char.inactive"],
                    "status": "assignment_pending",
                },
            }
        },
    }
    writes = {}

    ref = ensure_contract_dossier(
        read_json=_reader(files),
        writes=writes,
        contract_ref=contract_ref,
        faction_ref="house_tang",
        actor_ref=leader_ref,
        at_iso="0061-09-14T10:00:00",
        phase="accepted",
        participant_refs=[leader_ref],
        objective="Fulfill the House escort assignment.",
        issuer_ref="char.zhu",
    )

    assert ref == operation_ref
    row = writes[OPERATIONS_PATH]["active"][operation_ref]
    assert row["phase"] == "accepted"
    assert row["participant_refs"] == [leader_ref, *member_refs]
    assert "char.inactive" not in row["participant_refs"]


def test_public_contract_accepted_dossier_keeps_explicit_principals_only():
    leader_ref = "pc_wei_tang"
    files = {
        OPERATIONS_PATH: {
            "schema": "jianghu-institutional-operations-state-1.0",
            "active": {},
            "archive": {},
        },
        _DEPLOYMENTS: {
            "deployments": {
                "retinue.wei": {
                    "operation_kind": "standing_retinue",
                    "leader_ref": leader_ref,
                    "member_refs": ["char.han", "char.jiang", "char.fu"],
                    "status": "active",
                }
            }
        },
    }
    writes = {}

    ref = ensure_contract_dossier(
        read_json=_reader(files),
        writes=writes,
        contract_ref="contract.public",
        faction_ref="house_tang",
        actor_ref=leader_ref,
        at_iso="0061-09-14T10:00:00",
        phase="accepted",
        participant_refs=[leader_ref],
    )

    row = writes[OPERATIONS_PATH]["active"][ref]
    assert row["mission_source"] == "public_contract"
    assert row["participant_refs"] == [leader_ref]
