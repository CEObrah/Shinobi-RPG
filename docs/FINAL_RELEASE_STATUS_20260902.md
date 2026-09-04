# Final Release Status - 2026-09-02

## Canonical package state

- Campaign revision: **1**.
- Canonical Git branch: **`main` only**.
- Runtime startup: `SHINOBI_GIT_BRANCH=main PYTHONPATH=/app/runtime python -m shinobi_runtime.bootstrap`.
- Split campaign/durability branch bootstrap has been removed from the active source tree.
- Pre-rebaseline revision-158 state is preserved at `docs/forensics/campaign-state-revision-158-pre-rebaseline.tar.gz`.
- Deployment requires a fresh/cleared Railway campaign checkout and a fresh/cleared private runtime WAL/receipt/recovery directory.
- No legacy campaign branch is required for recovery or forensics.

## Narrative contract

The repository Skill keeps ChatGPT as the scene engine and the runtime as hard-consequence authority. The long-form scene standard applies to dialogue, family life, travel, training, politics, investigation, downtime, personal combat, battles, sieges, aftermath, and transitions. Important sequences should build, develop/reverse, resolve, and leave consequences/residue rather than collapse into command/result/status loops.

Confirmed P0/P1/systemic P2 defects must trigger an audit of the analogous subsystem in the other game before the issue is called globally fixed.

## Verification actually run on this final tree

- `python tools/quick_check.py` - PASS.
- `python tools/test_changed.py ...` for final main-branch/rebaseline/release-surface paths - **57 passed**.
- Single-main bootstrap/rebaseline/release-surface focused batch - **21 passed**.
- Narrative/scene/cross-game contracts - **46 passed**.
- Exact combat/pursuit/withdrawal/mounted/parley/span safety - **68 passed**.
- Transaction crash recovery/WAL/scheduler/living-world/world invariants - **111 passed**.
- `python tools/verify_jianghu_semantics.py` - PASS, **0 errors / 0 warnings**.
- `python tools/audit_state_bloat.py` - PASS.
- `python tools/verify_noop_roundtrip.py` - PASS.

The package itself still requires deployment-tier verification after the user commits it to `main`, clears the old Railway volumes/recovery store, deploys, refreshes MCP/Skill as needed, and confirms live `get_play_context` reports revision 1.
