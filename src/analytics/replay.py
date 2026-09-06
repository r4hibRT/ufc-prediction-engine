"""Walk every bout in date order, capturing what each fighter knew going in.

This is the source of the bout_snapshots table and therefore of every
point-in-time statistic the site shows. Snapshots are taken before state is
advanced, so nothing here can see the future.
"""

import pandas as pd

from src.analytics.state import FighterState, fight_seconds
from src.db.connection import get_connection


def load_frames(conn):
    bouts = pd.read_sql("""
        SELECT id AS bout_id, date, fighter_a_id, fighter_b_id, winner_id,
               method, weight_class, outcome, round, time
        FROM bouts
        ORDER BY date ASC, id ASC
    """, conn)

    ratings = pd.read_sql("""
        SELECT fighter_id, bout_id, rating, rd, volatility FROM ratings
    """, conn)

    stats = pd.read_sql("""
        SELECT bout_id, fighter_id, sig_strikes_landed, sig_strikes_attempted,
               takedowns_landed, takedowns_attempted, submission_attempts,
               knockdowns, control_time_seconds
        FROM bout_stats
    """, conn)

    return bouts, ratings, stats


def replay(until=None):
    """One snapshot per fighter per bout. `until` truncates history so the
    point-in-time property stays testable against a full-history run."""
    conn = get_connection()
    bouts, ratings, stats = load_frames(conn)
    conn.close()

    if until is not None:
        bouts = bouts[bouts["date"] < until].copy()

    rating_lookup = ratings.set_index(["fighter_id", "bout_id"])[
        ["rating", "rd", "volatility"]].to_dict("index")
    stats_lookup = stats.set_index(["bout_id", "fighter_id"]).to_dict("index")

    state = {}
    snapshots = []

    def get_state(fighter_id):
        if fighter_id not in state:
            state[fighter_id] = FighterState()
        return state[fighter_id]

    for bout in bouts.itertuples(index=False):
        a_id, b_id = bout.fighter_a_id, bout.fighter_b_id
        st_a, st_b = get_state(a_id), get_state(b_id)

        for fid, st in ((a_id, st_a), (b_id, st_b)):
            snapshots.append({
                "bout_id": bout.bout_id, "fighter_id": fid,
                "date": bout.date, **st.snapshot(bout.date),
            })

        prior_a, prior_b = st_a.rating, st_b.rating
        seconds = fight_seconds(bout.round, bout.time)

        # A no contest leaves the record untouched but still counts as activity.
        counts_result = bout.outcome != "nc"
        if not counts_result:
            result_a = result_b = None
        elif bout.winner_id is None:
            result_a = result_b = 0.5
        else:
            result_a = 1 if bout.winner_id == a_id else 0
            result_b = 1 - result_a

        for fid, opp_id, result, prior_opp in (
            (a_id, b_id, result_a, prior_b),
            (b_id, a_id, result_b, prior_a),
        ):
            r = rating_lookup.get((fid, bout.bout_id)) or {}
            get_state(fid).update(
                date=bout.date,
                weight_class=bout.weight_class,
                result=result,
                method=bout.method,
                seconds=seconds,
                rating=r.get("rating"),
                rd=r.get("rd"),
                volatility=r.get("volatility"),
                opponent_prior_rating=prior_opp,
                stats=stats_lookup.get((bout.bout_id, fid)),
                opp_stats=stats_lookup.get((bout.bout_id, opp_id)),
                counts_result=counts_result,
            )

    return snapshots
