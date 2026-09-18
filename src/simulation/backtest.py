import pandas as pd
import numpy as np
import lightgbm as lgb
from sklearn.model_selection import train_test_split, StratifiedKFold
from sklearn.metrics import brier_score_loss, log_loss, accuracy_score

FEATURE_COLS = [
    # existing core
    "rating_diff", "rd_sum",
    "reach_diff", "height_diff", "age_diff",
    "same_stance",
    "experience_diff", "layoff_diff",
    "division_experience_a", "division_experience_b",
    "is_title_fight",
    "five_round_experience_interaction",
    "win_streak_diff", "recent_form_diff",
    # A. durability
    "ko_loss_rate_diff", "sub_loss_rate_diff", "finish_rate_diff",
    # B. activity
    "fights_last_2yr_diff",
    # C. strength of schedule
    "avg_opponent_rating_diff",
    # D. raw ages (non-differenced, for age-curve nonlinearity)
    "age_a", "age_b",
    # E. physical mismatch magnitude
    "abs_reach_diff",
]

train_df = pd.read_csv("train_data.csv", parse_dates=["date"])
test_df = pd.read_csv("test_data.csv", parse_dates=["date"])

X = train_df[FEATURE_COLS]
y = train_df["label"]

# --- 75/25 split within train for early stopping ---
X_fit, X_val, y_fit, y_val = train_test_split(X, y, test_size=0.25, random_state=42, stratify=y)

params = {
    "objective": "binary",
    "metric": "binary_logloss",
    "verbosity": -1,
    "num_leaves": 15,        # kept small deliberately given modest row count
    "min_data_in_leaf": 50,  # guards against overfitting on ~10k rows
    "learning_rate": 0.03,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
}

train_set = lgb.Dataset(X_fit, label=y_fit)
val_set = lgb.Dataset(X_val, label=y_val, reference=train_set)

model = lgb.train(
    params, train_set,
    num_boost_round=1000,
    valid_sets=[val_set],
    callbacks=[lgb.early_stopping(stopping_rounds=50, verbose=False)]
)

print(f"Best iteration: {model.best_iteration}")

# --- 10-fold CV on training data as a stability check ---
# Note: this is a random (not time-ordered) K-fold WITHIN the training window.
# It's a reasonable stability check on Brier score since every row's features
# are already point-in-time correct (computed from real historical state prior
# to that bout), so folds don't leak future rating info into features. But it's
# not a substitute for the true chronological holdout below, since a model
# fit on later-training-window rows could still validate on earlier ones.
skf = StratifiedKFold(n_splits=10, shuffle=True, random_state=42)
cv_briers = []
for fold_i, (fit_idx, val_idx) in enumerate(skf.split(X, y)):
    X_f, X_v = X.iloc[fit_idx], X.iloc[val_idx]
    y_f, y_v = y.iloc[fit_idx], y.iloc[val_idx]
    fold_train = lgb.Dataset(X_f, label=y_f)
    fold_model = lgb.train(params, fold_train, num_boost_round=model.best_iteration)
    fold_probs = fold_model.predict(X_v)
    cv_briers.append(brier_score_loss(y_v, fold_probs))

print(f"\n10-fold CV Brier scores: {[round(b, 4) for b in cv_briers]}")
print(f"CV Brier mean: {np.mean(cv_briers):.4f}  std: {np.std(cv_briers):.4f}")

# --- Final evaluation on TRUE chronological holdout (test_data.csv) ---
X_test = test_df[FEATURE_COLS]
y_test = test_df["label"]
test_probs = model.predict(X_test)
test_preds = (test_probs >= 0.5).astype(int)

brier = brier_score_loss(y_test, test_probs)
ll = log_loss(y_test, test_probs)
acc = accuracy_score(y_test, test_preds)

print(f"\n=== TRUE HOLDOUT (test_data.csv, chronological) ===")
print(f"LightGBM Brier score: {brier:.4f}")
print(f"LightGBM log loss:    {ll:.4f}")
print(f"LightGBM accuracy:    {acc:.4f}")

# --- Feature importance ---
importance = pd.DataFrame({
    "feature": FEATURE_COLS,
    "gain": model.feature_importance(importance_type="gain")
}).sort_values("gain", ascending=False)
print(f"\nFeature importance (gain):")
print(importance.to_string(index=False))

# After training LightGBM, also load/train the logistic regression and compare
# predictions head-to-head on the SAME test rows.
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

LR_FEATURES = [
    "rating_diff", "rd_sum", "reach_diff", "height_diff", "age_diff",
    "same_stance", "experience_diff", "layoff_diff",
    "division_experience_a", "division_experience_b",
    "is_title_fight", "five_round_experience_interaction",
]

# LR needs no-NaN rows; use only the original 12 features (which have no NaN)
lr_scaler = StandardScaler()
X_lr_train = lr_scaler.fit_transform(train_df[LR_FEATURES])
lr = LogisticRegression(max_iter=1000).fit(X_lr_train, train_df["label"])
lr_probs = lr.predict_proba(lr_scaler.transform(test_df[LR_FEATURES]))[:, 1]

# LightGBM probs on the same test rows = test_probs (already computed above)
diff = np.abs(test_probs - lr_probs)
print(f"\nMean |LGBM_prob - LR_prob| per fight: {diff.mean():.4f}")
print(f"Max  |LGBM_prob - LR_prob| per fight: {diff.max():.4f}")
print(f"Fights where models disagree on favorite (prob crosses 0.5): "
      f"{((test_probs >= 0.5) != (lr_probs >= 0.5)).sum()} / {len(test_probs)}")
print(f"Correlation between the two models' probs: {np.corrcoef(test_probs, lr_probs)[0,1]:.4f}")