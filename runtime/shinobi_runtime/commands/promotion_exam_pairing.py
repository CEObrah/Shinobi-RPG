"""Deterministic maximum cross-team matching for promotion-exam finals.

The finals integrity layer forbids same-team bouts. A naive greedy scan can still
consume scarce opponents in the wrong order and leave same-team co-finalists even
when a full lawful cross-team matching exists. This helper pairs the two largest
remaining team groups at each step, which yields a maximum matching in the
complete multipartite candidate graph. Tie-breaking follows the already-seeded
candidate order, then the stable team ref, so callers cannot choose opponents.
"""
from __future__ import annotations

import heapq
from collections import defaultdict
from typing import Mapping

_INSTALLED = False


def maximum_cross_team_pairs(
    contenders: list[str],
    team_by_candidate: Mapping[str, str],
) -> tuple[list[tuple[str, str]], list[str]]:
    order = {candidate_ref: index for index, candidate_ref in enumerate(contenders)}
    groups: dict[str, list[str]] = defaultdict(list)
    for candidate_ref in contenders:
        team_ref = team_by_candidate.get(candidate_ref, f"candidate:{candidate_ref}")
        groups[team_ref].append(candidate_ref)

    heap: list[tuple[int, int, str, list[str]]] = []
    for team_ref, candidates in groups.items():
        heapq.heappush(
            heap,
            (-len(candidates), order[candidates[0]], team_ref, candidates),
        )

    pairs: list[tuple[str, str]] = []
    while len(heap) >= 2:
        neg_a, _first_a, team_a, candidates_a = heapq.heappop(heap)
        neg_b, _first_b, team_b, candidates_b = heapq.heappop(heap)
        if team_a == team_b:
            raise ValueError("promotion exam matching heap duplicated one team")

        left = candidates_a.pop(0)
        right = candidates_b.pop(0)
        pair = (left, right) if order[left] <= order[right] else (right, left)
        pairs.append(pair)

        if candidates_a:
            heapq.heappush(
                heap,
                (neg_a + 1, order[candidates_a[0]], team_a, candidates_a),
            )
        if candidates_b:
            heapq.heappush(
                heap,
                (neg_b + 1, order[candidates_b[0]], team_b, candidates_b),
            )

    byes: list[str] = []
    while heap:
        _neg, _first, _team_ref, candidates = heapq.heappop(heap)
        byes.extend(candidates)
    byes.sort(key=order.__getitem__)
    return pairs, byes


def install_promotion_exam_pairing() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.commands import promotion_exam_integrity as integrity

    integrity._cross_team_pairs = maximum_cross_team_pairs
    _INSTALLED = True


__all__ = ["install_promotion_exam_pairing", "maximum_cross_team_pairs"]
