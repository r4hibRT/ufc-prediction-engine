import pandas as pd
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import brier_score_loss, log_loss, accuracy_score

FEATURE_COLS = [
    "rating_diff", "rd_sum", "reach_diff", "height_diff", "age_diff",
    "same_stance", "experience_diff", "layoff_diff",
    "division_experience_a", "division_experience_b",
    "is_title_fight", "five_round_experience_interaction",
]


def load_data():
    train_df = pd.read_csv("train_data.csv", parse_dates=["date"])
    test_df = pd.read_csv("test_data.csv", parse_dates=["date"])
    return train_df, test_df


def fit_model(train_df):
    X_train = train_df[FEATURE_COLS]
    y_train = train_df["label"]

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)

    model = LogisticRegression(max_iter=1000)
    model.fit(X_train_scaled, y_train)

    return model, scaler


def evaluate(model, scaler, test_df):
    X_test = test_df[FEATURE_COLS]
    y_test = test_df["label"]
    X_test_scaled = scaler.transform(X_test)

    probs = model.predict_proba(X_test_scaled)[:, 1]
    preds = (probs >= 0.5).astype(int)

    brier = brier_score_loss(y_test, probs)
    ll = log_loss(y_test, probs)
    acc = accuracy_score(y_test, preds)

    # Every bout appears twice (mirrored), so row count overstates sample size.
    n_fights = test_df["bout_id"].nunique()
    print(f"Test set: {len(test_df)} rows = {n_fights} unique fights")
    print(f"Test Brier score: {brier:.4f}  (lower is better, 0.25 = uninformative baseline)")
    print(f"Test log loss:    {ll:.4f}")
    print(f"Test accuracy:    {acc:.4f}")

    return probs, y_test


def print_coefficients(model, scaler):
    print("\nFeature coefficients (standardized scale):")
    coefs = sorted(zip(FEATURE_COLS, model.coef_[0]), key=lambda x: -abs(x[1]))
    for feat, coef in coefs:
        print(f"  {feat:<35}{coef:+.4f}")
    print(f"  {'intercept':<35}{model.intercept_[0]:+.4f}")


def reliability_diagram(probs, y_test, n_bins=10):
    print("\nReliability diagram (predicted vs. actual win rate, by bucket):")
    bins = np.linspace(0, 1, n_bins + 1)
    bin_ids = np.digitize(probs, bins) - 1
    bin_ids = np.clip(bin_ids, 0, n_bins - 1)

    df = pd.DataFrame({"prob": probs, "actual": y_test.values, "bin": bin_ids})
    summary = df.groupby("bin").agg(
        n=("actual", "count"),
        avg_predicted=("prob", "mean"),
        avg_actual=("actual", "mean")
    )
    print(summary.round(3).to_string())


if __name__ == "__main__":
    train_df, test_df = load_data()
    model, scaler = fit_model(train_df)

    probs, y_test = evaluate(model, scaler, test_df)
    print_coefficients(model, scaler)
    reliability_diagram(probs, y_test)
