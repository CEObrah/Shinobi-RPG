# Runtime Architecture

The game has three mechanical authorities: `runtime/`, `game/`, and `state/`. Chat, narration, tests and Skill prose never override committed state.

Every persistent gameplay transaction validates actor, revision, authority, knowledge, ownership, resources and due causal dependencies; builds an exact write manifest; validates schemas/templates/conservation; persists atomically; records a receipt; and narrates only after commitment.

Time is settled through the compact Jianghu causal frontier. Cost scales with due work rather than total world population. Routine offscreen progression belongs to faction/institution/cohort owners; exact people wake when individual causality requires it.

Martial-faction people are persistent identities. Aggregate civilians are consumed on recruitment/materialization. Deployments reference existing people and never create manpower.

Exact combat is a bounded local geometry patch. Strategic/formation abstractions remain separate and reconcile consequences once.
