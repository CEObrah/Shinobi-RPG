from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil

from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.commands.campaign_runtime_planner import CampaignCommandPlanner
from shinobi_runtime.store import RegisteredSchemaValidator, RegisteredTemplateValidator, RepositoryStore
from shinobi_runtime.sim.events import CampaignTime

ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN_ID = "shinobi-wei-main"
SUBMITTED = "2026-08-14T00:00:00Z"


def _copy_campaign(tmp_path: Path) -> Path:
    campaign = tmp_path / "campaign"
    shutil.copytree(ROOT, campaign, ignore=shutil.ignore_patterns(".pytest_cache", "__pycache__", "*.pyc"))
    return campaign


def _command(repo: RepositoryStore, suffix: str) -> CommandEnvelope:
    meta = repo.read_json("state/meta.json")
    return CommandEnvelope(
        campaign_id=CAMPAIGN_ID, request_id=f"vitality-{suffix}-{meta['revision']}",
        actor_id="pc_wei_tang", command_type="advance_time", expected_revision=meta["revision"],
        submitted_at=SUBMITTED, payload={"target_time": meta["time"]}, mode="gameplay",
    )


def _validate_records(repo: RepositoryStore, writes: dict[str, dict]) -> None:
    templates = RegisteredTemplateValidator(repo); schemas = RegisteredSchemaValidator(repo)
    for path, row in writes.items():
        if not path.endswith('.json') or not isinstance(row, dict) or not isinstance(row.get('schema'), str):
            continue
        template = templates.templates.get(row['schema'])
        if template is not None: templates._validate_document(row, template, label=path)
        validator = schemas.validators.get(row['schema'])
        if validator is not None: validator.validate(row)


def _family_entry() -> dict[str, list[str]]:
    return {k: [] for k in ("courtships","proposals","unions","households","kinships","parenthoods","parentage","successions","events")}


def test_saved_kinship_bootstrap_is_authoritative_without_inventing_biology_or_dates() -> None:
    family = json.loads((ROOT / "state/family/index.json").read_text())
    assert family["counts"]["parentage"] >= 9
    assert family["counts"]["kinships"] >= 6
    for path in family["parentage"].values():
        row = json.loads((ROOT / path).read_text())
        assert row["authority"] is True
        if ".bootstrap." in row["parentage_id"]:
            assert row.get("source_refs")
            assert all(link["kind"] == "recognized_legal" for link in row["parent_links"])
        else:
            assert row.get("provenance_note")
            assert all(link["kind"] in {"biological", "adoptive", "recognized_legal"} for link in row["parent_links"])
        assert "occurred_at" not in row


def test_accepted_npc_proposal_can_continue_but_player_process_cannot(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    family_path = root / "state/family/index.json"
    family = json.loads(family_path.read_text())
    proposal_id = "family.proposal.test.npc_marriage"
    proposal_path = f"state/family/proposals/{proposal_id}.json"
    proposal = {
        "schema":"family-proposal","proposal_id":proposal_id,"kind":"marriage_proposal",
        "proposer_id":"canon_hayate","target_id":"canon_yugao","status":"accepted","authority":True,
        "proposed_at":"SE-0061-06-01T07:00:00","player_choice_required":False,
    }
    target = root / proposal_path; target.parent.mkdir(parents=True, exist_ok=True); target.write_text(json.dumps(proposal, indent=2)+"\n")
    family["proposals"][proposal_id] = proposal_path; family["counts"]["proposals"] = len(family["proposals"])
    for ref in ("canon_hayate","canon_yugao"):
        family["person_index"].setdefault(ref, _family_entry())["proposals"].append(proposal_id)
    family["autonomy_queue_refs"] = [proposal_id]
    family_path.write_text(json.dumps(family, indent=2)+"\n")

    repo = RepositoryStore(root); planner = CampaignCommandPlanner(repo); writes: dict[str, dict] = {}
    result = planner._settle_one_autonomous_npc_family_process(
        at=CampaignTime.parse(repo.read_json("state/meta.json")["time"]), command=_command(repo,"npc-family"),
        world_events=copy.deepcopy(planner._world_events()), record_writes=writes,
    )
    assert result["status"] == "settled"
    union = writes[f"state/family/unions/{result['record_ref']}.json"]
    assert union["status"] == "married" and set(union["participants"]) == {"canon_hayate","canon_yugao"}
    assert writes["state/family/index.json"]["autonomy_queue_refs"] == []
    assert "canon_yugao" in writes["state/family/kinship-index.json"]["person_links"]["canon_hayate"]["spouses"]
    _validate_records(repo, writes)

    # A player-involving process is explicitly protected even if a fixture marks it accepted.
    player_id = "family.proposal.test.player"
    player_path = f"state/family/proposals/{player_id}.json"
    player = dict(proposal, proposal_id=player_id, proposer_id="pc_wei_tang", target_id="canon_yugao", player_choice_required=True)
    (root / player_path).write_text(json.dumps(player, indent=2)+"\n")
    family = json.loads(family_path.read_text()); family["proposals"][player_id]=player_path; family["autonomy_queue_refs"]=[player_id]
    family_path.write_text(json.dumps(family, indent=2)+"\n")
    repo = RepositoryStore(root); planner = CampaignCommandPlanner(repo); staged: dict[str, dict] = {}
    protected = planner._settle_one_autonomous_npc_family_process(
        at=CampaignTime.parse(repo.read_json("state/meta.json")["time"]), command=_command(repo,"player-family"),
        world_events=copy.deepcopy(planner._world_events()), record_writes=staged,
    )
    assert protected["status"] == "protected_or_ineligible"
    assert not any(path.startswith("state/family/unions/family.union.autonomy") for path in staged)


def test_delivered_institutional_operation_creates_audience_specific_reputation(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path); repo = RepositoryStore(root); planner = CampaignCommandPlanner(repo)
    at = CampaignTime.parse(repo.read_json("state/meta.json")["time"])
    program = planner._institutional_program("faction.fire_daimyo_liaison")
    template = next(row for row in program["operation_templates"] if row["id"] == "daimyo.border_coordination")
    op = planner._operation_spec_from_template(
        faction_id="faction.fire_daimyo_liaison", actor="support.daimyo.noboru_shimizu", at=at, template=template,
    )
    op["status"] = "active"; op["progress_milli"] = 700; op["next_due_at"] = str(at)
    writes: dict[str, dict] = {}
    planner._write_world_operation(op, record_writes=writes)
    queue = planner._operation_queue("faction.fire_daimyo_liaison", at=at, record_writes=writes)
    queue["pending_refs"] = [x for x in queue["pending_refs"] if x != op["operation_id"]]
    if op["operation_id"] not in queue["active_refs"]: queue["active_refs"].append(op["operation_id"])
    result = planner._settle_one_world_operation(
        faction_id="faction.fire_daimyo_liaison", at=at, command=_command(repo,"reputation"),
        world_events=copy.deepcopy(planner._world_events()), record_writes=writes,
    )
    assert result and result["status"] == "succeeded"
    rep_index = writes["state/reputation/index.json"]
    subject_path = rep_index["subjects"]["faction.fire_daimyo_liaison"]
    subject = writes[subject_path]
    profile_path = subject["audience_profiles"]["faction.fire_border_authority"]
    profile = writes[profile_path]
    assert profile["evidence_count"] >= 1
    assert profile["dimensions"]["mission_reliability"]["score"] > 50
    _validate_records(repo, writes)
