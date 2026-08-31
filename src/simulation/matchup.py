import numpy as np
from src.db.connection import get_connection

# Canonical archetype order for this module's matrix and index space.
ARCHETYPES = [
    "Elite Finisher",
    "Pure Striker",
    "Submission Threat",
    "Technical Grappler",
    "Versatile Striker"
]
ARCHETYPE_INDEX = {a: i for i, a in enumerate(ARCHETYPES)}

# cluster.py's GMM label order — prob_0..prob_4 in the fighters table are
# indexed THIS way, not in ARCHETYPES order above. Must reindex before use.
CLUSTER_LABEL_ORDER = {
    0: "Versatile Striker",
    1: "Elite Finisher",
    2: "Submission Threat",
    3: "Technical Grappler",
    4: "Pure Striker"
}

SMOOTHING_K = 20   # Bayesian smoothing strength for matchup matrix


def build_matchup_matrix():
    """Build the 5x5 archetype win-rate matrix from historical bouts."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        WITH all_bouts AS (
            SELECT 
                f_a.archetype as archetype_a,
                f_b.archetype as archetype_b,
                CASE WHEN b.winner_id = b.fighter_a_id THEN 1 ELSE 0 END as a_wins
            FROM bouts b
            JOIN fighters f_a ON b.fighter_a_id = f_a.id
            JOIN fighters f_b ON b.fighter_b_id = f_b.id
            WHERE f_a.archetype IS NOT NULL
            AND f_b.archetype IS NOT NULL
            AND b.winner_id IS NOT NULL
        ),
        forward AS (
            SELECT archetype_a, archetype_b, COUNT(*) as bouts, SUM(a_wins) as wins
            FROM all_bouts GROUP BY archetype_a, archetype_b
        ),
        reverse AS (
            SELECT archetype_b as archetype_a, archetype_a as archetype_b,
                   COUNT(*) as bouts, SUM(1 - a_wins) as wins
            FROM all_bouts GROUP BY archetype_a, archetype_b
        )
        SELECT archetype_a, archetype_b, SUM(bouts) as total_bouts, SUM(wins) as total_wins
        FROM (SELECT * FROM forward UNION ALL SELECT * FROM reverse) all_data
        GROUP BY archetype_a, archetype_b
    """)

    rows = cur.fetchall()
    cur.close()
    conn.close()

    matrix = np.full((5, 5), 0.5)

    for archetype_a, archetype_b, total_bouts, total_wins in rows:
        if archetype_a not in ARCHETYPE_INDEX or archetype_b not in ARCHETYPE_INDEX:
            continue
        i = ARCHETYPE_INDEX[archetype_a]
        j = ARCHETYPE_INDEX[archetype_b]
        smoothed = (float(total_wins) + SMOOTHING_K * 0.5) / (float(total_bouts) + SMOOTHING_K)
        matrix[i][j] = smoothed

    return matrix


def reindex_probs(fighter):
    """
    fighter['prob_0']..['prob_4'] are indexed by cluster.py's GMM label order
    (CLUSTER_LABEL_ORDER). Remap into this module's ARCHETYPE_INDEX order
    so they can be safely multiplied against build_matchup_matrix()'s output.

    fighter: dict with keys 'prob_0' through 'prob_4'
    Returns: list of 5 floats, indexed per ARCHETYPES/ARCHETYPE_INDEX
    """
    reindexed = [0.0] * 5
    for cluster_label, archetype_name in CLUSTER_LABEL_ORDER.items():
        matrix_idx = ARCHETYPE_INDEX[archetype_name]
        reindexed[matrix_idx] = fighter[f"prob_{cluster_label}"]
    return reindexed


def compute_style_probability(probs_a, probs_b, matrix):
    """
    probs_a, probs_b: lists of 5 floats, already in ARCHETYPE_INDEX order
    (i.e. output of reindex_probs — do not pass raw prob_0..prob_4 dicts here)
    """
    style_prob = 0.0
    for i in range(5):
        for j in range(5):
            style_prob += probs_a[i] * probs_b[j] * matrix[i][j]
    return style_prob


if __name__ == "__main__":
    matrix = build_matchup_matrix()
    print("Archetype matchup matrix (with Bayesian smoothing):")
    print(f"{'':25}", end="")
    for a in ARCHETYPES:
        print(f"{a[:8]:>10}", end="")
    print()
    for i, a in enumerate(ARCHETYPES):
        print(f"{a:25}", end="")
        for j in range(5):
            print(f"{matrix[i][j]:>10.3f}", end="")
        print()