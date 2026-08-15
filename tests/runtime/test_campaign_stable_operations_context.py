from shinobi_runtime.api import campaign_stable_operations as stable


def test_production_scene_cast_uses_shared_context_person_limit(monkeypatch):
    operations = stable.RouteAwareCampaignOperations.__new__(
        stable.RouteAwareCampaignOperations
    )
    permitted = tuple(f"person.test.{index:03d}" for index in range(120))
    captured = {}

    monkeypatch.setattr(
        operations,
        "_permitted_person_lookup_ids",
        lambda *, player_id: permitted,
    )
    monkeypatch.setattr(
        operations,
        "_owner_record",
        lambda person_id: (
            f"state/char/{person_id}.json",
            {"schema": "person", "owner_id": person_id},
        ),
    )

    def fake_build_scene_cast(**kwargs):
        captured.update(kwargs)
        return {"present_people": []}

    monkeypatch.setattr(stable, "build_scene_cast", fake_build_scene_cast)

    result = operations._player_scene_cast(
        player_id="pc_wei_tang",
        scene={"schema": "scene"},
        payload={"object_reads": {}},
    )

    assert result == {"present_people": []}
    assert len(captured["permitted_person_ids"]) == 96
    assert captured["permitted_person_ids"] == permitted[:96]
