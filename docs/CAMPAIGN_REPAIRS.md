# Campaign Repair Log

This file records explicit production campaign-state repairs. Repairs are exceptional maintenance actions, not normal gameplay writes.

## 2026-08-11 - Remove test advance-time revision 27

- Campaign: `shinobi-wei-main`
- Bad transaction commit: `2080fa82f111671d59c4e450598f282c83934512`
- Bad request: `wei.advance.until.next.causal.r26`
- Bad resulting revision: `27`
- Bad resulting world time: `SE-0062-01-01T00:00:00`
- Last legitimate campaign commit: `9cb24aefa5130a3f1e8fd8c1e5804eb05505b842`
- Restored campaign revision: `26`
- Restored world time: `SE-0061-02-07T14:18:21`
- Repair commit: `7780d94794457439c6c0aa1bc8ee1573c7f1a795`

### Diagnosis

Revision 27 was created intentionally as a test `advance_time` transaction and was not intended to become campaign canon. The transaction advanced autonomous simulation across the large time interval and modified 51 files, all beneath `state/`, including force/formation state, missions, population, faction registries, world events, scheduler state, institutions, economy, scene state, and campaign metadata.

No source, rules, tests, Skill files, or other non-state paths were part of the bad transaction.

### Repair

The repair commit preserves the current `main` source tree and atomically replaces only the complete `state/` subtree with the exact tree from legitimate revision 26 (`aac518dbd1ba4cec76734f779788b8381f0f5980`). This removes all consequences produced solely by the test timeskip while preserving every legitimate campaign mutation through revision 26 and all later non-state development commits.

This documentation commit intentionally follows the state-only repair so Railway's non-state deployment watch restarts the service and bootstrap can safely fast-forward the persistent campaign checkout to the repaired `main` head.

### Acceptance

Before consequential play resumes, verify that the live Runtime reports campaign revision `26` and world time `SE-0061-02-07T14:18:21`, and that runtime recovery reports no unresolved WAL state.

## 2026-08-11 - Repair stale revision-26 scene projection

- Campaign: `shinobi-wei-main`
- Campaign revision retained: `26`
- World time retained: `SE-0061-02-07T14:18:21`
- Mechanical gameplay state changed: no
- Projection repair commit: `4660eb059969bc52a0ed0bee9a17d38a25f9bfc7`
- Readiness projection implementation head: `38f91abeb28fe2af13a0d0abe4196178ad555601`

### Diagnosis

`state/scene.json` still described Black Hound as unpracticed and Team Fujin's personal refits as pending. Those statements were contradicted by already-committed campaign truth: Black Hound's `bh-walkthrough-004` shared training transaction at world revision 19 persisted a one-hour session ending `SE-0061-02-06T22:15:00` and doctrine familiarity `1` for all six members, while Team Fujin's Kai, Riku and Mei refits were committed at revisions 23 through 25.

The stale scene also retained an expired question about the night before the `SE-0061-02-07` 08:00 report and an approaching consequence for that already-passed report time.

### Repair

The state-only repair changes only `state/scene.json`. It removes expired questions and resolved information paths, replaces the contradicted Black Hound familiarity and Team Fujin fitting statements with the already-persisted facts, updates the last-scene summary to the current Team Fujin training completion, and removes the false observable pressure that Black Hound had never practiced.

No character sheet, team owner, doctrine owner, inventory, stock, development bank, scheduler, mission, economy, campaign metadata, world time, or gameplay revision changes in this repair.

Separately, the runtime projection now exposes exact-team recovery eligibility, co-located member groupings without exact teammate location disclosure, authorized instructor presence, and the latest resolved team session. This prevents the GM from probing training previews blindly or mistaking an existing session for an unperformed one.

### Acceptance

Before consequential play resumes, verify that the live Runtime still reports revision `26` at `SE-0061-02-07T14:18:21`, that Black Hound inspection exposes its latest resolved session ending `SE-0061-02-06T22:15:00`, that the next all-member recovery eligibility is `SE-0061-02-09T06:15:00`, and that live play context no longer claims Black Hound has never practiced or Team Fujin refits are pending.
