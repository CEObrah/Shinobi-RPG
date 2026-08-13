# Exact-team lifecycle and doctrine migration — 2026-08-11

## Scope

This maintenance migration adopts the exact-team lifecycle contract introduced by source commit `45fc1ccf2ec0f9ff1f29e541f93e91203f36fe13` and migrates the three active exact teams present at campaign revision 27.

Campaign time remains `SE-0061-02-09T22:18:21`. No world-time advance, character progression, resource gain, mission outcome, injury, relationship change, or reputation change is created by this migration.

## State migration

State commit `de993a57daec8056044845e60487a10acbacd71f`:

- marks Team Fujin, Black Hound, and Team Guy as standing teams with exclusive active-team membership;
- sets player-led Team Fujin and Black Hound to `authority_review` for roster replacement so autonomous simulation cannot replace Wei's teammates without a lawful player/authority decision;
- sets Team Guy to `authority_review` until Konoha's standing-team replacement authority is connected to a bounded lawful personnel pool;
- adds a compact Team Fujin combat doctrine derived from the player-authored battlefield-control doctrine, with no retroactive familiarity credit;
- compacts Black Hound's existing doctrine while preserving its original effective time and familiarity `1` for all six members;
- adds a compact Team Guy doctrine without inventing past training familiarity;
- registers the two newly created doctrine owners.

## Runtime behavior

New exact teams write lifecycle metadata automatically. Autonomous team creation cannot borrow members from another active exact team. Dynamic autonomous owners may maintain a bounded number of teams instead of being globally restricted to one. Autonomous teams created with `maintain_strength` may replace permanently lost members from the same bounded lawful personnel pool while preserving surviving members and team identity. Temporary injury, recovery, travel, or capture does not silently remove a standing member.

Standing teams persist across individual missions. `mission_bound` teams are dissolution-gated until their saved mission purpose is terminal. Explicit lawful authority may still reorganize or dissolve standing organizations through the normal lifecycle command.

## Known follow-up work

- Offscreen exact-team review currently increases doctrine familiarity but does not yet execute the same individual training/progression reducer used by player-resolved team training. A later integration should make autonomous training consume time/readiness and produce lawful individual development.
- Autonomous mission result claims already persist success/failure knowledge for participants, commander, and faction; reputation and relationship propagation should be extended from those events rather than inventing a second report system.
- Existing named standing teams use `authority_review` until their lawful replacement authority/pool is explicitly represented.
