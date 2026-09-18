from src.scraper.events import scrape_events
from src.scraper.bouts import scrape_bout_urls, scrape_bout_details, scrape_bout_stats
from src.scraper.fighters import scrape_fighter_profile
from src.db.insert import insert_fighter, insert_bout, insert_bout_stats, fighter_exists
from src.db.connection import get_connection
from src.db.events import completed_urls, record_event
from playwright.sync_api import sync_playwright
from datetime import datetime
import os


def parse_date(date_str):
    try:
        return datetime.strptime(date_str.strip(), "%B %d, %Y").date()
    except (ValueError, AttributeError):
        return None
FAILED_BOUTS_LOG = "logs/failed_bouts.log"


def log_failed_bout(event_name, bout_url, error):
    """Record an unscrapeable bout. Its event is marked partial so the next run
    retries it, rather than silently dropping the bout forever."""
    os.makedirs(os.path.dirname(FAILED_BOUTS_LOG), exist_ok=True)
    with open(FAILED_BOUTS_LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat()}\t{event_name}\t{bout_url}\t{error}\n")


def run_pipeline(events, page):
    completed = completed_urls()

    for event in events:
        if event["url"] in completed:
            print(f"Skipping (already done): {event['name']}")
            continue

        print(f"Processing event: {event['name']} ({event['date']})")
        event_date = parse_date(event["date"])
        bout_urls = scrape_bout_urls(event["url"], page)
        success = 0
        failed = 0
        # One connection for the whole card instead of one per insert.
        conn = get_connection()
        for bout_url in bout_urls:
            try:
                details = scrape_bout_details(bout_url, page)

                fighter_a_id = fighter_exists(details["fighter_a_url"], conn=conn)
                if fighter_a_id is None:
                    profile_a = scrape_fighter_profile(details["fighter_a_url"], page)
                    fighter_a_id = insert_fighter(
                        url=details["fighter_a_url"],
                        name=details["fighter_a_name"],
                        dob=profile_a["dob"],
                        height=profile_a["height"],
                        reach=profile_a["reach"],
                        stance=profile_a["stance"],
                        conn=conn
                    )

                fighter_b_id = fighter_exists(details["fighter_b_url"], conn=conn)
                if fighter_b_id is None:
                    profile_b = scrape_fighter_profile(details["fighter_b_url"], page)
                    fighter_b_id = insert_fighter(
                        url=details["fighter_b_url"],
                        name=details["fighter_b_name"],
                        dob=profile_b["dob"],
                        height=profile_b["height"],
                        reach=profile_b["reach"],
                        stance=profile_b["stance"],
                        conn=conn
                    )

                if details["winner"] is None:
                    winner_id = None
                elif details["winner"] == details["fighter_a_name"]:
                    winner_id = fighter_a_id
                else:
                    winner_id = fighter_b_id

                bout_id = insert_bout(
                    date=event_date,
                    fighter_a_id=fighter_a_id,
                    fighter_b_id=fighter_b_id,
                    winner_id=winner_id,
                    method=details["method"],
                    method_detail=details["method_detail"],
                    round_=details["round"],
                    time=details["time"],
                    weight_class=details["weight_class"],
                    is_title_fight=details["is_title_fight"],
                    is_defence=details["is_defence"],
                    outcome=details["outcome"],
                    conn=conn
                )

                stats = scrape_bout_stats(
                    details["soup"],
                    details["fighter_a_url"],
                    details["fighter_b_url"]
                )

                if stats:
                    insert_bout_stats(bout_id, fighter_a_id, stats[0], conn=conn)
                    insert_bout_stats(bout_id, fighter_b_id, stats[1], conn=conn)

                print(f"  ✓ {details['fighter_a_name']} vs {details['fighter_b_name']}")
                success += 1

            except Exception as e:
                print(f"  ✗ Failed {bout_url}: {e}")
                log_failed_bout(event["name"], bout_url, f"{type(e).__name__}: {e}")
                failed += 1
                conn.rollback()
                continue

        conn.commit()
        conn.close()

        complete = failed == 0 and success > 0
        record_event(event["url"], event["name"], event_date, success, complete)

        print(f"  → {success} succeeded, {failed} failed")
        if failed:
            print(f"  ⚠ {failed} bout(s) failed — event marked partial, will retry next run")
        elif not success:
            print(f"  ⚠ No bouts scraped — event marked partial, will retry next run")

if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        events = scrape_events(page)
        events = list(reversed(events))
        run_pipeline(events, page)

        browser.close()