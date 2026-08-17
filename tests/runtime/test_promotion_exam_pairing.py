from shinobi_runtime.commands import promotion_exam_integrity as integrity
from shinobi_runtime.commands.promotion_exam_pairing import (
    install_promotion_exam_pairing,
    maximum_cross_team_pairs,
)


def test_matching_does_not_create_avoidable_same_team_remainder():
    # A naive first-different greedy matcher can pair B-C first here, leaving
    # A,A unmatched despite a perfect four-bout cross-team matching existing.
    contenders = ["b1", "c1", "a1", "a2", "a3", "a4", "d1", "d2"]
    team = {
        "a1": "team.a",
        "a2": "team.a",
        "a3": "team.a",
        "a4": "team.a",
        "b1": "team.b",
        "c1": "team.c",
        "d1": "team.d",
        "d2": "team.d",
    }

    pairs, byes = maximum_cross_team_pairs(contenders, team)

    assert byes == []
    assert len(pairs) == 4
    for left, right in pairs:
        assert team[left] != team[right]


def test_matching_leaves_only_unavoidable_largest_team_byes():
    contenders = ["a1", "b1", "a2", "c1", "a3", "d1", "a4", "a5"]
    team = {
        "a1": "team.a",
        "a2": "team.a",
        "a3": "team.a",
        "a4": "team.a",
        "a5": "team.a",
        "b1": "team.b",
        "c1": "team.c",
        "d1": "team.d",
    }

    pairs, byes = maximum_cross_team_pairs(contenders, team)

    assert len(pairs) == 3
    assert len(byes) == 2
    assert all(team[left] != team[right] for left, right in pairs)
    assert {team[ref] for ref in byes} == {"team.a"}


def test_install_replaces_integrity_matcher_without_second_bracket_owner():
    install_promotion_exam_pairing()
    assert integrity._cross_team_pairs is maximum_cross_team_pairs
