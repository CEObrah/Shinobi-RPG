# Training and Development Balance

## Design goals

Training must support a professional shinobi life without turning calendar time into automatic universal mastery. A shinobi may train on consecutive days and may spend a full professional week on drills, conditioning, sparring, study, and technical practice. The limiter on long-horizon stat inflation is mastery cost, not an arbitrary multi-day cooldown.

The stat scale is open-ended. `200` is the ordinary reference maximum, not a hard cap. Reaching that band should therefore be possible for exceptional specialists while remaining rare in the population.

## Team schedule

Exact-team training uses a rolling seven-day ledger only to prevent impossible team-session overbooking. The limit is 48 active team-training hours per member in a rolling seven-day window. There is no mandatory cooldown between healthy team sessions. Injury, health, recovery, availability, location, instructor access, facilities, missions, and other causal state remain the real readiness constraints.

This 48-hour value is not a statement that shinobi work only 48 hours. It is the maximum active time that may be recorded as organized team training in that window. Missions, travel, guard duty, administration, individual practice, recovery, and ordinary life are separate activities.

## Mastery cost

The next point costs development units according to:

`tier = floor(max(0, current_value - 40) / 20)`

`point_cost = 1 + tier^2 + 2 * max(0, tier - 3)^2`

Reference costs are:

| Current value band | Cost per next point |
| --- | ---: |
| 0-59 | 1 |
| 60-79 | 2 |
| 80-99 | 5 |
| 100-119 | 10 |
| 120-139 | 19 |
| 140-159 | 34 |
| 160-179 | 55 |
| 180-199 | 82 |
| 200-219 | 115 |

The existing post-100 diminishing-return factor still applies on top of this cost curve. Aptitude, instructor quality, facilities, equipment, health, recovery, relevance, difficulty fit, and capacity continue to matter.

## Long-horizon calibration

The following figures assume ideal training factors, a single stat receives every training hour, no missions or injuries interrupt the schedule, and the character starts at 100. They are deliberately optimistic lower bounds on elapsed time.

| Aptitude | Focused hours/week | Approx. time 100 to 200 |
| ---: | ---: | ---: |
| 100 | 4 | 32.2 years |
| 100 | 12 | 10.7 years |
| 100 | 30 | 4.3 years |
| 130 | 4 | 24.8 years |
| 130 | 12 | 8.3 years |
| 130 | 30 | 3.3 years |
| 160 | 4 | 20.1 years |
| 160 | 12 | 6.7 years |
| 160 | 30 | 2.7 years |
| 190 | 4 | 17.0 years |
| 190 | 12 | 5.7 years |
| 190 | 30 | 2.3 years |

Because a training session advances only one capability target, making every attribute, martial skill, operational skill, chakra dimension, domain proficiency, and method reach 200 would require many times these single-target horizons. Autonomous exact teams also rotate around mission obligations and normally resolve only a small focused block per review, so the living world should not converge toward universal 200s.

## Experience semantics

The current executable training law does not yet have an evidence-backed field-experience ledger feeding `experience_modifier`; production training therefore uses the neutral modifier of `1`. The development contract no longer claims that field experience is a mandatory high-tier training gate. Command experience and combat experience remain non-classroom, non-bulk concepts and must not be manufactured by training commands.

If an evidence-backed field/combat development bridge is added later, it must be tied to resolved mission/combat events and must not duplicate the training bank or rewrite historical progression receipts.

## Historical receipts

Balance changes are prospective. Historical gameplay receipts and diagnosed campaign repairs keep the formula version that produced them. Regression tests for old Fujin training repair arithmetic explicitly pin the legacy point-cost law rather than reinterpreting past results under the current balance curve.
