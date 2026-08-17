import copy
import json
from pathlib import Path
import shutil

from shinobi_runtime.commands.campaign_runtime_planner import CampaignCommandPlanner
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store import RepositoryStore

ROOT = Path(__file__).resolve().parents[2]


def _copy_campaign(tmp_path: Path) -> Path:
    root = tmp_path / "campaign"
    shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".pytest_cache", "__pycache__", "*.pyc"))
    return root


def test_suna_puppet_readiness_repairs_only_current_specialist_assets_with_reusable_kit(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    puppet_path = root / "state/reg/puppets.json"
    registry = json.loads(puppet_path.read_text())
    chiyo = next(row for row in registry["puppets"] if row["puppet_id"] == "puppet.chiyo.chikamatsu.01")
    chiyo["current_damage"] = 70
    chiyo["deployed"] = False
    chiyo["available"] = True
    # Sasori is not a member of the current Suna Puppet Corps field cell. His asset must not be repaired.
    sasori = next(row for row in registry["puppets"] if row["owner_id"] == "canon_sasori")
    sasori["current_damage"] = 70
    sasori["deployed"] = False
    sasori["available"] = True
    puppet_path.write_text(json.dumps(registry, indent=2) + "\n")

    before_stock = RepositoryStore(root).read_json("state/stock/suna-shinobi.json")["puppet_kits"]
    planner = CampaignCommandPlanner(RepositoryStore(root))
    writes = {}
    result = planner._apply_autonomous_operation_effect(
        operation={
            "operation_id": "operation.test.suna_puppet_readiness",
            "operation_kind": "puppet_readiness",
            "subject_ref": "faction.suna_puppet_corps",
            "place_refs": ["place.suna.command"],
            "route_refs": [],
            "opposition_refs": [],
            "classification": "restricted",
        },
        faction_id="faction.suna_puppet_corps",
        actor="canon_chiyo",
        at=CampaignTime.parse("SE-0061-06-01T12:00:00"),
        evidence_event_ref="event.test.puppet_readiness",
        record_writes=writes,
    )
    assert result["status"] == "applied"
    staged = writes["state/reg/puppets.json"]
    repaired_chiyo = next(row for row in staged["puppets"] if row["puppet_id"] == chiyo["puppet_id"])
    untouched_sasori = next(row for row in staged["puppets"] if row["puppet_id"] == sasori["puppet_id"])
    assert repaired_chiyo["current_damage"] == 30
    assert untouched_sasori["current_damage"] == 70
    # Field kits are reusable equipment. Readiness requires one but does not consume it as a spare part.
    assert writes["state/stock/suna-shinobi.json"]["puppet_kits"] == before_stock


def test_civil_review_records_durable_evidence_backed_diplomatic_incident(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    planner = CampaignCommandPlanner(RepositoryStore(root))
    writes = {}
    result = planner._apply_autonomous_operation_effect(
        operation={
            "operation_id": "operation.test.daimyo_petition",
            "operation_kind": "petition_review",
            "subject_ref": "institution.konoha.hokage_administration",
            "client_ref": "faction.fire_daimyo_liaison",
            "place_refs": ["place.fire.daimyo.court"],
            "route_refs": [],
            "opposition_refs": [],
            "classification": "restricted",
        },
        faction_id="faction.fire_daimyo_liaison",
        actor="support.daimyo.noboru_shimizu",
        at=CampaignTime.parse("SE-0061-06-01T12:00:00"),
        evidence_event_ref="event.test.petition_evidence",
        record_writes=writes,
    )
    assert result["status"] == "applied"
    incidents = writes["state/reg/diplomacy.json"]["incidents"]
    incident = next(row for row in incidents if row["id"] in result["refs"])
    assert incident["kind"] == "petition_review"
    assert incident["evidence_ref"] == "event.test.petition_evidence"
    assert "faction.fire_daimyo_liaison" in incident["party_refs"]
    assert "institution.konoha.hokage_administration" in incident["party_refs"]


def test_autonomous_operation_reputation_can_materialize_new_subject_and_audience_profile(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    planner = CampaignCommandPlanner(RepositoryStore(root))
    writes = {}
    planner._apply_autonomous_reputation_signal(
        subject_ref="faction.suna_puppet_corps",
        audience_id="canon_rasa",
        source_event_ref="event.test.suna_puppet_readiness_completed",
        source_event_kind="institutional_operation_completed",
        signal_ref="reputation.signal.technical_achievement",
        classification="restricted",
        at=CampaignTime.parse("SE-0061-06-01T12:00:00"),
        record_writes=writes,
    )
    index = writes["state/reputation/index.json"]
    assert "faction.suna_puppet_corps" in index["subjects"]
    subject_path = index["subjects"]["faction.suna_puppet_corps"]
    subject = writes[subject_path]
    profile_path = subject["audience_profiles"]["canon_rasa"]
    profile = writes[profile_path]
    assert profile["evidence_count"] == 1
    assert profile["dimensions"] or profile["standing"]
    assert any(path.startswith("state/reputation/events/reputation.event.") for path in writes)


def test_kiri_transition_requires_political_governance_or_diplomacy_evidence() -> None:
    policy = json.loads((ROOT / "game/rules/autonomy/world-fronts.json").read_text())
    front = policy["fronts"]["pressure_kiri_transition"]
    assert front["faction_roles"] == {"faction_kiri": "source"}
    assert front["bootstrap_action_cycle"] == []
    assert front["strategic_action_cycle"] == []
    assert all("institutional_operation_completed" not in row["event_kinds"] for row in front["event_sources"])
    political = {kind for row in front["event_sources"] for kind in row["event_kinds"]}
    assert "governance_set_policy" in political
    assert "diplomacy_record_incident" in political

    programs = json.loads((ROOT / "game/rules/autonomy/institutional-programs.json").read_text())
    kiri_program = programs["programs"]["institution.kiri.mizukage_administration"]
    reform = next(row for row in kiri_program["operation_templates"] if row["operation_kind"] == "kiri_internal_reform_docket")
    assert reform["subject_candidates"] == ["faction_kiri"]

    mechanics = json.loads((ROOT / "game/data/mechanics/operational-world.json").read_text())
    assert mechanics["autonomous_effects"]["kiri_internal_reform_docket"]["effect_kind"] == "diplomacy_incident"


def test_kiri_mizukage_administration_can_emit_front_eligible_political_incident() -> None:
    repo = RepositoryStore(ROOT)
    planner = CampaignCommandPlanner(repo)
    bundle = repo.read_json("state/world/institutions-great-villages.json")
    institution = copy.deepcopy(next(
        row for row in bundle["payload"]["institutions"]
        if row["id"] == "institution.kiri.mizukage_administration"
    ))
    meta = repo.read_json("state/meta.json")
    command = CommandEnvelope(
        campaign_id=meta["campaign_id"], request_id="kiri-political-producer",
        actor_id="institution.kiri.mizukage_administration", command_type="advance_time",
        expected_revision=meta["revision"], submitted_at=meta["time"],
        payload={"target_time": "SE-0061-07-31T07:00:00"}, mode="autonomous",
    )
    writes: dict[str, dict] = {}
    world_events = copy.deepcopy(repo.read_json("state/reg/world-events.json"))
    opened = planner._review_bundled_institution_operation(
        institution=institution, at=CampaignTime.parse("SE-0061-07-01T07:00:00"),
        command=command, world_events=world_events, record_writes=writes,
    )
    assert opened["operation_kind"] == "kiri_internal_reform_docket"
    assert opened["status"] == "active"

    operation_path = planner._world_operation_path(opened["operation_ref"])
    due_at = CampaignTime.parse(writes[operation_path]["next_due_at"])
    completed = planner._review_bundled_institution_operation(
        institution=institution, at=due_at,
        command=command, world_events=world_events, record_writes=writes,
    )
    assert completed["status"] == "succeeded"
    assert any(ref.startswith("incident.autonomy.") for ref in completed["domain_effect_refs"])
    political_events = [
        event for event in world_events["events"]
        if event.get("kind") == "diplomacy_record_incident" and "faction_kiri" in event.get("host_refs", [])
    ]
    assert political_events
