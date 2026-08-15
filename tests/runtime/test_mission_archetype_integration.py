from __future__ import annotations

from pathlib import Path

from shinobi_runtime.commands.campaign_runtime_planner import CampaignCommandPlanner
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store import RepositoryStore

ROOT = Path(__file__).resolve().parents[2]


def test_autonomous_mission_brief_consumes_registered_archetype_constraints() -> None:
    repo = RepositoryStore(ROOT)
    planner = CampaignCommandPlanner(repo)
    program = planner._institutional_program("faction.fire_border_authority")
    template = next(
        row for row in program["operation_templates"]
        if row["id"] == "border.counter_smuggling"
    )
    at = CampaignTime.parse(repo.read_json("state/meta.json")["time"])
    operation = planner._operation_spec_from_template(
        faction_id="faction.fire_border_authority",
        actor="support.border.kazuo_murata",
        at=at,
        template=template,
    )
    brief = planner._operation_brief(
        operation,
        mission_id="mission.test.archetype",
        objective_kind="intercept",
        at=at,
    )
    assert brief is not None
    joined = "\n".join(brief.intelligence_constraints)
    assert "Investigate and interdict a smuggling route using lawfully acquired evidence" in joined
    assert "Intended assignment region: Fire Country" in joined
    assert "Assignment scope: team" in joined
    assert brief.subject_ref == "faction.fire_smuggling_network"
