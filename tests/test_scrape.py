"""The scraper's parsers, run against real ufcstats pages saved in tests/fixtures.

The pages were saved with scripts and styles stripped; the parsers only read the
markup. If ufcstats redesigns, the live scrape breaks but these still pass: they
guard against regressions in our own parsing code. To refresh a fixture, save the
page from a browser (ufcstats blocks plain HTTP clients) over the old file.
"""

import datetime
from pathlib import Path

import pytest

from src import scrape

FIXTURES = Path(__file__).parent / "fixtures"


class SavedPage:
    """Stands in for a Playwright page: navigation is a no-op, content is the fixture."""

    def __init__(self, name):
        self.html = (FIXTURES / name).read_text(encoding="utf-8")

    def goto(self, url, **kwargs):
        pass

    def content(self):
        return self.html


def fight(name):
    details = scrape.scrape_bout_details("saved", SavedPage(name))
    stats = scrape.scrape_bout_stats(details["soup"], details["fighter_a_url"],
                                     details["fighter_b_url"])
    return details, stats


def test_knockout_win():
    details, stats = fight("fight_ko.html")
    assert (details["fighter_a_name"], details["fighter_b_name"]) == ("Gable Steveson", "Sean Sharaf")
    assert details["winner"] == "Sean Sharaf" and details["outcome"] == "win"
    assert (details["method"], details["round"], details["time"]) == ("KO/TKO", "1", "0:12")
    assert details["weight_class"] == "Heavyweight" and details["is_title_fight"] is False
    assert len(stats) == 2 and stats[0]["sig_strikes_landed"] <= stats[0]["sig_strikes_attempted"]


def test_draw_names_no_winner():
    details, stats = fight("fight_draw.html")                 # Edgar vs Maynard 2, UFC 125
    assert details["winner"] is None and details["outcome"] == "draw"
    assert (details["method"], details["method_detail"]) == ("Decision", "Split")
    assert details["is_title_fight"] is True and details["weight_class"] == "Lightweight"
    assert stats[0]["sig_strikes_landed"] == 95 and stats[0]["control_time_seconds"] == 85


def test_overturned_result_is_a_no_contest():
    details, _ = fight("fight_nc.html")                       # Vera vs Silva, overturned
    assert details["winner"] is None and details["outcome"] == "nc"
    assert details["method"] == "Overturned"


def test_fighter_profile():
    profile = scrape.scrape_fighter_profile("saved", SavedPage("fighter.html"))  # Joshua Van
    assert profile == {"height": 65, "reach": 65.0, "stance": "Orthodox",
                       "dob": datetime.date(2001, 10, 10)}


def test_completed_card_lists_every_fight():
    urls = scrape.scrape_bout_urls("saved", SavedPage("event_completed.html"))   # UFC 331
    assert len(urls) == 12
    assert "http://www.ufcstats.com/fight-details/023ce4fa7b7b76f9" in urls


def test_upcoming_card_in_billing_order():
    card = scrape.scrape_upcoming_card("saved", SavedPage("event_upcoming.html"))
    assert len(card) == 12
    main = card[0]
    assert (main["fighter_a_name"], main["fighter_b_name"]) == ("Raul Rosas Jr.", "Raoni Barcelos")
    assert main["weight_class"] == "Bantamweight"
    assert all(b["bout_url"].startswith("http://www.ufcstats.com/fight-details/") for b in card)


def test_upcoming_events_listing():
    events = scrape.scrape_upcoming_events(SavedPage("events_upcoming.html"))
    assert events[0]["name"] == "UFC Fight Night: Rosas Jr. vs. Barcelos"
    assert scrape.parse_date(events[0]["date"]) == datetime.date(2026, 9, 26)


@pytest.mark.parametrize("title, division", [
    ("UFC Light Heavyweight Title Bout", "Light Heavyweight"),
    ("Women's Flyweight Bout", "Women's Flyweight"),
    ("UFC 4 Tournament Title Bout", "Open Weight"),
])
def test_weight_class_prefers_the_most_specific_division(title, division):
    assert scrape.parse_weight_class(title) == division
