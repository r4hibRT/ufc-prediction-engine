from pathlib import Path

import pandas as pd
import numpy as np
from src.db.connection import get_connection
from src.simulation.matchup import build_matchup_matrix, reindex_probs, compute_style_probability


def build_raw_features():
    conn = get_connection()

    bouts_df = pd.read_sql("""
        SELECT id AS bout_id, date, fighter_a_id, fighter_b_id, winner_id,
               method, weight_class, is_title_fight
        FROM bouts
        WHERE winner_id IS NOT NULL
        ORDER BY date ASC, id ASC
    """, conn)

    ratings_df = pd.read_sql("""
        SELECT fighter_id, bout_id, date, rating, rd
        FROM ratings
        ORDER BY date ASC, id ASC
    """, conn)

    fighters_df = pd.read_sql("""
        SELECT id AS fighter_id, dob, reach, height, stance,
               prob_0, prob_1, prob_2, prob_3, prob_4
        FROM fighters
    """, conn)

    conn.close()

    fighters_df = fighters_df.set_index("fighter_id")
    rating_lookup = ratings_df.set_index(["fighter_id", "bout_id"])[["rating", "rd"]].to_dict("index")

    # Style-matchup matrix built once (STATIC archetypes -- leakage-flagged screening feature)
    matchup_matrix = build_matchup_matrix()

    def style_matchup_prob(fid_a, fid_b):
        if fid_a not in fighters_df.index or fid_b not in fighters_df.index:
            return np.nan
        fa, fb = fighters_df.loc[fid_a], fighters_df.loc[fid_b]
        if pd.isna(fa["prob_0"]) or pd.isna(fb["prob_0"]):
            return np.nan
        probs_a = reindex_probs({f"prob_{i}": fa[f"prob_{i}"] for i in range(5)})
        probs_b = reindex_probs({f"prob_{i}": fb[f"prob_{i}"] for i in range(5)})
        return compute_style_probability(probs_a, probs_b, matchup_matrix)

    state = {}

    def get_state(fid):
        if fid not in state:
            state[fid] = {
                "total_bouts": 0, "division_bouts": {}, "decision_count": 0,
                "last_bout_date": None, "last_rating": None, "last_rd": None,
            }
        return state[fid]

    def physical_features(fid_a, fid_b, bout_date):
        fa = fighters_df.loc[fid_a] if fid_a in fighters_df.index else None
        fb = fighters_df.loc[fid_b] if fid_b in fighters_df.index else None

        reach_diff = (fa["reach"] - fb["reach"]) if fa is not None and fb is not None else np.nan
        height_diff = (fa["height"] - fb["height"]) if fa is not None and fb is not None else np.nan

        def age_at(f):
            if f is None or pd.isna(f["dob"]):
                return np.nan
            return (bout_date - f["dob"]).days / 365.25

        age_diff = age_at(fa) - age_at(fb) if fa is not None and fb is not None else np.nan

        stance_a = fa["stance"] if fa is not None else None
        stance_b = fb["stance"] if fb is not None else None
        stance_matchup = f"{stance_a}_vs_{stance_b}" if stance_a and stance_b else "unknown"

        return reach_diff, height_diff, age_diff, stance_matchup

    rows = []

    for _, bout in bouts_df.iterrows():
        bout_id = bout["bout_id"]
        date = bout["date"]
        a_id, b_id = bout["fighter_a_id"], bout["fighter_b_id"]
        weight_class = bout["weight_class"]
        is_title = bool(bout["is_title_fight"])
        winner = bout["winner_id"]
        method = bout["method"]

        st_a, st_b = get_state(a_id), get_state(b_id)
        prior_rating_a, prior_rating_b = st_a["last_rating"], st_b["last_rating"]

        if prior_rating_a is not None and prior_rating_b is not None:
            rating_diff = prior_rating_a - prior_rating_b
            rd_sum = st_a["last_rd"] + st_b["last_rd"]

            reach_diff, height_diff, age_diff, stance_matchup = physical_features(a_id, b_id, date)
            experience_diff = st_a["total_bouts"] - st_b["total_bouts"]

            layoff_a = (date - st_a["last_bout_date"]).days if st_a["last_bout_date"] else np.nan
            layoff_b = (date - st_b["last_bout_date"]).days if st_b["last_bout_date"] else np.nan
            layoff_diff = (layoff_a - layoff_b) if not (pd.isna(layoff_a) or pd.isna(layoff_b)) else np.nan

            div_exp_a_flag = int(st_a["division_bouts"].get(weight_class, 0) > 0)
            div_exp_b_flag = int(st_b["division_bouts"].get(weight_class, 0) > 0)

            pct_distance_a = (st_a["decision_count"] / st_a["total_bouts"]) if st_a["total_bouts"] > 0 else np.nan
            pct_distance_b = (st_b["decision_count"] / st_b["total_bouts"]) if st_b["total_bouts"] > 0 else np.nan
            if not (pd.isna(pct_distance_a) or pd.isna(pct_distance_b)):
                five_rd_interaction = int(is_title) * (pct_distance_a - pct_distance_b)
            else:
                five_rd_interaction = np.nan

            style_prob = style_matchup_prob(a_id, b_id)

            label_a_wins = 1 if winner == a_id else 0

            rows.append({
                "bout_id": bout_id, "date": date,
                "rating_diff": rating_diff, "rd_sum": rd_sum,
                "reach_diff": reach_diff, "height_diff": height_diff, "age_diff": age_diff,
                "stance_matchup": stance_matchup,
                "experience_diff": experience_diff, "layoff_diff": layoff_diff,
                "division_experience_a": div_exp_a_flag, "division_experience_b": div_exp_b_flag,
                "is_title_fight": int(is_title),
                "five_round_experience_interaction": five_rd_interaction,
                "style_matchup_prob": style_prob,
                "label": label_a_wins
            })

            rows.append({
                "bout_id": bout_id, "date": date,
                "rating_diff": -rating_diff, "rd_sum": rd_sum,
                "reach_diff": -reach_diff, "height_diff": -height_diff, "age_diff": -age_diff,
                "stance_matchup": f"{stance_matchup.split('_vs_')[1]}_vs_{stance_matchup.split('_vs_')[0]}" if "_vs_" in stance_matchup else "unknown",
                "experience_diff": -experience_diff,
                "layoff_diff": -layoff_diff if not pd.isna(layoff_diff) else np.nan,
                "division_experience_a": div_exp_b_flag, "division_experience_b": div_exp_a_flag,
                "is_title_fight": int(is_title),
                "five_round_experience_interaction": -five_rd_interaction if not pd.isna(five_rd_interaction) else np.nan,
                "style_matchup_prob": (1 - style_prob) if not pd.isna(style_prob) else np.nan,
                "label": 1 - label_a_wins
            })

        for fid in [a_id, b_id]:
            st = get_state(fid)
            st["total_bouts"] += 1
            st["division_bouts"][weight_class] = st["division_bouts"].get(weight_class, 0) + 1
            if method == "Decision":
                st["decision_count"] += 1
            st["last_bout_date"] = date
            r = rating_lookup.get((fid, bout_id))
            if r:
                st["last_rating"] = r["rating"]
                st["last_rd"] = r["rd"]

    return pd.DataFrame(rows)


def clean_and_encode(df):
    df = df.dropna(subset=[
        "reach_diff", "height_diff", "age_diff", "style_matchup_prob"
    ])

    def same_stance(matchup_str):
        if matchup_str == "unknown" or "_vs_" not in matchup_str:
            return np.nan
        a, b = matchup_str.split("_vs_")
        return int(a == b)

    df["same_stance"] = df["stance_matchup"].apply(same_stance)
    df = df.dropna(subset=["same_stance"])
    df = df.drop(columns=["stance_matchup"])
    return df


def chronological_split(df, test_fraction=0.15):
    df = df.sort_values("date").reset_index(drop=True)
    split_date = df["date"].quantile(1 - test_fraction, interpolation="nearest")
    train_df = df[df["date"] < split_date].copy()
    test_df = df[df["date"] >= split_date].copy()
    return train_df, test_df, split_date


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TRAIN_CSV = PROJECT_ROOT / "train_data.csv"
TEST_CSV = PROJECT_ROOT / "test_data.csv"


def rebuild_dataset(verbose=True):
    """Rebuild train/test CSVs from the current DB state.

    Written to absolute, project-root-relative paths so this behaves the same
    whether it is run from the repo root or by a scheduled task with an
    arbitrary working directory.
    """
    def say(msg):
        if verbose:
            print(msg)

    say("Building raw features from DB...")
    raw_df = build_raw_features()
    say(f"Raw rows (mirrored pairs): {len(raw_df)}")

    df = clean_and_encode(raw_df)
    say(f"After cleaning: {len(df)}")

    if verbose:
        missing = df.isna().sum()
        missing = missing[missing > 0]
        say("")
        say("Missing values per column:")
        say(missing.to_string() if len(missing) else "  none")

    round_map = {
        "rating_diff": 2, "rd_sum": 2,
        "reach_diff": 1, "height_diff": 1, "age_diff": 2,
        "layoff_diff": 1,
        "five_round_experience_interaction": 4,
        "style_matchup_prob": 4,
    }
    for col, decimals in round_map.items():
        df[col] = df[col].round(decimals)

    train_df, test_df, split_date = chronological_split(df)
    say("")
    say(f"Split date: {split_date}")
    say(f"Train: {len(train_df)} rows, label balance {round(train_df['label'].mean(), 3)}")
    say(f"Test:  {len(test_df)} rows, label balance {round(test_df['label'].mean(), 3)}")

    train_df.to_csv(TRAIN_CSV, index=False)
    test_df.to_csv(TEST_CSV, index=False)
    say("")
    say(f"Saved {TRAIN_CSV.name} and {TEST_CSV.name}")

    return {
        "train_rows": len(train_df),
        "test_rows": len(test_df),
        "split_date": str(split_date),
    }


if __name__ == "__main__":
    rebuild_dataset()
