from shinobi_runtime.commands.campaign_mission_assignment import CampaignCommandPlanner


class _Repo:
    def read_json(self, path):
        assert path == "state/team/registry.json"
        return {
            "schema": "exact-team-registry",
            "active_teams": ["team.alpha", "team.beta"],
        }


class _PlannerView:
    repository = _Repo()

    def _exact_team(self, team_ref):
        rows = {
            "team.alpha": {
                "schema": "exact-team",
                "id": "team.alpha",
                "status": "active",
                "member_refs": ["char.alpha"],
            },
            "team.beta": {
                "schema": "exact-team",
                "id": "team.beta",
                "status": "active",
                "member_refs": ["char.beta.before"],
            },
        }
        return f"state/team/{team_ref}.json", rows[team_ref]


def test_active_team_member_compatibility_honors_staged_after_images():
    staged = {
        "state/team/team.beta.json": {
            "schema": "exact-team",
            "id": "team.beta",
            "status": "inactive",
            "member_refs": ["char.beta.before"],
        },
        "state/team/team.gamma.json": {
            "schema": "exact-team",
            "id": "team.gamma",
            "status": "active",
            "member_refs": ["char.gamma"],
        },
    }
    members = CampaignCommandPlanner._active_exact_team_members(_PlannerView(), staged)
    assert members == {"char.alpha", "char.gamma"}
