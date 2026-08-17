from __future__ import annotations

from types import SimpleNamespace

from shinobi_runtime.commands.academy_team_assignment_reconciliation import _desired_assignments
from shinobi_runtime.commands.living_world_academy import (
    LivingWorldAcademyMixin,
    _academy_affinity_groups,
    _academy_instructor,
    _academy_student_roster,
)
from shinobi_runtime.commands.team_composition import TeamMemberProfile


class _Repo:
    def __init__(self, population):
        self.population = population

    def read_json(self, path):
        assert path == "state/population/registry.json"
        return self.population


class _Planner(LivingWorldAcademyMixin):
    def __init__(self, records, population):
        self.records = records
        self.repository = _Repo(population)

    def _resolve_covered_owner_view(self, ref, *, cache):
        return f"state/char/{ref}.json", "digest", self.records[ref]


def _profile(ref: str, *, leadership: int = 50, support: int = 50) -> TeamMemberProfile:
    return TeamMemberProfile(
        person_ref=ref,
        available=True,
        availability_reason="ready",
        scores={
            "leadership": leadership,
            "support": support,
            "reconnaissance": 50,
            "control": 50,
            "assault": 50,
            "mobility": 50,
            "stealth": 50,
            "engineering": 50,
            "capture": 50,
        },
    )


def _spec():
    return {
        "parent_institution_ref": "institution.konoha.academy",
        "graduate_candidate_refs": [],
        "instructor_candidate_refs": ["canon_kakashi", "canon_asuma", "canon_kurenai"],
        "allow_service_pool_instructors": False,
        "cohort_affinity_groups": [
            {
                "student_refs": ["canon_naruto", "canon_sasuke", "canon_sakura"],
                "preferred_instructor_refs": ["canon_kakashi"],
            },
            {
                "student_refs": ["canon_hinata", "canon_kiba", "canon_shino"],
                "preferred_instructor_refs": ["canon_kurenai"],
            },
            {
                "student_refs": ["canon_ino", "canon_shikamaru", "canon_choji"],
                "preferred_instructor_refs": ["canon_asuma"],
            },
        ],
    }


def test_service_roster_expands_graduates_but_not_instructors_by_default():
    records = {
        "canon_naruto": {"official_rank_or_status": "Genin"},
        "canon_danzo": {"official_rank_or_status": "elite_jonin"},
        "canon_kakashi": {"official_rank_or_status": "elite_jonin"},
    }
    population = {
        "pools": {
            "pool.konoha.shinobi_service": {
                "representation": {
                    "rostered_person_refs": ["canon_naruto", "canon_danzo", "canon_kakashi"]
                }
            }
        }
    }
    planner = _Planner(records, population)
    policy_book = SimpleNamespace(
        institution_assignment=lambda _ref: {"service_pool_id": "pool.konoha.shinobi_service"}
    )

    graduates, instructors = planner._academy_dynamic_candidate_refs(
        spec=_spec(), policy_book=policy_book, record_writes={}
    )

    assert "canon_naruto" in graduates
    assert instructors == ["canon_kakashi", "canon_asuma", "canon_kurenai"]
    assert "canon_danzo" not in instructors


def test_service_roster_instructors_require_explicit_policy_opt_in():
    spec = dict(_spec())
    spec["allow_service_pool_instructors"] = True
    records = {
        "canon_danzo": {"official_rank_or_status": "elite_jonin"},
        "canon_kakashi": {"official_rank_or_status": "elite_jonin"},
    }
    population = {
        "pools": {
            "pool.konoha.shinobi_service": {
                "representation": {
                    "rostered_person_refs": ["canon_danzo", "canon_kakashi"]
                }
            }
        }
    }
    planner = _Planner(records, population)
    policy_book = SimpleNamespace(
        institution_assignment=lambda _ref: {"service_pool_id": "pool.konoha.shinobi_service"}
    )

    _graduates, instructors = planner._academy_dynamic_candidate_refs(
        spec=spec, policy_book=policy_book, record_writes={}
    )

    assert "canon_danzo" in instructors


def test_complete_affinity_group_is_preferred_without_becoming_a_hard_lock():
    spec = _spec()
    graduates = [
        _profile("canon_naruto"),
        _profile("canon_sasuke"),
        _profile("canon_sakura"),
        _profile("canon_hinata"),
    ]
    roster, group = _academy_student_roster(graduates, spec=spec)
    assert [row.person_ref for row in roster] == ["canon_naruto", "canon_sasuke", "canon_sakura"]
    assert group is not None

    incomplete = [
        _profile("canon_naruto"),
        _profile("canon_sasuke"),
        _profile("canon_hinata"),
        _profile("canon_kiba"),
    ]
    fallback, fallback_group = _academy_student_roster(incomplete, spec=spec)
    assert len(fallback) == 3
    assert fallback_group is None


def test_preferred_instructor_beats_higher_raw_score_when_policy_names_them():
    group = _academy_affinity_groups(_spec())[0]
    kakashi = _profile("canon_kakashi", leadership=70, support=60)
    danzo = _profile("canon_danzo", leadership=100, support=100)

    selected = _academy_instructor([danzo, kakashi], affinity_group=group)

    assert selected is not None
    assert selected.person_ref == "canon_kakashi"


def test_repair_assignments_are_policy_derived_and_cover_each_named_genin_once():
    groups = _academy_affinity_groups(_spec())
    desired = _desired_assignments(groups)

    assert desired == [
        ("canon_kakashi", ("canon_naruto", "canon_sasuke", "canon_sakura")),
        ("canon_kurenai", ("canon_hinata", "canon_kiba", "canon_shino")),
        ("canon_asuma", ("canon_ino", "canon_shikamaru", "canon_choji")),
    ]
    students = [ref for _leader, roster in desired for ref in roster]
    assert len(students) == len(set(students)) == 9
