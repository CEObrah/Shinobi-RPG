from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from shinobi_runtime.commands.campaign_runtime_planner import CampaignCommandPlanner
from shinobi_runtime.commands.constants import OBJECTIVE_EVIDENCE_EVENT_KINDS
from shinobi_runtime.commands.mission_subject_transport import MissionSubjectTransportMixin
from shinobi_runtime.commands.player_mission_offer_policy import PlayerMissionOfferPolicyMixin

ROOT = Path(__file__).resolve().parents[2]


def test_production_planner_composes_player_offer_and_principal_transport() -> None:
    mro = CampaignCommandPlanner.mro()
    assert PlayerMissionOfferPolicyMixin in mro
    assert MissionSubjectTransportMixin in mro
    assert mro.index(PlayerMissionOfferPolicyMixin) < mro.index(MissionSubjectTransportMixin)


def test_konoha_player_offer_cycle_contains_only_executable_brief_templates() -> None:
    policy = json.loads(
        (ROOT / "game/rules/autonomy/living-world.json").read_text(encoding="utf-8")
    )
    offer = policy["faction_assignments"]["faction.konoha_mission_office"]["player_offer"]
    cycle = offer["objective_cycle"]
    templates = offer["briefing_templates"]

    assert cycle == ["escort", "protect", "investigate"]
    assert set(cycle) == set(templates)
    assert "deliver" not in templates
    assert "correspondence" not in json.dumps(offer, ensure_ascii=False).lower()


def test_protect_objective_accepts_only_explicit_principal_travel_evidence() -> None:
    kinds = OBJECTIVE_EVIDENCE_EVENT_KINDS["protect"]
    assert "protected_travel_completed" in kinds
    assert "travel_completed" not in kinds


def test_existing_noboru_person_record_has_bounded_travel_capability() -> None:
    record = json.loads(
        (ROOT / "state/person/world/support-daimyo-noboru_shimizu.json").read_text(
            encoding="utf-8"
        )
    )
    speed = MissionSubjectTransportMixin._mission_person_speed(record)
    assert Decimal("0.50") <= speed <= Decimal("1.80")
