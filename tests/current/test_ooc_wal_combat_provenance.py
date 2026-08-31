from __future__ import annotations

import base64
import json

import shinobi_runtime.api.ooc as ooc


def _b64(value):
    return base64.b64encode((json.dumps(value) + "\n").encode()).decode("ascii")


class _Repo:
    root = None

    def read_json(self, path):
        if path == "state/meta.json":
            return {"campaign_id": "c", "game": "jianghu", "revision": 143, "time": "T1"}
        if path == "state/martial-world/scheduler.json":
            return {"settled_through": "T1", "recurring": {}}
        if path == "state/martial-world/civilian-populations.json":
            return {"schema": "x", "places": {}}
        raise FileNotFoundError(path)


class _Wal:
    def __init__(self, _path):
        pass

    def records(self, _statuses):
        combat_ref = "combat:test"
        return ({
            "transaction_id": "tx.gameplay.test",
            "manifest": {"campaign_id": "c", "base_revision": 142, "target_revision": 143, "request_id": "combat-test"},
            "entries": [
                {"path": "state/meta.json", "before_b64": _b64({"time": "T0"}), "after_b64": _b64({"time": "T1"})},
                {"path": "state/martial-world/combats.json", "before_b64": _b64({"combats": {combat_ref: {"elapsed_ms": 1000}}}), "after_b64": _b64({"combats": {combat_ref: {"elapsed_ms": 6200000}}})},
            ],
            "receipt": {"result": {"combat_ref": combat_ref, "exchanges_resolved": 160, "scope_stop_reason": "execution_frontier"}},
        },)


def test_focused_ooc_audit_surfaces_bounded_combat_wal_timeline(monkeypatch, tmp_path):
    monkeypatch.setattr(ooc, "WriteAheadLog", _Wal)
    monkeypatch.setattr(ooc, "_derived_person_routes", lambda _repo: {})
    monkeypatch.setattr(ooc, "civilian_population_total", lambda _value: 0)
    monkeypatch.setattr(ooc, "inspect_deployment_freshness", lambda _root: type("D", (), {"healthy": True, "diagnostic": lambda self: "deployment:ok"})())
    result = ooc.RepositoryOocAudit(_Repo(), tmp_path)("combat repair provenance", ())
    joined = "\n".join(result.diagnostics)
    assert "wal_combat:base=142 rev=143" in joined
    assert "elapsed_ms=1000->6200000" in joined
    assert "exchanges=160" in joined
