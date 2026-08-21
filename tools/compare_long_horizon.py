#!/usr/bin/env python3
from __future__ import annotations
import argparse,json
from pathlib import Path

def load(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('left'); ap.add_argument('right'); ap.add_argument('--json',dest='out')
    args=ap.parse_args(); a=load(args.left); b=load(args.right)
    ignored={'elapsed_seconds'}
    ca={k:v for k,v in a.items() if k not in ignored}; cb={k:v for k,v in b.items() if k not in ignored}
    result={'status':'PASS' if ca==cb and a.get('substantive_state_sha256')==b.get('substantive_state_sha256') else 'FAIL','hash_a':a.get('substantive_state_sha256'),'hash_b':b.get('substantive_state_sha256'),'hashes_match':a.get('substantive_state_sha256')==b.get('substantive_state_sha256'),'substantive_results_match':ca==cb,'frontiers_a':a.get('frontiers'),'frontiers_b':b.get('frontiers'),'people_after_a':a.get('people_after'),'people_after_b':b.get('people_after'),'projected_state_bytes_after_a':a.get('projected_state_bytes_after'),'projected_state_bytes_after_b':b.get('projected_state_bytes_after'),'elapsed_seconds_a':a.get('elapsed_seconds'),'elapsed_seconds_b':b.get('elapsed_seconds')}
    text=json.dumps(result,indent=2)+'\n'
    if args.out: Path(args.out).write_text(text,encoding='utf-8')
    print(text,end=''); return 0 if result['status']=='PASS' else 1
if __name__=='__main__': raise SystemExit(main())
