# AI Judgment Boundary Closure - 2026-09-12

## Scope

This local r108 package repair closes residual paths where deterministic Python still selected consequential human/NPC choices after the initial AI-judgment repair. It does not mutate campaign state and does not claim cross-game closure.

## Root invariant

Python may compute feasibility, candidates, constraints, objective scoring evidence, and deterministic physical/economic consequences. Meaningful voluntary choices belong to the player or to GM judgment. A committed GM choice must be revalidated against current authoritative state before application.

## Repaired paths

- Public disclosure now stages `route_interception_policy` instead of directly converting deterministic interception scoring into a pursuit.
- Route interception judgment owns whether to attack, exact attacker composition/leader, and contact motive; committed choices are revalidated.
- Government warrant contact now stages `government_contact_response_policy` for surrender versus resistance rather than deriving the decision from force ratio.
- Immediate death cleanup in command and warfare paths now stages institutional office appointment judgments instead of dropping the new helper contract.
- Hostile strategic autonomy now includes an explicit stand-down option and exact GM-selected detachment, leader, and operation intent. The prior `len(active_strategic_operations(...))` type error was also repaired.
- Autonomous escort-contract acceptance now carries exact GM-selected participants, leader, and muster location instead of Python selecting the strongest party.
- Strategic raid aftermath now stages exact loot/captive/withdraw choices rather than selecting the highest-value asset or person automatically.
- Friendly aid judgments now include an exact transfer amount; the reducer revalidates the chosen amount against current affordability bounds.
- Generic NPC judgment validation now rejects player-authored participation hidden in semantic actor/participant fields while still allowing NPCs to lawfully target the player.

## Regression updates

Stale tests that encoded pre-repair automatic decisions were converted into boundary tests. New assertions cover no consequence before commitment, exact selected consequence after commitment, nested-player participation rejection, exact aid amounts, explicit strategic stand-down, exact strategic detachments, and succession judgment staging.

## Verification

- `python tools/quick_check.py` - PASS.
- `python -m compileall -q runtime/shinobi_runtime` - PASS.
- `tools/test_changed.py` selected 29 maintained regression files for the actual changed paths. The single wrapper invocation exceeded the execution window, so the exact selected set was run in maintained shards/direct pytest runs: 380 tests passed, 0 failed.
- Targeted route-interception judgment/objective regressions also passed during development.
- Mutable `state/` remained byte-identical to the uploaded r108 baseline.
- Static `game/` data and packaged Skill/plugin source remained byte-identical to the baseline.

## Delivery boundary

This work repairs only the supplied Shinobi repository package. It does not change GitHub, CI, Railway, the connected MCP runtime, the installed ChatGPT Skill, or live campaign state. The analogous subsystem in the other RPG was not audited in this local-package task, so no global/cross-game closure is claimed.
