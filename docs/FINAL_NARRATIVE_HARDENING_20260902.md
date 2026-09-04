# Final Narrative Hardening - 2026-09-02

## Observed cross-game failure class

Live Sword play demonstrated a systemic presentation failure that also had to be audited in Shinobi: mechanically correct context could still be rendered as polished status/briefing prose instead of a lived serialized scene.

The bad structural pattern is:

`dateline -> generic atmosphere/accounting -> state distinctions -> NPCs verbalize those distinctions -> large menu`

The analogous combat failure is `action -> result -> status -> next action`.

## Repairs

### Whole-game prose contract

The Shinobi GM Skill now has a top-level hard narrative quality gate. Family life, Jianghu travel, training, markets, faction business, missions, politics, quiet scenes, personal combat, pursuit, recovery, and aftermath all use the same serialized-scene standard.

The Skill and scene-craft references explicitly reject polished state dumps, validator-shaped narration, NPCs used as runtime mouthpieces, premature decision screens, and resolver-transcript combat. The writer must choose the dramatic pressure and camera focus instead of emptying the context packet into prose.

Combat presentation now explicitly follows mechanically committed physical causality through anticipation, geometry, human intent expressed through action, attack/defense interplay, reversal, bodily consequence, and aftermath. Mechanically repetitive exchanges compress rather than becoming a combat log.

### Choice UX

Choice scaffolding now uses the smallest useful set, usually two to four materially distinct choices. More than five is exceptional. Menus appear at real player decisions rather than serving as the default closing device for every scene.

### Runtime writer handoff

`gm_scene_context.scene_direction` now exposes dynamic narrative-stage and paraphrase-risk signals without creating hard truth.

Natural-language report/event summaries are kept cold rather than copied into `world_pressure`; the writer receives identifiers, metadata, and an exact-read affordance. This preserves information access without feeding report prose directly into the scene generator.

Regression coverage explicitly verifies that report-shaped summary prose does not enter the primary writer workspace.

## Cross-game analogue audit

The shared narration/choice/combat-presentation/report-handoff defect is repaired in both Sword & Banners and Shinobi.

Sword additionally had a behavior-profile indexing defect and placeholder recurring command identities. Shinobi's analogous character-direction system was inspected. It does not use a cold behavior-profile index for the broad Jianghu population; it derives backstage direction from exact person owners, relationships, current cognition/goals, mission/process state, physical presence, and scene continuity. No analogous profile-index loss was found, so no fake mirror subsystem was introduced.

## Verification ledger

Final Shinobi tree:

- `python tools/quick_check.py`: PASS.
- `python tools/verify_jianghu_semantics.py`: PASS, 0 errors, 0 warnings.
- `python tools/audit_state_bloat.py`: PASS.
- `PYTHONPATH=runtime python tools/verify_noop_roundtrip.py`: PASS across all 240 faction owners.
- Maintained `tools/test_changed.py` for the narration/runtime/Skill changed paths: 105 passed, RC=0.
- Focused scene/Skill/combat-presentation batch: 35 passed.
- Narrative hard-gate regression file: 3 passed, including cold-report-prose coverage.

## Release boundary

This package is revision-1 source plus baseline state for a clean `main` deployment. It does not claim that Railway, MCP connector state, or an installed ChatGPT Skill has already refreshed. Deployment should use wiped campaign and private recovery storage as previously specified for the revision-1 rebaseline.
