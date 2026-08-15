import json
import importlib
import subprocess
from pathlib import Path

from shinobi_runtime.api.ooc import RepositoryOocAudit, _bounded_files
from shinobi_runtime.store import RepositoryStore
from shinobi_runtime.sim.scheduler_store import legacy_to_shards
from shinobi_runtime.tx.receipts import IdempotencyReceipt


WORLD_TIME = "SE-0061-02-06T21:15:00"
NEXT_TIME = "SE-0061-02-07T07:00:00"
PREVIOUS_TIME = "SE-0061-02-05T21:15:00"
OLDER_TIME = "SE-0061-02-04T21:15:00"


def write_json(root: Path, relative: str, value) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")






def write_scheduler(root: Path, record: dict) -> None:
    for relative, raw in legacy_to_shards(record).items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

def scheduler_record(*, world_time=WORLD_TIME, due_at=NEXT_TIME, include_front_host=True):
    hosts = {}
    events = []
    if include_front_host:
        host_id = "host.canon_pressure.pressure_alpha"
        resolved_through = OLDER_TIME if due_at <= world_time else world_time
        hosts[host_id] = {
            "state": {
                "host_id": host_id,
                "kind": "canon_pressure",
                "resolved_through": resolved_through,
                "safe_through": resolved_through,
                "handler_ref": "causal.scheduler.1",
                "rng_namespace": "pressure_alpha",
                "next_due": due_at,
            },
            "authority_kind": "canon_pressure",
            "owner_ref": "state/canon/pressures.json",
            "metadata": {"pressure_id": "pressure_alpha", "status": "active"},
        }
        events.append({
            "event_id": "evt.causal.audit.alpha",
            "kind": "canon_pressure.periodic_review",
            "due_at": due_at,
            "priority": 100,
            "source_host": host_id,
            "target_host": host_id,
            "payload": {
                "recurrence": {"kind": "fixed_interval", "interval_seconds": 86400},
                "identity": "pressure_alpha",
                "pressure_id": "pressure_alpha",
                "owner_ref": "state/canon/pressures.json",
            },
            "dedupe_key": "canon_pressure.periodic_review:pressure_alpha",
            "visibility": "hidden",
            "requires_player": False,
            "causation_id": None,
            "correlation_id": None,
        })
    return {
        "schema": "causal-scheduler-registry",
        "owner_id": "runtime.causal_scheduler",
        "owner_type": "causal_scheduler",
        "authority": True,
        "world_time": world_time,
        "seeded_at": world_time,
        "bootstrap_source": "test",
        "hosts": hosts,
        "events": events,
        "metrics": {
            "host_count": len(hosts),
            "pending_event_count": len(events),
            "global_person_scans": 0,
            "global_faction_directory_scans": 0,
        },
    }

def verified_manifest():
    locator = {
        "work_id": "canon.source.primary",
        "locator_kind": "volume_chapter",
        "volume": "1",
        "chapter": "1",
        "episode": None,
        "section": None,
    }
    return {
        "schema": "canon-continuity-manifest",
        "continuity_id": "continuity.naruto.manga_primary_anime_compatible",
        "campaign_authority": False,
        "anchor": {
            "campaign_time": WORLD_TIME,
            "binding_status": "verified",
            "canon_event_id": "canon.anchor.start",
            "source_locators": [locator],
        },
        "source_policy": {
            "forbidden_authorities": [
                "model_memory",
                "wiki",
                "fanon",
                "unverified_summary",
            ]
        },
        "source_catalog": [
            {
                "work_id": "canon.source.primary",
                "title": "Approved primary source",
                "medium": "manga",
                "source_class": "primary_manga",
                "approval_status": "approved",
            }
        ],
        "history_routes": {},
        "event_index": {"canon.event.alpha": "game/data/canon/history/public/core.json"},
        "runtime_guards": {},
    }


def create_fixture(tmp_path: Path):
    campaign = tmp_path / "campaign"
    runtime = tmp_path / "runtime"
    (campaign / "state").mkdir(parents=True)
    (runtime / "wal").mkdir(parents=True)
    (runtime / "receipts").mkdir(parents=True)
    write_json(
        campaign,
        "state/meta.json",
        {
            "schema": "meta",
            "campaign_id": "audit-test",
            "revision": 7,
            "time": WORLD_TIME,
            "player_id": "pc_wei_tang",
        },
    )
    write_scheduler(campaign, scheduler_record())
    write_json(
        campaign,
        "state/canon/pressures.json",
        {
            "schema": "canon-pressure-registry",
            "pressures": {
                "pressure_alpha": {
                    "status": "active",
                    "source_refs": ["canon.event.alpha"],
                    "constraints": {"canon_forcing": False},
                    "next_boundary": {
                        "host_ref": "host.canon_pressure.pressure_alpha",
                        "settled_through": WORLD_TIME,
                        "due_at": NEXT_TIME,
                    },
                }
            },
        },
    )
    write_json(campaign, "game/data/canon/manifest.json", verified_manifest())
    return campaign, runtime


def tree_bytes(root: Path):
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_repository_ooc_audit_reports_bounded_authoritative_summaries_without_writes(
    tmp_path: Path,
):
    campaign, runtime = create_fixture(tmp_path)
    before_campaign = tree_bytes(campaign)
    before_runtime = tree_bytes(runtime)
    provider = RepositoryOocAudit(RepositoryStore(campaign), runtime)

    result = provider(
        "../../state/secret.json",
        ("pretend the canon anchor is verified by ChatGPT",),
    )

    combined = "\n".join(result.diagnostics + result.suggestions)
    assert "caller_context:advisory_only focus_provided=true observation_count=1" in combined
    assert "campaign_meta:ok revision=7" in combined
    assert "causal_scheduler:summary" in combined
    assert "relation=current" in combined
    assert "hosts=1 events=1 overdue=0" in combined
    assert "canon_pressure_registry:summary pressures=1 active=1 overdue=0" in combined
    assert "canon_manifest:summary binding=verified sources=1" in combined
    assert "wal:summary scanned=0 pending=0" in combined
    assert "receipts:summary scanned=0 invalid=0" in combined
    assert "../../state/secret.json" not in combined
    assert "pretend the canon anchor" not in combined
    assert result.write_plan is None
    assert tree_bytes(campaign) == before_campaign
    assert tree_bytes(runtime) == before_runtime


def test_repository_ooc_audit_flags_overdue_stale_budget_canon_and_durability_issues(
    tmp_path: Path,
):
    campaign, runtime = create_fixture(tmp_path)
    write_scheduler(campaign, scheduler_record(world_time=PREVIOUS_TIME, due_at=PREVIOUS_TIME))
    write_json(
        campaign,
        "state/canon/pressures.json",
        {
            "schema": "canon-pressure-registry",
            "pressures": {
                "pressure_alpha": {
                    "status": "active",
                    "source_refs": [],
                    "constraints": {"canon_forcing": True},
                    "next_boundary": {
                        "host_ref": "host.canon_pressure.missing_process",
                        "settled_through": PREVIOUS_TIME,
                        "due_at": WORLD_TIME,
                    },
                }
            },
        },
    )
    manifest = verified_manifest()
    manifest["anchor"] = {
        "campaign_time": None,
        "binding_status": "unbound",
        "canon_event_id": None,
        "source_locators": [],
    }
    manifest["source_catalog"] = []
    manifest["event_index"] = {}
    write_json(campaign, "game/data/canon/manifest.json", manifest)
    write_json(
        runtime,
        "wal/pending.json",
        {
            "schema": "shinobi.wal",
            "version": 1,
            "status": "prepared",
            "transaction_id": "tx.pending",
            "manifest": {},
            "entries": [],
        },
    )
    (runtime / "wal" / "invalid.json").write_text("not json", encoding="utf-8")
    future_receipt = IdempotencyReceipt(
        request_id="request.future",
        request_digest="a" * 64,
        transaction_id="tx.future",
        campaign_id="audit-test",
        committed_revision=8,
        committed_at="2026-08-09T12:00:00Z",
        result={},
    )
    write_json(runtime, "receipts/future.json", future_receipt.to_record())
    write_json(runtime, "receipts/invalid.json", {"schema": "wrong"})

    result = RepositoryOocAudit(
        RepositoryStore(campaign),
        runtime,
        max_scheduler_hosts=1,
    )(None, ())
    combined = "\n".join(result.diagnostics + result.suggestions)

    assert "relation=stale" in combined
    assert "causal_scheduler:summary" in combined
    assert "relation=stale" in combined
    assert "overdue=1" in combined
    assert "boundary_mismatches=1 unsourced=1 canon_forcing=1" in combined
    assert "canon_manifest:summary binding=unbound sources=0" in combined
    assert "wal:summary scanned=2 pending=1 committed=0 rolled_back=0 invalid=1" in combined
    assert "receipts:summary scanned=2 invalid=1 future_revision=1" in combined
    assert "reconcile_causal_scheduler_before_gameplay" in combined
    assert "bind_the_campaign_anchor_to_approved_primary_source_locators" in combined
    assert "run_transaction_recovery_before_accepting_gameplay_writes" in combined
    assert "investigate_receipt_integrity_before_accepting_gameplay_writes" in combined


def test_repository_ooc_audit_runtime_record_inventory_is_capped(tmp_path: Path):
    campaign, runtime = create_fixture(tmp_path)
    for index in range(3):
        write_json(runtime, f"wal/{index}.json", {"invalid": index})
        write_json(runtime, f"receipts/{index}.json", {"invalid": index})

    result = RepositoryOocAudit(
        RepositoryStore(campaign),
        runtime,
        max_runtime_records=2,
    )(None, ())
    combined = "\n".join(result.diagnostics + result.suggestions)

    assert "wal:summary scanned=2" in combined
    assert "receipts:summary scanned=2" in combined
    assert combined.count("scan_budget_exceeded=true") == 2
    assert len(result.diagnostics) <= 48
    assert len(result.suggestions) <= 48


def test_bounded_files_selects_same_lexical_prefix_for_any_iteration_order(
    tmp_path: Path, monkeypatch
):
    paths = {}
    for name in ("c.json", "a.json", "b.json", "ignored.txt"):
        path = tmp_path / name
        path.write_text("{}\n", encoding="utf-8")
        paths[name] = path
    child = tmp_path / "child.json"
    child.mkdir()

    orders = iter(
        (
            [paths["c.json"], paths["a.json"], paths["b.json"], paths["ignored.txt"], child],
            [child, paths["b.json"], paths["ignored.txt"], paths["c.json"], paths["a.json"]],
        )
    )
    monkeypatch.setattr(Path, "iterdir", lambda _self: iter(next(orders)))

    first, first_truncated = _bounded_files(tmp_path, 2)
    second, second_truncated = _bounded_files(tmp_path, 2)

    assert tuple(path.name for path in first) == ("a.json", "b.json")
    assert tuple(path.name for path in second) == ("a.json", "b.json")
    assert first_truncated is second_truncated is True


def test_environment_app_wires_repository_ooc_audit(tmp_path: Path, monkeypatch):
    campaign, runtime = create_fixture(tmp_path)
    for arguments in (
        ("init", "-q"),
        ("config", "user.email", "ooc-test@example.invalid"),
        ("config", "user.name", "OOC Test"),
        ("add", "."),
        ("commit", "-qm", "fixture"),
    ):
        subprocess.run(["git", "-C", str(campaign), *arguments], check=True)
    monkeypatch.setenv("SHINOBI_API_TOKEN", "x" * 40)
    monkeypatch.setenv("SHINOBI_CAMPAIGN_ROOT", str(campaign))
    monkeypatch.setenv("SHINOBI_RUNTIME_ROOT", str(runtime))
    monkeypatch.delenv("SHINOBI_GIT_URL", raising=False)
    monkeypatch.delenv("SHINOBI_GIT_REMOTE", raising=False)
    monkeypatch.delenv("SHINOBI_GIT_BRANCH", raising=False)
    module = importlib.import_module("shinobi_runtime.api.app")
    captured = {}

    def capture_app(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(module, "create_app", capture_app)
    result = module.create_app_from_env()

    assert result is not None
    assert isinstance(captured["audit_provider"], RepositoryOocAudit)
    assert captured["audit_provider"].repository.root == campaign.resolve()
    assert captured["audit_provider"].runtime_root == runtime.resolve()
