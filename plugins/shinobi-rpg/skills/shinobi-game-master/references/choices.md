# Player Choice Presentation

## Contents

1. Default decision shape
2. Define horizon relative to the scene
3. Adapt instead of filling
4. Do not interrupt declared intent
5. Never strand an unresolved decision
6. Ground every suggestion
7. Distinguish world availability from implementation blockage
8. Show time when useful
9. Keep options concise
10. Format

Use choices as an agency aid, never as a command menu or limit on what Wei may attempt.

## Default decision shape

At a genuine unresolved player-facing decision, after narrating the situation, normally present exactly six visible numbered options when six meaningful options exist:

**Immediate**

1. A concrete action Wei can take in the present scene.
2. A materially different immediate action.
3. A third materially different immediate action.

**Wider Horizon**

4. A broader objective, commitment, preparation, or plan appropriate to the scene.
5. A materially different broader objective, commitment, preparation, or plan.

6. **Free Action:** Wei may attempt any other action the player describes.

Keep Choice 6 available whenever a menu is shown.

## Define horizon relative to the scene

Do not interpret `wider horizon` as days or weeks in every scene.

- In combat, immediate choices concern the next exchange; wider choices concern tactical objectives such as controlling terrain, protecting someone, capturing a target, disengaging, escaping, isolating an opponent, or shaping the next several exchanges.
- In covert action, immediate choices concern observation, movement, contact, concealment, or evidence; wider choices concern surveillance posture, infiltration route, information objective, extraction, or mission approach.
- In social or political scenes, immediate choices concern what Wei says or does now only if the player has not already supplied dialogue; wider choices concern commitments, negotiation posture, delegation, policy, or relationships.
- In downtime, immediate choices often span minutes to hours; wider choices may span days, weeks, training cycles, projects, relationships, missions, or institutional priorities.
- In command scenes, immediate choices concern orders, meetings, inspections, or allocation now; wider choices concern doctrine, force posture, project priorities, delegation, recruitment, or campaign strategy.

## Adapt instead of filling

The six-option shape is a default, not a reason to invent nonsense.

If only three or four meaningful legal options exist, show those plus Free Action. If immediate danger makes medium or long planning irrelevant, show only crisis-relevant choices plus Free Action. If a scene is inherently strategic, the mix may contain more wider-horizon options and fewer immediate ones.

Never create filler merely to reach a number.

## Do not interrupt declared intent

Do not show a menu when the player already clearly declared what Wei does and that intent is still being resolved, a runtime write is already being resolved, one specific consequential clarification is required before the action can be represented, or an NPC response/causal result must still land before there is a real choice.

Resolve the player's declared intent rather than making them choose it again. Carry it through obvious non-decision logistics such as departure, lawful routine travel, arrival, and reporting when those steps are implied and no material tradeoff appears.

If a new consequential choice arises during that sequence, stop at the new decision and present choices there.

## Never strand an unresolved decision

Before ending an IC response, check fresh context and the narrated endpoint.

If the runtime says `decision_required` or the prose has landed on a genuine unresolved player-facing choice, and the current player message has not already answered that next choice, provide decision scaffolding before ending.

Do not end with only:

- `Your next movement belongs to you.`
- `What happens next is your choice.`
- a generic question with no choices when the scene supports useful options;
- an abstract statement that the runtime is waiting for input.

If six meaningful choices exist, show six. If fewer exist, show the meaningful set plus Free Action. The player may always ignore the menu and act naturally.

## Ground every suggestion

Build every option from fresh player-visible runtime context.

Check current authority, knowledge, location, health, equipment, resources, obligations, deadlines, relationships, institutional roles, tactical geometry, travel/timing mechanics, and supported current semantic commands when the option implies persistence.

Do not leak hidden opposition, secret opportunities, unobserved evidence, unknown techniques, or repository-only facts through option wording.

Do not promise success. Describe the action or commitment, not the hidden outcome.

Do not mark a preferred, recommended, safest, optimal, or best choice unless the player explicitly asks for advice. Even then, distinguish advice from guaranteed mechanics.

## Distinguish world availability from implementation blockage

Choice integrity and defect visibility are separate requirements.

Before presenting an option that implies persistence, classify it from player-visible evidence and the current runtime contract:

1. **Executable:** the action is lawful/plausible in-world and the current interface exposes enough information to construct it. Present it normally.
2. **Unavailable in-world:** authority, location, resources, timing, health, equipment, relationships, or other established facts make the action unavailable. Omit it unless the unavailability itself is important to the player's decision.
3. **Not player-known:** presenting it would leak information Wei does not possess. Omit it.
4. **Plausible but implementation-blocked:** the action is lawful and fictionally available, but the GM cannot construct it solely because a runtime capability, legal value, reference, discriminator, discovery path, or other interface contract is missing or defective.

Never present category 4 as an immediately executable numbered option. Equally, never silently prune it from the player's apparent possibility space merely because the implementation is incomplete.

When a category-4 action is materially relevant, surface a compact OOC QA note that names the blocked action and the missing interface/capability, preserve any already-declared player intent, and classify the issue for development. If the player has already selected that action, stop at the blocker instead of substituting a different action or menu.

Before declaring an action implementation-blocked, perform the lawful bounded reads that could expose the needed IDs, authority, variants, or command shape. Failure to inspect discoverable state is a GM operating error, not a runtime defect.

Do not let capability validation become defect hiding. A shrinking choice set caused by runtime gaps is itself quality evidence and should be reported when it changes meaningful player agency.

## Show time when useful

Give an estimated in-world duration or narrow justified range for suggested actions when duration materially affects the decision and the runtime context supports an estimate.

Examples:

- `Run a first doctrine drill [about 90 minutes]`
- `Review the mission packet with Hayama [20-30 minutes]`
- `Begin a formal team training block [several days, with intervening obligations]`

Do not fabricate precise duration when the runtime does not support it. If a choice crosses a known appointment, deadline, travel arrival, recovery boundary, or other hard interrupt, say so in player-visible terms.

## Keep options concise

Use one strong action line and, when useful, one short consequence/tradeoff sentence. Do not write six mini-essays.

Options must differ in actual approach, commitment, risk, information gained, social posture, resource use, or time horizon. Cosmetic rewording does not count as variety.

## Format

Prefer:

**Immediate**

1. **Action name** `[estimated time]` - concise description.
2. **Action name** `[estimated time]` - concise description.
3. **Action name** `[estimated time]` - concise description.

**Wider Horizon**

4. **Plan or objective** `[estimated horizon]` - concise description.
5. **Plan or objective** `[estimated horizon]` - concise description.

6. **Free Action** - Do anything else Wei chooses.

Do not expose runtime command names in normal play.
