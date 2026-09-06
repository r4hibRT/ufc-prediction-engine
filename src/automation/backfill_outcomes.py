"""Repair historical bout outcomes.

Before this, the scraper decided the winner with `"W" if status == "W" else
fighter_b`, so every draw and no contest was silently recorded as a fighter B
win. The database cannot be repaired from itself -- the raw W/L/D/NC token was
never stored, so a corrupted row is indistinguishable from a real decision.

Event listing pages carry a win/draw/nc flag per bout alongside both fighter
URLs, so one request settles an entire card. That makes the repair cost 785
event fetches rather than 8,833 individual bout fetches.

Rows are matched on the fighter pair plus the bout date, never on names,
and only rows whose outcome actually disagrees with the site are written.

Usage:
    python -m src.automation.backfill_outcomes --dry-run
    python -m src.automation.backfill_outcomes
    python -m src.automation.backfill_outcomes --limit 20
"""

import argparse
import sys
from collections import Counter

from playwright.sync_api import sync_playwright

from src.db.connection import get_connection
from src.scraper.bouts import scrape_event_results
from src.scraper.events import scrape_events
from src.scraper.pipeline import parse_date


def load_fighter_ids(cur):
    cur.execute("SELECT url, id FROM fighters;")
    return dict(cur.fetchall())


def load_bouts(cur):
    """Index bouts by (unordered fighter pair, date). The pair alone is not
    enough: 199 pairs fought more than once, and rematches are where draws cluster."""
    cur.execute("SELECT id, date, fighter_a_id, fighter_b_id, winner_id, outcome FROM bouts;")
    index = {}
    for bout_id, date_, a_id, b_id, winner_id, outcome in cur.fetchall():
        index[(frozenset((a_id, b_id)), date_)] = {
            "id": bout_id, "a": a_id, "b": b_id,
            "winner": winner_id, "outcome": outcome,
        }
    return index


def main():
    parser = argparse.ArgumentParser(description="Correct draw and no-contest outcomes.")
    parser.add_argument("--dry-run", action="store_true", help="report changes without writing")
    parser.add_argument("--limit", type=int, help="only process the N most recent events")
    args = parser.parse_args()

    conn = get_connection()
    cur = conn.cursor()
    fighter_ids = load_fighter_ids(cur)
    bout_index = load_bouts(cur)
    print(f"Loaded {len(fighter_ids)} fighters and {len(bout_index)} bouts.")

    corrections = []
    tally = Counter()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            events = scrape_events(page)
            if args.limit:
                events = events[:args.limit]
            print(f"Checking {len(events)} events...")

            for i, event in enumerate(events, 1):
                event_date = parse_date(event["date"])
                if event_date is None:
                    print(f"  [{i}/{len(events)}] unparseable date: {event['date']!r}")
                    tally["bad_event_date"] += 1
                    continue

                try:
                    results = scrape_event_results(event["url"], page)
                except Exception as exc:
                    print(f"  [{i}/{len(events)}] FAILED {event['name']}: {exc}")
                    tally["event_failed"] += 1
                    continue

                for r in results:
                    a_id = fighter_ids.get(r["fighter_a_url"])
                    b_id = fighter_ids.get(r["fighter_b_url"])
                    if a_id is None or b_id is None or a_id == b_id:
                        tally["unmatched_fighter"] += 1
                        continue

                    bout = bout_index.get((frozenset((a_id, b_id)), event_date))
                    if bout is None:
                        tally["no_bout_row"] += 1
                        continue

                    site_outcome = r["outcome"]
                    tally[f"site_{site_outcome}"] += 1

                    if site_outcome == "win":
                        expected_winner = bout["winner"]
                        needs_fix = bout["outcome"] != "win"
                        new_winner = expected_winner
                    else:
                        needs_fix = bout["winner"] is not None or bout["outcome"] != site_outcome
                        new_winner = None

                    if needs_fix:
                        corrections.append((new_winner, site_outcome, bout["id"]))
                        if site_outcome != "win":
                            tally["fixed_" + site_outcome] += 1

                if i % 50 == 0:
                    print(f"  [{i}/{len(events)}] {len(corrections)} rows to update so far")
        finally:
            browser.close()

    print()
    print(f"Rows needing update: {len(corrections)}")
    print(f"  draws corrected:        {tally['fixed_draw']}")
    print(f"  no contests corrected:  {tally['fixed_nc']}")
    print(f"  site says win:          {tally['site_win']}")
    print(f"  no matching bout row:    {tally['no_bout_row']} (skipped)")
    print(f"  unresolvable fighter:    {tally['unmatched_fighter']} (skipped)")
    print(f"  event fetch failures:    {tally['event_failed']}")
    print(f"  unparseable event dates: {tally['bad_event_date']}")

    if args.dry_run:
        print("\nDry run -- nothing written.")
    elif corrections:
        cur.executemany(
            "UPDATE bouts SET winner_id = %s, outcome = %s WHERE id = %s;",
            corrections
        )
        conn.commit()
        print(f"\nUpdated {len(corrections)} rows.")
    else:
        print("\nNothing to update.")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
