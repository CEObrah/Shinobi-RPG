# Training and Development

## Representation-neutral development
A unit is compressed storage, not a training multiplier. Exact people, character-lite people, and unit members use the same underlying development law. Representation may reduce calculation and storage cost but may not increase training speed, promotion probability, experience gain, resource efficiency, recovery, or access to elite qualification.

Development depends on starting capability, aptitude, age/body development, scheduled hours, attendance, instructor access and quality, student-to-instructor ratio, facilities, equipment, nutrition, health, fatigue, recovery, relevance, difficulty fit, real mission experience, combat experience, current mastery, and diminishing returns.

## Lazy deterministic development bank
Development may settle lazily, but elapsed eligible work may never disappear merely because an owner is offscreen or because an integer capability did not cross a point threshold. `state/development/banks.json` stores sparse residual **development units** keyed by owner and exact capability target.

Each owner has exactly one authoritative development cursor. If an owner has an entry in `state/development/banks.json`, that entry's `resolved_through` is the sole cursor for development settlement and the owner sheet must not carry a second independently writable development settlement cursor. If no bank entry exists, settlement begins from the authoritative owner/process cursor and a bank entry is created only when a residual credit or lazy owner cursor is actually needed.

A bank never stores unearned scheduled hours. On a periodic settlement, causal wake-up, direct interaction, or before a capability-dependent resolution, determine the owner-specific unresolved interval from the single authoritative cursor. Split that interval at every injury, assignment, instructor, facility, equipment, doctrine, resource, mission, or other causal change. Resolve only actual eligible activity with the normal training formula, then add the resulting development units to the matching target credit.

For an integer capability, repeatedly consume `point_cost(current_value)` development units for each whole point earned, recomputing point cost after every point. Keep the nonnegative residual in the bank. Credits never move between owners or between capability targets. A bank cursor advances only through the interval actually resolved for that owner.

For aggregate units, the same development law applies. Residual credits remain unit-specific and target-specific; capability changes update the authoritative multidimensional distribution and invalidate/rebuild the derived battle kernel. Qualified-subset promotion remains a conserved integer personnel transfer and cannot be fabricated from a fractional bank. Routine training cannot manufacture field, combat, or command experience.

Sword Manor anonymous cohorts use the same law as aggregate units but keep their compact distribution and residual credits inside the House owner. A shared training block updates the cohort once. It must not fan out identical writes across every anonymous disciple. Persistent Sword Manor person-lite/exact standouts remain separate people and settle only their own attended hours; they do not receive both the cohort gain and a duplicate individual gain.

The aggregate temporal frontier is not a substitute for an owner-specific development cursor. Advancing a coarse world process may defer an owner's capability materialization, but it may not erase the unresolved interval. This is the lazy-development guarantee that prevents offscreen named characters and units from freezing or losing fractional progress.

## Capacity conservation
Instructor time, specialist equipment, training grounds, medical support, food, and facilities are finite. Group instruction is allowed, but broad supervision is not equivalent to personal correction. Advanced training scales poorly without enough qualified instructors.

## Field experience
Training grounds may build technique. They do not manufacture command experience, enemy-contact judgment, casualty management, independent mission judgment, or pressure-tested leadership. High-rank progression may require real experience.

## Promotion-by-transfer
Training changes capability. Promotion changes institutional rank. A unit never jumps rank as a whole merely because time passed. Qualified people transfer between quality/rank populations and headcount must conserve.

## Elite resolution
As personnel become rarer, stronger, higher-ranking, more specialized, or politically important, resolve them in progressively smaller units or as character-lite/exact people. Operational Jounin groups should normally be 1-25 people; ANBU elites and unique specialists should usually be exact or very small units.

## Batch equivalence
Batch settlement is allowed only when causal conditions are unchanged. A safe batch must be materially equivalent to resolving the same cycles individually. Split batches at injury, promotion, war, equipment shortage, instructor change, assignment change, or other causal boundaries.
