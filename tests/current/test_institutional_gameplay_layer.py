import copy
import json
from datetime import datetime
from pathlib import Path

from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.commands.specs import COMMAND_SPECS
from shinobi_runtime.martial_world.faction_relations import (
    active_treaty_kinds,
    proposal_kind_supported,
    stage_treaty,
    treaty_forbids_hostilities,
)
from shinobi_runtime.martial_world.institutional_operations import (
    OPERATIONS_PATH,
    close_linked_contract_operation,
    ensure_contract_dossier,
    stage_house_assignment_offer,
)
from shinobi_runtime.martial_world.institutional_progression import close_expired_contract_dossiers
from shinobi_runtime.store.repository import RepositoryStore

ROOT = Path(__file__).resolve().parents[2]


def _reader(files):
    def read_json(path):
        if path not in files:
            raise FileNotFoundError(path)
        return copy.deepcopy(files[path])
    return read_json


def test_person_roster_registers_institutional_service_merit_state():
    template = json.loads((ROOT / "runtime/contracts/templates/jianghu-person-lite-roster-1.0.template.json").read_text())
    person_contract = template["object_contracts"]["/people/*"]
    assert "institutional_service" in person_contract["allowed_keys"]
    service_contract = template["object_contracts"]["/people/*/institutional_service"]
    assert service_contract["mode"] == "closed"
    assert set(service_contract["allowed_keys"]) == {
        "completed_missions", "successful_missions", "service_days",
        "commands_completed", "last_review_at",
    }
    types = template["type_contracts"]
    assert types["/people/*/institutional_service"] == ["object"]
    for field in ("completed_missions", "successful_missions", "service_days", "commands_completed"):
        assert types[f"/people/*/institutional_service/{field}"] == ["integer"]
    assert types["/people/*/institutional_service/last_review_at"] == ["string"]


def test_institutional_command_and_owner_are_registered():
    spec = COMMAND_SPECS["jianghu_institutional_operation_resolution"]
    assert set(spec.variants) >= {
        "propose", "accept_assignment", "convene", "submit_plan", "dispatch",
        "request_aid", "decline_assignment", "cancel", "settle_reward",
        "service_review", "accept_career_offer", "decline_career_offer",
    }
    registry = json.loads((ROOT / "game/schemas/registry.json").read_text())
    assert "jianghu-institutional-operations-state-1.0" in json.dumps(registry)
    owner = json.loads((ROOT / OPERATIONS_PATH).read_text())
    assert owner["schema"] == "jianghu-institutional-operations-state-1.0"


def test_current_accepted_house_escort_has_migrated_dossier():
    contracts = json.loads((ROOT / "state/martial-world/contracts/index.json").read_text())
    owner = json.loads((ROOT / OPERATIONS_PATH).read_text())
    accepted = [
        ref for ref, row in contracts.get("active", {}).items()
        if row.get("status") == "accepted" and row.get("beneficiary_ref") == "house_tang"
    ]
    assert accepted
    for contract_ref in accepted:
        matches = [
            row for row in owner.get("active", {}).values()
            if isinstance(row, dict) and row.get("linked_contract_ref") == contract_ref
        ]
        assert len(matches) == 1
        assert matches[0]["phase"] in {"accepted", "mustering", "in_transit", "returning"}


def test_contract_dossier_closes_compactly_without_hidden_enemy_truth():
    files = {OPERATIONS_PATH: {"schema": "jianghu-institutional-operations-state-1.0", "active": {}, "archive": {}}}
    writes = {}
    ref = ensure_contract_dossier(
        read_json=_reader(files), writes=writes, contract_ref="contract.test", faction_ref="house_tang",
        actor_ref="pc_wei_tang", at_iso="0061-09-14T09:15:00", phase="accepted",
        participant_refs=["pc_wei_tang", "char.test"], commander_ref="pc_wei_tang",
    )
    closed = close_linked_contract_operation(
        read_json=_reader(files), writes=writes, contract_ref="contract.test",
        at_iso="0061-09-16T09:15:00", success=True, closure_reason="delivered",
        returned_refs=["pc_wei_tang", "char.test"],
        extra_report={"contract_revenue_cash": 500, "hidden_enemy_reaction": {"alert": 999}},
    )
    assert ref == "mission:contract:contract.test"
    assert closed["after_action_report"]["contract_revenue_cash"] == 500
    assert "hidden_enemy_reaction" not in closed["after_action_report"]
    assert ref not in writes[OPERATIONS_PATH]["active"]
    assert ref in writes[OPERATIONS_PATH]["archive"]


def test_contract_expiry_closes_linked_house_assignment_once():
    files = {OPERATIONS_PATH: {"schema": "jianghu-institutional-operations-state-1.0", "active": {}, "archive": {}}}
    writes = {}
    assert stage_house_assignment_offer(
        read_json=_reader(files), writes=writes, operation_ref="mission:house:test", faction_ref="house_tang",
        issuer_ref="char.zhu", assignee_ref="pc_wei_tang", mission_kind="escort",
        objective="Take the commission.", at_iso="0061-09-14T09:15:00",
        linked_contract_ref="contract.test", trigger_ref="contract.test",
    )
    handoffs = []
    closed = close_expired_contract_dossiers(
        read_json=_reader(files), writes=writes, handoffs=handoffs,
        reviews=[{"kind": "contract_expiry", "contract_refs": ["contract.test"]}],
        at=datetime.fromisoformat("0061-10-14T00:00:00"),
    )
    assert closed == ["mission:house:test"]
    assert writes[OPERATIONS_PATH]["archive"]["mission:house:test"]["outcome"] == "contract_expired"
    assert close_expired_contract_dossiers(
        read_json=_reader(files), writes=writes, handoffs=handoffs,
        reviews=[{"kind": "contract_expiry", "contract_refs": ["contract.test"]}],
        at=datetime.fromisoformat("0061-10-14T00:00:00"),
    ) == []


def test_treaties_have_real_behavior_and_no_combat_bonus_contract():
    state = {"edges": []}
    state = stage_treaty(state, a="house_a", b="house_b", kind="mutual_defense", at_iso="0061-09-14T09:15:00")
    assert "mutual_defense" in active_treaty_kinds(state, "house_a", "house_b")
    assert not treaty_forbids_hostilities(state, "house_a", "house_b")
    state = stage_treaty(state, a="house_a", b="house_b", kind="truce", at_iso="0061-09-14T09:15:00")
    assert treaty_forbids_hostilities(state, "house_a", "house_b")
    assert "combat_bonus" not in json.dumps(state)
    for kind in ("silver_exchange", "restitution", "tribute", "prisoner_exchange", "non_aggression", "mutual_defense", "alliance", "truce"):
        assert proposal_kind_supported(kind)


def test_real_campaign_player_can_propose_institutional_mission_read_only(tmp_path):
    import shutil
    root = tmp_path / "campaign"
    shutil.copytree(ROOT / "state", root / "state")
    shutil.copytree(ROOT / "game", root / "game")
    _prepare_institutional_fixture(root)
    repository = RepositoryStore(root)
    planner = RepositoryCommandPlanner(repository)
    meta = repository.read_json("state/meta.json")
    before = repository.read_bytes(OPERATIONS_PATH)
    command = CommandEnvelope(
        campaign_id=meta["campaign_id"], request_id="request.institutional-propose-preview",
        actor_id=meta["player_id"], command_type="jianghu_institutional_operation_resolution",
        expected_revision=meta["revision"], submitted_at="2026-08-26T00:00:00Z",
        payload={
            "action": "propose", "operation_ref": "mission:test:preview",
            "mission_kind": "reconnaissance", "objective": "Reconnoiter a hostile faction and report.",
            "target_faction_ref": "shaolin", "target_site_ref": "", "target_person_ref": "",
            "linked_contract_ref": "", "reward_cash": 0, "reward_mode": "none",
        },
    )
    preview = planner.preview(command)
    assert preview.status == "ready"
    assert repository.read_bytes(OPERATIONS_PATH) == before


def _write_plan(root, plan):
    for rel, payload in plan.writes.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, bytes):
            path.write_bytes(payload)
        else:
            path.write_text(str(payload), encoding="utf-8")


def _prepare_institutional_fixture(root, player_ref="pc_wei_tang"):
    """Detach synthetic council workflows from the mutable live ambush/travel state."""
    path = root / "state/martial-world/route-operations.json"
    state = json.loads(path.read_text())
    movements = state.get("movements", {})
    if isinstance(movements, dict):
        state["movements"] = {
            ref: row for ref, row in movements.items()
            if not (
                isinstance(row, dict)
                and player_ref in [
                    str(member_ref) for member_ref in row.get("participant_refs", [])
                    if isinstance(member_ref, str)
                ]
            )
        }
    path.write_text(json.dumps(state), encoding="utf-8")

    combats_path = root / "state/martial-world/combats.json"
    combats = json.loads(combats_path.read_text())
    for row in combats.get("combats", {}).values():
        if not isinstance(row, dict) or row.get("status") != "active":
            continue
        sides = row.get("sides", {}) if isinstance(row.get("sides"), dict) else {}
        members = {
            str(ref) for refs in sides.values() if isinstance(refs, list)
            for ref in refs if isinstance(ref, str)
        }
        if player_ref in members:
            row["status"] = "resolved"
    combats_path.write_text(json.dumps(combats), encoding="utf-8")

    roster_path = root / "state/martial-world/people/house_tang.json"
    roster = json.loads(roster_path.read_text())
    for person in roster.get("people", []):
        if isinstance(person, dict) and person.get("person_id") == player_ref:
            person["location_ref"] = "site.house_tang"
            break
    roster_path.write_text(json.dumps(roster), encoding="utf-8")

    scene_path = root / "state/scene.json"
    scene = json.loads(scene_path.read_text())
    scene.pop("active_combat_ref", None)
    scene["location_id"] = "site.house_tang"
    scene["present_person_ids"] = [player_ref, "char.zhu", "char.ling"]
    scene["visible_person_ids"] = [player_ref, "char.zhu", "char.ling"]
    scene_path.write_text(json.dumps(scene), encoding="utf-8")


def test_house_assignment_offer_is_a_protected_player_decision():
    from shinobi_runtime.martial_world.institutional_progression import (
        settle_house_assignment_offer_deliveries, stage_house_assignment_offers,
    )

    repository = RepositoryStore(ROOT)
    writes = {
        OPERATIONS_PATH: {
            "schema": "jianghu-institutional-operations-state-1.0",
            "active": {},
            "archive": {},
        }
    }
    handoffs = []
    pending = []
    added = stage_house_assignment_offers(
        read_json=repository.read_json,
        writes=writes,
        handoffs=handoffs,
        pending_one_off_events=pending,
        reviews=[{
            "kind": "faction_review",
            "faction_ref": "house_tang",
            "executed_actions": [{
                "action": "respond_known_captivity",
                "result": "player_decision_required",
                "person_ref": "mw.person.house_tang.1000",
                "holder_faction_ref": "shaolin",
                "custody_ref": "custody:test:house-assignment",
            }],
        }],
        at=datetime.fromisoformat("0061-09-14T12:00:00"),
    )
    assert len(added) == 1
    row = writes[OPERATIONS_PATH]["active"][added[0]]
    assert row["mission_source"] == "house_assignment"
    assert row["phase"] == "offered"
    assert row["assignee_ref"] == "pc_wei_tang"
    assert row["issuer_ref"] != "pc_wei_tang"
    # Wei is traveling in the current save. Institutional creation of the offer
    # is not player receipt: a physical delivery obligation exists first.
    assert handoffs == []
    assert row["offer_delivery_status"] == "in_transit"
    assert len(pending) == 1
    assert pending[0]["kind"] == "house_assignment_offer_delivery"
    assert pending[0]["requires_player_decision"] is False

    # Disposable overlay: by courier arrival Wei has reached the advertised
    # Huashan destination. Only then does the protected player decision surface.
    route_state = repository.read_json("state/martial-world/route-operations.json")
    route_state = copy.deepcopy(route_state)
    movements = route_state.get("movements", {})
    if isinstance(movements, dict):
        route_state["movements"] = {
            ref: movement for ref, movement in movements.items()
            if not (isinstance(movement, dict) and "pc_wei_tang" in movement.get("participant_refs", []))
        }
    roster = copy.deepcopy(repository.read_json("state/martial-world/people/house_tang.json"))
    for person in roster.get("people", []):
        if isinstance(person, dict) and person.get("person_id") == "pc_wei_tang":
            person["location_ref"] = "site.huashan.inn"
    writes["state/martial-world/route-operations.json"] = route_state
    writes["state/martial-world/people/house_tang.json"] = roster
    delivered_handoffs = []
    replacements = []
    delivered = settle_house_assignment_offer_deliveries(
        read_json=repository.read_json, writes=writes, handoffs=delivered_handoffs,
        events=pending, at=datetime.fromisoformat(pending[0]["due_at"]),
        pending_one_off_events=replacements,
    )
    assert delivered == added
    assert replacements == []
    assert delivered_handoffs and delivered_handoffs[0]["requires_player_decision"] is True
    assert writes[OPERATIONS_PATH]["active"][added[0]]["offer_delivery_status"] == "delivered"


def test_house_escort_reuses_one_linked_dossier_instead_of_duplicating():
    files = {OPERATIONS_PATH: {"schema": "jianghu-institutional-operations-state-1.0", "active": {}, "archive": {}}}
    writes = {}
    op_ref = "mission:house:escort-dedup"
    assert stage_house_assignment_offer(
        read_json=_reader(files), writes=writes, operation_ref=op_ref, faction_ref="house_tang",
        issuer_ref="char.zhu", assignee_ref="pc_wei_tang", mission_kind="escort",
        objective="Take the funded escort commission.", at_iso="0061-09-14T09:15:00",
        linked_contract_ref="contract.dedup", trigger_ref="contract.dedup",
    )

    class Overlay:
        def read_json(self, path):
            if path in writes:
                return copy.deepcopy(writes[path])
            return _reader(files)(path)

    ensure_contract_dossier(
        read_json=Overlay().read_json, writes=writes, contract_ref="contract.dedup",
        faction_ref="house_tang", actor_ref="pc_wei_tang", at_iso="0061-09-14T10:00:00",
        phase="accepted", participant_refs=["pc_wei_tang"], issuer_ref="char.zhu",
    )
    linked = [
        (ref, row) for ref, row in writes[OPERATIONS_PATH]["active"].items()
        if row.get("linked_contract_ref") == "contract.dedup"
    ]
    assert len(linked) == 1
    assert linked[0][0] == op_ref
    assert linked[0][1]["mission_source"] == "house_assignment"
    assert linked[0][1]["phase"] == "accepted"


def test_reward_and_service_settlement_are_idempotent():
    from shinobi_runtime.martial_world.institutional_progression import settle_closed_mission_records
    from shinobi_runtime.martial_world.live_state import roster_person
    from shinobi_runtime.martial_world.faction_state import read_faction

    repository = RepositoryStore(ROOT)
    player_path, _roster, _ordinal, player_before = roster_person(repository, "pc_wei_tang")
    faction_path, faction_before = read_faction(repository, "house_tang")
    op_ref = "mission:test:idempotent-settlement"
    writes = {
        OPERATIONS_PATH: {
            "schema": "jianghu-institutional-operations-state-1.0",
            "active": {},
            "archive": {
                op_ref: {
                    "operation_ref": op_ref,
                    "faction_ref": "house_tang",
                    "mission_source": "house_assignment",
                    "mission_kind": "reconnaissance",
                    "commander_ref": "pc_wei_tang",
                    "phase": "closed",
                    "outcome": "success",
                    "after_action_report": {"success": True, "returned_refs": ["pc_wei_tang"]},
                    "reward_settlement": {"authorized_cash": 100, "mode": "commander", "status": "pending"},
                    "service_credit": {"credited_refs": ["pc_wei_tang"], "success": True, "service_days": 2, "reviewed": False},
                }
            },
        }
    }
    handoffs = []
    first = settle_closed_mission_records(
        read_json=repository.read_json, writes=writes, handoffs=handoffs,
        at=datetime.fromisoformat("0061-09-16T09:15:00"),
    )
    assert first and first[0]["paid_cash"] == 100

    def person_from_written_roster():
        owner = writes[player_path]
        return next(row for row in owner["people"] if row.get("person_id") == "pc_wei_tang")

    player_after_first = person_from_written_roster()
    faction_after_first = writes[faction_path]
    assert int(player_after_first.get("personal_cash", 0)) == int(player_before.get("personal_cash", 0)) + 100
    assert int(faction_after_first.get("treasury_cash", 0)) == int(faction_before.get("treasury_cash", 0)) - 100
    service_after_first = copy.deepcopy(player_after_first.get("institutional_service", {}))

    second = settle_closed_mission_records(
        read_json=repository.read_json, writes=writes, handoffs=handoffs,
        at=datetime.fromisoformat("0061-09-17T09:15:00"),
    )
    player_after_second = person_from_written_roster()
    faction_after_second = writes[faction_path]
    assert not any(row.get("paid_cash") for row in second)
    assert int(player_after_second.get("personal_cash", 0)) == int(player_after_first.get("personal_cash", 0))
    assert int(faction_after_second.get("treasury_cash", 0)) == int(faction_after_first.get("treasury_cash", 0))
    assert player_after_second.get("institutional_service", {}) == service_after_first
    archived = writes[OPERATIONS_PATH]["archive"][op_ref]
    assert archived["reward_settlement"]["status"] == "settled"
    assert archived["service_credit"]["reviewed"] is True


def test_council_can_delegate_exact_diplomacy_terms_to_wei(tmp_path):
    import shutil

    root = tmp_path / "campaign"
    shutil.copytree(ROOT / "state", root / "state")
    shutil.copytree(ROOT / "game", root / "game")
    _prepare_institutional_fixture(root)

    def command(command_type, payload, request_id):
        repo = RepositoryStore(root)
        meta = repo.read_json("state/meta.json")
        return repo, RepositoryCommandPlanner(repo), CommandEnvelope(
            campaign_id=meta["campaign_id"], request_id=request_id, actor_id=meta["player_id"],
            command_type=command_type, expected_revision=meta["revision"], submitted_at="2026-08-26T00:00:00Z",
            payload=payload, mode="gameplay",
        )

    op_ref = "mission:test:delegated-diplomacy"
    repo, planner, cmd = command("jianghu_institutional_operation_resolution", {
        "action": "propose", "operation_ref": op_ref, "mission_kind": "diplomacy",
        "objective": "Seek a formal non-aggression pact with Shaolin.", "target_faction_ref": "shaolin",
        "target_site_ref": "", "target_person_ref": "", "linked_contract_ref": "",
        "reward_cash": 0, "reward_mode": "none",
    }, "institutional.diplomacy.propose")
    assert planner.preview(cmd).status == "ready"
    _write_plan(root, planner._build(cmd))

    repo, planner, cmd = command("jianghu_institutional_operation_resolution", {
        "action": "convene", "operation_ref": op_ref,
        "attendee_refs": ["pc_wei_tang", "char.zhu", "char.ling"],
    }, "institutional.diplomacy.council")
    assert planner.preview(cmd).status == "ready"
    _write_plan(root, planner._build(cmd))

    repo, planner, cmd = command("jianghu_institutional_operation_resolution", {
        "action": "submit_plan", "operation_ref": op_ref, "commander_ref": "pc_wei_tang",
        "member_refs": ["pc_wei_tang"], "operation_kind": "diplomatic_mission", "doctrine": "formal_envoy",
        "proposal_kind": "non_aggression", "value_cash": 0, "cost_cash": 0,
    }, "institutional.diplomacy.authorize")
    assert planner.preview(cmd).status == "ready"
    built = planner._build(cmd)
    approved = json.loads(built.writes[OPERATIONS_PATH])["active"][op_ref]
    assert approved["approved_by_ref"] in {"char.zhu", "char.ling"}
    assert approved["diplomacy_authorization"]["proposal_kind"] == "non_aggression"
    _write_plan(root, built)

    repo, planner, cmd = command("jianghu_diplomacy_resolution", {
        "target_faction_ref": "shaolin", "proposal_kind": "non_aggression",
        "value_cash": 0, "cost_cash": 0, "institutional_operation_ref": op_ref,
    }, "institutional.diplomacy.execute")
    assert planner.preview(cmd).status == "ready"
    built = planner._build(cmd)
    mission_after = json.loads(built.writes[OPERATIONS_PATH])
    assert op_ref not in mission_after["active"]
    assert op_ref in mission_after["archive"]
    report = mission_after["archive"][op_ref]["after_action_report"]
    assert report["diplomacy_target_faction_ref"] == "shaolin"
    assert report["diplomacy_proposal_kind"] == "non_aggression"
    assert report["diplomacy_decision"] in {"accepted", "counteroffer", "rejected"}


def test_player_can_dispatch_delegated_recon_without_joining_it(tmp_path):
    import shutil

    root = tmp_path / "campaign"
    shutil.copytree(ROOT / "state", root / "state")
    shutil.copytree(ROOT / "game", root / "game")
    _prepare_institutional_fixture(root)

    def issue(payload, request_id):
        repo = RepositoryStore(root)
        meta = repo.read_json("state/meta.json")
        cmd = CommandEnvelope(
            campaign_id=meta["campaign_id"], request_id=request_id, actor_id=meta["player_id"],
            command_type="jianghu_institutional_operation_resolution", expected_revision=meta["revision"],
            submitted_at="2026-08-26T00:00:00Z", payload=payload, mode="gameplay",
        )
        planner = RepositoryCommandPlanner(repo)
        assert planner.preview(cmd).status == "ready"
        built = planner._build(cmd)
        _write_plan(root, built)
        return built

    op_ref = "mission:test:delegated-recon"
    issue({
        "action": "propose", "operation_ref": op_ref, "mission_kind": "reconnaissance",
        "objective": "Reconnoiter Shaolin and return with an operational report.", "target_faction_ref": "shaolin",
        "target_site_ref": "", "target_person_ref": "", "linked_contract_ref": "",
        "reward_cash": 0, "reward_mode": "none",
    }, "institutional.recon.propose")
    issue({
        "action": "convene", "operation_ref": op_ref,
        "attendee_refs": ["pc_wei_tang", "char.zhu", "char.ling"],
    }, "institutional.recon.council")
    commander = "mw.person.house_tang.1000"
    issue({
        "action": "submit_plan", "operation_ref": op_ref, "commander_ref": commander,
        "member_refs": [commander], "operation_kind": "faction_reconnaissance", "doctrine": "observe_and_return",
    }, "institutional.recon.plan")
    built = issue({"action": "dispatch", "operation_ref": op_ref}, "institutional.recon.dispatch")
    deployments = json.loads(built.writes["state/martial-world/deployments.json"])["deployments"]
    physical = deployments[f"operation:institutional:{op_ref}"]
    assert physical["commander_ref"] == commander
    assert physical["participant_refs"] == [commander]
    assert "pc_wei_tang" not in physical["participant_refs"]
    assert physical["institutional_operation_ref"] == op_ref
    schedule = json.loads(built.writes["state/martial-world/scheduler.json"])
    assert any(row.get("owner_ref") == f"operation:institutional:{op_ref}" for row in schedule.get("one_off", {}).values())


def test_delegated_prisoner_exchange_releases_only_exact_authorized_captives(tmp_path):
    import shutil

    root = tmp_path / "campaign"
    shutil.copytree(ROOT / "state", root / "state")
    shutil.copytree(ROOT / "game", root / "game")
    _prepare_institutional_fixture(root)
    house_captive = "mw.person.house_tang.1000"
    shaolin_captive = "mw.person.shaolin.0001"

    custody_path = root / "state/martial-world/custody.json"
    custody = json.loads(custody_path.read_text())
    custody["records"] = [
        {"custody_id": "custody:test:house", "person_ref": house_captive, "holder_faction_ref": "shaolin", "status": "captive", "location_ref": "shaolin"},
        {"custody_id": "custody:test:shaolin", "person_ref": shaolin_captive, "holder_faction_ref": "house_tang", "status": "captive", "location_ref": "luoyang"},
    ]
    custody_path.write_text(json.dumps(custody), encoding="utf-8")

    relations_path = root / "state/martial-world/faction-relations.json"
    relations = json.loads(relations_path.read_text())
    edges = relations.setdefault("edges", [])
    edge = next((row for row in edges if row.get("from_faction") == "house_tang" and row.get("to_faction") == "shaolin"), None)
    if edge is None:
        edge = {"from_faction": "house_tang", "to_faction": "shaolin"}
        edges.append(edge)
    edge.update({"trust": 100, "respect": 100, "hostility": 0, "obligation": 0})
    relations_path.write_text(json.dumps(relations), encoding="utf-8")

    def execute(command_type, payload, request_id):
        repo = RepositoryStore(root)
        meta = repo.read_json("state/meta.json")
        cmd = CommandEnvelope(
            campaign_id=meta["campaign_id"], request_id=request_id, actor_id=meta["player_id"],
            command_type=command_type, expected_revision=meta["revision"], submitted_at="2026-08-26T00:00:00Z",
            payload=payload, mode="gameplay",
        )
        planner = RepositoryCommandPlanner(repo)
        assert planner.preview(cmd).status == "ready"
        built = planner._build(cmd)
        _write_plan(root, built)
        return built

    op_ref = "mission:test:prisoner-exchange"
    execute("jianghu_institutional_operation_resolution", {
        "action": "propose", "operation_ref": op_ref, "mission_kind": "diplomacy",
        "objective": "Negotiate an exact prisoner exchange with Shaolin.", "target_faction_ref": "shaolin",
        "target_site_ref": "", "target_person_ref": "", "linked_contract_ref": "",
        "reward_cash": 0, "reward_mode": "none",
    }, "institutional.exchange.propose")
    execute("jianghu_institutional_operation_resolution", {
        "action": "convene", "operation_ref": op_ref,
        "attendee_refs": ["pc_wei_tang", "char.zhu", "char.ling"],
    }, "institutional.exchange.council")
    execute("jianghu_institutional_operation_resolution", {
        "action": "submit_plan", "operation_ref": op_ref, "commander_ref": "pc_wei_tang",
        "member_refs": ["pc_wei_tang"], "operation_kind": "diplomatic_mission", "doctrine": "formal_exchange",
        "proposal_kind": "prisoner_exchange", "value_cash": 0, "cost_cash": 0,
        "source_captive_refs": [shaolin_captive], "target_captive_refs": [house_captive],
    }, "institutional.exchange.authorize")
    built = execute("jianghu_diplomacy_resolution", {
        "target_faction_ref": "shaolin", "proposal_kind": "prisoner_exchange",
        "value_cash": 0, "cost_cash": 0,
        "source_captive_refs": [shaolin_captive], "target_captive_refs": [house_captive],
        "institutional_operation_ref": op_ref,
    }, "institutional.exchange.execute")
    proposal = built.result["proposal"]
    assert proposal["decision"] == "accepted"
    custody_after = json.loads(built.writes["state/martial-world/custody.json"])
    assert not any(row.get("person_ref") in {house_captive, shaolin_captive} for row in custody_after.get("records", []))
    mission_after = json.loads(built.writes[OPERATIONS_PATH])
    assert mission_after["archive"][op_ref]["after_action_report"]["diplomacy_decision"] == "accepted"

def test_mission_plan_rejects_members_in_different_sites_of_same_city(tmp_path):
    import shutil
    import pytest
    from shinobi_runtime.api.contracts import CommandRejectedError

    root = tmp_path / "campaign"
    shutil.copytree(ROOT / "state", root / "state")
    shutil.copytree(ROOT / "game", root / "game")
    _prepare_institutional_fixture(root)

    def command(payload, request_id):
        repo = RepositoryStore(root)
        meta = repo.read_json("state/meta.json")
        cmd = CommandEnvelope(
            campaign_id=meta["campaign_id"], request_id=request_id, actor_id=meta["player_id"],
            command_type="jianghu_institutional_operation_resolution", expected_revision=meta["revision"],
            submitted_at="2026-08-26T00:00:00Z", payload=payload, mode="gameplay",
        )
        return repo, RepositoryCommandPlanner(repo), cmd

    op_ref = "mission:test:split-site-roster"
    for payload, request_id in [
        ({
            "action": "propose", "operation_ref": op_ref, "mission_kind": "reconnaissance",
            "objective": "Reconnoiter Shaolin and return.", "target_faction_ref": "shaolin",
            "target_site_ref": "", "target_person_ref": "", "linked_contract_ref": "",
            "reward_cash": 0, "reward_mode": "none",
        }, "institutional.split.propose"),
        ({
            "action": "convene", "operation_ref": op_ref,
            "attendee_refs": ["pc_wei_tang", "char.zhu", "char.ling"],
        }, "institutional.split.council"),
    ]:
        _repo, planner, cmd = command(payload, request_id)
        assert planner.preview(cmd).status == "ready"
        _write_plan(root, planner._build(cmd))

    roster_path = root / "state/martial-world/people/house_tang.json"
    roster = json.loads(roster_path.read_text())
    commander_ref = "mw.person.house_tang.1000"
    for person in roster.get("people", []):
        if not isinstance(person, dict):
            continue
        if person.get("person_id") == "pc_wei_tang":
            person["location_ref"] = "site.house_tang"
        elif person.get("person_id") == commander_ref:
            # Both sites have luoyang as their parent place.  This is deliberately
            # not exact co-presence and must not be enough to launch one party.
            person["location_ref"] = "site.luoyang.inn"
    roster_path.write_text(json.dumps(roster), encoding="utf-8")

    _repo, planner, cmd = command({
        "action": "submit_plan", "operation_ref": op_ref, "commander_ref": commander_ref,
        "member_refs": ["pc_wei_tang", commander_ref],
        "operation_kind": "faction_reconnaissance", "doctrine": "observe_and_return",
    }, "institutional.split.plan")
    with pytest.raises(CommandRejectedError) as exc:
        planner.preview(cmd)
    assert exc.value.code == "jianghu_institutional_members_not_colocated"


def test_house_career_offer_is_not_player_knowledge_until_physical_delivery():
    from shinobi_runtime.martial_world.institutional_progression import (
        _View, _queue_career_offer_delivery, settle_house_career_offer_deliveries,
    )

    repository = RepositoryStore(ROOT)
    operation_ref = "mission:test:career-delivery"
    row = {
        "operation_ref": operation_ref,
        "faction_ref": "house_tang",
        "phase": "closed",
        "career_offer": {
            "kind": "membership_grade",
            "candidate_ref": "pc_wei_tang",
            "from_grade": "elite",
            "to_grade": "elder",
            "status": "offered",
            "offered_at": "0061-09-14T12:00:00",
            "authorized_by_ref": "char.zhu",
            "stat_changes": {},
        },
    }
    writes = {
        OPERATIONS_PATH: {
            "schema": "jianghu-institutional-operations-state-1.0",
            "active": {},
            "archive": {operation_ref: row},
        }
    }
    pending = []
    view = _View(repository.read_json, writes)
    delivered_now = _queue_career_offer_delivery(
        view=view, row=row, offer=row["career_offer"], player_ref="pc_wei_tang",
        faction_ref="house_tang", at=datetime.fromisoformat("0061-09-14T12:00:00"),
        pending_one_off_events=pending,
    )
    assert delivered_now is False
    assert row["career_offer"]["delivery_status"] == "in_transit"
    assert len(pending) == 1 and pending[0]["kind"] == "house_career_offer_delivery"

    route_state = copy.deepcopy(repository.read_json("state/martial-world/route-operations.json"))
    movements = route_state.get("movements", {})
    if isinstance(movements, dict):
        route_state["movements"] = {
            ref: movement for ref, movement in movements.items()
            if not (isinstance(movement, dict) and "pc_wei_tang" in movement.get("participant_refs", []))
        }
    roster = copy.deepcopy(repository.read_json("state/martial-world/people/house_tang.json"))
    for person in roster.get("people", []):
        if isinstance(person, dict) and person.get("person_id") == "pc_wei_tang":
            person["location_ref"] = "site.huashan.inn"
    writes["state/martial-world/route-operations.json"] = route_state
    writes["state/martial-world/people/house_tang.json"] = roster
    handoffs = []
    replacements = []
    delivered = settle_house_career_offer_deliveries(
        read_json=repository.read_json, writes=writes, handoffs=handoffs,
        events=pending, at=datetime.fromisoformat(pending[0]["due_at"]),
        pending_one_off_events=replacements,
    )
    assert delivered == [operation_ref]
    assert replacements == []
    assert handoffs and handoffs[0]["kind"] == "house_career_offer"
    assert handoffs[0]["requires_player_decision"] is True
    assert writes[OPERATIONS_PATH]["archive"][operation_ref]["career_offer"]["delivery_status"] == "delivered"
