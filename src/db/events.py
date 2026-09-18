"""Scrape checkpoint, kept in the database rather than a file.

It used to be progress.log in the repository root. A branch merge deleted that
file and the next weekly run re-scraped all of UFC history, so the state that
decides what gets scraped now lives somewhere version control cannot touch.
"""

from contextlib import contextmanager
from pathlib import Path

from src.db.connection import get_connection

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LEGACY_PROGRESS_LOG = PROJECT_ROOT / "progress.log"


@contextmanager
def _cursor(conn=None):
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
                name = EXCLUDED.name,
                date = EXCLUDED.date,
                status = EXCLUDED.status,
                bouts_scraped = EXCLUDED.bouts_scraped,
                attempts = events.attempts + 1,
                scraped_at = now();
        """, (url, name, event_date, "complete" if complete else "partial", bouts_scraped))


def sync_metadata(listing, conn=None):
    """Fill names and dates for events seeded from the old URL-only file.
    `listing` holds (url, name, date) for every event on the site's index."""
    with _cursor(conn) as cur:
        cur.executemany("""
            UPDATE events SET name = %s, date = %s
            WHERE url = %s AND (name IS NULL OR date IS NULL);
        """, [(name, event_date, url) for url, name, event_date in listing])
        return cur.rowcount


def seed_from_file(path=LEGACY_PROGRESS_LOG, conn=None):
    """One-time import of the old file checkpoint. Does nothing once seeded."""
    path = Path(path)
    with _cursor(conn) as cur:
        cur.execute("SELECT COUNT(*) FROM events;")
        if cur.fetchone()[0] > 0 or not path.exists():
            return 0
        urls = [line.strip() for line in path.read_text().splitlines() if line.strip()]
        cur.executemany("""
            INSERT INTO events (url, status) VALUES (%s, 'complete')
            ON CONFLICT (url) DO NOTHING;
        """, [(u,) for u in dict.fromkeys(urls)])
        return len(set(urls))
