import json, subprocess, sys
from pathlib import Path

from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.commands.specs import COMMAND_SPECS
from shinobi_runtime.api.command_discovery import compact_commands
from shinobi_runtime.store import RepositoryStore
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.people.repository import RepositoryPersonSheetResolver
from shinobi_runtime.tx.manifest import TransactionPlanner

ROOT=Path(__file__).resolve().parents[2]


def test_command_surface_matches_current_specs_with_reducers():
    repo=RepositoryStore(ROOT); planner=RepositoryCommandPlanner(repo)
    assert len(COMMAND_SPECS)==23
    assert set(planner.COMMAND_TYPES)==set(COMMAND_SPECS)
    assert all(callable(getattr(planner,'_'+name,None)) for name in COMMAND_SPECS)
    compact=compact_commands({'supported_command_types':sorted(planner.COMMAND_TYPES)})
    assert set(compact['supported_command_types'])==set(COMMAND_SPECS)


def test_player_sheet_resolves_from_jianghu_roster_and_exposes_derived_condition():
    repo=RepositoryStore(ROOT); person=RepositoryPersonSheetResolver(repo)('pc_wei_tang')
    assert person['name']=='Tang Wei'
    assert person['faction_ref']=='house_tang'
    assert 'derived_condition' in person
    assert person['derived_condition']['vision']['state']=='binocular'
    assert person['combat_doctrine_ref']=='doctrine.tang_wei.precision_function_denial'
    assert not (ROOT/'state/player.json').exists()


def test_current_save_revision_and_no_dev_receipt_history():
    meta=json.loads((ROOT/'state/meta.json').read_text())
    assert meta['game']=='jianghu' and meta['revision']==1
    names={p.name for p in (ROOT/'state').rglob('*') if p.is_dir()}
    assert 'receipts' not in names and 'wal' not in names


def test_structure_validator_passes():
    r=subprocess.run([sys.executable,str(ROOT/'tools/verify_structure.py')],cwd=ROOT,capture_output=True,text=True)
    assert r.returncode==0,r.stdout+r.stderr
    assert '11691 people' in r.stdout


def test_live_planner_previews_current_training_and_time_commands():
    from shinobi_runtime.commands.envelope import CommandEnvelope
    repo=RepositoryStore(ROOT); planner=RepositoryCommandPlanner(repo); meta=repo.read_json('state/meta.json')
    base=dict(campaign_id=meta['campaign_id'],actor_id=meta['player_id'],expected_revision=meta['revision'],submitted_at='2026-08-20T04:00:00Z',mode='gameplay')
    training=CommandEnvelope(request_id='test-training',command_type='jianghu_training_focus_resolution',payload={'subject_ref':meta['player_id'],'focus':'sword'},**base)
    assert planner.preview(training).status=='ready'
    advancing=CommandEnvelope(request_id='test-time',command_type='advance_time',payload={'target_time':'SE-0061-08-14T22:15:00'},**base)
    assert planner.preview(advancing).status=='ready'


def test_current_month_timeskip_builds_and_validates_the_exact_transaction_overlay():
    from shinobi_runtime.commands.envelope import CommandEnvelope
    repo=RepositoryStore(ROOT); planner=RepositoryCommandPlanner(repo); meta=repo.read_json('state/meta.json')
    command=CommandEnvelope(
        campaign_id=meta['campaign_id'],request_id='test-month-timeskip-overlay',
        actor_id=meta['player_id'],command_type='advance_time',
        expected_revision=meta['revision'],submitted_at='2026-08-20T04:00:00Z',
        payload={'target_time':'SE-0061-09-14T21:15:00'},mode='gameplay',
    )
    preview=planner.preview(command)
    assert preview.status=='ready'
    plan=planner.plan(command)
    manifest=TransactionPlanner(repo).plan(
        command,plan.transaction_id,plan.created_at,plan.writes,
    )
    overlay=StagedOverlay(repo,manifest)
    plan.validator(overlay,manifest)
    assert plan.result['world_time']=='SE-0061-08-31T23:59:59'
    assert plan.result['continuation_required'] is True


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
    assert 'python -m shinobi_runtime.bootstrap' in text
    assert (ROOT/'runtime/shinobi_runtime/bootstrap.py').is_file()
