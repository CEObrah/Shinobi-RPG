from __future__ import annotations

from shinobi_runtime.commands.player_mission_delegation import _report_commitment_id


def test_report_order_identity_is_stable_and_mission_specific() -> None:
    first = _report_commitment_id("mission.offer.alpha", "pc_wei_tang")
    assert first == _report_commitment_id("mission.offer.alpha", "pc_wei_tang")
    assert first.startswith("commitment.mission_report_back.")
    assert first != _report_commitment_id("mission.offer.beta", "pc_wei_tang")
    assert first != _report_commitment_id("mission.offer.alpha", "pc_other")
