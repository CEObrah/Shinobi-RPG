#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
export PYTHONPATH="$ROOT/runtime${PYTHONPATH:+:$PYTHONPATH}"
mkdir -p artifacts

python -m compileall -q runtime/shinobi_runtime tools tests
python tools/quick_check.py
python tools/verify_release_state.py
python tools/sync_gm_skill_contract.py --check
python tools/playability_lab.py --no-mutations
python tools/mutation_audit.py
python tools/combat_balance_lab.py --json artifacts/final-combat-balance.json
python tools/test_changed.py

# Run maintained tests in isolated bounded shards. Each file receives an exact
# pass/fail/timeout verdict even if a third-party pytest teardown hook stalls.
python tools/run_pytest_shards.py --timeout 60 --quiet-passes --checkpoint .release-pytest-shards.json

python tools/verify_jianghu_semantics.py --json artifacts/final-semantic-audit.json
python tools/audit_state_bloat.py --json artifacts/final-state-bloat-audit.json
python tools/verify_noop_roundtrip.py --json artifacts/final-noop-roundtrip.json
python tools/project_jianghu_development.py --json artifacts/final-development-projections.json >/dev/null

# Maintained release horizon. These disposable simulations do not mutate the
# canonical campaign state. Ninety days is the release-blocking integration
# horizon; longer A/B soaks are deliberate extended QA, not a merge/release gate.
python tools/run_long_horizon.py --days 1 --json artifacts/final-1d.json
python tools/run_long_horizon.py --days 7 --json artifacts/final-7d.json

run_resumable() {
  days=$1
  name=$2
  budget=${3:-120}
  checkpoint="artifacts/${name}.checkpoint.json"
  result="artifacts/${name}.json"
  rm -f "$checkpoint" "$result"
  while :; do
    set +e
    python tools/run_long_horizon.py --days "$days" --checkpoint "$checkpoint" --json "$result" --frontier-budget "$budget" --checkpoint-every 20
    code=$?
    set -e
    if [ "$code" -eq 0 ]; then break; fi
    if [ "$code" -ne 2 ]; then exit "$code"; fi
  done
  rm -f "$checkpoint"
}

# Dense physiology/frontier periods should not turn release verification into one
# opaque long-running process. Thirty- and ninety-day gates resume from bounded
# checkpoints until the exact horizon completes.
run_resumable 30 final-30 120
run_resumable 90 final-90 120

if [ "${SHINOBI_EXTENDED_SOAK:-0}" = "1" ]; then
  run_resumable 365 final-365a 120
  run_resumable 365 final-365b 120
  python tools/compare_long_horizon.py artifacts/final-365a.json artifacts/final-365b.json --json artifacts/final-365-determinism-comparison.json
  printf '%s\n' 'EXTENDED 365-DAY A/B SOAK PASS'
else
  printf '%s\n' 'EXTENDED 365-DAY A/B SOAK SKIPPED (set SHINOBI_EXTENDED_SOAK=1 to run)'
fi

printf '%s\n' 'RELEASE VERIFICATION PASS'
