import shinobi_runtime.commands.planner as planner_module
from shinobi_runtime.commands.combat_npc_judgment_contract import (
    ATTACK_JUDGMENT_WIRE_CONTRACT,
    normalize_jianghu_combat_payload,
)
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.planner import RepositoryCommandPlanner


def _attack(**overrides):
    row = {
        "intent": "attack",
        "target_ref": "pc_wei_tang",
        "action_kind": "thrust",
        "weapon_ref": "weapon_spear",
        "targeting_intent": "disable",
        "hit_zone": "chest",
        "qi_allocation_milli": {},
        "poison_ref": None,
    }
    row.update(overrides)
    return row


def test_live_style_noop_aliases_canonicalize_without_changing_meaningful_choice():
    payload = {
        "action": "exchange",
        "combat_ref": "combat:test",
        "npc_judgments": {
            "npc.attacker": _attack(qi_allocation_milli=0, poison_ref="none"),
            "npc.holder": {"intent": "hold"},
        },
    }

    normalized = normalize_jianghu_combat_payload(payload)
    attack = normalized["npc_judgments"]["npc.attacker"]

    assert attack["intent"] == "attack"
    assert attack["target_ref"] == "pc_wei_tang"
    assert attack["action_kind"] == "thrust"
    assert attack["weapon_ref"] == "weapon_spear"
    assert attack["targeting_intent"] == "disable"
    assert attack["hit_zone"] == "chest"
    assert attack["qi_allocation_milli"] == {}
    assert attack["poison_ref"] is None
    assert normalized["npc_judgments"]["npc.holder"] == {"intent": "hold"}

    # Raw input remains the client-authored representation used by the command
    # digest/idempotency boundary.
    assert payload["npc_judgments"]["npc.attacker"]["qi_allocation_milli"] == 0
    assert payload["npc_judgments"]["npc.attacker"]["poison_ref"] == "none"


def test_planner_canonicalizes_aliases_before_the_combat_reducer(monkeypatch):
    class _Repository:
        @staticmethod
        def read_json(_path):
            return {}

    class _CapturePlanner(RepositoryCommandPlanner):
        def __init__(self):
            self.repository = _Repository()
            self._allow_site_service_presence = False

        def _base(self, _command):
            return {}, None

        def _jianghu_combat_resolution(self, command, _meta, _now):
            return command

    monkeypatch.setattr(planner_module, "active_combat_for_person", lambda *_args, **_kwargs: None)
    command = CommandEnvelope(
        campaign_id="campaign.test",
        request_id="request.test",
        actor_id="pc_wei_tang",
        command_type="jianghu_combat_resolution",
        expected_revision=1,
        submitted_at="2026-09-13T00:00:00Z",
        payload={
            "action": "exchange",
            "combat_ref": "combat:test",
            "npc_judgments": {
                "npc.attacker": _attack(qi_allocation_milli=0, poison_ref="none"),
            },
        },
        mode="gameplay",
    )

    reduced_command = _CapturePlanner()._build(command)
    attack = reduced_command.payload["npc_judgments"]["npc.attacker"]

    assert reduced_command.digest == command.digest
    assert attack["qi_allocation_milli"] == {}
    assert attack["poison_ref"] is None
    assert command.payload["npc_judgments"]["npc.attacker"]["qi_allocation_milli"] == 0
    assert command.payload["npc_judgments"]["npc.attacker"]["poison_ref"] == "none"


def test_none_poison_alias_is_case_and_whitespace_tolerant():
    payload = {"npc_judgments": {"npc.attacker": _attack(poison_ref=" NoNe ")}}
    normalized = normalize_jianghu_combat_payload(payload)
    assert normalized["npc_judgments"]["npc.attacker"]["poison_ref"] is None


def test_normalizer_does_not_make_meaningful_qi_or_poison_choices():
    payload = {
        "npc_judgments": {
            "npc.attacker": _attack(qi_allocation_milli=250, poison_ref="poison.unknown"),
        }
    }
    assert normalize_jianghu_combat_payload(payload) is payload
    assert payload["npc_judgments"]["npc.attacker"]["qi_allocation_milli"] == 250
    assert payload["npc_judgments"]["npc.attacker"]["poison_ref"] == "poison.unknown"


def test_already_canonical_and_nonattack_rows_are_noops():
    canonical = {"npc_judgments": {"npc.attacker": _attack(), "npc.retreat": {"intent": "withdraw"}}}
    assert normalize_jianghu_combat_payload(canonical) is canonical


def test_wire_contract_documents_accepted_noop_aliases():
    assert ATTACK_JUDGMENT_WIRE_CONTRACT["qi_allocation_milli"]["canonical_no_qi"] == {}
    assert 0 in ATTACK_JUDGMENT_WIRE_CONTRACT["qi_allocation_milli"]["accepted_no_qi_aliases"]
    assert ATTACK_JUDGMENT_WIRE_CONTRACT["poison_ref"]["canonical_no_poison"] is None
    assert "none" in ATTACK_JUDGMENT_WIRE_CONTRACT["poison_ref"]["accepted_no_poison_aliases"]
