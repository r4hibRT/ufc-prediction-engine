from src.db.connection import get_connection


def get_peak_rating(fighter_id):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT rating, rd, date
        FROM ratings
        WHERE fighter_id = %s
        ORDER BY rating DESC
        LIMIT 1;
    """, (fighter_id,))

    row = cur.fetchone()
    cur.close()
    conn.close()

    if row is None:
        return None
    return {"rating": row[0], "rd": row[1], "date": row[2]}


def get_current_rating(fighter_id):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT rating, rd, volatility, date
        FROM ratings
        WHERE fighter_id = %s
        ORDER BY date DESC, id DESC
        LIMIT 1;
    """, (fighter_id,))

    row = cur.fetchone()
    cur.close()
    conn.close()

    if row is None:
        return None
    return {"rating": row[0], "rd": row[1], "volatility": row[2], "date": row[3]}


def get_career_arc(fighter_id):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT r.date, r.rating, r.rd, b.method, b.weight_class,
               f_a.name as opponent_name
        FROM ratings r
        JOIN bouts b ON r.bout_id = b.id
        JOIN fighters f_a ON (
            CASE
                WHEN b.fighter_a_id = %s THEN b.fighter_b_id
                ELSE b.fighter_a_id
            END = f_a.id
        )
        WHERE r.fighter_id = %s
        ORDER BY r.date ASC, r.id ASC;
    """, (fighter_id, fighter_id))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    return [
        {
            "date": row[0],
            "rating": row[1],
            "rd": row[2],
            "method": row[3],
            "weight_class": row[4],
            "opponent": row[5]
        }
        for row in rows
    ]


def get_peak_rankings(limit=25, min_bouts=8):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        WITH peak_bout AS (
            SELECT
                fighter_id,
                rating,
                rd,
                ROW_NUMBER() OVER (PARTITION BY fighter_id ORDER BY rating DESC) AS rn
            FROM ratings
        ),
        bout_counts AS (
            SELECT fighter_id, COUNT(*) AS total_bouts
            FROM ratings
            GROUP BY fighter_id
        )
        SELECT
            f.id,
            f.name,
            pb.rating AS peak_rating,
            pb.rd AS rd_at_peak,
            ROUND(pb.rating - (pb.rd * 2), 2) AS adjusted_peak,
            r2.rating AS current_rating,
            r2.rd AS current_rd,
            bc.total_bouts,
            (SELECT b.weight_class FROM bouts b
             JOIN ratings r3 ON r3.bout_id = b.id
             WHERE r3.fighter_id = f.id
             ORDER BY r3.date DESC LIMIT 1) AS primary_division
        FROM peak_bout pb
        JOIN fighters f ON f.id = pb.fighter_id
        JOIN bout_counts bc ON bc.fighter_id = pb.fighter_id
        JOIN LATERAL (
            SELECT rating, rd
            FROM ratings
            WHERE fighter_id = f.id
            ORDER BY date DESC, id DESC
            LIMIT 1
        ) r2 ON true
        WHERE pb.rn = 1
          AND bc.total_bouts >= %s
        ORDER BY (pb.rating - (pb.rd * 2)) DESC
        LIMIT %s;
    """, (min_bouts, limit))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    return [
        {
            "fighter_id": row[0],
            "name": row[1],
            "peak_rating": round(row[2], 2),
            "rd_at_peak": round(row[3], 2),
            "adjusted_peak": row[4],
            "current_rating": row[5],
            "current_rd": row[6],
            "total_bouts": row[7],
            "primary_division": row[8],
            "rank": i + 1
        }
        for i, row in enumerate(rows)
    ]

def get_biggest_upsets(limit=10):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT 
            f_winner.name as winner,
            f_loser.name as loser,
            b.date,
            b.method,
            b.weight_class,
            r_loser.expected_score as loser_expected,
            r_winner.rating as winner_rating_after,
            r_loser.rating as loser_rating_after
        FROM ratings r_winner
        JOIN ratings r_loser ON r_winner.bout_id = r_loser.bout_id
            AND r_winner.fighter_id != r_loser.fighter_id
        JOIN bouts b ON r_winner.bout_id = b.id
        JOIN fighters f_winner ON r_winner.fighter_id = f_winner.id
        JOIN fighters f_loser ON r_loser.fighter_id = f_loser.id
        WHERE b.winner_id = r_winner.fighter_id
        AND r_loser.expected_score > 0.7
        ORDER BY r_loser.expected_score DESC
        LIMIT %s;
    """, (limit,))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    return [
        {
            "winner": row[0],
            "loser": row[1],
            "date": row[2],
            "method": row[3],
            "weight_class": row[4],
            "loser_win_probability": round(float(row[5]), 3),
        }
        for row in rows
    ]