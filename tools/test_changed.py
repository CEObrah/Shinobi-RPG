#!/usr/bin/env python3
"""Run the maintained current-Jianghu regression suite."""
from __future__ import annotations
import os, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main()->int:
    env=os.environ.copy(); env['PYTHONDONTWRITEBYTECODE']='1'; env['PYTHONPATH']=str(ROOT/'runtime')
    cmd=[sys.executable,'-m','pytest','-q','-p','no:cacheprovider','tests/current']
    print('CHANGED TESTS: tests/current')
    return subprocess.call(cmd,cwd=ROOT,env=env)
if __name__=='__main__': raise SystemExit(main())
