import pytest

from rate_parity import safety
from rate_parity.safety import (
    DEFAULT_COMMIT_PATH_PATTERNS, NetworkGuard, SafePage, SafetyViolation,
    SelectorMissing, is_denied_label, self_check,
)
from tests.fakes import FakeContext, FakeElement, FakePage, FakeRoute

STEP = "#select-room"
NEXT = "#next"


def make_page(elements=None, redirect_to=None, tmp_path=None, steps=(STEP, NEXT)):
    fake = FakePage(elements, url="https://www.booking.com/hotel/x.html", redirect_to=redirect_to)
    safe = SafePage(fake, "booking_com", ["booking.com"], steps, tmp_path or "screenshots")
    return fake, safe


def test_booking_is_never_allowed():
    assert safety.BOOKING_ALLOWED is False


@pytest.mark.parametrize("label", [
    "Pay Now", "pay", "Payment", "Confirm", "Confirm booking", "Complete booking",
    "Complete your booking", "Place order", "Book now and pay", "Book & pay",
    "Book now & pay", "Submit", "Purchase", "Checkout now", "Proceed to pay",
    "Add card", "Pay via UPI", "Wallet", "PAY  NOW", "confirmation", "Payable at hotel",
])
def test_deny_labels(label):
    assert is_denied_label(label)


@pytest.mark.parametrize("label", [
    "Select room", "I'll reserve", "Reserve", "Continue", "Next: Final details",
    "See availability", "Show prices", "", None,
])
def test_allowed_labels(label):
    assert not is_denied_label(label)


def test_click_configured_safe_step(tmp_path):
    button = FakeElement("I'll reserve")
    _, safe = make_page({STEP: button}, tmp_path=tmp_path)
    safe.click_step(STEP)
    assert button.clicked == 1


def test_refuses_unconfigured_selector():
    button = FakeElement("Continue")
    _, safe = make_page({"#other": button})
    with pytest.raises(SafetyViolation):
        safe.click_step("#other")
    assert button.clicked == 0


@pytest.mark.parametrize("element", [
    FakeElement("Pay now"),
    FakeElement("", {"aria-label": "Complete booking"}),
    FakeElement("", {"value": "Submit"}),
    FakeElement("→", {"title": "Proceed to pay"}),
])
def test_refuses_denied_element_even_if_configured(element):
    _, safe = make_page({STEP: element})
    with pytest.raises(SafetyViolation):
        safe.click_step(STEP)
    assert element.clicked == 0


def test_missing_step_raises_selector_missing():
    _, safe = make_page({})
    with pytest.raises(SelectorMissing):
        safe.click_step(STEP)


def test_click_that_lands_off_allowlist_is_stopped():
    button = FakeElement("Continue")
    fake, safe = make_page({STEP: button})
    fake.url = "https://pay.example-gateway.com/checkout"
    with pytest.raises(SafetyViolation):
        safe.click_step(STEP)


@pytest.mark.parametrize("url", [
    "http://www.booking.com/hotel/x.html",
    "https://evil.com/hotel",
    "https://booking.com.evil.com/",
    "https://www.booking.com@evil.com/",
    "javascript:alert(1)",
])
def test_goto_refuses_bad_urls(url):
    fake, safe = make_page()
    with pytest.raises(SafetyViolation):
        safe.goto(url)
    assert fake.visited == []


def test_goto_allows_subdomain():
    fake, safe = make_page()
    safe.goto("https://secure.booking.com/book.html")
    assert fake.visited == ["https://secure.booking.com/book.html"]


def test_goto_redirect_off_allowlist_is_stopped():
    _, safe = make_page(redirect_to="https://checkout.razorpay.com/")
    with pytest.raises(SafetyViolation):
        safe.goto("https://www.booking.com/hotel/x.html")


def test_safe_page_exposes_no_input_methods():
    _, safe = make_page()
    for name in ("fill", "type", "press", "keyboard", "evaluate", "check", "select_option", "mouse"):
        assert not hasattr(safe, name)


def test_read_text_and_screenshot(tmp_path):
    fake, safe = make_page({"#total": FakeElement("₹ 7,906")}, tmp_path=tmp_path)
    assert safe.read_text("#total") == "₹ 7,906"
    assert safe.read_text("#absent") is None
    path = safe.screenshot("room 1/../x")
    assert path.startswith(str(tmp_path))
    assert "/../" not in path[len(str(tmp_path)):]
    assert fake.screenshots == [path]


def make_guard():
    return NetworkGuard(DEFAULT_COMMIT_PATH_PATTERNS)


@pytest.mark.parametrize("url", [
    "https://checkout.razorpay.com/v1/checkout.js",
    "https://secure.payu.in/_payment",
    "https://securegw.paytm.in/theia",
    "https://js.stripe.com/v3",
    "https://checkoutshopper-live.adyen.com/x",
    "https://api.juspay.in/x",
    "https://secure.ccavenue.com/x",
    "https://pgi.billdesk.com/x",
    "https://api.cashfree.com/x",
    "https://api.checkout.com/payments",
    "https://payments.braintree-api.com/x",
    "https://www.paypal.com/x",
])
def test_guard_blocks_payment_gateways_even_for_get(url):
    assert make_guard().block_reason(url, "GET")


@pytest.mark.parametrize("url", [
    "https://secure.booking.com/book.html",
    "https://www.example.com/api/reservation/create",
    "https://www.example.com/checkout/complete",
    "https://www.example.com/booking/confirm",
    "https://www.example.com/payment/init",
])
def test_guard_blocks_commit_posts(url):
    assert make_guard().block_reason(url, "POST")


def test_guard_allows_ordinary_traffic():
    guard = make_guard()
    assert guard.block_reason("https://www.booking.com/hotel/in/x.html?checkin=2026-10-15", "GET") is None
    assert guard.block_reason("https://www.booking.com/dml/graphql", "POST") is None


def test_extra_payment_hosts_add_to_builtins():
    guard = NetworkGuard(DEFAULT_COMMIT_PATH_PATTERNS, ["phonepe"])
    assert guard.block_reason("https://mercury.phonepe.com/x", "GET")
    assert guard.block_reason("https://api.razorpay.com/x", "GET")


def test_handle_aborts_and_logs(caplog):
    guard = make_guard()
    blocked = FakeRoute("https://api.razorpay.com/v1/payments?card=4111", "POST")
    allowed = FakeRoute("https://www.booking.com/hotel/x.html")
    guard.handle(blocked)
    guard.handle(allowed)
    assert blocked.result == "aborted"
    assert allowed.result == "continued"
    assert guard.aborted == ["POST https://api.razorpay.com/v1/payments"]
    assert "4111" not in caplog.text
    assert "NETWORK GUARD aborted" in caplog.text


def test_install_registers_catch_all_route():
    guard, context = make_guard(), FakeContext()
    guard.install(context)
    assert context.routes == [("**/*", guard.handle)]
    assert guard.installed


def test_self_check_refuses_without_guard():
    with pytest.raises(SafetyViolation):
        self_check(None)
    with pytest.raises(SafetyViolation):
        self_check(make_guard())  # created but never installed


def test_self_check_passes_with_installed_guard():
    guard = make_guard()
    guard.install(FakeContext())
    self_check(guard)


def test_self_check_refuses_if_booking_allowed(monkeypatch):
    guard = make_guard()
    guard.install(FakeContext())
    monkeypatch.setattr(safety, "BOOKING_ALLOWED", True)
    with pytest.raises(SafetyViolation):
        self_check(guard)


def test_click_refused_if_booking_allowed(monkeypatch):
    button = FakeElement("Continue")
    _, safe = make_page({STEP: button})
    monkeypatch.setattr(safety, "BOOKING_ALLOWED", True)
    with pytest.raises(SafetyViolation):
        safe.click_step(STEP)
    assert button.clicked == 0


# --- reviewer hardening -------------------------------------------------------

@pytest.mark.parametrize("label", [
    "P​ay now",            # zero-width space inside the word
    "Pay­ment",            # soft hyphen
    "Ｐａｙ now",   # full-width "Pay"
    "Complete reservation", "Finish booking", "Place your order",
    "अभी भुगतान करें", "बुकिंग की पुष्टि करें", "पेमेंट करें",
])
def test_deny_labels_resist_evasion(label):
    assert is_denied_label(label)


@pytest.mark.parametrize("element", [
    FakeElement("Continue", clickable_ancestor=FakeElement("Continue ₹ 9,000 Pay now")),
    FakeElement("", {"alt": "Pay now"}),                     # <input type=image alt=...>
    FakeElement("", {"name": "confirm"}),
    FakeElement("", {"aria-labelledby": "lbl"}),             # label text lives elsewhere
])
def test_refuses_pay_label_on_ancestor_or_hidden_attribute(element):
    _, safe = make_page({STEP: element, '[id="lbl"]': FakeElement("Confirm and pay")})
    with pytest.raises(SafetyViolation):
        safe.click_step(STEP)
    assert element.clicked == 0


def test_refuses_element_with_no_readable_label():
    icon_only = FakeElement("")
    _, safe = make_page({STEP: icon_only})
    with pytest.raises(SafetyViolation):
        safe.click_step(STEP)
    assert icon_only.clicked == 0


def test_refuses_unsafe_aria_labelledby_reference():
    element = FakeElement("Continue", {"aria-labelledby": 'x"],[id=y'})
    _, safe = make_page({STEP: element})
    with pytest.raises(SafetyViolation):
        safe.click_step(STEP)
    assert element.clicked == 0


def test_safe_ancestor_still_clickable(tmp_path):
    element = FakeElement("Select", clickable_ancestor=FakeElement("Select room"))
    _, safe = make_page({STEP: element}, tmp_path=tmp_path)
    safe.click_step(STEP)
    assert element.clicked == 1


@pytest.mark.parametrize("body", [
    '{"operationName":"CreateBooking","variables":{}}',
    '{"query":"mutation { confirm_reservation(id: 1) }"}',
    "action=place-order&hotel=1",
])
def test_guard_blocks_commit_bodies_on_generic_endpoints(body):
    assert make_guard().block_reason("https://www.booking.com/dml/graphql", "POST", body)


def test_guard_allows_search_bodies_and_ignores_get_bodies():
    guard = make_guard()
    assert guard.block_reason("https://www.booking.com/dml/graphql", "POST", '{"operationName":"SearchRooms"}') is None
    assert guard.block_reason("https://www.booking.com/x", "GET", "createbooking") is None


def test_handle_reads_request_body():
    route = FakeRoute("https://www.example.com/graphql", "POST", '{"operationName":"CreateBooking"}')
    make_guard().handle(route)
    assert route.result == "aborted"


def test_safe_page_closes_popups(tmp_path):
    page = FakePage()
    SafePage(page, "booking_com", ["booking.com"], [], tmp_path)
    popup = FakePage()
    page.handlers["popup"](popup)
    assert popup.closed


def test_readonly_post_path_is_exact_and_still_body_checked():
    guard = NetworkGuard(DEFAULT_COMMIT_PATH_PATTERNS, (), ["/booking/multibox.php"])
    search = "action=destination_list&checkIn=22-10-2026&isroomsearch=1"
    assert guard.block_reason("https://book.zenhotels.in/booking/multibox.php", "POST", search) is None
    # exact match only: neighbours and sub-paths stay blocked
    assert guard.block_reason("https://book.zenhotels.in/booking/multibox.php/x", "POST", search)
    assert guard.block_reason("https://book.zenhotels.in/booking/book-rooms", "POST", search)
    # a booking body on the exempt path is still aborted
    assert guard.block_reason("https://book.zenhotels.in/booking/multibox.php", "POST", "action=createBooking")
    # payment hosts are never exempt
    assert NetworkGuard((), (), ["/v1/payments"]).block_reason("https://api.razorpay.com/v1/payments", "POST")


def test_scroll_through_only_uses_the_mouse_wheel(tmp_path):
    fake = FakePage()
    SafePage(fake, "agoda", ["agoda.com"], [], tmp_path).scroll_through(steps=3)
    assert fake.mouse.wheel_calls == 3 and fake.visited == []


@pytest.mark.parametrize("label", ["NEXT: FINAL STEP", "Next step", "Continue to payment", "Go to payment"])
def test_agoda_booking_form_buttons_are_refused(label):
    # Seen live on Agoda's Booking Form (8 Oct 2026): guest details were pre-filled
    # for a logged-in user and "NEXT: FINAL STEP" leads to payment.
    assert safety.is_denied_label(label)


def test_scroll_until_stable_keeps_going_while_rooms_appear(tmp_path):
    from tests.fakes import FakeLocator

    class GrowingPage(FakePage):
        """Renders one more room box per scroll, up to 5 (like Agoda)."""
        def locator(self, selector):
            loc = FakeLocator(FakeElement("room"))
            loc.count = lambda: min(1 + self.mouse.wheel_calls, 5)
            return loc

    fake = GrowingPage()
    found = SafePage(fake, "agoda", ["agoda.com"], [], tmp_path).scroll_until_stable(".room")
    assert found == 5
    assert fake.mouse.wheel_calls == 7  # 4 scrolls to load rooms 2-5, then 3 with nothing new
