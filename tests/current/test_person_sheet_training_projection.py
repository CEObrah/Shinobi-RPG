import copy
import hashlib

from shinobi_runtime.people.repository import RepositoryPersonSheetResolver


class _ReadOnlyRepo:
    def __init__(self, rows):
        self.rows = copy.deepcopy(rows)

    def read_json(self, path):
        if path not in self.rows:
            raise FileNotFoundError(path)
        return copy.deepcopy(self.rows[path])


def _fixture_repo():
    person_id = "pc_wei_tang"
    bucket = hashlib.sha256(person_id.encode("utf-8")).hexdigest()[:2]
    rows = {
        "state/meta.json": {
            "campaign_id": "jianghu-test",
            "game": "jianghu",
            "player_id": person_id,
            "revision": 1,
            "time": "SE-0061-01-09T00:00:00",
        },
        f"state/martial-world/person-routes/{bucket}.json": {
            "people": {person_id: ["house_tang", 0]},
        },
        "state/martial-world/factions/house_tang.json": {
            "schema": "jianghu-faction-state-1.0",
            "faction_id": "house_tang",
            "headquarters": "luoyang",
            "local_site_ref": "site.house_tang",
            "population": 1,
            "treasury_cash": 1000,
            "buildings": {
                "training_hall": 5,
                "training_grounds": 5,
                "qi_hall": 5,
                "library_records": 5,
                "infirmary_apothecary": 5,
                "armory_workshop": 5,
                "main_hall": 5,
            },
            "training_epoch": {
                "started_at": "0061-01-01T00:00:00",
                "settled_through": "0061-01-01T00:00:00",
                "elapsed_training_days": 0,
                "intensity_milli": 1000,
            },
        },
        "state/martial-world/people/house_tang.json": {
            "schema": "jianghu-roster-state-1.0",
            "faction_ref": "house_tang",
            "people": [{
                "person_id": person_id,
                "name": "Tang Wei",
                "membership_grade": "elite",
                "birth_year": 44,
                "attributes": {
                    "strength": 82,
                    "speed": 90,
                    "dexterity": 94,
                    "endurance": 84,
                    "perception": 96,
                    "intelligence": 100,
                    "willpower": 92,
                },
                "martial_skills": {"sword": 115, "unarmed": 72},
                "professional_skills": {"instruction": 25},
                "aptitudes": {
                    "physical": 200,
                    "martial": 200,
                    "qi": 200,
                    "cognitive": 200,
                    "leadership": 200,
                },
                "health": {"status": "ready", "consciousness": 100, "injuries": []},
                "qi": 150,
                "qi_control": 78,
                "personal_cash": 3118,
            }],
        },
    }
    return _ReadOnlyRepo(rows)


def test_person_sheet_projects_lazy_training_to_campaign_time_without_writing():
    repo = _fixture_repo()
    before = copy.deepcopy(repo.rows)

    person = RepositoryPersonSheetResolver(repo)("pc_wei_tang")

    assert person is not None
    assert person["training_state"]["institutional_days_applied"] == 8
    assert repo.rows == before


def test_person_sheet_projection_preserves_existing_lazy_anchor_and_adds_only_delta():
    repo = _fixture_repo()
    faction = repo.rows["state/martial-world/factions/house_tang.json"]
    faction["training_epoch"]["settled_through"] = "0061-01-05T00:00:00"
    faction["training_epoch"]["elapsed_training_days"] = 4
    repo.rows["state/martial-world/people/house_tang.json"]["people"][0]["training_state"] = {
        "institutional_days_applied": 4,
    }

    person = RepositoryPersonSheetResolver(repo)("pc_wei_tang")

    assert person is not None
    assert person["training_state"]["institutional_days_applied"] == 8
