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
