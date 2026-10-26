"""First-visit pause skip, JPEG screenshots, CSV column change, old evidence cleanup."""
import csv
import os
import time

import pytest

from rate_parity import browser, runner
from rate_parity.config import ConfigError, load_config
from rate_parity.models import COLUMNS
from rate_parity.safety import SafePage
from rate_parity.storage import csv_store
from tests.fakes import FakePage

EXAMPLE = "config.example.yaml"


# --- polite pause: only on a repeat visit to the same site --------------------

class FakeContext:
    def new_page(self):
        return FakePage(url="https://www.agoda.com/")

    def route(self, *args):
        pass

    def set_default_timeout(self, ms):
        pass


def test_pause_skipped_on_first_visit_only(monkeypatch):
    cfg = load_config(EXAMPLE, check_placeholders=False)
    sleeps = []
    monkeypatch.setattr(browser.time, "sleep", sleeps.append)
    monkeypatch.setattr(browser, "_visited_sites", set())
    context = browser.GuardedContext(FakeContext(), cfg, "logged_out", [])
    agoda, mmt = cfg.sites["agoda"], cfg.sites["makemytrip"]
    context.new_safe_page(agoda).goto("https://www.agoda.com/x")
    context.new_safe_page(mmt).goto("https://www.makemytrip.com/x")
    assert sleeps == []  # two different sites, first visit each: no wait
    context.new_safe_page(agoda).goto("https://www.agoda.com/y")
    low, high = cfg.browser.delay_seconds
    assert len(sleeps) == 1 and low <= sleeps[0] <= high  # second Agoda visit: polite pause


# --- screenshots ----------------------------------------------------------------

def test_screenshot_is_jpeg(tmp_path):
    page = FakePage(url="https://www.agoda.com/")
    path = SafePage(page, "agoda", ["agoda.com"], [], tmp_path).screenshot("list")
    assert path.endswith(".jpg") and os.path.getsize(path) > 0
    assert page.screenshots == [path]


class EmptyJpegPage(FakePage):
    """Chrome writes an empty JPEG for pages taller than 65,535 px, with no error."""

    def screenshot(self, path=None, full_page=False, type=None, quality=None):
        self.screenshots.append(path)
        open(path, "wb").write(b"" if type == "jpeg" else b"png")


def test_empty_jpeg_falls_back_to_png(tmp_path):
    page = EmptyJpegPage(url="https://www.agoda.com/")
    path = SafePage(page, "agoda", ["agoda.com"], [], tmp_path).screenshot("list")
    assert path.endswith(".png") and os.path.getsize(path) > 0
    assert not any(f.suffix == ".jpg" for f in tmp_path.iterdir())  # the empty file is not left behind


class FailingJpegPage(FakePage):
    def screenshot(self, path=None, full_page=False, type=None, quality=None):
        if type == "jpeg":
            raise RuntimeError("jpeg not supported")
        open(path, "wb").write(b"png")


def test_failed_jpeg_falls_back_to_png(tmp_path):
    path = SafePage(FailingJpegPage(url="https://x.agoda.com/"), "agoda", ["agoda.com"], [], tmp_path).screenshot("l")
    assert path.endswith(".png")


# --- CSV whose columns changed between versions ---------------------------------

def test_csv_with_old_columns_is_kept_and_a_new_file_started(tmp_path):
    target = tmp_path / "rate_parity.csv"
    old_header = [c for c in COLUMNS if c != "ota_offer"]
    with open(target, "w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows([old_header, ["old"] * len(old_header)])
    csv_store.append([["new"] * len(COLUMNS)], target)
    rows = list(csv.reader(open(target, encoding="utf-8")))
    assert rows == [list(COLUMNS), ["new"] * len(COLUMNS)]
    (old,) = tmp_path.glob("rate_parity_old_columns_*.csv")
    assert list(csv.reader(open(old, encoding="utf-8")))[0] == old_header  # nothing lost


def test_csv_with_current_columns_is_appended(tmp_path):
    target = tmp_path / "rate_parity.csv"
    csv_store.append([["a"] * len(COLUMNS)], target)
    csv_store.append([["b"] * len(COLUMNS)], target)
    assert len(list(csv.reader(open(target, encoding="utf-8")))) == 3
    assert list(tmp_path.iterdir()) == [target]


def test_csv_in_another_encoding_is_kept_not_crashed(tmp_path):
    target = tmp_path / "rate_parity.csv"
    target.write_bytes("ota,price\n₹ 1\n".encode("utf-16"))
    csv_store.append([["x"] * len(COLUMNS)], target)
    assert next(csv.reader(open(target, encoding="utf-8"))) == list(COLUMNS)
    assert len(list(tmp_path.glob("rate_parity_old_columns_*.csv"))) == 1


# --- old evidence cleanup -------------------------------------------------------

def _file(folder, name, age_days):
    path = folder / name
    path.write_text("x")
    stamp = time.time() - age_days * 86400
    os.utime(path, (stamp, stamp))
    return path


def test_old_evidence_is_deleted_and_nothing_else(tmp_path):
    old_shot = _file(tmp_path, "agoda_list_2026-10-08_20261008-150620.jpg", 40)
    old_png = _file(tmp_path, "makemytrip_list_2026-10-08_20261008-165232.png", 40)
    old_page = _file(tmp_path, "website_2026-10-08_20261008-150523.html", 40)
    new_shot = _file(tmp_path, "agoda_list_2026-10-25_20261025-100000.jpg", 2)
    checker_page = _file(tmp_path, "agoda_check_agoda.html", 40)  # no time stamp: overwritten each check
    not_ours = _file(tmp_path, "requirements.txt", 400)
    assert runner.clean_old_evidence(tmp_path, 30) == 3
    assert not old_shot.exists() and not old_png.exists() and not old_page.exists()
    assert new_shot.exists() and checker_page.exists() and not_ours.exists()


def test_keep_zero_days_keeps_everything(tmp_path):
    old = _file(tmp_path, "agoda_list_2026-10-08_20261008-150620.jpg", 400)
    assert runner.clean_old_evidence(tmp_path, 0) == 0 and old.exists()


def test_missing_evidence_folder_is_fine(tmp_path):
    assert runner.clean_old_evidence(tmp_path / "nope", 30) == 0


def test_keep_evidence_days_from_config(tmp_path):
    assert load_config(EXAMPLE, check_placeholders=False).keep_evidence_days == 30
    bad = tmp_path / "c.yaml"
    bad.write_text(open(EXAMPLE, encoding="utf-8").read().replace("keep_evidence_days: 30", "keep_evidence_days: -1"),
                   encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(bad, check_placeholders=False)
