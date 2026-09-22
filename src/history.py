"""Point-in-time history: what every fighter had done before every bout.

ufcstats publishes career totals, not career-to-date totals, so this module
replays every bout in date order and records each fighter's state walking into
it. A snapshot is always taken before the bout is applied, which is what keeps
the statistics honest: nothing here can see the future. The output feeds the
`bout_snapshots` table (the site's point-in-time views) and the forecasting
engine's features.

It also marks title defences, the other column derived from bout history rather
than scraped, by tracking each division's champion. ufcstats flags tournament
finals (TUF, Road to UFC) as title fights too; one is skipped when a champion
exists, neither fighter is that champion, and neither has MIN_TITLE_EXPERIENCE
prior appearances, or it would crown a false champion and hide the real one's
next defence. A vacated or stripped belt is not in the data, so its last holder
is still treated as champion. Both outputs are recomputed wholesale on every
refresh, since each is a pure function of the bouts table.

Run:
    python -m src.history
"""

import pandas as pd
from psycopg2.extras import execute_values

from src.db import MIN_TITLE_EXPERIENCE, get_connection

RECENT_FORM_WINDOW = 5

SNAPSHOT_COLUMNS = [
    "bout_id", "fighter_id", "date",
    "bouts_before", "appearances_before", "wins_before", "win_streak", "loss_streak",
    "recent_form_5", "rating_before", "rd_before", "peak_rating_before", "layoff_days",
    "ko_losses", "sub_losses", "finishes", "career_seconds",
    "sig_landed", "sig_attempted", "sig_absorbed",
    "td_landed", "td_attempted", "opp_td_landed", "opp_td_attempted",
    "control_seconds", "sub_attempts", "knockdowns", "knockdowns_absorbed",
    "avg_opponent_rating", "max_opponent_rating",
]


def fight_seconds(round_, time_str):
    """Elapsed fight time: five minutes per completed round plus the last."""
    try:
        completed = (int(round_) - 1) * 300
        mins, secs = time_str.split(":")
        return completed + int(mins) * 60 + int(secs)
    except (TypeError, ValueError, AttributeError):
        return None


class FighterState:
    """What a fighter had done before the bout currently being processed."""

    def __init__(self):
        self.bouts = 0          # results that stood
        self.appearances = 0    # times in the cage, no contests included
        self.wins = 0
        self.win_streak = 0
        self.loss_streak = 0
        self.recent = []
        self.ko_losses = 0
        self.sub_losses = 0
        self.finishes = 0
        self.career_seconds = 0
        self.last_bout_date = None
        self.rating = None
        self.rd = None
        self.peak_rating = None
        self.opponent_ratings = []
        self.sig_landed = self.sig_attempted = self.sig_absorbed = 0
        self.td_landed = self.td_attempted = 0
        self.opp_td_landed = self.opp_td_attempted = 0
        self.control_seconds = self.sub_attempts = 0
        self.knockdowns = self.knockdowns_absorbed = 0

    def snapshot(self, bout_date):
        """This fighter's state entering a bout, as a bout_snapshots row."""
        window = self.recent[-RECENT_FORM_WINDOW:]
        opp = self.opponent_ratings
        return {
            "bouts_before": self.bouts,
            "appearances_before": self.appearances,
            "wins_before": self.wins,
            "win_streak": self.win_streak,
            "loss_streak": self.loss_streak,
            "recent_form_5": sum(window) / len(window) if window else None,
            "rating_before": self.rating,
            "rd_before": self.rd,
            "peak_rating_before": self.peak_rating,
            "layoff_days": (bout_date - self.last_bout_date).days if self.last_bout_date else None,
            "ko_losses": self.ko_losses,
            "sub_losses": self.sub_losses,
            "finishes": self.finishes,
            "career_seconds": self.career_seconds,
            "sig_landed": self.sig_landed,
            "sig_attempted": self.sig_attempted,
            "sig_absorbed": self.sig_absorbed,
            "td_landed": self.td_landed,
            "td_attempted": self.td_attempted,
            "opp_td_landed": self.opp_td_landed,
            "opp_td_attempted": self.opp_td_attempted,
            "control_seconds": self.control_seconds,
            "sub_attempts": self.sub_attempts,
            "knockdowns": self.knockdowns,
            "knockdowns_absorbed": self.knockdowns_absorbed,
            "avg_opponent_rating": sum(opp) / len(opp) if opp else None,
            "max_opponent_rating": max(opp) if opp else None,
        }

    def update(self, *, date, result, method, seconds, rating, rd,
               opponent_prior_rating, stats, opp_stats, counts_result=True):
        """Advance state after a bout. A no contest still accumulates cage time,
        statistics and activity, but leaves the win-loss record untouched."""
        self.appearances += 1
        self.last_bout_date = date
        if seconds:
            self.career_seconds += seconds
        if opponent_prior_rating is not None:
            self.opponent_ratings.append(opponent_prior_rating)
        if stats:
            self.sig_landed += stats["sig_strikes_landed"] or 0
            self.sig_attempted += stats["sig_strikes_attempted"] or 0
            self.td_landed += stats["takedowns_landed"] or 0
            self.td_attempted += stats["takedowns_attempted"] or 0
            self.control_seconds += stats["control_time_seconds"] or 0
            self.sub_attempts += stats["submission_attempts"] or 0
            self.knockdowns += stats["knockdowns"] or 0
        if opp_stats:
            self.sig_absorbed += opp_stats["sig_strikes_landed"] or 0
            self.opp_td_landed += opp_stats["takedowns_landed"] or 0
            self.opp_td_attempted += opp_stats["takedowns_attempted"] or 0
            self.knockdowns_absorbed += opp_stats["knockdowns"] or 0
        if not counts_result:
            return

        self.bouts += 1
        if result == 1:
            self.wins += 1
            self.win_streak += 1
            self.loss_streak = 0
        elif result == 0:
            self.win_streak = 0
            self.loss_streak += 1
        else:
            self.win_streak = self.loss_streak = 0
        self.recent.append(result)

        if result == 1 and method in ("KO/TKO", "Submission"):
            self.finishes += 1
        if result == 0 and method == "KO/TKO":
            self.ko_losses += 1
        elif result == 0 and method == "Submission":
            self.sub_losses += 1
        if rating is not None:
            self.rating, self.rd = rating, rd
            if self.peak_rating is None or rating > self.peak_rating:
                self.peak_rating = rating


def _load(conn):
    bouts = pd.read_sql("""
        SELECT id AS bout_id, date, fighter_a_id, fighter_b_id, winner_id,
               method, weight_class, outcome, round, time
        FROM bouts ORDER BY date ASC, id ASC
    """, conn)
    ratings = pd.read_sql("SELECT fighter_id, bout_id, rating, rd FROM ratings", conn)
    stats = pd.read_sql("""
        SELECT bout_id, fighter_id, sig_strikes_landed, sig_strikes_attempted,
               takedowns_landed, takedowns_attempted, submission_attempts,
               knockdowns, control_time_seconds
        FROM bout_stats
    """, conn)
    return bouts, ratings, stats


def replay(until=None, extra=None):
    """One snapshot per fighter per bout, before `until` if given (for the lookahead test).
    `extra` appends bouts with outcome "upcoming": snapshotted, never applied."""
    conn = get_connection()
    bouts, ratings, stats = _load(conn)
    conn.close()
    if until is not None:
        bouts = bouts[bouts["date"] < until].copy()
    if extra is not None:
        bouts = pd.concat([bouts, extra[bouts.columns]], ignore_index=True)

    rating_at = ratings.set_index(["fighter_id", "bout_id"])[["rating", "rd"]].to_dict("index")
    stats_at = stats.set_index(["bout_id", "fighter_id"]).to_dict("index")
    state, snapshots = {}, []

    for bout in bouts.itertuples(index=False):
        a_id, b_id = bout.fighter_a_id, bout.fighter_b_id
        st_a = state.setdefault(a_id, FighterState())
        st_b = state.setdefault(b_id, FighterState())
        for fid, st in ((a_id, st_a), (b_id, st_b)):
            snapshots.append({"bout_id": bout.bout_id, "fighter_id": fid,
                              "date": bout.date, **st.snapshot(bout.date)})
        if bout.outcome == "upcoming":
            continue

        # A no contest leaves the record untouched but still counts as activity.
        counts_result = bout.outcome != "nc"
        if not counts_result:
            result_a = result_b = None
        elif bout.winner_id is None:
            result_a = result_b = 0.5
        else:
            result_a = 1 if bout.winner_id == a_id else 0
            result_b = 1 - result_a

        seconds = fight_seconds(bout.round, bout.time)
        prior_a, prior_b = st_a.rating, st_b.rating
        for fid, opp_id, st, result, prior_opp in ((a_id, b_id, st_a, result_a, prior_b),
                                                   (b_id, a_id, st_b, result_b, prior_a)):
            r = rating_at.get((fid, bout.bout_id)) or {}
            st.update(date=bout.date, result=result, method=bout.method, seconds=seconds,
                      rating=r.get("rating"), rd=r.get("rd"), opponent_prior_rating=prior_opp,
                      stats=stats_at.get((bout.bout_id, fid)),
                      opp_stats=stats_at.get((bout.bout_id, opp_id)),
                      counts_result=counts_result)
    return snapshots


def write_snapshots(log=print):
    """Rewrite bout_snapshots wholesale; a partial update of a pure replay could not be trusted."""
    snapshots = replay()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("TRUNCATE bout_snapshots;")
    execute_values(cur, f"INSERT INTO bout_snapshots ({', '.join(SNAPSHOT_COLUMNS)}) VALUES %s",
                   [tuple(row[c] for c in SNAPSHOT_COLUMNS) for row in snapshots],
                   page_size=1000)
    conn.commit()
    cur.execute("SELECT COUNT(*), COUNT(DISTINCT fighter_id) FROM bout_snapshots;")
    rows, fighters = cur.fetchone()
    cur.close()
    conn.close()
    log(f"Snapshots rebuilt: {rows} rows covering {fighters} fighters.")
    return {"snapshots": rows, "fighters": fighters}


def mark_title_defences(log=print):
    """Mark title fights where a participant walked in holding the belt (see the module
    docstring for tournament finals). Needs fresh snapshots; a draw or NC retains."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT b.id, b.weight_class, b.fighter_a_id, b.fighter_b_id, b.winner_id, b.outcome,
               GREATEST(sa.appearances_before, sb.appearances_before)
        FROM bouts b
        JOIN bout_snapshots sa ON sa.bout_id = b.id AND sa.fighter_id = b.fighter_a_id
        JOIN bout_snapshots sb ON sb.bout_id = b.id AND sb.fighter_id = b.fighter_b_id
        WHERE b.is_title_fight ORDER BY b.date ASC, b.id ASC;
    """)
    champion, defences, tournaments = {}, {}, 0
    rows = cur.fetchall()
    for bout_id, weight_class, a_id, b_id, winner_id, outcome, experience in rows:
        holder = champion.get(weight_class)
        if holder in (a_id, b_id):
            defences[bout_id] = holder
        elif holder is not None and experience < MIN_TITLE_EXPERIENCE:
            tournaments += 1
            continue
        # The belt only changes hands on a decisive result.
        if outcome == "win" and winner_id is not None:
            champion[weight_class] = winner_id
    cur.execute("UPDATE bouts SET is_defence = FALSE, title_holder_id = NULL WHERE is_title_fight;")
    cur.executemany("UPDATE bouts SET is_defence = TRUE, title_holder_id = %s WHERE id = %s;",
                    [(holder, bout_id) for bout_id, holder in defences.items()])
    conn.commit()
    cur.close()
    conn.close()
    log(f"Title defences marked: {len(defences)} of {len(rows)} title fights "
        f"({tournaments} tournament finals skipped).")
    return {"title_fights": len(rows), "defences": len(defences), "tournaments": tournaments}


if __name__ == "__main__":
    write_snapshots()
    mark_title_defences()
