# Live-Play Quality Review

## Contents

1. Purpose
2. Review cadence
3. Narration and scene craft
4. Characters and dialogue
5. Combat mechanics
6. Combat narration
7. Systems, features, and player experience
8. Simulation, balance, and world depth
9. Evidence and diagnosis
10. Improvement ownership and action

## Purpose

Treat actual campaign play as continuous quality evidence for the GM Skill, runtime, rules, simulation, content, and player experience. Judge not only whether a turn is mechanically valid, but whether the game is producing clear, engaging, tactically meaningful, causally coherent play.

The goal is continuous improvement without turning normal play into a design review meeting.

## Review cadence

Observe quality continuously during play. Do not append a critique to every turn.

Surface a finding immediately when it:

- blocks the declared action or makes the game impossible to operate correctly;
- creates or risks false campaign truth;
- materially violates player agency or knowledge boundaries;
- makes a consequential combat or decision state misleading;
- exposes a serious exploit, conservation error, or repeatable mechanical failure.

Otherwise preserve scene flow and mention only the strongest useful finding at a natural stopping point. Batch related observations when several symptoms share one cause.

Repeated symptoms matter more than one unusual scene. Do not rebalance a system from one lucky roll, one failed action, or one stylistic sentence unless the underlying rule is clearly defective.

## Narration and scene craft

Judge whether narration is doing its actual job:

- causal action is understandable without backend knowledge;
- scene transitions are concrete rather than administrative or abstract;
- important spatial, temporal, social, and material changes are legible;
- prose has momentum and does not repeatedly summarize what was just shown;
- exposition appears when useful instead of replacing lived action;
- tension comes from real stakes, uncertainty, incentives, and consequences rather than vague ominous language;
- dialogue and action beats carry characterization instead of narrator explanation doing all the work;
- player decisions land at a clear point and the handoff is understandable;
- the scene does not become repetitive in sentence structure, cadence, imagery, or interaction pattern;
- spectacle remains earned by the resolved event rather than inflated beyond it.

When a narration problem appears, distinguish a one-line wording problem from a reusable craft problem. Prefer fixing the reusable rule when the symptom is likely to recur.

## Characters and dialogue

Judge whether characters feel like independent people rather than interchangeable response generators.

Review:

- speaker and addressee clarity;
- age, rank, profession, culture, personality evidence, relationship, authority, audience, and current pressure;
- different conversational registers for different relationships;
- NPC-to-NPC interaction that does not always route through Wei;
- disagreement, correction, humor, restraint, interruption, deference, challenge, affection, or hostility when actually grounded;
- continuity of prior promises, injuries, grievances, loyalties, duties, and knowledge;
- whether crowded casts remain trackable through compact identity cues;
- whether silence has a concrete reason instead of occurring because narration forgot the cast.

Do not optimize for maximum dialogue. Optimize for believable social presence.

## Combat mechanics

Judge combat as a game system as well as a resolved simulation.

Look for:

- dominant loops or obviously superior actions that erase meaningful choice;
- actions that have no plausible counterplay or whose counters are not represented by mechanics;
- meaningless choices whose outcomes are mechanically equivalent;
- excessive whiffing, excessive lethality, damage sponges, or fights that resolve before tactics matter;
- action-economy or initiative behavior that freezes teammates or allows implausible repeated turns;
- geometry, range, cover, concealment, movement, terrain, and objectives that fail to influence outcomes when they should;
- resource costs, fatigue, strain, wounds, equipment, and collateral that fail to constrain future choices;
- intelligent opponents behaving irrationally because the decision model lacks objectives, knowledge, risk, retreat, capture, protection, or coordination;
- team doctrine and familiarity having no visible mechanical consequence, or overwhelming individual capability;
- exploits caused by missing conservation, duplicate credit, free repositioning, free information, repeated retry, or ambiguous authority;
- mechanics that are correct but too opaque for the player to make an informed tactical decision.

Do not call a difficult fight unfair merely because Wei loses. Fairness means the rules, information boundaries, capabilities, objectives, and counterplay are coherent.

## Combat narration

Judge whether the prose exposes the useful causal structure of the mechanics without becoming a combat log.

Review:

- who initiates and who perceives the initiation;
- startup, movement, path, timing, range, obstruction, counter, contact or miss, and aftermath when causal;
- new positions after each meaningful exchange;
- injuries and resource consequences that remain present rather than disappearing after one sentence;
- techniques described by recognizable physical effects instead of generic energy language;
- uncertainty preserved until Wei can lawfully identify an attacker, technique, trap, clone, feint, poison, or objective;
- allies acting with visible agency instead of freezing while Wei receives every beat;
- opponent competence shown through behavior rather than narrator declarations;
- tactical choices arising from the changed battlefield instead of a generic menu.

Flag combat narration when a player cannot tell why something hit, missed, was countered, changed position, consumed a resource, or created the next decision.

## Systems, features, and player experience

Treat friction during play as product evidence. Review features across training, teams, missions, travel, economy, equipment, relationships, institutions, family, intelligence, forces, world simulation, and any newly exposed runtime domain.

Look for:

- missing actions the fiction clearly supports but the runtime cannot represent;
- command descriptors that hide legal values, required nulls, variants, authority, timing, or eligibility;
- rejection messages that say an action is blocked without saying who, why, or what state would make it possible;
- important state that exists but is not discoverable from the player interface;
- plausible, player-valid actions disappearing from choice scaffolding solely because the runtime cannot yet construct them;
- capability validation that silently shrinks the player's apparent possibility space and thereby masks an interface or feature defect;
- too much low-value ceremony for common actions;
- accidental teleportation, stranded appointments, duplicated transactions, or workflows that stop before obvious prerequisite logistics;
- stale projections or summaries that contradict committed state;
- features that technically exist but never create interesting decisions;
- repeated manual bookkeeping that should become a bounded semantic operation;
- opportunities for a new feature when play repeatedly requires the same unsupported workaround.

Do not confuse an implementation blocker with an in-world prohibition. When an action is lawful and plausible but blocked only because the runtime lacks a discoverable reference, discriminator, payload shape, capability, or read path, keep the action visible as a QA finding rather than quietly deleting it from consideration. The GM must not present it as executable, but must preserve declared intent and report the implementation gap when it materially affects play.

Prefer improving the generic system over adding a one-off special case for Wei.

## Simulation, balance, and world depth

Judge whether the living world remains causally active beyond the player without becoming arbitrary.

Review:

- institutional, clan, family, team, faction, economic, and military actors receiving comparable simulation depth when causally relevant;
- offscreen progression conserving time, people, instructors, money, equipment, health, and authority;
- representation scale staying neutral between exact characters, cohorts, formations, and aggregates;
- canon or setting data being applied fairly across villages and cultures rather than privileging the most documented faction;
- future canon not leaking backward into current campaign truth;
- major NPCs and institutions possessing goals, constraints, relationships, resources, and consequences independent of Wei;
- progression rates, prices, training throughput, force capability, reputation, and other numerical systems producing sensible long-run behavior;
- new depth adding meaningful causality rather than data for its own sake.

A desire for more detail is not automatically a defect. Recommend depth when it creates decisions, consequences, distinct actors, or useful simulation.

## Evidence and diagnosis

For a meaningful finding, keep the reasoning concrete:

1. **Observed symptom:** what happened in actual play.
2. **Player impact:** why it harmed clarity, agency, tactics, immersion, pacing, fairness, continuity, or usability.
3. **Likely owner:** Skill, runtime interface, reducer/mechanics, static rule/content, player-facing projection, or campaign state.
4. **Confidence:** confirmed defect, repeated pattern, plausible design concern, or speculative idea.
5. **Smallest coherent fix:** the least invasive reusable change that addresses the cause.
6. **Regression check:** what should be tested or observed afterward so the fix does not merely move the problem.

Do not diagnose from vibes alone when authoritative state or source inspection can resolve the question.

## Improvement ownership and action

Use these default owners:

- prose, dialogue, pacing, cast clarity, transitions, choice framing, combat presentation: GM Skill;
- discoverability, payload contracts, error explanations, action availability: runtime interface;
- success/failure resolution, costs, timing, damage, progression, combat behavior, conservation: runtime/rules mechanics;
- world definitions, clan profiles, technique data, prices, static content: game data/rules;
- contradictory summaries or derived views: projection/source diagnosis before any state repair;
- confirmed corrupted campaign fact: explicit repair or migration with provenance;
- missing repeated capability: feature proposal, then the narrowest coherent implementation if explicitly authorized.

During ordinary IC or OOC play, proactively suggest worthwhile GitHub changes but do not silently edit source. When the player explicitly requests `OOC DEV:` implementation, make the coherent source change, update its contracts/tests/docs, verify it, and keep campaign-state repairs separate.

Prefer fixes that improve the game generally. Avoid overfitting mechanics or narration to make Wei win, to force a preferred story, or to erase legitimate difficulty.
