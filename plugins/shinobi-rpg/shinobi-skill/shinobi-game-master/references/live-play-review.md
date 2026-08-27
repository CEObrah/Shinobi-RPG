# Live Play Review

Treat real Shinobi campaign play as continuous integration, playtesting, narrative review, and feature discovery. Judge correctness and quality continuously without turning every scene into a QA report.

## What to watch

Watch for:
- false campaign truth, stale state, impossible chronology, duplicate ownership, or conservation failures;
- player agency violations, especially invented dialogue/commitments or missing scaffolding after a direct consequential question;
- knowledge leaks, omniscient mission narration, recognition without basis, or rumors treated as fact;
- `continue` accidentally becoming a time skip or protected choice;
- repeated menus for a standing policy, or the opposite problem: a genuine decision left with no useful options;
- selected menu options being treated as invisible control input instead of visible Wei action/dialogue;
- coarse combat intent being over-constrained, or doctrine silently overriding explicit player target/weapon/Qi/poison/restraint instructions;
- combat geometry, fatigue, defense load, injury, Qi, poison, equipment, or ammunition behaving inconsistently;
- House missions skipping acceptance, authority, commander, exact member assignment, equipment/provisions, causal travel, allied mobilization, return, settlement, or AAR;
- factions becoming static, plot-protected, or player-serving rather than resource/goal/authority driven;
- economy, recruitment, population, custody, treatment, travel, or production creating/destroying resources without the owning mechanic;
- interaction attempts being narrated as responses or institutional acceptance;
- repetitive scene summaries where real participants should speak;
- excessive backend caveats inside IC narration;
- context/retrieval bloat that could be replaced by one exact targeted read;
- a valid but underdeveloped system that materially reduces tactical depth, causal flow, world vitality, clarity, or long-campaign reliability.

## QA output contract

After every live gameplay turn, append exactly one concise `OOC QA:` line after the IC result.

When play exposed a concrete reusable issue or improvement, give only the strongest current finding: observed symptom, player impact, likely owner, and smallest coherent fix or regression. Valid owners include GM Skill/presentation, runtime interface, runtime/rules mechanics, game data, projection source, explicit state repair, or feature/design.

When no material improvement is supported by that turn, write:

`OOC QA: No material improvement identified this turn.`

Do not manufacture a problem, repeat an unchanged finding as though new, or expand the QA line into a changelog. Ordinary play is observational only; actual source/state mutation requires explicit `OOC DEV:` intent.

## Immediate escalation

Flag immediately when an issue risks false campaign truth, breaks agency/knowledge boundaries, blocks declared intent, creates a serious exploit, makes a consequential choice misleading, or threatens transaction durability.
