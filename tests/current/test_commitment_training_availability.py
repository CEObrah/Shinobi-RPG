from __future__ import annotations

import copy

from shinobi_runtime.commands import jianghu
from shinobi_runtime.sim.events import CampaignTime


class _Repo:
    def read_json(self, _path):
        return {}


class _Harness(jianghu.JianghuCommandsMixin):
    def __init__(self):
        self.repository = _Repo()

    def _person(self, ref):
        person = {"person_id": ref, "faction_ref": "house_tang"}
        return "state/martial-world/people/house_tang.json", {}, 0, person

    def _physically_unavailable_person_refs(self):
        return {"fighter.away"}


def _patch_training(monkeypatch, captured):
    faction = {"faction_id": "house_tang"}
    roster = {"people": [
        {"person_id": "member", "faction_ref": "house_tang", "training_state": {}},
        {"person_id": "fighter.away", "faction_ref": "house_tang", "training_state": {}},
    ]}
    monkeypatch.setattr(jianghu, "read_faction", lambda _repo, _fid: ("state/martial-world/factions/house_tang.json", copy.deepcopy(faction)))
    monkeypatch.setattr(jianghu, "derived_commitment_state", lambda _read: {"commitments": {}, "person_index": {}})
    monkeypatch.setattr(jianghu, "hydrate_roster_state", lambda row, *, faction: copy.deepcopy(dict(row)))
    monkeypatch.setattr(jianghu, "compact_roster_state", lambda row, *, faction: copy.deepcopy(dict(row)))

    def pause_refs(_faction, _people, *, unavailable_refs=()):
        captured.append(set(unavailable_refs))
        return list(unavailable_refs)

    monkeypatch.setattr(jianghu, "institutional_training_pause_refs", pause_refs)
    monkeypatch.setattr(
        jianghu,
        "settle_and_reset_faction_training_cycle",
        lambda faction, roster, *, at_iso, paused_refs=(): (copy.deepcopy(dict(faction)), copy.deepcopy(dict(roster)), {}),
    )
    return faction, roster


def test_starting_commitment_training_reset_pauses_physically_unavailable_people(monkeypatch):
    captured = []
    faction, roster = _patch_training(monkeypatch, captured)
    harness = _Harness()
    harness._pause_institutional_training_now(
        ["member"], CampaignTime.parse("SE-0061-09-28T12:00:00"),
        faction_override=faction, roster_override=roster,
    )
    assert captured
    assert "fighter.away" in captured[0]


def test_releasing_commitment_training_reset_pauses_physically_unavailable_people(monkeypatch):
    captured = []
    faction, roster = _patch_training(monkeypatch, captured)
    harness = _Harness()
    harness._resume_institutional_training_now(
        ["member"], CampaignTime.parse("SE-0061-09-28T12:00:00"),
        faction_override=faction, roster_override=roster,
    )
    assert captured
    assert "fighter.away" in captured[0]


def test_resumption_uses_target_after_image_for_exact_combat():
    harness = _Harness()

    def read_json(path):
        if path == "state/martial-world/custody.json":
            return {"records": []}
        if path == "state/martial-world/combats.json":
            return {
                "combats": {
                    "combat:target": {
                        "status": "active",
                        "combatants": {"member": {}},
                    }
                }
            }
        if path == "state/martial-world/route-operations.json":
            return {"movements": {}}
        return {}

    assert harness._resumable_after_commitment_release(
        ["member"], {"person_index": {}}, read_json=read_json,
    ) == []


def test_resumption_uses_target_after_image_for_physical_travel():
    harness = _Harness()

    def read_json(path):
        if path == "state/martial-world/custody.json":
            return {"records": []}
        if path == "state/martial-world/combats.json":
            return {"combats": {}}
        if path == "state/martial-world/route-operations.json":
            return {
                "movements": {
                    "movement:target": {
                        "status": "traveling",
                        "participant_refs": ["member"],
                    }
                }
            }
        return {}

    assert harness._resumable_after_commitment_release(
        ["member"], {"person_index": {}}, read_json=read_json,
    ) == []
