"""Unattended refresh of the whole pipeline.

Designed to be run by a scheduler once a week, after the weekend's card has
settled. Every step is idempotent, so a re-run after a partial failure is safe.

Steps:
  1. scrape newly completed events (incremental -- progress.log gates what is
     re-fetched, so this normally costs one events-list request plus whatever
     bouts are genuinely new)
  2. recompute Glicko-2 ratings from scratch (~6s over ~8.8k bouts, cheap
     enough that incremental rating updates are not worth the complexity)
  3. rewrite per-bout snapshots for the point-in-time statistics

Usage:
    python -m src.automation.refresh
    python -m src.automation.refresh --skip-scrape
    python -m src.automation.refresh --dry-run
"""

import argparse
import io
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = PROJECT_ROOT / "logs"
STATUS_FILE = LOG_DIR / "last_run.json"
LOCK_FILE = LOG_DIR / "refresh.lock"
LOCK_STALE_SECONDS = 6 * 60 * 60


def _force_utf8_stdio():
    """Task Scheduler gives a cp1252 console and the scraper prints non-ASCII,
    which would otherwise raise UnicodeEncodeError and kill a healthy run."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


class Tee:
    """Write to the console and the run log at once."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            s.write(data)
            s.flush()

    def flush(self):
        for s in self.streams:
            s.flush()


def log(message):
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}] {message}")


def acquire_lock():
    """Stop two refreshes writing at once; the ratings step truncates a table,
    so an overlapping run would corrupt it."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    if LOCK_FILE.exists():
        age = time.time() - LOCK_FILE.stat().st_mtime
        if age < LOCK_STALE_SECONDS:
            log(f"Another refresh is running (lock is {age / 60:.0f} min old). Exiting.")
            return False
        log(f"Clearing stale lock ({age / 3600:.1f}h old).")
        LOCK_FILE.unlink()

    LOCK_FILE.write_text(f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}\n")
    return True


def release_lock():
    try:
        LOCK_FILE.unlink()
    except FileNotFoundError:
        pass


def step_scrape(dry_run=False):
    """Scrape any completed events not already in progress.log."""
    from playwright.sync_api import sync_playwright
    from src.scraper.events import scrape_events, scrape_upcoming_events
    from src.scraper.pipeline import run_pipeline, load_progress

    completed_urls = load_progress()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            events = scrape_events(page)
            upcoming = scrape_upcoming_events(page)

            pending = [e for e in events if e["url"] not in completed_urls]
            log(f"Site lists {len(events)} completed events; {len(pending)} not yet scraped.")
            log(f"Next scheduled card: {upcoming[0]['date']} - {upcoming[0]['name']}"
                if upcoming else "No upcoming events listed.")

            if not pending:
                return {"new_events": 0, "upcoming": len(upcoming)}

            if dry_run:
                for e in pending:
                    log(f"  would scrape: {e['date']} - {e['name']}")
                return {"new_events": 0, "pending": len(pending), "upcoming": len(upcoming)}

            # Oldest first, so ratings stay chronologically coherent if the
            # run is interrupted partway through the backlog.
            run_pipeline(list(reversed(pending)), page)

            remaining = [e for e in events if e["url"] not in load_progress()]
            scraped = len(pending) - len(remaining)
            return {"new_events": scraped, "still_pending": len(remaining),
                    "upcoming": len(upcoming)}
        finally:
            browser.close()


def step_ratings(dry_run=False):
    from src.ratings.runner import run_ratings

    if dry_run:
        log("  would recompute all Glicko-2 ratings")
        return {"skipped": "dry-run"}

    run_ratings()
    return {"recomputed": True}


def step_snapshots(dry_run=False):
    from src.db.snapshots import write_snapshots

    if dry_run:
        log("  would rewrite bout_snapshots")
        return {"skipped": "dry-run"}

    return write_snapshots(verbose=True)


STEPS = [
    ("scrape", step_scrape),
    ("ratings", step_ratings),
    ("snapshots", step_snapshots),
]


def main():
    parser = argparse.ArgumentParser(description="Refresh UFC data, ratings and snapshots.")
    parser.add_argument("--skip-scrape", action="store_true", help="reuse the DB as-is")
    parser.add_argument("--skip-ratings", action="store_true", help="leave ratings untouched")
    parser.add_argument("--skip-snapshots", action="store_true",
                        help="do not rewrite bout_snapshots")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would happen without writing anything")
    args = parser.parse_args()

    _force_utf8_stdio()
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    started = datetime.now(timezone.utc)
    log_path = LOG_DIR / f"refresh-{started.strftime('%Y-%m-%d')}.log"
    log_file = io.open(log_path, "a", encoding="utf-8", newline="\n")

    original_stdout = sys.stdout
    sys.stdout = Tee(original_stdout, log_file)

    skipped = {
        "scrape": args.skip_scrape,
        "ratings": args.skip_ratings,
        "snapshots": args.skip_snapshots,
    }

    results = {}
    failed_step = None

    try:
        print("=" * 70)
        log(f"Refresh started {started.isoformat()}"
            + ("  [DRY RUN]" if args.dry_run else ""))
        print("=" * 70)

        if not args.dry_run and not acquire_lock():
            return 0

        try:
            for name, fn in STEPS:
                if skipped[name]:
                    log(f"--- {name}: skipped by flag ---")
                    results[name] = {"skipped": "flag"}
                    continue

                log(f"--- {name} ---")
                step_started = time.time()
                try:
                    results[name] = fn(dry_run=args.dry_run)
                    log(f"--- {name} ok ({time.time() - step_started:.1f}s) ---")
                except Exception as exc:
                    failed_step = name
                    results[name] = {"error": f"{type(exc).__name__}: {exc}"}
                    log(f"--- {name} FAILED: {type(exc).__name__}: {exc} ---")
                    traceback.print_exc(file=sys.stdout)
                    # Later steps consume this one's output, so stop rather
                    # than build snapshots from half-updated ratings.
                    break
        finally:
            if not args.dry_run:
                release_lock()

        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        log(f"Refresh finished in {elapsed:.1f}s"
            + (f" -- FAILED at '{failed_step}'" if failed_step else " -- ok"))

        if not args.dry_run:
            STATUS_FILE.write_text(json.dumps({
                "started": started.isoformat(),
                "elapsed_seconds": round(elapsed, 1),
                "ok": failed_step is None,
                "failed_step": failed_step,
                "results": results,
                "log": log_path.name,
            }, indent=2, default=str), encoding="utf-8")

        return 1 if failed_step else 0
    finally:
        sys.stdout = original_stdout
        log_file.close()


if __name__ == "__main__":
    sys.exit(main())
