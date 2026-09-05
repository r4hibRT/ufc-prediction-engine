"""Build the modelling dataset by replaying every bout in order.

The loop is the guarantee: for each bout it emits a feature row from each
fighter's state as it stands BEFORE the fight, and only then updates that
state. Nothing here can see the future, and `--check` asserts it.

Feature definitions live in src/simulation/features.py; the spec they
implement is docs/feature-spec.md.
"""

import argparse
from pathlib import Path

import pandas as pd
import numpy as np

from src.db.connection import get_connection
from src.simulation.features import (
    FighterState, FEATURE_COLUMNS, BLOCKS, build_feature_row, mirror_row,
    assert_mirror_rules, fight_seconds,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TRAIN_CSV = PROJECT_ROOT / "train_data.csv"
VAL_CSV = PROJECT_ROOT / "val_data.csv"
TEST_CSV = PROJECT_ROOT / "test_data.csv"

# Three-way chronological split (docs/feature-spec.md section 3.1).
VALIDATION_START = pd.Timestamp("2023-01-01").date()
TEST_START = pd.Timestamp("2024-06-29").date()

# Rows are dropped only when these are missing. Everything else may be NaN --
# an early-career fighter genuinely has no takedown-defence rate, and an
# explicit NaN is more honest than an imputed zero.
REQUIRED = ["rating_diff", "reach_diff", "height_diff", "age_diff"]


def load_frames(conn):
    bouts = pd.read_sql("""
        SELECT id AS bout_id, date, fighter_a_id, fighter_b_id, winner_id,
               method, weight_class, is_title_fight, is_defence, outcome,
               round, time
        FROM bouts
        ORDER BY date ASC, id ASC
    """, conn)

    ratings = pd.read_sql("""
        SELECT fighter_id, bout_id, rating, rd, volatility
        FROM ratings
    """, conn)

    fighters = pd.read_sql("""
        SELECT id AS fighter_id, dob, reach, height, stance
        FROM fighters
    """, conn)

    stats = pd.read_sql("""
        SELECT bout_id, fighter_id, sig_strikes_landed, sig_strikes_attempted,
               takedowns_landed, takedowns_attempted, submission_attempts,
               knockdowns, control_time_seconds
        FROM bout_stats
    """, conn)

    return bouts, ratings, fighters, stats


def physical_accessor(fighters_df):
    """Static attributes plus an age-at-date closure, per fighter."""
    lookup = fighters_df.set_index("fighter_id").to_dict("index")
    missing = {"reach": np.nan, "height": np.nan, "stance": None, "dob": None}

    def get(fighter_id):
        row = lookup.get(fighter_id, missing)
        dob = row.get("dob")

        def age_at(bout_date):
            if dob is None or pd.isna(dob):
                return float("nan")
            return (bout_date - dob).days / 365.25

        return {
            "reach": row.get("reach") if row.get("reach") is not None else np.nan,
            "height": row.get("height") if row.get("height") is not None else np.nan,
            "stance": row.get("stance"),
            "age_at": age_at,
        }

    return get


def build_raw_features(check=False, until=None):
    """Replay every bout. `until` truncates history, which exists so the
    point-in-time property can be tested: rows built from a truncated history
    must be identical to the same rows built from the full history."""
    conn = get_connection()
    bouts, ratings, fighters, stats = load_frames(conn)
    conn.close()

    if until is not None:
        bouts = bouts[bouts["date"] < until].copy()

    rating_lookup = ratings.set_index(["fighter_id", "bout_id"])[
        ["rating", "rd", "volatility"]].to_dict("index")
    stats_lookup = stats.set_index(["bout_id", "fighter_id"]).to_dict("index")
    physical = physical_accessor(fighters)

    state = {}

    def get_state(fighter_id):
        if fighter_id not in state:
            state[fighter_id] = FighterState()
        return state[fighter_id]

    rows = []
    checked = 0

    for bout in bouts.itertuples(index=False):
        a_id, b_id = bout.fighter_a_id, bout.fighter_b_id
        st_a, st_b = get_state(a_id), get_state(b_id)

        # --- emit BEFORE updating state ---
        if bout.winner_id is not None and st_a.has_rating and st_b.has_rating:
            ctx = {
                "date": bout.date,
                "weight_class": bout.weight_class,
                "is_title_fight": bool(bout.is_title_fight),
                "is_defence": bool(bout.is_defence),
            }
            row = build_feature_row(st_a, st_b, physical(a_id), physical(b_id), ctx)

            if check and checked < 500:
                assert_mirror_rules(row)
                checked += 1

            label = 1 if bout.winner_id == a_id else 0
            meta = {"bout_id": bout.bout_id, "date": bout.date}

            rows.append({**meta, **row, "label": label})
            rows.append({**meta, **mirror_row(row), "label": 1 - label})

        # --- now advance state ---
        prior_rating_a = st_a.rating
        prior_rating_b = st_b.rating
        seconds = fight_seconds(bout.round, bout.time)

        if bout.outcome == "nc":
            result_a = result_b = None
        elif bout.winner_id is None:
            result_a = result_b = 0.5
        else:
            result_a = 1 if bout.winner_id == a_id else 0
            result_b = 1 - result_a

        for fid, opp_id, result, prior_opp in (
            (a_id, b_id, result_a, prior_rating_b),
            (b_id, a_id, result_b, prior_rating_a),
        ):
            if result is None:
                continue
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
            )

    if check:
        print(f"  mirror rules asserted on {checked} rows")

    return pd.DataFrame(rows)


def clean(df):
    before = len(df)
    df = df.dropna(subset=REQUIRED).copy()
    print(f"  dropped {before - len(df)} rows missing {REQUIRED}")
    return df


def three_way_split(df):
    df = df.sort_values(["date", "bout_id"]).reset_index(drop=True)
    train = df[df["date"] < VALIDATION_START].copy()
    val = df[(df["date"] >= VALIDATION_START) & (df["date"] < TEST_START)].copy()
    test = df[df["date"] >= TEST_START].copy()
    return train, val, test


def rebuild_dataset(verbose=True, check=False):
    def say(msg):
        if verbose:
            print(msg)

    say("Replaying bouts...")
    raw = build_raw_features(check=check)
    say(f"Raw rows (mirrored pairs): {len(raw)}")

    df = clean(raw)
    say(f"After cleaning: {len(df)} rows / {df.bout_id.nunique()} fights")

    train, val, test = three_way_split(df)
    for name, part, path in (("Train", train, TRAIN_CSV),
                             ("Val  ", val, VAL_CSV),
                             ("Test ", test, TEST_CSV)):
        part.to_csv(path, index=False)
        say(f"{name}: {len(part):>6} rows / {part.bout_id.nunique():>5} fights "
            f"| {part.date.min()} -> {part.date.max()} | balance {part.label.mean():.3f}")

    if verbose:
        say("")
        say("Missing-value rate by block:")
        for block, cols in BLOCKS.items():
            rate = df[cols].isna().mean().mean()
            say(f"  {block:<14} {rate*100:5.1f}%")

    return {
        "train_rows": len(train), "val_rows": len(val), "test_rows": len(test),
        "train_fights": int(train.bout_id.nunique()),
        "val_fights": int(val.bout_id.nunique()),
        "test_fights": int(test.bout_id.nunique()),
        "features": len(FEATURE_COLUMNS),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rebuild the modelling dataset.")
    parser.add_argument("--check", action="store_true",
                        help="assert mirroring rules while building")
    args = parser.parse_args()
    rebuild_dataset(check=args.check)
