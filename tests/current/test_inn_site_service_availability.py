from pathlib import Path
from types import SimpleNamespace

from shinobi_runtime.commands.jianghu import JianghuCommandsMixin
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.martial_world.services import service_quote
from shinobi_runtime.sim.events import CampaignTime


ROOT = Path(__file__).resolve().parents[2]


class _CombatOnlyRepository:
    def read_json(self, path):
        if str(path) == "state/martial-world/combats.json":
            return {"combats": {}}
        raise FileNotFoundError(str(path))


def test_service_build_scopes_site_rest_availability_override(monkeypatch):
    planner = object.__new__(RepositoryCommandPlanner)
    planner.repository = _CombatOnlyRepository()
    planner._allow_site_service_presence = False
    planner._validated_preview_build = None
    monkeypatch.setattr(
        planner,
        "_base",
        lambda _command: ({"schema": "meta", "game": "jianghu"}, CampaignTime.parse("SE-0061-01-01T00:00:00")),
    )

    observed = []

    def reducer(_command, _meta, _now):
        observed.append(planner._allow_site_service_presence)
        return object()

    monkeypatch.setattr(planner, "_jianghu_service_purchase_resolution", reducer)
    command = SimpleNamespace(
        command_type="jianghu_service_purchase_resolution",
        actor_id="pc.test",
        payload={"site_ref": "site.test.inn", "service_ref": "simple_meal"},
    )

    planner._build(command)

    assert observed == [True]
    assert planner._allow_site_service_presence is False


def test_site_service_override_releases_only_exact_site_available_people(monkeypatch):
    planner = object.__new__(RepositoryCommandPlanner)
    planner._allow_site_service_presence = True
    unavailable = {"person.rest", "person.road", "person.custody"}
    monkeypatch.setattr(
        JianghuCommandsMixin,
        "_physically_unavailable_person_refs",
        lambda self: set(unavailable),
    )
    monkeypatch.setattr(
        planner,
        "_person",
        lambda ref: ("roster.json", {}, 0, {"person_id": ref}),
    )
    presence = {
        "person.rest": {"location_ref": "site.test.inn", "available_for_site_activity": True},
        "person.road": {"location_ref": "route.a.b", "available_for_site_activity": False},
        "person.custody": {"location_ref": "site.test.inn", "available_for_site_activity": False},
    }
    monkeypatch.setattr(planner, "_effective_person_presence", lambda ref, person=None: presence[ref])

    assert planner._physically_unavailable_person_refs() == {"person.road", "person.custody"}


def test_changan_inn_serves_ordinary_wine():
    quote = service_quote(site_ref="site.changan.inn", service_ref="wine_jar", buyer_age=17)

    assert quote["site_type"] == "inn"
    assert quote["price_cash"] > 0
    assert quote["duration_minutes"] > 0


def test_narration_rule_forbids_surname_only_shortening():
    text = (ROOT / "plugins/shinobi-rpg/skill/shinobi-game-master/references/narration.md").read_text(encoding="utf-8")

    assert "Never shorten a multi-part Chinese personal name to the surname alone" in text
