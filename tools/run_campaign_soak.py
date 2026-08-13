#!/usr/bin/env python3
"""Run one 1,000-transaction mixed real-campaign production soak.

Run this command in a fresh process for each deterministic replay. Use
``--compare-to`` on the second invocation to verify the final state root and
command mix against the first result. This keeps the verifier itself from
retaining filesystem/cache pressure across 2,000 transactions.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from shinobi_runtime.acceptance.soak import run_mixed_soak  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", default=".")
    parser.add_argument("--work-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--name", default="run")
    parser.add_argument("--compare-to", default=None)
    args = parser.parse_args()

    result = run_mixed_soak(
        Path(args.source_root).resolve(),
        Path(args.work_root).resolve(),
        name=args.name,
    ).to_record()
    if args.compare_to is not None:
        prior = json.loads(Path(args.compare_to).resolve().read_text())
        if result["final_state_root"] != prior.get("final_state_root"):
            raise AssertionError("mixed soak deterministic replay roots differ")
        if result["command_counts"] != prior.get("command_counts"):
            raise AssertionError("mixed soak command mixes differ")
        result["deterministic_replay_against"] = str(Path(args.compare_to).resolve())
        result["root_equal"] = True

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
