from __future__ import annotations

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected exactly one anchor in {path}, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# 1) Severe fatigue must degrade performance without deleting relative capability.
exertion = ROOT / "runtime/shinobi_runtime/martial_world/combat_exertion.py"
replace_once(
    exertion,
    "_REFERENCE_ENDURANCE = 80\n\n\ndef fatigue_performance_milli(fatigue_milli: int) -> int:\n    \"\"\"Whole-body combat performance remaining under current fatigue.\n\n    ``fatigue_milli`` is normalized physiological burden: 0 is fresh and 3000\n    is complete combat exhaustion. Burden may exceed 3000 as recovery debt, but\n    it cannot produce negative capability. Endurance changes how quickly burden\n    accumulates; it does not create a second hidden fatigue scale.\n    \"\"\"\n    fatigue = max(0, min(3000, int(fatigue_milli)))\n    return max(0, 1000 - fatigue // 3)\n",
    "_REFERENCE_ENDURANCE = 80\n_FATIGUE_MAX_PENALTY_MILLI = 3000\n_FATIGUE_PERFORMANCE_FLOOR_MILLI = 250\n\n\ndef fatigue_performance_milli(fatigue_milli: int) -> int:\n    \"\"\"Whole-body combat performance remaining under current fatigue.\n\n    ``fatigue_milli`` is normalized physiological burden: 0 is fresh and 3000\n    reaches the maximum combat-performance penalty. Burden may exceed 3000 as\n    recovery debt, but severe exhaustion must not erase every difference in\n    skill, speed, perception, strength, or dexterity. A 250-milli floor keeps\n    exhausted fighters badly degraded while preserving relative capability so\n    elite and ordinary combatants do not collapse to the same zero-stat actor.\n    Endurance changes how quickly burden accumulates; it does not create a\n    second hidden fatigue scale.\n    \"\"\"\n    fatigue = max(0, int(fatigue_milli))\n    if fatigue >= _FATIGUE_MAX_PENALTY_MILLI:\n        return _FATIGUE_PERFORMANCE_FLOOR_MILLI\n    usable = 1000 - _FATIGUE_PERFORMANCE_FLOOR_MILLI\n    penalty = fatigue * usable // _FATIGUE_MAX_PENALTY_MILLI\n    return max(_FATIGUE_PERFORMANCE_FLOOR_MILLI, 1000 - penalty)\n",
)

# 2) Register a temporary aggressive engagement template. Resource discipline and
# targeting stay identical to Wei's standing doctrine; only immediate engagement
# posture changes, and the wrapper restores the saved doctrine before persistence.
doctrine_path = ROOT / "game/data/martial-world/combat-doctrines.json"
doctrine_data = json.loads(doctrine_path.read_text(encoding="utf-8"))
templates = doctrine_data["individual_templates"]
base_ref = "doctrine.tang_wei.precision_function_denial"
override_ref = "doctrine.tang_wei.precision_function_denial.lethal_pursuit"
base = copy.deepcopy(templates[base_ref])
override = copy.deepcopy(base)
override["engagement"].update(
    {
        "initiative_posture": "assertive",
        "commitment_posture": "committed",
        "pursuit_posture": "persistent",
        "movement_economy": "mobile",
        "finishing_window": "commit_decisively",
    }
)
templates[override_ref] = override
doctrine_path.write_text(json.dumps(doctrine_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

# 3) An explicit lethal until-resolution span is the registered representation of
# a relentless lethal pursuit. Apply the aggressive doctrine only inside the span.
extended = ROOT / "runtime/shinobi_runtime/commands/jianghu_extended.py"
replace_once(
    extended,
    "    doctrine_ref=(\n        str(people_cursor[player_ref].get('combat_doctrine_ref'))\n        if isinstance(people_cursor.get(player_ref),Mapping) and people_cursor[player_ref].get('combat_doctrine_ref')\n        else None\n    )\n    side_by_ref={\n",
    "    doctrine_ref=(\n        str(people_cursor[player_ref].get('combat_doctrine_ref'))\n        if isinstance(people_cursor.get(player_ref),Mapping) and people_cursor[player_ref].get('combat_doctrine_ref')\n        else None\n    )\n    explicit_lethal_pursuit=(\n        bool(until_resolution) and str(targeting_intent or '')=='lethal'\n    )\n    if explicit_lethal_pursuit and isinstance(people_cursor.get(player_ref),dict):\n        # This is an immediate command-span override, not a saved doctrine edit.\n        # Keep Wei's force/resource/targeting policy, but do not reinterpret an\n        # explicit relentless lethal order through a restrained pursuit posture.\n        people_cursor[player_ref]['combat_doctrine_ref']='doctrine.tang_wei.precision_function_denial.lethal_pursuit'\n    side_by_ref={\n",
)
replace_once(
    extended,
    "    continuation_required=(stop_reason=='execution_frontier' and str(combat_cursor.get('status') or '')=='active')\n    return {\n        **last_resolved,\n        'combat_after':combat_cursor,'people_after':people_cursor,\n",
    "    continuation_required=(stop_reason=='execution_frontier' and str(combat_cursor.get('status') or '')=='active')\n    if explicit_lethal_pursuit and isinstance(people_cursor.get(player_ref),dict):\n        # Never persist the command-span override as Tang Wei's standing policy.\n        if doctrine_ref:\n            people_cursor[player_ref]['combat_doctrine_ref']=doctrine_ref\n        else:\n            people_cursor[player_ref].pop('combat_doctrine_ref',None)\n    return {\n        **last_resolved,\n        'combat_after':combat_cursor,'people_after':people_cursor,\n",
)

# 4) GM presentation: preserve compound combat intent and never infer negative
# outcomes from sampled pages of a paginated committed transition.
combat_ref = ROOT / "plugins/shinobi-rpg/skill/shinobi-game-master/references/combat.md"
replace_once(
    combat_ref,
    "A longer instruction such as **fight for thirty seconds**, **keep attacking**, or **finish the fight** may carry the same standing policy through many exact exchanges. This is valid player delegation. Do not force Tang Wei to micromanage every swing merely to keep the fight moving.\n\nA long standing combat span is never permission to erase the fight as a scene.",
    "A longer instruction such as **fight for thirty seconds**, **keep attacking**, or **finish the fight** may carry the same standing policy through many exact exchanges. This is valid player delegation. Do not force Tang Wei to micromanage every swing merely to keep the fight moving.\n\nPreserve compound engagement intent, not only force intent. **Kill as many as possible as quickly as possible**, **run them down**, or another explicit relentless lethal-until-resolution declaration must not be reduced to `targeting_intent: lethal` while silently restoring a restrained pursuit posture for the omitted tempo. The registered lethal-until-resolution combat span supplies a temporary assertive, committed, persistent, mobile engagement override while retaining Wei's standing resource discipline and targeting policy; it must never overwrite his saved doctrine.\n\nA long standing combat span is never permission to erase the fight as a scene.",
)
replace_once(
    combat_ref,
    "A combat command receipt that contains ordered `events` is transition evidence. Preserve that event sequence while refreshing play context. The refreshed context establishes current truth; the receipt establishes how the committed transition happened. Do not throw away the events and reconstruct the fight afterward from final health totals.\n\nFor any committed combat span, narrate the fight in chronological scene beats before presenting a compact status summary.",
    "A combat command receipt that contains ordered `events` is transition evidence. Preserve that event sequence while refreshing play context. The refreshed context establishes current truth; the receipt establishes how the committed transition happened. Do not throw away the events and reconstruct the fight afterward from final health totals.\n\nWhen current-transition recovery is paginated, follow `next_object_ref` sequentially from the first page until it is null before making any negative claim about the span. Never sample arbitrary event offsets and infer an absence. Claims such as **no kill**, **no wound**, **no Qi use**, **no casualty**, or **nothing material happened** require complete receipt coverage or an authoritative bounded summary that explicitly establishes that absence. If complete chronology cannot be recovered, narrate only what the recovered evidence positively establishes and say the omitted outcome is unresolved rather than inventing certainty.\n\nFor any committed combat span, narrate the fight in chronological scene beats before presenting a compact status summary.",
)

# 5) Regression coverage.
test_path = ROOT / "tests/current/test_combat_lethal_pursuit_repair.py"
test_path.write_text(
    '''from __future__ import annotations\n\nimport copy\nfrom pathlib import Path\n\nimport shinobi_runtime.commands.jianghu_extended as extended\nfrom shinobi_runtime.martial_world.combat_exertion import fatigue_performance_milli\nfrom shinobi_runtime.martial_world.doctrines import resolve_individual_doctrine\nfrom shinobi_runtime.martial_world.exact_combat import capability_from_person\n\nROOT = Path(__file__).resolve().parents[2]\n\n\ndef _person(*, sword: int, strength: int, speed: int, dexterity: int, perception: int, fatigue: int) -> dict:\n    return {\n        "attributes": {\n            "strength": strength, "speed": speed, "dexterity": dexterity,\n            "endurance": 84, "perception": perception, "willpower": 70,\n        },\n        "martial_skills": {"sword": sword},\n        "health": {"status": "ready", "injuries": []},\n        "fatigue_milli": fatigue,\n        "body_mass_kg": 64,\n    }\n\n\ndef test_severe_fatigue_preserves_relative_combat_capability():\n    assert fatigue_performance_milli(0) == 1000\n    assert fatigue_performance_milli(3000) == 250\n    assert fatigue_performance_milli(9000) == 250\n\n    wei = capability_from_person(\n        _person(sword=115, strength=82, speed=90, dexterity=94, perception=96, fatigue=9000),\n        action_skill="sword",\n    )\n    ordinary = capability_from_person(\n        _person(sword=45, strength=58, speed=58, dexterity=58, perception=58, fatigue=9000),\n        action_skill="sword",\n    )\n    assert wei.offense > ordinary.offense > 0\n    assert wei.control > ordinary.control > 0\n    assert wei.reaction > ordinary.reaction > 0\n    assert wei.mobility > ordinary.mobility > 0\n\n\ndef test_lethal_pursuit_template_changes_engagement_only():\n    base = resolve_individual_doctrine("doctrine.tang_wei.precision_function_denial")\n    pursuit = resolve_individual_doctrine("doctrine.tang_wei.precision_function_denial.lethal_pursuit")\n    assert pursuit["engagement"]["initiative_posture"] == "assertive"\n    assert pursuit["engagement"]["commitment_posture"] == "committed"\n    assert pursuit["engagement"]["pursuit_posture"] == "persistent"\n    assert pursuit["engagement"]["movement_economy"] == "mobile"\n    assert pursuit["resource_discipline"] == base["resource_discipline"]\n    assert pursuit["force_policy"] == base["force_policy"]\n    assert pursuit["targeting"] == base["targeting"]\n\n\ndef test_explicit_lethal_until_resolution_uses_temporary_pursuit_doctrine(monkeypatch):\n    seen: list[str | None] = []\n\n    monkeypatch.setattr(extended, "hydrate_equipment_ledger", lambda value: copy.deepcopy(value))\n    monkeypatch.setattr(extended, "compact_equipment_ledger", lambda value: copy.deepcopy(value))\n    monkeypatch.setattr(extended, "apply_martial_events", lambda state, events, side_by_ref: copy.deepcopy(state))\n\n    def fake_default_target_for(*, people, actor_ref, **kwargs):\n        seen.append(people[actor_ref].get("combat_doctrine_ref"))\n        return "enemy"\n\n    def fake_default_action_for(**kwargs):\n        return "cut", "weapon_jian"\n\n    def fake_resolve_exchange(**kwargs):\n        people = copy.deepcopy(kwargs["people"])\n        seen.append(people[kwargs["player_ref"]].get("combat_doctrine_ref"))\n        combat = copy.deepcopy(kwargs["combat"])\n        combat["status"] = "resolved"\n        combat["elapsed_ms"] = int(combat.get("elapsed_ms", 0)) + 1000\n        return {\n            "combat_after": combat,\n            "people_after": people,\n            "equipment_ledger_after": copy.deepcopy(kwargs["equipment_ledger"]),\n            "events": [],\n        }\n\n    monkeypatch.setattr(extended, "default_target_for", fake_default_target_for)\n    monkeypatch.setattr(extended, "default_action_for", fake_default_action_for)\n    monkeypatch.setattr(extended, "resolve_exchange", fake_resolve_exchange)\n\n    original = "doctrine.tang_wei.precision_function_denial"\n    result = extended._resolve_player_combat_span(\n        combat={"status": "active", "elapsed_ms": 0, "sides": {"a": ["pc"], "b": ["enemy"]}},\n        people={\n            "pc": {"combat_doctrine_ref": original, "health": {"status": "ready"}},\n            "enemy": {"faction_ref": "enemy", "health": {"status": "ready"}},\n        },\n        equipment_ledger={}, doctrines={}, player_ref="pc", social_state={}, player_retinue_context=None,\n        raw_target_ref="auto", raw_action_kind="attack", raw_weapon_ref="auto", hit_zone="auto",\n        target_structure_ref=None, targeting_intent="lethal", explicit_poison_ref=None, poison_auto=False,\n        explicit_qi_allocation_milli=None, qi_auto=False, exchange_count=None, duration_seconds=None,\n        until_resolution=True, frontier_exchanges=4,\n    )\n\n    assert seen == [\n        "doctrine.tang_wei.precision_function_denial.lethal_pursuit",\n        "doctrine.tang_wei.precision_function_denial.lethal_pursuit",\n    ]\n    assert result["people_after"]["pc"]["combat_doctrine_ref"] == original\n\n\ndef test_combat_narration_requires_complete_paginated_receipt_before_absence_claims():\n    text = (ROOT / "plugins/shinobi-rpg/skill/shinobi-game-master/references/combat.md").read_text(encoding="utf-8")\n    assert "follow `next_object_ref` sequentially from the first page until it is null" in text\n    assert "Never sample arbitrary event offsets and infer an absence" in text\n    assert "Kill as many as possible as quickly as possible" in text\n''',
    encoding="utf-8",
)

# The temporary workflow and script are scaffolding only; remove both from the
# final branch commit so the PR contains only the product fix and regressions.
(ROOT / ".github/workflows/ooc-combat-fix.yml").unlink(missing_ok=True)
Path(__file__).unlink(missing_ok=True)
