import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _json_bytes
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.store.repository import RepositoryStore

ROOT = Path(__file__).resolve().parents[2]


class _FakeRepository:
    def __init__(self):
        self.files = {
            "state/meta.json": _json_bytes({
                "schema": "meta",
                "campaign_id": "test-campaign",
                "revision": 1,
                "time": "SE-0061-01-01T00:00:00",
                "game": "jianghu",
                "player_id": "pc.test",
            })
        }

    def campaign_id(self, meta_path):
        return self.read_json(meta_path)["campaign_id"]

    def require_campaign(self, campaign_id, meta_path):
        if self.campaign_id(meta_path) != campaign_id:
            raise ValueError("campaign mismatch")

    def require_revision(self, revision, meta_path):
        if self.read_json(meta_path)["revision"] != revision:
            raise ValueError("revision mismatch")

    def read_optional_bytes(self, path):
        return self.files.get(str(path))

    def read_bytes(self, path):
        raw = self.read_optional_bytes(path)
        if raw is None:
            raise FileNotFoundError(str(path))
        return raw

    def read_json(self, path):
        return json.loads(self.read_bytes(path).decode("utf-8"))

    def digest(self, path):
        raw = self.read_optional_bytes(path)
        return None if raw is None else hashlib.sha256(raw).hexdigest()


def _command():
    return CommandEnvelope(
        campaign_id="test-campaign",
        request_id="request.preview-validation",
        actor_id="pc.test",
        command_type="advance_time",
        expected_revision=1,
        submitted_at="2026-08-22T00:00:00Z",
        payload={"target_time": "SE-0061-01-02T00:00:00"},
    )


def _built(validator):
    writes = {
        "state/meta.json": _json_bytes({
            "schema": "meta",
            "campaign_id": "test-campaign",
            "revision": 2,
            "time": "SE-0061-01-02T00:00:00",
            "game": "jianghu",
            "player_id": "pc.test",
        })
    }
    return _BuiltPlan(
        code="time_advanced",
        affected_refs=("state/meta.json",),
        writes=writes,
        result={"world_time": "SE-0061-01-02T00:00:00"},
        validator=validator,
    )


def _future_campaign_time(meta, *, days=31):
    current = datetime.fromisoformat(str(meta["time"]).removeprefix("SE-"))
    return "SE-" + (current + timedelta(days=days)).isoformat()


def test_preview_runs_staged_transaction_validator_and_fails_closed():
    repository = _FakeRepository()
    planner = RepositoryCommandPlanner(repository)
    planner._build = lambda _command: _built(
        lambda _overlay, _manifest: (_ for _ in ()).throw(ValueError("invalid after-image"))
    )

    with pytest.raises(CommandRejectedError) as caught:
        planner.preview(_command())

    assert caught.value.code == "transaction_rejected"


def test_preview_staged_validation_is_read_only():
    repository = _FakeRepository()
    before = dict(repository.files)
    planner = RepositoryCommandPlanner(repository)

    def validate(overlay, manifest):
        assert manifest.base_revision == 1
        assert manifest.target_revision == 2
        assert overlay.read_json("state/meta.json")["revision"] == 2

    planner._build = lambda _command: _built(validate)
    preview = planner.preview(_command())

    assert preview.status == "ready"
    assert preview.target_revision == 2
    assert repository.files == before


def test_real_campaign_monthly_advance_preview_is_transaction_valid_and_read_only():
    repository = RepositoryStore(ROOT)
    planner = RepositoryCommandPlanner(repository)
    meta = repository.read_json("state/meta.json")
    before = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (ROOT / "state").rglob("*.json")
    }
    command = CommandEnvelope(
        campaign_id=meta["campaign_id"],
        request_id="request.monthly-preview-regression",
        actor_id=meta["player_id"],
        command_type="advance_time",
        expected_revision=meta["revision"],
        submitted_at="2026-08-22T00:00:00Z",
        payload={"target_time": _future_campaign_time(meta)},
    )

    preview = planner.preview(command)

    after = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (ROOT / "state").rglob("*.json")
    }
    assert preview.status == "ready"
    assert preview.target_revision == meta["revision"] + 1
    assert after == before


def test_real_campaign_retinue_request_preview_is_valid_zero_time_and_read_only():
    repository = RepositoryStore(ROOT)
    planner = RepositoryCommandPlanner(repository)
    meta_before = repository.read_bytes("state/meta.json")
    deployments_before = repository.read_bytes("state/martial-world/deployments.json")
    scheduler_before = repository.read_bytes("state/martial-world/scheduler.json")
    meta = json.loads(meta_before.decode("utf-8"))
    command = CommandEnvelope(
        campaign_id=meta["campaign_id"],
        request_id="request.retinue-preview-regression",
        actor_id=meta["player_id"],
        command_type="jianghu_retinue_resolution",
        expected_revision=meta["revision"],
        submitted_at="2026-08-22T00:00:00Z",
        payload={
            "action": "request",
            "retinue_ref": "retinue.preview.regression",
            "chooser_ref": "char.zhu",
            "requested_count": 3,
        },
    )

    preview = planner.preview(command)

    assert preview.status == "ready"
    assert preview.target_revision == meta["revision"] + 1
    assert repository.read_bytes("state/meta.json") == meta_before
    assert repository.read_bytes("state/martial-world/deployments.json") == deployments_before
    assert repository.read_bytes("state/martial-world/scheduler.json") == scheduler_before
