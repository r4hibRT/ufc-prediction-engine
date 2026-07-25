import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
from src.clustering.features import compute_fighter_features

FEATURE_COLS = [
    "sig_strike_accuracy",
    "sig_strikes_per_min",
    "strikes_absorbed_per_min",
    "takedown_accuracy",
    "takedowns_per_min",
    "knockdowns_per_15min",
    "takedown_defense_rate",
    "ko_per_15min",
    "sub_per_15min"
]


def run_feature_analysis():
    print("Loading features...")
    features = compute_fighter_features()

    # Check all feature cols exist
    missing = [c for c in FEATURE_COLS if c not in features.columns]
    if missing:
        print(f"Missing features: {missing}")
        print("Available columns:", features.columns.tolist())
        return

    X = features[FEATURE_COLS].copy()

    print(f"\nFighters with 5+ bouts: {len(X)}")

    # === 1. VARIANCE ANALYSIS ===
    print("\n=== VARIANCE ANALYSIS ===")
    variances = X.var().sort_values(ascending=False)
    print("Feature variances (higher = more discriminating):")
    for feat, var in variances.items():
        print(f"  {feat}: {var:.4f}")

    low_variance = variances[variances < 0.01]
    if len(low_variance) > 0:
        print(f"\nLow variance features (< 0.01): {low_variance.index.tolist()}")
    else:
        print("\nNo low variance features detected")

    # === 2. CORRELATION ANALYSIS ===
    print("\n=== CORRELATION ANALYSIS ===")
    corr = X.corr().round(2)
    print(corr.to_string())

    print("\nHighly correlated pairs (|r| > 0.7):")
    found = False
    for i in range(len(corr.columns)):
        for j in range(i + 1, len(corr.columns)):
            val = corr.iloc[i, j]
            if abs(val) > 0.7:
                print(f"  {corr.columns[i]} vs {corr.columns[j]}: {val}")
                found = True
    if not found:
        print("  None found")

    # === 3. PCA ANALYSIS ===
    print("\n=== PCA ANALYSIS ===")
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    pca = PCA()
    pca.fit(X_scaled)

    explained = pca.explained_variance_ratio_
    cumulative = np.cumsum(explained)

    print("Variance explained per component:")
    for i, (exp, cum) in enumerate(zip(explained, cumulative)):
        print(f"  PC{i+1}: {exp:.3f} ({cum:.3f} cumulative)")
        if cum >= 0.95:
            print(f"\n  → {i+1} components explain 95% of variance")
            break

    print("\nPCA feature loadings (top contributors per component):")
    loadings = pd.DataFrame(
        pca.components_[:3].T,
        index=FEATURE_COLS,
        columns=[f"PC{i+1}" for i in range(3)]
    )
    print(loadings.round(3).to_string())


if __name__ == "__main__":
    run_feature_analysis()