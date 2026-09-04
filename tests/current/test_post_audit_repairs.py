"""Non-overlapping regressions for the final reconstructed audit closure."""
import inspect
import json
from datetime import datetime
from pathlib import Path

from shinobi_runtime.martial_world.aggregate_transport import faction_available_capacity, make_transport_reservation
from shinobi_runtime.martial_world.autonomy_frontier import settle_faction_autonomy_frontier
from shinobi_runtime.martial_world.calendar_participation import calendar_event_ref, occurrence_for_ref
from shinobi_runtime.martial_world.events import calendar_event_occurrence
from shinobi_runtime.martial_world.government_finance import fund_bounty_escrow, refund_bounty_escrow

ROOT = Path(__file__).resolve().parents[2]


def load(rel: str):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def test_aggregate_transport_reservations_subtract_existing_use_before_reuse():
    inventory = {"transport_capacity": {"rider_slots": 5, "freight_capacity_kg": 1000}}
    routes = {"movements": {"a": {
        "status": "active",
        "transport_reservation": make_transport_reservation(
            provider_kind="faction_pool", provider_ref="faction.test",
            rider_slots=2, freight_capacity_kg=600,
        ),
    }}}
    assert faction_available_capacity(inventory, routes, faction_ref="faction.test") == {
        "rider_slots": 3,
        "freight_capacity_kg": 400,
    }


def test_unused_government_bounty_escrow_refunds_without_minting_cash():
    before = {"cash_pool": 1000}
    funded = fund_bounty_escrow(before, existing_warrant={"bounty_escrow_cash": 300}, desired_cash=2000)
    refunded = refund_bounty_escrow(funded["market_after"], {"bounty_escrow_cash": funded["escrow_cash"]})
    assert refunded["refunded_cash"] == 1300
    assert refunded["market_after"]["cash_pool"] == 1300
    assert before["cash_pool"] + 300 == refunded["market_after"]["cash_pool"]


def test_calendar_occurrence_refs_round_trip_to_the_same_deterministic_event():
    occurrence = calendar_event_occurrence("autumn_trade_fair", 61)
    assert occurrence is not None
    ref = calendar_event_ref(occurrence)
    assert ref == "calendar:autumn_trade_fair:0061-09-15"
    assert occurrence_for_ref(ref) == occurrence


def test_readme_matches_canonical_campaign_revision():
    revision = load("state/meta.json")["revision"]
    text = (ROOT / "README.md").read_text(encoding="utf-8").lower()
    assert f"campaign revision: {revision}" in text
    assert "packaged save starts at revision 1" not in text


def test_autonomy_frontier_refreshes_shared_commitment_authority_after_callback_owned_activity():
    signature = inspect.signature(settle_faction_autonomy_frontier)
    assert "get_commitments_state" in signature.parameters
    assert "set_commitments_state" in signature.parameters
    source = inspect.getsource(settle_faction_autonomy_frontier)
    assert "def refresh_commitments" in source
    assert "def publish_commitments" in source
    assert "publish_commitments(next_commitments)" in source
    for callback in (
        "start_strategic_operation",
        "start_autonomous_investment",
        "start_monthly_merchant_trade",
        "start_custody_rescue_operation",
    ):
        marker = f"{callback}("
        at = source.index(marker)
        tail = source[at:at + 500]
        assert "refresh_commitments()" in tail, callback


def test_monthly_merchant_trade_reloads_callback_owned_state_before_final_write():
    """Guard the silver/provision stale-cache repair discovered by the 90-day audit."""
    source = inspect.getsource(settle_faction_autonomy_frontier)
    marker = 'merchant_trade = start_monthly_merchant_trade(fid)'
    at = source.index(marker)
    tail = source[at:at + 1400]
    assert 'merchant_trade.get("result") == "merchant_trade_started"' in tail
    for reload in (
        'fpath, faction = load_faction(fid)',
        'ipath, inventory = load_inventory(fid)',
        '_rpath, review_roster = load_roster(fid)',
    ):
        assert reload in tail, reload
    assert tail.index('fpath, faction = load_faction(fid)') < tail.index('executed_actions.append')
