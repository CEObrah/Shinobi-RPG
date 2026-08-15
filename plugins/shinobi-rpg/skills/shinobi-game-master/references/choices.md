# Player Choice Presentation

## Contents

1. Default decision shape
2. Define horizon relative to the scene
3. Adapt instead of filling
4. Do not interrupt declared intent
5. Never strand an unresolved decision
6. Meaningful event handoffs
7. Arrival and pending-response handoffs
8. Ground every suggestion
9. Preserve premise parity
10. Render selected choices in-world
11. Bounded judgment delegation
12. Do not re-offer completed setup
13. Keep implementation state out of IC choices
14. Distinguish world availability from implementation blockage
15. Show time when useful
16. Keep options concise
17. Format

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

If the runtime says `decision_required`, the prose has landed on a genuine unresolved player-facing choice, or a meaningful player-facing event has been fully presented and leaves multiple materially distinct lawful responses, and the current player message has not already answered that next choice, provide decision scaffolding before ending.

Do not end with only:

- `Your next movement belongs to you.`
- `What happens next is your choice.`
- a generic question with no choices when the scene supports useful options;
- an abstract statement that the runtime is waiting for input.

If six meaningful choices exist, show six. If fewer exist, show the meaningful set plus Free Action. The player may always ignore the menu and act naturally.

## Meaningful event handoffs

`scene.decision_required` is authoritative when it names an explicit protected decision, but a null value does not mean that no useful player-facing fork exists. Reports, briefings, interruptions, reveals, completed arrivals, newly delivered information, institutional responses, and other meaningful events may create a genuine decision surface through their content.

Present the event first. Then judge the endpoint from the facts Wei actually has. If two or more materially distinct lawful next actions exist, normally provide grounded choices even when `decision_required` is null. Do not wait for a backend flag to state what the fiction has already made clear.

A delivered report is not itself a menu. Read or stage the player-readable report content before offering responses. Useful responses may include acting on the report, seeking clarification, involving relevant teammates or superiors, preserving the current plan, or deliberately deferring action when those are genuinely distinct and lawful. Do not create response options from facts the report did not disclose.

If the player already declared the immediate handling of the event, such as `open the seal`, `read it`, `answer him`, or `go inside`, carry that action through first. Only show a new menu after the declared action has resolved and a new fork actually exists.

If the event has only one obvious procedural continuation, continue it instead of manufacturing a menu. If the event merely reports something with no current decision, a concise lived beat may be enough.

## Arrival and pending-response handoffs

A completed movement, message, request, or wait command can end one mechanical action without ending the player's causal purpose. After the commit, refresh context and judge the actual endpoint.

For arrival, carry the declared purpose through obvious non-decision logistics supported by current authority. If Wei came to report, seek an office, meet someone, or begin an already-selected task, do not end with a cinematic arrival line when the next real beat is either a lawful automatic handoff or a material decision. Never invent current access, occupancy, or a waiting NPC merely to make the handoff smoother.

For a pending response, preserve the player's standing wait when one was declared. Do not offer `keep waiting` as a repetitive new choice after every quiet chunk. Stop when a response, deadline, resource problem, interruption, or other genuine decision appears.

If current scene projection is stale after a committed change, ground options in current exact/player-visible facts plus the committed endpoint. A previously player-known purpose may explain why Wei is there, but stale prose cannot establish present people, access, pressure, or opportunities. Phrase uncertain access as an attempt Wei can make rather than a success already granted.

## Ground every suggestion

Build every option from fresh player-visible runtime context.

Check current authority, knowledge, location, health, equipment, resources, obligations, deadlines, relationships, institutional roles, tactical geometry, travel/timing mechanics, and supported current semantic commands when the option implies persistence.

For any option that sends, calls, reports to, summons, or otherwise contacts someone who is not established as immediately present, prove the communication affordance before presenting it as executable. Use a fresh player-visible/runtime-supported channel, current co-location, registered messenger/process, or a stable site capability whose use is lawful from the current location. Do not silently assume remote communication, instant institutional access, or message delivery. If the in-world contact is plausible but the current interface cannot construct the channel, classify it as implementation-blocked rather than inventing travel or a reply.

Do not leak hidden opposition, secret opportunities, unobserved evidence, unknown techniques, repository-only facts, developer facts, or implementation status through option wording.

Do not promise success. Describe the action or commitment, not the hidden outcome.

Do not mark a preferred, recommended, safest, optimal, or best choice unless the player explicitly asks for advice. Even then, distinguish advice from guaranteed mechanics.

## Preserve premise parity

Every material fact needed to understand a numbered option must already be established in the preceding prose or fresh player-visible context. An option may not suddenly introduce an unseen bridge, flank, witness, enemy, legal restriction, item, route, resource, terrain feature, opportunity, or relationship fact merely to make the menu more interesting. The option may propose what Wei attempts to do with established circumstances; it may not smuggle new circumstances into existence.

## Render selected choices in-world

When the player answers with a number, short label, or other unambiguous menu selection, translate that selection into Wei's explicit in-world action before or as it is resolved. Do not treat `1`, `2`, or `5` as an invisible interface click. Show the movement, order, request, or spoken words implied by the selected option to the degree the player actually delegated them, while preserving all normal agency boundaries and avoiding invented extra commitments.

## Bounded judgment delegation

When the player explicitly says to use Wei's judgment, knowledge, or stats to answer for them, treat that as permission for **one bounded answer or order only**. Retrieve the relevant current sheet/knowledge when it can materially affect the formulation, choose within what Wei can lawfully know and reasonably judge, and render what Wei actually says or orders. This permission does not become a standing delegation for later dialogue, promises, strategy, spending, consent, mercy, allegiance, or other protected choices unless the player separately persists such authority through the game.

## Do not re-offer completed setup

Treat persisted plans, doctrines, training models, standing orders, rosters, facilities, instructors, equipment standards, schedules, assignments, and other durable arrangements as already established until authoritative state shows that they changed, expired, failed, or need revision.

Before offering a planning, preparation, setup, review, doctrine, schedule, or configuration choice, inspect the relevant current owner when the state is discoverable. Ask what would actually change.

If the proposed option would merely recreate or restate existing state, do not offer it. In particular, do not repeatedly offer choices such as `set up the training block`, `prepare the team training plan`, `establish the doctrine`, `arrange instructors`, or `configure the facility` when those facts are already persisted.

For an established standing system, prefer choices that advance play:

- execute the existing plan;
- inspect a new problem or changed condition;
- respond to a report, mission, injury, absence, conflict, deadline, or other new pressure;
- deliberately revise an existing plan when the player has a reason to change it;
- delegate, suspend, resume, or redirect established activity when those are genuine decisions.

An unresolved hook such as `training cycle`, `readiness cycle`, or `team development` does not mean setup is unfinished. It may simply mean an already-established process is ongoing. Do not convert an ongoing process into repetitive administrative choices.

When the player explicitly asks to time-skip until something significant happens, do not interrupt the skip for routine execution of already-established training, readiness, administration, or other standing processes unless the runtime produces a real player-facing decision, material consequence, hard boundary, or meaningful report.

## Keep implementation state out of IC choices

Numbered choices are part of the fiction-facing handoff. Write them as actions or objectives Wei could understand from inside the world.

Never put any of the following inside an IC option label or description:

- runtime, command, schema, tool, API, code, GitHub, deployment, migration, bug, fix, repair, implementation, unsupported, or similar engineering language;
- phrases such as `until the system is repaired`, `because the runtime cannot`, `work around the missing command`, or any other explanation whose only meaning exists outside the setting;
- a developer workaround presented as if it were Wei's natural motive.

Do not make Wei perform extra in-world labor merely because the current implementation lacks a cleaner capability. For example, do not turn a lawful standing order for subordinates to assemble into `personally escort them because autonomous movement is not implemented`. That changes the fiction to accommodate software.

If an implementation defect materially removes a fictionally valid option, keep the IC scene and any remaining IC options clean. Put the QA explanation in a separate OOC note outside the numbered choice list. If the player already selected the blocked action, preserve that declared intent and stop at the narrowest honest boundary instead of substituting a different in-world action.

## Distinguish world availability from implementation blockage

Choice integrity and defect visibility are separate requirements.

Before presenting an option that implies persistence, classify it from player-visible evidence and the current runtime contract:

1. **Executable:** the action is lawful/plausible in-world and the current interface exposes enough information to construct it. Present it normally.
2. **Unavailable in-world:** authority, location, resources, timing, health, equipment, relationships, or other established facts make the action unavailable. Omit it unless the unavailability itself is important to the player's decision.
3. **Not player-known:** presenting it would leak information Wei does not possess. Omit it.
4. **Plausible but implementation-blocked:** the action is lawful and fictionally available, but the GM cannot construct it solely because a runtime capability, legal value, reference, discriminator, discovery path, or other interface contract is missing or defective.

Never present category 4 as an executable numbered option and never convert it into an executable-but-fictionally-worse workaround. Equally, never silently reinterpret the fiction so the missing capability appears unnecessary.

When a category-4 action is materially relevant, preserve the in-world possibility and surface a compact, clearly separated OOC QA note after the IC handoff or at the point where the selected action becomes blocked. The OOC note may name the missing capability and development owner. The IC prose and choice text must remain diegetic.

Before declaring an action implementation-blocked, perform the lawful bounded reads that could expose the needed IDs, authority, variants, or command shape. Failure to inspect discoverable state is a GM operating error, not a runtime defect.

Do not let capability validation become defect hiding. A shrinking choice set caused by runtime gaps is itself quality evidence and should be reported when it changes meaningful player agency.

## Show time when useful

Give an estimated in-world duration or narrow justified range for suggested actions when duration materially affects the decision and the runtime context supports an estimate.

Examples:

- `Run a first doctrine drill [about 90 minutes]`
- `Review the mission packet with Hayama [20-30 minutes]`
- `Begin a formal team training block [several days, with intervening obligations]`

Do not fabricate precise duration when the runtime does not support an estimate. If a choice crosses a known appointment, deadline, travel arrival, recovery boundary, or other hard interrupt, say so in player-visible terms.

## Keep options concise

Use one strong action line and, when useful, one short consequence/tradeoff sentence. Do not write six mini-essays.

Options must differ in actual approach, commitment, risk, information gained, social posture, resource use, or time horizon. Cosmetic rewording does not count as variety.

Do not explain why an option exists from the GM, design, or implementation perspective. The option should read naturally if copied out of the game without any OOC context.

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

Do not expose runtime command names or any other implementation language in normal play.
