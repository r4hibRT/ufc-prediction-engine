from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

EVENTS_URL = "http://www.ufcstats.com/statistics/events/completed?page=all"
UPCOMING_URL = "http://www.ufcstats.com/statistics/events/upcoming?page=all"


def _parse_event_rows(page, url):
    page.goto(url, wait_until="networkidle", timeout=60000)
    html = page.content()
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("tr.b-statistics__table-row")

    events = []
    for row in rows:
        name_tag = row.select_one("a.b-link")
        date_tag = row.select_one("span.b-statistics__date")

        if not name_tag or not date_tag:
            continue

        events.append({
            "name": name_tag.get_text(strip=True),
            "date": date_tag.get_text(strip=True),
            "url": name_tag["href"]
        })

    return events


def scrape_events(page):
    return _parse_event_rows(page, EVENTS_URL)


def scrape_upcoming_events(page):
    """Scheduled events that have not happened yet.

    Same markup as the completed listing, different URL. Bout pages for these
    events have fighters and a weight class but no result, so they feed
    forward predictions rather than the ratings history.
    """
    return _parse_event_rows(page, UPCOMING_URL)


if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        completed = scrape_events(page)
        upcoming = scrape_upcoming_events(page)

        print(f"Completed events: {len(completed)}")
        if completed:
            print(f"  most recent: {completed[0]['date']} - {completed[0]['name']}")
        print(f"Upcoming events: {len(upcoming)}")
        for e in upcoming[:5]:
            print(f"  {e['date']} - {e['name']}")

        browser.close()
