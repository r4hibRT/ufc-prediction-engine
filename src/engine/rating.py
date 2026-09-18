"""Glicko-2 rating layer for the prediction engine, replayed in memory.

Standard Glicko-2 updated per bout: a win scores 1, a draw 0.5, and a no-contest
or overturned result is void. None of the site's engineered adjustments (title
weight, upset discount, rating cap, era factor) are applied, because context
belongs in the corrections layer. Nothing is written to the database.

The pre-bout probability combines both fighters' uncertainty, g(sqrt(phi_a^2 +
phi_b^2)), so it is symmetric in the corners. `tune()` picks the constants by
walk-forward log loss after a one-parameter recalibration, which is how the
model consumes the rating.

Run:
    python -m src.engine.rating
"""

import itertools
import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from src.db.connection import get_connection
from src.engine import harness
from src.ratings.glicko2 import (
    INITIAL_VOLATILITY, RATING_PERIOD_DAYS, Glicko2Fighter, _g, _scale_down, update_ratings,
)

EPS = 1e-6
# A shared debut rating only translates the scale, and tau barely moves volatility
# with one bout per update, so neither is tuned.
GRID = {"initial_rd": (250, 300, 350, 400, 500),
        "initial_volatility": (0.18, 0.24, 0.35, 0.5, 0.7)}


@dataclass(frozen=True)
class Params:
    initial_rd: float = 150
    initial_volatility: float = INITIAL_VOLATILITY
    tau: float = 0.5


# Chosen by tune(); the raw probability is overconfident, so the model's b0 is about 0.48.
TUNED = Params(initial_rd=400, initial_volatility=0.7)


def load_bouts():
    conn = get_connection()
    bouts = pd.read_sql("""
        SELECT id AS bout_id, date, fighter_a_id, fighter_b_id, winner_id, method, outcome
        FROM bouts ORDER BY date, id
    """, conn)
    conn.close()
    return bouts


def score(bout):
    """Result for fighter A: 1, 0.5 or 0; None when the bout is void."""
    if bout.outcome == "win" and not pd.isna(bout.winner_id):
        return 1.0 if bout.winner_id == bout.fighter_a_id else 0.0
    if bout.outcome == "draw" and bout.method != "Overturned":
        return 0.5
    return None


def _pre_bout_phi(fighter, periods, max_phi):
    return min(math.sqrt(_scale_down(fighter)[1] ** 2
                         + fighter.volatility ** 2 * periods), max_phi)


def replay(bouts, params=Params()):
    """Pre-bout rating state for every bout and Glicko's P(A wins), in date order."""
    fighters, last = {}, {}
    max_phi = params.initial_rd / 173.7178
    rows = []
    for b in bouts.itertuples(index=False):
        pair = []
        for fid in (b.fighter_a_id, b.fighter_b_id):
            f = fighters.get(fid) or Glicko2Fighter(1500, params.initial_rd,
                                                    params.initial_volatility)
            prev = last.get(fid)
            periods = 1.0 if prev is None else max((b.date - prev).days, 0) / RATING_PERIOD_DAYS
            pair.append((f, periods, _pre_bout_phi(f, periods, max_phi), prev is None))
        (fa, per_a, phi_a, new_a), (fb, per_b, phi_b, new_b) = pair

        mu_diff = (fa.rating - fb.rating) / 173.7178
        p = 1 / (1 + math.exp(-_g(math.hypot(phi_a, phi_b)) * mu_diff))
        rows.append((b.bout_id, fa.rating, fb.rating, phi_a * 173.7178, phi_b * 173.7178,
                     new_a, new_b, p))

        s = score(b)
        if s is None:
            continue
        fighters[b.fighter_a_id], fighters[b.fighter_b_id] = update_ratings(
            fa, fb, s, per_a, per_b, tau=params.tau, max_rd=params.initial_rd)
        last[b.fighter_a_id] = last[b.fighter_b_id] = b.date

    return pd.DataFrame(rows, columns=["bout_id", "rating_a", "rating_b", "rd_a", "rd_b",
                                       "debut_a", "debut_b", "glicko_p"])


def logit(p):
    p = np.clip(p, EPS, 1 - EPS)
    return np.log(p / (1 - p))


def recalibrated(train, val):
    """Glicko alone with the model's calibration dial: P = sigmoid(b0 * logit(p))."""
    lr = LogisticRegression(fit_intercept=False, C=1e6)
    lr.fit(logit(train["glicko_p"].to_numpy())[:, None], train["label"])
    return lr.predict_proba(logit(val["glicko_p"].to_numpy())[:, None])[:, 1]


def scoring_frame(bouts, ratings):
    """Decided bouts only, orientation balanced so AUC and calibration are meaningful."""
    df = bouts.merge(ratings, on="bout_id")
    df = df[(df["outcome"] == "win") & df["winner_id"].notna()].copy()
    df["label"] = (df["winner_id"] == df["fighter_a_id"]).astype(int)
    return harness.balance(df, probs=["glicko_p"])


def tune(bouts=None, grid=GRID):
    """Score every constant combination; lower log loss is better."""
    bouts = load_bouts() if bouts is None else bouts
    results = []
    for values in itertools.product(*grid.values()):
        params = Params(**dict(zip(grid, values)))
        df = scoring_frame(bouts, replay(bouts, params))
        raw = harness.evaluate(harness.column("glicko_p"), df)["pooled"]
        cal = harness.evaluate(recalibrated, df)["pooled"]
        results.append({**asdict(params), "log_loss": cal["log_loss"], "raw_log_loss": raw["log_loss"],
                        "auc": cal["auc"], "ece": cal["ece"]})
    return pd.DataFrame(results).sort_values("log_loss", ignore_index=True)


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    bouts = load_bouts()
    table = tune(bouts)
    print(table.head(15).round(4).to_string(index=False))

    best = Params(**{k: float(table.iloc[0][k]) for k in GRID})
    tuned = harness.evaluate(recalibrated, scoring_frame(bouts, replay(bouts, best)))
    standard = harness.evaluate(recalibrated, scoring_frame(bouts, replay(bouts)))
    print(harness.summary("tuned " + str(best), tuned))
    print(harness.summary("standard constants", standard))
    c = harness.compare(tuned, standard)
    print(f"tuned vs standard: {c['delta']:+.4f} CI [{c['delta_ci'][0]:+.4f}, "
          f"{c['delta_ci'][1]:+.4f}]  better in {c['folds_better']}/{c['folds']} years")
