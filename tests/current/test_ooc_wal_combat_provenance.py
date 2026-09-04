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
            return {"campaign_id": "c", "game": "jianghu", "revision": 143, "time": "T1", "player_id": "pc.test"}
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



class _LegacyGit:
    def __init__(self, _root):
        pass

    def root_commits(self):
        return ("root143",)

    def unreachable_commits(self, max_count=512):
        return ("noise", "old143")

    def read_path_at(self, commit, path):
        meta = {
            "root143": {"campaign_id": "c", "revision": 143, "time": "T143", "player_id": "pc.test"},
            "noise": {"campaign_id": "c", "revision": 143, "time": "TN", "player_id": "pc.test"},
            "old143": {"campaign_id": "c", "revision": 143, "time": "T143", "player_id": "pc.test"},
            "old142": {"campaign_id": "c", "revision": 142, "time": "T142", "player_id": "pc.test"},
            "old141": {"campaign_id": "c", "revision": 141, "time": "T141", "player_id": "pc.test"},
        }
        if path == "state/meta.json" and commit in meta:
            return (json.dumps(meta[commit]) + "\n").encode()
        if path == "state/martial-world/combats.json" and commit in {"old143", "old142", "old141"}:
            elapsed = {"old143": 6_212_079, "old142": 12_000, "old141": 0}[commit]
            return (json.dumps({"combats": {"combat:test": {"status": "active", "elapsed_ms": elapsed}}}) + "\n").encode()
        if path == "state/martial-world/people/house_tang.json" and commit in {"old143", "old142", "old141"}:
            fatigue = {"old143": 3265, "old142": 120, "old141": 0}[commit]
            return (json.dumps({"people": [{"person_id": "pc.test", "fatigue_milli": fatigue}]}) + "\n").encode()
        return None

    def tree_oid(self, commit, path):
        assert path == "state"
        return {"root143": "state-same", "old143": "state-same", "noise": "state-other"}.get(commit, "older-state")

    def get_commit(self, commit):
        trailers = {
            "old143": {"Shinobi-Campaign": "c", "Shinobi-World-Revision": "143"},
            "noise": {"Shinobi-Campaign": "c", "Shinobi-World-Revision": "143"},
        }.get(commit, {})
        return type("Record", (), {"trailers": trailers})()

    def first_parent(self, commit):
        mapping = {"old143": "old142", "old142": "old141"}
        if commit not in mapping:
            from shinobi_runtime.tx.errors import GitStageError
            raise GitStageError(1, "no parent")
        return mapping[commit]


def test_legacy_lineage_audit_requires_exact_release_root_state_tree_match(monkeypatch, tmp_path):
    monkeypatch.setattr(ooc, "GitStager", _LegacyGit)
    monkeypatch.setattr(ooc, "_derived_person_routes", lambda _repo: {})
    monkeypatch.setattr(ooc, "civilian_population_total", lambda _value: 0)
    monkeypatch.setattr(ooc, "inspect_deployment_freshness", lambda _root: type("D", (), {"healthy": True, "diagnostic": lambda self: "deployment:ok"})())
    result = ooc.RepositoryOocAudit(_Repo(), tmp_path)("legacy lineage pre-root", ())
    joined = "\n".join(result.diagnostics)
    assert "legacy_lineage_anchor:" in joined
    assert "severed_commit=old143" in joined
    assert "state_tree_match=true" in joined
    assert "legacy_world:rev=142 commit=old142" in joined
    assert "combat_elapsed_ms=12000" in joined
    assert "player_fatigue_milli=120" in joined
    assert "severed_commit=noise" not in joined
