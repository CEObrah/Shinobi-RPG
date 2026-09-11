from datetime import datetime

from shinobi_runtime.martial_world.life_frontier import settle_annual_life_frontier


def _person(ref, birth_year, *, office=None, affiliation="government.imperial_court", home="luoyang", cash=0):
    row = {
        "person_id": ref,
        "name": ref,
        "birth_year": birth_year,
        "sex": "male",
        "body_mass_kg": 70,
        "appearance": 50,
        "aptitudes": {"physical": 80, "martial": 80, "qi": 80, "cognitive": 90, "leadership": 90},
        "attributes": {"strength": 50, "speed": 50, "dexterity": 50, "endurance": 50, "perception": 50, "intelligence": 60, "willpower": 60},
        "martial_skills": {"command": 20},
        "professional_skills": {"administration": 60, "commerce": 20, "medicine": 0, "crafting": 0, "instruction": 0},
        "qi": 5,
        "qi_control": 5,
        "standing_offices": [office] if office else [],
        "affiliation_ref": affiliation,
        "social_rank": "official",
        "home_place_ref": home,
        "location_ref": home,
        "public_site_types": ["government_office"],
        "personal_cash": cash,
    }
    return row


def _settle(*, final=True, civic=None, independent=None, civilians=None, family=None):
    schedule = {"recurring": {"faction_annual": {"owner_refs": ["a", "z"]}}}
    event = {"kind": "test_global_life_boundary", "schedule_class": "faction_annual", "owner_ref": "z" if final else "a"}
    markets = {"central_plain": {"schema": "jianghu-market-state-1.0", "cash_pool": 100}}
    writes = {}
    reviews = []

    def load_market(region):
        return f"state/martial-world/markets/{region}.json", markets[region]

    result = settle_annual_life_frontier(
        events=[event], at=datetime(61, 9, 14), player_ref="pc",
        family_state=family or {"schema":"jianghu-family-state-1.0","marriages":{},"parentage":{},"households":{},"succession_claims":{}},
        social_state={"schema":"jianghu-social-state-1.0","relationships":{},"courtships":{}},
        custody_state={"schema":"jianghu-custody-state-1.0","records":[]},
        independent_state={"schema":"jianghu-independent-people-state-1.0","people":independent or []},
        civic_state={"schema":"jianghu-civic-people-state-1.0","people":civic or []},
        civilian_state=civilians or {"schema":"jianghu-civilian-populations-1.0","places":{"luoyang":{"current_population":1000}}},
        schedule=schedule, world_seed="test-world", place_region={"luoyang":"central_plain"}, site_rows={},
        load_market=load_market, market_cache={}, writes=writes, reviews=reviews, handoffs=[],
        pending_training_resume_refs=set(),
        load_faction=lambda _fid: (_ for _ in ()).throw(AssertionError("faction load not expected")),
        load_roster=lambda _fid: (_ for _ in ()).throw(AssertionError("roster load not expected")),
        committed_person_refs=lambda:set(), active_combat_person_refs=lambda:set(), unavailable_person_refs=lambda:set(),
        family_bound_refs=lambda _fid:set(), faction_cache={}, roster_cache={},
    )
    return result, writes, reviews, markets


def test_global_nonfaction_life_runs_only_on_final_faction_annual_chunk():
    old = _person("civic.old", -200, office="magistrate", affiliation="government.luoyang", cash=25)
    result, writes, reviews, markets = _settle(final=False, civic=[old])
    assert not any(row.get("kind") == "annual_nonfaction_life_review" for row in reviews)
    assert "state/martial-world/civic-people.json" not in writes
    assert markets["central_plain"]["cash_pool"] == 100

    result, writes, reviews, markets = _settle(final=True, civic=[old])
    review = next(row for row in reviews if row.get("kind") == "annual_nonfaction_life_review")
    assert review["natural_death_count"] == 1
    assert review["estate_cash_settled"] == 25
    assert markets["central_plain"]["cash_pool"] == 125


def test_emperor_succeeds_to_existing_prince_without_minting_person():
    emperor = _person("civic.emperor", -200, office="emperor", cash=30)
    prince = _person("civic.prince", 40, office="prince", cash=5)
    civilians = {"schema":"jianghu-civilian-populations-1.0","places":{"luoyang":{"current_population":1000,"identity_ordinal_cursor":7}}}
    result, writes, reviews, _markets = _settle(final=True, civic=[emperor, prince], civilians=civilians)
    rows = result["civic_state"]["people"]
    successor = next(row for row in rows if row["person_id"] == "civic.prince")
    assert "emperor" in successor.get("standing_offices", [])
    assert "prince" not in successor.get("standing_offices", [])
    assert len(rows) == 2
    assert result["civilian_state"]["places"]["luoyang"]["current_population"] == 1000


def test_local_office_vacancy_consumes_aggregate_civilian_instead_of_minting():
    magistrate = _person("civic.magistrate", -200, office="magistrate", affiliation="government.luoyang")
    civilians = {"schema":"jianghu-civilian-populations-1.0","places":{"luoyang":{"current_population":1000,"identity_ordinal_cursor":7}}}
    result, writes, reviews, _markets = _settle(final=True, civic=[magistrate], civilians=civilians)
    rows = result["civic_state"]["people"]
    living_magistrates = [row for row in rows if "magistrate" in row.get("standing_offices", []) and row.get("health",{}).get("status") != "dead"]
    assert len(living_magistrates) == 1
    assert len(rows) == 2
    assert result["civilian_state"]["places"]["luoyang"]["current_population"] == 999
    assert result["civilian_state"]["places"]["luoyang"]["identity_ordinal_cursor"] == 8
    assert "state/martial-world/civilian-populations.json" in writes


def test_nonfaction_estate_prefers_exact_living_spouse():
    dead = _person("ind.dead", -200, cash=77, affiliation="", home="luoyang")
    spouse = _person("ind.spouse", 35, cash=3, affiliation="", home="luoyang")
    family = {
        "schema":"jianghu-family-state-1.0",
        "marriages":{"m":{"spouse_refs":["ind.dead","ind.spouse"],"status":"married","faction_refs":[]}},
        "parentage":{},"households":{},"succession_claims":{},
    }
    result, _writes, _reviews, markets = _settle(final=True, independent=[dead, spouse], family=family)
    rows = {row["person_id"]: row for row in result["independent_state"]["people"]}
    assert rows["ind.dead"].get("personal_cash", 0) == 0
    assert rows["ind.spouse"].get("personal_cash", 0) == 80
    assert markets["central_plain"]["cash_pool"] == 100
    assert result["family_state"]["marriages"]["m"]["status"] == "widowed"
