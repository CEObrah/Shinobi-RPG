import pytest

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.jianghu_scene import JianghuSceneCommandsMixin
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.sim.events import CampaignTime


COMBAT_REF = "combat:test:parley"
PLAYER_REF = "pc.test"
ENEMY_REFS = ["enemy.hidden.1", "enemy.hidden.2"]


class _CombatRepository:
    def __init__(self):
        self.rows = {
            "state/martial-world/combats.json": {
                "schema": "jianghu-combat-state-1.0",
                "combats": {
                    COMBAT_REF: {
                        "combat_id": COMBAT_REF,
                        "status": "active",
                        "sides": {
                            "side_a": [PLAYER_REF, "ally.test"],
                            "side_b": list(ENEMY_REFS),
                        },
                        "combatants": {
                            PLAYER_REF: {},
                            "ally.test": {},
                            ENEMY_REFS[0]: {},
                            ENEMY_REFS[1]: {},
                        },
                    }
                },
            },
        }

    def read_json(self, path):
        if path not in self.rows:
            raise FileNotFoundError(path)
        return self.rows[path]


class _SceneHarness(JianghuSceneCommandsMixin):
    def __init__(self):
        self.repository = _CombatRepository()
        self.scene_path = "state/scene.json"

    def _simple_plan(self, command, meta, current_time, *, writes_records, code, result):
        return {
            "writes_records": writes_records,
            "code": code,
            "result": result,
            "world_time": str(current_time),
        }


def _interaction(target_ref=COMBAT_REF):
    return CommandEnvelope(
        campaign_id="test-campaign",
        request_id="request.combat-parley",
        actor_id=PLAYER_REF,
        command_type="jianghu_interaction_resolution",
        expected_revision=1,
        submitted_at="2026-08-28T00:00:00Z",
        payload={
            "action": "ask",
            "target_ref": target_ref,
            "player_statement": "State your business.",
            "topic": "parley",
        },
    )


def test_combat_side_parley_records_only_player_attempt_without_enemy_identity_leak():
    harness = _SceneHarness()
    current_time = CampaignTime.parse("SE-0061-01-01T00:00:00")

    built = harness._jianghu_interaction_resolution(_interaction(), {}, current_time)

    assert built["code"] == "jianghu_interaction_recorded"
    assert built["result"]["target_kind"] == "opposing_combat_side"
    assert built["result"]["world_response_status"] == "not_established_by_attempt"
    ledger = built["writes_records"]["state/martial-world/interaction-attempts.json"]
    row = ledger["attempts"][-1]
    assert row["target_ref"] == COMBAT_REF
    assert row["target_kind"] == "opposing_combat_side"
    assert row["world_response_status"] == "not_established_by_attempt"
    rendered = repr(built)
    assert all(enemy_ref not in rendered for enemy_ref in ENEMY_REFS)


def test_combat_side_parley_rejects_guessed_nonactive_combat_reference():
    harness = _SceneHarness()
    current_time = CampaignTime.parse("SE-0061-01-01T00:00:00")

    with pytest.raises(CommandRejectedError) as caught:
        harness._jianghu_interaction_resolution(_interaction("combat:test:other"), {}, current_time)

    assert caught.value.code == "jianghu_scene_person_not_player_visible"


def _planner_harness():
    planner = object.__new__(RepositoryCommandPlanner)
    planner.repository = _CombatRepository()
    planner._allow_site_service_presence = False
    planner._base = lambda _command: ({}, CampaignTime.parse("SE-0061-01-01T00:00:00"))
    planner._jianghu_interaction_resolution = lambda command, meta, now: "interaction_allowed"
    return planner


def test_active_combat_allows_only_reversible_interaction_beside_combat_resolution():
    planner = _planner_harness()

    assert planner._build(_interaction()) == "interaction_allowed"

    unrelated = CommandEnvelope(
        campaign_id="test-campaign",
        request_id="request.combat-parley-unrelated",
        actor_id=PLAYER_REF,
        command_type="advance_time",
        expected_revision=1,
        submitted_at="2026-08-28T00:00:00Z",
        payload={"target_time": "SE-0061-01-02T00:00:00"},
    )
    with pytest.raises(CommandRejectedError) as caught:
        planner._build(unrelated)
    assert caught.value.code == "jianghu_active_combat_requires_resolution"
