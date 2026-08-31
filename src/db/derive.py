"""Columns computed from the bouts table rather than scraped.

Like the ratings, these are a pure function of the bout history, so they are
recomputed wholesale rather than updated incrementally.
"""

from src.db.connection import get_connection


def derive_title_defences(conn=None):
    """Mark title fights where a participant walked in holding the belt.

    ufcstats does not say whether a title fight is a defence, so `is_defence`
    sat hardcoded False on all 8,833 bouts. It is recoverable: walk each
    division's title fights in order, track who last won one, and a bout is a
    defence when either fighter is that person.

    A draw or no contest leaves the belt where it was, which is how the sport
    actually works -- the champion retains.
    """
    owned = conn is None
    if owned:
        conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, weight_class, fighter_a_id, fighter_b_id, winner_id, outcome
        FROM bouts
        WHERE is_title_fight = TRUE
        ORDER BY date ASC, id ASC;
    """)
    rows = cur.fetchall()

    champion = {}
    defences = []
    new_titles = []

    for bout_id, weight_class, a_id, b_id, winner_id, outcome in rows:
        holder = champion.get(weight_class)
        is_defence = holder is not None and holder in (a_id, b_id)

        (defences if is_defence else new_titles).append(bout_id)

        # The belt only changes hands on a decisive result.
        if outcome == "win" and winner_id is not None:
            champion[weight_class] = winner_id

    cur.execute("UPDATE bouts SET is_defence = FALSE WHERE is_title_fight = TRUE;")
    if defences:
        cur.execute(
            "UPDATE bouts SET is_defence = TRUE WHERE id = ANY(%s);",
            (defences,)
        )

    if owned:
        conn.commit()
    cur.close()
    if owned:
        conn.close()

    return {
        "title_fights": len(rows),
        "defences": len(defences),
        "vacant_or_new": len(new_titles),
        "divisions": len(champion),
    }


if __name__ == "__main__":
    result = derive_title_defences()
    print(f"Title fights:      {result['title_fights']}")
    print(f"  defences:        {result['defences']}")
    print(f"  vacant / new:    {result['vacant_or_new']}")
    print(f"  divisions seen:  {result['divisions']}")
