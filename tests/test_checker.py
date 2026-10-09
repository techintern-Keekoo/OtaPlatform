from rate_parity.checker import candidate, probe, report
from rate_parity.config import load_config
from rate_parity.safety import SafePage
from tests.fakes import FakeElement, FakePage
from tests.test_config_and_storage import EXAMPLE


def test_candidate_strips_prefix_and_skips_unfilled():
    assert candidate("TODO-verify: [data-testid='x']") == "[data-testid='x']"
    assert candidate("TODO: open this room") is None
    assert candidate("TODO-verify: shows TODO inside") is None
    assert candidate(None) is None
    assert candidate(".price") == ".price"


def test_probe_reports_found_missing_and_todo():
    page = SafePage(FakePage({".price": FakeElement("₹ 1,901\n per night")}), "agoda", ["agoda.com"], [], "unused")
    assert probe(page, "TODO-verify: .price") == ("OK", "₹ 1,901 per night")
    assert probe(page, ".gone")[0] == "MISSING"
    assert probe(page, "TODO")[0] == "TODO"


def test_report_never_clicks_steps():
    agoda = load_config(EXAMPLE, check_placeholders=False).sites["agoda"]
    fake = FakePage({})
    rows = report(SafePage(fake, "agoda", ["agoda.com"], agoda.step_selectors(), "unused"), agoda)
    assert any(field.startswith("step[") for _, field, _, _ in rows)
    assert all(status in ("OK", "MISSING", "ERROR", "TODO") for _, _, status, _ in rows)
    assert fake.visited == []  # report itself never navigates or clicks


def test_diagnose_reports_title_counts_and_room_names(tmp_path):
    from rate_parity.checker import diagnose
    fake = FakePage({"[data-testid='room-name']": FakeElement("Premium Cottage")}, url="https://www.agoda.com/x")
    page = SafePage(fake, "agoda", ["agoda.com"], [], tmp_path)
    lines = diagnose(page)
    assert lines[0] == "title: Fake page" and lines[1] == "url: https://www.agoda.com/x"
    assert any(l.strip().startswith("1  [data-testid='room-name']") for l in lines)
    assert lines[-1] == "room names seen: Premium Cottage"
    assert page.save_html("check").endswith(".html")


# --- candidates ("TODO-verify: ") and the tax basis hint -------------------------

GOIBIBO_CARD = ("Standard Garden View Room\nRoom With Free Cancellation\nFree Cancellation before 20 Oct\n"
                "₹ 3,680\n₹ 1,901\n+₹ 245 Taxes & Fees Per Night\nBOOK NOW")


def test_candidate_strips_the_prefix_inside_filled_labels_too():
    assert candidate("TODO-verify: div:has-text('TODO-verify: Std Room')") == "div:has-text('Std Room')"
    assert candidate("TODO-verify:") is None
    assert candidate("TODO-verify: TODO") is None


def test_tax_basis_hint_lists_phrases_found_case_insensitive():
    from rate_parity.checker import tax_basis_hint
    text = "₹ 2,035\nPrice per night   EXCL. Taxes & Fees\n+ Taxes and fees"
    assert tax_basis_hint(text) == ["excl. taxes", "+ taxes", "taxes and fees", "taxes & fees", "price per night"]
    assert tax_basis_hint("Including taxes") == ["including taxes"]
    assert tax_basis_hint("Book now") == []


def test_candidate_site_drops_unfilled_todos_that_would_break_loading():
    from rate_parity.checker import candidate_site
    booking = candidate_site(load_config(EXAMPLE, check_placeholders=False).sites["booking_com"])
    assert booking.captcha_selectors == () and booking.login_wall_selectors == ()
    assert booking.logged_in_marker == "[data-testid='header-profile']"
    assert booking.rooms["standard_garden"].search_price.startswith(".hprt-table tr:has(")


def test_run_config_still_refuses_candidates():
    cfg = load_config(EXAMPLE)  # the checker never changes what a run accepts
    assert not cfg.sites["goibibo"].ready and not cfg.sites["booking_com"].ready


def _goibibo():
    from rate_parity.checker import candidate_site
    site = load_config(EXAMPLE, check_placeholders=False).sites["goibibo"]
    return site, candidate_site(site).rooms["standard_garden"].price_text[0]


def test_report_tests_goibibo_card_candidates(tmp_path):
    site, container = _goibibo()
    page = SafePage(FakePage({container: FakeElement(GOIBIBO_CARD)}), "goibibo", ["goibibo.com"], [], tmp_path)
    rows = {room: (status, detail) for room, field, status, detail in report(page, site)}
    assert rows["standard_garden"] == ("OK", "price 1901 + taxes 245 (free cancellation)")
    assert rows["family_suite"][0] == "MISSING"


def test_report_shows_unfilled_card_container_as_todo(tmp_path):
    import dataclasses
    site, _ = _goibibo()
    room = dataclasses.replace(site.rooms["standard_garden"], price_text=("TODO", r"(?P<price>\d+)"))
    site = dataclasses.replace(site, rooms={"standard_garden": room})
    rows = report(SafePage(FakePage({}), "goibibo", ["goibibo.com"], [], tmp_path), site)
    assert rows[0] == ("standard_garden", "price (card text)", "TODO", "not filled yet")


class _CheckContext:
    """Stands in for the browser: a real NetworkGuard on a fake context, one fake page."""

    def __init__(self, cfg, fake, tmp_path):
        from rate_parity.safety import NetworkGuard
        from tests.fakes import FakeContext
        self.guard = NetworkGuard(cfg.commit_path_patterns, cfg.extra_payment_hosts, cfg.readonly_post_paths)
        self.guard.install(FakeContext())
        self.fake, self.tmp_path, self.sites = fake, tmp_path, []

    def new_safe_page(self, site):
        self.sites.append(site)
        return SafePage(self.fake, site.key, site.allowed_domains, site.step_selectors(), self.tmp_path)

    def close(self):
        pass


def test_check_site_goibibo_loads_and_reads_candidates_and_prints_tax_hint(tmp_path, monkeypatch, capsys):
    import playwright.sync_api

    from rate_parity import browser, checker

    class NoPlaywright:
        def __enter__(self):
            return None

        def __exit__(self, *exc):
            return False

    cfg = load_config(EXAMPLE, check_placeholders=False)
    site, container = _goibibo()
    fake = FakePage({container: FakeElement(GOIBIBO_CARD),
                     "[data-testid='rmType__roomName']": FakeElement("Standard Garden View Room"),
                     "body": FakeElement("Zen Manali " + GOIBIBO_CARD)}, url="https://www.goibibo.com/hotels/x")
    context = _CheckContext(cfg, fake, tmp_path)
    monkeypatch.setattr(playwright.sync_api, "sync_playwright", NoPlaywright)
    monkeypatch.setattr(browser, "open_quick_context", lambda pw, cfg: context)

    assert checker.check_site(cfg, "goibibo", days=1) == 0
    out = capsys.readouterr().out
    assert "NOTE:" not in out  # the candidate wait_for was used, so the room list "loaded"
    assert "OK       standard_garden    price (card text)        price 1901 + taxes 245" in out
    assert "tax basis hint (config price_includes_tax: false): page says 'taxes & fees'" in out
    assert len(fake.visited) == 1 and "goibibo.com/hotels/hotel-details/" in fake.visited[0]
    assert context.sites == [cfg.sites["goibibo"]]  # the page's click allow-list is the config's, unchanged
