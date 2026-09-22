"""Scraping ufcstats.com into the database.

Three layers, top to bottom: page parsers that turn one ufcstats page into
plain dicts, the inserts that store them, and `run()`, the incremental scrape
the refresh calls. Pages are fetched with a headless Chromium (Playwright).

The `events` table is the checkpoint: an event is re-fetched until every one of
its bouts has been stored, and never again after that. It used to be a file in
the repository root, until a branch merge deleted it and the next run re-scraped
all of UFC history; state that decides what gets scraped now lives in the database.

Run:
    python -m src.scrape              # scrape completed events not yet stored
    python -m src.scrape --dry-run    # list them without fetching
"""

import os
from contextlib import contextmanager
from datetime import datetime

from bs4 import BeautifulSoup

from src.db import get_connection

EVENTS_URL = "http://www.ufcstats.com/statistics/events/completed?page=all"
UPCOMING_URL = "http://www.ufcstats.com/statistics/events/upcoming?page=all"
FAILED_BOUTS_LOG = "logs/failed_bouts.log"
TIMEOUT_MS = 60000

# Longest / most specific first: "Heavyweight" is a substring of
# "Light Heavyweight", and "Flyweight" of "Women's Flyweight".
WEIGHT_CLASSES = [
    "Women's Strawweight", "Women's Flyweight", "Women's Bantamweight",
    "Women's Featherweight", "Women's Lightweight",
    "Light Heavyweight", "Super Heavyweight",
    "Strawweight", "Flyweight", "Bantamweight", "Featherweight",
    "Lightweight", "Welterweight", "Middleweight", "Heavyweight",
    "Catch Weight", "Open Weight",
]


# --- page parsers -----------------------------------------------------------

def _soup(page, url):
    page.goto(url, wait_until="networkidle", timeout=TIMEOUT_MS)
    return BeautifulSoup(page.content(), "html.parser")


def parse_date(text):
    try:
        return datetime.strptime(text.strip(), "%B %d, %Y").date()
    except (ValueError, AttributeError):
        return None


def parse_weight_class(title_text):
    """Match the title against known divisions; stripping words out of it left
    debris like "4 Tournament". Anything unrecognised is an open-weight bout."""
    if not title_text:
        return None
    lowered = title_text.lower()
    return next((w for w in WEIGHT_CLASSES if w.lower() in lowered), "Open Weight")


def _event_list(page, url):
    events = []
    for row in _soup(page, url).select("tr.b-statistics__table-row"):
        name = row.select_one("a.b-link")
        when = row.select_one("span.b-statistics__date")
        if name and when:
            events.append({"name": name.get_text(strip=True),
                           "date": when.get_text(strip=True), "url": name["href"]})
    return events


def scrape_events(page):
    """Every completed event, newest first."""
    return _event_list(page, EVENTS_URL)


def scrape_upcoming_events(page):
    """Scheduled events: same markup as the completed listing, different URL."""
    return _event_list(page, UPCOMING_URL)


def scrape_bout_urls(event_url, page):
    rows = _soup(page, event_url).select("tr.b-fight-details__table-row")
    return [row["data-link"] for row in rows if row.get("data-link")]


def scrape_upcoming_card(event_url, page):
    """Matchups on a scheduled card, main event first: fight URL, both fighters, division."""
    card = []
    for row in _soup(page, event_url).select("tr.b-fight-details__table-row"):
        bout_url = row.get("data-link")
        fighters = [a for a in row.select("a") if "fighter-details" in (a.get("href") or "")]
        cells = row.select("td")
        if not bout_url or len(fighters) < 2 or len(cells) < 7:
            continue
        card.append({
            "bout_url": bout_url,
            "fighter_a_url": fighters[0]["href"], "fighter_a_name": fighters[0].get_text(strip=True),
            "fighter_b_url": fighters[1]["href"], "fighter_b_name": fighters[1].get_text(strip=True),
            "weight_class": parse_weight_class(cells[6].get_text(" ", strip=True)),
        })
    return card


def scrape_bout_details(bout_url, page):
    """One fight's result; `soup` is returned so the stats table needs no second fetch."""
    soup = _soup(page, bout_url)
    people = soup.select("a.b-link.b-fight-details__person-link")
    a_name, b_name = people[0].get_text(strip=True), people[1].get_text(strip=True)

    # The status tag reads W/L, but D for a draw and NC for a no contest, so
    # only an explicit "W" names a winner.
    statuses = [t.get_text(strip=True) for t in soup.select("i.b-fight-details__person-status")]
    if statuses and statuses[0] == "W":
        winner = a_name
    elif len(statuses) > 1 and statuses[1] == "W":
        winner = b_name
    else:
        winner = None
    outcome = "win" if winner else ("nc" if "NC" in statuses else "draw")

    details = soup.select_one("div.b-fight-details__content")
    method = details.select_one("i.b-fight-details__text-item_first i:not([class])").get_text(strip=True)
    items = details.select("i.b-fight-details__text-item")
    title = soup.select_one("i.b-fight-details__fight-title")
    title_text = title.get_text(strip=True) if title else ""
    method_type, _, method_detail = (part.strip() for part in method.partition(" - "))

    return {
        "fighter_a_name": a_name, "fighter_a_url": people[0]["href"],
        "fighter_b_name": b_name, "fighter_b_url": people[1]["href"],
        "winner": winner, "outcome": outcome,
        "method": method_type, "method_detail": method_detail or None,
        "round": items[0].get_text(strip=True).replace("Round:", "").strip(),
        "time": items[1].get_text(strip=True).replace("Time:", "").strip(),
        "weight_class": parse_weight_class(title_text) if title else None,
        "is_title_fight": "title" in title_text.lower(),
        "soup": soup,
    }


def scrape_bout_stats(soup, fighter_a_url, fighter_b_url):
    """Fight-total striking and grappling for both corners, or None if absent."""
    sections = soup.select("section.b-fight-details__section")
    table = sections[1].select_one("table") if len(sections) > 1 else None
    if not table:
        return None
    row = next((cols for r in table.select("tr.b-fight-details__table-row")
                if (cols := r.select("td.b-fight-details__table-col"))), None)
    if not row:
        return None

    def cell(col, i):
        ps = row[col].select("p")
        return ps[i].get_text(strip=True) if len(ps) > i else ""

    def count(col, i):
        text = cell(col, i)
        return int(text) if text.isdigit() else 0

    def landed_of(col, i):
        text = cell(col, i)
        if "of" not in text:
            return 0, 0
        landed, attempted = text.split("of")
        return int(landed.strip()), int(attempted.strip())

    def seconds(col, i):
        text = cell(col, i)
        if ":" not in text:
            return 0
        minutes, secs = text.split(":")
        return int(minutes) * 60 + int(secs)

    stats = []
    for i, url in enumerate((fighter_a_url, fighter_b_url)):
        sig = landed_of(2, i)
        total = landed_of(4, i)
        td = landed_of(5, i)
        stats.append({
            "fighter_url": url, "knockdowns": count(1, i),
            "sig_strikes_landed": sig[0], "sig_strikes_attempted": sig[1],
            "total_strikes_landed": total[0], "total_strikes_attempted": total[1],
            "takedowns_landed": td[0], "takedowns_attempted": td[1],
            "submission_attempts": count(7, i), "control_time_seconds": seconds(9, i),
        })
    return stats


def scrape_fighter_profile(fighter_url, page):
    """Date of birth, height and reach in inches, and stance, each None if unlisted."""
    items = [t.get_text(strip=True) for t in
             _soup(page, fighter_url).select("li.b-list__box-list-item")]

    def field(label):
        value = next((t[len(label):].strip() for t in items if t.startswith(label)), None)
        return None if value in (None, "", "--") else value

    def height(text):
        try:
            feet, inches = text.replace('"', "").split("'")
            return int(feet.strip()) * 12 + int(inches.strip())
        except (ValueError, AttributeError):
            return None

    def reach(text):
        try:
            return float(text.replace('"', "").strip())
        except (ValueError, AttributeError):
            return None

    def dob(text):
        try:
            return datetime.strptime(text, "%b %d, %Y").date()
        except (ValueError, TypeError):
            return None

    return {"height": height(field("Height:")), "reach": reach(field("Reach:")),
            "stance": field("STANCE:"), "dob": dob(field("DOB:"))}


# --- storage ----------------------------------------------------------------

@contextmanager
def _cursor(conn=None):
    """A cursor on the caller's connection, or on a short-lived one of its own."""
    owned = conn is None
    if owned:
        conn = get_connection()
    cur = conn.cursor()
    try:
        yield cur
        if owned:
            conn.commit()
    finally:
        cur.close()
        if owned:
            conn.close()


def completed_urls(conn=None):
    """Event URLs scraped in full. Partial events are left out so they retry."""
    with _cursor(conn) as cur:
        cur.execute("SELECT url FROM events WHERE status = 'complete';")
        return {row[0] for row in cur.fetchall()}


def record_event(url, name, event_date, bouts_scraped, complete, conn=None):
    """Upsert one event's outcome; attempts counts how often it has been tried."""
    with _cursor(conn) as cur:
        cur.execute("""
            INSERT INTO events (url, name, date, status, bouts_scraped)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (url) DO UPDATE SET
                name = EXCLUDED.name, date = EXCLUDED.date, status = EXCLUDED.status,
                bouts_scraped = EXCLUDED.bouts_scraped,
                attempts = events.attempts + 1, scraped_at = now();
        """, (url, name, event_date, "complete" if complete else "partial", bouts_scraped))


def _fighter_id(url, name, page, conn):
    """Stored id for a fighter, scraping and inserting their profile on first sight."""
    with _cursor(conn) as cur:
        cur.execute("SELECT id FROM fighters WHERE url = %s;", (url,))
        row = cur.fetchone()
        if row:
            return row[0]
        p = scrape_fighter_profile(url, page)
        cur.execute("""
            INSERT INTO fighters (url, name, dob, height, reach, stance)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (url) DO UPDATE SET url = EXCLUDED.url
            RETURNING id;
        """, (url, name, p["dob"], p["height"], p["reach"], p["stance"]))
        return cur.fetchone()[0]


def _store_bout(details, event_date, a_id, b_id, conn):
    winner_id = None if details["winner"] is None else (
        a_id if details["winner"] == details["fighter_a_name"] else b_id)
    with _cursor(conn) as cur:
        cur.execute("""
            INSERT INTO bouts (date, fighter_a_id, fighter_b_id, winner_id, method, method_detail,
                               round, time, weight_class, is_title_fight, outcome)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (date, fighter_a_id, fighter_b_id)
            DO UPDATE SET date = EXCLUDED.date
            RETURNING id;
        """, (event_date, a_id, b_id, winner_id, details["method"], details["method_detail"],
              details["round"], details["time"], details["weight_class"],
              details["is_title_fight"], details["outcome"]))
        bout_id = cur.fetchone()[0]

        stats = scrape_bout_stats(details["soup"], details["fighter_a_url"], details["fighter_b_url"])
        for fighter_id, s in zip((a_id, b_id), stats or ()):
            cur.execute("""
                INSERT INTO bout_stats (bout_id, fighter_id, sig_strikes_landed,
                    sig_strikes_attempted, total_strikes_landed, total_strikes_attempted,
                    takedowns_landed, takedowns_attempted, submission_attempts, knockdowns,
                    control_time_seconds)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (bout_id, fighter_id) DO NOTHING;
            """, (bout_id, fighter_id, s["sig_strikes_landed"], s["sig_strikes_attempted"],
                  s["total_strikes_landed"], s["total_strikes_attempted"], s["takedowns_landed"],
                  s["takedowns_attempted"], s["submission_attempts"], s["knockdowns"],
                  s["control_time_seconds"]))


# --- the incremental scrape ---------------------------------------------------

def _log_failed_bout(event_name, bout_url, error):
    """Record an unscrapeable bout; its event stays partial so the next run retries."""
    os.makedirs(os.path.dirname(FAILED_BOUTS_LOG), exist_ok=True)
    with open(FAILED_BOUTS_LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat()}\t{event_name}\t{bout_url}\t{error}\n")


def scrape_event(event, page, log=print):
    """Every bout on one completed card; the event is complete only if none failed."""
    event_date = parse_date(event["date"])
    stored = failed = 0
    conn = get_connection()
    try:
        for bout_url in scrape_bout_urls(event["url"], page):
            try:
                details = scrape_bout_details(bout_url, page)
                a_id = _fighter_id(details["fighter_a_url"], details["fighter_a_name"], page, conn)
                b_id = _fighter_id(details["fighter_b_url"], details["fighter_b_name"], page, conn)
                _store_bout(details, event_date, a_id, b_id, conn)
                conn.commit()
                stored += 1
                log(f"  + {details['fighter_a_name']} vs {details['fighter_b_name']}")
            except Exception as exc:
                conn.rollback()
                failed += 1
                log(f"  x {bout_url}: {type(exc).__name__}: {exc}")
                _log_failed_bout(event["name"], bout_url, f"{type(exc).__name__}: {exc}")
    finally:
        conn.close()
    record_event(event["url"], event["name"], event_date, stored,
                 complete=failed == 0 and stored > 0)
    if failed or not stored:
        log(f"  {event['name']} left partial ({stored} stored, {failed} failed); retried next run")
    return stored, failed


def run(dry_run=False, log=print):
    """Scrape every completed event not yet recorded as complete, oldest first."""
    from playwright.sync_api import sync_playwright

    done = completed_urls()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            events = scrape_events(page)
            upcoming = scrape_upcoming_events(page)
            pending = [e for e in events if e["url"] not in done]
            log(f"Site lists {len(events)} completed events; {len(pending)} not yet scraped.")
            log(f"Next scheduled card: {upcoming[0]['date']} - {upcoming[0]['name']}"
                if upcoming else "No upcoming events listed.")
            if dry_run:
                for e in pending:
                    log(f"  would scrape: {e['date']} - {e['name']}")
                return {"new_events": 0, "pending": len(pending), "upcoming": len(upcoming)}

            # Oldest first, so an interrupted backlog leaves history contiguous.
            for event in reversed(pending):
                log(f"Scraping {event['name']} ({event['date']})")
                scrape_event(event, page, log)
        finally:
            browser.close()

    remaining = [e for e in pending if e["url"] not in completed_urls()]
    return {"new_events": len(pending) - len(remaining), "still_pending": len(remaining),
            "upcoming": len(upcoming)}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Scrape new completed UFC events.")
    parser.add_argument("--dry-run", action="store_true", help="list pending events only")
    run(dry_run=parser.parse_args().dry_run)
