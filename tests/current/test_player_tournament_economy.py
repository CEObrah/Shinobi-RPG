import copy
import json
from pathlib import Path

from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
import shinobi_runtime.commands.jianghu as jianghu_commands
from shinobi_runtime.martial_world.faction_state import faction_path, roster_path
from shinobi_runtime.martial_world.live_state import roster_person
from shinobi_runtime.martial_world.person_state import hydrate_roster_state
from shinobi_runtime.martial_world.tournaments import open_tournament
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store import RepositoryStore

ROOT = Path(__file__).resolve().parents[2]


def _command(meta, *, request_id, action, tournament_ref):
    return CommandEnvelope(
        request_id=request_id,
        command_type='jianghu_tournament_resolution',
        payload={'action': action, 'tournament_ref': tournament_ref},
        campaign_id=meta['campaign_id'],
        actor_id=meta['player_id'],
        expected_revision=meta['revision'],
        submitted_at='2026-08-21T16:00:00Z',
        mode='gameplay',
    )


def _read_write(plan, path):
    return json.loads(plan.writes[path].decode('utf-8'))


def _fixture_read_json_at_house_tang(base_read_json, tournament_state, *, player_ref):
    player_roster_path = roster_path('house_tang')

    def read_json(path):
        key = str(path)
        if key == jianghu_commands._TOURNAMENTS:
            return copy.deepcopy(tournament_state)
        if key == jianghu_commands._ROUTE_OPS:
            state = copy.deepcopy(base_read_json(path))
            movements = state.get('movements', {}) if isinstance(state, dict) else {}
            if isinstance(movements, dict):
                state['movements'] = {
                    ref: row for ref, row in movements.items()
                    if not (
                        isinstance(row, dict)
                        and player_ref in [str(x) for x in row.get('participant_refs', []) if isinstance(x, str)]
                    )
                }
            return state
        if key == jianghu_commands._COMBATS:
            state = copy.deepcopy(base_read_json(path))
            combats = state.get('combats', {}) if isinstance(state, dict) else {}
            if isinstance(combats, dict):
                for row in combats.values():
                    if not isinstance(row, dict) or str(row.get('status') or '') in {'resolved', 'completed', 'closed'}:
                        continue
                    participants = [str(x) for x in row.get('participant_refs', []) if isinstance(x, str)]
                    if not participants:
                        participants = [str(x) for x in row.get('participants', []) if isinstance(x, str)]
                    sides = row.get('sides', {}) if isinstance(row.get('sides'), dict) else {}
                    for members in sides.values():
                        if isinstance(members, list):
                            participants.extend(str(x) for x in members if isinstance(x, str))
                    if player_ref in participants:
                        row['status'] = 'resolved'
            return state
        if key == player_roster_path:
            state = copy.deepcopy(base_read_json(path))
            for row in state.get('people', []):
                if isinstance(row, dict) and str(row.get('person_id') or '') == player_ref:
                    row['location_ref'] = 'site.house_tang'
            return state
        return base_read_json(path)
    return read_json


def test_player_registration_is_paid_by_faction_not_personal_cash(monkeypatch):
    repo = RepositoryStore(ROOT)
    planner = RepositoryCommandPlanner(repo)
    meta = repo.read_json('state/meta.json')
    tref = 'tournament.test-player-registration'
    tournament = open_tournament(
        event_id=tref,
        format_ref='individual',
        organizer_ref='government.imperial',
        great=True,
    )
    tournament.update({'host_place_ref': 'luoyang', 'venue_site_ref': 'site.house_tang'})
    tournament_state = {'schema': 'jianghu-tournament-state-1.0', 'tournaments': {tref: tournament}}

    base_read_json = repo.read_json
    monkeypatch.setattr(
        repo,
        'read_json',
        _fixture_read_json_at_house_tang(base_read_json, tournament_state, player_ref=meta['player_id']),
    )

    player_before = roster_person(repo, meta['player_id'])[3]
    house_before = base_read_json(faction_path('house_tang'))
    plan = planner._jianghu_tournament_resolution(
        _command(meta, request_id='test-player-tournament-register', action='register', tournament_ref=tref),
        meta,
        CampaignTime.parse(meta['time']),
    )

    house_after = _read_write(plan, faction_path('house_tang'))
    tournament_after = _read_write(plan, jianghu_commands._TOURNAMENTS)['tournaments'][tref]
    fee = int(tournament['entry_fee_cash'])

    assert int(house_after['treasury_cash']) == int(house_before['treasury_cash']) - fee
    assert roster_path('house_tang') not in plan.writes
    assert roster_person(repo, meta['player_id'])[3]['personal_cash'] == player_before['personal_cash']
    assert tournament_after['prize_escrow_cash'] == fee
    assert tournament_after['registrations'][-1]['faction_ref'] == 'house_tang'
    assert plan.result['sponsor_faction_ref'] == 'house_tang'


def test_player_completion_uses_top_four_split_instead_of_champion_take_all(monkeypatch):
    repo = RepositoryStore(ROOT)
    planner = RepositoryCommandPlanner(repo)
    meta = repo.read_json('state/meta.json')
    tref = 'tournament.test-player-payout'
    entrants = ['pc_wei_tang', 'char.zhu', 'char.ling', 'mw.person.house_tang.1000']
    placements = dict(zip(('first', 'second', 'third', 'fourth'), entrants))

    tournament = open_tournament(
        event_id=tref,
        format_ref='individual',
        organizer_ref='government.imperial',
        great=True,
    )
    tournament.update({
        'host_place_ref': 'luoyang',
        'venue_site_ref': 'site.house_tang',
        'status': 'competition_active',
        'prize_escrow_cash': 100_000,
        'registrations': [
            {'entrant_ref': ref, 'faction_ref': 'house_tang', 'public_qualifying_score': 100 - i, 'fee_cash': 10_000, 'prize_contribution_cash': 10_000}
            for i, ref in enumerate(entrants)
        ],
    })
    tournament_state = {'schema': 'jianghu-tournament-state-1.0', 'tournaments': {tref: tournament}}

    base_read_json = repo.read_json
    monkeypatch.setattr(
        repo,
        'read_json',
        _fixture_read_json_at_house_tang(base_read_json, tournament_state, player_ref=meta['player_id']),
    )

    people_before = {ref: roster_person(repo, ref)[3] for ref in entrants}
    house_before = base_read_json(faction_path('house_tang'))

    def fake_advance(tourn, *, people, equipment_ledger, doctrines, combats_state, zone_ref, at_iso, player_ref):
        live = copy.deepcopy(dict(tourn))
        live['placements'] = dict(placements)
        live['status'] = 'completed'
        return {
            'tournament_after': live,
            'combats_state_after': copy.deepcopy(combats_state),
            'equipment_ledger_after': copy.deepcopy(equipment_ledger),
            'people_after': {ref: copy.deepcopy(dict(people[ref])) for ref in entrants},
            'winner_points': {},
            'waiting_for_player': False,
            'combat_ref': None,
            'champion_ref': entrants[0],
        }

    monkeypatch.setattr(jianghu_commands, 'advance_individual_competition', fake_advance)
    plan = planner._jianghu_tournament_resolution(
        _command(meta, request_id='test-player-tournament-payout', action='advance', tournament_ref=tref),
        meta,
        CampaignTime.parse(meta['time']),
    )

    house_after = _read_write(plan, faction_path('house_tang'))
    roster_after = hydrate_roster_state(_read_write(plan, roster_path('house_tang')), faction=house_after)
    people_after = {row['person_id']: row for row in roster_after['people'] if row.get('person_id') in entrants}
    awards = {row['place']: row for row in plan.result['placement_awards']}

    assert set(awards) == {'first', 'second', 'third', 'fourth'}
    assert sum(row['gross_prize_cash'] for row in awards.values()) == 100_000
    assert sum(row['faction_prize_cash'] for row in awards.values()) == 70_000
    assert sum(row['personal_prize_cash'] for row in awards.values()) == 30_000
    assert int(house_after['treasury_cash']) == int(house_before['treasury_cash']) + 70_000
    for place, ref in placements.items():
        assert people_after[ref]['personal_cash'] == people_before[ref]['personal_cash'] + awards[place]['personal_prize_cash']
    assert jianghu_commands._TOURNAMENTS not in plan.writes
    assert plan.result['champion_ref'] == entrants[0]
