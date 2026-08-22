#!/usr/bin/env python3
"""Fast release gate for the single-authority Jianghu campaign."""
from __future__ import annotations

import json
import py_compile
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))

TEXT_SUFFIXES={'.py','.json','.md','.yaml','.yml','.toml','.txt'}


def check_json(errors):
    for root in ('game','runtime/contracts','state'):
        for p in (ROOT/root).rglob('*.json'):
            try: json.loads(p.read_text(encoding='utf-8'))
            except Exception as exc: errors.append(f'invalid JSON {p.relative_to(ROOT)}: {exc}')


def check_python(errors):
    for root in ('runtime/shinobi_runtime','tools','tests'):
        base=ROOT/root
        if not base.exists(): continue
        for p in base.rglob('*.py'):
            try: py_compile.compile(str(p),doraise=True)
            except Exception as exc: errors.append(f'python compile failed {p.relative_to(ROOT)}: {exc}')


def check_commands(errors):
    from shinobi_runtime.commands.planner import RepositoryCommandPlanner
    from shinobi_runtime.commands.specs import COMMAND_SPECS
    from shinobi_runtime.api.command_discovery import compact_commands
    from shinobi_runtime.store import RepositoryStore
    from shinobi_runtime.commands.envelope import CommandEnvelope
    repo=RepositoryStore(ROOT); planner=RepositoryCommandPlanner(repo)
    if set(planner.COMMAND_TYPES)!=set(COMMAND_SPECS): errors.append('planner/spec command surface mismatch')
    for name in COMMAND_SPECS:
        if not callable(getattr(planner,'_'+name,None)): errors.append(f'missing reducer for {name}')
    compact=compact_commands({'supported_command_types':sorted(planner.COMMAND_TYPES)})
    if set(compact['supported_command_types'])!=set(COMMAND_SPECS): errors.append('command discovery surface mismatch')
    meta=repo.read_json('state/meta.json')
    base=dict(campaign_id=meta['campaign_id'],actor_id=meta['player_id'],expected_revision=meta['revision'],submitted_at='2026-08-20T04:00:00Z',mode='gameplay')
    current_text=str(meta.get('time') or '')
    normalized=current_text.removeprefix('SE-')
    try:
        future=(datetime.fromisoformat(normalized)+timedelta(hours=1)).isoformat()
    except ValueError as exc:
        errors.append(f'campaign time invalid for command smoke preview: {exc}')
        return
    examples=[
      ('jianghu_training_focus_resolution',{'subject_ref':meta['player_id'],'focus':'sword'}),
      ('advance_time',{'target_time':'SE-'+future}),
    ]
    for i,(name,payload) in enumerate(examples):
        try:
            preview=planner.preview(CommandEnvelope(request_id=f'quick-{i}',command_type=name,payload=payload,**base))
            if preview.status!='ready': errors.append(f'{name} smoke preview not ready')
        except Exception as exc: errors.append(f'{name} smoke preview failed: {exc}')


def check_consumers(errors):
    p=ROOT/'game/data/martial-world/determinism-contract.json'; d=json.loads(p.read_text())
    for sid,sub in d.get('subsystems',{}).items():
        status=str(sub.get('status',''))
        if 'pending' in status or 'compatibility' in status: errors.append(f'determinism subsystem still pending: {sid}={status}')
        for key,val in sub.items():
            if key not in {'consumer','production_consumer','weather_consumer'}: continue
            vals=val if isinstance(val,list) else [val]
            for rel in vals:
                if isinstance(rel,str) and not (ROOT/rel).is_file(): errors.append(f'{sid}: missing live consumer {rel}')



def main():
    errors=[]
    check_json(errors); check_python(errors)
    r=subprocess.run([sys.executable,str(ROOT/'tools/verify_structure.py')],cwd=ROOT,env={**__import__('os').environ,'PYTHONPATH':str(ROOT/'runtime')},capture_output=True,text=True)
    if r.returncode: errors.append('structure validator failed:\n'+r.stdout+r.stderr)
    check_commands(errors); check_consumers(errors)
    if errors:
        print('QUICK CHECK FAILED')
        for e in errors: print(' -',e)
        return 1
    print(r.stdout.strip())
    print(f'COMMAND SURFACE OK: {len(__import__("shinobi_runtime.commands.specs", fromlist=["COMMAND_SPECS"]).COMMAND_SPECS)} semantic commands')
    print('LIVE RULE CONSUMERS OK')
    print('QUICK CHECK OK')
    return 0
if __name__=='__main__': raise SystemExit(main())
