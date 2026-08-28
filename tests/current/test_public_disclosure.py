import json
import shutil
from pathlib import Path

from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.martial_world.public_observation import (
    disclosure_credibility_milli,
    hears_public_disclosure,
)
from shinobi_runtime.store.repository import RepositoryStore
from fixture_support import put_movement_at_lodging_fixture

ROOT = Path(__file__).resolve().parents[2]


def test_public_disclosure_hearing_and_credibility_are_deterministic_without_rumor_state():
    from datetime import datetime

    listener = {
        "person_id": "listener.test",
        "attributes": {"perception": 80, "intelligence": 75},
    }
    at = datetime(61, 9, 14, 18, 0, 0)
    args = dict(
        listener=listener,
        speaker_ref="speaker.test",
        site_type="inn",
        at=at,
        disclosure_ref="escort.boast.test",
    )
    heard_a = hears_public_disclosure(**args)
    heard_b = hears_public_disclosure(**args)
    assert heard_a == heard_b

    cred_a = disclosure_credibility_milli(
        listener,
        speaker_ref="speaker.test",
        claimed_value_cash=100_000,
        at=at,
        disclosure_ref="escort.boast.test",
    )
    cred_b = disclosure_credibility_milli(
        listener,
        speaker_ref="speaker.test",
        claimed_value_cash=100_000,
        at=at,
        disclosure_ref="escort.boast.test",
    )
    assert cred_a == cred_b
    assert 100 <= cred_a <= 950


def test_public_escort_boast_can_create_hidden_real_pursuit_without_persisting_a_rumor(tmp_path):
    # Use the real authored world/campaign as a fixture, but never mutate it.
    # A copied save lets the test put an existing NPC escort party in a public
    # Changan inn and exercise the exact semantic command end-to-end.
    root = tmp_path / "campaign"
    shutil.copytree(ROOT / "state", root / "state")
    shutil.copytree(ROOT / "game", root / "game")

    meta = json.loads((root / "state/meta.json").read_text(encoding="utf-8"))
    route_path = root / "state/martial-world/route-operations.json"
    before = json.loads(route_path.read_text(encoding="utf-8"))
    movement_ref = next(
        ref for ref, row in before["movements"].items()
        if row.get("movement_kind") == "escort_contract"
        and row.get("origin_place_ref") == "changan"
    )
    put_movement_at_lodging_fixture(root, movement_ref, "site.changan.inn")
    before = json.loads(route_path.read_text(encoding="utf-8"))
    actor_ref = before["movements"][movement_ref]["participant_refs"][0]

    scene = {
        "schema": "scene",
        "scene_id": "test.public.disclosure",
        "location_id": "site.changan.inn",
        "present_person_ids": [actor_ref],
        "visible_person_ids": [actor_ref],
    }
    (root / "state/scene.json").write_text(json.dumps(scene), encoding="utf-8")

    repository = RepositoryStore(root)
    planner = RepositoryCommandPlanner(repository)
    command = CommandEnvelope(
        campaign_id=meta["campaign_id"],
        request_id="request.public-disclosure-regression",
        actor_id=actor_ref,
        command_type="jianghu_public_disclosure_resolution",
        expected_revision=meta["revision"],
        submitted_at="2026-08-23T00:00:00Z",
        payload={
            "movement_ref": movement_ref,
            "claim_kind": "cargo_value",
            "claimed_value_cash": 1_000_000,
        },
        mode="autonomous",
    )

    preview = planner.preview(command)
    assert preview.status == "ready"
    assert preview.code == "jianghu_public_disclosure_spoken"

    built = planner._build(command)
    route_write = json.loads(built.writes["state/martial-world/route-operations.json"])
    pursuits = [
        row for row in route_write["movements"].values()
        if row.get("movement_kind") == "route_pursuit"
        and row.get("target_movement_ref") == movement_ref
    ]
    assert pursuits
    assert all(row.get("participant_refs") for row in pursuits)
    assert all(row.get("status") == "pursuing" for row in pursuits)
    assert all(row.get("route_ref") == before["movements"][movement_ref]["route_ref"] for row in pursuits)
    social_write = json.loads(built.writes["state/martial-world/social.json"])
    beliefs = [
        row for row in social_write.get("beliefs", {}).values()
        if isinstance(row, dict) and row.get("subject_ref") == movement_ref and row.get("claim_kind") == "cargo_value"
    ]
    assert beliefs
    assert all(row.get("source_ref") == actor_ref for row in beliefs)
    assert all(0 <= int(row.get("confidence_milli", -1)) <= 1000 for row in beliefs)

    # The player receives confirmation only of the words they chose to reveal.
    # Listener identities and hostile plans remain hidden until they are learned
    # through the world normally.
    assert "listener" not in built.result
    assert "faction" not in built.result
    assert "pursuit" not in built.result

    # Speech itself is not a save-game journal. The only new durable thing is an
    # actual pursuit that can move, fight, fail, turn back, or be intercepted.
    assert not any("rumor" in path.name.lower() for path in (root / "state").rglob("*.json"))
    assert json.loads(route_path.read_text(encoding="utf-8")) == before


def test_aggregate_civilian_becomes_exact_only_when_tip_causes_real_pursuit(tmp_path, monkeypatch):
    import shinobi_runtime.commands.jianghu_information as info

    root = tmp_path / "campaign"
    shutil.copytree(ROOT / "state", root / "state")
    shutil.copytree(ROOT / "game", root / "game")
    meta = json.loads((root / "state/meta.json").read_text(encoding="utf-8"))
    route_before = json.loads((root / "state/martial-world/route-operations.json").read_text(encoding="utf-8"))
    movement_ref = next(
        ref for ref, row in route_before["movements"].items()
        if row.get("movement_kind") == "escort_contract" and row.get("origin_place_ref") == "changan"
    )
    put_movement_at_lodging_fixture(root, movement_ref, "site.changan.inn")
    route_before = json.loads((root / "state/martial-world/route-operations.json").read_text(encoding="utf-8"))
    actor_ref = route_before["movements"][movement_ref]["participant_refs"][0]
    (root / "state/scene.json").write_text(json.dumps({
        "schema": "scene",
        "scene_id": "test.aggregate.informant",
        "location_id": "site.changan.inn",
        "present_person_ids": [actor_ref],
        "visible_person_ids": [actor_ref],
    }), encoding="utf-8")

    # Existing exact faction members deliberately miss the words. The only
    # listener who hears is the transient aggregate-civilian candidate. If the
    # resulting tip does not produce a real pursuit, no civic identity may be
    # written at all.
    monkeypatch.setattr(
        info, "hears_public_disclosure",
        lambda listener, **_kwargs: str(listener.get("person_id") or "").startswith("civic.person."),
    )
    monkeypatch.setattr(info, "best_route_observer", lambda rows: next(iter(rows), None))
    monkeypatch.setattr(info, "observed_escort_strength", lambda **_kwargs: {
        "visible_escort_count": 1, "estimated_combat_index": 1, "confidence_milli": 1000,
    })
    monkeypatch.setattr(info, "person_combat_index", lambda _person: 100)
    # This regression targets the aggregate-civilian promotion boundary, not
    # mutable live-save outlaw deployment. Provide one exact locally based
    # outlaw organization and a bounded force size so the synthetic disclosure
    # deterministically reaches the pursuit branch.
    monkeypatch.setattr(info, "current_faction_refs_at_place", lambda *_args, **_kwargs: ["faction.broken_tooth_gang"])
    monkeypatch.setattr(info, "interception_force_size", lambda **_kwargs: 2)
    monkeypatch.setattr(info, "interception_decision", lambda **_kwargs: {"attack": True, "intent": "robbery"})

    repository = RepositoryStore(root)
    planner = RepositoryCommandPlanner(repository)
    command = CommandEnvelope(
        campaign_id=meta["campaign_id"],
        request_id="request.aggregate-informant-regression",
        actor_id=actor_ref,
        command_type="jianghu_public_disclosure_resolution",
        expected_revision=meta["revision"],
        submitted_at="2026-08-23T00:00:00Z",
        payload={"movement_ref": movement_ref, "claim_kind": "cargo_value", "claimed_value_cash": 1_000_000},
        mode="autonomous",
    )

    civilians_before = repository.read_json("state/martial-world/civilian-populations.json")
    civic_before = repository.read_json("state/martial-world/civic-people.json")
    preview = planner.preview(command)
    assert preview.status == "ready"
    built = planner._build(command)
    assert "state/martial-world/civilian-populations.json" in built.writes
    assert "state/martial-world/civic-people.json" in built.writes
    civilians_after = json.loads(built.writes["state/martial-world/civilian-populations.json"])
    civic_after = json.loads(built.writes["state/martial-world/civic-people.json"])
    assert civilians_after["places"]["changan"]["current_population"] == civilians_before["places"]["changan"]["current_population"] - 1
    assert len(civic_after["people"]) == len(civic_before["people"]) + 1
    new_ref = next(row["person_id"] for row in civic_after["people"] if row not in civic_before["people"])
    assert new_ref.startswith("civic.person.")
    route_after = json.loads(built.writes["state/martial-world/route-operations.json"])
    pursuits = [
        row for row in route_after["movements"].values()
        if row.get("movement_kind") == "route_pursuit" and row.get("target_movement_ref") == movement_ref
    ]
    assert any(row.get("source_observer_ref") == new_ref for row in pursuits)
    social_after = json.loads(built.writes["state/martial-world/social.json"])
    assert any(
        isinstance(row, dict) and row.get("observer_ref") == new_ref and row.get("subject_ref") == movement_ref
        for row in social_after.get("beliefs", {}).values()
    )


def test_aggregate_listener_who_does_not_act_never_becomes_save_state(tmp_path, monkeypatch):
    import shinobi_runtime.commands.jianghu_information as info

    root = tmp_path / "campaign"
    shutil.copytree(ROOT / "state", root / "state")
    shutil.copytree(ROOT / "game", root / "game")
    meta = json.loads((root / "state/meta.json").read_text(encoding="utf-8"))
    route_before = json.loads((root / "state/martial-world/route-operations.json").read_text(encoding="utf-8"))
    movement_ref = next(
        ref for ref, row in route_before["movements"].items()
        if row.get("movement_kind") == "escort_contract" and row.get("origin_place_ref") == "changan"
    )
    put_movement_at_lodging_fixture(root, movement_ref, "site.changan.inn")
    route_before = json.loads((root / "state/martial-world/route-operations.json").read_text(encoding="utf-8"))
    actor_ref = route_before["movements"][movement_ref]["participant_refs"][0]
    (root / "state/scene.json").write_text(json.dumps({
        "schema": "scene", "scene_id": "test.aggregate.no-action", "location_id": "site.changan.inn",
        "present_person_ids": [actor_ref], "visible_person_ids": [actor_ref],
    }), encoding="utf-8")
    monkeypatch.setattr(info, "hears_public_disclosure", lambda *_args, **_kwargs: False)

    repository = RepositoryStore(root)
    planner = RepositoryCommandPlanner(repository)
    command = CommandEnvelope(
        campaign_id=meta["campaign_id"], request_id="request.aggregate-no-action", actor_id=actor_ref,
        command_type="jianghu_public_disclosure_resolution", expected_revision=meta["revision"],
        submitted_at="2026-08-23T00:00:00Z",
        payload={"movement_ref": movement_ref, "claim_kind": "cargo_value", "claimed_value_cash": 1_000_000}, mode="autonomous",
    )
    built = planner._build(command)
    assert "state/martial-world/civilian-populations.json" not in built.writes
    assert "state/martial-world/civic-people.json" not in built.writes
    assert "state/martial-world/social.json" not in built.writes
