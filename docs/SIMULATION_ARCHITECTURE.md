# Shinobi simulation architecture

This document describes the current simulation model, the public vocabulary, the mass-war representation rule, location functionality, state-authority rules, context-efficiency rules, and feature maturity. Gameplay rules themselves remain in `game/rules/` and state current behavior only.

## Public organization vocabulary

### Person

One exact persistent individual. Exact person state is used when identity can affect causality: relationships, injuries, offices, command, unique techniques/assets, commitments, missions, or other individual consequences.

### Team

A small exact roster of named people who operate as a socially or tactically coherent group. Membership is exact. A team may own leadership, roles, doctrine, training configuration, assignments, readiness, and commitments.

A team is not a manpower pool and is not a second copy of its members.

### Force

The aggregate manpower authority for a military or security organization. A force owns conserved personnel totals, availability partitions, troop pools, and institutional capability baselines.

### Formation

An aggregate operational body represented explicitly because it matters to current operations, deployment, or combat. A formation belongs to one force and represents personnel already contained in that force's deployed manpower.

A formation can mobilize, drill, reconstitute, split, merge, fight, move, and release manpower under lawful authority.

## Engine-only vocabulary

### Cohort

A compressed population representation for similar unnamed or not-currently-exact people. Cohorts let routine people develop without one persistent person record and one scheduler wake per human.

### Host

A causal scheduler neighborhood that can be lazily settled. Hosts are implementation state, not player-facing organizations.

### Component

A bounded subgroup inside an aggregate formation used for composition and capability calculation. Components are not independent manpower owners.

## The 10,000 versus 10,000 village-war rule

Suppose Konoha commits 10,000 shinobi against Suna.

The engine does **not** create 2,500 four-person team owners for Konoha and another 2,500 for Suna.

A useful representation is:

```text
force.konoha.shinobi
    deployed manpower includes the 10,000-person theater commitment

war theater
    formation.konoha.front.01     1,000
    formation.konoha.front.02       750
    formation.konoha.front.03     1,250
    formation.konoha.front.04       500
    ...
    represented theater total    10,000
```

The exact number and size of formations is chosen for useful simulation resolution. Formations do not need to correspond to a rigid real-world echelon.

### Named teams inside the war

If Team Guy has four exact members and its team owner records `current_assignment_ref` to a 700-person formation:

```text
formation personnel_total = 700
exact Team Guy subset      =   4
remaining aggregate people = 696
```

The formation remains 700, not 704. The team members identify four people already included in the formation headcount.

Ordinary squads among the remaining 696 people are represented statistically through formation components, doctrine, training, readiness, leadership quality, and current objective/context. They do not require persistent team owners.

If an ordinary squad becomes causally important, its members can be materialized or a new exact team can be formed from already represented manpower without creating additional humans.

### What exact teams contribute in mass combat

Named teams matter only where their exact identities can change a local outcome. Examples include:

- a named sensor team detecting an ambush;
- Team Guy holding a breach;
- a medical team preserving a commander;
- an ANBU cell infiltrating behind the line;
- a named team being separated for a mission;
- an exact person's unique technique changing a sector result.

Mass combat otherwise remains formation-level. This lets the simulation zoom from a four-person mission to a 20,000-person battle without changing the conservation model.

## Manpower invariants

1. A force owns aggregate manpower.
2. Every explicitly represented formation person is included in that force's `deployed` partition.
3. The sum of explicit formation personnel may be lower than deployed personnel because not every deployment must be materialized.
4. The sum of explicit formation personnel may never exceed deployed personnel.
5. A fully materialized war theater may have explicit formations whose theater headcount equals the committed theater manpower.
6. Exact named people or teams embedded in a formation are subsets of formation headcount, never additions to it.
7. Split, merge, mobilization, reinforcement, release, death, capture, recovery, and demobilization must conserve the relevant people across their authoritative partitions.

## One fact, one authority

The simulation follows a strict authority rule.

| Fact | Authority |
|---|---|
| Total Konoha military manpower | force owner |
| Explicit formation headcount | formation registry |
| Exact team membership | exact-team owner |
| Person identity/injury/technique divergence | person owner/components |
| Training law and numerical limits | mechanics data |
| Team doctrine preferences | team-doctrine owner |
| Strategic route connectivity | world route data |
| Current exact location | person/place state |
| Transaction revision/WAL status | runtime transaction metadata |

Templates define structure. They do not compete with mechanics for gameplay values. Derived combat kernels, indexes, projections, and caches are rebuildable and do not become a second source of truth.

## Determinism rules

Determinism is more than seeded random numbers.

- Explicit object targets are never silently replaced with a hash-selected different object.
- Canonical IDs identify one entity unambiguously.
- Machine logic uses typed fields rather than matching descriptive prose.
- Iteration order is stable before deterministic selection or allocation.
- Random draws use registered deterministic namespaces/seeds.
- Conserved resources use explicit before/after validation.
- A derived cache cannot override its source authorities.
- A feature counts as mechanically live only when state, command/input, reducer, validation, temporal integration where needed, and bounded readback all connect.

## Location functionality

A location does not need mutable mechanics merely because it exists in canon or narration.

### Ambient place

Cold world content used for description and scene grounding. It needs identity, name, parent/anchor, tags, and description. It does not receive mutable staff, finances, inventory, schedules, security, or scheduler activity unless gameplay requires them.

### Navigable place

A place that can be a canonical travel/scene destination. Local child locations resolve through a strategic route anchor. Strategic routes connect major anchors.

### Mechanical site

A place receives a mechanical module only when the module can affect an outcome. Examples:

- `training`: per-session capacity, quality, and supported training categories;
- `medical`: quality and procedure specialties that actual medical reducers enforce;
- `custody`: detention capacity and custody security used by detention/escape resolution.

There is no generic place-security/infiltration module. Ordinary inventory and commerce are not place modules. They live in the inventory/economy authorities and may reference a place when location matters.

A ramen shop can remain useful flavor without a persistent restaurant-management simulation.

## Hot-state and context-efficiency policy

The world may be large while each turn remains small.

- Do not materialize every civilian or ordinary shinobi.
- Do not create exact teams for routine squads in mass warfare.
- Do not schedule flavor locations or routine people individually.
- Load a known owner directly through indexes instead of scanning catalogs.
- Use typed bounded reads for requested people, teams, forces, formations, missions, places, family records, reputation profiles, contracts, commitments, conflicts/fronts, custody records, combat operations, institutional projects, prices/services, economy/inventory views, named assets/items, and player-visible relationships.
- Keep static canon/content cold until referenced.
- Keep cohorts and formations aggregate until exact identity changes an outcome.
- Persist mutable facts, not giant repeated derived capability matrices.
- Use one semantic group transaction for a group process rather than one LLM action per member whenever the mechanics permit it.
- Do not ask the caller to repeat facts the runtime can derive unambiguously. Public command variants expose only action-relevant required fields; optional context may be omitted rather than sent as meaningless null values.
- Bound hot operational histories whose old entries are no longer current authority. Active obligations remain exact; deep history remains available through world events and source-control history rather than growing every hot owner indefinitely.

## Current feature maturity

### Mechanically connected

- deterministic transaction, WAL, idempotency, readback, and Git durability;
- causal scheduling and bounded time advancement;
- mission creation, transition, objective evidence, and settlement;
- individual, cohort, and exact-team training and development;
- injury, recovery, medical stabilization/treatment/surgery, biological implants, and conserved ocular extraction/implantation;
- exact and aggregate combat with exact-person zoom that reconciles team embedding, formation headcount, force availability, and physical population;
- population transfer, recruitment, sparse materialization, death, capture, recovery, and live birth conservation;
- exact-team lifecycle, formation assignment, doctrine configuration, external instructors, and shared team-training sessions;
- relationships plus reputation-conditioned initial social baselines;
- information claims and delivery;
- commitments;
- named-asset transfer/custody, quantity inventory issue/return/consumption/refit, direct open-market retail, negotiated purchase-contract offer/accept/cancel, and conserved stock/currency purchasing;
- force command assignments;
- formation mobilization at force-owned anchors, route/time-based movement, drill, reconstitution, split, merge, and release;
- persistent conflicts/fronts with connected geography, formation assignment, route control/disruption, hostile-route movement friction, bounded supply pressure, fortification, winner-backed multi-place occupation, ceasefire/resumption, and conclusion;
- custody placement/authorized transfer/release/exchange/escape against real custody capacity/security and captured-personnel conservation;
- bounded exact-combat Eight Gates, jinchuriki transformation, puppet deployment, and summon activation/dismissal;
- career promotion/demotion/graduation/retirement and office appointment/removal/transfer;
- technique learning from initiation through practice and evaluation;
- family proposal, union/lifecycle, household/parentage state, and conserved live birth;
- institutional project authorization, resource payment, elapsed work, cancellation, completion, and facility capability changes;
- strategic-route and local-anchor travel;
- bounded player-safe reads for people, teams, forces, formations, missions, places, family, reputation, contracts, commitments, conflicts/fronts, custody, combat operations, institutional projects, prices/services, economy/inventory views, named assets/items, and relationships.

### Intentionally bounded rather than universally simulated

Not every domain is a global subsystem. Sparse mechanics are deliberate when richer state would not change a gameplay decision.

- Facility modules matter only to reducers that explicitly consume training, medical, or custody fields. Commerce and ordinary stock remain separate economy/inventory authorities. There is no universal facility booking, business, or per-room occupancy simulator.
- Ambient shops, inns, streets, restaurants, landmarks, and similar places remain cold unless persistent training, medical, custody, damage, or another gameplay consequence requires a real consumer.
- Troop-pool counts are capability/source envelopes, not a second disjoint personnel partition. Force `availability` is the physical conserved partition and must sum to force total.
- Deep site infiltration remains mission/exact-scene/combat gameplay. There is currently no generic site-security/infiltration module and no room-by-room stealth physics simulator. If infiltration becomes a repeated strategic mechanic, it should receive an explicit bounded site-operation authority rather than reusing custody security.
- Succession/inheritance resolves only through the family, asset, office, and authority owners actually implicated by an event. There is no universal property-economy ledger for every civilian household.
- Routine unnamed squads, civilians, workers, trainees, and flavor NPCs remain aggregate/cohort-backed until exact identity changes an outcome.
- Derived formation combat kernels and capability matrices are computed from force sources plus formation state rather than persisted as duplicate mutable authority.

A domain is described as mechanically live only when the relevant state, semantic command/input, reducer, validation, temporal integration where needed, event/history write, and bounded readback are connected.

## Rule-writing standard

Gameplay rule files state the current rule in present tense. They do not contain release notes, migration history, deprecated-system explanations, or gameplay-version labels.

Mutable gameplay trees do not carry semantic version identifiers. Runtime transaction revisions, Git commit hashes, WAL compatibility, and other software/transaction metadata remain internal where needed for safety.
