import json

from shinobi_runtime.api.scene_vitality import (
    apply_scene_vitality_handoff,
    build_scene_cast,
)
from shinobi_runtime.commands import campaign_runtime_planner as runtime_planner
from shinobi_runtime.commands.core import _BuiltPlan, _json_bytes
from shinobi_runtime.commands import living_world_team_vitality as team_vitality
from shinobi_runtime.sim.events import CampaignTime


def _plan(scene, *, result=None):
    return _BuiltPlan(
        code="advance_time_ready",
        affected_refs=("state/scene.json",),
        writes={"state/scene.json": _json_bytes(scene)},
        result={} if result is None else result,
        validator=lambda overlay, manifest: None,
    )


def _scene_from(plan):
    return json.loads(plan.writes["state/scene.json"].decode("utf-8"))


def test_time_handoff_removes_only_inherited_stale_pressure():
    previous = {
        "observable_pressures": ["old pressure"],
        "narrative": {
            "current_tension": "old tension",
            "available_reports": ["old report"],
        },
    }
    unchanged = {
        "observable_pressures": ["old pressure"],
        "narrative": {
            "current_tension": "old tension",
            "available_reports": ["old report"],
        },
    }

    refreshed = runtime_planner._refresh_time_advanced_plan(
        _plan(unchanged),
        "state/scene.json",
        previous_scene=previous,
    )
    scene = _scene_from(refreshed)

    assert scene["observable_pressures"] == []
    assert "current_tension" not in scene["narrative"]
    assert "available_reports" not in scene["narrative"]

    changed = {
        "observable_pressures": ["new pressure"],
        "narrative": {"current_tension": "new tension"},
    }
    refreshed_changed = runtime_planner._refresh_time_advanced_plan(
        _plan(changed),
        "state/scene.json",
        previous_scene=previous,
    )
    changed_scene = _scene_from(refreshed_changed)

    assert changed_scene["observable_pressures"] == ["new pressure"]
    assert changed_scene["narrative"]["current_tension"] == "new tension"


def test_time_handoff_rebuilds_player_facing_mission_and_team_pressure():
    previous = {
        "observable_pressures": ["stale"],
        "narrative": {"available_reports": ["stale report"]},
    }
    settled = {
        "observable_pressures": ["stale"],
        "narrative": {"available_reports": ["stale report"]},
    }
    result = {
        "autonomous_actions": [
            {
                "kind": "player_mission_offer",
                "mission_id": "mission.offer.test",
            }
        ],
        "team_reviews": [
            {
                "kind": "player_led_team_checkin",
                "team_id": "team.blackhound",
                "team_name": "Black Hound",
            }
        ],
    }

    refreshed = runtime_planner._refresh_time_advanced_plan(
        _plan(settled, result=result),
        "state/scene.json",
        previous_scene=previous,
    )
    scene = _scene_from(refreshed)

    assert scene["observable_pressures"] == [
        "A new mission offer from the Mission Office is awaiting review.",
        "Black Hound has a fresh internal check-in ready.",
    ]
    assert scene["narrative"]["available_reports"] == [
        "The Mission Office has new operational tasking available for review.",
        "Black Hound has routine field, training, or readiness matters ready to discuss.",
    ]


def test_scene_cast_distinguishes_present_nearby_and_referenced_people():
    scene = {
        "location_id": "place.sword_manor",
        "present_person_ids": ["person.present", "person.hidden"],
        "loaded_owner_ids": ["person.present", "person.reference", "person.hidden"],
    }
    cast = build_scene_cast(
        scene=scene,
        player_id="pc_wei_tang",
        permitted_person_ids=(
            "pc_wei_tang",
            "person.present",
            "person.nearby",
            "person.team",
            "person.reference",
        ),
        person_records={
            "person.present": {"schema": "person", "location_ref": "place.other"},
            "person.nearby": {"schema": "person", "location_ref": "place.sword_manor"},
            "person.reference": {"schema": "person", "location_ref": "place.other"},
        },
        team_records={
            "team.blackhound": {
                "schema": "exact-team",
                "location_ref": "place.sword_manor",
                "member_refs": ["pc_wei_tang", "person.team"],
            }
        },
    )

    assert cast["present_people"] == ["person.present"]
    assert cast["visible_people"] == ["person.present"]
    assert cast["nearby_people"] == ["person.nearby", "person.team"]
    assert cast["referenced_people"] == ["person.reference"]
    assert "person.hidden" not in json.dumps(cast)
    assert cast["basis"]["person.nearby"] == ["exact_person_location"]
    assert cast["basis"]["person.team"] == ["team_location:team.blackhound"]


def test_scene_vitality_handoff_exposes_safe_presentation_boundary():
    payload = {"context_policy": {"truncated_fields": ["narrative.known_clues"]}}
    cast = {
        "present_people": ["person.present"],
        "nearby_people": ["person.nearby"],
        "referenced_people": ["person.reference"],
        "present_truncated": False,
        "nearby_truncated": True,
        "referenced_truncated": False,
    }

    projected = apply_scene_vitality_handoff(payload, scene_cast=cast)

    assert projected["scene_vitality"]["ephemeral_motion_allowed"] is True
    assert projected["scene_vitality"]["reversible_scene_local_interaction_allowed"] is True
    assert projected["scene_vitality"]["attempt_is_not_world_reaction"] is True
    assert projected["scene_vitality"]["interaction_candidate_ids"] == [
        "person.present",
        "person.nearby",
        "person.reference",
    ]
    assert "relationship changes" in projected["scene_vitality"][
        "durable_state_requires_runtime"
    ]
    assert projected["context_policy"]["truncated_fields"] == [
        "narrative.known_clues",
        "scene_cast.nearby_people",
    ]


class _Repo:
    def __init__(self, team):
        self.team = team

    def read_json(self, path):
        assert path == "state/team/team.blackhound.json"
        return self.team


class _Policy:
    def team_profile(self, team_type):
        assert team_type == "special_mission_cell"
        return {
            "player_led_contact_chance_milli": 1000,
            "training_focus": ["containment", "extraction"],
        }


class _TeamHarness(team_vitality.LivingWorldTeamVitalityMixin):
    def __init__(self, team):
        self.repository = _Repo(team)

    def _append_internal_event(self, *args, **kwargs):
        assert kwargs["kind"] == "player_led_team_checkin_ready"
        assert kwargs["audience_refs"] == ("pc_wei_tang",)
        assert kwargs["affected_owner_refs"] == ()
        assert kwargs["material_consequence_refs"] == (
            "player_led_team_checkin:team.blackhound:person.hayama",
        )
        return "event.team.checkin"


def test_player_led_team_review_creates_checkin_without_mutating_team(monkeypatch):
    team = {
        "schema": "exact-team",
        "id": "team.blackhound",
        "name": "Black Hound",
        "status": "active",
        "team_type": "special_mission_cell",
        "leader_ref": "pc_wei_tang",
        "deputy_ref": "person.hayama",
        "member_refs": ["pc_wei_tang", "person.hayama", "person.ensui"],
        "classification": "restricted",
        "current_assignment_ref": None,
    }
    monkeypatch.setattr(team_vitality, "_stable_roll", lambda *args, **kwargs: 0)
    harness = _TeamHarness(team)
    record_writes = {}
    command = type("Command", (), {"actor_id": "pc_wei_tang"})()

    result = harness._apply_team_autonomy_review(
        owner_ref="state/team/team.blackhound.json",
        at=CampaignTime.parse("SE-0061-06-11T07:00:00"),
        compacted=1,
        command=command,
        scheduler=object(),
        policy_book=_Policy(),
        world_events={},
        record_writes=record_writes,
    )

    assert result["kind"] == "player_led_team_checkin"
    assert result["team_id"] == "team.blackhound"
    assert result["contact_actor_ref"] == "person.hayama"
    assert result["event_id"] == "event.team.checkin"
    assert result["topic_cues"] == [
        "containment",
        "extraction",
        "readiness, equipment, and the next training block",
    ]
    assert record_writes == {}
    assert team["current_assignment_ref"] is None
