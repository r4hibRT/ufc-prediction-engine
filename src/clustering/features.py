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

    # Pull all bout stats with bout context
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

    # Get opponent stats by self-joining
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
    df = df.merge(
        opp_df,
        on="bout_id",
        suffixes=("", "_opp")
    )
    df = df[df["fighter_id"] != df["opponent_id"]]

    # Compute fight duration
    df["fight_mins"] = df.apply(
        lambda row: parse_fight_duration(row["round"], row["time"]), axis=1
    )
    df = df.dropna(subset=["fight_mins"])
    df = df[df["fight_mins"] > 0]

    # Win/finish flags
    df["is_win"] = df["winner_id"] == df["fighter_id"]
    df["is_finish"] = df["is_win"] & df["method"].isin(["KO/TKO", "Submission"])

    # Aggregate per fighter
    agg = df.groupby("fighter_id").agg(
        total_fights=("bout_id", "count"),
        total_wins=("is_win", "sum"),
        total_finishes=("is_finish", "sum"),
        total_knockdowns=("knockdowns", "sum"),
        sig_landed=("sig_strikes_landed", "sum"),
        sig_attempted=("sig_strikes_attempted", "sum"),
        takedowns_landed=("takedowns_landed", "sum"),
        takedowns_attempted=("takedowns_attempted", "sum"),
        submission_attempts=("submission_attempts", "sum"),
        control_time_seconds=("control_time_seconds", "sum"),
        opp_sig_landed=("opp_sig_strikes_landed", "sum"),
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
    agg["submission_attempts_per_min"] = agg["submission_attempts"] / agg["total_fight_mins"]
    agg["knockdowns_per_fight"] = agg["total_knockdowns"] / agg["total_fights"]
    agg["finish_rate"] = agg["total_finishes"] / agg["total_wins"].replace(0, 1)
    agg["control_time_per_min"] = agg["control_time_seconds"] / 60 / agg["total_fight_mins"]

    features = agg[[
        "fighter_id",
        "sig_strike_accuracy",
        "sig_strikes_per_min",
        "strikes_absorbed_per_min",
        "takedown_accuracy",
        "takedowns_per_min",
        "submission_attempts_per_min",
        "knockdowns_per_fight",
        "finish_rate",
        "control_time_per_min"
    ]]

    return features


if __name__ == "__main__":
    features = compute_fighter_features()
    print(f"Fighters with 5+ bouts: {len(features)}")
    print(features.describe())