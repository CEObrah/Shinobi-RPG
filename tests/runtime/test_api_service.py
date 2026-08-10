import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from shinobi_runtime.api.app import ServiceSettings, create_app, create_app_from_env
from shinobi_runtime.api.contracts import CommandPlan, CommandPreview, OocAuditResult
from shinobi_runtime.api.operations import CampaignOperations
from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.commands.mission_owner import MissionOwner
from shinobi_runtime.reducers import Mission, MissionObjective
from shinobi_runtime.sim import CampaignTime
from shinobi_runtime.store import RepositoryStore, content_root
from shinobi_runtime.tx import GitStager, ReceiptStore, TransactionCoordinator, WriteAheadLog


TOKEN = "test-token-that-is-more-than-thirty-two-characters"


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root)] + list(arguments),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def meta_bytes(revision: int) -> bytes:
    return (
        json.dumps(
            {
                "schema": "meta",
                "campaign_id": "api-test",
                "revision": revision,
                "time": "SE-0061-02-06T21:15:00",
                "player_id": "pc_wei_tang",
            },
            indent=2,
        ).encode("utf-8")
        + b"\n"
    )


def mission_record(mission_id: str, participant_refs: tuple[str, ...]) -> dict:
    return dict(
        MissionOwner(
            mission=Mission(
                mission_id=mission_id,
                state="offered",
                participant_refs=participant_refs,
                objectives=(
                    MissionObjective(
                        objective_id="obj.first",
                        kind="identify",
                        required=True,
                    ),
                ),
            ),
            issuer_ref="faction.test",
            authority_ref="authority.test",
            mission_rank="D",
            funding_holder_ref="faction.test",
            escrow_holder_ref=None,
            opened_at=CampaignTime.parse("SE-0061-02-06T21:15:00"),
            authorized_at=CampaignTime.parse("SE-0061-02-06T21:15:00"),
            starts_at=None,
            deadline_at=CampaignTime.parse("SE-0061-02-09T00:00:00"),
            next_due_at=CampaignTime.parse("SE-0061-02-08T00:00:00"),
            operation_ref=None,
            closed_at=None,
        ).to_record()
    )


class FakePlanner:
    def preview(self, command):
        return CommandPreview(
            status="ready",
            code="command_valid",
            target_revision=command.expected_revision + 1,
            affected_refs=("owner.scene",),
        )

    def plan(self, command):
        def validate(overlay, manifest):
            assert overlay.read_json("state/meta.json")["revision"] == 2
            assert overlay.read_json("state/owner.json")["status"] == "after"

        return CommandPlan(
            transaction_id="tx." + command.request_id,
            created_at="2026-08-09T12:00:01Z",
            writes={
                "state/meta.json": meta_bytes(command.expected_revision + 1),
                "state/owner.json": b'{"status":"after","ratio":1.25}\n',
            },
            result={"status": "committed", "visible": ["one minute passes"]},
            validator=validate,
        )


class AuditProvider:
    def __init__(self):
        self.include_write_plan = False

    def __call__(self, focus, observations):
        return OocAuditResult(
            diagnostics=("no_persistence_error",),
            suggestions=("continue_vertical_slice",),
            write_plan=(
                {"writes": {"state/meta.json": "forbidden"}}
                if self.include_write_plan
                else None
            ),
        )


def make_service(tmp_path: Path):
    campaign = tmp_path / "campaign"
    runtime = tmp_path / "runtime"
    (campaign / "state").mkdir(parents=True)
    (campaign / "state" / "meta.json").write_bytes(meta_bytes(1))
    (campaign / "state" / "owner.json").write_bytes(
        b'{"status":"before","ratio":1.25}\n'
    )
    (campaign / "state" / "scene.json").write_text(
        json.dumps(
            {
                "schema": "scene",
                "scene_id": "scene.api-test",
                "world_time": "SE-0061-02-06T21:15:00",
                "location_id": "place.test",
                "active_combat": False,
                "time_passage_allowed": True,
                "freeform_actions_allowed": True,
                "loaded_owner_ids": [f"person.test.{index}" for index in range(20)],
                "known_clock_boundaries": [],
                "observable_pressures": ["A deadline is visible."],
                "scene_summary": "A bounded test scene.",
                "decision_required": "Choose the next action.",
                "narrative": {
                    "current_scene_type": "institutional_command",
                    "current_tension": "deadline",
                    "known_clues": ["One player-known clue."],
                    "active_npc_agendas": ["must remain hidden"],
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (campaign / "state" / "player.json").write_text(
        json.dumps(
            {
                "schema": "person",
                "owner_id": "pc_wei_tang",
                "name": "Wei Tang",
                "official_rank_or_status": "Jonin",
                "current_location_id": "place.test",
                "current_assignment_ref": "formation.test.command",
                "condition": {"readiness": "ready"},
                "resources": {"chakra": {"current": 10, "capacity": 10}},
                "roles": ["player_character"],
                "goals": [],
                "goal_state": {"current_goals": []},
                "career_state": {"rank": "Jonin"},
                "player_choice_protection": {"dialogue": True},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (campaign / "state" / "index" / "owners").mkdir(parents=True)
    (campaign / "state" / "house").mkdir(parents=True)
    (campaign / "state" / "index" / "owners.json").write_text(
        json.dumps(
            {
                "schema": "owner-index-root",
                "prefix_index": {
                    "house": "state/index/owners/house.json",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (campaign / "state" / "index" / "owners" / "house.json").write_text(
        json.dumps(
            {
                "schema": "owner-index-shard",
                "owners": {"house.test": "state/house/test.json"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (campaign / "state" / "house" / "test.json").write_text(
        json.dumps(
            {
                "schema": "house",
                "id": "house.test",
                "member_ids": ["pc_wei_tang", "person.test.0"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (campaign / "state" / "reg").mkdir(parents=True, exist_ok=True)
    (campaign / "state" / "reg" / "jinchuriki.json").write_text(
        json.dumps({"schema": "jinchuriki-registry", "records": []}) + "\n",
        encoding="utf-8",
    )
    (campaign / "state" / "reg" / "puppets.json").write_text(
        json.dumps({"schema": "puppet-registry", "puppets": []}) + "\n",
        encoding="utf-8",
    )
    (campaign / "state" / "reg" / "summons.json").write_text(
        json.dumps({"schema": "summon-registry", "profiles": {}}) + "\n",
        encoding="utf-8",
    )
    (campaign / "state" / "mission").mkdir(parents=True)
    (campaign / "state" / "mission" / "mission.player.json").write_text(
        json.dumps(mission_record("mission.player", ("pc_wei_tang",))) + "\n",
        encoding="utf-8",
    )
    (campaign / "state" / "mission" / "mission.hidden.json").write_text(
        json.dumps(mission_record("mission.hidden", ("person.hidden",))) + "\n",
        encoding="utf-8",
    )
    (campaign / "runtime" / "contracts").mkdir(parents=True)
    (campaign / "runtime" / "contracts" / "narration-router.json").write_text(
        json.dumps(
            {
                "schema": "narration-router",
                "authority": False,
                "default_primary": "social_village_institution",
                "pressure_gated_modules": ["command_large_war"],
                "scene_type_primary": {
                    "institutional_command": "social_village_institution"
                },
                "pressure_primary_overrides": {
                    "large_force_command": "command_large_war"
                },
                "modules": {
                    "social_village_institution": "runtime/contracts/social.md",
                    "command_large_war": "runtime/contracts/command.md",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (campaign / "runtime" / "contracts" / "social.md").write_text(
        "Keep institutional dialogue grounded.\n",
        encoding="utf-8",
    )
    (campaign / "runtime" / "contracts" / "command.md").write_text(
        "Use only for causal large-force command.\n",
        encoding="utf-8",
    )
    git(campaign.parent, "init", "-q", str(campaign))
    git(campaign, "config", "user.email", "runtime@example.invalid")
    git(campaign, "config", "user.name", "Runtime Test")
    git(campaign, "add", "state", "runtime")
    git(campaign, "commit", "-qm", "baseline")

    repository = RepositoryStore(campaign)
    stager = GitStager(campaign)
    coordinator = TransactionCoordinator(
        repository,
        stager,
        WriteAheadLog(runtime / "wal"),
        ReceiptStore(runtime / "receipts"),
        lock_path=runtime / "writer.lock",
    )
    audit = AuditProvider()

    def sheet_resolver(person_id):
        if person_id not in ("pc_wei_tang", "person.test.0", "person.hidden"):
            return None
        return {
            "core": {
                "person_id": person_id,
                "display_name": (
                    "Wei Tang" if person_id == "pc_wei_tang" else "Test Person"
                ),
                "representation": "exact",
                "legacy_ratio": 1.25,
                "roles": ["visible_role"],
            },
            "cohort_baseline": {},
            "components": {"profile.exact": {"secret_method": "must_not_leak"}},
        }

    app = create_app(
        repository=repository,
        coordinator=coordinator,
        command_planner=FakePlanner(),
        sheet_resolver=sheet_resolver,
        audit_provider=audit,
        settings=ServiceSettings(
            auth_token=TOKEN,
            allowed_actor_ids=frozenset(("pc_wei_tang",)),
        ),
    )
    return (
        campaign,
        repository,
        stager,
        coordinator,
        audit,
        TestClient(app),
    )


def headers():
    return {"Authorization": "Bearer " + TOKEN}


def command_body():
    return {
        "campaign_id": "api-test",
        "request_id": "request-api-001",
        "actor_id": "pc_wei_tang",
        "command_type": "wait",
        "expected_revision": 1,
        "submitted_at": "2026-08-09T12:00:00Z",
        "payload": {"duration_seconds": 60},
        "mode": "gameplay",
    }


def test_health_is_public_and_every_campaign_route_requires_bearer(tmp_path: Path):
    campaign, repository, stager, coordinator, audit, client = make_service(tmp_path)
    assert client.get("/health").json() == {"status": "ok"}
    unauthenticated = client.get("/v1/campaign")
    assert unauthenticated.status_code == 401
    assert unauthenticated.headers["www-authenticate"] == "Bearer"
    assert client.get(
        "/v1/campaign",
        headers={"Authorization": "Bearer incorrect-token-value"},
    ).status_code == 401
    assert client.get("/v1/play/context").status_code == 401

    response = client.get("/v1/campaign", headers=headers())
    assert response.status_code == 200
    assert response.json() == {
        "campaign_id": "api-test",
        "revision": 1,
        "world_time": "SE-0061-02-06T21:15:00",
        "state_root": content_root(campaign).root_sha256,
    }
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_play_context_is_bounded_player_visible_and_read_only(tmp_path: Path):
    campaign, repository, stager, coordinator, audit, client = make_service(tmp_path)
    before_head = stager.head()
    before_root = content_root(campaign, include_roots=("state",)).root_sha256

    response = client.get("/v1/play/context", headers=headers())

    assert response.status_code == 200
    context = response.json()
    assert context["campaign"]["revision"] == 1
    assert context["campaign"]["player_id"] == "pc_wei_tang"
    assert context["scene"]["scene_summary"] == "A bounded test scene."
    assert context["player"]["name"] == "Wei Tang"
    assert context["person_reads"]["suggested_owner_ids"] == [
        "pc_wei_tang",
        "person.test.0",
    ]
    assert context["person_reads"]["total_permitted_ids"] == 2
    assert context["person_reads"]["suggested_ids_truncated"] is False
    assert context["person_reads"]["nonplayer_view"] == (
        "player_visible_identity_only"
    )
    assert context["narration"]["primary_module_id"] == (
        "social_village_institution"
    )
    assert context["commands"]["active_mission_owner_ids"] == ["mission.player"]
    assert len(context["narration"]["modules"]) == 1
    assert context["scene"]["causal_refs"] == []
    assert context["context_policy"][
        "loaded_owner_ids_are_internal_not_player_visibility"
    ] is True
    assert "active_npc_agendas" not in context["scene"]["narrative"]
    assert stager.head() == before_head
    assert content_root(campaign, include_roots=("state",)).root_sha256 == before_root
    stager.assert_pristine()


def test_person_sheet_resolver_is_bounded_and_fails_closed(tmp_path: Path):
    campaign, repository, stager, coordinator, audit, client = make_service(tmp_path)
    response = client.get("/v1/person/pc_wei_tang/sheet", headers=headers())
    assert response.status_code == 200
    assert response.json()["view"] == "player_full_logical_sheet"
    assert response.json()["sheet"]["core"]["display_name"] == "Wei Tang"
    assert response.json()["sheet"]["core"]["legacy_ratio"] == 1.25
    visible = client.get("/v1/person/person.test.0/sheet", headers=headers())
    assert visible.status_code == 200
    assert visible.json()["view"] == "player_visible_identity"
    assert visible.json()["sheet"] == {
        "view": "player_visible_identity",
        "core": {
            "person_id": "person.test.0",
            "display_name": "Test Person",
            "roles": ["visible_role"],
        },
    }
    assert "secret_method" not in json.dumps(visible.json())
    hidden = client.get("/v1/person/person.hidden/sheet", headers=headers())
    assert hidden.status_code == 404
    assert hidden.json()["detail"]["code"] == "person_not_player_visible"
    unresolved = client.get("/v1/person/npc_unknown/sheet", headers=headers())
    assert unresolved.status_code == 404
    assert unresolved.json()["detail"]["code"] == "person_not_player_visible"
    assert client.get("/v1/person/BAD_ID/sheet", headers=headers()).status_code == 422
    assert client.get("/v1/files/state.meta", headers=headers()).status_code == 404


def test_player_house_person_access_handles_hundreds_without_owner_scan(
    tmp_path: Path,
):
    member_ids = ["pc_wei_tang", *[f"ht.core.{index:03d}" for index in range(200)]]
    (tmp_path / "state/index/owners").mkdir(parents=True)
    (tmp_path / "state/house").mkdir(parents=True)
    (tmp_path / "state/index/owners.json").write_text(
        json.dumps(
            {
                "schema": "owner-index-root",
                "prefix_index": {"house": "state/index/owners/house.json"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "state/index/owners/house.json").write_text(
        json.dumps(
            {
                "schema": "owner-index-shard",
                "owners": {"house.tang": "state/house/tang.json"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "state/house/tang.json").write_text(
        json.dumps(
            {
                "schema": "house",
                "id": "house.tang",
                "member_ids": member_ids,
            }
        ),
        encoding="utf-8",
    )
    operations = CampaignOperations.__new__(CampaignOperations)
    operations.repository = RepositoryStore(tmp_path)

    permitted = operations._permitted_person_lookup_ids(
        player_id="pc_wei_tang",
    )

    assert len(permitted) == 201
    assert "ht.core.199" in permitted
    assert "canon_hiruzen" not in permitted


def test_preview_is_strict_bounded_and_read_only(tmp_path: Path):
    campaign, repository, stager, coordinator, audit, client = make_service(tmp_path)
    original_head = stager.head()
    original_root = content_root(campaign).root_sha256
    response = client.post(
        "/v1/commands/preview",
        headers=headers(),
        json=command_body(),
    )
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "code": "command_valid",
        "target_revision": 2,
        "affected_refs": ["owner.scene"],
    }
    assert stager.head() == original_head
    assert content_root(campaign).root_sha256 == original_root
    stager.assert_pristine()

    extra = {**command_body(), "raw_path": "state/meta.json"}
    assert client.post(
        "/v1/commands/preview", headers=headers(), json=extra
    ).status_code == 422
    floating = {**command_body(), "payload": {"unsafe": 0.1}}
    assert client.post(
        "/v1/commands/preview", headers=headers(), json=floating
    ).status_code == 422
    oversized = {**command_body(), "payload": {"text": "x" * (140 * 1024)}}
    too_large = client.post(
        "/v1/commands/preview",
        headers=headers(),
        json=oversized,
    )
    assert too_large.status_code == 413
    assert too_large.json()["detail"]["code"] == "body_too_large"


@pytest.mark.parametrize("route", ("preview", "execute"))
def test_command_routes_reject_cross_campaign_envelopes(tmp_path: Path, route: str):
    campaign, repository, stager, coordinator, audit, client = make_service(tmp_path)
    body = {**command_body(), "campaign_id": "different-campaign"}
    response = client.post(
        f"/v1/commands/{route}",
        headers=headers(),
        json=body,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "campaign_mismatch"
    assert repository.current_revision() == 1
    stager.assert_pristine()


def test_execute_returns_only_committed_or_duplicate_receipt(tmp_path: Path):
    campaign, repository, stager, coordinator, audit, client = make_service(tmp_path)
    first = client.post(
        "/v1/commands/execute",
        headers=headers(),
        json=command_body(),
    )
    assert first.status_code == 200
    assert first.json()["status"] == "committed"
    assert first.json()["transaction_id"] == "tx.request-api-001"
    assert first.json()["committed_revision"] == 2
    assert "commit_hash" not in first.json()
    assert "writes" not in first.json()
    assert repository.current_revision() == 2
    assert repository.read_json("state/owner.json")["status"] == "after"
    recovered = client.app.state.campaign_operations.lookup_command_receipt(
        CommandEnvelope(**command_body())
    )
    assert recovered is not None
    assert recovered["status"] == "duplicate"
    assert recovered["transaction_id"] == first.json()["transaction_id"]

    duplicate = client.post(
        "/v1/commands/execute",
        headers=headers(),
        json=command_body(),
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "duplicate"
    assert duplicate.json()["transaction_id"] == first.json()["transaction_id"]
    assert duplicate.json()["result"] == first.json()["result"]
    assert git(campaign, "rev-list", "--count", "HEAD") == "2"
    stager.assert_pristine()


def test_ooc_audit_returns_diagnostics_and_rejects_write_plan(tmp_path: Path):
    campaign, repository, stager, coordinator, audit, client = make_service(tmp_path)
    original_root = content_root(campaign).root_sha256
    clean = client.post(
        "/v1/ooc/audit",
        headers=headers(),
        json={"focus": "mission pacing", "observations": ["quiet opening"]},
    )
    assert clean.status_code == 200
    assert clean.json() == {
        "diagnostics": ["no_persistence_error"],
        "suggestions": ["continue_vertical_slice"],
    }
    assert content_root(campaign).root_sha256 == original_root

    audit.include_write_plan = True
    rejected = client.post(
        "/v1/ooc/audit",
        headers=headers(),
        json={"focus": "write something", "observations": []},
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "ooc_write_plan_rejected"
    assert content_root(campaign).root_sha256 == original_root
    assert repository.current_revision() == 1
    stager.assert_pristine()


def test_core_package_import_does_not_eagerly_require_fastapi(tmp_path: Path):
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "runtime")
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, shinobi_runtime; assert 'fastapi' not in sys.modules",
        ],
        env=environment,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    blocked = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import builtins\n"
                "real_import = builtins.__import__\n"
                "def guarded(name, *args, **kwargs):\n"
                "    if name.split('.')[0] in {'fastapi', 'pydantic', 'starlette'}:\n"
                "        raise ModuleNotFoundError(name=name)\n"
                "    return real_import(name, *args, **kwargs)\n"
                "builtins.__import__ = guarded\n"
                "import shinobi_runtime\n"
                "import shinobi_runtime.api as api\n"
                "try:\n"
                "    api.create_app()\n"
                "except api.ServiceDependencyError:\n"
                "    pass\n"
                "else:\n"
                "    raise AssertionError('missing optional dependency was not reported')\n"
            ),
        ],
        env=environment,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert blocked.returncode == 0, blocked.stderr


def test_environment_factory_recovers_before_serving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign, repository, stager, coordinator, audit, client = make_service(tmp_path)
    calls = []
    original_recover = TransactionCoordinator.recover

    def tracked_recover(self):
        calls.append(self.repository.root)
        return original_recover(self)

    monkeypatch.setattr(TransactionCoordinator, "recover", tracked_recover)
    monkeypatch.setenv("SHINOBI_CAMPAIGN_ROOT", str(campaign))
    monkeypatch.setenv("SHINOBI_RUNTIME_ROOT", str(coordinator.wal.directory.parent))
    monkeypatch.setenv("SHINOBI_API_TOKEN", TOKEN)
    for name in (
        "SHINOBI_GIT_URL",
        "SHINOBI_GIT_REMOTE",
        "SHINOBI_GIT_BRANCH",
        "SHINOBI_GIT_TOKEN",
        "GIT_ASKPASS",
        "GIT_ASKPASS_REQUIRE",
    ):
        monkeypatch.delenv(name, raising=False)

    app = create_app_from_env()

    assert app is not None
    assert calls == [campaign.resolve()]
