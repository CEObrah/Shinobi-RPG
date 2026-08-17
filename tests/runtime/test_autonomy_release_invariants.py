import json
from pathlib import Path

from shinobi_runtime.commands.campaign_runtime_planner import CampaignCommandPlanner
from shinobi_runtime.information.store import InformationStore
from shinobi_runtime.store import RepositoryStore
from shinobi_runtime.sim.scheduler_store import SchedulerStore

ROOT = Path(__file__).resolve().parents[2]


def test_specialist_team_candidates_do_not_expand_to_whole_force_roster() -> None:
    planner = CampaignCommandPlanner(RepositoryStore(ROOT))
    book = planner._autonomy_policy_book()
    assignment = book.faction_assignments["faction.kumo_military_intelligence"]
    faction = planner.repository.read_json("state/reg/factions/faction-kumo-military-intelligence.json")
    spec = assignment["team_creation"]
    refs = planner._candidate_refs(assignment, faction, spec)
    assert set(refs).issubset(set(assignment.get("mission_participant_refs", [])) | set(faction["faction"].get("leadership_ids", [])) | set(faction["faction"].get("key_member_ids", [])))
    assert "canon_killer_b" not in refs
    assert "canon_yugito" not in refs


def test_world_registry_review_handles_clan_collection_and_has_no_first_eight_slice() -> None:
    source = (ROOT / "runtime/shinobi_runtime/commands/domains/time.py").read_text()
    assert 'payload_record.get("clans", [])' in source
    assert 'selected_institutions = (pipeline + remaining)[:8]' not in source
    assert 'list(institutions) + list(clans)' in source


def test_autonomy_policy_has_no_fixed_mission_or_team_ceiling() -> None:
    book = CampaignCommandPlanner(RepositoryStore(ROOT))._autonomy_policy_book()
    for assignment in book.faction_assignments.values():
        assert "max_active_missions" not in assignment
        spec = assignment.get("team_creation") if isinstance(assignment, dict) else None
        if isinstance(spec, dict):
            assert "max_active_teams" not in spec


def test_core_autonomy_has_no_hidden_mission_or_decision_slice() -> None:
    autonomy = (ROOT / "runtime/shinobi_runtime/commands/domains/autonomy.py").read_text()
    engine = (ROOT / "runtime/shinobi_runtime/autonomy/engine.py").read_text()
    assert "active_autonomous_mission_exists" not in autonomy
    assert "del open_refs[4:]" not in autonomy
    assert "tuple(decisions[:3])" not in engine


def test_mission_linked_world_operations_cannot_self_complete_in_generic_queue() -> None:
    source = (ROOT / "runtime/shinobi_runtime/commands/living_world_operations.py").read_text()
    assert 'linked_missions = [ref for ref in op.get("mission_refs", [])' in source
    assert '"status": "mission_linked"' in source
    # The guard must occur before generic progress changes are calculated.
    assert source.index('linked_missions = [ref for ref in op.get("mission_refs", [])') < source.index('participant_count = len([x for x in op.get("participant_refs", [])')


def test_revalidated_specialist_team_state_contains_no_orphan_rosters_or_stale_training() -> None:
    repo = RepositoryStore(ROOT)
    team_index = repo.read_json("state/index/owners/team.json")["owners"]
    scheduler = SchedulerStore(repo).load(full=True)
    bad_team_refs = {
        "team.iwa.engineering.command.auto.7ea28f6462fa",
        "team.kumo.military.intelligence.auto.42f5d569ba6c",
        "team.suna.puppet.corps.auto.74552fea8bcb",
    }
    for team_ref in bad_team_refs:
        assert team_ref not in team_index
        assert f"{team_ref}.doctrine" not in team_index
        assert f"host.team.{team_ref}" not in scheduler.hosts
        assert all(team_ref not in str(event.to_record()) for event in scheduler.queue.snapshot())

    current_specialist_refs = {
        "team.iwa.engineering.command.auto.3d13f5395a68",
        "team.kumo.military.intelligence.auto.021c09f80884",
        "team.suna.puppet.corps.auto.09d41af9515f",
        "team.kiri.hunter.office.auto.a29a55f55d97",
    }
    for team_ref in current_specialist_refs:
        team = repo.read_json(team_index[team_ref])
        member_refs = set(team["member_refs"])
        for session in team.get("training", {}).get("recent_sessions", []):
            assert set(session.get("member_refs", [])).issubset(member_refs)
        history = repo.read_json(f"state/team/history/{team_ref}.json")
        training_event_refs = [
            ref for ref in history.get("notable_event_refs", [])
            if isinstance(ref, str) and ref.startswith("event.team_training_session_resolved.")
        ]
        assert history["training_sessions"] == len(training_event_refs)
        assert history["training_sessions"] >= len(team.get("training", {}).get("recent_sessions", []))
        assert set(history.get("notable_event_refs", [])) == set(training_event_refs)


def test_specialist_revalidation_repair_removed_false_progression_and_social_edges() -> None:
    repo = RepositoryStore(ROOT)
    expected = {
        "state/char/roshi.json": (107, 86, 94, 92),
        "state/char/han-jinchuriki.json": (105, 82, 90, 88),
        "state/char/atsui.json": (94, 72, 80, 78),
        "state/char/yugito-nii.json": (112, 88, 95, 94),
    }
    for path, values in expected.items():
        record = repo.read_json(path)
        movement, leadership, tactics, coordination = values
        assert record["martial_skills"]["movement"] == movement
        assert record["operational_skills"]["leadership"] == leadership
        assert record["operational_skills"]["tactics"] == tactics
        assert record["operational_skills"]["team_coordination"] == coordination

    banks = repo.read_json("state/development/banks.json")["entries"]
    for person_ref in ("canon_roshi", "canon_han_jinchuriki", "canon_atsui", "canon_yugito_nii"):
        assert person_ref not in banks

    rel_index = repo.read_json("state/reg/relationship-edge-index.json")["edge_index"]
    for edge_ref in (
        "rel.canon_han_jinchuriki.canon_roshi.professional_teammates",
        "rel.canon_roshi.canon_han_jinchuriki.professional_teammates",
        "rel.canon_atsui.canon_yugito_nii.professional_teammates",
        "rel.canon_yugito_nii.canon_atsui.professional_teammates",
    ):
        assert edge_ref not in rel_index


def test_legacy_vague_autonomy_missions_are_not_left_live_after_rich_operation_upgrade() -> None:
    repo = RepositoryStore(ROOT)
    legacy_ref = "mission.autonomy.528941e32428a2648b"
    mission = repo.read_json(f"state/mission/{legacy_ref}.json")
    assert mission["state"] == "aborted"
    assert mission["terminal_reason_ref"] == "baseline.rich_autonomy_terminal_state"
    assert mission["operation_ref"] is None
    assert mission["settlement"]["outcome"] == "aborted"
    memory = repo.read_json("state/autonomy/faction-memory/faction.kumo_military_intelligence.json")
    assert legacy_ref not in memory["active_mission_team_refs"]
    faction = repo.read_json("state/reg/factions/faction-kumo-military-intelligence.json")["faction"]
    assert legacy_ref not in faction["plan_state"].get("autonomous_mission_refs", [])


def test_autonomous_internal_reports_are_routed_to_their_owning_faction_holder() -> None:
    repo = RepositoryStore(ROOT)
    information = InformationStore(repo)
    projection = information.projection()
    assert projection["delivery_count"] > 0
    claims = {}
    for path in (ROOT / "state/reg/information/claims").glob("*/*.json"):
        shard = json.loads(path.read_text())
        claims.update(shard["claims"])
    for claim_id, claim in claims.items():
        subject_ref = claim["subject_ref"]
        source_ref = claim["source_ref"]
        if (
            claim["epistemic_kind"] != "report"
            or not claim_id.startswith("claim.autonomy.")
            or not subject_ref.startswith("faction.")
            or source_ref == subject_ref
        ):
            continue
        if not information.holder_knows(source_ref, claim_id):
            continue
        assert information.holder_knows(subject_ref, claim_id)
        delivered = False
        for delivery_id in information.holder_delivery_refs(subject_ref):
            row = information.delivery(delivery_id)
            if row is not None and row["claim_id"] == claim_id and row["recipient_ref"] == subject_ref:
                delivered = True
                break
        assert delivered


def test_every_persisted_autonomous_faction_host_is_in_living_faction_registry() -> None:
    repo = RepositoryStore(ROOT)
    registry = repo.read_json("state/reg/factions.json")["record_index"]
    scheduler = SchedulerStore(repo).load(full=True)
    hosted = {
        host_ref.removeprefix("host.faction.")
        for host_ref in scheduler.hosts
        if host_ref.startswith("host.faction.")
    }
    assert hosted
    assert hosted.issubset(set(registry))
    for faction_ref in hosted:
        owner = repo.read_json(registry[faction_ref])
        assert owner["faction"]["id"] == faction_ref


def test_delegated_institution_review_does_not_create_parallel_operation_authority() -> None:
    import copy
    from shinobi_runtime.commands.envelope import CommandEnvelope
    from shinobi_runtime.sim.events import CampaignTime

    repo = RepositoryStore(ROOT)
    planner = CampaignCommandPlanner(repo)
    bundle = repo.read_json("state/world/institutions-konoha.json")
    institution = copy.deepcopy(next(
        row for row in bundle["payload"]["institutions"]
        if row["id"] == "institution.konoha.anbu"
    ))
    meta = repo.read_json("state/meta.json")
    command = CommandEnvelope(
        campaign_id=meta["campaign_id"],
        request_id="delegated-institution-parity",
        actor_id="canon_hiruzen",
        command_type="advance_time",
        expected_revision=meta["revision"],
        submitted_at=meta["time"],
        payload={"target_time": "SE-0061-07-01T07:00:00"},
        mode="autonomous",
    )
    writes: dict[str, dict] = {}
    world_events = copy.deepcopy(repo.read_json("state/reg/world-events.json"))
    result = planner._review_bundled_institution_operation(
        institution=institution,
        at=CampaignTime.parse("SE-0061-07-01T07:00:00"),
        command=command,
        world_events=world_events,
        record_writes=writes,
    )
    assert result == {
        "institution_ref": "institution.konoha.anbu",
        "status": "delegated",
        "delegated_faction_ref": "faction.konoha_anbu",
    }
    assert not any(path.startswith("state/operation/") for path in writes)


def test_existing_faction_relationships_gain_salience_without_stance_change_or_new_edges() -> None:
    import copy

    repo = RepositoryStore(ROOT)
    planner = CampaignCommandPlanner(repo)
    border_path = "state/reg/factions/faction-fire-border-authority.json"
    smuggling_path = "state/reg/factions/faction-fire-smuggling-network.json"
    border = copy.deepcopy(repo.read_json(border_path))
    smuggling_before = repo.read_json(smuggling_path)
    before_border_rel = next(
        row for row in border["faction"]["relationships"]
        if row["target_id"] == "faction.fire_smuggling_network"
    )
    before_smuggling_rel = next(
        row for row in smuggling_before["faction"]["relationships"]
        if row["target_id"] == "faction.fire_border_authority"
    )
    border_intensity = before_border_rel["intensity"]
    smuggling_intensity = before_smuggling_rel["intensity"]
    border_stance = before_border_rel["stance"]
    smuggling_stance = before_smuggling_rel["stance"]
    relationship_count = len(border["faction"]["relationships"])
    writes: dict[str, dict] = {}

    touched = planner._update_faction_relationship_evidence(
        faction_id="faction.fire_border_authority",
        operation={
            "opposition_refs": ["faction.fire_smuggling_network", "faction.akatsuki"],
            "client_ref": None,
        },
        faction_record=border,
        record_writes=writes,
    )

    border_after = next(
        row for row in border["faction"]["relationships"]
        if row["target_id"] == "faction.fire_smuggling_network"
    )
    smuggling = writes[smuggling_path]
    smuggling_after = next(
        row for row in smuggling["faction"]["relationships"]
        if row["target_id"] == "faction.fire_border_authority"
    )
    assert border_after["intensity"] == min(100, border_intensity + 1)
    assert smuggling_after["intensity"] == min(100, smuggling_intensity + 1)
    assert border_after["stance"] == border_stance
    assert smuggling_after["stance"] == smuggling_stance
    assert len(border["faction"]["relationships"]) == relationship_count
    assert all(row["target_id"] != "faction.akatsuki" for row in border["faction"]["relationships"])
    assert touched == ["faction.fire_border_authority", "faction.fire_smuggling_network"]


def test_no_first_n_causal_caps_in_team_assignment_mission_consequences_or_exact_method_selection() -> None:
    assignment = (ROOT / "runtime/shinobi_runtime/commands/living_world_assignment.py").read_text()
    mission = (ROOT / "runtime/shinobi_runtime/commands/living_world_mission.py").read_text()
    consequences = (ROOT / "runtime/shinobi_runtime/commands/living_world_consequences.py").read_text()
    social = (ROOT / "runtime/shinobi_runtime/commands/living_world_social.py").read_text()
    academy = (ROOT / "runtime/shinobi_runtime/commands/living_world_academy.py").read_text()
    combat = (ROOT / "runtime/shinobi_runtime/commands/domains/combat.py").read_text()
    operations = (ROOT / "runtime/shinobi_runtime/commands/living_world_operations.py").read_text()

    for forbidden in (
        "refs[:32]",
        "tuple(refs)[-32:]",
        "participant_refs[:16]",
        "participants[:16]",
        "participants[:8]",
        "ranked[:24]",
        "raw[:32]",
        "_MAX_RELATIONSHIP_UPDATES",
    ):
        assert forbidden not in assignment + mission + consequences + social + academy + combat
    assert 'holder_claim_refs(actor)' not in operations
    assert 'holder_recent_claim_refs(actor, limit=64)' in operations
    assert 'holder_subject_claim_refs(actor, subject_ref, limit=64)' in operations
    assert 'self._stable_program_pick(' in operations


def test_konoha_clan_settlement_clocks_match_current_scheduler_boundary_without_retroactive_outcomes() -> None:
    repo = RepositoryStore(ROOT)
    bundle = repo.read_json("state/world/institutions-konoha.json")
    scheduler = SchedulerStore(repo).load(full=True)
    host = scheduler.hosts["host.world.institutions.konoha"].state
    for clan in bundle["payload"]["clans"]:
        assert clan["settlement"]["last_settled_at"] == str(host.resolved_through)
        assert clan["settlement"]["next_due_at"] == (None if host.next_due is None else str(host.next_due))
