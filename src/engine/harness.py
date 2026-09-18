"""Evaluation harness for the prediction engine. Fixed before any model is fitted.

Every candidate is scored the same way: expanding-window folds by calendar year
(train on everything before the year, predict that year), pooled out-of-fold
log loss as the selection metric, with Brier, AUC and calibration reported.
Data is one row per bout, where `label` means fighter A won. Features must be
antisymmetric (A minus B, or a log-odds), so a bout's mirror is its negation.
"""

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

VALIDATION_YEARS = tuple(range(2019, 2026))
EPS = 1e-6


def mirror(df, features):
    """Both corners of every bout: features negate and the label flips."""
    flipped = df.copy()
    flipped[features] = -flipped[features]
    flipped["label"] = 1 - flipped["label"]
    return pd.concat([df, flipped], ignore_index=True)


def balance(df, features=(), probs=()):
    """Flip odd-numbered bouts so fighter A is not the scrape-order winner 64% of the time.
    Log loss is unaffected for a symmetric model; AUC and calibration need this."""
    out = df.copy()
    odd = (out["bout_id"] % 2 == 1).to_numpy()
    for f in features:
        out.loc[odd, f] = -out.loc[odd, f]
    for c in probs:
        out.loc[odd, c] = 1 - out.loc[odd, c]
    out.loc[odd, "label"] = 1 - out.loc[odd, "label"]
    return out


def folds(df, years=VALIDATION_YEARS):
    year = pd.to_datetime(df["date"]).dt.year
    for y in years:
        train, val = df[year < y], df[year == y]
        if len(val):
            yield y, train, val


def calibration_error(y, p, bins=10):
    """Expected calibration error: bin-weighted gap between predicted and observed."""
    idx = np.clip(np.digitize(p, np.linspace(0, 1, bins + 1)) - 1, 0, bins - 1)
    return sum(abs(p[idx == b].mean() - y[idx == b].mean()) * (idx == b).mean()
               for b in range(bins) if (idx == b).any())


def _losses(y, p):
    p = np.clip(p, EPS, 1 - EPS)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def metrics(y, p):
    p = np.clip(p, EPS, 1 - EPS)
    return {"log_loss": log_loss(y, p, labels=[0, 1]),
            "brier": brier_score_loss(y, p),
            "auc": roc_auc_score(y, p),
            "ece": calibration_error(y, p),
            "n": len(y)}


def bootstrap_ci(values, n=1000, seed=0):
    """95% interval of a mean, resampling bouts (one row per bout)."""
    rng = np.random.default_rng(seed)
    means = [values[rng.integers(0, len(values), len(values))].mean() for _ in range(n)]
    return tuple(np.percentile(means, [2.5, 97.5]))


def evaluate(fit_predict, df, years=VALIDATION_YEARS):
    """Walk-forward score of `fit_predict(train, val) -> P(A wins) per val row`."""
    fold_rows, ys, ps, yrs = [], [], [], []
    for year, train, val in folds(df, years):
        p = np.asarray(fit_predict(train, val), dtype=float)
        y = val["label"].to_numpy().astype(float)
        fold_rows.append({"year": year, **metrics(y, p)})
        ys.append(y); ps.append(p); yrs.append(np.full(len(y), year))
    y, p = np.concatenate(ys), np.concatenate(ps)
    pooled = metrics(y, p)
    pooled["log_loss_ci"] = bootstrap_ci(_losses(y, p))
    return {"pooled": pooled, "folds": pd.DataFrame(fold_rows),
            "y": y, "p": p, "year": np.concatenate(yrs)}


def compare(candidate, incumbent):
    """Paired difference on identical bouts; negative means the candidate is better."""
    diff = _losses(candidate["y"], candidate["p"]) - _losses(incumbent["y"], incumbent["p"])
    by_year = pd.Series(diff).groupby(candidate["year"]).mean()
    return {"delta": diff.mean(), "delta_ci": bootstrap_ci(diff),
            "folds_better": int((by_year < 0).sum()), "folds": len(by_year)}


def coin_flip(train, val):
    return np.full(len(val), 0.5)


def column(name):
    """Baseline reading a precomputed probability column, e.g. Glicko alone."""
    return lambda train, val: val[name].to_numpy()


def summary(name, result):
    r = result["pooled"]
    lo, hi = r["log_loss_ci"]
    return (f"{name:<30} log loss {r['log_loss']:.4f} [{lo:.4f}, {hi:.4f}]  "
            f"brier {r['brier']:.4f}  auc {r['auc']:.4f}  ece {r['ece']:.4f}  n={r['n']}")
