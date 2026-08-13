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

## 2026-08-13 - Repair delegated Fujin training and Black Hound B-rank compensation

- Campaign: `shinobi-wei-main`
- Repair request: `ooc-repair-probe-20260813-r97`
- Repair transaction: `tx.gameplay.148b579ceb5aea440e88735d236f6ce475ab10953df30514d7922a05966cae93`
- Repair revision: `97 -> 98`
- World time: retained at `SE-0061-06-10T01:14:37`
- Repair event: `event.campaign_repair_resolution.148b579ceb5aea440e88`
- Historical Fujin training event: `event.team_training_session_resolved.77a9b9c841a2357d1f6acc95`
- Affected mission: `mission.offer.0a7361790026211550`
- Mission compensation: `70,000 ryō` per participant, six participants, `420,000 ryō` total from `treasury.konoha`

### Diagnosis

The June 4-10 mission travel and time advance were correctly relayed to the causal scheduler. Team Fujin's periodic host actually consumed its `SE-0061-06-05T21:15:00` review and the scheduler later reported no overdue work. The missing training was therefore not a clock-relay defect.

The exact-team autonomous training reducer treated any active mission involving any team member as a whole-team training preemption. Wei's unrelated Black Hound deployment consequently froze Team Fujin even though persisted standing policy explicitly authorized routine assembly at Sword Manor under Zhu or Linh when Wei was unavailable. The source fix now excludes only the policy-named absent player participant from that one delegated training review; a mission involving any remaining trainee still preempts the session.

Separately, living-world player mission offers bypassed the generic mission-creation escrow path. The B-rank Black Hound escort was created with `funding_holder_ref=treasury.konoha` but no settlement terms and no escrow, so successful terminal settlement lawfully produced no transfer. Future player mission offers now create conserved rank-banded participant reward terms and fund them from institutional treasury at offer creation.

House Tang was audited separately and was not missing progression. Its standing seven-day readiness program uses deliberate lazy development settlement on the House review host; unresolved daily windows remain recoverable and are settled deterministically at the next lawful review rather than written every day.

### Repair

A temporary guarded semantic repair command was deployed, previewed at revision 97, and executed through the normal transaction coordinator. It reused the canonical autonomous team-training reducer at the consumed June 5 boundary rather than hand-editing character skills. Wei was excluded because he was deployed. Linh instructed the three eligible Fujin members for eight active hours:

- Kai: `martial_skills.movement` `102 -> 104` (`+2`)
- Mei Arakawa: `martial_skills.movement` `100 -> 102` (`+2`)
- Riku Hyuga: `attributes.awareness` `109 -> 111` (`+2`)

The same repair conserved the omitted B-rank compensation directly from Konoha treasury: `70,000 ryō` to each of Ensui Nara, Hana Inuzuka, Hayama Shirakumo, Hoheto Hyuga, Tekuno Kanden, and Wei Tang, for `420,000 ryō` total. The original terminal mission settlement remains unchanged with empty reward terms so historical provenance is not rewritten; the explicit repair event records the corrective transfer.

After the committed receipt was verified, the one-shot repair command was removed from the production planner and its reducer file deleted. The permanent delegated-training and future mission-offer reward fixes remain.

### Verification and acceptance

Live runtime verification immediately after repair reported revision `98` at the unchanged world time `SE-0061-06-10T01:14:37`. Team Fujin inspection showed the repaired session from `SE-0061-06-05T13:15:00` through `21:15:00`, instructor `char.linh`, and only Kai, Mei, and Riku as trainees. Wei's inventory summary reported `1,540,000 ryō`, including the repaired `70,000 ryō` mission payment. The completed mission remains rank B and succeeded with its original empty settlement terms.

Regression tests were committed for both permanent fixes, but GitHub Actions did not execute them because GitHub rejected the jobs before startup with an account billing/spending-limit annotation. Do not record those workflow runs as test failures or passes. Production runtime import/preview/execution verified the repaired path; ordinary CI remains externally blocked until GitHub can start jobs again.
