"""Replay the two Fujin sessions affected by legacy healthy-condition projection.

These assertions pin the historical progression law that produced the committed
repair evidence. They intentionally do not call the current training reducer,
because later balance revisions must not rewrite the arithmetic of old receipts.
"""

from decimal import Decimal, ROUND_HALF_UP


def _legacy_point_cost(value: int) -> int:
    return 1 + max(0, value - 40) // 20


def _session(*, aptitude: int, current: int, residual: str, health: str, recovery: str):
    effective = Decimal("6") * Decimal("1.20") * Decimal(health) * Decimal(recovery)
    diminishing = Decimal(1) / (
        Decimal(1) + Decimal(max(0, current - 100)) / Decimal(100)
    )
    earned = (
        effective * Decimal(aptitude) / Decimal(100) * diminishing
    ).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    remaining = Decimal(residual) + earned
    value = current
    while remaining >= _legacy_point_cost(value):
        remaining -= Decimal(_legacy_point_cost(value))
        value += 1
    remaining = remaining.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    return value, remaining


def _two_sessions(*, aptitude: int, start: int, residual: str, health: str, recovery: str):
    first = _session(
        aptitude=aptitude,
        current=start,
        residual=residual,
        health=health,
        recovery=recovery,
    )
    second = _session(
        aptitude=aptitude,
        current=first[0],
        residual=str(first[1]),
        health=health,
        recovery=recovery,
    )
    return first, second


def test_committed_buggy_fujin_results_reproduce_exactly() -> None:
    kai = _two_sessions(aptitude=200, start=78, residual="0.574", health="0.85", recovery="0.90")
    mei = _two_sessions(aptitude=123, start=97, residual="0.075", health="0.85", recovery="0.90")
    riku = _two_sessions(aptitude=132, start=81, residual="0.300", health="0.85", recovery="0.90")

    assert (kai[0][0], str(kai[0][1])) == (82, "1.590")
    assert (kai[1][0], str(kai[1][1])) == (86, "0.606")
    assert (mei[0][0], str(mei[0][1])) == (99, "0.850")
    assert (mei[1][0], str(mei[1][1])) == (101, "0.625")
    assert (riku[0][0], str(riku[0][1])) == (83, "1.571")
    assert (riku[1][0], str(riku[1][1])) == (85, "2.842")


def test_correct_healthy_projection_yields_historical_repair_targets() -> None:
    kai = _two_sessions(aptitude=200, start=78, residual="0.574", health="1", recovery="1")
    mei = _two_sessions(aptitude=123, start=97, residual="0.075", health="1", recovery="1")
    riku = _two_sessions(aptitude=132, start=81, residual="0.300", health="1", recovery="1")

    assert (kai[0][0], str(kai[0][1])) == (83, "1.974")
    assert (kai[1][0], str(kai[1][1])) == (88, "1.374")
    assert (mei[0][0], str(mei[0][1])) == (99, "2.931")
    assert (mei[1][0], str(mei[1][1])) == (102, "0.787")
    assert (riku[0][0], str(riku[0][1])) == (84, "0.804")
    assert (riku[1][0], str(riku[1][1])) == (87, "1.308")
