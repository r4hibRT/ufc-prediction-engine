"""SQL fragments shared across query layers.

Lives under db/ so both src/ratings/queries.py and src/api/queries.py can use
it without either importing the other.
"""

# A one-off catchweight bout should never define a fighter's home division.
NON_DIVISIONS = ("Catch Weight", "Open Weight")


def primary_division_cte(date_filter=""):
    """A fighter's home division: most bouts, not most recent bout.

    Inferring from the latest bout misfiled anyone who moved up once -- Usman
    read as a middleweight, Makhachev as a welterweight, St-Pierre as a
    middleweight. Ranking by bout count (preferring real divisions, then
    recency as a tie-break) files a fighter where they actually competed.
    """
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
