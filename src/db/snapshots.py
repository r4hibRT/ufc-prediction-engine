"""Materialise per-bout point-in-time state into bout_snapshots.

"What did this fighter look like going into that fight" is the platform's
differentiator -- ufcstats publishes career totals, not career-to-date totals.
The table is rewritten wholesale because the replay is a pure function of the
bouts and ratings tables, so a partial update could not be trusted.
"""

from psycopg2.extras import execute_values

from src.db.connection import get_connection
from src.analytics.replay import replay

COLUMNS = [
    "bout_id", "fighter_id", "date",
    "bouts_before", "appearances_before", "wins_before", "win_streak", "loss_streak", "recent_form_5",
    "rating_before", "rd_before", "peak_rating_before", "layoff_days",
    "ko_losses", "sub_losses", "finishes", "career_seconds",
    "sig_landed", "sig_attempted", "sig_absorbed",
    "td_landed", "td_attempted", "opp_td_landed", "opp_td_attempted",
    "control_seconds", "sub_attempts", "knockdowns", "knockdowns_absorbed",
    "avg_opponent_rating", "max_opponent_rating",
]


def write_snapshots(conn=None, verbose=True):
    owned = conn is None
    if owned:
        conn = get_connection()

    if verbose:
        print("Replaying bouts to collect point-in-time state...")
    snapshots = replay()
    if verbose:
        print(f"  collected {len(snapshots)} snapshots")

    values = [tuple(row[c] for c in COLUMNS) for row in snapshots]

    cur = conn.cursor()
    cur.execute("TRUNCATE bout_snapshots;")
    execute_values(
        cur,
        f"INSERT INTO bout_snapshots ({', '.join(COLUMNS)}) VALUES %s",
        values,
        page_size=1000,
    )
    conn.commit()

    cur.execute("SELECT COUNT(*), COUNT(DISTINCT fighter_id) FROM bout_snapshots;")
    rows, fighters = cur.fetchone()
    cur.close()
    if owned:
        conn.close()

    if verbose:
        print(f"  wrote {rows} snapshots covering {fighters} fighters")

    return {"snapshots": rows, "fighters": fighters}


if __name__ == "__main__":
    write_snapshots()
