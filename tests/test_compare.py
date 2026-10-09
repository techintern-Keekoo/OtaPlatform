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


ZERO = D("0")


def test_suspect_when_ota_not_above_zen():
    # Keekoo's rule: Zen must be cheaper, so an equal OTA price is already suspect
    assert is_suspect(D("99"), D("100"))
    assert is_suspect(D("100"), D("100"))
    assert not is_suspect(D("101"), D("100"))


def test_suspect_margin_widens_the_net():
    assert is_suspect(D("101"), D("100"), D("1"))      # only 1% above Zen
    assert not is_suspect(D("101.5"), D("100"), D("1"))


def test_ota_cheaper_than_zen_is_violation():
    assert decide_status(D("9800"), D("10000"), ZERO) is Status.VIOLATION


def test_ota_equal_to_zen_is_violation():
    assert decide_status(D("10000"), D("10000"), ZERO) is Status.VIOLATION


def test_ota_above_zen_is_fine():
    assert decide_status(D("10001"), D("10000"), ZERO) is Status.FALSE_ALARM


def test_margin_flags_ota_only_slightly_above_zen():
    assert decide_status(D("10100"), D("10000"), TOL) is Status.VIOLATION   # +1.00%, not more than 1%
    assert decide_status(D("10101"), D("10000"), TOL) is Status.FALSE_ALARM  # +1.01%


def test_unrounded_gap_is_used():
    # +1.004% rounds to +1.00 but is above a 1% margin, so it is fine
    assert decide_status(D("10100.4"), D("10000"), TOL) is Status.FALSE_ALARM


def test_suspect_fine_at_checkout_is_false_alarm():
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
