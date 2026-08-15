"""Regression coverage for bounded NPC team assembly and combat doctrine."""

from pathlib import Path
import json

from shinobi_runtime.commands.campaign_runtime_planner import CampaignCommandPlanner
from shinobi_runtime.commands.team_composition import (
    TeamMemberProfile,
    build_compact_doctrine,
    capability_profile_from_record,
    derive_member_roles,
    select_complementary_roster,
)
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store import RepositoryStore


ROOT = Path(__file__).resolve().parents[2]


def _record(*, leadership=0, recon=0, control=0, assault=0, mobility=0, stealth=0, support=0, engineering=0, capture=0, readiness="ready"):
    return {
        "schema": "shinobi_character",
        "life_status": "active",
        "condition": {"readiness": readiness},
        "attributes": {
            "intelligence": leadership,
            "composure": leadership,
            "presence": leadership,
            "awareness": recon,
            "strength": assault,
            "agility": max(assault, mobility),
            "coordination": max(leadership, mobility),
        },
        "chakra_dimensions": {
            "control": max(control, capture, support),
            "output": assault,
            "sensing": recon,
            "suppression": stealth,
        },
        "operational_skills": {
            "leadership": leadership,
            "tactics": max(leadership, control, capture),
            "team_coordination": max(leadership, support, capture),
            "tracking": recon,
            "investigation": recon,
            "infiltration": stealth,
            "survival": max(mobility, support),
            "traps": engineering,
        },
        "martial_skills": {
            "movement": mobility,
            "stealth": stealth,
            "grappling": capture,
            "kenjutsu": assault,
        },
        "domain_proficiencies": {
            "battlefield_control": control,
            "engineering": engineering,
            "medical": support,
        },
        "roles": [],
    }


def test_capability_projection_respects_unavailability() -> None:
    ready = capability_profile_from_record("char.ready", _record(recon=80, control=60))
    down = capability_profile_from_record("char.down", _record(recon=95, readiness="incapacitated"))
    assert ready.available is True
    assert ready.scores["reconnaissance"] > ready.scores["assault"]
    assert down.available is False
    assert down.availability_reason == "incapacitated"


def test_roster_selection_prefers_complementary_members() -> None:
    profiles = [
        capability_profile_from_record("char.command", _record(leadership=90, support=55)),
        capability_profile_from_record("char.recon", _record(recon=95, stealth=75)),
        capability_profile_from_record("char.control", _record(control=95, capture=70)),
        capability_profile_from_record("char.assault", _record(assault=95, mobility=80)),
        capability_profile_from_record("char.assault2", _record(assault=92, mobility=78)),
    ]
    selected = select_complementary_roster(profiles, target_size=4)
    refs = {profile.person_ref for profile in selected}
    assert selected[0].person_ref == "char.command"
    assert {"char.recon", "char.control", "char.assault"}.issubset(refs)
    assert "char.assault2" not in refs


def test_compact_doctrine_uses_roster_strengths_without_storing_an_essay() -> None:
    roster = (
        capability_profile_from_record("char.command", _record(leadership=90, support=65)),
        capability_profile_from_record("char.recon", _record(recon=95, stealth=80)),
        capability_profile_from_record("char.control", _record(control=96, engineering=75)),
        capability_profile_from_record("char.assault", _record(assault=94, mobility=82, capture=70)),
    )
    leader = roster[0].person_ref
    team = {
        "id": "team.test.dynamic",
        "name": "Dynamic Test Team",
        "leader_ref": leader,
        "deputy_ref": "char.recon",
        "member_refs": [profile.person_ref for profile in roster],
        "roles": derive_member_roles(roster, leader_ref=leader),
        "training": {"instructor_refs": [leader]},
    }
    doctrine = build_compact_doctrine(
        team,
        {profile.person_ref: profile for profile in roster},
        at=CampaignTime.parse("SE-0061-02-10T07:00:00"),
        doctrine_identity="complementary control doctrine",
        motto="See. Shape. Strike. Return.",
        training_focus=("team coordination",),
    )
    phases = {phase["name"]: phase for phase in doctrine["phases"]}
    assert "char.recon" in phases["SEE"]["primary_members"]
    assert "char.control" in phases["SHAPE"]["primary_members"]
    assert "char.assault" in phases["BREAK"]["primary_members"]
    assert len(json.dumps(doctrine, ensure_ascii=False).encode("utf-8")) < 6500


def test_campaign_policy_gives_force_factions_dynamic_team_assembly() -> None:
    planner = CampaignCommandPlanner(RepositoryStore(ROOT))
    book = planner._autonomy_policy_book()
    kumo = book.faction_assignments["faction.kumo_military_intelligence"]
    assert kumo["team_creation"]["mode"] == "dynamic"
    assert kumo["team_creation"]["team_type"] == "temporary_task_force"
    root = book.faction_assignments["faction.root"]
    assert root["team_creation"]["team_id"] == "team.konoha.root.field_cell"


def test_exact_team_registration_creates_member_derived_doctrine() -> None:
    repo = RepositoryStore(ROOT)
    planner = CampaignCommandPlanner(repo)
    at = CampaignTime.parse(repo.read_json("state/meta.json")["time"])
    scheduler = planner._load_scheduler(current_time=at, scene=planner._scene_base(at))
    writes = {}
    team_id = "team.test.autodoctrine"
    path, team = planner._register_exact_team_state(
        team_id=team_id,
        name="Auto Doctrine Test Team",
        team_type="temporary_task_force",
        parent_institution_ref="institution.konoha.hokage_administration",
        assignment_authority_ref="canon_hiruzen",
        leader_ref="canon_yugao",
        member_refs=("canon_yugao", "canon_aoba", "canon_hayate"),
        roles={
            "canon_yugao": "field_lead",
            "canon_aoba": "reconnaissance_sensor",
            "canon_hayate": "assault_finisher",
        },
        classification="restricted",
        at=at,
        basis="test only",
        scheduler=scheduler,
        record_writes=writes,
    )
    assert path == "state/team/team-test-autodoctrine.json"
    assert team["doctrine_ref"] == f"{team_id}.doctrine"
    doctrine_path = planner._team_doctrine_path(team_id)
    assert doctrine_path in writes
    doctrine = writes[doctrine_path]
    assert doctrine["approved_by"] == "canon_hiruzen"
    assert doctrine["team_id"] == team_id
    assert set(doctrine["familiarity"]) == set(team["member_refs"])
