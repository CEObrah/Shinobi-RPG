# Choices

Choices are decision scaffolding, not the game. Narrate the lived scene first. Present options only after a genuine unresolved player-facing decision has landed.

## Hard output gate: direct NPC questions

A direct NPC question can itself create a player-facing decision even when the runtime does not mark an unresolved decision. If an NPC asks Wei for an answer that would determine or materially condition his voluntary dialogue, oath, allegiance, service, rank tolerance, House duty, mission acceptance, command, spending, promise, surrender, mercy, strategy, relationship, family decision, treatment, or another protected commitment, treat the question as unresolved unless the player's **current** message already supplied the answer.

At that point:
- do not end the turn on the NPC question alone;
- do not rely on a null/empty runtime decision field as permission to omit scaffolding;
- immediately follow the question with grounded choices;
- for a genuinely binary or conditional question, two or three meaningful branches plus **Free Action** are enough;
- include useful conditional answers when the scene supports them rather than forcing artificial yes/no phrasing.

A routine reversible clarification that does not require a protected commitment does not trigger this guard.

**Final-line audit before sending IC:** inspect the narrated endpoint, not only runtime fields. If the final beat contains a direct consequential question to Wei and the player has not already answered it, a choice block must follow before the response ends.

## Default structure

When the scene supports it, present six visible choices:

**Immediate**
1. A materially distinct action Wei can take now.
2. A second immediate approach with a different objective, commitment, risk, or information value.
3. A third immediate approach that is genuinely available and different.

**Wider Horizon**
4. A plan or objective that shapes the next phase rather than only the next beat.
5. A second wider-horizon direction with a materially different tradeoff.

6. **Free Action**: Any other natural-language action.

The player is never restricted to these suggestions.

## Horizon is scene-relative

`Immediate` means the current causal beat, not necessarily a few seconds. `Wider Horizon` means beyond the current beat, not necessarily weeks.

Examples:
- duel or melee: attack, reposition, protect, disengage; wider horizon capture, escape, preserve an ally, control terrain;
- team fight: pressure one threat, screen an ally, hold a lane, withdraw; wider horizon change team doctrine, split the group, seek reinforcements;
- House council or mission: answer the current proposal, ask for clarification, nominate a commander; wider horizon alter the operational plan, request resources, decline or negotiate terms;
- investigation: question a witness, inspect evidence, follow one lead; wider horizon change investigative strategy, recruit help, suspend the inquiry;
- travel: depart now, delay, choose a known route; wider horizon change destination sequence, escort posture, or travel objective;
- training: choose the current focus; wider horizon establish a training block, doctrine goal, instructor plan, or integration objective;
- negotiation: answer the current term, counter, ask for proof, defer; wider horizon change the relationship, alliance posture, restitution terms, or mission objective.

## Adapt instead of filling slots

Do not manufacture six options when the scene cannot support six good ones. Urgent danger may support only immediate choices plus Free Action. A planning council may support two immediate and three strategic options. A binary legal or social decision may genuinely have only two lawful branches.

Quality outranks count. Never create filler merely to satisfy the format.

## Establish every premise before the menu

The menu may summarize or act on facts, but it must not be the first place a material scene fact appears. Before presenting choices, ensure the preceding IC prose has already made visible every terrain feature, enemy contact, route condition, deadline, resource, authority limit, injury, social fact, known clue, or other premise needed to understand the options.

A choice may add **an action**, not retroactively add **the situation that makes the action sensible**.

Before sending a menu, perform a parity check: a player reading only the IC prose above the menu should understand why every option is available and what known fact it responds to. If an option depends on a premise that appears only inside the option, move that premise into the scene or remove the option.

## Selected choices become visible player actions

Treat a reply such as `1`, `option 2`, the option title, or pasted option text as the player's declaration of that offered choice. Do not ask the player to restate it and do not treat the menu as an invisible control panel.

Translate the selected option into the fiction and show it. Render the concrete action, orders, or faithful natural dialogue before narrating any response. Preserve the option's objective, scope, risk, and limits; expand only details directly implied by the option and do not create a new protected commitment.

If the selected choice is consequential, persist that player-authored intent through the lawful runtime path first, refresh context, then render the committed action and result. If that resolution produces a new consequential fork, stop there and scaffold the new decision normally.

## Never strand an unresolved decision

Before ending an IC response, check fresh runtime context and the narrated endpoint. If the runtime says a decision is required, or the prose has landed on a genuine unresolved player-facing choice, and the current player message has not already supplied that next action, provide decision scaffolding before ending.

Do not end with only `What do you do?`, `The choice is yours`, `Your next move is up to you`, or an abstract statement that the runtime is waiting for input when grounded options exist. If six meaningful choices exist, show six. If fewer exist, show the meaningful set plus **Free Action**.

## Narrated-fork guard

The GM's own prose can create a decision handoff even when the runtime decision field is empty. If the narration says or clearly implies that something is now worth deciding, asks what Wei will do, contrasts materially different courses, or otherwise frames the next beat as Wei's choice, treat that as a genuine player-facing fork.

At such a fork, do exactly one of these:
- if the player's current message already supplied the next action, resolve that declared action and do not insert a menu first;
- otherwise provide grounded choice scaffolding from facts already established in the scene.

Never manufacture decision language merely for drama and then stop without options. If no genuine decision exists, continue the obvious causal or procedural handoff instead.

## Completed-objective handoffs

A local success is often a transition, not an endpoint. Finishing one duel, interview question, council agenda item, training session, journey leg, investigation step, escort leg, mission phase, or administrative task does not by itself mean the surrounding process is over.

Before ending after a completed local objective, classify the next handoff:
- **New consequential decision:** narrate the completed result, establish every premise, then scaffold the new decision.
- **Obvious procedural continuation:** carry it forward without a menu when no new commitment is required.
- **Waiting on an external response:** if the player already chose to wait or continue under a standing policy, use lawful chronology until a response, wake, hard boundary, or new tradeoff appears.
- **Larger declared objective still active:** continue toward that objective through obvious reversible logistics until a real new fork appears.

Do not stop merely because one command or subsystem completed.

## Standing policies should stay standing

A choice may establish a policy such as `keep fighting under my doctrine`, `continue the escort unless the route becomes unsafe`, `wait until the House replies`, `train this routine for three days unless interrupted`, or `stay on this route until new information arrives`.

When the player selects such a choice, do not treat it as a one-beat pose that immediately requires another menu. Preserve the declared policy until:
- the named response or condition arrives;
- a known deadline or boundary matters;
- a high-salience wake or interruption occurs;
- fatigue, resources, access, safety, or another condition creates a new tradeoff;
- the policy reaches its natural endpoint.

Do not silently add unrelated commitments during the interval.

## Do not re-offer completed setup

Treat persisted doctrine, rosters, standing orders, schedules, instructors, equipment standards, mission plans, assignments, and other durable arrangements as established until authoritative state says they changed, expired, failed, or need revision.

Do not repeatedly offer `set up the training`, `establish the doctrine`, `prepare the roster`, `choose the same escort plan`, or equivalent administration when those facts already persist. Prefer execution, inspection of a new problem, deliberate revision, delegation, suspension, resumption, or redirection.

## Keep implementation state out of IC choices

Numbered choices are fiction-facing. Never put runtime, command, schema, API, code, deployment, migration, bug, fix, unsupported-action, or similar engineering language inside an IC option.

If a fictionally valid action is implementation-blocked, keep any remaining IC choices clean and place the narrow diagnosis in a separate OOC note. Never make Wei choose a worse in-world action merely to accommodate an implementation limitation.

## Ground every choice

Every suggested action must come from fresh player-visible context. Do not:
- reveal hidden enemy plans;
- imply access Wei does not possess;
- invent money, personnel, equipment, poison, medicine, authority, relationships, routes, intelligence, vacancies, or opportunities;
- imply guaranteed outcomes;
- mark a secret best choice;
- convert model historical or genre knowledge into a player option unless Wei knows it.

Choices may express uncertainty honestly. `Send someone to scout the eastern road` can be valid when the road is uncertain. `Ambush the hidden men on the eastern road` is not valid unless Wei knows they are there.

## Make options materially different

Avoid several phrasings of the same action. Good alternatives differ in one or more of objective, commitment, risk, authority used, information gained, resource exposure, political/social cost, time horizon, reversibility, or combat posture.

Describe what Wei does, not what hidden outcome the simulation will award.

## Time estimates

When fresh runtime information supports it, attach an estimated in-world duration or narrow range to choices whose time matters, such as `~20 minutes`, `1 to 2 hours`, `most of the day`, or `several days of travel`.

Do not invent precision the runtime does not support.

## Strategic choices do not skip simulation

A wider-horizon option represents a plan, commitment, delegation, or objective. It does not silently complete days of persistent work. Translate the chosen plan into supported semantic actions, resolve them sequentially, refresh context after writes, and stop when a new interruption or player decision appears.

## No menu before a declared action

If the player already supplied a clear action, resolve it. Do not answer with a list of alternatives before attempting the action. Carry that intent through obvious non-decision logistics such as preparation, departure, routine lawful travel, arrival, reporting, or taking an already-selected position when those steps are implied and no material tradeoff appears.

If a new consequential choice arises during that sequence, stop at that new decision and scaffold it.

## Choice language

Write choices in plain player-facing terms. Keep command names, object IDs, payload fields, revisions, validators, and backend terminology out of the menu.
