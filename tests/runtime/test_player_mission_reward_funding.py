from __future__ import annotations

import copy

from shinobi_runtime.commands.player_mission_reward_funding import PlayerMissionRewardFundingMixin


MISSION_ID = "mission.offer.testreward"
PARTICIPANTS = ("pc_wei_tang", "canon_hayama_shirakumo", "canon_ensui_nara")


class _Repository:
    def __init__(self):
        self.inventory = {
            "schema": "inventory-registry",
            "authority": True,
            "holders": {"treasury.konoha": {"currency.ryo": 1_000_000}},
        }

    def read_json(self, path):
        assert path == "state/inventory/registry.json"
        return copy.deepcopy(self.inventory)


class _Planner(PlayerMissionRewardFundingMixin):
    def __init__(self):
        self.repository = _Repository()

    def _economy_mechanics(self):
        return {
            "mission_ranks": {
                "B": {
                    "participant_bonus_typical_ryo": 70_000,
                    "participant_bonus_max_ryo": 100_000,
                }
            }
        }


def _owner_record():
    return {
        "schema": "mission-runtime",
        "mission_id": MISSION_ID,
        "issuer_ref": "faction.konoha_mission_office",
        "authority_ref": "canon_hiruzen",
        "mission_rank": "B",
        "funding_holder_ref": "treasury.konoha",
        "escrow_holder_ref": None,
        "opened_at": "SE-0061-06-01T07:00:00",
        "authorized_at": "SE-0061-06-01T07:00:00",
        "starts_at": None,
        "deadline_at": "SE-0061-06-08T07:00:00",
        "next_due_at": None,
        "operation_ref": "team.blackhound",
        "closed_at": None,
        "briefing": None,
        "state": "offered",
        "participant_refs": list(PARTICIPANTS),
        "objectives": [
            {
                "objective_id": "objective.testreward",
                "kind": "protect",
                "required": True,
                "dependencies": [],
                "status": "pending",
                "progress_milli": 0,
                "resolution_ref": None,
            }
        ],
        "settlement_terms": [],
        "terminal_reason_ref": None,
        "settlement": None,
    }


def test_player_offer_is_funded_at_rank_typical_bonus_per_participant() -> None:
    planner = _Planner()
    path = f"state/mission/{MISSION_ID}.json"
    event_id = "event.player_mission_offered.testreward"
    record_writes = {path: _owner_record()}
    world_events = {
        "events": [
            {
                "id": event_id,
                "material_consequence_refs": [MISSION_ID],
                "affected_owner_refs": [path],
            }
        ]
    }
    result = planner._fund_player_offer_reward(
        offer={"kind": "player_mission_offer", "mission_id": MISSION_ID, "event_id": event_id},
        world_events=world_events,
        record_writes=record_writes,
    )

    owner = record_writes[path]
    escrow_ref = "escrow." + MISSION_ID
    inventory = record_writes["state/inventory/registry.json"]
    assert result["participant_bonus_ryo"] == 70_000
    assert result["escrowed_reward_ryo"] == 210_000
    assert owner["escrow_holder_ref"] == escrow_ref
    assert len(owner["settlement_terms"]) == len(PARTICIPANTS)
    assert {term["account_ref"] for term in owner["settlement_terms"]} == set(PARTICIPANTS)
    assert all(term["quantity"] == 70_000 for term in owner["settlement_terms"])
    assert all(term["applies_on"] == ["succeeded"] for term in owner["settlement_terms"])
    assert inventory["holders"]["treasury.konoha"]["currency.ryo"] == 790_000
    assert inventory["holders"][escrow_ref]["currency.ryo"] == 210_000
    assert "state/inventory/registry.json" in world_events["events"][0]["affected_owner_refs"]
