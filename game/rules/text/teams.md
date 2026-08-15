# Exact Teams and Assignment

A team exists only when persistent exact-team state records its roster and lawful activation. Graduation, promotion, recommendation, compatible personalities, or a proposed roster do not create an active team by themselves.

An exact team owns exact member references, leader and deputy, member roles, team type, parent institution when applicable, assignment authority, classification, doctrine reference, training model, instructors, facilities, and current missions and commitments through their domain owners.

## Team types

Reusable legal shapes live in `game/data/team/team-types.json`. Team creation and reorganization must obey the selected type's roster constraints and authority requirements.

A special mission cell or temporary task force may use a flexible named roster when lawful authority and member obligations permit it. ANBU authority, personal-force authority, or another restricted status must already exist before a team type that requires it may activate.

## Training and instructors

Every exact team uses the generic team training mechanics. Differences come from people, doctrine, curriculum, instructors, facilities, intensity, equipment, mission history, health, and available time.

A configured instructor may be a team member or an outside specialist. The instructor must be a real available person, be lawfully permitted to teach the session, and be present where the training occurs. Instructor status is not created by adding a name to a doctrine record.

## Teams in large formations

Exact teams remain separate social owners even when operationally attached to a formation. Their members are a subset of the formation headcount rather than extra personnel.

Ordinary small squads inside a mass formation do not require exact-team state. Their effect is represented by formation composition, doctrine, training, cohesion, command, and capability distributions.

A named team wakes into exact resolution only when its individual members can materially affect the outcome or suffer individually important consequences.

## Operational assignment

An exact team may hold one `current_assignment_ref` to an operational formation. The assignment is an overlay on the exact roster and never adds personnel to the formation. Assigning or unassigning a team does not create, delete, or duplicate members. Formation assignment requires lawful team-side authority and lawful force-side authority.

The team owner is the authority for exact membership and current operational assignment. While assigned, the team also records which exact members are presently embedded in the formation headcount. A formation does not store a second copy of the team roster.

An embedded member who is evacuated, captured, killed, or otherwise leaves operational formation accounting is removed from that exact-identity overlay and from the formation headcount once. Recovery restores the member to the formation only when the team is still assigned there, the person is a current team member, the person and formation are co-located, and authorized formation capacity remains. Otherwise recovery returns the person to an appropriate ready force partition without inventing a deployment.

## Shared training sessions

A team training plan only configures doctrine, instructors, facilities, and focus. Mechanical development occurs only through resolved training time.

A shared team training session advances time once for all attendees, requires every selected member and the instructor to be available and co-located, enforces any configured facility restriction, and applies each member's own aptitude, health, recovery, target value, and development residual. The generic team training mechanics own the rolling weekly-hour limit and minimum recovery interval. Doctrine state does not duplicate those limits.

The team keeps only a bounded recent-session ledger needed to enforce schedule and recovery. Long-form history belongs to semantic events. Shared attended training may increase doctrine familiarity, but it never directly grants techniques, equipment, rank, authority, or bonus personnel.
