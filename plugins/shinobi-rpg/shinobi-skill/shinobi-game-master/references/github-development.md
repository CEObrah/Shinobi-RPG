# Repository Development

Use this reference for `OOC DEV:` work involving GitHub. GitHub is a bounded development surface, never the live campaign-state API or a gameplay authority. Preserve one authoritative source, avoid reconstructing large state manually through the connector, and never expose credentials or secrets. Prefer an uploaded/local workspace for broad coupled edits.

## Development loop

1. Inspect current `main` and the smallest authoritative source owner. Use a branch/PR for nontrivial changes.
2. Keep runtime, schema/data, tests, and repository Skill changes coherent.
3. Run local maintained gates before push:

```text
python tools/quick_check.py
python tools/test_changed.py <changed paths>
```

4. Push/open the PR and inspect required GitHub Actions. The `verify` workflow reruns the fast structural/semantic/determinism gates plus changed-owner regressions from a clean standard runner.
5. If a required check is red, inspect the failing assertion/log and determine whether the implementation, test expectation, fixture, dependency/environment, or workflow is wrong. Repair the correct owner, push again, and rerun. Never distort a valid mechanic merely to satisfy a stale test.
6. Merge only when required checks are green and the branch is current with intentional `main` changes.
7. Railway auto-deployment, deployed source-head confirmation, live smoke validation, MCP schema refresh, and installed Skill refresh are separate steps. A green PR proves none of them.
8. Return to play/playtesting. When play reveals a mechanical defect, add a focused regression when practical and re-enter through explicit `OOC DEV:`.

Normal gameplay does not query or wait on CI. CI verifies software changes; the Runtime remains the only authority for committed campaign mechanics and state.
