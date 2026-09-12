from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

from shinobi_runtime.api.command_discovery import compact_play_context
from shinobi_runtime.api.operations import CampaignOperations
from shinobi_runtime.api.travel_operations import TravelAwareCampaignOperations
import shinobi_runtime.api.travel_operations as travel_operations


class _Repository:
    def read_json(self, path: str):
        if path == "state/meta.json":
            return {
                "campaign_id": "campaign.test",
                "revision": 7,
                "player_id": "pc.test",
            }
        raise AssertionError(path)


class _TravelOps(TravelAwareCampaignOperations):
    def __init__(self) -> None:
        self.repository = _Repository()
        self.coordinator = SimpleNamespace(
            git=SimpleNamespace(assert_pristine=lambda: None)
        )
        self.sheet_resolver = lambda person_ref: {"person_id": person_ref}

    def _locked(self):
        return nullcontext()

    def _read_fingerprint(self):
        return ("fingerprint", "state-root")

    def _require_read_only(self, *_args, **_kwargs) -> None:
        return None


def test_travel_enrichment_preserves_base_combat_judgment_frontier(monkeypatch) -> None:
    """Physical combat enrichment must extend, never replace, the LLM frontier."""

    def base_play_context(_self):
        return {
            "campaign": {
                "campaign_id": "campaign.test",
                "revision": 7,
                "player_id": "pc.test",
                "state_root": "state-root",
                "world_time": "SE-0001-01-01T00:00:00",
            },
            "scene": {
                "location_id": "route.test",
                "present_person_ids": ["pc.test"],
                "visible_person_ids": ["pc.test"],
                "gm_private_director_context": {
                    "privacy": "gm_private_scene_bounded_omniscient_truth_not_player_knowledge",
                    "combat": {
                        "privacy": "gm_private_exact_combat_direction_not_player_knowledge",
                        "combat_ref": "combat:test",
                        "status": "active",
                        "elapsed_ms": 0,
                        "npc_judgment_envelopes": [
                            {
                                "actor_ref": "npc.test",
                                "requires_llm_authored_intent": True,
                                "allowed_intents": ["attack", "hold", "withdraw"],
                            }
                        ],
                        "npc_judgment_contract": {
                            "meaningful_choice_owner": "chatgpt",
                            "freshness": "one_exact_decision_frontier",
                        },
                    },
                },
            },
            "player": {"person_id": "pc.test"},
            "person_reads": {"suggested_owner_ids": ["pc.test"]},
        }

    monkeypatch.setattr(CampaignOperations, "play_context", base_play_context)
    monkeypatch.setattr(
        travel_operations,
        "movement_scene_projection",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        travel_operations,
        "combat_observation_scene_projection",
        lambda **_kwargs: {
            "combat_ref": "combat:test",
            "friendly_participant_person_ids": ["pc.test"],
        },
    )
    monkeypatch.setattr(
        travel_operations,
        "gm_private_combat_director_projection",
        lambda **_kwargs: {
            "privacy": "gm_private_scene_bounded_omniscient_truth_not_player_knowledge",
            "combat_ref": "combat:test",
            "elapsed_ms": 0,
            "participant_count": 2,
            "world_truth_scope": "exact_active_combat_only",
        },
    )

    enriched = _TravelOps().play_context()
    combat = enriched["scene"]["gm_private_director_context"]["combat"]
    assert combat["participant_count"] == 2
    assert combat["npc_judgment_envelopes"][0]["actor_ref"] == "npc.test"
    assert combat["npc_judgment_contract"]["meaningful_choice_owner"] == "chatgpt"

    compact = compact_play_context(enriched)
    private_combat = compact["gm_scene_context"]["gm_private_scene_truth"]["combat"]
    assert private_combat["participant_count"] == 2
    assert private_combat["npc_judgment_envelopes"][0]["actor_ref"] == "npc.test"
    assert private_combat["npc_judgment_contract"]["meaningful_choice_owner"] == "chatgpt"
