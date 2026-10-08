from decimal import Decimal as D

import pytest

from rate_parity.compare import decide_status, gap_pct, is_suspect, room_matches
from rate_parity.models import RoomKey, Status

TOL = D("1.0")


def test_gap_pct_negative_when_ota_cheaper():
    assert gap_pct(D("9500"), D("10000")) == D("-5.00")


def test_gap_pct_rounds_to_two_places():
    assert gap_pct(D("7906"), D("7999")) == D("-1.16")


def test_gap_pct_rejects_zero_website_price():
    with pytest.raises(ValueError):
        gap_pct(D("100"), D("0"))


def test_floats_are_rejected():
    with pytest.raises(TypeError):
        gap_pct(95.0, D("100"))
    with pytest.raises(TypeError):
        is_suspect(D("1"), 2)


def test_is_suspect_only_when_strictly_cheaper():
    assert is_suspect(D("99"), D("100"))
    assert not is_suspect(D("100"), D("100"))
    assert not is_suspect(D("101"), D("100"))


def test_violation_beyond_tolerance():
    assert decide_status(D("9800"), D("10000"), TOL) is Status.VIOLATION


def test_exactly_at_tolerance_is_not_a_violation():
    assert decide_status(D("9900"), D("10000"), TOL) is Status.FALSE_ALARM


def test_unrounded_gap_is_used():
    # -1.004% rounds to -1.00 but is still beyond a 1% tolerance
    assert decide_status(D("9899.6"), D("10000"), TOL) is Status.VIOLATION


def test_suspect_not_cheaper_at_checkout_is_false_alarm():
    assert decide_status(D("10200"), D("10000"), TOL, suspect=True) is Status.FALSE_ALARM


def test_not_suspect_is_in_parity():
    assert decide_status(D("10200"), D("10000"), TOL, suspect=False) is Status.IN_PARITY


def test_missing_values_could_not_check():
    assert decide_status(None, D("10000"), TOL) is Status.COULD_NOT_CHECK
    assert decide_status(D("10000"), None, TOL) is Status.COULD_NOT_CHECK


EXPECTED = RoomKey("Deluxe Room", "Breakfast included", "Free cancellation")


def test_room_match_requires_all_three():
    seen = RoomKey("Deluxe  ROOM with balcony", "breakfast included", "Free cancellation before 12 Oct")
    assert room_matches(EXPECTED, seen)


@pytest.mark.parametrize("seen", [
    RoomKey("Premium Room", "Breakfast included", "Free cancellation"),
    RoomKey("Deluxe Room", "Room only", "Free cancellation"),
    RoomKey("Deluxe Room", "Breakfast included", "Non-refundable"),
    RoomKey("", "", ""),
])
def test_room_mismatch(seen):
    assert not room_matches(EXPECTED, seen)


def test_empty_expected_label_never_matches():
    assert not room_matches(RoomKey("", "x", "y"), RoomKey("anything", "x", "y"))
