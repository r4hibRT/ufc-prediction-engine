import pandas as pd
from src.db.connection import get_connection


def parse_fight_duration(round_, time_str):
    """Convert round + time to total minutes."""
    try:
        round_ = int(round_)
        mins, secs = map(int, time_str.split(":"))
        completed_rounds = (round_ - 1) * 5
        return completed_rounds + mins + secs / 60
    except:
        return None


def compute_fighter_features():
    conn = get_connection()

    query = """
        SELECT 
            bs.fighter_id,
            bs.bout_id,
            bs.sig_strikes_landed,
            bs.sig_strikes_attempted,
            bs.total_strikes_landed,
            bs.total_strikes_attempted,
            bs.takedowns_landed,
            bs.takedowns_attempted,
            bs.submission_attempts,
            bs.knockdowns,
            bs.control_time_seconds,
            b.round,
            b.time,
            b.winner_id,
            b.method,
            b.fighter_a_id,
            b.fighter_b_id
        FROM bout_stats bs
        JOIN bouts b ON bs.bout_id = b.id
    """

    df = pd.read_sql(query, conn)

    opponent_query = """
        SELECT 
            bs.bout_id,
            bs.fighter_id as opponent_id,
            bs.sig_strikes_landed as opp_sig_strikes_landed,
            bs.sig_strikes_attempted as opp_sig_strikes_attempted,
            bs.takedowns_landed as opp_takedowns_landed,
            bs.takedowns_attempted as opp_takedowns_attempted
        FROM bout_stats bs
    """

    opp_df = pd.read_sql(opponent_query, conn)
    conn.close()

    # Merge opponent stats
    df = df.merge(opp_df, on="bout_id", suffixes=("", "_opp"))
    df = df[df["fighter_id"] != df["opponent_id"]]

    # Compute fight duration
    df["fight_mins"] = df.apply(
        lambda row: parse_fight_duration(row["round"], row["time"]), axis=1
    )
    df = df.dropna(subset=["fight_mins"])
    df = df[df["fight_mins"] > 0]

    # Win/finish flags
    df["is_win"] = df["winner_id"] == df["fighter_id"]
    df["is_ko_win"] = df["is_win"] & df["method"].isin(["KO/TKO"])
    df["is_sub_win"] = df["is_win"] & df["method"].isin(["Submission"])
    df["is_decision_win"] = df["is_win"] & df["method"].isin(["Decision"])

    # Aggregate per fighter
    agg = df.groupby("fighter_id").agg(
        total_fights=("bout_id", "count"),
        total_wins=("is_win", "sum"),
        total_ko_wins=("is_ko_win", "sum"),
        total_sub_wins=("is_sub_win", "sum"),
        total_decision_wins=("is_decision_win", "sum"),
        total_knockdowns=("knockdowns", "sum"),
        sig_landed=("sig_strikes_landed", "sum"),
        sig_attempted=("sig_strikes_attempted", "sum"),
        takedowns_landed=("takedowns_landed", "sum"),
        takedowns_attempted=("takedowns_attempted", "sum"),
        opp_sig_landed=("opp_sig_strikes_landed", "sum"),
        opp_td_landed=("opp_takedowns_landed", "sum"),
        opp_td_attempted=("opp_takedowns_attempted", "sum"),
        total_fight_mins=("fight_mins", "sum"),
    ).reset_index()

    # Filter minimum fights
    agg = agg[agg["total_fights"] >= 5]

    # Compute features
    agg["sig_strike_accuracy"] = agg["sig_landed"] / agg["sig_attempted"].replace(0, 1)
    agg["sig_strikes_per_min"] = agg["sig_landed"] / agg["total_fight_mins"]
    agg["strikes_absorbed_per_min"] = agg["opp_sig_landed"] / agg["total_fight_mins"]
    agg["takedown_accuracy"] = agg["takedowns_landed"] / agg["takedowns_attempted"].replace(0, 1)
    agg["takedowns_per_min"] = agg["takedowns_landed"] / agg["total_fight_mins"]
    agg["knockdowns_per_15min"] = (agg["total_knockdowns"] / agg["total_fight_mins"]) * 15
    agg["takedown_defense_rate"] = 1 - (agg["opp_td_landed"] / agg["opp_td_attempted"].replace(0, 1))
    agg["ko_per_15min"] = (agg["total_ko_wins"] / agg["total_fight_mins"]) * 15
    agg["sub_per_15min"] = (agg["total_sub_wins"] / agg["total_fight_mins"]) * 15

    features = agg[[
        "fighter_id",
        "sig_strike_accuracy",
        "sig_strikes_per_min",
        "strikes_absorbed_per_min",
        "takedown_accuracy",
        "takedowns_per_min",
        "knockdowns_per_15min",
        "takedown_defense_rate",
        "ko_per_15min",
        "sub_per_15min",
    ]].copy()

    # Cap extreme values at 99th percentile
    feature_cols = [c for c in features.columns if c != "fighter_id"]
    for col in feature_cols:
        cap = features[col].quantile(0.99)
        features[col] = features[col].clip(upper=cap)

    return features


if __name__ == "__main__":
    features = compute_fighter_features()
    print(f"Fighters with 5+ bouts: {len(features)}")
    print(features.describe())