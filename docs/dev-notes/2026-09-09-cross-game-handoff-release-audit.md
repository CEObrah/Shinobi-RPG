# 2026-09-09 Cross-game handoff and release-state audit

## Cross-game analogue checked from Sword & Banners

Original game/subsystem: Sword & Banners campaign-command upward-report projection and GM handoff.

Confirmed Sword defect pattern: a multi-layer bounded projection could hide an older response-bearing pending courier behind newer traffic, could relabel delivered history as pending, and could describe an unresolved route as an in-flight courier.

Shinobi analogue inspected: event-seeking delayed handoffs, semantic waiting, scene open-thread projection, compact interaction history, and exact open-thread retrieval.

Result: defect did not reproduce in Shinobi. Interrupting delayed events stop on the authoritative event-seeking boundary. Active response-bearing scene waits are represented by open-thread state with explicit count/truncation and an exact `scene_open_threads` read path instead of a second chronological tail over durable courier rows. Existing semantic-wait and scene-session regressions passed. No handoff runtime repair was required here.

## Packaging/release defect reproduced as a shared class

Severity: systemic P1 deployment/release defect.

The repository previously had no release contract binding the source candidate to the intended mutable campaign snapshot. That allowed a mechanically valid but stale save to be repackaged with newer source, which is how the revision-106 damaged combat state could supersede the intended revision-97 full-fresh baseline.

Shinobi repair:

- Added a release campaign-state contract pinned to revision 97 and world time `SE-0061-09-27T21:21:45`.
- Added deterministic state-tree digest verification.
- Added exact fresh-combat assertions for the active 12-v-18 encounter, zero elapsed combat, 30 fresh combatants, full player Qi, jian condition 1000, 19 needles, and zero interaction/scene-history records.
- Wired the verifier into the release script.
- Added an adversarial regression that changes a disposable copy to revision 106 and requires release verification to fail.

Sword & Banners received the analogous release-state contract and stale-snapshot rejection test independently in its own repository.

The canonical `state/` tree was not mutated by this development work.
