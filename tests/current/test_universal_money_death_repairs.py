import copy
from datetime import datetime

import pytest

from shinobi_runtime.martial_world.death_lifecycle import (
    clean_social_and_custody_for_deaths,
    exact_person_index,
    prune_dead_from_durable_activities,
    settle_exact_death_estates,
)
from shinobi_runtime.martial_world.family_simulation import review_conceptions
from shinobi_runtime.martial_world.faction_state import faction_path, roster_path
from shinobi_runtime.martial_world.faction_transitions import reconcile_family_transition

INDEPENDENTS = "state/martial-world/independent-people.json"
CIVIC = "state/martial-world/civic-people.json"


def _person(ref, *, cash=0, dead=False, sex="male", faction=None, location="luoyang"):
    row = {
        "person_id": ref,
        "name": ref,
        "birth_year": 30,
        "sex": sex,
        "personal_cash": cash,
        "health": {"status": "dead" if dead else "healthy", "consciousness": 0 if dead else 100},
        "location_ref": location,
        "home_place_ref": "luoyang",
    }
    if faction:
        row["faction_ref"] = faction
    return row


def _records(*, a_people=(), b_people=(), independents=(), civic=()):
    return {
        roster_path("a"): {"faction_ref": "a", "people": list(a_people)},
        roster_path("b"): {"faction_ref": "b", "people": list(b_people)},
        faction_path("a"): {"faction_id": "a", "treasury_cash": 100},
        faction_path("b"): {"faction_id": "b", "treasury_cash": 200},
        INDEPENDENTS: {"people": list(independents)},
        CIVIC: {"people": list(civic)},
        "state/martial-world/markets/central_plain.json": {"region_id": "central_plain", "cash_pool": 1000},
    }


def _reader(records):
    def read(path):
        if path not in records:
            raise FileNotFoundError(path)
        return copy.deepcopy(records[path])
    return read


def _cash(records, writes, path, ref):
    owner = writes.get(path, records[path])
    return next(row["personal_cash"] for row in owner["people"] if row["person_id"] == ref)


def test_faction_decedent_pays_living_spouse_in_other_faction_before_treasury():
    dead = _person("dead", cash=77, dead=True, faction="a")
    spouse = _person("spouse", cash=3, faction="b", sex="female")
    records = _records(a_people=[dead], b_people=[spouse])
    family = {"marriages": {"m": {"status": "widowed", "spouse_refs": ["dead", "spouse"], "faction_refs": ["a", "b"]}}, "parentage": {}}
    writes = {}

    result = settle_exact_death_estates(
        read_json=_reader(records), writes=writes, faction_refs=["a", "b"], family=family,
        dead_refs=["dead"], place_region={"luoyang": "central_plain"}, site_rows={},
    )

    assert result["heir_cash"] == 77
    assert _cash(records, writes, roster_path("a"), "dead") == 0
    assert _cash(records, writes, roster_path("b"), "spouse") == 80
    assert writes.get(faction_path("a"), records[faction_path("a")])["treasury_cash"] == 100



def test_divorced_spouse_does_not_override_living_child_inheritance():
    dead = _person("parent.divorced", cash=70, dead=True)
    former_spouse = _person("former.spouse", cash=9, faction="b", sex="female")
    child = _person("living.child", cash=5, faction="b", sex="female")
    records = _records(b_people=[former_spouse, child], independents=[dead])
    family = {
        "marriages": {"old": {"status": "divorced", "spouse_refs": ["parent.divorced", "former.spouse"], "faction_refs": ["b"]}},
        "parentage": {"living.child": {"parent_refs": ["parent.divorced"]}},
    }
    writes = {}

    result = settle_exact_death_estates(
        read_json=_reader(records), writes=writes, faction_refs=["a", "b"], family=family,
        dead_refs=["parent.divorced"], place_region={"luoyang": "central_plain"}, site_rows={},
    )

    assert result["heir_cash"] == 70
    assert _cash(records, writes, roster_path("b"), "former.spouse") == 9
    assert _cash(records, writes, roster_path("b"), "living.child") == 75
    assert _cash(records, writes, INDEPENDENTS, "parent.divorced") == 0

def test_independent_decedent_can_pay_living_faction_child():
    dead = _person("parent", cash=55, dead=True)
    child = _person("child", cash=5, faction="b", sex="female")
    records = _records(b_people=[child], independents=[dead])
    family = {"marriages": {}, "parentage": {"child": {"parent_refs": ["parent"]}}}
    writes = {}

    result = settle_exact_death_estates(
        read_json=_reader(records), writes=writes, faction_refs=["a", "b"], family=family,
        dead_refs=["parent"], place_region={"luoyang": "central_plain"}, site_rows={},
    )

    assert result["heir_cash"] == 55
    assert _cash(records, writes, INDEPENDENTS, "parent") == 0
    assert _cash(records, writes, roster_path("b"), "child") == 60
    assert writes.get("state/martial-world/markets/central_plain.json") is None


def test_unclaimed_faction_estate_falls_to_its_faction_and_unclaimed_civic_estate_to_region():
    member = _person("member", cash=40, dead=True, faction="a")
    official = _person("official", cash=60, dead=True)
    records = _records(a_people=[member], civic=[official])
    writes = {}

    result = settle_exact_death_estates(
        read_json=_reader(records), writes=writes, faction_refs=["a", "b"], family={"marriages": {}, "parentage": {}},
        dead_refs=["member", "official"], place_region={"luoyang": "central_plain"}, site_rows={},
    )

    assert result["faction_cash"] == 40
    assert result["regional_cash"] == 60
    assert writes[faction_path("a")]["treasury_cash"] == 140
    assert writes["state/martial-world/markets/central_plain.json"]["cash_pool"] == 1060
    assert _cash(records, writes, roster_path("a"), "member") == 0
    assert _cash(records, writes, CIVIC, "official") == 0


def test_unresolved_regional_estate_destination_fails_before_clearing_purse():
    dead = _person("independent.dead", cash=91, dead=True, location="unknown")
    dead["home_place_ref"] = "unknown"
    records = _records(independents=[dead])
    writes = {}

    with pytest.raises(ValueError, match="regional destination unresolved"):
        settle_exact_death_estates(
            read_json=_reader(records), writes=writes, faction_refs=["a", "b"], family={"marriages": {}, "parentage": {}},
            dead_refs=["independent.dead"], place_region={"luoyang": "central_plain"}, site_rows={},
        )

    assert _cash(records, writes, INDEPENDENTS, "independent.dead") == 91
    assert "state/martial-world/markets/central_plain.json" not in writes


def test_universal_exact_person_index_rejects_civic_faction_duplicate_identity():
    duplicate_a = _person("duplicate", faction="a")
    duplicate_civic = _person("duplicate")
    records = _records(a_people=[duplicate_a], civic=[duplicate_civic])
    with pytest.raises(ValueError, match="duplicate jianghu exact person identity"):
        exact_person_index(read_json=_reader(records), writes={}, faction_refs=["a", "b"])



def test_institutional_custody_survives_individual_captor_death():
    social = {
        "courtships": {"pair": {"person_refs": ["captor", "other"]}},
        "relationships": {"captor|other": {"trust": 1}, "unrelated|other": {"trust": 2}},
    }
    custody = {
        "records": [
            {"person_ref": "institutional.prisoner", "captor_ref": "captor", "holder_faction_ref": "a", "status": "held"},
            {"person_ref": "personal.prisoner", "captor_ref": "captor", "status": "held"},
        ]
    }
    social_after, custody_after, released = clean_social_and_custody_for_deaths(
        social, custody, dead_refs=["captor"],
    )
    assert "pair" not in social_after["courtships"]
    assert "captor|other" not in social_after["relationships"]
    assert "unrelated|other" in social_after["relationships"]
    assert released == {"personal.prisoner"}
    assert custody_after["records"] == [
        {"person_ref": "institutional.prisoner", "captor_ref": "", "holder_faction_ref": "a", "status": "held"}
    ]

def test_cross_faction_conception_is_reviewed_only_by_mothers_faction_and_requires_colocation():
    mother = _person("mother", sex="female", faction="a", location="site.shared")
    father = _person("father", sex="male", faction="b", location="site.shared")
    family = {
        "marriages": {"m": {"status": "married", "spouse_refs": ["mother", "father"], "faction_refs": ["a", "b"]}},
        "parentage": {}, "households": {}, "succession_claims": {},
    }
    world = [mother, father]

    conceived = None
    conceived_at = None
    for month_index in range(36):
        year = 60 + month_index // 12
        month = month_index % 12 + 1
        at_iso = datetime(year, month, 1).isoformat()
        result = review_conceptions(family, faction_ref="a", roster_people=[], world_people=world, at_iso=at_iso)
        if result["conceived_refs"]:
            conceived = result
            conceived_at = at_iso
            break
    assert conceived is not None
    assert conceived["one_off_events"][0]["owner_ref"] == "a"

    wrong_owner = review_conceptions(family, faction_ref="b", roster_people=[], world_people=world, at_iso=conceived_at)
    assert wrong_owner["conceived_refs"] == []

    separated = [dict(mother), dict(father)]
    separated[1]["location_ref"] = "site.away"
    separated_result = review_conceptions(family, faction_ref="a", roster_people=[], world_people=separated, at_iso=conceived_at)
    assert separated_result["conceived_refs"] == []


def test_one_spouse_leaving_faction_keeps_marriage_affiliated_without_claiming_both_are_members():
    family = {
        "marriages": {"m": {"status": "married", "spouse_refs": ["one", "two"], "faction_ref": "a"}},
        "parentage": {}, "households": {}, "succession_claims": {},
    }
    after = reconcile_family_transition(
        family, moved_refs=["one"], source_faction_ref="a", target_faction_ref=None,
    )
    marriage = after["marriages"]["m"]
    assert "faction_ref" not in marriage
    assert marriage["faction_refs"] == ["a"]


def test_direct_player_enterprise_expansion_moves_overhead_cash_into_regional_market(tmp_path):
    import json
    import shutil
    from pathlib import Path

    from shinobi_runtime.commands.envelope import CommandEnvelope
    from shinobi_runtime.commands.planner import RepositoryCommandPlanner
    from shinobi_runtime.store.repository import RepositoryStore

    root = Path(__file__).resolve().parents[2]
    clone = tmp_path / "project_cash"
    shutil.copytree(root, clone, ignore=shutil.ignore_patterns(".pytest_cache", "__pycache__", "*.pyc"))
    meta = json.loads((clone / "state/meta.json").read_text())
    player = meta["player_id"]

    roster_path_file = clone / "state/martial-world/people/house_tang.json"
    roster = json.loads(roster_path_file.read_text())
    for row in roster["people"]:
        if row.get("person_id") == player:
            row["standing_offices"] = ["leader"]
            row["location_ref"] = "site.house_tang"
    roster_path_file.write_text(json.dumps(roster, ensure_ascii=False, indent=2) + "\n")

    projects_path = clone / "state/martial-world/projects.json"
    projects = json.loads(projects_path.read_text())
    projects["projects"] = {}
    projects_path.write_text(json.dumps(projects, ensure_ascii=False, indent=2) + "\n")

    repo = RepositoryStore(clone)
    planner = RepositoryCommandPlanner(repo)
    command = CommandEnvelope(
        campaign_id=meta["campaign_id"], request_id="request.project.cash.conservation",
        actor_id=player, command_type="jianghu_infrastructure_resolution",
        expected_revision=meta["revision"], submitted_at="2026-08-23T00:00:00Z",
        payload={
            "action": "expand_enterprise", "faction_ref": "house_tang",
            "project_ref": "test.project.cash.conservation",
            "enterprise_type": "trade_merchant_business", "additional_scale": 1000,
        }, mode="gameplay",
    )
    preview = planner.preview(command)
    assert preview.status == "ready"
    plan = planner.plan(command)

    faction_ref = "state/martial-world/factions/house_tang.json"
    market_ref = "state/martial-world/markets/central_plain.json"
    before_faction = repo.read_json(faction_ref)["treasury_cash"]
    before_market = repo.read_json(market_ref)["cash_pool"]
    after_faction = json.loads(plan.writes[faction_ref])["treasury_cash"]
    after_market = json.loads(plan.writes[market_ref])["cash_pool"]
    spent = before_faction - after_faction

    assert spent > 0
    assert plan.result["regional_cash_paid"] == spent
    assert after_market - before_market == spent
    assert (before_faction + before_market) == (after_faction + after_market)


def test_player_travel_toll_fails_closed_when_regional_destination_is_missing(tmp_path):
    import json
    import shutil
    from pathlib import Path

    from shinobi_runtime.api.contracts import CommandRejectedError
    from shinobi_runtime.commands.envelope import CommandEnvelope
    from shinobi_runtime.commands.planner import RepositoryCommandPlanner
    from shinobi_runtime.store.repository import RepositoryStore

    root = Path(__file__).resolve().parents[2]
    clone = tmp_path / "toll_fail_closed"
    shutil.copytree(root, clone, ignore=shutil.ignore_patterns(".pytest_cache", "__pycache__", "*.pyc"))
    meta = json.loads((clone / "state/meta.json").read_text())
    player = meta["player_id"]
    roster_path_file = clone / "state/martial-world/people/house_tang.json"
    roster = json.loads(roster_path_file.read_text())
    for row in roster["people"]:
        if row.get("person_id") == player:
            row["location_ref"] = "site.house_tang"
            row["personal_cash"] = max(1000, int(row.get("personal_cash", 0)))
            row["personal_rations_days"] = 30
    roster_path_file.write_text(json.dumps(roster, ensure_ascii=False, indent=2) + "\n")
    before_cash = next(row["personal_cash"] for row in roster["people"] if row.get("person_id") == player)

    # Luoyang -> Zhengzhou has a positive toll credited to central_plain. If the
    # exact regional owner cannot be resolved, the command must reject before
    # the player's purse is staged downward.
    (clone / "state/martial-world/markets/central_plain.json").unlink()
    repo = RepositoryStore(clone)
    planner = RepositoryCommandPlanner(repo)
    command = CommandEnvelope(
        campaign_id=meta["campaign_id"], request_id="request.travel.toll.fail.closed",
        actor_id=player, command_type="jianghu_strategic_travel_resolution",
        expected_revision=meta["revision"], submitted_at="2026-08-23T00:00:00Z",
        payload={"destination_site_ref": "site.zhengzhou.inn", "mode": "foot"}, mode="gameplay",
    )
    with pytest.raises(CommandRejectedError, match="jianghu_travel_toll_destination_unresolved"):
        planner.preview(command)

    after_roster = repo.read_json("state/martial-world/people/house_tang.json")
    after_cash = next(row["personal_cash"] for row in after_roster["people"] if row.get("person_id") == player)
    assert after_cash == before_cash


def test_long_horizon_global_metrics_include_civic_people_and_civic_cash():
    import json
    from pathlib import Path

    import importlib.util

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("run_long_horizon_test", root / "tools/run_long_horizon.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _person_metrics = module._person_metrics
    _tracked_cash_metrics = module._tracked_cash_metrics
    civic = json.loads((root / "state/martial-world/civic-people.json").read_text())
    independent = json.loads((root / "state/martial-world/independent-people.json").read_text())
    civic_cash = sum(max(0, int(row.get("personal_cash", 0))) for row in civic.get("people", []) if isinstance(row, dict))
    assert civic_cash > 0

    faction_people = 0
    faction_cash = 0
    for path in (root / "state/martial-world/people").glob("*.json"):
        owner = json.loads(path.read_text())
        rows = [row for row in owner.get("people", []) if isinstance(row, dict)]
        faction_people += len(rows)
        faction_cash += sum(max(0, int(row.get("personal_cash", 0))) for row in rows)
    independent_rows = [row for row in independent.get("people", []) if isinstance(row, dict)]
    independent_cash = sum(max(0, int(row.get("personal_cash", 0))) for row in independent_rows)

    cash = _tracked_cash_metrics({})
    people = _person_metrics({})
    assert cash["faction_personal_cash"] == faction_cash
    assert cash["independent_personal_cash"] == independent_cash
    assert cash["civic_personal_cash"] == civic_cash
    assert cash["personal_cash"] == faction_cash + independent_cash + civic_cash
    assert people["people"] == faction_people + len(independent_rows) + len(civic.get("people", []))
    assert people["living_civic_people"] + people["dead_civic_identities"] == len(civic.get("people", []))
    assert people["duplicate_person_ids"] == 0


def test_last_member_personal_cash_is_settled_before_faction_extinction():
    from shinobi_runtime.martial_world.faction_existence import settle_extinctions_from_touched_rosters
    from shinobi_runtime.martial_world.faction_registry import REGISTRY_PATH

    dead = _person("last.member", cash=88, dead=True, faction="a")
    records = _records(a_people=[dead])
    records[REGISTRY_PATH] = {
        "schema": "jianghu-faction-registry-1.0", "faction_refs": ["a", "b"], "dormant_estate_refs": [],
    }
    records["state/martial-world/faction-relations.json"] = {
        "schema": "jianghu-faction-relations-1.0", "edges": [
            {"from_faction": "a", "to_faction": "b", "trust": 0},
            {"from_faction": "b", "to_faction": "a", "trust": 0},
        ],
    }
    records["state/martial-world/social.json"] = {"schema": "jianghu-social-state-1.0", "vows": {}}
    writes = {roster_path("a"): copy.deepcopy(records[roster_path("a")])}
    read = _reader(records)

    estate = settle_exact_death_estates(
        read_json=read, writes=writes, faction_refs=["a", "b"], family={"marriages": {}, "parentage": {}},
        dead_refs=["last.member"], place_region={"luoyang": "central_plain"}, site_rows={},
    )
    assert estate["faction_cash"] == 88
    assert writes[faction_path("a")]["treasury_cash"] == 188
    assert _cash(records, writes, roster_path("a"), "last.member") == 0

    def load_faction(fid):
        path = faction_path(fid)
        return path, copy.deepcopy(writes.get(path, records[path]))

    extinction = settle_extinctions_from_touched_rosters(
        read_json=read, writes=writes,
        relations_state=records["state/martial-world/faction-relations.json"],
        load_faction=load_faction,
    )
    assert extinction["extinct_refs"] == ["a"]
    assert writes[faction_path("a")]["status"] == "extinct"
    assert writes[faction_path("a")]["treasury_cash"] == 188
    assert "a" not in extinction["registry"]["faction_refs"]
    assert "a" in extinction["registry"]["dormant_estate_refs"]


def test_cross_faction_due_birth_routes_child_to_mothers_current_faction():
    from shinobi_runtime.martial_world.family_frontier import settle_due_births
    from shinobi_runtime.martial_world.faction_registry import REGISTRY_PATH

    due = datetime(61, 9, 1, 12, 0, 0)
    mother = _person("mother.birth", faction="a", sex="female", location="site.shared")
    father = _person("father.birth", faction="b", sex="male", location="site.shared")
    mother["aptitudes"] = {"physical": 100, "cognitive": 100, "appearance": 100}
    father["aptitudes"] = {"physical": 100, "cognitive": 100, "appearance": 100}
    mother["appearance"] = father["appearance"] = 100
    family = {
        "marriages": {
            "m.birth": {
                "status": "married", "spouse_refs": ["mother.birth", "father.birth"],
                "faction_refs": ["a", "b"],
                "pregnancy": {
                    "mother_ref": "mother.birth", "father_ref": "father.birth",
                    "conceived_at": datetime(60, 12, 1).isoformat(), "due_at": due.isoformat(),
                    "child_ref": "child.cross.faction",
                },
            }
        },
        "parentage": {}, "households": {}, "succession_claims": {},
    }
    records = _records(a_people=[mother], b_people=[father])
    records[REGISTRY_PATH] = {
        "schema": "jianghu-faction-registry-1.0", "faction_refs": ["a", "b"], "dormant_estate_refs": [],
    }
    records[faction_path("a")].update({"headquarters": "luoyang", "local_site_ref": "site.a"})
    records[faction_path("b")].update({"headquarters": "luoyang", "local_site_ref": "site.b"})
    records["state/martial-world/family.json"] = family
    records["state/meta.json"] = {"player_id": "someone.else"}
    writes = {}
    result = settle_due_births(
        read_json=_reader(records), writes=writes,
        events=[{
            "event_id": "family_birth_due:child.cross.faction", "kind": "family_birth_due",
            "due_at": due.isoformat(), "owner_ref": "a", "marriage_ref": "m.birth",
            "child_ref": "child.cross.faction",
        }],
        at=due,
    )
    assert result["reviews"][0]["result"] == "birth"
    assert result["reviews"][0]["faction_ref"] == "a"
    a_people = writes[roster_path("a")]["people"]
    b_people = writes.get(roster_path("b"), records[roster_path("b")])["people"]
    assert any(row.get("person_id") == "child.cross.faction" for row in a_people)
    assert not any(row.get("person_id") == "child.cross.faction" for row in b_people)
    assert writes["state/martial-world/family.json"]["parentage"]["child.cross.faction"]["parent_refs"] == ["mother.birth", "father.birth"]


def test_institutional_evolution_does_not_treat_broken_commitment_index_as_everyone_free(monkeypatch):
    import shinobi_runtime.martial_world.institutional_evolution_frontier as evolution
    from shinobi_runtime.martial_world.faction_registry import REGISTRY_PATH

    monkeypatch.setattr(evolution, "derived_commitment_state", lambda _read: (_ for _ in ()).throw(ValueError("broken commitments")))
    monkeypatch.setattr(evolution, "_rules", lambda: {"annual_max_transitions": 1})
    schedule = {"recurring": {"faction_annual": {"owner_refs": ["final.owner"]}}}
    events = [{"schedule_class": "faction_annual", "owner_ref": "final.owner"}]
    records = {REGISTRY_PATH: {"schema": "jianghu-faction-registry-1.0", "faction_refs": [], "dormant_estate_refs": []}}
    with pytest.raises(ValueError, match="broken commitments"):
        evolution.settle_autonomous_institutional_evolution(
            read_json=_reader(records), writes={}, schedule=schedule, events=events, year=61,
            at_iso=datetime(61, 12, 1).isoformat(), player_ref="pc.none", site_rows={},
            relations_state={"edges": []}, family_state={"marriages": {}, "parentage": {}},
            independent_state={"schema": "jianghu-independent-people-state-1.0", "people": []},
            social_state={"relationships": {}},
        )


def test_failed_autonomous_faction_foundation_does_not_charge_rejected_clique(monkeypatch):
    import shinobi_runtime.martial_world.institutional_evolution_frontier as evolution
    from shinobi_runtime.martial_world.faction_registry import REGISTRY_PATH

    monkeypatch.setattr(evolution, "derived_commitment_state", lambda _read: {"person_index": {}})
    monkeypatch.setattr(evolution, "_rules", lambda: {
        "annual_max_transitions": 1,
        "dissolution": {"maximum_living_members": 0},
        "merge": {"mutual_faction_trust_minimum": 1000},
        "split": {"minimum_living_members": 9999},
        "foundation": {
            "minimum_members": 2, "mutual_trust_minimum": 60, "startup_cash_per_member": 500,
            "eligible_site_types": ["guild_hall"], "faction_type": "society", "jianghu_camp": "independent",
        },
    })
    people = [
        _person("bad.one", cash=1000, location="site.a_bad"),
        _person("bad.two", cash=1000, location="site.a_bad"),
        _person("good.one", cash=1000, location="site.b_good"),
        _person("good.two", cash=1000, location="site.b_good"),
    ]
    for row in people:
        row["aptitudes"] = {"leadership": 100}
    independent_state = {"schema": "jianghu-independent-people-state-1.0", "people": people}
    social = {"relationships": {}}
    for a, b in (("bad.one", "bad.two"), ("good.one", "good.two")):
        social["relationships"][f"{a}|{b}"] = {"trust": 100}
        social["relationships"][f"{b}|{a}"] = {"trust": 100}
    sites = {
        # Eligible but malformed destination. This clique must remain untouched.
        "site.a_bad": {"site_type": "guild_hall", "public_access": "public"},
        "site.b_good": {"site_type": "guild_hall", "public_access": "public", "parent_place_ref": "luoyang"},
    }
    records = {
        REGISTRY_PATH: {"schema": "jianghu-faction-registry-1.0", "faction_refs": [], "dormant_estate_refs": []},
        INDEPENDENTS: independent_state,
    }
    schedule = {"recurring": {"faction_annual": {"owner_refs": ["final.owner"]}}}
    events = [{"schedule_class": "faction_annual", "owner_ref": "final.owner"}]
    writes = {}
    result = evolution.settle_autonomous_institutional_evolution(
        read_json=_reader(records), writes=writes, schedule=schedule, events=events, year=61,
        at_iso=datetime(61, 12, 1).isoformat(), player_ref="pc.none", site_rows=sites,
        relations_state={"edges": []}, family_state={"marriages": {}, "parentage": {}},
        independent_state=independent_state, social_state=social,
    )
    assert result["reviews"] and result["reviews"][0]["kind"] == "autonomous_faction_foundation"
    assert result["reviews"][0]["member_refs"] == ["good.one", "good.two"]
    remaining = {row["person_id"]: row for row in writes[INDEPENDENTS]["people"]}
    assert remaining["bad.one"]["personal_cash"] == 1000
    assert remaining["bad.two"]["personal_cash"] == 1000
    new_ref = result["reviews"][0]["faction_ref"]
    founded_roster = writes[roster_path(new_ref)]["people"]
    assert sum(int(row.get("personal_cash", 0)) for row in founded_roster) == 1000
    assert writes[faction_path(new_ref)]["treasury_cash"] == 1000


def test_dead_operation_issue_stays_on_corpse_instead_of_teleporting_to_returning_armory():
    from shinobi_runtime.martial_world.operational_equipment import reclaim_operation_equipment
    from shinobi_runtime.martial_world.property import provenance_claim

    dead = _person("dead.issue", dead=True, faction="a", location="site.target")
    survivor = _person("live.issue", faction="a", location="site.target")
    records = _records(a_people=[dead, survivor])
    records["state/martial-world/equipment-ledger.json"] = {
        "schema": "jianghu-equipment-ledger-1.0",
        "person_loadouts": {
            "dead.issue": {"items": {"weapon_jian": 1}},
            "live.issue": {"items": {"weapon_jian": 1}},
        },
    }
    op_ref = "operation:test:issued-death"
    records["state/martial-world/deployments.json"] = {
        "schema": "jianghu-deployments-state-1.0",
        "deployments": {
            op_ref: {
                "faction_ref": "a",
                "operation_kind": "faction_raid",
                "participant_refs": ["dead.issue", "live.issue"],
                "issued_equipment": {
                    "dead.issue": {"weapon_jian": 1},
                    "live.issue": {"weapon_jian": 1},
                },
                "issued_equipment_baseline": {
                    "dead.issue": {"weapon_jian": 0},
                    "live.issue": {"weapon_jian": 0},
                },
                "issued_equipment_claim_baseline": {
                    "dead.issue": {"weapon_jian": 0},
                    "live.issue": {"weapon_jian": 0},
                },
            }
        },
    }
    writes = {}
    read = _reader(records)

    settle_exact_death_estates(
        read_json=read, writes=writes, faction_refs=["a", "b"],
        family={"marriages": {}, "parentage": {}}, dead_refs=["dead.issue"],
        place_region={"luoyang": "central_plain", "site.target": "central_plain"}, site_rows={},
    )
    prune_dead_from_durable_activities(
        read_json=read, writes=writes, dead_refs=["dead.issue"], faction_refs=["a", "b"],
    )

    deployment = writes["state/martial-world/deployments.json"]["deployments"][op_ref]
    assert deployment["participant_refs"] == ["live.issue"]
    assert deployment["issued_equipment"] == {"live.issue": {"weapon_jian": 1}}
    assert deployment["issued_equipment_baseline"] == {"live.issue": {"weapon_jian": 0}}
    assert deployment["issued_equipment_claim_baseline"] == {"live.issue": {"weapon_jian": 0}}

    ledger = writes["state/martial-world/equipment-ledger.json"]
    assert ledger["person_loadouts"]["dead.issue"]["items"] == {"weapon_jian": 1}
    assert provenance_claim(ledger, "dead.issue", "weapon_jian") == {
        "owner_ref": "a", "quantity": 1, "status": "estate",
    }

    settled = reclaim_operation_equipment(
        operation=deployment, inventory={"faction_ref": "a", "equipment": {}},
        equipment_ledger=ledger,
    )
    assert settled["inventory_after"]["equipment"] == {"weapon_jian": 1}
    assert settled["recovered"] == {"weapon_jian": 1}
    assert settled["equipment_ledger_after"]["person_loadouts"]["dead.issue"]["items"] == {"weapon_jian": 1}
    assert provenance_claim(settled["equipment_ledger_after"], "dead.issue", "weapon_jian") == {
        "owner_ref": "a", "quantity": 1, "status": "estate",
    }
