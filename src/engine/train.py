"""Building the engine: evaluation, rating tuning, block selection, the artifact.

Everything here decides *what* the engine is; nothing here runs in the weekly
refresh. The frozen result is a JSON artifact in models/ that src/engine/
predict.py loads.

Evaluation. Expanding-window folds by calendar year: train on everything before
a year, predict that year, 2019 to 2025. Pooled out-of-fold log loss selects;
Brier, AUC and calibration are reported. Bouts are oriented at random (odd ids
flipped) because ufcstats lists the winner first 64% of the time.

The model. logit P(A beats B) = b0 * logit(Glicko p) + sum_i b_i * (A_i - B_i):
an L2 logistic regression with no intercept on antisymmetric inputs, so
P(A) + P(B) = 1 exactly. Correction blocks enter by forward selection through a
gate: a block stays only if its paired walk-forward log-loss gain has a
bootstrap interval entirely below zero and each coefficient keeps one sign in
at least STABLE_FOLDS folds. The frozen outcome: rating + age + striking.

Run:
    python -m src.engine.train rating    # grid the rating constants
    python -m src.engine.train select    # forward selection, calibration, holdout
    python -m src.engine.train save      # fit the frozen model, write the artifact
"""

import itertools
import json
from dataclasses import asdict
from datetime import date

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from src.db import get_connection
from src.engine import features
from src.engine.features import BLOCKS, FEATURES, TUNED, Params, build, logit, rating_replay
from src.engine.predict import MODELS_DIR, predict

VALIDATION_YEARS = tuple(range(2019, 2026))
EPS = 1e-6

# A shared debut rating only translates the scale, and tau barely moves volatility
# with one bout per update, so neither is tuned.
GRID = {"initial_rd": (250, 300, 350, 400, 500),
        "initial_volatility": (0.18, 0.24, 0.35, 0.5, 0.7)}

SELECTION_C = 1.0
C_GRID = (0.003, 0.01, 0.03, 0.1, 0.3, 1.0)
STABLE_FOLDS = 6
HOLDOUT_YEAR = 2026

# Frozen from the selection run: rating + age + striking, flat in C from 0.01 to 1.
FROZEN_BLOCKS = ("age", "striking")
FROZEN_C = 0.01


# --- evaluation -----------------------------------------------------------------

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


def column(name):
    """Baseline reading a precomputed probability column, e.g. Glicko alone."""
    return lambda train, val: val[name].to_numpy()


def summary(name, result):
    r = result["pooled"]
    lo, hi = r["log_loss_ci"]
    return (f"{name:<30} log loss {r['log_loss']:.4f} [{lo:.4f}, {hi:.4f}]  "
            f"brier {r['brier']:.4f}  auc {r['auc']:.4f}  ece {r['ece']:.4f}  n={r['n']}")


def training_frame(df):
    """Decided bouts only, orientation balanced (see `balance`)."""
    out = df[df["label"].notna()].copy()
    out["label"] = out["label"].astype(int)
    return balance(out, features=FEATURES + ["rating"], probs=["glicko_p"])


# --- the rating's constants ------------------------------------------------------

def load_bouts():
    conn = get_connection()
    bouts = pd.read_sql("""
        SELECT id AS bout_id, date, fighter_a_id, fighter_b_id, winner_id, method, outcome
        FROM bouts ORDER BY date, id
    """, conn)
    conn.close()
    return bouts


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
    return balance(df, probs=["glicko_p"])


def tune_rating(bouts=None, grid=GRID):
    """Score every constant combination; lower log loss is better."""
    bouts = load_bouts() if bouts is None else bouts
    results = []
    for values in itertools.product(*grid.values()):
        params = Params(**dict(zip(grid, values)))
        df = scoring_frame(bouts, rating_replay(bouts, params))
        raw = evaluate(column("glicko_p"), df)["pooled"]
        cal = evaluate(recalibrated, df)["pooled"]
        results.append({**asdict(params), "log_loss": cal["log_loss"], "raw_log_loss": raw["log_loss"],
                        "auc": cal["auc"], "ece": cal["ece"]})
    return pd.DataFrame(results).sort_values("log_loss", ignore_index=True)


# --- the model -------------------------------------------------------------------

def make_model(C):
    # Scaling without centring keeps every input odd, so symmetry survives.
    return make_pipeline(StandardScaler(with_mean=False),
                         LogisticRegression(fit_intercept=False, C=C, max_iter=1000))


def columns(blocks):
    return ["rating"] + [f for b in blocks for f in BLOCKS[b]]


def coefficients(model, cols):
    """Coefficients per unit of each raw difference; `rating` is b0."""
    scaler, lr = model[0], model[-1]
    return pd.Series(lr.coef_[0] / scaler.scale_, index=cols)


def walk_forward(df, blocks, C=SELECTION_C):
    cols = columns(blocks)
    coefs = {}

    def fit_predict(train, val):
        m = make_model(C).fit(train[cols], train["label"])
        coefs[pd.to_datetime(val["date"]).dt.year.iloc[0]] = coefficients(m, cols)
        return m.predict_proba(val[cols])[:, 1]

    result = evaluate(fit_predict, df)
    result["coefs"] = pd.DataFrame(coefs).T
    return result


def sign_stable(coefs, cols):
    pos = (coefs[cols] > 0).sum()
    return bool((np.maximum(pos, len(coefs) - pos) >= STABLE_FOLDS).all())


def select(df, log=print):
    """Forward block selection through the gate, from the rating alone."""
    chosen, current = [], walk_forward(df, [])
    log(summary("rating only", current))
    remaining = list(BLOCKS)
    while remaining:
        trials = {b: walk_forward(df, chosen + [b]) for b in remaining}
        accepted = None
        for b in sorted(trials, key=lambda b: trials[b]["pooled"]["log_loss"]):
            c = compare(trials[b], current)
            stable = sign_stable(trials[b]["coefs"], BLOCKS[b])
            passed = c["delta_ci"][1] < 0 and stable
            log(f"  + {b:<11} {c['delta']:+.4f} [{c['delta_ci'][0]:+.4f}, {c['delta_ci'][1]:+.4f}]"
                f"  {c['folds_better']}/{c['folds']} yrs  signs {'stable' if stable else 'flip'}"
                f"  {'ACCEPT' if passed else ''}")
            if passed:
                accepted = b
                break
        if accepted is None:
            break
        chosen.append(accepted)
        remaining.remove(accepted)
        current = trials[accepted]
        log(summary(" + ".join(["rating"] + chosen), current))
    return chosen, current


def fit_final(df, blocks=FROZEN_BLOCKS, C=FROZEN_C):
    """The engine, fitted on every decided bout in `df`; returns it with its input columns."""
    cols = columns(blocks)
    return make_model(C).fit(df[cols], df["label"]), cols


def tune_c(df, blocks):
    return {C: walk_forward(df, blocks, C)["pooled"]["log_loss"] for C in C_GRID}


def reliability(y, p, bins=10):
    """Observed win rate against mean prediction, per decile of prediction."""
    edges = np.quantile(p, np.linspace(0, 1, bins + 1))
    idx = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, bins - 1)
    return pd.DataFrame({"p": p, "y": y, "bin": idx}).groupby("bin").agg(
        predicted=("p", "mean"), observed=("y", "mean"), n=("y", "size"))


def holdout(df, blocks, C):
    """Train on everything before HOLDOUT_YEAR, score that year once."""
    year = pd.to_datetime(df["date"]).dt.year
    train, test = df[year < HOLDOUT_YEAR], df[year == HOLDOUT_YEAR]
    cols = columns(blocks)
    p = make_model(C).fit(train[cols], train["label"]).predict_proba(test[cols])[:, 1]
    return metrics(test["label"].to_numpy().astype(float), p)


# --- the artifact ----------------------------------------------------------------

def train_artifact(df=None):
    """Fit the frozen configuration on every decided bout and describe it."""
    raw = build() if df is None else df
    df = training_frame(raw)
    fitted, cols = fit_final(df)
    wf = walk_forward(df, list(FROZEN_BLOCKS), FROZEN_C)
    pooled = {k: (list(v) if isinstance(v, tuple) else float(v)) for k, v in wf["pooled"].items()}
    return {
        "version": date.today().isoformat(),
        "trained_through": str(max(df["date"])),
        "n_train": int(len(df)),
        "columns": cols,
        "coefficients": dict(zip(cols, map(float, coefficients(fitted, cols)))),
        "blocks": list(FROZEN_BLOCKS),
        "C": FROZEN_C,
        "rating_params": asdict(TUNED),
        "feature_constants": {k: getattr(features, k) for k in (
            "K_MINUTES", "K_ATTEMPTS", "K_BOUTS", "DIVISION_MINUTES",
            "DEBUT_LAYOFF_DAYS", "ASSUMED_AGE", "REACH_PER_HEIGHT")},
        "validation": {"years": list(VALIDATION_YEARS), **pooled},
    }, fitted


def save_artifact(artifact, directory=MODELS_DIR):
    directory.mkdir(exist_ok=True)
    path = directory / f"engine-{artifact['version']}.json"
    path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return path


def _print_compare(label, c):
    print(f"{label}: {c['delta']:+.4f} CI [{c['delta_ci'][0]:+.4f}, {c['delta_ci'][1]:+.4f}]"
          f"  better in {c['folds_better']}/{c['folds']} years")


if __name__ == "__main__":
    import argparse
    import warnings
    warnings.filterwarnings("ignore")
    parser = argparse.ArgumentParser(description="Build and justify the forecasting engine.")
    parser.add_argument("task", choices=("rating", "select", "save"))
    task = parser.parse_args().task

    if task == "rating":
        bouts = load_bouts()
        print(tune_rating(bouts).head(15).round(4).to_string(index=False))
        tuned = evaluate(recalibrated, scoring_frame(bouts, rating_replay(bouts, TUNED)))
        standard = evaluate(recalibrated, scoring_frame(bouts, rating_replay(bouts)))
        print(summary("tuned", tuned))
        print(summary("standard constants", standard))
        _print_compare("tuned vs standard", compare(tuned, standard))

    elif task == "select":
        df = training_frame(build())
        chosen, _ = select(df)
        grid = tune_c(df, chosen)
        best_c = min(grid, key=grid.get)
        print("C grid:", {c: round(v, 4) for c, v in grid.items()}, "->", best_c)
        final, base = walk_forward(df, chosen, best_c), walk_forward(df, [])
        print(summary("final", final))
        _print_compare("final vs rating only", compare(final, base))
        print(final["coefs"].round(4).to_string())
        print(reliability(final["y"], final["p"]).round(3).to_string())
        for blocks in (chosen, []):
            result = {k: round(float(v), 4) for k, v in holdout(df, blocks, best_c).items()}
            print(f"holdout {HOLDOUT_YEAR} {' + '.join(['rating', *blocks])}: {result}")

    else:
        raw = build()
        art, fitted = train_artifact(raw)
        df = training_frame(raw)
        gap = np.abs(predict(art, df) - fitted.predict_proba(df[art["columns"]])[:, 1]).max()
        assert gap < 1e-9, f"JSON coefficients disagree with the fitted model by {gap}"
        print(f"saved {save_artifact(art)}  (max gap to sklearn {gap:.1e})")
