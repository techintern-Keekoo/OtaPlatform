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
