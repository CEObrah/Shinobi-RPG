import json, re, subprocess, sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.commands.specs import COMMAND_SPECS
from shinobi_runtime.api.command_discovery import compact_command_family, compact_commands, compact_play_context
from shinobi_runtime.martial_world.physical_presence import active_combat_for_person
from shinobi_runtime.store import RepositoryStore
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.people.repository import RepositoryPersonSheetResolver
from shinobi_runtime.tx.manifest import TransactionPlanner

ROOT=Path(__file__).resolve().parents[2]


class _ScenePresenceReader:
    def read_json(self, path):
        raise FileNotFoundError(path)


def _future_campaign_time(meta, *, hours=1, days=0):
    current=datetime.fromisoformat(str(meta['time']).removeprefix('SE-'))
    return 'SE-'+(current+timedelta(days=days,hours=hours)).isoformat()


def _player_has_active_combat(repo, player_id):
    return active_combat_for_person(repo.read_json, player_id) is not None


def test_command_surface_matches_current_specs_with_reducers():
    repo=RepositoryStore(ROOT); planner=RepositoryCommandPlanner(repo)
    assert 'jianghu_faction_lifecycle_resolution' in COMMAND_SPECS
    assert set(planner.COMMAND_TYPES)==set(COMMAND_SPECS)
    assert all(callable(getattr(planner,'_'+name,None)) for name in COMMAND_SPECS)
    surface={'supported_command_types':sorted(planner.COMMAND_TYPES)}
    compact=compact_commands(surface)
    discovered=set()
    for family in compact['mechanic_families']:
        discovered.update(compact_command_family(surface,family)['command_types'])
    assert discovered==set(COMMAND_SPECS)
    assert compact['operation_count']==len(COMMAND_SPECS)
    assert 'supported_command_types' not in compact


def test_partial_scene_departure_keeps_remaining_conversation_physically_live():
    from shinobi_runtime.api.operations import _hot_active_scene_thread_rows, _project_active_session_presence

    people = {
        'pc_wei_tang': {'person_id': 'pc_wei_tang', 'location_ref': 'site.house_tang.hall'},
        'npc.stays': {'person_id': 'npc.stays', 'location_ref': 'site.house_tang.hall'},
        'npc.left': {'person_id': 'npc.left', 'location_ref': 'site.road'},
    }
    session = {
        'session_ref': 'scene.test', 'status': 'active', 'kind': 'house_council',
        'location_ref': 'site.house_tang.hall',
        'participant_refs': ['pc_wei_tang', 'npc.stays', 'npc.left'],
        'open_thread_refs': ['interaction_attempt_1'],
    }
    reader = _ScenePresenceReader()
    projected = _project_active_session_presence(
        reader.read_json, lambda ref: people[ref], session,
        player_id='pc_wei_tang', player_sheet=people['pc_wei_tang'],
        player_location='site.house_tang.hall', live_combat=None,
    )
    assert projected is not None
    assert projected['participant_refs'] == ['pc_wei_tang', 'npc.stays']
    assert projected['physically_absent_participant_refs'] == ['npc.left']
    assert projected['open_thread_refs'] == ['interaction_attempt_1']

    attempts = {
        'attempts': [
            {
                'attempt_ref': 'thread.stays', 'thread_status': 'open',
                'scene_session_ref': 'scene.test', 'target_ref': 'npc.stays',
            },
            {
                'attempt_ref': 'thread.left', 'thread_status': 'open',
                'scene_session_ref': 'scene.test', 'target_ref': 'npc.left',
            },
        ]
    }
    hot = _hot_active_scene_thread_rows(attempts, projected)
    assert [row['attempt_ref'] for row in hot] == ['thread.stays']


def test_play_context_command_surface_is_demand_loaded_not_full_schema_dump():
    full={
        'supported_command_types':['advance_time','jianghu_training_focus_resolution'],
        'command_types':{
            'advance_time':{'description':'x'*5000},
            'jianghu_training_focus_resolution':{'description':'y'*5000},
        },
        'availability_overrides':{'advance_time':True},
        'limits':{'preview_before_execute':True},
    }
    compact=compact_play_context({'commands':full,'campaign':{'revision':1}})
    assert 'command_types' not in compact['commands']
    assert compact['commands']['operation_count']==2
    assert compact['commands']['mechanic_families']['time']['operation_count']==1
    assert compact['commands']['mechanic_families']['training']['operation_count']==1
    assert 'supported_command_types' not in compact['commands']
    assert compact['commands']['family_lookup'].startswith('Only when a hard consequence')
    assert compact['commands']['limits']=={'preview_before_execute':True}
    semantic=compact['semantic_action_contract']
    assert semantic['intent_before_mechanics'] is True
    assert semantic['ordinary_reversible_scene_action_needs_command'] is False
    assert semantic['player_authored_external_outcomes_forbidden'] is True
    assert semantic['gm_private_director_truth_may_exceed_player_knowledge'] is True
    assert semantic['player_output_remains_knowledge_bounded'] is True
    assert len(json.dumps(compact['commands'])) < len(json.dumps(full)) // 4


def test_rest_transport_redacts_gm_private_packets_without_stripping_mcp_source():
    from shinobi_runtime.api.app import _player_safe_transport
    synthetic = {
        'campaign': {'revision': 1},
        'scene': {
            'public': 'visible',
            'gm_private_director_context': {'secret': 'ambush geometry'},
        },
        'person_reads': {},
        'object_reads': {},
        'contract_reads': {},
        'world_events': {},
        'commands': {},
        'narration': {},
        'context_policy': {},
        'person': {
            'npc_response_envelope': {
                'may': ['react'],
                'gm_private_cognition': {'hidden_goal': 'take the cargo'},
            },
        },
        'combat_parley': {
            'npc_response_envelope': {
                'privacy': 'gm_private_scene_bounded_omniscient_truth_not_player_knowledge',
                'truthful_dialogue_material': 'They attacked for the cargo.',
                'gm_private_causal_context': {'motive_kind': 'loot'},
            },
        },
    }
    safe = _player_safe_transport(synthetic)
    assert safe['scene']['public'] == 'visible'
    assert 'gm_private_director_context' not in safe['scene']
    assert safe['person']['npc_response_envelope']['may'] == ['react']
    assert 'gm_private_cognition' not in safe['person']['npc_response_envelope']
    assert 'npc_response_envelope' not in safe['combat_parley']

    # MCP compaction intentionally operates on the unredacted GM source.
    compact = compact_play_context(synthetic)
    assert compact['scene']['gm_private_director_context']['secret'] == 'ambush geometry'


def test_rest_play_context_response_model_accepts_current_runtime_shape():
    from shinobi_runtime.api.models import PlayContextResponse

    payload = {
        'campaign': {'campaign_id': 'campaign.test', 'revision': 1, 'world_time': 'SE-0061-01-01T00:00:00', 'state_root': 'root', 'player_id': 'pc.test', 'game': 'jianghu'},
        'scene': {}, 'player': {}, 'person_reads': {}, 'object_reads': {}, 'contract_reads': {},
        'mission_reads': {}, 'world_events': {}, 'commands': {},
        'active_scene_session': {'session_ref': 'scene.test'},
        'recent_scene_history': [{'speech_ref': 'speech.test'}],
        'active_threads': [{'attempt_ref': 'thread.test'}],
        'active_thread_count': 1, 'active_threads_truncated': False,
        'active_questions': [], 'read_hints': {'scene_open_threads': {'object_ref': 'scene_open_threads'}},
        'narration': {}, 'context_policy': {}, 'causal_freshness': {},
    }
    validated = PlayContextResponse.model_validate(payload)
    assert validated.active_thread_count == 1
    assert validated.mission_reads == {}
    assert validated.read_hints['scene_open_threads']['object_ref'] == 'scene_open_threads'


def test_social_commitment_hot_window_reports_true_total_and_truncation():
    from shinobi_runtime.api.operations import _bounded_social_commitment_rows
    rows = [
        {"obligation_ref": f"obligation:{index:03d}", "actor_ref": "char_tang_wei"}
        for index in range(70)
    ]
    bounded, total, truncated = _bounded_social_commitment_rows(rows, "obligation_ref")
    assert len(bounded) == 64
    assert total == 70
    assert truncated is True
    assert bounded[0]["obligation_ref"] == "obligation:000"


def test_operations_demand_loads_one_mechanic_family_without_exposing_unrelated_operations():
    repo=RepositoryStore(ROOT)
    planner=RepositoryCommandPlanner(repo)
    from shinobi_runtime.api.operations import CampaignOperations
    operations=CampaignOperations.__new__(CampaignOperations)
    operations.command_planner=planner
    family=operations.command_family('combat')
    assert family['family']=='combat'
    assert family['command_types']==['jianghu_combat_resolution']
    assert 'advance_time' not in family['command_types']
    assert 'jianghu_training_focus_resolution' not in family['command_types']


def test_player_sheet_resolves_from_jianghu_roster_and_exposes_derived_condition():
    repo=RepositoryStore(ROOT); person=RepositoryPersonSheetResolver(repo)('pc_wei_tang')
    assert person['name']=='Tang Wei'
    assert person['faction_ref']=='house_tang'
    assert 'derived_condition' in person
    assert person['derived_condition']['vision']['state']=='binocular'
    assert person['combat_doctrine_ref']=='doctrine.tang_wei.precision_function_denial'
    assert not (ROOT/'state/player.json').exists()


def test_current_save_is_live_jianghu_and_no_dev_receipt_history():
    meta=json.loads((ROOT/'state/meta.json').read_text())
    assert meta['game']=='jianghu'
    assert isinstance(meta['revision'],int) and meta['revision']>=1
    names={p.name for p in (ROOT/'state').rglob('*') if p.is_dir()}
    assert 'receipts' not in names and 'wal' not in names


def test_structure_validator_passes():
    r=subprocess.run([sys.executable,str(ROOT/'tools/verify_structure.py')],cwd=ROOT,capture_output=True,text=True)
    assert r.returncode==0,r.stdout+r.stderr
    match=re.search(r'(\d+) persistent martial identities',r.stdout)
    assert match,r.stdout
    assert int(match.group(1))>=11691


def test_live_planner_previews_current_training_and_time_commands():
    from shinobi_runtime.commands.envelope import CommandEnvelope
    repo=RepositoryStore(ROOT); planner=RepositoryCommandPlanner(repo); meta=repo.read_json('state/meta.json')
    base=dict(campaign_id=meta['campaign_id'],actor_id=meta['player_id'],expected_revision=meta['revision'],submitted_at='2026-08-20T04:00:00Z',mode='gameplay')
    training=CommandEnvelope(request_id='test-training',command_type='jianghu_training_focus_resolution',payload={'subject_ref':meta['player_id'],'focus':'sword'},**base)
    advancing=CommandEnvelope(request_id='test-time',command_type='advance_time',payload={'target_time':_future_campaign_time(meta)},**base)
    if _player_has_active_combat(repo, meta['player_id']):
        for command in (training, advancing):
            with pytest.raises(CommandRejectedError) as caught:
                planner.preview(command)
            assert caught.value.code == 'jianghu_active_combat_requires_resolution'
        return
    assert planner.preview(training).status=='ready'
    assert planner.preview(advancing).status=='ready'


def test_current_month_timeskip_builds_and_validates_the_exact_transaction_overlay(monkeypatch):
    from shinobi_runtime.commands.envelope import CommandEnvelope
    import shinobi_runtime.commands.jianghu_time as jianghu_time
    # The public month-long intent survives across bounded continuation
    # transactions. One real frontier is enough to prove that the exact
    # transaction after-image validates; autonomous battle load belongs in
    # soak/performance coverage rather than this release-surface invariant.
    monkeypatch.setattr(jianghu_time, '_PUBLIC_TIME_FRONTIER_CHUNK', 1)
    repo=RepositoryStore(ROOT); planner=RepositoryCommandPlanner(repo); meta=repo.read_json('state/meta.json')
    target=_future_campaign_time(meta,days=31,hours=0)
    command=CommandEnvelope(
        campaign_id=meta['campaign_id'],request_id='test-month-timeskip-overlay',
        actor_id=meta['player_id'],command_type='advance_time',
        expected_revision=meta['revision'],submitted_at='2026-08-20T04:00:00Z',
        payload={'target_time':target},mode='gameplay',
    )
    if _player_has_active_combat(repo, meta['player_id']):
        with pytest.raises(CommandRejectedError) as caught:
            planner.preview(command)
        assert caught.value.code == 'jianghu_active_combat_requires_resolution'
        return
    preview=planner.preview(command)
    assert preview.status=='ready'
    plan=planner.plan(command)
    manifest=TransactionPlanner(repo).plan(
        command,plan.transaction_id,plan.created_at,plan.writes,
    )
    overlay=StagedOverlay(repo,manifest)
    plan.validator(overlay,manifest)
    current=datetime.fromisoformat(str(meta['time']).removeprefix('SE-'))
    reached=datetime.fromisoformat(str(plan.result['world_time']).removeprefix('SE-'))
    requested=datetime.fromisoformat(target.removeprefix('SE-'))
    # Equality is lawful when the compact scheduler still has a resumable owner
    # chunk or another schedule class due at the current campaign timestamp.
    assert current <= reached <= requested
    assert isinstance(plan.result['continuation_required'],bool)
    if reached == current:
        assert plan.result['continuation_required'] or plan.result['interrupted']


def test_combat_state_does_not_persist_exchange_trace_history():
    t=json.loads((ROOT/'runtime/contracts/templates/jianghu-combat-state-1.0.template.json').read_text())
    keys=set(t['object_contracts']['/combats/*']['allowed_keys'])
    assert 'history' not in keys and 'decision_provenance' not in keys and 'awareness_evidence' not in keys

def test_public_api_modules_import_on_clean_release():
    import importlib.util
    import shinobi_runtime.api.app  # noqa: F401
    import shinobi_runtime.api.operations  # noqa: F401
    if importlib.util.find_spec('mcp') is not None:
        import shinobi_runtime.api.mcp  # noqa: F401


def test_railway_start_target_exists_and_bootstrap_imports():
    import shinobi_runtime.bootstrap  # noqa: F401
    text=(ROOT/'railway.toml').read_text()
    assert 'SHINOBI_GIT_BRANCH=main' in text
    assert 'python -m shinobi_runtime.bootstrap' in text
    assert 'branch_bootstrap' not in text
    assert (ROOT/'runtime/shinobi_runtime/bootstrap.py').is_file()



def test_object_inspection_projection_blocks_unrelated_mutable_world_truth():
    from shinobi_runtime.api.operations import CampaignOperations, OperationError
    from shinobi_runtime.store import RepositoryStore

    operations = CampaignOperations.__new__(CampaignOperations)
    operations.repository = RepositoryStore(ROOT)
    meta = json.loads((ROOT / 'state/meta.json').read_text())
    player = RepositoryPersonSheetResolver(operations.repository)(str(meta['player_id']))

    hidden_faction = json.loads((ROOT / 'state/martial-world/factions/faction.red_road_band.json').read_text())
    projected, view = operations._player_safe_inspection_object(
        object_ref='faction:faction.red_road_band', obj=hidden_faction,
        view='faction_summary', meta=meta, player=player,
    )
    assert view == 'public_faction_identity'
    assert projected['faction_id'] == 'faction.red_road_band'
    assert 'treasury_cash' not in projected
    assert 'training_epoch' not in projected
    assert 'infrastructure' not in projected

    hidden_inventory = json.loads((ROOT / 'state/martial-world/inventories/faction.red_road_band.json').read_text())
    with pytest.raises(OperationError) as exc:
        operations._player_safe_inspection_object(
            object_ref='inventory:faction.red_road_band', obj=hidden_inventory,
            view='inventory_summary', meta=meta, player=player,
        )
    assert exc.value.code == 'object_not_found'

    relations = json.loads((ROOT / 'state/martial-world/faction-relations.json').read_text())
    safe_relations, relation_view = operations._player_safe_inspection_object(
        object_ref='relations', obj=relations, view='relations_summary', meta=meta, player=player,
    )
    assert relation_view == 'player_faction_relations'
    assert all(
        player['faction_ref'] in {row.get('from_faction'), row.get('to_faction')}
        for row in safe_relations['edges']
    )
    assert len(safe_relations['edges']) < len(relations['edges'])

    government = json.loads((ROOT / 'state/martial-world/government.json').read_text())
    safe_government, government_view = operations._player_safe_inspection_object(
        object_ref='government', obj=government, view='government_summary', meta=meta, player=player,
    )
    assert government_view == 'player_relevant_government'
    assert set(safe_government['attention']) <= {meta['player_id']}
    assert 'regional_capacity' not in safe_government


def test_object_inspection_projection_allows_own_faction_inventory_and_current_market_only(monkeypatch):
    from shinobi_runtime.api.operations import CampaignOperations, OperationError
    import shinobi_runtime.api.operations as operations_module
    from shinobi_runtime.store import RepositoryStore

    operations = CampaignOperations.__new__(CampaignOperations)
    operations.repository = RepositoryStore(ROOT)
    meta = json.loads((ROOT / 'state/meta.json').read_text())
    player = RepositoryPersonSheetResolver(operations.repository)(str(meta['player_id']))

    own_inventory = json.loads((ROOT / 'state/martial-world/inventories/house_tang.json').read_text())
    projected, view = operations._player_safe_inspection_object(
        object_ref='inventory:house_tang', obj=own_inventory,
        view='inventory_summary', meta=meta, player=player,
    )
    assert view == 'player_faction_inventory'
    assert projected['schema'] == own_inventory['schema']

    # Isolate the market-visibility contract from the campaign's current combat/
    # route position. Chang'an is a central_plain market location in geography.
    monkeypatch.setattr(
        operations_module,
        'effective_person_presence',
        lambda *_args, **_kwargs: {'location_ref': 'changan'},
    )
    current_market = json.loads((ROOT / 'state/martial-world/markets/central_plain.json').read_text())
    projected_market, market_view = operations._player_safe_inspection_object(
        object_ref='market:central_plain', obj=current_market,
        view='market_summary', meta=meta, player=player,
    )
    assert market_view == 'current_region_market_stock'
    assert projected_market['region_id'] == 'central_plain'
    assert projected_market['stock'] == current_market['stock']
    assert 'cash_pool' not in projected_market

    # Exact stock in an unrelated region must not be remotely inspectable.
    remote_market = json.loads((ROOT / 'state/martial-world/markets/northwest_dry.json').read_text())
    with pytest.raises(OperationError) as exc:
        operations._player_safe_inspection_object(
            object_ref='market:northwest_dry', obj=remote_market,
            view='market_summary', meta=meta, player=player,
        )
    assert exc.value.code == 'object_not_found'


def test_railway_start_uses_single_main_bootstrap():
    railway = (ROOT / "railway.toml").read_text(encoding="utf-8")
    assert "SHINOBI_GIT_BRANCH=main" in railway
    assert "python -m shinobi_runtime.bootstrap" in railway
    assert "branch_bootstrap" not in railway
