import copy
import json
import shutil
from pathlib import Path

import pytest

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.martial_world.contracts import create_contract_owner, transition as contract_transition
from shinobi_runtime.store.repository import RepositoryStore

ROOT = Path(__file__).resolve().parents[2]


def _clear_active_physical_owners(root: Path, refs: set[str]) -> None:
    combats_path = root / "state/martial-world/combats.json"
    combats = json.loads(combats_path.read_text())
    rows = combats.get("combats", {})
    if isinstance(rows, dict):
        for combat_ref, combat in list(rows.items()):
            sides = combat.get("sides", {}) if isinstance(combat, dict) else {}
            members = {str(ref) for side in sides.values() if isinstance(side, list) for ref in side}
            combatants = set(combat.get("combatants", {})) if isinstance(combat, dict) and isinstance(combat.get("combatants"), dict) else set()
            if refs & (members | combatants):
                rows.pop(combat_ref, None)
    combats_path.write_text(json.dumps(combats, ensure_ascii=False, indent=2) + "\n")

    routes_path = root / "state/martial-world/route-operations.json"
    routes = json.loads(routes_path.read_text())
    movements = routes.get("movements", {})
    if isinstance(movements, dict):
        for movement_ref, movement in list(movements.items()):
            participants = set(movement.get("participant_refs", [])) if isinstance(movement, dict) and isinstance(movement.get("participant_refs"), list) else set()
            if isinstance(movement, dict) and isinstance(movement.get("leader_ref"), str):
                participants.add(movement["leader_ref"])
            if refs & participants:
                movements.pop(movement_ref, None)
    routes_path.write_text(json.dumps(routes, ensure_ascii=False, indent=2) + "\n")

    custody_path = root / "state/martial-world/custody.json"
    custody = json.loads(custody_path.read_text())
    records = custody.get("records", [])
    if isinstance(records, list):
        custody["records"] = [row for row in records if not (isinstance(row, dict) and row.get("person_ref") in refs and row.get("status") not in {"released", "escaped", "rescued", "executed"})]
    custody_path.write_text(json.dumps(custody, ensure_ascii=False, indent=2) + "\n")


def _accepted_contract(
    root: Path, *, participant_refs: list[str], approved: bool = False,
    approved_participant_refs: list[str] | None = None,
) -> str:
    contracts_path = root / "state/martial-world/contracts/index.json"
    contracts = json.loads(contracts_path.read_text())
    contract = create_contract_owner(
        contract_type="escort",
        issuer_ref="market:guanzhong",
        beneficiary_ref=None,
        offered_at="0061-09-27T22:58:33",
        expires_at="0061-10-27T22:58:33",
        reward_cash=100,
        funding_cash=100,
        objective={
            "kind": "escort_shipment",
            "route_ref": "route.changan.huashan",
            "source_place_ref": "changan",
            "destination_place_ref": "huashan",
            "item_ref": "food_ration_day",
            "quantity": 1,
            "cargo_value_cash": 1,
            "minimum_escort_count": 2,
        },
        source_ref="test.contract.exact-muster",
    )
    accepted = contract_transition(
        contract,
        at="0061-09-27T22:58:33",
        to_status="accepted",
        actor_ref="pc_wei_tang",
        participants=participant_refs,
    )
    accepted["beneficiary_ref"] = "house_tang"
    contracts.setdefault("active", {})[accepted["contract_id"]] = accepted
    contracts_path.write_text(json.dumps(contracts, ensure_ascii=False, indent=2) + "\n")
    if approved:
        operations_path = root / "state/martial-world/institutional-operations.json"
        operations = json.loads(operations_path.read_text())
        op_ref = f"mission:contract:{accepted['contract_id']}"
        approved_refs = list(approved_participant_refs or participant_refs)
        operations.setdefault("active", {})[op_ref] = {
            "operation_ref": op_ref,
            "faction_ref": "house_tang",
            "mission_source": "public_contract",
            "issuer_ref": "market:guanzhong",
            "assignee_ref": "pc_wei_tang",
            "mission_kind": "escort",
            "objective": "Test exact public escort muster.",
            "linked_contract_ref": accepted["contract_id"],
            "phase": "approved",
            "created_at": "0061-09-27T22:58:33",
            "updated_at": "0061-09-27T22:58:33",
            "participant_refs": approved_refs,
            "commander_ref": "pc_wei_tang",
            "operation_kind": "escort_contract",
            "approved_by_ref": "char.zhu",
            "approved_at": "0061-09-27T22:58:33",
            "reward_cash": 0,
            "reward_mode": "none",
        }
        operations_path.write_text(json.dumps(operations, ensure_ascii=False, indent=2) + "\n")
    return str(accepted["contract_id"])


def _start_command(repo: RepositoryStore, contract_ref: str) -> CommandEnvelope:
    meta = repo.read_json("state/meta.json")
    return CommandEnvelope(
        campaign_id=meta["campaign_id"],
        request_id="contract.exact-muster.start",
        actor_id=meta["player_id"],
        command_type="jianghu_contract_resolution",
        expected_revision=meta["revision"],
        submitted_at="2026-08-29T12:00:00Z",
        payload={"action": "start", "contract_ref": contract_ref},
        mode="gameplay",
    )


def test_contract_start_rejects_escort_members_in_different_sites_of_same_city(tmp_path):
    root = tmp_path / "campaign"
    shutil.copytree(ROOT / "state", root / "state")
    shutil.copytree(ROOT / "game", root / "game")

    roster_path = root / "state/martial-world/people/house_tang.json"
    roster = json.loads(roster_path.read_text())
    second_ref = "mw.person.house_tang.1001"
    approved_refs = ["pc_wei_tang", second_ref, "mw.person.house_tang.1006", "mw.person.house_tang.1017"]
    _clear_active_physical_owners(root, set(approved_refs))
    second = next(row for row in roster["people"] if row.get("person_id") == second_ref)
    assert next(row for row in roster["people"] if row.get("person_id") == "pc_wei_tang")["location_ref"] == "site.changan.inn"
    second["location_ref"] = "site.changan.market"
    roster_path.write_text(json.dumps(roster, ensure_ascii=False, indent=2) + "\n")

    contract_ref = _accepted_contract(
        root, participant_refs=["pc_wei_tang", second_ref], approved=True,
        approved_participant_refs=approved_refs,
    )
    repo = RepositoryStore(root)
    planner = RepositoryCommandPlanner(repo)
    with pytest.raises(CommandRejectedError) as exc:
        planner.preview(_start_command(repo, contract_ref))
    assert exc.value.code == "jianghu_contract_participant_not_colocated"


def test_contract_start_requires_institutional_authorization_before_house_departure(tmp_path):
    root = tmp_path / "campaign"
    shutil.copytree(ROOT / "state", root / "state")
    shutil.copytree(ROOT / "game", root / "game")
    _clear_active_physical_owners(root, {"pc_wei_tang"})
    contract_ref = _accepted_contract(root, participant_refs=["pc_wei_tang"])
    repo = RepositoryStore(root)
    with pytest.raises(CommandRejectedError) as exc:
        RepositoryCommandPlanner(repo).preview(_start_command(repo, contract_ref))
    assert exc.value.code == "jianghu_contract_requires_institutional_authorization"


def test_contract_accept_cannot_name_uncontrolled_house_member_as_principal(tmp_path):
    root = tmp_path / "campaign"
    shutil.copytree(ROOT / "state", root / "state")
    shutil.copytree(ROOT / "game", root / "game")
    _clear_active_physical_owners(root, {"pc_wei_tang", "char.zhu"})

    contracts_path = root / "state/martial-world/contracts/index.json"
    contracts = json.loads(contracts_path.read_text())
    contract = create_contract_owner(
        contract_type="escort", issuer_ref="market:guanzhong", beneficiary_ref=None,
        offered_at="0061-09-27T22:58:33", expires_at="0061-10-27T22:58:33",
        reward_cash=100, funding_cash=100,
        objective={
            "kind": "escort_shipment", "route_ref": "route.changan.huashan",
            "source_place_ref": "changan", "destination_place_ref": "huashan",
            "item_ref": "food_ration_day", "quantity": 1, "cargo_value_cash": 1,
            "minimum_escort_count": 2,
        },
        source_ref="test.contract.principal-authority",
    )
    contracts.setdefault("active", {})[contract["contract_id"]] = contract
    contracts_path.write_text(json.dumps(contracts, ensure_ascii=False, indent=2) + "\n")

    repo = RepositoryStore(root)
    meta = repo.read_json("state/meta.json")
    command = CommandEnvelope(
        campaign_id=meta["campaign_id"], request_id="contract.accept.uncontrolled-principal",
        actor_id=meta["player_id"], command_type="jianghu_contract_resolution",
        expected_revision=meta["revision"], submitted_at="2026-08-29T12:00:00Z",
        payload={
            "action": "accept", "contract_ref": contract["contract_id"],
            "participant_refs": ["pc_wei_tang", "char.zhu"],
        },
        mode="gameplay",
    )
    with pytest.raises(CommandRejectedError) as exc:
        RepositoryCommandPlanner(repo).preview(command)
    assert exc.value.code == "jianghu_contract_participant_not_under_player_authority"
