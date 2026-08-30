import copy

from shinobi_runtime.martial_world.institutional_evolution_frontier import settle_autonomous_institutional_evolution


def person(ref, *, location="site.home", cash=0, leader=False):
    return {
        "person_id": ref, "name": ref.replace("p.", "Person "), "birth_year": 30, "sex": "male",
        "appearance": 50,
        "aptitudes": {"physical": 100, "martial": 100, "qi": 100, "cognitive": 100, "leadership": 100},
        "attributes": {"strength": 50, "speed": 50, "dexterity": 50, "endurance": 50, "perception": 50, "intelligence": 50, "willpower": 50},
        "martial_skills": {}, "professional_skills": {}, "body_mass_kg": 65,
        "membership_grade": "full", "standing_offices": ["leader"] if leader else [],
        "location_ref": location, "personal_cash": cash,
    }


def faction(fid, *, treasury=100, site="site.home", controlled=None):
    row = {
        "schema":"jianghu-faction-state-1.0", "faction_id":fid, "name":fid, "type":"society",
        "headquarters":"place.home", "local_site_ref":site, "treasury_cash":treasury,
        "buildings":{}, "enterprises":{}, "jianghu_camp":"independent",
    }
    if controlled is not None:
        row["controlled_estates"] = controlled
    return row


def roster(fid, people):
    return {"schema":"jianghu-person-lite-roster-1.0", "faction_ref":fid, "people":people}


def inventory(fid, *, food=0, equipment=None):
    row={"schema":"jianghu-faction-inventory-1.0","faction_ref":fid,"food_ration_days":food}
    if equipment: row["equipment"] = dict(equipment)
    return row


def run(state, *, social=None, relations=None, sites=None):
    state=copy.deepcopy(state)
    state.setdefault("state/martial-world/faction-relations.json", relations or {"edges":[]})
    state.setdefault("state/martial-world/family.json", {"schema":"jianghu-family-state-1.0","marriages":{},"parentage":{},"households":{},"succession_claims":{}})
    state.setdefault("state/martial-world/social.json", social or {"relationships":{}})
    state.setdefault("state/martial-world/independent-people.json", {"schema":"jianghu-independent-people-state-1.0","people":[]})
    writes={}
    def read(path):
        if path in state: return copy.deepcopy(state[path])
        raise FileNotFoundError(path)
    result=settle_autonomous_institutional_evolution(
        read_json=read, writes=writes,
        schedule={"recurring":{"faction_annual":{"owner_refs":["f.final"]}}},
        events=[{"schedule_class":"faction_annual","owner_ref":"f.final"}],
        year=62, at_iso="0062-01-01T00:00:00", player_ref="pc",
        site_rows=sites or {}, relations_state=state["state/martial-world/faction-relations.json"],
        family_state=state["state/martial-world/family.json"],
        independent_state=state["state/martial-world/independent-people.json"],
        social_state=state["state/martial-world/social.json"],
    )
    return result,writes


def test_autonomous_dissolution_moves_exact_living_person_only():
    fid="faction.test_dissolve"
    state={
        "state/martial-world/faction-registry.json":{"schema":"jianghu-faction-registry-1.0","faction_refs":[fid],"dormant_estate_refs":[]},
        f"state/martial-world/factions/{fid}.json":faction(fid,treasury=0),
        f"state/martial-world/people/{fid}.json":roster(fid,[person("p.one",leader=True)]),
        f"state/martial-world/inventories/{fid}.json":inventory(fid,food=0),
    }
    result,writes=run(state)
    assert result["reviews"][0]["kind"]=="autonomous_faction_dissolution"
    assert fid not in result["registry"]["faction_refs"]
    assert fid in result["registry"]["dormant_estate_refs"]
    assert [p["person_id"] for p in writes["state/martial-world/independent-people.json"]["people"]]==["p.one"]


def test_autonomous_merger_moves_people_cash_inventory_and_estate_control():
    a,b="faction.merge_a","faction.merge_b"
    estate={"site.a2":{"source_faction_ref":a,"acquired_at":"0061-01-01T00:00:00","status":"occupied","headquarters_place_ref":"place.a2","buildings":{},"infrastructure":{},"enterprises":{}}}
    state={
        "state/martial-world/faction-registry.json":{"schema":"jianghu-faction-registry-1.0","faction_refs":[a,b],"dormant_estate_refs":[]},
        f"state/martial-world/factions/{a}.json":faction(a,treasury=50,site="site.a",controlled=estate),
        f"state/martial-world/people/{a}.json":roster(a,[person("p.a1",leader=True),person("p.a2")]),
        f"state/martial-world/inventories/{a}.json":inventory(a,food=20,equipment={"Jian":4}),
        f"state/martial-world/factions/{b}.json":faction(b,treasury=200,site="site.b"),
        f"state/martial-world/people/{b}.json":roster(b,[person("p.b1",leader=True),person("p.b2"),person("p.b3")]),
        f"state/martial-world/inventories/{b}.json":inventory(b,food=30,equipment={"Jian":1}),
    }
    rel={"edges":[{"from_faction":a,"to_faction":b,"trust":90},{"from_faction":b,"to_faction":a,"trust":90}]}
    result,writes=run(state,relations=rel)
    review=result["reviews"][0]
    assert review["kind"]=="autonomous_faction_merger"
    source,target=review["source_faction_ref"],review["target_faction_ref"]
    assert source==a and target==b
    assert writes[f"state/martial-world/factions/{b}.json"]["treasury_cash"]==250
    assert writes[f"state/martial-world/inventories/{b}.json"]["food_ration_days"]==50
    assert writes[f"state/martial-world/inventories/{b}.json"]["equipment"]["Jian"]==5
    assert "site.a" in writes[f"state/martial-world/factions/{b}.json"]["controlled_estates"]
    assert "site.a2" in writes[f"state/martial-world/factions/{b}.json"]["controlled_estates"]


def test_autonomous_split_requires_real_dissident_clique_at_controlled_estate():
    fid="faction.split_parent"; estate_site="site.branch"
    estate={estate_site:{"source_faction_ref":fid,"acquired_at":"0061-01-01T00:00:00","status":"occupied","headquarters_place_ref":"place.branch","buildings":{"main_hall":{"level":1}},"infrastructure":{},"enterprises":{}}}
    people=[person("p.leader",leader=True)] + [person(f"p.{i}",location=estate_site if i in (1,2) else "site.home") for i in range(1,6)]
    state={
        "state/martial-world/faction-registry.json":{"schema":"jianghu-faction-registry-1.0","faction_refs":[fid],"dormant_estate_refs":[]},
        f"state/martial-world/factions/{fid}.json":faction(fid,treasury=600,controlled=estate),
        f"state/martial-world/people/{fid}.json":roster(fid,people),
        f"state/martial-world/inventories/{fid}.json":inventory(fid,food=120,equipment={"Jian":12}),
    }
    social={"relationships":{
        "p.1|p.leader":{"trust":-70}, "p.2|p.leader":{"trust":-50},
        "p.2|p.1":{"trust":70},
    }}
    result,writes=run(state,social=social,sites={estate_site:{"site_type":"martial_headquarters","parent_place_ref":"place.branch"}})
    review=result["reviews"][0]
    assert review["kind"]=="autonomous_faction_split"
    new=review["new_faction_ref"]
    assert set(review["member_refs"])=={"p.1","p.2"}
    assert estate_site==writes[f"state/martial-world/factions/{new}.json"]["local_site_ref"]
    assert estate_site not in writes[f"state/martial-world/factions/{fid}.json"].get("controlled_estates",{})
    assert writes[f"state/martial-world/inventories/{new}.json"]["food_ration_days"]==40
    assert writes[f"state/martial-world/inventories/{new}.json"]["equipment"]["Jian"]==4


def test_autonomous_foundation_consumes_independent_people_and_their_real_cash():
    p1=person("p.founder",location="site.guild",cash=800); p2=person("p.friend",location="site.guild",cash=700)
    # independent rows have no faction membership authority
    for p in (p1,p2): p.pop("membership_grade",None)
    state={
        "state/martial-world/faction-registry.json":{"schema":"jianghu-faction-registry-1.0","faction_refs":[],"dormant_estate_refs":[]},
        "state/martial-world/independent-people.json":{"schema":"jianghu-independent-people-state-1.0","people":[p1,p2]},
    }
    social={"relationships":{"p.founder|p.friend":{"trust":70},"p.friend|p.founder":{"trust":70}}}
    sites={"site.guild":{"site_type":"guild_hall","parent_place_ref":"place.guild","public_access":"public"}}
    result,writes=run(state,social=social,sites=sites)
    review=result["reviews"][0]
    assert review["kind"]=="autonomous_faction_foundation"
    new=review["faction_ref"]
    assert review["startup_cash"]==1000
    assert writes["state/martial-world/independent-people.json"]["people"]==[]
    assert writes[f"state/martial-world/factions/{new}.json"]["treasury_cash"]==1000
    assert writes[f"state/martial-world/factions/{new}.json"]["training"] == {}
    assert len(writes[f"state/martial-world/people/{new}.json"]["people"])==2
