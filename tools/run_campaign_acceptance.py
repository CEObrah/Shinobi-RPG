#!/usr/bin/env python3
"""Run real-campaign production acceptance scenarios on disposable archive copies."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
from shinobi_runtime.acceptance import run_campaign_scenarios  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-root", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    if args.work_root is None:
        with tempfile.TemporaryDirectory(prefix="shinobi-campaign-acceptance-") as td:
            results = run_campaign_scenarios(ROOT, Path(td))
    else:
        args.work_root.mkdir(parents=True, exist_ok=True)
        results = run_campaign_scenarios(ROOT, args.work_root)
    record = {"status": "passed", "scenario_count": len(results), "scenarios": [r.to_record() for r in results]}
    text = json.dumps(record, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
