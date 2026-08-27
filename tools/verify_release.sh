#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
export PYTHONPATH="$ROOT/runtime${PYTHONPATH:+:$PYTHONPATH}"
mkdir -p artifacts

python -m compileall -q runtime/shinobi_runtime tools tests
python tools/quick_check.py
python tools/test_changed.py

# Run maintained tests in isolated file shards. The execution environment can
# hang after a monolithic pytest session has already completed every test body;
# separate processes make teardown bounded and preserve a reliable exit status.
for test_file in tests/current/test_*.py; do
  pytest -q "$test_file"
done

python tools/verify_jianghu_semantics.py --json artifacts/final-semantic-audit.json
python tools/audit_state_bloat.py --json artifacts/final-state-bloat-audit.json
python tools/verify_noop_roundtrip.py --json artifacts/final-noop-roundtrip.json
python tools/project_jianghu_development.py --json artifacts/final-development-projections.json >/dev/null

# Maintained release horizon. These disposable simulations do not mutate the
# canonical campaign state. Ninety days is the release-blocking integration
# horizon; longer A/B soaks are deliberate extended QA, not a merge/release gate.
python tools/run_long_horizon.py --days 1 --json artifacts/final-1d.json
python tools/run_long_horizon.py --days 7 --json artifacts/final-7d.json
python tools/run_long_horizon.py --days 30 --json artifacts/final-30d.json
python tools/run_long_horizon.py --days 90 --json artifacts/final-90d.json

run_365() {
  name=$1
  checkpoint="artifacts/${name}.checkpoint.json"
  result="artifacts/${name}.json"
  rm -f "$checkpoint" "$result"
  while :; do
    set +e
    python tools/run_long_horizon.py --days 365 --checkpoint "$checkpoint" --json "$result" --frontier-budget 120 --checkpoint-every 20
    code=$?
    set -e
    if [ "$code" -eq 0 ]; then break; fi
    if [ "$code" -ne 2 ]; then exit "$code"; fi
  done
  rm -f "$checkpoint"
}

if [ "${SHINOBI_EXTENDED_SOAK:-0}" = "1" ]; then
  run_365 final-365a
  run_365 final-365b
  python tools/compare_long_horizon.py artifacts/final-365a.json artifacts/final-365b.json --json artifacts/final-365-determinism-comparison.json
  printf '%s\n' 'EXTENDED 365-DAY A/B SOAK PASS'
else
  printf '%s\n' 'EXTENDED 365-DAY A/B SOAK SKIPPED (set SHINOBI_EXTENDED_SOAK=1 to run)'
fi

printf '%s\n' 'RELEASE VERIFICATION PASS'
