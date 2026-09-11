from shinobi_runtime.martial_world.travel_provisions import (
    add_faction_upkeep_credit, apply_monthly_upkeep_credit,
    apply_route_provision_progress, refund_unused_to_faction,
    reserve_faction_rations, reserve_personal_rations,
)


def test_partial_route_refunds_only_unused_faction_rations_and_credits_consumed_days_once():
    inv = {"food_ration_days": 20}
    inv_after, reservation = reserve_faction_rations(
        inv, faction_ref="faction.test", participant_count=2, travel_seconds=3 * 24 * 3600,
    )
    assert inv_after["food_ration_days"] == 14
    movement = {"provision_reservation": reservation}
    movement, newly = apply_route_provision_progress(movement, progressed_seconds=25 * 3600)
    assert newly == 4
    credited = add_faction_upkeep_credit(inv_after, newly)
    credited, net_due, used = apply_monthly_upkeep_credit(credited, gross_food_due=10)
    assert (net_due, used) == (6, 4)
    credited2, net_due2, used2 = apply_monthly_upkeep_credit(credited, gross_food_due=10)
    assert (net_due2, used2) == (10, 0)
    refunded, unused = refund_unused_to_faction(credited2, movement)
    assert unused == 2
    assert refunded["food_ration_days"] == 16


def test_personal_route_reservation_refunds_unused_to_same_person():
    person = {"travel_ration_days": 8}
    person_after, reservation = reserve_personal_rations(
        person, person_ref="pc", participant_count=2, travel_seconds=2 * 24 * 3600,
    )
    assert person_after["travel_ration_days"] == 4
    movement = {"provision_reservation": reservation}
    movement, newly = apply_route_provision_progress(movement, progressed_seconds=2 * 3600)
    assert newly == 2
    from shinobi_runtime.martial_world.travel_provisions import refund_unused_to_person
    person_refunded, unused = refund_unused_to_person(person_after, movement)
    assert unused == 2
    assert person_refunded["travel_ration_days"] == 6
