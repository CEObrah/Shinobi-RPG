from __future__ import annotations

import json
from pathlib import Path
import shutil

from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store import RegisteredSchemaValidator, RegisteredTemplateValidator, RepositoryStore
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionPlanner


ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN_ID = "shinobi-wei-main"
SUBMITTED = "2026-08-15T00:00:00Z"
KONOHA_FORMATION = "formation.konoha.barrier.2"
SUNA_FORMATION = "formation.suna.intelligence.1"
CONFLICT_REF = "conflict.test.operational_battlefield"
FRONT_REF = "front.test.operational_battlefield"
BATTLEFIELD_REF = "battlefield.test.operational_battlefield"


def _copy_campaign(tmp_path: Path) -> Path:
    root = tmp_path / "campaign"
    shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".git", ".pytest_cache", "__pycache__", "*.pyc"))
    return root


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _formation(root: Path, registry_path: str, formation_ref: str) -> dict:
    registry = json.loads((root / registry_path).read_text(encoding="utf-8"))
    for row in registry["formations"]:
        if row.get("id") == formation_ref:
            return row
    raise AssertionError(formation_ref)


def _fixture_conflict(root: Path) -> None:
    # Put one opposing exact formation at the same physical anchor. The battle
    # opener must discover both from authoritative front membership/location;
    # the caller never supplies an enemy deployment.
    suna_path = root / "state/formation/force-suna-shinobi.json"
    suna = json.loads(suna_path.read_text(encoding="utf-8"))
    for row in suna["formations"]:
        if row.get("id") == SUNA_FORMATION:
            row["location_ref"] = "place.konoha"
            break
    else:
        raise AssertionError(SUNA_FORMATION)
    _write_json(suna_path, suna)

    registry = {
        "schema": "conflict-registry",
        "records": {
            CONFLICT_REF: {
                "id": CONFLICT_REF,
                "name": "Operational Battlefield Regression",
                "status": "active",
                "side_refs": ["faction_konoha", "faction_suna"],
                "objectives": {
                    "faction_konoha": ["hold Konoha"],
                    "faction_suna": ["break Konoha line"],
                },
                "started_at": RepositoryStore(root).read_json("state/meta.json")["time"],
                "ended_at": None,
                "ceasefire_consents": [],
                "end_consents": [],
                "fronts": {
                    FRONT_REF: {
                        "id": FRONT_REF,
                        "name": "Konoha Test Front",
                        "status": "active",
                        "place_refs": ["place.konoha"],
                        "route_refs": [],
                        "formation_refs": [KONOHA_FORMATION, SUNA_FORMATION],
                        "control_ref": None,
                        "fortification_milli": 0,
                        "route_state": {},
                        "occupations": {},
                    }
                },
            }
        },
    }
    _write_json(root / "state/conflict/registry.json", registry)


def _command(root: Path, *, actor: str, action: str, suffix: str, extra: dict) -> CommandEnvelope:
    meta = RepositoryStore(root).read_json("state/meta.json")
    payload = {
        "action": action,
        "conflict_ref": CONFLICT_REF,
        "front_ref": FRONT_REF,
        "battlefield_ref": BATTLEFIELD_REF,
        **extra,
    }
    return CommandEnvelope(
        campaign_id=CAMPAIGN_ID,
        request_id=f"battlefield-{suffix}-{meta['revision']}",
        actor_id=actor,
        command_type="battlefield_resolution",
        expected_revision=meta["revision"],
        submitted_at=SUBMITTED,
        payload=payload,
        mode="autonomous",
    )


def _validated_plan(root: Path, command: CommandEnvelope):
    repo = RepositoryStore(root)
    planner = RepositoryCommandPlanner(repo)
    plan = planner.plan(command)
    manifest = TransactionPlanner(repo).plan(
        command,
        transaction_id=plan.transaction_id,
        created_at=plan.created_at,
        writes=plan.writes,
    )
    overlay = StagedOverlay(repo, manifest)
    plan.validator(overlay, manifest)
    RegisteredSchemaValidator(repo).validate_overlay(overlay, manifest.paths)
    RegisteredTemplateValidator(repo).validate_overlay(overlay, manifest.paths)
    return plan, overlay


def _commit(root: Path, plan) -> None:
    for relative, payload in plan.writes.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


def _battlefield(root: Path) -> dict:
    registry = RepositoryStore(root).read_json("state/conflict/registry.json")
    return registry["records"][CONFLICT_REF]["fronts"][FRONT_REF]["battlefields"][BATTLEFIELD_REF]


def test_open_derives_both_sides_from_exact_front_state(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    _fixture_conflict(root)
    before_konoha = _formation(root, "state/formation/force-konoha-shinobi.json", KONOHA_FORMATION)["personnel_total"]
    before_suna = _formation(root, "state/formation/force-suna-shinobi.json", SUNA_FORMATION)["personnel_total"]

    plan, overlay = _validated_plan(
        root,
        _command(
            root,
            actor="canon_hiruzen",
            action="open",
            suffix="open",
            extra={
                "name": "Konoha Operational Battle",
                "place_ref": "place.konoha",
                "side_refs": ["faction_konoha", "faction_suna"],
                "layout_ref": "battlefield.layout.line_three",
            },
        ),
    )
    staged = overlay.read_json("state/conflict/registry.json")
    battlefield = staged["records"][CONFLICT_REF]["fronts"][FRONT_REF]["battlefields"][BATTLEFIELD_REF]
    assert set(battlefield["assignments"]) == {KONOHA_FORMATION, SUNA_FORMATION}
    assert {row["side_ref"] for row in battlefield["assignments"].values()} == {"faction_konoha", "faction_suna"}
    _commit(root, plan)

    # Operational setup is geometry/command state only. It cannot manufacture
    # casualties or alter conserved formation manpower.
    assert _formation(root, "state/formation/force-konoha-shinobi.json", KONOHA_FORMATION)["personnel_total"] == before_konoha
    assert _formation(root, "state/formation/force-suna-shinobi.json", SUNA_FORMATION)["personnel_total"] == before_suna


def test_remote_order_has_delivery_time_before_it_changes_sector_behavior(tmp_path: Path) -> None:
    root = _copy_campaign(tmp_path)
    _fixture_conflict(root)
    open_plan, _ = _validated_plan(
        root,
        _command(
            root,
            actor="canon_hiruzen",
            action="open",
            suffix="open",
            extra={
                "name": "Konoha Operational Battle",
                "place_ref": "place.konoha",
                "side_refs": ["faction_konoha", "faction_suna"],
                "layout_ref": "battlefield.layout.line_three",
            },
        ),
    )
    _commit(root, open_plan)

    order_plan, order_overlay = _validated_plan(
        root,
        _command(
            root,
            actor="canon_hiruzen",
            action="set_order",
            suffix="order",
            extra={"formation_ref": KONOHA_FORMATION, "order": "attack"},
        ),
    )
    staged = order_overlay.read_json("state/conflict/registry.json")
    assignment = staged["records"][CONFLICT_REF]["fronts"][FRONT_REF]["battlefields"][BATTLEFIELD_REF]["assignments"][KONOHA_FORMATION]
    assert assignment["order"] != "attack"
    assert assignment["pending_order"] == "attack"
    assert isinstance(assignment["command_eta_at"], str)
    _commit(root, order_plan)

    registry = RepositoryStore(root).read_json("state/conflict/registry.json")
    battlefield = _battlefield(root)
    start = CampaignTime.parse(battlefield["last_settled_at"])
    eta = CampaignTime.parse(battlefield["assignments"][KONOHA_FORMATION]["command_eta_at"])
    planner = RepositoryCommandPlanner(RepositoryStore(root))
    result = planner._settle_battlefields(
        registry,
        actor_ref="pc_wei_tang",
        start_time=start,
        end_time=eta.add_seconds(60),
    )
    settled = registry["records"][CONFLICT_REF]["fronts"][FRONT_REF]["battlefields"][BATTLEFIELD_REF]["assignments"][KONOHA_FORMATION]
    assert result["changed"] is True
    assert settled["pending_order"] is None
    assert settled["command_eta_at"] is None
    assert settled["order"] == "attack"
