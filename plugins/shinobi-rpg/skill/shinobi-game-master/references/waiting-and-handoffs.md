# Waiting and Causal Handoffs

Use this reference when Tang Wei is waiting for an external reply, summons, courier, House disposition, mission report, delayed information, recovery boundary, or other future dependency, especially after completing a local objective away from his ordinary base.

## Waiting is an interrupt condition

Treat `wait for X` primarily as permission to let **X interrupt ordinary life**, not as an automatic instruction to remain physically motionless at the current location.

Separate two questions:
1. what future event or condition ends the waiting policy;
2. where and how Wei spends the interval.

If the player explicitly says `hold here`, `remain at the inn`, `stay at the House`, or equivalent, preserve that posture. If the player says only `wait for the House answer`, `await the courier`, or equivalent, do not infer either `stay here` or `go home`.

Travel destination remains protected player agency. When the current place is clearly temporary and no saved plan already determines Wei's next location, hand back that one meaningful choice before advancing substantial time. Ground options in known destinations and current facts; do not invent a destination merely to fill a menu.

## Waiting should coexist with ordinary life

Once interval location and routine are chosen or already established, let the waiting policy run over ordinary life. Preserve saved training routines, House duties, rest, meals, equipment care, medicine, administration, or already-declared travel when the runtime supports them. The awaited dependency becomes the interrupt condition.

Use causal chronology until one of these occurs:
- the named response/report arrives;
- a known deadline or clock boundary matters;
- a high-salience wake interrupts;
- fatigue, supply, access, safety, money, injury, or another condition creates a new tradeoff;
- the chosen waiting policy reaches its endpoint.

Do not manufacture a menu every day merely because time passed. Do not silently add unrelated commitments.

The runtime may return `continuation_required` with
`continuation_reason: quiet_frontier_chunk` before the player's requested date.
That is a bounded transaction-work boundary, not a new gameplay decision and
not evidence that the standing wait ended. Refresh context, preserve the same
semantic target and wait policy, generate a fresh request ID at the new
revision, and continue automatically. Do not show a menu or ask the player to
authorize the same wait again. Stop auto-carry only when the requested target
is reached or a real semantic/hard player-facing boundary appears.

## Actionability-filtered standing waits

When the player says `until I can act`, `until something meaningful happens`, `until there is a real decision`, or equivalent, do not stop the wait for every background event.

Treat a delivered event as **interim** and carry the same policy forward when it establishes only background movement, broad activity, confirmation of known facts, or incomplete information that gives Wei no materially distinct action, commitment, authority choice, resource tradeoff, or meaningful information-gathering choice.

Treat it as a **terminal handoff** when player-visible facts establish a concrete new course, protected decision, hard authority/resource/safety boundary, meaningful report, mission disposition, or materially different information-gathering opportunity. If the player's current message has not already supplied the response, load `choices.md`, narrate the decision-relevant facts first, and provide grounded choice scaffolding before ending.

A true hard wake always interrupts when the runtime says settlement cannot lawfully continue without Wei's immediate response.

## Choice wording must include physical implications

A menu option authorizes only what it actually says. Never write `withdraw and wait for orders` and later treat that as authorization to remain in town, return to Tang Manor, relocate a team, or begin a new routine.

If relocation is part of the intended action, say so before the player selects it. If relocation is intentionally left open, resolve the withdrawal first and then scaffold the destination decision.

## Contact before petition

Treat access-seeking and substantive business as separate causal stages.

Trying to find the proper receiving person, office, elder, manager, physician, official, or House authority does not mean the actual request has already been delivered. Do not narrate an institution as considering a proposal before a lawful receiving handoff exists.

When the runtime establishes the receiver/audience, stage that meeting first. The subsequent petition, request, report, negotiation, or offer is a distinct player action unless the player already explicitly delegated that exact immediate communication.

A hearing is not acceptance. A request is not a decision. Waiting for access is not the same as waiting for the eventual substantive answer.

## Mission and delegation handoffs

When Wei dispatches a House team under a named commander, his declared mission policy remains standing while they are away. Do not expose hidden progress omnisciently and do not ask Wei to reauthorize routine offscreen execution. Interrupt only when a lawful report, messenger, public consequence, mission decision, casualty/reinforcement request, or other player-facing boundary reaches him.

When a mission closes and Wei can lawfully receive the result, surface the compact AAR, settle the surrounding House/service handoff, then continue the larger process or present the next genuine decision.

## Bare continue while waiting

If the player's existing standing intent is clearly to keep waiting, bare `continue` may carry that wait to the next lawful boundary. If no such standing intent exists, bare `continue` does not invent one.
## Semantic stop criteria

When the player names several distinct reasons that would end a wait, treat those reasons as alternatives unless the player explicitly says all must be true. For example, `wait until entry authority changes, material military intelligence arrives, or hostile contact occurs` should wake on the first matching reason. Encode each precise reason as one conjunctive semantic clause and place distinct alternative reasons in `wait_policy.any_of`; values inside one criterion field are alternatives. Do not flatten a precise source+topic condition into a broad OR, and do not fall back to `stop on any notice`. Unrelated reports may be recorded without breaking the standing wait.

