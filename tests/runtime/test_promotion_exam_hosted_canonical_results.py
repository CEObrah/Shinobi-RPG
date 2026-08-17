from __future__ import annotations

import pytest

from shinobi_runtime.commands.promotion_exam_hosted_lifecycle import _route_days_result_value
from shinobi_runtime.tx.canonical import canonical_json_bytes


def test_hosted_route_duration_result_value_is_canonical_safe() -> None:
    with pytest.raises(TypeError, match="floating-point values are forbidden"):
        canonical_json_bytes({"minimum_route_days": 8.0})

    assert _route_days_result_value(8.0) == "8"
    assert _route_days_result_value(8.5) == "8.5"
    assert canonical_json_bytes(
        {
            "hosted_delegation_travel_reconciliation": [
                {"minimum_route_days": _route_days_result_value(8.0)}
            ]
        }
    ) == b'{"hosted_delegation_travel_reconciliation":[{"minimum_route_days":"8"}]}\n'
