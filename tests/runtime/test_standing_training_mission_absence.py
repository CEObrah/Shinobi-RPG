from __future__ import annotations

from types import SimpleNamespace

from shinobi_runtime.commands.standing_training_mission_absence import StandingTrainingMissionAbsenceMixin


class _Base:
    def _team_participation_policy(self, _team):
        return {
            "participates_in_autonomous_training": True,
            "assemble_nonplayer_members": True,
            "participant_ref": "pc_wei_tang",
        }

    def _team_active_mission_ref(self, *, scheduler, member_refs):
        active = set(scheduler.active_members)
        return "mission.test" if active.intersection(member_refs) else None

    def _eligible_autonomous_group(self, *, team, record_writes):
        rows = [
            (ref, f"state/{ref}.json", {"current_location_id": "place.sword_manor"})
            for ref in team["member_refs"]
        ]
        return "char.zhu", {}, "place.sword_manor", rows

    def _apply_autonomous_team_training(self, *, team, scheduler, **_kwargs):
        mission_ref = self._team_active_mission_ref(
            scheduler=scheduler,
            member_refs=team["member_refs"],
        )
        if mission_ref is not None:
            return {"skipped": "active_mission_preempts_training", "mission_ref": mission_ref}
        group = self._eligible_autonomous_group(team=team, record_writes={})
        return {"trained_refs": [row[0] for row in group[3]]}


class _Planner(StandingTrainingMissionAbsenceMixin, _Base):
    pass


TEAM = {
    "id": "team.konoha.fujin",
    "member_refs": ["pc_wei_tang", "char.kai", "char.riku_hyuga", "char.mei_arakawa"],
}


def _run(active_members):
    planner = _Planner()
    return planner._apply_autonomous_team_training(
        team=dict(TEAM),
        owner_ref="state/team/fujin.json",
        at=None,
        compacted=1,
        command=None,
        scheduler=SimpleNamespace(active_members=set(active_members)),
        policy_book=None,
        world_events={},
        record_writes={},
    )


def test_delegated_fujin_training_excludes_only_deployed_wei() -> None:
    result = _run({"pc_wei_tang"})
    assert result["trained_refs"] == ["char.kai", "char.riku_hyuga", "char.mei_arakawa"]


def test_mission_for_other_fujin_member_still_preempts_training() -> None:
    result = _run({"pc_wei_tang", "char.kai"})
    assert result == {"skipped": "active_mission_preempts_training", "mission_ref": "mission.test"}
