from pathlib import Path

from shinobi_runtime.api.gm_scene_context import build_gm_scene_context

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "plugins/shinobi-rpg/skill/shinobi-game-master/SKILL.md"
SCENE = ROOT / "plugins/shinobi-rpg/skill/shinobi-game-master/references/scene-craft.md"
NARRATION = ROOT / "plugins/shinobi-rpg/skill/shinobi-game-master/references/narration.md"
CHOICES = ROOT / "plugins/shinobi-rpg/skill/shinobi-game-master/references/choices.md"


def test_skill_hard_rejects_polished_state_dump_and_default_six_menu():
    skill = SKILL.read_text(encoding="utf-8").lower()
    scene = SCENE.read_text(encoding="utf-8").lower()
    narration = NARRATION.read_text(encoding="utf-8").lower()
    choices = CHOICES.read_text(encoding="utf-8").lower()
    assert "hard narrative quality gate" in skill
    assert "serialized lived jianghu saga, not a turn report" in skill
    assert "hard anti-briefing gate" in scene
    assert "polished briefing prose" in scene
    assert "goal is not to empty the context packet into prose" in scene
    assert "narrative focus beats informational completeness" in narration
    assert "raw summaries" in narration and "research notes" in narration
    assert "2 to 4 materially distinct choices" in choices
    assert "more than 5 should be exceptional" in choices


def test_runtime_marks_fresh_people_process_scene_as_high_paraphrase_risk():
    context = {
        "campaign": {"world_time": "0061-09-27T21:15:54", "player_id": "pc_wei_tang"},
        "player": {"current_location_id": "site.house_tang.hall"},
        "scene": {
            "location_id": "site.house_tang.hall",
            "present_person_ids": ["pc_wei_tang", "npc.test"],
            "visible_person_ids": ["pc_wei_tang", "npc.test"],
        },
        "active_scene_session": None,
        "mission_reads": {"active_missions": [{"object_ref": "mission:test", "phase": "active", "mission_kind": "escort"}]},
    }
    gm = build_gm_scene_context(context)
    direction = gm["scene_direction"]
    assert direction["fresh_scene_entry"] is True
    assert direction["narrative_stage_hint"] == "approach_or_anticipation"
    assert direction["raw_context_paraphrase_risk"] == "high"
    assert gm["writer_contract"]["serial_scene_not_turn_summary"] is True
    assert gm["writer_contract"]["raw_summaries_are_reference_not_prose"] is True
    assert gm["writer_contract"]["anti_state_dump_gate"] is True


def test_writer_workspace_keeps_report_summary_prose_cold():
    context = {
        "campaign": {"world_time": "0061-09-27T21:15:54", "player_id": "pc_wei_tang"},
        "player": {"current_location_id": "site.house_tang.hall"},
        "scene": {
            "location_id": "site.house_tang.hall",
            "present_person_ids": ["pc_wei_tang"],
            "visible_person_ids": ["pc_wei_tang"],
            "available_reports": [{
                "report_ref": "report.test",
                "kind": "escort_report",
                "status": "available",
                "summary": "This is deliberately report-shaped prose that must stay cold.",
            }],
        },
        "world_events": {"active": [{
            "event_ref": "event.test",
            "kind": "local_event",
            "status": "active",
            "summary": "This is also transport prose rather than scene prose.",
        }]},
    }
    gm = build_gm_scene_context(context)
    assert gm["world_pressure"]
    assert all("summary" not in row for row in gm["world_pressure"])
    assert all(row.get("detail_available_via_exact_read") is True for row in gm["world_pressure"])
