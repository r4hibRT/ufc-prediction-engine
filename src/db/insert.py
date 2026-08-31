from contextlib import contextmanager

from src.db.connection import get_connection


@contextmanager
def _cursor(conn=None):
    """Yield a cursor, reusing a caller's connection when one is supplied.

    Every writer here used to open and close its own connection, so a full
    scrape made thousands of them. Passing a connection in lets the caller hold
    one open for a whole event; omitting it keeps the original behaviour.
    """
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


def insert_fighter(url, name, dob=None, height=None, reach=None, stance=None, conn=None):
    with _cursor(conn) as cur:
        cur.execute("""
            INSERT INTO fighters (url, name, dob, height, reach, stance)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (url) DO NOTHING
            RETURNING id;
        """, (url, name, dob, height, reach, stance))

        row = cur.fetchone()

        if row is None:
            cur.execute("SELECT id FROM fighters WHERE url = %s;", (url,))
            row = cur.fetchone()

        return row[0]


def insert_bout(date, fighter_a_id, fighter_b_id, winner_id, method, method_detail,
                round_, time, weight_class, is_title_fight, is_defence,
                outcome=None, conn=None):
    with _cursor(conn) as cur:
        cur.execute("""
            INSERT INTO bouts (date, fighter_a_id, fighter_b_id, winner_id, method, method_detail,
                               round, time, weight_class, is_title_fight, is_defence, outcome)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (date, fighter_a_id, fighter_b_id) DO NOTHING
            RETURNING id;
        """, (date, fighter_a_id, fighter_b_id, winner_id, method, method_detail,
              round_, time, weight_class, is_title_fight, is_defence, outcome))

        row = cur.fetchone()
        if row is None:
            cur.execute("""
                SELECT id FROM bouts
                WHERE date = %s AND fighter_a_id = %s AND fighter_b_id = %s;
            """, (date, fighter_a_id, fighter_b_id))
            row = cur.fetchone()

        return row[0]


def insert_bout_stats(bout_id, fighter_id, stats, conn=None):
    with _cursor(conn) as cur:
        cur.execute("""
            INSERT INTO bout_stats (bout_id, fighter_id, sig_strikes_landed, sig_strikes_attempted,
                                    total_strikes_landed, total_strikes_attempted, takedowns_landed,
                                    takedowns_attempted, submission_attempts, knockdowns, control_time_seconds)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (bout_id, fighter_id) DO NOTHING;
        """, (
            bout_id,
            fighter_id,
            stats["sig_strikes_landed"],
            stats["sig_strikes_attempted"],
            stats["total_strikes_landed"],
            stats["total_strikes_attempted"],
            stats["takedowns_landed"],
            stats["takedowns_attempted"],
            stats["submission_attempts"],
            stats["knockdowns"],
            stats["control_time_seconds"]
        ))


def fighter_exists(url, conn=None):
    with _cursor(conn) as cur:
        cur.execute("SELECT id FROM fighters WHERE url = %s;", (url,))
        row = cur.fetchone()
        return row[0] if row else None


def insert_rating(fighter_id, bout_id, date, rating, rd, volatility, expected_score, conn=None):
    with _cursor(conn) as cur:
        cur.execute("""
            INSERT INTO ratings (fighter_id, bout_id, date, rating, rd, volatility, expected_score)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (fighter_id, bout_id) DO NOTHING;
        """, (fighter_id, bout_id, date, rating, rd, volatility, expected_score))
