# Runtime Architecture

The game has three mechanical authorities: `runtime/`, `game/`, and `state/`. Chat, narration, tests and Skill prose never override committed state.

Every persistent gameplay transaction validates actor, revision, authority, knowledge, ownership, resources and due causal dependencies; builds an exact write manifest; validates schemas/templates/conservation; persists atomically; records a receipt; and narrates only after commitment.

Time is settled through the compact Jianghu causal frontier. Cost scales with due work rather than total world population. Routine offscreen progression belongs to faction/institution/cohort owners; exact people wake when individual causality requires it.

Martial-faction people are persistent identities. Aggregate civilians are consumed on recruitment/materialization. Deployments reference existing people and never create manpower.

Exact combat is a bounded local geometry patch. Strategic/formation abstractions remain separate and reconcile consequences once.

## Physical presence and scene authority

Exact physical presence has one mechanical resolver: `runtime/shinobi_runtime/martial_world/physical_presence.py`. Resolve custody first, then active exact combat, then active route movement, then the sparse person owner location. Use that resolver or its derived physical-unavailability set whenever legality depends on where a person actually is. A stored home/default location must never make a traveler, captive, or active combatant simultaneously available at home.

`state/scene.json` is presentation/continuity only. It may provide player-visible candidate identities or receive a projected location/cast after a committed command, but it never grants co-presence, action legality, combat membership, training access, public attendance, or social access. Exact commands revalidate physical presence from durable owners.

`state/martial-world/scene-session.json` is a non-mechanical live conversation owner. It preserves established participants, process, soft duration, agenda, and unresolved conversational threads across command boundaries. Scene close abandons unresolved questions rather than pretending they were answered. Travel, combat, and other hard boundaries close a reversible scene when appropriate.

Attributed speech is durable observed history, not objective world truth. `scene-history-head.json` keeps a bounded recent window for fresh-context continuity; lossless period shards under `state/martial-world/scene-history/` preserve older attributed statements. These owners are `authority:false` and cannot grant knowledge beyond what the player actually observed, move resources, create obligations, or prove an NPC opinion true.

## Time and semantic waiting

Player-facing broad time passage settles all causally due internal frontiers through the requested horizon unless a hard wake, protected decision, or matching semantic wait criterion intervenes. Saved standing training/routines accrue without requiring the caller to remember a special training flag. Distinct natural-language stop reasons are alternatives through `wait_policy.any_of`; fields inside one precise clause are conjunctive, while values inside one criterion field are alternatives. This lets unrelated reports pass while any separately requested material development can stop the wait.

Long waits are transaction-bounded by causal frontier count rather than by an artificial amount of campaign time. `quiet_frontier_chunk` means the current atomic transaction reached its work budget while the original wait remains valid. The GM refreshes revision/context and automatically continues toward the same target with a fresh request ID. This implementation boundary must never become a player-facing acknowledgement loop or an elapsed-time cap.

An active scene requires explicit scene-time intent before broad chronology can cross it. `preserve_active_scene`, finishing/leaving, and deliberately skipping to conclusion are distinct policies. Bare natural-language `continue` is a GM continuation instruction, not elapsed-time authority.
