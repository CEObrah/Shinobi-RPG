from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(rel: str, old: str, new: str) -> None:
    path = ROOT / rel
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{rel}: expected one replacement target, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "tests/current/test_health_targeting.py",
    "def _person():\n    return copy.deepcopy(RepositoryPersonSheetResolver(RepositoryStore(ROOT))('pc_wei_tang'))\n",
    "def _person():\n    person=copy.deepcopy(RepositoryPersonSheetResolver(RepositoryStore(ROOT))('pc_wei_tang'))\n    # Mechanics tests need a stable synthetic baseline, not the live campaign's\n    # current fatigue/injuries/poison state. The save is allowed to progress.\n    person['fatigue_milli']=0\n    person['health']={'status':'ready','injuries':[],'blood_lost_ml':0,'shock':0,'consciousness':100}\n    person['poison_burdens']={}\n    person['pending_poison_burdens']={}\n    return person\n",
)

replace_once(
    "tests/current/test_mounted_combat.py",
    "    base['health'] = {'status': 'ready', 'consciousness': 100, 'injuries': []}\n    return base\n",
    "    base['health'] = {'status': 'ready', 'consciousness': 100, 'injuries': [], 'blood_lost_ml': 0, 'shock': 0}\n    base['fatigue_milli'] = 0\n    base['poison_burdens'] = {}\n    base['pending_poison_burdens'] = {}\n    return base\n",
)

replace_once(
    "tests/current/test_combat_geometry.py",
    "ROOT=Path(__file__).resolve().parents[2]\ndef load(rel): return json.loads((ROOT/rel).read_text())\n\n\ndef _pos",
    "ROOT=Path(__file__).resolve().parents[2]\n\ndef _clean_combat_person(row):\n    person=copy.deepcopy(row)\n    person['fatigue_milli']=0\n    person['health']={'status':'ready','injuries':[],'blood_lost_ml':0,'shock':0,'consciousness':100}\n    person['poison_burdens']={}\n    person['pending_poison_burdens']={}\n    return person\n\ndef load(rel):\n    payload=json.loads((ROOT/rel).read_text())\n    if rel=='state/martial-world/people/house_tang.json':\n        payload=copy.deepcopy(payload)\n        payload['people']=[_clean_combat_person(row) for row in payload.get('people',[])]\n    return payload\n\n\ndef _pos",
)

replace_once(
    "tests/current/test_combat_geometry.py",
    "def test_bilateral_blindness_blocks_visually_aimed_bow():\n",
    "def test_bilateral_blindness_blocks_visually_aimed_bow(monkeypatch):\n",
)

replace_once(
    "tests/current/test_combat_geometry.py",
    "    combat=initialize_combat(combat_ref='t',side_a_refs=[archer['person_id']],side_b_refs=[target['person_id']],people=people,zone_ref='site.house_tang',started_at='x',objective={'kind':'eliminate','target_refs':[target['person_id']]},initial_range_band=2)\n    result=resolve_exchange(combat=combat,people=people,equipment_ledger=ledger,doctrines={},player_ref=archer['person_id'],player_action_kind='bow_shot',player_target_ref=target['person_id'],player_weapon_ref='weapon_bow',player_hit_zone='chest',player_targeting_intent='disable')\n",
    "    combat=initialize_combat(combat_ref='t',side_a_refs=[archer['person_id']],side_b_refs=[target['person_id']],people=people,zone_ref='site.house_tang',started_at='x',objective={'kind':'eliminate','target_refs':[target['person_id']]},initial_range_band=2)\n    import shinobi_runtime.martial_world.exact_combat as exact\n    original_observe=exact._observe_visible_enemies\n    monkeypatch.setattr(exact,'_observe_visible_enemies',lambda combat,actor_ref,enemy_refs,people,at_ms: [] if actor_ref==target['person_id'] else original_observe(combat,actor_ref=actor_ref,enemy_refs=enemy_refs,people=people,at_ms=at_ms))\n    result=resolve_exchange(combat=combat,people=people,equipment_ledger=ledger,doctrines={},player_ref=archer['person_id'],player_action_kind='bow_shot',player_target_ref=target['person_id'],player_weapon_ref='weapon_bow',player_hit_zone='chest',player_targeting_intent='disable')\n",
)

replace_once(
    "tests/current/test_combat_geometry.py",
    "    monkeypatch.setattr(exact,'select_physical_defense',fake_defense)\n    monkeypatch.setattr(exact,'_projectile_interception',lambda **kwargs:{'outcome':'failed','trajectory':dict(kwargs['trajectory']),'speed_factor_milli':1000})\n    result=resolve_exchange(combat=combat,people=people,equipment_ledger=ledger,doctrines={},player_ref=archer['person_id'],player_action_kind='bow_shot',player_target_ref=defender['person_id'],player_weapon_ref='weapon_bow',player_hit_zone='chest',player_targeting_intent='disable')\n",
    "    monkeypatch.setattr(exact,'select_physical_defense',fake_defense)\n    monkeypatch.setattr(exact,'_projectile_interception',lambda **kwargs:{'outcome':'failed','trajectory':dict(kwargs['trajectory']),'speed_factor_milli':1000})\n    original_observe=exact._observe_visible_enemies\n    monkeypatch.setattr(exact,'_observe_visible_enemies',lambda combat,actor_ref,enemy_refs,people,at_ms: [] if actor_ref==defender['person_id'] else original_observe(combat,actor_ref=actor_ref,enemy_refs=enemy_refs,people=people,at_ms=at_ms))\n    result=resolve_exchange(combat=combat,people=people,equipment_ledger=ledger,doctrines={},player_ref=archer['person_id'],player_action_kind='bow_shot',player_target_ref=defender['person_id'],player_weapon_ref='weapon_bow',player_hit_zone='chest',player_targeting_intent='disable')\n",
)

print("combat test isolation patches applied")
