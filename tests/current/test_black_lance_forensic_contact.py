import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/black_lance_forensic_contact.json"


def _snapshot():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_black_lance_37524_contact_belongs_to_0018_not_0043():
    snapshot = _snapshot()
    people = snapshot["people"]
    p18 = people["mw.person.black_lance_company.0018"]
    p43 = people["mw.person.black_lance_company.0043"]

    injuries18 = (p18.get("health") or {}).get("injuries", [])
    injuries43 = (p43.get("health") or {}).get("injuries", [])
    assert [row.get("created_at") for row in injuries18] == ["37524"]
    assert all(row.get("created_at") != "37524" for row in injuries43)

    combat = snapshot["combat"]
    state18 = combat["combatants"]["mw.person.black_lance_company.0018"]
    state43 = combat["combatants"]["mw.person.black_lance_company.0043"]
    recent18 = state18.get("defense_state", {}).get("recent_attackers", {})
    recent43 = state43.get("defense_state", {}).get("recent_attackers", {})
    assert recent18.get("pc_wei_tang") == 37524
    assert "pc_wei_tang" not in recent43


def test_black_lance_forensic_fixture_preserves_roster_and_clock():
    combat = _snapshot()["combat"]
    assert combat["elapsed_ms"] == 53353
    assert "mw.person.black_lance_company.0018" in combat["sides"]["side_b"]
    assert "mw.person.black_lance_company.0043" in combat["sides"]["side_b"]
