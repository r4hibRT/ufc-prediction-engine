"""SQL fragments shared across query layers.

Lives under db/ so both src/ratings/queries.py and src/api/queries.py can use
it without either importing the other.
"""

# A one-off catchweight bout should never define a fighter's home division.
NON_DIVISIONS = ("Catch Weight", "Open Weight")


def primary_division_cte(date_filter=""):
    """Home division by bout count, not latest bout. Inferring from the latest
    misfiled anyone who moved up once -- Usman read as a middleweight."""
    return f"""
        primary_division AS (
            SELECT DISTINCT ON (fid) fid, weight_class
            FROM (
                SELECT fid, weight_class, COUNT(*) AS n, MAX(date) AS last_date,
                       (weight_class NOT IN {NON_DIVISIONS!r}) AS is_division
                FROM (
                    SELECT fighter_a_id AS fid, weight_class, date FROM bouts {date_filter}
                    UNION ALL
                    SELECT fighter_b_id, weight_class, date FROM bouts {date_filter}
                ) all_bouts
                WHERE weight_class IS NOT NULL
                GROUP BY fid, weight_class
            ) counts
            ORDER BY fid, is_division DESC, n DESC, last_date DESC
        )
    """
