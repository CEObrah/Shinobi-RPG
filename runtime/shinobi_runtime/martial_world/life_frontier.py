"""Annual persistent-person life-course frontier.

This domain reducer owns maturation, natural mortality, retirement, succession,
and lawful annual faction exit for already-persistent people. It stages writes
into the caller's atomic frontier and never owns campaign chronology itself.
"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from .civic import compact_civic_person, hydrate_civic_person
from .escort import materialize_civilian_identities
from .frontier_support import chunk_contains_final_owner
from .independent_people import compact_independent_person, hydrate_independent_person
from .faction_state import allows_ordinary_membership_exit, faction_path, roster_path
from .family_simulation import advance_annual_life_course, apply_recognized_succession
from .death_lifecycle import clean_social_and_custody_for_deaths, close_family_authorities, exact_person_index, is_living, prune_dead_from_durable_activities, settle_exact_death_estates
from .handoffs import classify_handoff
from .institutional_lifecycle import settle_institutional_offices
from .institutional_obligations import member_transition_bound_person_refs
from .person_state import compact_person_state, compact_roster_state, reconcile_faction_population
from .property import detach_faction_policy_holders
from .training import settle_and_reset_faction_training_cycle
from .world_health import annual_voluntary_departure_refs, living_member_count, retirement_due

_FAMILY_PATH = "state/martial-world/family.json"
_SOCIAL_PATH = "state/martial-world/social.json"
_CUSTODY_PATH = "state/martial-world/custody.json"
_INDEPENDENTS_PATH = "state/martial-world/independent-people.json"
_CIVIC_PATH = "state/martial-world/civic-people.json"
_CIVILIANS_PATH = "state/martial-world/civilian-populations.json"
_EQUIPMENT_PATH = "state/martial-world/equipment-ledger.json"


def _living(person: Mapping[str, Any]) -> bool:
    health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
    return health.get("status") != "dead"


def _civic_office_score(person: Mapping[str, Any], office: str) -> tuple[int, int, str]:
    professional = person.get("professional_skills", {}) if isinstance(person.get("professional_skills"), Mapping) else {}
    martial = person.get("martial_skills", {}) if isinstance(person.get("martial_skills"), Mapping) else {}
    apt = person.get("aptitudes", {}) if isinstance(person.get("aptitudes"), Mapping) else {}
    if office == "imperial_marshal":
        primary = int(martial.get("command", 0)) * 3 + int(apt.get("leadership", 0))
    elif office == "merchant_head":
        primary = int(professional.get("commerce", 0)) * 3 + int(apt.get("cognitive", 0))
    else:
        primary = int(professional.get("administration", 0)) * 3 + int(apt.get("leadership", 0))
    return (-primary, int(person.get("birth_year", 10**9)), str(person.get("person_id") or ""))


def appoint_civic_successors(
    civic_rows: list[dict[str, Any]], *, dead_rows: Sequence[Mapping[str, Any]],
    civilian_state: Mapping[str, Any], world_seed: str, year: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """Fill current civic vacancies from exact people or conserved civilians.

    Hereditary offices promote an existing lawful family-status holder. Ordinary
    administrative vacancies may materialize one exact local adult by consuming
    one body from the aggregate civilian pool. Nothing is minted.
    """
    rows = [copy.deepcopy(dict(x)) for x in civic_rows if isinstance(x, Mapping)]
    civilians = copy.deepcopy(dict(civilian_state)); appointments: list[dict[str, Any]] = []
    managed = {"grand_minister", "imperial_minister", "imperial_marshal", "magistrate", "merchant_head"}
    for dead in sorted((dict(x) for x in dead_rows if isinstance(x, Mapping)), key=lambda x: str(x.get("person_id") or "")):
        affiliation = str(dead.get("affiliation_ref") or ""); home = str(dead.get("home_place_ref") or "")
        for office in [str(x) for x in dead.get("standing_offices", []) if isinstance(x, str)]:
            living = [row for row in rows if _living(row)]
            candidate = None
            if office == "emperor":
                eligible = [row for row in living if row.get("affiliation_ref") == affiliation and "prince" in row.get("standing_offices", [])]
                if eligible:
                    candidate = sorted(eligible, key=lambda row: _civic_office_score(row, office))[0]
            elif office == "noble_head":
                eligible = [row for row in living if row.get("affiliation_ref") == affiliation and "noble_family" in row.get("standing_offices", [])]
                if eligible:
                    candidate = sorted(eligible, key=lambda row: _civic_office_score(row, office))[0]
            elif office in managed:
                eligible = [row for row in living if row.get("affiliation_ref") == affiliation and not row.get("retired_from_field")]
                if office == "grand_minister":
                    eligible = [row for row in eligible if "imperial_minister" in row.get("standing_offices", [])] or eligible
                if eligible:
                    candidate = sorted(eligible, key=lambda row: _civic_office_score(row, office))[0]
                elif home:
                    try:
                        created = materialize_civilian_identities(
                            civilians, {"schema":"jianghu-civic-people-state-1.0", "people":[compact_civic_person(row) for row in rows]},
                            world_seed=world_seed, source_place_ref=home, count=1, current_year=year,
                        )
                    except ValueError:
                        created = None
                    if created:
                        civilians = created["civilian_state"]
                        owner = created["civic_state"]; new_ref = created["person_refs"][0]
                        created_row = next(hydrate_civic_person(x) for x in owner.get("people", []) if isinstance(x, Mapping) and x.get("person_id") == new_ref)
                        created_row["affiliation_ref"] = affiliation
                        created_row["social_rank"] = str(dead.get("social_rank") or "official")
                        created_row["home_place_ref"] = home; created_row["location_ref"] = home
                        created_row["public_site_types"] = copy.deepcopy(dead.get("public_site_types", []))
                        rows.append(created_row); candidate = created_row
            if candidate is None:
                continue
            ref = str(candidate.get("person_id") or "")
            for index, row in enumerate(rows):
                if row.get("person_id") != ref:
                    continue
                offices = [str(x) for x in row.get("standing_offices", []) if isinstance(x, str)]
                if office == "emperor": offices = [x for x in offices if x != "prince"]
                if office == "noble_head": offices = [x for x in offices if x != "noble_family"]
                if office == "grand_minister": offices = [x for x in offices if x != "imperial_minister"]
                if office not in offices: offices.append(office)
                row["standing_offices"] = sorted(set(offices)); rows[index] = row; candidate = row
                break
            appointments.append({"office":office, "predecessor_ref":str(dead.get("person_id") or ""), "successor_ref":ref})
    return rows, civilians, appointments


def settle_annual_life_frontier(
    *,
    events: Sequence[Mapping[str, Any]],
    at: datetime,
    player_ref: str,
    family_state: Mapping[str, Any],
    social_state: Mapping[str, Any],
    custody_state: Mapping[str, Any],
    independent_state: Mapping[str, Any],
    civic_state: Mapping[str, Any],
    civilian_state: Mapping[str, Any],
    schedule: Mapping[str, Any],
    world_seed: str,
    place_region: Mapping[str, str],
    site_rows: Mapping[str, Any],
    load_market: Callable[[str], tuple[str, dict[str, Any]]],
    market_cache: dict[str, tuple[str, dict[str, Any]]],
    writes: dict[str, Any],
    reviews: list[dict[str, Any]],
    handoffs: list[dict[str, Any]],
    pending_training_resume_refs: set[str],
    load_faction: Callable[[str], tuple[str, dict[str, Any]]],
    load_roster: Callable[[str], tuple[str, dict[str, Any]]],
    committed_person_refs: Callable[[], set[str]],
    active_combat_person_refs: Callable[[], set[str]],
    unavailable_person_refs: Callable[[], set[str]],
    family_bound_refs: Callable[[str], set[str]],
    faction_cache: dict[str, tuple[str, dict[str, Any]]],
    roster_cache: dict[str, tuple[str, dict[str, Any]]],
    read_json: Callable[[str], Any] | None = None,
    faction_refs: Sequence[str] = (),
) -> dict[str, Any]:
    """Settle annual life events and return updated shared owner projections."""
    family = copy.deepcopy(dict(family_state))
    social = copy.deepcopy(dict(social_state))
    custody = copy.deepcopy(dict(custody_state))
    independents = copy.deepcopy(dict(independent_state))
    civic = copy.deepcopy(dict(civic_state))
    civilians = copy.deepcopy(dict(civilian_state))
    at_iso = at.isoformat()

    # Keep this domain reducer independently testable. Production supplies the
    # repository reader and complete active faction registry; isolated callers
    # can still exercise non-faction lifecycle semantics from the owners passed
    # directly to the reducer.
    if read_json is None:
        def _local_read_json(path: str) -> Any:
            if path == _INDEPENDENTS_PATH:
                return copy.deepcopy(writes.get(path, independents))
            if path == _CIVIC_PATH:
                return copy.deepcopy(writes.get(path, civic))
            if path == _FAMILY_PATH:
                return copy.deepcopy(writes.get(path, family))
            prefix = "state/martial-world/markets/"
            if path.startswith(prefix) and path.endswith(".json"):
                region = path[len(prefix):-5]
                if path in writes:
                    return copy.deepcopy(writes[path])
                _mpath, market = load_market(region)
                return copy.deepcopy(market)
            raise FileNotFoundError(path)
        read_json = _local_read_json

    def _transition_read(path: str) -> Any:
        return copy.deepcopy(writes[path]) if path in writes else read_json(path)

    transition_bound_refs = member_transition_bound_person_refs(_transition_read)

    def close_annual_deaths(dead_refs: Sequence[str]) -> dict[str, Any]:
        """Apply the shared exact-person death core inside annual biology.

        Domain-specific retirement/succession remains here, but family closure,
        current-office invalidation and personal estate ownership are identical
        to combat death. Cross-owner heirs are therefore legal and conserved.
        """
        nonlocal family, social, custody, independents, civic
        dead = sorted(set(str(x) for x in dead_refs if isinstance(x, str) and x))
        if not dead:
            return {"settled_cash": 0, "dead_civic_rows": []}

        # The local projections may already include earlier annual departures or
        # prior death transfers from this same resumable frontier. Stage them so
        # the universal index sees current truth rather than repository baseline.
        writes[_INDEPENDENTS_PATH] = copy.deepcopy(independents)
        writes[_CIVIC_PATH] = copy.deepcopy(civic)
        index = exact_person_index(read_json=read_json, writes=writes, faction_refs=faction_refs)
        living_people = {
            ref: route["person"] for ref, route in index.items()
            if ref not in dead and isinstance(route.get("person"), Mapping) and is_living(route["person"])
        }
        family = close_family_authorities(family, dead_refs=dead, living_people=living_people)
        social, custody, released = clean_social_and_custody_for_deaths(
            social, custody, dead_refs=dead,
        )
        pending_training_resume_refs.update(released)
        writes[_FAMILY_PATH] = family
        writes[_SOCIAL_PATH] = social
        writes[_CUSTODY_PATH] = custody

        dead_civic_rows: list[dict[str, Any]] = []
        for ref in dead:
            route = index.get(ref)
            if not isinstance(route, Mapping):
                continue
            if route.get("owner_kind") == "civic" and isinstance(route.get("person"), Mapping):
                dead_civic_rows.append(copy.deepcopy(dict(route["person"])))
            path = str(route.get("path") or "")
            if not path:
                continue
            owner = copy.deepcopy(writes.get(path) if isinstance(writes.get(path), Mapping) else read_json(path))
            rows = owner.get("people", []) if isinstance(owner, Mapping) else []
            if not isinstance(rows, list):
                raise ValueError("jianghu exact person owner invalid")
            ordinal = int(route.get("ordinal", -1))
            if not (0 <= ordinal < len(rows) and isinstance(rows[ordinal], Mapping) and rows[ordinal].get("person_id") == ref):
                ordinal = next((i for i, row in enumerate(rows) if isinstance(row, Mapping) and row.get("person_id") == ref), -1)
            if ordinal < 0:
                raise ValueError(f"jianghu exact person route stale: {ref}")
            row = copy.deepcopy(dict(rows[ordinal])); row["standing_offices"] = []; rows[ordinal] = row
            owner["people"] = rows; writes[path] = owner

        estate = settle_exact_death_estates(
            read_json=read_json, writes=writes, faction_refs=faction_refs, family=family,
            dead_refs=dead, place_region=place_region, site_rows=site_rows,
        )
        prune_dead_from_durable_activities(
            read_json=read_json, writes=writes, dead_refs=dead, faction_refs=faction_refs,
        )
        # Cross-owner inheritance can touch previously cached faction or market
        # owners. Remove those projections so later annual owners reload staged
        # post-transfer truth instead of overwriting it with stale cached data.
        for touched_fid in estate.get("touched_faction_refs", []):
            faction_cache.pop(str(touched_fid), None)
            roster_cache.pop(str(touched_fid), None)
        for region_raw in estate.get("touched_market_regions", []):
            region = str(region_raw)
            mpath = f"state/martial-world/markets/{region}.json"
            staged_market = writes.get(mpath)
            if isinstance(staged_market, Mapping):
                _loaded_path, loaded_market = load_market(region)
                loaded_market.clear(); loaded_market.update(copy.deepcopy(dict(staged_market)))
                market_cache[region] = (mpath, loaded_market)
        if isinstance(writes.get(_INDEPENDENTS_PATH), Mapping):
            independents = copy.deepcopy(dict(writes[_INDEPENDENTS_PATH]))
        if isinstance(writes.get(_CIVIC_PATH), Mapping):
            civic = copy.deepcopy(dict(writes[_CIVIC_PATH]))
        return {**estate, "dead_civic_rows": dead_civic_rows}

    for event in events:
        if event.get("kind") != "annual_faction_life_review":
            continue
        fid = event.get("owner_ref")
        if not isinstance(fid, str):
            continue
        fpath, faction = load_faction(fid)
        rpath, roster = load_roster(fid)

        # Materialize the old epoch before death, maturation, retirement,
        # succession or departure changes the next training environment.
        faction, roster, _training = settle_and_reset_faction_training_cycle(
            faction, roster, at_iso=at_iso,
        )
        before_people = [p for p in roster.get("people", []) if isinstance(p, Mapping)]
        life = advance_annual_life_course(
            before_people,
            year=at.year,
            player_ref=player_ref or None,
            exclude_death_refs=sorted(committed_person_refs() | active_combat_person_refs()),
        )
        roster["people"] = life["people_after"]
        died = list(life["died_refs"])
        matured = list(life["matured_refs"])

        retired_refs: list[str] = []
        retired_people: list[Any] = []
        for raw in roster.get("people", []):
            if not isinstance(raw, Mapping):
                retired_people.append(raw)
                continue
            person = copy.deepcopy(dict(raw))
            health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
            if (
                not person.get("retired_from_field")
                and retirement_due(person, year=at.year)
                and health.get("status") != "dead"
            ):
                person["retired_from_field"] = True
                if isinstance(person.get("person_id"), str):
                    retired_refs.append(str(person["person_id"]))
            retired_people.append(person)
        roster["people"] = retired_people

        succession_ref = None
        if died:
            # Stage the newly dead roster first, then run the same global death
            # core used by combat. This closes cross-owner family status and
            # settles personal cash before succession or later extinction.
            writes[fpath] = faction
            writes[rpath] = compact_roster_state(roster, faction=faction)
            close_annual_deaths(died)
            fpath, faction = load_faction(fid)
            rpath, roster = load_roster(fid)
            succession = apply_recognized_succession(
                family,
                faction_ref=fid,
                roster_people=[p for p in roster.get("people", []) if isinstance(p, Mapping)],
                year=at.year,
            )
            roster["people"] = succession["people_after"]
            succession_ref = succession.get("successor_ref")
            custody_unavailable = {
                str(row.get("person_ref"))
                for row in (custody.get("records", []) if isinstance(custody.get("records"), list) else [])
                if isinstance(row, Mapping) and str(row.get("person_ref") or "")
                and str(row.get("status") or "") not in {"released", "escaped", "rescued", "executed"}
            }
            office_result = settle_institutional_offices(
                faction, roster, year=at.year, social=social,
                player_ref=player_ref or None, unavailable_refs=sorted(custody_unavailable),
            )
            roster = office_result["roster"]
            if succession_ref is None:
                succession_ref = next((
                    row["person_ref"] for row in office_result["appointments"]
                    if row.get("office") == "leader"
                ), None)

        annual_departure_refs = annual_voluntary_departure_refs(
            [p for p in roster.get("people", []) if isinstance(p, Mapping)],
            faction_ref=fid,
            year=at.year,
            hardship_milli=0,
            protected_refs=sorted(
                family_bound_refs(fid)
                | unavailable_person_refs()
                | transition_bound_refs
                | ({player_ref} if player_ref else set())
            ),
            maximum=max(
                1,
                living_member_count([p for p in roster.get("people", []) if isinstance(p, Mapping)]) // 200,
            ),
            period_key=f"annual-{at.year}",
            allow_voluntary_departure=allows_ordinary_membership_exit(fid),
        )
        annual_departure_refs = [ref for ref in annual_departure_refs if ref not in transition_bound_refs]
        if annual_departure_refs:
            leaving = set(annual_departure_refs)
            kept: list[Any] = []
            independent_rows = independents.setdefault("people", [])
            if not isinstance(independent_rows, list):
                raise ValueError("jianghu independent people invalid")
            for raw in roster.get("people", []):
                if not isinstance(raw, Mapping) or str(raw.get("person_id")) not in leaving:
                    kept.append(raw)
                    continue
                person = compact_person_state(raw, faction_ref=fid)
                person.pop("membership_grade", None)
                person["standing_offices"] = []
                person["location_ref"] = str(
                    raw.get("location_ref")
                    or faction.get("local_site_ref")
                    or faction.get("headquarters")
                    or ""
                )
                person["former_faction_ref"] = fid
                person["independent_since"] = at_iso
                independent_rows.append(person)
            roster["people"] = kept
            try:
                equipment_source = writes.get(_EQUIPMENT_PATH, read_json(_EQUIPMENT_PATH))
            except FileNotFoundError:
                equipment_source = {"schema":"jianghu-equipment-ledger-1.0"}
            equipment_transition = detach_faction_policy_holders(
                equipment_source, source_faction_ref=fid, holder_refs=sorted(leaving),
            )
            writes[_EQUIPMENT_PATH] = equipment_transition["equipment_ledger_after"]
            writes[_INDEPENDENTS_PATH] = independents

        faction = reconcile_faction_population(faction, roster)
        writes[fpath] = faction
        faction_cache[fid] = (fpath, faction)
        writes[rpath] = compact_roster_state(roster, faction=faction)
        roster_cache[fid] = (rpath, roster)
        reviews.append({
            "kind": "annual_faction_life_review",
            "event_id": event.get("event_id"),
            "faction_ref": fid,
            "matured_count": len(matured),
            "natural_death_count": len(died),
            "retired_count": len(retired_refs),
            "departed_count": len(annual_departure_refs),
            "succession_ref": succession_ref,
        })
        if died and fid == "house_tang":
            notice = {
                "kind": "family_death_notice",
                "faction_ref": fid,
                "person_refs": sorted(died),
                "delivered_to_player": True,
                "requires_player_decision": False,
            }
            handoff = classify_handoff(notice)
            handoffs.append({**notice, "handoff": handoff})
        if succession_ref and (fid == "house_tang" or succession_ref == player_ref):
            notice = {
                "kind": "succession_notice",
                "faction_ref": fid,
                "successor_ref": succession_ref,
                "delivered_to_player": True,
            }
            handoff = classify_handoff(notice)
            handoffs.append({**notice, "handoff": handoff})

    # The faction annual schedule is resumable in four-owner chunks. Global
    # civic/independent biology must therefore execute exactly once, in the
    # chunk containing the final annual owner, never once per faction chunk.
    if chunk_contains_final_owner(schedule, events, class_id="faction_annual"):
        independent_rows = [hydrate_independent_person(row) for row in independents.get("people", []) if isinstance(row, Mapping)]
        civic_rows_before = [hydrate_civic_person(row) for row in civic.get("people", []) if isinstance(row, Mapping)]
        excluded = sorted(committed_person_refs() | active_combat_person_refs())
        independent_life = advance_annual_life_course(independent_rows, year=at.year, player_ref=player_ref or None, exclude_death_refs=excluded)
        civic_life = advance_annual_life_course(civic_rows_before, year=at.year, player_ref=player_ref or None, exclude_death_refs=excluded)
        independent_rows = [copy.deepcopy(dict(row)) for row in independent_life["people_after"]]
        civic_rows = [copy.deepcopy(dict(row)) for row in civic_life["people_after"]]

        retired_independent: list[str] = []; retired_civic: list[str] = []
        for rows, refs in ((independent_rows, retired_independent), (civic_rows, retired_civic)):
            for index, row in enumerate(rows):
                if not row.get("retired_from_field") and retirement_due(row, year=at.year) and _living(row):
                    row["retired_from_field"] = True
                    refs.append(str(row.get("person_id") or "")); rows[index] = row

        dead_independent = set(independent_life["died_refs"]); dead_civic = set(civic_life["died_refs"])
        dead_all = sorted(dead_independent | dead_civic)

        # Stage annual non-faction biology, then run the same death core used
        # for faction and combat mortality. This is what makes a living faction
        # spouse/child a valid heir to a civic or independent decedent.
        independents["people"] = [compact_independent_person(row) for row in independent_rows]
        civic["people"] = [compact_civic_person(row) for row in civic_rows]
        writes[_INDEPENDENTS_PATH] = independents
        writes[_CIVIC_PATH] = civic
        estate_cash = 0
        dead_civic_rows: list[dict[str, Any]] = []
        if dead_all:
            death_result = close_annual_deaths(dead_all)
            estate_cash = int(death_result.get("settled_cash", 0))
            # Annual life-course clearing removes current offices from the dead
            # row. Civic succession needs the pre-death office snapshot to know
            # which exact vacancy was created.
            dead_civic_rows = [
                copy.deepcopy(dict(row)) for row in civic_rows_before
                if isinstance(row, Mapping) and str(row.get("person_id") or "") in dead_civic
            ]
            independent_rows = [
                hydrate_independent_person(row) for row in independents.get("people", [])
                if isinstance(row, Mapping)
            ]
            civic_rows = [
                hydrate_civic_person(row) for row in civic.get("people", [])
                if isinstance(row, Mapping)
            ]

        # Resolve civic office vacancies from existing exact people first.
        # Ordinary administrative vacancies may consume one aggregate civilian
        # body and promote it into an exact civic identity.
        civic_rows, civilians, appointments = appoint_civic_successors(
            civic_rows, dead_rows=dead_civic_rows, civilian_state=civilians, world_seed=world_seed, year=at.year,
        )
        independents["people"] = [compact_independent_person(row) for row in independent_rows]
        civic["people"] = [compact_civic_person(row) for row in civic_rows]
        writes[_INDEPENDENTS_PATH] = independents; writes[_CIVIC_PATH] = civic
        if appointments:
            writes[_CIVILIANS_PATH] = civilians
        reviews.append({
            "kind":"annual_nonfaction_life_review",
            "matured_count":len(independent_life["matured_refs"]) + len(civic_life["matured_refs"]),
            "natural_death_count":len(dead_all),
            "retired_count":len(retired_independent) + len(retired_civic),
            "civic_appointments":appointments,
            "estate_cash_settled":estate_cash,
        })

    return {
        "family_state": family,
        "social_state": social,
        "custody_state": custody,
        "independent_state": independents,
        "civic_state": civic,
        "civilian_state": civilians,
    }


__all__ = ["appoint_civic_successors", "settle_annual_life_frontier"]
