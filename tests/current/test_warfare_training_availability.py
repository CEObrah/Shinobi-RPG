from __future__ import annotations

import copy
from datetime import datetime

from shinobi_runtime.martial_world import warfare
from shinobi_runtime.store import RepositoryStore


def test_released_training_epoch_still_pauses_exact_combatants(monkeypatch):
    repo = RepositoryStore('.')
    combats = copy.deepcopy(repo.read_json('state/martial-world/combats.json'))
    combats.setdefault('combats', {})['combat:test:training-pause'] = {
        'status': 'active',
        'sides': {'side_a': ['char.zhu'], 'side_b': ['char.kai']},
        'combatants': {'char.zhu': {}, 'char.kai': {}},
    }

    def view(path: str):
        if path == 'state/martial-world/combats.json':
            return combats
        return repo.read_json(path)

    captured: list[set[str]] = []

    def fake_settle(faction, roster, *, at_iso, next_intensity_milli=None, paused_refs=()):
        captured.append(set(paused_refs))
        return copy.deepcopy(dict(faction)), copy.deepcopy(dict(roster)), {}

    monkeypatch.setattr(warfare, 'settle_and_reset_faction_training_cycle', fake_settle)
    writes: dict[str, object] = {}
    warfare._settle_released_training(
        view=view,
        writes=writes,
        released_refs=['char.ling'],
        at=datetime.fromisoformat('0061-09-28T12:00:00'),
    )

    assert captured
    assert 'char.ling' in captured[0]
    assert 'char.zhu' in captured[0]
    assert 'char.kai' in captured[0]


def test_new_mobilization_training_reset_pauses_physically_unavailable_people(monkeypatch):
    seen: dict[str, set[str]] = {}

    def read_json(path: str):
        if path == warfare._DEPLOYMENTS:
            return {'deployments': {}}
        if path == warfare._RELATIONS:
            return {'edges': []}
        if path == warfare._LOCAL_SITES:
            return {'sites': {}}
        raise FileNotFoundError(path)

    writes = {
        warfare._DEPLOYMENTS: {
            'deployments': {
                'operation:test:mobilize': {
                    'status': 'mobilizing',
                    'operation_kind': 'faction_raid',
                    'faction_ref': 'faction.a',
                    'target_faction_ref': 'faction.b',
                    'participant_refs': ['seed'],
                    'travel_hours': 24,
                }
            }
        }
    }
    faction = {
        'faction_ref': 'faction.a', 'faction_id': 'faction.a',
        'headquarters': 'home', 'population': 3,
        'autonomy_policy': {'risk_tolerance': 100},
    }
    roster = {'people': [{'person_id': 'seed'}, {'person_id': 'extra'}, {'person_id': 'fighter.away'}]}
    inventory = {'food_ration_days': 1000}

    monkeypatch.setattr(warfare, 'derived_commitment_state', lambda _view: {'commitments': {}, 'person_index': {}})
    monkeypatch.setattr(warfare, 'physical_unavailable_person_refs', lambda _view: {'fighter.away'})
    monkeypatch.setattr(warfare, '_load_faction', lambda _view, _fid: copy.deepcopy(faction))
    monkeypatch.setattr(warfare, '_load_roster', lambda _view, _fid, _faction: copy.deepcopy(roster))
    monkeypatch.setattr(warfare, '_load_inventory', lambda _view, _fid: copy.deepcopy(inventory))
    monkeypatch.setattr(warfare, '_site_rows', lambda _view: {})
    monkeypatch.setattr(warfare, '_person_place', lambda _person, _faction, _sites: 'home')
    monkeypatch.setattr(warfare, '_relation_hostility', lambda _relations, _a, _b: 100)
    monkeypatch.setattr(warfare, 'extend_commitment_resources', lambda state, **_kwargs: state)
    monkeypatch.setattr(warfare, 'compact_faction_state', lambda row: copy.deepcopy(dict(row)))
    monkeypatch.setattr(warfare, 'compact_roster_state', lambda row, *, faction: copy.deepcopy(dict(row)))
    monkeypatch.setattr(warfare, 'compact_inventory_state', lambda row: copy.deepcopy(dict(row)))

    def fake_ready(people, *, year, unavailable_refs, minimum_age):
        seen['ready'] = set(unavailable_refs)
        return [p for p in people if p.get('person_id') not in seen['ready']]

    def fake_pause_refs(_faction, _people, *, unavailable_refs=()):
        seen['pause'] = set(unavailable_refs)
        return sorted(seen['pause'])

    monkeypatch.setattr(warfare, 'combat_ready_members', fake_ready)
    monkeypatch.setattr(warfare, 'institutional_training_pause_refs', fake_pause_refs)
    monkeypatch.setattr(warfare, '_pause_people', lambda faction, roster, refs, *, at_iso, paused_refs=(): (faction, roster))

    reviews = warfare.expand_new_strategic_mobilizations(
        read_json=read_json, writes=writes, at=datetime.fromisoformat('0061-09-28T12:00:00')
    )
    assert reviews and reviews[0]['kind'] == 'strategic_mobilization_expanded'
    assert 'fighter.away' in seen['ready']
    assert 'fighter.away' in seen['pause']
