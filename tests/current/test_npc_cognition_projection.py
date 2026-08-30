from shinobi_runtime.api.operations import _gm_private_person_cognition, _gm_private_relationship_cognition


def test_private_relationship_cognition_is_bounded_and_not_player_knowledge():
    cognition = _gm_private_relationship_cognition(
        {"trust": 42, "affection": -7, "respect": 61, "familiarity": 33, "other": 999}
    )
    assert cognition["relationship_to_player"] == {
        "trust": 42,
        "affection": -7,
        "respect": 61,
        "familiarity": 33,
    }
    assert cognition["privacy"] == "gm_private_cognition_not_player_knowledge"
    assert "never quote" in cognition["use_rule"].lower()
    assert "hard mechanical consequence" in cognition["use_rule"].lower()


def test_private_relationship_cognition_does_not_invent_missing_characterization():
    assert _gm_private_relationship_cognition({}) == {}
    assert _gm_private_relationship_cognition({"other": 3}) == {}


def test_private_person_cognition_can_use_hidden_character_truth_without_making_it_player_knowledge():
    cognition = _gm_private_person_cognition(
        {
            "hidden_goals": ["steal the convoy ledger without revealing the employer"],
            "private_knowledge": {"employer": "faction.hidden"},
            "temperament": "proud but cautious",
        },
        {"trust": -20, "respect": 15},
    )
    assert cognition["hidden_goals"] == ["steal the convoy ledger without revealing the employer"]
    assert cognition["private_knowledge"]["employer"] == "faction.hidden"
    assert cognition["relationship_to_player"]["trust"] == -20
    assert cognition["privacy"] == "gm_private_cognition_not_player_knowledge"
    assert "do not state hidden entries" in cognition["use_rule"].lower()


def test_private_scene_truth_is_bounded_director_context_not_mechanical_authority():
    from shinobi_runtime.api.operations import _gm_private_person_scene_truth
    truth = _gm_private_person_scene_truth({
        "person_id": "char.test",
        "name": "Test Person",
        "faction_ref": "house_tang",
        "health": {"status": "ready", "consciousness": 100},
        "fatigue": 7,
        "martial_skills": {"sword": 88},
        "attributes": {"strength": 70},
        "goal_state": {"current_goals": ["protect the household"]},
    })
    assert truth["person_id"] == "char.test"
    assert truth["martial_skills"]["sword"] == 88
    assert truth["privacy"] == "gm_private_scene_bounded_omniscient_truth_not_player_knowledge"
    assert truth["mechanical_authority"] is False


def test_scene_director_context_exists_before_conversation_session(monkeypatch):
    import shinobi_runtime.api.operations as operations_module
    from shinobi_runtime.api.operations import CampaignOperations

    people = {
        "pc.test": {"person_id": "pc.test", "name": "Wei"},
        "npc.one": {
            "person_id": "npc.one", "name": "Kai", "faction_ref": "house_tang",
            "health": {"status": "ready"}, "martial_skills": {"sword": 80},
            "hidden_goals": ["protect Wei without making a show of it"],
        },
        "npc.two": {
            "person_id": "npc.two", "name": "Ling", "faction_ref": "house_tang",
            "health": {"status": "ready"}, "professional_skills": {"administration": 90},
        },
    }

    class Repo:
        def read_json(self, path):
            if path == "state/martial-world/social.json":
                return {"relationships": {
                    "npc.one|pc.test": {"trust": 70, "affection": 50, "respect": 65, "familiarity": 90},
                    "npc.one|npc.two": {"trust": 60, "respect": 55},
                }}
            raise FileNotFoundError(path)

    ops = object.__new__(CampaignOperations)
    ops.repository = Repo()
    ops.sheet_resolver = lambda ref: people[ref]
    ops._validated_scene_person_ids = lambda meta, scene, visible_only=False: ["pc.test", "npc.one", "npc.two"]
    monkeypatch.setattr(operations_module, "same_effective_location", lambda *args, **kwargs: True)

    packet = ops._gm_private_scene_director_context(
        {"player_id": "pc.test"}, {}, {}, people["pc.test"]
    )
    refs = {row["person_ref"] for row in packet["present_people"]}
    assert refs == {"npc.one", "npc.two"}
    assert packet["privacy"] == "gm_private_scene_bounded_omniscient_truth_not_player_knowledge"
    assert packet["mechanical_consequence_authority"] is False
    one = next(row for row in packet["present_people"] if row["person_ref"] == "npc.one")
    assert one["cognition"]["hidden_goals"]
    assert packet["relationship_edges"]
    assert "before a formal conversation session" in packet["director_rule"]


def test_scene_director_context_includes_validated_derived_present_people(monkeypatch):
    import shinobi_runtime.api.operations as operations_module
    from shinobi_runtime.api.operations import CampaignOperations

    people = {
        "pc.test": {"person_id": "pc.test", "name": "Wei"},
        "npc.derived": {"person_id": "npc.derived", "name": "Clerk", "temperament": "quietly observant"},
    }

    class Repo:
        def read_json(self, path):
            if path == "state/martial-world/social.json":
                return {"relationships": {}}
            raise FileNotFoundError(path)

    ops = object.__new__(CampaignOperations)
    ops.repository = Repo()
    ops.sheet_resolver = lambda ref: people[ref]
    ops._validated_scene_person_ids = lambda meta, scene, visible_only=False: ["pc.test"]
    monkeypatch.setattr(operations_module, "same_effective_location", lambda *args, **kwargs: True)

    packet = ops._gm_private_scene_director_context(
        {"player_id": "pc.test"}, {}, {"present_person_ids": ["pc.test", "npc.derived"], "derived_present_person_ids": ["npc.derived"]}, people["pc.test"]
    )
    assert [row["person_ref"] for row in packet["present_people"]] == ["npc.derived"]
    assert packet["present_people"][0]["cognition"]["temperament"] == "quietly observant"


def test_scene_director_prioritizes_live_session_participant_over_old_cast_order(monkeypatch):
    import shinobi_runtime.api.operations as operations_module
    from shinobi_runtime.api.operations import CampaignOperations

    refs = [f"npc.{idx:02d}" for idx in range(20)]
    people = {"pc.test": {"person_id": "pc.test", "name": "Wei"}}
    people.update({
        ref: {"person_id": ref, "name": ref, "hidden_goals": [f"goal-{idx}"]}
        for idx, ref in enumerate(refs)
    })

    class Repo:
        def read_json(self, path):
            if path == "state/martial-world/social.json":
                return {"relationships": {}}
            raise FileNotFoundError(path)

    ops = object.__new__(CampaignOperations)
    ops.repository = Repo()
    ops.sheet_resolver = lambda ref: people[ref]
    ops._validated_scene_person_ids = lambda meta, scene, visible_only=False: ["pc.test", *refs[:-1]]
    monkeypatch.setattr(operations_module, "same_effective_location", lambda *args, **kwargs: True)

    packet = ops._gm_private_scene_director_context(
        {"player_id": "pc.test"},
        {},
        {
            "present_person_ids": ["pc.test", *refs],
            "scene_session_person_ids": ["pc.test", refs[-1]],
        },
        people["pc.test"],
    )
    projected_refs = [row["person_ref"] for row in packet["present_people"]]
    assert projected_refs[0] == refs[-1]
    assert refs[-1] in projected_refs
    assert packet["candidate_present_people_count"] == 20
    assert packet["present_people_context_count"] == 16
    assert packet["present_people_context_truncated"] is True
