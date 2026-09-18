"""Point-in-time correction features for the prediction engine.

One row per bout, every feature an A-minus-B difference of what each fighter
had shown before the bout, so a bout's mirror is its negation. Counts and dates
come from the analytics replay; ratings and opponent strength come from the
engine's own in-memory Glicko (`rating.TUNED`), never the site's ratings table.

Per-minute rates and percentages are shrunk toward a running division prior,
built only from bouts on earlier dates: rate = (count + prior * k) / (exposure
+ k). A debutant therefore carries the division prior, not a missing value.
`build(until)` equals `build()` restricted to earlier dates; `truncation_test`
checks it.

Run:
    python -m src.engine.features
"""

import numpy as np
import pandas as pd

from src.analytics.replay import replay as analytics_replay
from src.analytics.state import fight_seconds
from src.db.connection import get_connection
from src.engine import rating

# Prior strength: minutes of cage time, attempts, or bouts worth of evidence.
K_MINUTES = 15
K_ATTEMPTS = 20
K_BOUTS = 3
# Division prior blended toward the all-division prior until a division has this
# many fighter-minutes of history.
DIVISION_MINUTES = 600
DEBUT_LAYOFF_DAYS = 180
ASSUMED_AGE = 29.5
REACH_PER_HEIGHT = 1.02

# Blocks enter or leave the model together in step 5.
BLOCKS = {
    "age": ["age", "age_over_30"],
    "physical": ["reach", "height"],
    "stance": ["southpaw"],
    "layoff": ["log_layoff"],
    "debut": ["debut"],
    "experience": ["log_experience"],
    "form": ["form", "streak"],
    "schedule": ["opp_rating"],
    "striking": ["slpm", "sapm", "str_acc"],
    "grappling": ["td15", "td_acc", "td_def", "ctrl_share", "sub15"],
    "power": ["kd15", "kd_absorbed15", "ko_loss_rate"],
    "finishing": ["finish_rate"],
}
FEATURES = [f for cols in BLOCKS.values() for f in cols]

# (numerator, denominator) totals behind each shrunk rate's running prior.
PRIOR_TOTALS = {
    "slpm": ("sig_landed", "minutes"),
    "str_acc": ("sig_landed", "sig_attempted"),
    "td15": ("td_landed", "minutes"),
    "td_acc": ("td_landed", "td_attempted"),
    "ctrl_share": ("control_minutes", "minutes"),
    "sub15": ("sub_attempts", "minutes"),
    "kd15": ("knockdowns", "minutes"),
}


def _load():
    conn = get_connection()
    bouts = pd.read_sql("""
        SELECT id AS bout_id, date, fighter_a_id, fighter_b_id, winner_id,
               method, outcome, weight_class, round, time
        FROM bouts ORDER BY date, id
    """, conn)
    fighters = pd.read_sql("SELECT id AS fighter_id, dob, height, reach, stance FROM fighters", conn)
    stats = pd.read_sql("""
        SELECT bout_id, SUM(sig_strikes_landed) AS sig_landed,
               SUM(sig_strikes_attempted) AS sig_attempted,
               SUM(takedowns_landed) AS td_landed, SUM(takedowns_attempted) AS td_attempted,
               SUM(control_time_seconds) / 60.0 AS control_minutes,
               SUM(submission_attempts) AS sub_attempts, SUM(knockdowns) AS knockdowns
        FROM bout_stats GROUP BY bout_id
    """, conn)
    conn.close()
    return bouts, fighters, stats


def division_priors(bouts, stats):
    """Per-bout prior for each rate from bouts on strictly earlier dates."""
    df = bouts[["bout_id", "date", "weight_class", "round", "time"]].merge(stats, on="bout_id")
    secs = [fight_seconds(r, t) for r, t in zip(df["round"], df["time"])]
    df["minutes"] = 2 * pd.Series(secs, index=df.index).astype(float).fillna(0) / 60
    totals = sorted({c for pair in PRIOR_TOTALS.values() for c in pair})
    df[totals] = df[totals].astype(float).fillna(0)
    df["date"] = pd.to_datetime(df["date"])

    glob = df.groupby("date")[totals].sum().cumsum().reset_index()
    div = df.groupby(["weight_class", "date"])[totals].sum()
    div = div.groupby(level="weight_class").cumsum().reset_index().sort_values("date")

    out = bouts[["bout_id", "date", "weight_class"]].assign(date=lambda d: pd.to_datetime(d["date"]))
    out = out.sort_values("date", kind="stable")
    asof = dict(on="date", allow_exact_matches=False)
    out = pd.merge_asof(out, glob, **asof)
    out = pd.merge_asof(out, div, by="weight_class", suffixes=("_g", "_d"), **asof)
    out = out.fillna(0)

    priors = pd.DataFrame({"bout_id": out["bout_id"]})
    weight = np.minimum(out["minutes_d"] / DIVISION_MINUTES, 1.0)
    for name, (num, den) in PRIOR_TOTALS.items():
        g = out[f"{num}_g"] / out[f"{den}_g"].replace(0, np.nan)
        d = out[f"{num}_d"] / out[f"{den}_d"].replace(0, np.nan)
        priors[name] = (weight * d.fillna(g) + (1 - weight) * g).fillna(0)
    priors["td_def"] = 1 - priors["td_acc"]
    priors["sapm"] = priors["slpm"]
    return priors


def _opponent_strength(bouts, ratings):
    """Running mean of each fighter's opponents' pre-bout engine ratings."""
    r = bouts[["bout_id", "date", "fighter_a_id", "fighter_b_id"]].merge(ratings, on="bout_id")
    long = pd.concat([
        pd.DataFrame({"bout_id": r.bout_id, "fighter_id": r.fighter_a_id, "opp": r.rating_b}),
        pd.DataFrame({"bout_id": r.bout_id, "fighter_id": r.fighter_b_id, "opp": r.rating_a}),
    ]).sort_values("bout_id", kind="stable")
    long = long.set_index("bout_id").loc[bouts["bout_id"]].reset_index()
    g = long.groupby("fighter_id")["opp"]
    long["opp_sum"] = g.cumsum() - long["opp"]
    long["opp_n"] = g.cumcount()
    return long[["bout_id", "fighter_id", "opp_sum", "opp_n"]]


def _shrink(count, exposure, prior, k):
    return (count + prior * k) / (exposure + k)


def fighter_features(snaps, fighters, priors, opp):
    """Level features per fighter per bout; differences are taken in `build`."""
    s = (snaps.merge(fighters, on="fighter_id", how="left")
              .merge(priors, on="bout_id", suffixes=("", "_prior"))
              .merge(opp, on=["bout_id", "fighter_id"], how="left"))
    out = s[["bout_id", "fighter_id"]].copy()

    age = (pd.to_datetime(s["date"]) - pd.to_datetime(s["dob"])).dt.days / 365.25
    out["age"] = age.fillna(ASSUMED_AGE)
    out["age_over_30"] = np.maximum(out["age"] - 30, 0)
    out["height"] = s["height"].astype(float)
    out["reach"] = s["reach"].astype(float).fillna(out["height"] * REACH_PER_HEIGHT)
    out["southpaw"] = s["stance"].isin(["Southpaw", "Switch"]).astype(float)

    debut = s["appearances_before"] == 0
    out["debut"] = debut.astype(float)
    out["log_layoff"] = np.log1p(s["layoff_days"].fillna(DEBUT_LAYOFF_DAYS).astype(float))
    out["log_experience"] = np.log1p(s["appearances_before"])

    n_recent = np.minimum(s["bouts_before"], 5)
    out["form"] = _shrink(s["recent_form_5"].fillna(0.5) * n_recent, n_recent, 0.5, K_BOUTS)
    out["streak"] = np.clip(s["win_streak"] - s["loss_streak"], -5, 5)
    out["opp_rating"] = _shrink(s["opp_sum"].fillna(0), s["opp_n"].fillna(0), 1500, K_BOUTS)

    minutes = s["career_seconds"] / 60
    out["slpm"] = _shrink(s["sig_landed"], minutes, s["slpm"], K_MINUTES)
    out["sapm"] = _shrink(s["sig_absorbed"], minutes, s["sapm"], K_MINUTES)
    out["str_acc"] = _shrink(s["sig_landed"], s["sig_attempted"], s["str_acc"], K_ATTEMPTS)
    out["td15"] = 15 * _shrink(s["td_landed"], minutes, s["td15"], K_MINUTES)
    out["td_acc"] = _shrink(s["td_landed"], s["td_attempted"], s["td_acc"], K_ATTEMPTS)
    out["td_def"] = _shrink(s["opp_td_attempted"] - s["opp_td_landed"], s["opp_td_attempted"],
                            s["td_def"], K_ATTEMPTS)
    out["ctrl_share"] = _shrink(s["control_seconds"] / 60, minutes, s["ctrl_share"], K_MINUTES)
    out["sub15"] = 15 * _shrink(s["sub_attempts"], minutes, s["sub15"], K_MINUTES)
    out["kd15"] = 15 * _shrink(s["knockdowns"], minutes, s["kd15"], K_MINUTES)
    out["kd_absorbed15"] = 15 * _shrink(s["knockdowns_absorbed"], minutes, s["kd15"], K_MINUTES)
    out["ko_loss_rate"] = _shrink(s["ko_losses"], s["bouts_before"], 0.15, K_BOUTS)
    out["finish_rate"] = _shrink(s["finishes"], s["wins_before"], 0.5, K_BOUTS)
    return out


def build(until=None, params=rating.TUNED):
    """One row per bout before `until`: identifiers, Glicko probability, differences."""
    bouts, fighters, stats = _load()
    if until is not None:
        until = pd.Timestamp(until).date()
        bouts = bouts[bouts["date"] < until].reset_index(drop=True)
    ratings = rating.replay(bouts, params)
    snaps = pd.DataFrame(analytics_replay(until))
    snaps = snaps[snaps["bout_id"].isin(bouts["bout_id"])]

    levels = fighter_features(snaps, fighters, division_priors(bouts, stats),
                              _opponent_strength(bouts, ratings))
    a = bouts.merge(levels, left_on=["bout_id", "fighter_a_id"], right_on=["bout_id", "fighter_id"])
    b = bouts[["bout_id", "fighter_b_id"]].merge(
        levels, left_on=["bout_id", "fighter_b_id"], right_on=["bout_id", "fighter_id"])
    a, b = a.set_index("bout_id"), b.set_index("bout_id").loc[a["bout_id"]]

    df = a[["date", "fighter_a_id", "fighter_b_id", "winner_id", "outcome", "weight_class"]].copy()
    df[FEATURES] = a[FEATURES].to_numpy() - b[FEATURES].to_numpy()
    df = df.reset_index().merge(ratings[["bout_id", "glicko_p"]], on="bout_id")
    df["rating"] = rating.logit(df["glicko_p"].to_numpy())
    decided = (df["outcome"] == "win") & df["winner_id"].notna()
    df["label"] = np.where(decided, (df["winner_id"] == df["fighter_a_id"]).astype(float), np.nan)
    return df.sort_values(["date", "bout_id"], ignore_index=True)


def training_frame(df):
    """Decided bouts only, orientation balanced (see `harness.balance`)."""
    from src.engine import harness
    out = df[df["label"].notna()].copy()
    out["label"] = out["label"].astype(int)
    return harness.balance(out, features=FEATURES + ["rating"], probs=["glicko_p"])


def truncation_test(until="2020-01-01"):
    """Features built with history cut at `until` must equal the full build there."""
    full = build()
    cut = build(until)
    ref = full[full["date"] < pd.Timestamp(until).date()].reset_index(drop=True)
    cols = FEATURES + ["glicko_p"]
    assert len(ref) == len(cut) and (ref["bout_id"].to_numpy() == cut["bout_id"].to_numpy()).all()
    diff = np.abs(ref[cols].to_numpy() - cut[cols].to_numpy())
    assert np.nanmax(diff) < 1e-9, f"lookahead: max difference {np.nanmax(diff)}"
    return len(cut)


if __name__ == "__main__":
    df = build()
    print(f"{len(df)} bouts, {int(df['label'].notna().sum())} decided")
    print(df[FEATURES].describe().T[["mean", "std", "min", "max"]].round(3).to_string())
    print(f"truncation test passed on {truncation_test()} bouts")
