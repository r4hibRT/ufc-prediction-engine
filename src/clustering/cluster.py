import os
os.environ["OMP_NUM_THREADS"] = "1"

import pandas as pd
import numpy as np
from sklearn.preprocessing import StandardScaler
from sklearn.mixture import GaussianMixture
from sklearn.metrics import silhouette_score
import umap
from src.clustering.features import compute_fighter_features
from src.db.connection import get_connection

FEATURE_COLS = [
    "sig_strike_accuracy",
    "sig_strikes_per_min",
    "strikes_absorbed_per_min",
    "takedown_accuracy",
    "takedowns_per_min",
    "knockdowns_per_15min",
    "takedown_defense_rate",
    "ko_per_15min",
    "sub_per_15min",
]

ARCHETYPE_LABELS = {

}  # filled in after inspecting clusters


def find_best_k(X_scaled, k_range=range(2, 11)):
    print("Finding best k via BIC and silhouette score...")
    bic_scores = {}
    sil_scores = {}

    for k in k_range:
        gmm = GaussianMixture(n_components=k, random_state=42, n_init=10)
        labels = gmm.fit_predict(X_scaled)
        bic_scores[k] = gmm.bic(X_scaled)
        sil_scores[k] = silhouette_score(X_scaled, labels)
        print(f"  k={k}: BIC={bic_scores[k]:.1f}, silhouette={sil_scores[k]:.4f}")

    best_k_bic = min(bic_scores, key=bic_scores.get)
    best_k_sil = max(sil_scores, key=sil_scores.get)
    print(f"\nBest k by BIC: {best_k_bic}")
    print(f"Best k by silhouette: {best_k_sil}")

    return best_k_bic, bic_scores, sil_scores


def run_clustering(k=None):
    features = compute_fighter_features()
    fighter_ids = features["fighter_id"].values
    X = features[FEATURE_COLS].values

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    if k is None:
        k, bic_scores, sil_scores = find_best_k(X_scaled)
        print(f"\nUsing k={k} (BIC)")

    print(f"\nRunning final GMM with k={k}...")
    gmm = GaussianMixture(n_components=k, random_state=42, n_init=10)
    gmm.fit(X_scaled)

    cluster_labels = gmm.predict(X_scaled)
    probabilities = gmm.predict_proba(X_scaled)

    print("Computing UMAP embedding...")
    reducer = umap.UMAP(n_components=2, random_state=42)
    embedding = reducer.fit_transform(X_scaled)

    results = pd.DataFrame({
        "fighter_id": fighter_ids,
        "cluster": cluster_labels,
        "archetype": [ARCHETYPE_LABELS.get(c, "Unknown") for c in cluster_labels],
        "umap_x": embedding[:, 0],
        "umap_y": embedding[:, 1]
    })

    for i in range(k):
        results[f"prob_{i}"] = probabilities[:, i]

    print("\nCluster sizes:")
    for c in sorted(results["cluster"].unique()):
        size = len(results[results["cluster"] == c])
        print(f"  Cluster {c}: {size} fighters")

    return results, features, X_scaled, gmm, scaler


def inspect_clusters(results, features, n=8):
    conn = get_connection()
    cur = conn.cursor()

    merged = results.merge(features, on="fighter_id")

    for c in sorted(results["cluster"].unique()):
        cluster_df = merged[merged["cluster"] == c]
        fighter_ids_list = cluster_df["fighter_id"].tolist()

        cur.execute("""
            SELECT f.name, MAX(r.rating) as peak
            FROM fighters f
            JOIN ratings r ON r.fighter_id = f.id
            WHERE f.id = ANY(%s)
            GROUP BY f.id, f.name
            ORDER BY peak DESC
            LIMIT %s
        """, (fighter_ids_list, n))

        names = [row[0] for row in cur.fetchall()]
        print(f"\nCluster {c} ({len(cluster_df)} fighters)")
        print(f"Top fighters: {', '.join(names)}")
        print(cluster_df[FEATURE_COLS].mean().round(3).to_string())

    cur.close()
    conn.close()


def store_archetypes(results):
    conn = get_connection()
    cur = conn.cursor()

    for _, row in results.iterrows():
        cur.execute("""
            UPDATE fighters
            SET archetype = %s,
                umap_x = %s,
                umap_y = %s,
                prob_0 = %s,
                prob_1 = %s,
                prob_2 = %s,
                prob_3 = %s,
                prob_4 = %s
            WHERE id = %s
        """, (
            row["archetype"],
            float(row["umap_x"]),
            float(row["umap_y"]),
            float(row["prob_0"]),
            float(row["prob_1"]),
            float(row["prob_2"]),
            float(row["prob_3"]),
            float(row["prob_4"]),
            int(row["fighter_id"])
        ))

    conn.commit()
    cur.close()
    conn.close()
    print(f"Stored archetypes and probabilities for {len(results)} fighters")


if __name__ == "__main__":
    results, features, X_scaled, gmm, scaler = run_clustering(k=5)
    inspect_clusters(results, features)
    store_archetypes(results)