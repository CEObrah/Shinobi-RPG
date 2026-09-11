from __future__ import annotations

import base64
import hashlib
import json
from contextlib import nullcontext
from pathlib import Path

import pytest

from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.api.repair import CampaignRepairService, REPAIR_COMMAND_TYPE
from shinobi_runtime.commands import CommandEnvelope


def _bytes(value):
    return (json.dumps(value, indent=2) + "\n").encode("utf-8")


def _sha(value):
    return None if value is None else hashlib.sha256(value).hexdigest()


def _b64(value):
    return None if value is None else base64.b64encode(value).decode("ascii")


def _entry(path, before, after):
    return {
        "path": path,
        "before_sha256": _sha(before),
        "after_sha256": _sha(after),
        "before_b64": _b64(before),
        "after_b64": _b64(after),
    }


def _record(campaign_id, base, target, tx, entries):
    return {
        "status": "committed",
        "transaction_id": tx,
        "manifest": {
            "transaction_id": tx,
            "campaign_id": campaign_id,
            "request_id": f"req-{target}",
            "command_digest": "d" * 64,
            "mode": "gameplay",
            "base_revision": base,
            "target_revision": target,
            "created_at": f"2026-08-30T00:00:{target:02d}Z",
            "mutations": [
                {"path": row["path"], "before_sha256": row["before_sha256"], "after_sha256": row["after_sha256"]}
                for row in entries
            ],
        },
        "entries": list(entries),
        "receipt": None,
    }


class _Repository:
    def __init__(self, root: Path, files):
        self.root = root
        self.files = dict(files)

    def read_optional_bytes(self, path):
        return self.files.get(str(path))

    def read_bytes(self, path):
        value = self.read_optional_bytes(path)
        if value is None:
            raise FileNotFoundError(str(path))
        return value

    def read_json(self, path):
        return json.loads(self.read_bytes(path).decode("utf-8"))

    def digest(self, path):
        return _sha(self.read_optional_bytes(path))

    def require_campaign(self, expected, path="state/meta.json"):
        if self.read_json(path).get("campaign_id") != expected:
            raise ValueError("campaign mismatch")
        return expected

    def require_revision(self, expected, path="state/meta.json"):
        from shinobi_runtime.tx.errors import StaleRevisionError
        actual = self.read_json(path).get("revision")
        if actual != expected:
            raise StaleRevisionError(expected, actual)
        return actual


class _Wal:
    def __init__(self, records):
        self._records = tuple(records)

    def records(self, statuses=None):
        return self._records


class _Coordinator:
    def __init__(self, records):
        self.wal = _Wal(records)


class _Operations:
    def __init__(self, repository, records):
        self.repository = repository
        self.coordinator = _Coordinator(records)
        self.allowed_actor_ids = frozenset({"pc.test"})

    def _locked(self):
        return nullcontext()


def _command(expected=144, payload=None):
    return CommandEnvelope(
        campaign_id="test-campaign",
        request_id="repair-wal-test",
        actor_id="pc.test",
        command_type=REPAIR_COMMAND_TYPE,
        expected_revision=expected,
        submitted_at="2026-08-31T00:00:00Z",
        payload={"damaged_wal_revision_start": 143} if payload is None else payload,
        mode="repair",
    )


def _fixture(tmp_path):
    meta142 = _bytes({"schema": "meta", "campaign_id": "test-campaign", "game": "jianghu", "revision": 142, "time": "SE-0061-09-27T21:15:00", "player_id": "pc.test"})
    meta143 = _bytes({"schema": "meta", "campaign_id": "test-campaign", "game": "jianghu", "revision": 143, "time": "SE-0061-09-27T22:58:33", "player_id": "pc.test"})
    meta144 = _bytes({"schema": "meta", "campaign_id": "test-campaign", "game": "jianghu", "revision": 144, "time": "SE-0061-09-27T22:59:22", "player_id": "pc.test"})
    clean = _bytes({"value": "clean"})
    bad1 = _bytes({"value": "bad-143"})
    bad2 = _bytes({"value": "bad-144"})
    records = [
        _record("test-campaign", 142, 143, "tx.gameplay." + "1" * 64, [_entry("state/meta.json", meta142, meta143), _entry("state/example.json", clean, bad1)]),
        _record("test-campaign", 143, 144, "tx.gameplay." + "2" * 64, [_entry("state/meta.json", meta143, meta144), _entry("state/example.json", bad1, bad2)]),
    ]
    repository = _Repository(tmp_path, {"state/meta.json": meta144, "state/example.json": bad2})
    return repository, records, clean


def test_wal_chain_repair_restores_before_severed_git_root_and_advances_forward(tmp_path):
    repository, records, clean = _fixture(tmp_path)
    service = CampaignRepairService(_Operations(repository, records))
    plan = service._build(_command())
    assert plan.result["repair_kind"] == "forward_wal_revision_chain_repair"
    assert plan.result["restored_state_revision"] == 142
    assert plan.result["committed_revision"] == 145
    assert plan.result["restored_world_time"] == "SE-0061-09-27T21:15:00"
    assert plan.writes["state/example.json"] == clean
    repaired_meta = json.loads(plan.writes["state/meta.json"].decode())
    assert repaired_meta["revision"] == 145
    assert repaired_meta["time"] == "SE-0061-09-27T21:15:00"


def test_wal_chain_repair_fails_if_a_world_revision_is_missing(tmp_path):
    repository, records, _ = _fixture(tmp_path)
    service = CampaignRepairService(_Operations(repository, records[1:]))
    with pytest.raises(OperationError) as caught:
        service._build(_command())
    assert caught.value.code == "repair_wal_provenance_incomplete"


def test_wal_chain_repair_fails_on_path_hash_discontinuity(tmp_path):
    repository, records, _ = _fixture(tmp_path)
    records[1]["entries"][1]["before_sha256"] = "f" * 64
    service = CampaignRepairService(_Operations(repository, records))
    with pytest.raises(OperationError) as caught:
        service._build(_command())
    assert caught.value.code == "repair_wal_provenance_invalid"


def test_wal_chain_repair_fails_if_current_state_no_longer_matches_last_after_image(tmp_path):
    repository, records, _ = _fixture(tmp_path)
    repository.files["state/example.json"] = _bytes({"value": "different"})
    service = CampaignRepairService(_Operations(repository, records))
    with pytest.raises(OperationError) as caught:
        service._build(_command())
    assert caught.value.code == "repair_wal_base_changed"


def test_wal_repair_payload_cannot_choose_paths_or_replacement_values(tmp_path):
    repository, records, _ = _fixture(tmp_path)
    service = CampaignRepairService(_Operations(repository, records))
    with pytest.raises(OperationError) as caught:
        service._build(_command(payload={"damaged_wal_revision_start": 143, "path": "state/example.json"}))
    assert caught.value.code == "repair_payload_invalid"
