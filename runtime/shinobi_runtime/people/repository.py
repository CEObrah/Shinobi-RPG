"""Cold repository resolver for one full logical person sheet."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from shinobi_runtime.people.cohorts import cohort_slot_baseline
from shinobi_runtime.people.profiles import apply_rostered_profile, profile_entry_for
from shinobi_runtime.people.core import (
    assemble_sheet,
    core_from_exact,
    core_from_person,
    core_from_registry,
)
from shinobi_runtime.sim import CampaignTime
from shinobi_runtime.store import RepositoryStore




def _population_host_baseline(*, pool_id: str, profile: Mapping[str, Any], person_id: str) -> Mapping[str, Any]:
    """Derive one stable cold-person baseline from a population/cohort host.

    Population materialization does not invent exact stats.  The stable person
    therefore inherits the current aggregate host baseline until a material
    individual divergence component exists.  Categorical placement is chosen
    deterministically from saved proportions so reopening the same cold person
    never rerolls them.
    """

    if not isinstance(profile, Mapping):
        raise ValueError("population-backed person requires a population profile")
    numeric = profile.get("numeric_distributions")
    dimensions = profile.get("dimension_counts")
    if not isinstance(numeric, Mapping) or not isinstance(dimensions, Mapping):
        raise ValueError("population-backed person profile is incomplete")
    numeric_values: Dict[str, float] = {}
    for name, summary in sorted(numeric.items()):
        if not isinstance(name, str) or not isinstance(summary, Mapping):
            raise ValueError("population numeric distribution is invalid")
        mean = summary.get("mean")
        if isinstance(mean, bool) or not isinstance(mean, (int, float)):
            raise ValueError("population numeric distribution mean is invalid")
        numeric_values[name] = float(mean)

    category_values: Dict[str, tuple[str, ...]] = {}
    for dimension, counts in sorted(dimensions.items()):
        if not isinstance(dimension, str) or not isinstance(counts, Mapping):
            raise ValueError("population dimension distribution is invalid")
        weighted = []
        total = 0
        for label, amount in sorted(counts.items()):
            if not isinstance(label, str) or isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
                raise ValueError("population dimension count is invalid")
            if amount:
                weighted.append((label, amount))
                total += amount
        if total <= 0:
            category_values[dimension] = ()
            continue
        digest = hashlib.sha256(f"{pool_id}\x00{person_id}\x00{dimension}".encode("utf-8")).digest()
        pick = int.from_bytes(digest[:8], "big") % total
        running = 0
        chosen = weighted[-1][0]
        for label, amount in weighted:
            running += amount
            if pick < running:
                chosen = label
                break
        category_values[dimension] = (chosen,)

    source_hash = hashlib.sha256(
        json.dumps(profile, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "representation": "rostered_population",
        "cohort_ref": pool_id,
        "source_profile_sha256": source_hash,
        "numeric_values": numeric_values,
        "category_values": category_values,
    }

def _owner_prefix(owner_id: str) -> str:
    dot = owner_id.find(".")
    underscore = owner_id.find("_")
    boundaries = [value for value in (dot, underscore) if value >= 0]
    return owner_id if not boundaries else owner_id[: min(boundaries)]


class RepositoryPersonSheetResolver:
    """Resolve one ID without scanning or preloading the person catalog."""

    def __init__(self, repository: RepositoryStore) -> None:
        self.repository = repository

    def _owner_index(self) -> Mapping[str, Any]:
        index = self.repository.read_json("state/index/owners.json")
        if not isinstance(index, Mapping):
            raise ValueError("owner index root is invalid")
        if not isinstance(index.get("prefix_index"), Mapping):
            raise ValueError("owner prefix index is invalid")
        return index

    def _owner_path(
        self,
        owner_id: str,
        *,
        index: Mapping[str, Any],
        shard_cache: Dict[str, Mapping[str, Any]],
    ) -> Optional[str]:
        shard_path = index["prefix_index"].get(_owner_prefix(owner_id))
        if not isinstance(shard_path, str):
            return None
        shard = shard_cache.get(shard_path)
        if shard is None:
            loaded = self.repository.read_json(shard_path)
            if not isinstance(loaded, Mapping):
                raise ValueError("owner index shard is invalid")
            shard = loaded
            shard_cache[shard_path] = shard
        path = shard.get("owners", {}).get(owner_id)
        return path if isinstance(path, str) else None

    def _components(self, references: Mapping[str, Any]) -> Mapping[str, Mapping[str, Any]]:
        components = {}
        for namespace, relative_path in references.items():
            if not isinstance(namespace, str) or not isinstance(relative_path, str):
                raise ValueError("person component route is invalid")
            component = self.repository.read_json(relative_path)
            if not isinstance(component, Mapping):
                raise ValueError("person component owner is invalid")
            components[namespace] = component
        return components

    def _continuity(self, person_id: str) -> Optional[Mapping[str, Any]]:
        try:
            record = self.repository.read_json("state/reg/person-continuity.json")
        except (FileNotFoundError, ValueError):
            return None
        people = record.get("people") if isinstance(record, Mapping) else None
        value = people.get(person_id) if isinstance(people, Mapping) else None
        return dict(value) if isinstance(value, Mapping) else None

    def __call__(self, person_id: str) -> Optional[Mapping[str, Any]]:
        owner_index = self._owner_index()
        shard_cache: Dict[str, Mapping[str, Any]] = {}
        path = self._owner_path(
            person_id,
            index=owner_index,
            shard_cache=shard_cache,
        )
        if path is None:
            return None
        record = self.repository.read_json(path)
        if not isinstance(record, Mapping):
            raise ValueError("person owner root is invalid")
        schema = record.get("schema")

        if schema == "shinobi_character":
            if record.get("owner_id") != person_id:
                raise ValueError("exact person owner ID mismatch")
            core = core_from_exact(
                record,
                component_ref="profile.exact",
                source_ref=path,
            )
            continuity = self._continuity(person_id)
            baseline = {} if continuity is None else {"continuity": continuity}
            return assemble_sheet(
                core,
                cohort_baseline=baseline,
                components={"profile.exact": record},
            ).to_record()

        if schema == "person":
            if record.get("id") != person_id:
                raise ValueError("person owner ID mismatch")
            core = core_from_person(
                record,
                component_ref="profile.person",
                source_ref=path,
            )
            continuity = self._continuity(person_id)
            baseline = {} if continuity is None else {"continuity": continuity}
            return assemble_sheet(
                core,
                cohort_baseline=baseline,
                components={"profile.person": record},
            ).to_record()

        if schema != "person-core-registry":
            return None
        people = record.get("people")
        if not isinstance(people, Mapping) or person_id not in people:
            raise ValueError("owner index routes to a registry without the requested core")
        core_record = people[person_id]
        if not isinstance(core_record, Mapping):
            raise ValueError("person core is invalid")
        core = core_from_registry(record, person_id=person_id, source_ref=path)

        owner_ref = record.get("owner_ref")
        cohort_ref = core.cohort_ref
        saved_profile = None
        if isinstance(record.get("profiles"), Mapping):
            saved_profile = profile_entry_for(record, person_id)
        if saved_profile is not None:
            baseline = dict(apply_rostered_profile(record, saved_profile))
            institutional = baseline.get("institutional_progression")
            standing = institutional.get("standing") if isinstance(institutional, Mapping) else None
            profile_cohort = saved_profile.get("cohort_ref")
            if profile_cohort != cohort_ref:
                raise ValueError("person core and individual profile cohort refs diverge")
            core = replace(
                core,
                rank_or_status=standing if isinstance(standing, str) else core.rank_or_status,
            )
        elif isinstance(cohort_ref, str) and cohort_ref.startswith("pool."):
            population = self.repository.read_json("state/population/registry.json")
            pools = population.get("pools") if isinstance(population, Mapping) else None
            pool = pools.get(cohort_ref) if isinstance(pools, Mapping) else None
            if not isinstance(pool, Mapping):
                raise ValueError("population-backed person cohort is missing")
            baseline = dict(
                _population_host_baseline(
                    pool_id=cohort_ref,
                    profile=pool.get("profile"),
                    person_id=person_id,
                )
            )
        elif isinstance(owner_ref, str):
            house_path = self._owner_path(
                owner_ref,
                index=owner_index,
                shard_cache=shard_cache,
            )
            if house_path is not None:
                house = self.repository.read_json(house_path)
            else:
                house = None
            if isinstance(house, Mapping) and house.get("schema") == "house":
                matches = [
                    cohort
                    for cohort in house.get("cohorts", [])
                    if isinstance(cohort, Mapping) and cohort.get("id") == cohort_ref
                ]
                if len(matches) != 1:
                    raise ValueError("person core cohort is missing or ambiguous")
                cohort = matches[0]
                refs = cohort.get("roster_refs")
                if not isinstance(refs, list) or person_id not in refs:
                    raise ValueError("person core is absent from its claimed cohort roster")
                baseline = dict(
                    cohort_slot_baseline(
                        cohort_id=cohort_ref,
                        profile=cohort.get("cohort_profile"),
                        slot=core.source_ordinal,
                        expected_count=len(refs),
                    )
                )
            else:
                # A rostered identity may belong to a civil institution, minor
                # settlement, canon host, or another world organization whose
                # routine mechanics are not yet individualized.  Identity
                # remains persistent without inventing capability from absence.
                baseline = {
                    "representation": "rostered_identity",
                    "cohort_ref": cohort_ref,
                    "numeric_values": {},
                    "category_values": {},
                    "source_ref": owner_ref,
                }
        else:
            baseline = {
                "representation": "rostered_identity",
                "cohort_ref": cohort_ref,
                "numeric_values": {},
                "category_values": {},
                "source_ref": "unassigned_world_roster",
            }
        # Exact calendar facts such as age are always derived from the stable
        # birth anchor at read time. A saved lightweight profile freezes the
        # person's capability independently of cohort statistics; registries
        # without profiles retain the generic cohort-backed fallback.
        try:
            meta = self.repository.read_json("state/meta.json")
            now = CampaignTime.parse(meta.get("time"))
        except (FileNotFoundError, TypeError, ValueError, AttributeError) as exc:
            raise ValueError("campaign time is unavailable for person catch-up") from exc
        if core.birth_date is not None:
            age = now.year - core.birth_date.year
            if (now.month, now.day) < (core.birth_date.month, core.birth_date.day):
                age -= 1
            exact_age = max(0, age)
            numeric = dict(baseline.get("numeric_values", {}))
            numeric["age_years"] = float(exact_age)
            baseline["numeric_values"] = numeric
            categories = dict(baseline.get("category_values", {}))
            if exact_age < 12:
                age_band = "child"
            elif exact_age < 18:
                age_band = "adolescent"
            elif exact_age < 65:
                age_band = "adult"
            else:
                age_band = "elder"
            categories["age_band"] = (age_band,)
            baseline["category_values"] = categories
        if not core.component_refs:
            core = replace(core, resolved_through=now)
        continuity = self._continuity(person_id)
        if continuity is not None:
            baseline = dict(baseline)
            baseline["continuity"] = continuity
        component_refs = core_record.get("component_refs")
        if not isinstance(component_refs, Mapping):
            raise ValueError("person core component refs are invalid")
        return assemble_sheet(
            core,
            cohort_baseline=baseline,
            components=self._components(component_refs),
        ).to_record()


def repository_sheet_resolver(root: Path) -> RepositoryPersonSheetResolver:
    return RepositoryPersonSheetResolver(RepositoryStore(root))
