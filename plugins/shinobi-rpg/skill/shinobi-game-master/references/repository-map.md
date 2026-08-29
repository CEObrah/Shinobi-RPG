# Repository Map

Current authorities:

```text
runtime/shinobi_runtime/commands/       closed Jianghu semantic commands
runtime/shinobi_runtime/martial_world/  deterministic domain mechanics
runtime/shinobi_runtime/martial_world/physical_presence.py  single exact physical-presence resolver: person + custody/combat/route owners
runtime/shinobi_runtime/martial_world/scene_sessions.py    reversible scene/session and attributed-speech continuity
runtime/shinobi_runtime/commands/jianghu_scene.py          generic interaction attempts and non-mechanical scene-session writes
runtime/shinobi_runtime/commands/jianghu_time.py           full-horizon public time settlement, semantic waits, explicit scene time policy
runtime/shinobi_runtime/combat/         exact local geometry/defense/team tactics
runtime/shinobi_runtime/people/         direct persistent person reads
runtime/shinobi_runtime/tx/             transaction/WAL/receipt durability
runtime/shinobi_runtime/api/transition_operations.py       bounded current-revision receipt/transition re-entry projection
runtime/contracts/                      current closed structural templates

game/data/martial-world/                static Jianghu rules/world data
game/schemas/                           current mutable-owner JSON schemas

state/meta.json                          campaign time/revision/mode
meta.player_id -> person route          authoritative player person; player view is derived
state/scene.json                         presentation/continuity projection only; never mechanical presence authority
state/martial-world/scene-session.json     authority:false active reversible conversation/session owner
state/martial-world/interaction-attempts.json  authority:false bounded interaction-thread routing
state/martial-world/scene-history-head.json + scene-history/*.json  authority:false attributed-speech continuity
state/martial-world/                     all other mutable Jianghu owners
state/martial-world/institutional-operations.json  active/archive House mission dossiers; orchestration only
```

For source work, find the smallest owner. For live gameplay, use Runtime reads rather than repository browsing.

Scene authority rule: mechanics asking where a person is must use `physical_presence.py`; `state/scene.json` can only supply presentation candidates that are revalidated against exact presence. NPC attributed speech records what was said, not whether the statement was objectively true.

Transition evidence rule: immutable transaction receipts live outside mutable campaign owners. They may preserve the exact committed command plus result chronology for idempotency/re-entry, but they never override the refreshed campaign state. Player-facing recovery is limited to the receipt that produced the current campaign revision; it is not an arbitrary history interface.
