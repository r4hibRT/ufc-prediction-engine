"""The prediction engine: Glicko plus corrections, selected through the feature gate.

    logit P(A beats B) = b0 * logit(Glicko p) + sum_i b_i * (A_i - B_i)

An L2 logistic regression with no intercept on antisymmetric inputs, so
P(A) + P(B) = 1 exactly. Correction blocks from `features.BLOCKS` enter by
forward selection: a block stays only if its paired walk-forward log-loss gain
has a bootstrap interval entirely below zero and each of its coefficients keeps
one sign in at least STABLE_FOLDS of the validation folds. The regularisation
strength is then chosen on the selected set.

Run:
    python -m src.engine.model
"""

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from src.engine import features, harness

SELECTION_C = 1.0
C_GRID = (0.003, 0.01, 0.03, 0.1, 0.3, 1.0)
STABLE_FOLDS = 6
HOLDOUT_YEAR = 2026

# Frozen from the selection run recorded in docs/engine-plan.md, step 5.
FROZEN_BLOCKS = ("age", "striking")
FROZEN_C = 0.01


def make_model(C):
    # Scaling without centring keeps every input odd, so symmetry survives.
    return make_pipeline(StandardScaler(with_mean=False),
                         LogisticRegression(fit_intercept=False, C=C, max_iter=1000))


def columns(blocks):
    return ["rating"] + [f for b in blocks for f in features.BLOCKS[b]]


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

    result = harness.evaluate(fit_predict, df)
    result["coefs"] = pd.DataFrame(coefs).T
    return result


def sign_stable(coefs, cols):
    pos = (coefs[cols] > 0).sum()
    return bool((np.maximum(pos, len(coefs) - pos) >= STABLE_FOLDS).all())


def select(df, log=print):
    """Forward block selection through the gate, from the rating alone."""
    chosen, current = [], walk_forward(df, [])
    log(harness.summary("rating only", current))
    remaining = list(features.BLOCKS)
    while remaining:
        trials = {b: walk_forward(df, chosen + [b]) for b in remaining}
        accepted = None
        for b in sorted(trials, key=lambda b: trials[b]["pooled"]["log_loss"]):
            c = harness.compare(trials[b], current)
            stable = sign_stable(trials[b]["coefs"], features.BLOCKS[b])
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
        log(harness.summary(" + ".join(["rating"] + chosen), current))
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
    return harness.metrics(test["label"].to_numpy().astype(float), p)


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    df = features.training_frame(features.build())
    chosen, result = select(df)
    grid = tune_c(df, chosen)
    best_c = min(grid, key=grid.get)
    print("C grid:", {c: round(v, 4) for c, v in grid.items()}, "->", best_c)

    final = walk_forward(df, chosen, best_c)
    base = walk_forward(df, [])
    print(harness.summary("final", final))
    c = harness.compare(final, base)
    print(f"final vs rating only: {c['delta']:+.4f} CI [{c['delta_ci'][0]:+.4f}, "
          f"{c['delta_ci'][1]:+.4f}]  better in {c['folds_better']}/{c['folds']} years")
    print(final["coefs"].round(4).to_string())
    print(reliability(final["y"], final["p"]).round(3).to_string())
    print("holdout", HOLDOUT_YEAR, "final:", {k: round(v, 4) for k, v in holdout(df, chosen, best_c).items()})
    print("holdout", HOLDOUT_YEAR, "rating only:", {k: round(v, 4) for k, v in holdout(df, [], best_c).items()})
