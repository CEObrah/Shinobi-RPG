import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PEOPLE_PATH = ROOT / "state/martial-world/people/black_lance_company.json"
COMBATS_PATH = ROOT / "state/martial-world/combats.json"


def _people():
    doc = json.loads(PEOPLE_PATH.read_text(encoding="utf-8"))
    return {row["person_id"]: row for row in doc["people"]}


def _active_combat():
    doc = json.loads(COMBATS_PATH.read_text(encoding="utf-8"))
    active = [row for row in doc["combats"].values() if row.get("status") == "active"]
    assert len(active) == 1
    return active[0]


def test_live_black_lance_37524_contact_belongs_to_0018_not_0043():
    people = _people()
    p18 = people["mw.person.black_lance_company.0018"]
    p43 = people["mw.person.black_lance_company.0043"]

    injuries18 = (p18.get("health") or {}).get("injuries", [])
    injuries43 = (p43.get("health") or {}).get("injuries", [])
    assert [row.get("created_at") for row in injuries18] == ["37524"]
    assert all(row.get("created_at") != "37524" for row in injuries43)

    combat = _active_combat()
    state18 = combat["combatants"]["mw.person.black_lance_company.0018"]
    state43 = combat["combatants"]["mw.person.black_lance_company.0043"]
    recent18 = state18.get("defense_state", {}).get("recent_attackers", {})
    recent43 = state43.get("defense_state", {}).get("recent_attackers", {})
    assert recent18.get("pc_wei_tang") == 37524
    assert "pc_wei_tang" not in recent43


def test_live_black_lance_contact_repair_does_not_change_combat_roster_or_clock():
    combat = _active_combat()
    assert combat["elapsed_ms"] == 53353
    assert "mw.person.black_lance_company.0018" in combat["sides"]["side_b"]
    assert "mw.person.black_lance_company.0043" in combat["sides"]["side_b"]
