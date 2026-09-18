"""Persist and load the frozen prediction engine.

The engine is linear, so the artifact is plain JSON rather than a pickle: input
columns, coefficients per unit of each raw difference, the rating and feature
constants it was built with, and its validation metrics. Inference needs no
sklearn: P(A wins) = sigmoid(sum of coefficient * difference). Files are
versioned by training date under `models/`.

Run:
    python -m src.engine.artifact
"""

import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from src.engine import features, harness, model, rating

MODELS_DIR = Path(__file__).resolve().parents[2] / "models"


def train(df=None):
    """Fit the frozen configuration on every decided bout and describe it."""
    raw = features.build() if df is None else df
    df = features.training_frame(raw)
    fitted, cols = model.fit_final(df)
    wf = model.walk_forward(df, list(model.FROZEN_BLOCKS), model.FROZEN_C)
    pooled = {k: (list(v) if isinstance(v, tuple) else float(v)) for k, v in wf["pooled"].items()}
    return {
        "version": date.today().isoformat(),
        "trained_through": str(max(df["date"])),
        "n_train": int(len(df)),
        "columns": cols,
        "coefficients": dict(zip(cols, map(float, model.coefficients(fitted, cols)))),
        "blocks": list(model.FROZEN_BLOCKS),
        "C": model.FROZEN_C,
        "rating_params": asdict(rating.TUNED),
        "feature_constants": {k: getattr(features, k) for k in (
            "K_MINUTES", "K_ATTEMPTS", "K_BOUTS", "DIVISION_MINUTES",
            "DEBUT_LAYOFF_DAYS", "ASSUMED_AGE", "REACH_PER_HEIGHT")},
        "validation": {"years": list(harness.VALIDATION_YEARS), **pooled},
    }, fitted


def save(artifact, directory=MODELS_DIR):
    directory.mkdir(exist_ok=True)
    path = directory / f"engine-{artifact['version']}.json"
    path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return path


def load(path=None, directory=MODELS_DIR):
    """A given artifact, or the newest one in `directory`."""
    path = path or max(directory.glob("engine-*.json"))
    return json.loads(Path(path).read_text(encoding="utf-8"))


def predict(artifact, rows):
    """P(fighter A wins) for rows holding the artifact's difference columns."""
    coefs = pd.Series(artifact["coefficients"])
    z = rows[coefs.index].to_numpy(dtype=float) @ coefs.to_numpy()
    return 1 / (1 + np.exp(-z))


def contributions(artifact, row):
    """Per-column share of one bout's log-odds, for explaining a prediction."""
    coefs = pd.Series(artifact["coefficients"])
    return row[coefs.index].astype(float) * coefs


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    art, fitted = train()
    df = features.training_frame(features.build())
    gap = np.abs(predict(art, df) - fitted.predict_proba(df[art["columns"]])[:, 1]).max()
    assert gap < 1e-9, f"JSON coefficients disagree with the fitted model by {gap}"
    print(f"saved {save(art)}  (max gap to sklearn {gap:.1e})")
    print(json.dumps({k: art[k] for k in ("trained_through", "n_train", "coefficients")}, indent=2))
