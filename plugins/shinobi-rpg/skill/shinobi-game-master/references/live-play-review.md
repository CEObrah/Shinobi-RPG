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
- a committed transition becoming unavailable to narration after OOC/tool/context interruption, causing re-entry to reconstruct from terminal state or casualty totals instead of recoverable receipt chronology;
- a stale scene/activity handoff being treated as a fresh unresolved decision after current-revision evidence already proves Wei acted on it;
- coarse combat intent being over-constrained, or doctrine silently overriding explicit player target/weapon/Qi/poison/restraint instructions;
- combat geometry, fatigue, defense load, injury, Qi, poison, equipment, or ammunition behaving inconsistently;
- House missions skipping acceptance, authority, commander, exact member assignment, equipment/provisions, causal travel, allied mobilization, return, settlement, or AAR;
- factions becoming static, plot-protected, or player-serving rather than resource/goal/authority driven;
- economy, recruitment, population, custody, treatment, travel, or production creating/destroying resources without the owning mechanic;
- interaction attempts being narrated as responses or institutional acceptance;
- substantive interaction flattened into a status digest, exposition block, or one generic quote when real participants should respond to one another;
- multiple NPCs speaking as interchangeable exposition channels instead of from distinct lawful roles, knowledge, relationships, and stakes;
- vague interaction caused by stopping at compact context when one small exact player-permitted read would supply the material participants are actually discussing;
- excessive backend caveats inside IC narration;
- self-referential corrective contrast or anti-exposition boasting inside IC narration, such as telling the player that someone does not repeat what the room already knows instead of simply omitting it;
- NPCs asking for facts that are already shared premises for the relevant participants, creating fake ignorance or recap dialogue instead of pursuing a live unknown;
- context/retrieval bloat that could be replaced by one exact targeted read;
- a valid but underdeveloped system that materially reduces tactical depth, causal flow, world vitality, clarity, or long-campaign reliability.

## QA output contract

Review every live gameplay turn and append exactly one compact `OOC QA:` footer after the IC response. Use `PASS`, `WARN`, or `FAIL`. A turn can be `PASS` only after fresh authoritative `get_play_context` succeeded for that turn, the returned delivery-integrity proof confirms both the current GM Skill/runtime compatibility token and current deployment/source compatibility, and no material defect was observed in the resolved/narrated beat, and identifies the certified `release_baseline_id`, baseline revision, and baseline world time for the same live campaign identity. A failed runtime read, missing handshake proof, Skill-contract mismatch, deployment incompatibility, or campaign-lineage contradiction is automatically `FAIL`. `WARN` means the result is mechanically valid but a reusable system, narration, pacing, or UX weakness is visible; `FAIL` also covers false campaign truth, agency/knowledge breakage, blocked intent, major exploit, misleading consequence, chronology error, or persistence risk.

Report at most the strongest reusable finding: observed symptom, player impact, likely owner, and smallest coherent root fix/regression. Valid owners include GM Skill/presentation, runtime interface, runtime/rules mechanics, game data, projection source, explicit state repair, deployment/delivery, or feature/design. Do not manufacture or repeat a defect to satisfy the footer; `PASS — fresh runtime + Skill handshake + deployment compatibility verified; no material defect observed this turn` is the correct output when nothing material failed. Ordinary play is observational only; actual source/state mutation requires explicit `OOC DEV:` intent.

### Turn QA is not release certification

A mechanically coherent turn on one live revision does not prove that the intended release was deployed. For an explicit fresh-release acceptance claim, require all of the following evidence as one release unit: the packaged release-state contract verifies the intended starting snapshot; the production service passes its source/deployment startup guard; the connected `get_play_context` succeeds with the packaged Skill token; the live campaign identity/revision/time match the intended acceptance baseline before play; and at least one black-box smoke is executed through the same ChatGPT/MCP path the player will use. If any tier is unavailable or stale, report that tier as unverified or failed instead of converting local repository PASS counts into a production guarantee.

## Immediate escalation

Flag immediately when an issue risks false campaign truth, breaks agency/knowledge boundaries, blocks declared intent, creates a serious exploit, makes a consequential choice misleading, loses a committed transition needed to explain current state, or threatens transaction durability.
