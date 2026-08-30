# Runtime Architecture

The game has three mechanical authorities: `runtime/`, `game/`, and `state/`. Chat, narration, tests and Skill prose never override committed state.

Every persistent gameplay transaction validates actor, revision, authority, knowledge, ownership, resources and due causal dependencies; builds an exact write manifest; validates schemas/templates/conservation; persists atomically; records a receipt; and narrates only after commitment.

Time is settled through the compact Jianghu causal frontier. Cost scales with due work rather than total world population. Routine offscreen progression belongs to faction/institution/cohort owners; exact people wake when individual causality requires it.

Martial-faction people are persistent identities. Aggregate civilians are consumed on recruitment/materialization. Deployments reference existing people and never create manpower.

Exact combat is a bounded local geometry patch. Strategic/formation abstractions remain separate and reconcile consequences once.


## Intent and mechanic boundary

The command catalog is a **mechanical consequence registry**. It answers which runtime owner can adjudicate or persist a hard effect after the GM already understands the action. It never defines the set of possible gestures, speech acts, local scene interactions, creative tactics, or ordinary NPC behavior. Unsupported **hard state mutation** fails closed; unsupported ordinary fiction does not.

Reversible scene truth may be GM-realized from fresh lawful context. Salient observed local details may be persisted as authority-false scene facts so fresh chats can recover them, but those records have no mechanical-consequence authority. Hard facts are promoted only through domain authority. Attributed speech proves who said something, not whether the statement is objectively true. Private cognition support, when explicitly returned by a runtime envelope, may guide what an NPC chooses to disclose without granting Wei hidden knowledge.

## Transaction evidence and re-entry

Immutable idempotency receipts live outside mutable campaign owners. They are transaction evidence, not a fourth mechanical authority. A new receipt records the exact committed command together with its result; legacy receipts without embedded command bytes remain valid and readable.

The production read projection may recover only the receipt whose `committed_revision` equals the campaign's current revision. `transition:current` exposes that evidence in bounded ordered event pages so interrupted narration can be reconstructed from the actual committed transition rather than guessed from terminal state. It never accepts an arbitrary revision and never changes campaign time or state.

On re-entry, refreshed play context establishes current truth. The current-revision receipt establishes how that truth was reached. If the two disagree, fail closed rather than allowing receipt evidence to override state. An embedded prior command may recover standing intent only under the normal player-agency/delegation rules.

## Physical presence and scene authority

Exact physical presence has one mechanical resolver: `runtime/shinobi_runtime/martial_world/physical_presence.py`. Resolve custody first, then active exact combat, then active route movement, then the sparse person owner location. Use that resolver or its derived physical-unavailability set whenever legality depends on where a person actually is. A stored home/default location must never make a traveler, captive, or active combatant simultaneously available at home.

`state/scene.json` is presentation/continuity only. It may provide player-visible candidate identities or receive a projected location/cast after a committed command, but it never grants co-presence, action legality, combat membership, training access, public attendance, or social access. Exact commands revalidate physical presence from durable owners.

`state/martial-world/scene-session.json` is a non-mechanical live conversation owner. It preserves established participants, process, soft duration, agenda, and unresolved conversational threads across command boundaries. Scene close abandons unresolved questions rather than pretending they were answered. Travel, combat, and other hard boundaries close a reversible scene when appropriate.

Attributed speech and salient reversible scene facts are durable observed history, not objective mechanical authority. `scene-history-head.json` keeps a bounded recent window for fresh-context continuity; lossless period shards under `state/martial-world/scene-history/` preserve older records. These owners are `authority:false` and cannot grant knowledge beyond what the player actually observed, move resources, injure people, transfer ownership, create obligations, or prove an NPC opinion true.

## Time and semantic waiting

Player-facing broad time passage settles all causally due internal frontiers through the requested horizon unless a hard wake, protected decision, or matching semantic wait criterion intervenes. Saved standing training/routines accrue without requiring the caller to remember a special training flag. Distinct natural-language stop reasons are alternatives through `wait_policy.any_of`; fields inside one precise clause are conjunctive, while values inside one criterion field are alternatives. This lets unrelated reports pass while any separately requested material development can stop the wait.

Long waits are transaction-bounded by causal frontier count rather than by an artificial amount of campaign time. `quiet_frontier_chunk` means the current atomic transaction reached its work budget while the original wait remains valid. The GM refreshes revision/context and automatically continues toward the same target with a fresh request ID. This implementation boundary must never become a player-facing acknowledgement loop or an elapsed-time cap.

An active scene requires explicit scene-time intent before broad chronology can cross it. `preserve_active_scene`, finishing/leaving, and deliberately skipping to conclusion are distinct policies. Bare natural-language `continue` is a GM continuation instruction, not elapsed-time authority.

## GM-private director projection

The read architecture intentionally separates **mechanical truth**, **GM-private current-scene truth**, and **Wei-visible knowledge**. The AI GM may receive a bounded omniscient packet for the active scene or exact combat because coherent direction often requires facts that Wei has not perceived yet. Every such packet is explicitly marked private and non-authoritative for disclosure. Public narration and player choices must still be filtered through Wei's lawful perception/knowledge boundary. This avoids the former failure mode where protecting secrets starved the GM of the causal information needed to make NPCs and combat feel alive.

Exact established scene participants may contribute bounded private character/cognition context even before a conversation session exists. A scene session is continuity, not an NPC activation switch: present people may initiate, interrupt, object, joke, confer, or otherwise behave naturally on ordinary continuation. Exact combat may additionally expose private encounter causality and participant direction. For legacy active route contacts created before causal metadata existed, `runtime/shinobi_runtime/api/encounter_causality.py` may reconstruct only a historically deterministic motive whose antecedent saved facts fully prove the old decision branch; otherwise motive remains unknown rather than invented. This read-time compatibility projection never rewrites the encounter or grants Wei knowledge.
