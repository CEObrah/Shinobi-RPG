# Team Guy development-cursor authority repair

Date: 2026-08-11
Campaign revision: 45
Campaign time: SE-0061-02-22T10:18:21

## Diagnosis

Team Guy's autonomous exact-team training correctly advanced real skill values and the shared `state/development/banks.json` entries, but Guy, Lee, Neji, and Tenten still carried legacy character-side `development.last_settled_at` fields from the initial campaign baseline. Once a character has a shared development-bank entry, `resolved_through` in that bank is the sole writable development cursor. Carrying both cursors violates state consolidation and risks future progression drift.

## Source correction

Commit `e0d2f68390b4961d5f5d4b3a7c38ed2f5c8e493d` adds a production-planner normalization guard. Before no-op write pruning, any semantic transaction that changes a character's shared development-bank entry removes that character's legacy `development.last_settled_at` field in the same bounded transaction. The guard resolves only bank entries changed by the current write set; it does not scan global character state and does not create a second progression authority.

Regression coverage exercises both direct exact-character training and Team Guy's autonomous exact-team training path.

## Campaign repair

State-only maintenance commit `e84d1aa23c0dda2feb6bf7cdf1499c10db19f7d2` removed only the redundant `development.last_settled_at` fields from:

- `state/char/guy.json`
- `state/char/lee.json`
- `state/char/neji.json`
- `state/char/tenten.json`

The repair runner guarded campaign revision/time and the four authoritative bank entries before writing. Its diff guard required exactly zero added lines and four deleted lines across those character records.

The repair preserved Team Guy's earned progression, development residual credits, shared-bank `resolved_through` timestamps, campaign revision 45, and world time `SE-0061-02-22T10:18:21`.

## Verification gate

This documentation commit intentionally follows the state-only repair so canonical CI, including runtime tests and the deterministic real-campaign replay, executes against the repaired campaign state and the corrected producer together.
