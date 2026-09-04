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
runtime/shinobi_runtime/api/operations.py                  live player/scene projection, exact presence revalidation, GM-private present-person direction
runtime/shinobi_runtime/api/gm_scene_context.py            scene-first LLM writer workspace and turn-level active-scene directing protocol; never prose authority
runtime/shinobi_runtime/api/command_discovery.py           compact MCP handoff; gm_scene_context is the canonical writer-facing private scene packet
runtime/shinobi_runtime/api/mcp_security.py           transport-independent preview attestation security used by MCP and release tests
runtime/shinobi_runtime/api/encounter_causality.py       GM-private current/legacy route-contact causality projection; deterministic read-time legacy recovery only, never state authority
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

Scene authority rule: mechanics asking where a person is must use `physical_presence.py`; `state/scene.json` can only supply presentation candidates that are revalidated against exact presence. NPC attributed speech records what was said, not whether the statement was objectively true. Salient scene facts record only observed reversible local continuity and never replace hard mechanics.

GM-director rule: exact established present people may expose bounded private character/cognition truth before a conversation session, and exact combat may expose private encounter causality. Active-session/event/authored presence is revalidated against exact physical truth before it reaches the writer. `gm_scene_context.scene_direction` makes the LLM responsible for selecting and staging the next grounded reversible beat; the runtime must not choose a speaker or pre-script ordinary performance. These projections help the AI direct coherent initiative, cross-talk, practical action, combat reaction and dialogue but are not player knowledge and have no mechanical-consequence authority. A formal session whose other participants are no longer physically present remains visible only as lifecycle reconciliation metadata: absent people are removed from live cast/thread eligibility, and opaque durable thread refs are never promoted back into immediate dialogue. Active-session participants and current event/process actors outrank general site attendance as causal beat hints, never as a speaking queue.

Transition evidence rule: immutable transaction receipts live outside mutable campaign owners. They may preserve the exact committed command plus result chronology for idempotency/re-entry, but they never override the refreshed campaign state. Player-facing recovery is limited to the receipt that produced the current campaign revision; it is not an arbitrary history interface.


LLM lifecycle rule: narrative scene start/continue/transition/end belongs to ChatGPT from fresh lawful context. `gm_scene_context.scene_direction.scene_lifecycle` exposes the optional interaction -> `jianghu_scene_session_resolution` persistence route when a people-centered scene needs continuity across commands or fresh contexts. Runtime command completion is never itself a scene boundary; hard movement/time/combat consequences still use their real owners.
