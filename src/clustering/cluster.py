import pandas as pd
import numpy as np
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
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
    "submission_attempts_per_min",
    "knockdowns_per_fight",
    "finish_rate",
    "control_time_per_min"
]

# Archetype labels — fill in after inspecting clusters
ARCHETYPE_LABELS = {
    0: "Unknown",
    1: "Unknown",
    2: "Unknown",
    3: "Unknown",
    4: "Unknown",
    5: "Unknown",
}


def find_best_k(scaled_features, k_range=range(2, 11)):
    print("Finding best k via silhouette score...")
    scores = {}
    for k in k_range:
        kmeans = KMeans(n_clusters=k, random_state=42, n_init=10)
        labels = kmeans.fit_predict(scaled_features)
        score = silhouette_score(scaled_features, labels)
        scores[k] = score
        print(f"  k={k}: silhouette={score:.4f}")

    best_k = max(scores, key=scores.get)
    print(f"\nBest k: {best_k} (silhouette={scores[best_k]:.4f})")
    return best_k, scores


def run_clustering(k=None):
    # Load features
    features = compute_fighter_features()
    fighter_ids = features["fighter_id"].values
    X = features[FEATURE_COLS].values

    # Scale
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # Find best k if not provided
    if k is None:
        k, scores = find_best_k(X_scaled)

    # Final clustering on scaled features
    print(f"\nRunning final K-Means with k={k}...")
    kmeans = KMeans(n_clusters=k, random_state=42, n_init=10)
    cluster_labels = kmeans.fit_predict(X_scaled)

    # UMAP for visualisation only
    print("Computing UMAP embedding...")
    reducer = umap.UMAP(n_components=2, random_state=42)
    embedding = reducer.fit_transform(X_scaled)

    # Build results dataframe
    results = pd.DataFrame({
        "fighter_id": fighter_ids,
        "cluster": cluster_labels,
        "archetype": [ARCHETYPE_LABELS.get(c, "Unknown") for c in cluster_labels],
        "umap_x": embedding[:, 0],
        "umap_y": embedding[:, 1]
    })

    # Print cluster sizes
    print("\nCluster sizes:")
    for c in sorted(results["cluster"].unique()):
        size = len(results[results["cluster"] == c])
        print(f"  Cluster {c}: {size} fighters")

    return results, features, X_scaled, kmeans


def store_archetypes(results):
    conn = get_connection()
    cur = conn.cursor()

    for _, row in results.iterrows():
        cur.execute("""
            UPDATE fighters
            SET archetype = %s,
                umap_x = %s,
                umap_y = %s
            WHERE id = %s
        """, (row["archetype"], float(row["umap_x"]), float(row["umap_y"]), int(row["fighter_id"])))

    conn.commit()
    cur.close()
    conn.close()
    print(f"Stored archetypes for {len(results)} fighters")


def inspect_clusters(results, features, n=5):
    """Print top fighters per cluster for manual labelling."""
    conn = get_connection()
    cur = conn.cursor()

    merged = results.merge(features, on="fighter_id")

    for c in sorted(results["cluster"].unique()):
        cluster_df = merged[merged["cluster"] == c]
        fighter_ids = cluster_df["fighter_id"].tolist()

        cur.execute("""
            SELECT id, name FROM fighters
            WHERE id = ANY(%s)
            LIMIT %s
        """, (fighter_ids, n))

        names = [row[1] for row in cur.fetchall()]
        print(f"\nCluster {c} ({len(cluster_df)} fighters) — sample: {', '.join(names)}")
        print(cluster_df[FEATURE_COLS].mean().round(3).to_string())

    cur.close()
    conn.close()


if __name__ == "__main__":
    results, features, X_scaled, kmeans = run_clustering()
    inspect_clusters(results, features)