"""Read-only queries behind the site's fighter and rankings pages.

Two conventions used throughout:

  * A fighter's record counts results that stood; no contests are reported
    separately, matching how MMA records are normally written (14-3, 1 NC).
  * A fighter's division is the one they fought in most, never a one-off
    catchweight (see `primary_division_cte` in src/db.py).
"""

from src.db import MIN_TITLE_EXPERIENCE, get_connection, primary_division_cte

REAL_TITLE_FIGHTS = """
    real_titles AS (
        SELECT b.id, b.winner_id, b.weight_class, b.title_holder_id, b.outcome, b.date
        FROM bouts b
        JOIN bout_snapshots sa ON sa.bout_id = b.id AND sa.fighter_id = b.fighter_a_id
        JOIN bout_snapshots sb ON sb.bout_id = b.id AND sb.fighter_id = b.fighter_b_id
        WHERE b.is_title_fight
          AND GREATEST(sa.appearances_before, sb.appearances_before) >= %(min_title_exp)s
    )
"""

ACTIVE_WINDOW_DAYS = 730

def _rows(sql, params=(), conn=None):
    owned = conn is None
    if owned:
        conn = get_connection()
    cur = conn.cursor()
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    out = [dict(zip(cols, r)) for r in cur.fetchall()]
    cur.close()
    if owned:
        conn.close()
    return out


def _one(sql, params=(), conn=None):
    rows = _rows(sql, params, conn)
    return rows[0] if rows else None


RECORD_SQL = """
    SELECT
        COUNT(*) FILTER (WHERE b.outcome = 'win' AND b.winner_id = %(fid)s)  AS wins,
        COUNT(*) FILTER (WHERE b.outcome = 'win' AND b.winner_id <> %(fid)s) AS losses,
        COUNT(*) FILTER (WHERE b.outcome = 'draw')                           AS draws,
        COUNT(*) FILTER (WHERE b.outcome = 'nc')                             AS no_contests,
        COUNT(*)                                                             AS appearances,
        COUNT(*) FILTER (WHERE b.outcome = 'win' AND b.winner_id = %(fid)s
                         AND b.method = 'KO/TKO')                            AS ko_wins,
        COUNT(*) FILTER (WHERE b.outcome = 'win' AND b.winner_id = %(fid)s
                         AND b.method = 'Submission')                        AS sub_wins,
        COUNT(*) FILTER (WHERE b.outcome = 'win' AND b.winner_id = %(fid)s
                         AND b.method = 'Decision')                          AS dec_wins,
        COUNT(*) FILTER (WHERE b.is_title_fight)                             AS title_fights,
        COUNT(*) FILTER (WHERE b.title_holder_id = %(fid)s
                         AND b.winner_id = %(fid)s)                          AS title_defences,
        MIN(b.date) AS debut, MAX(b.date) AS last_bout
    FROM bouts b
    WHERE b.fighter_a_id = %(fid)s OR b.fighter_b_id = %(fid)s
"""


def search_fighters(q, limit=20, conn=None):
    return _rows("""
        SELECT f.id, f.name, f.stance, f.height, f.reach,
               r.rating AS current_rating, r.date AS last_rated
        FROM fighters f
        LEFT JOIN LATERAL (
            SELECT rating, date FROM ratings
            WHERE fighter_id = f.id ORDER BY date DESC, id DESC LIMIT 1
        ) r ON TRUE
        WHERE f.name ILIKE %s
        ORDER BY (r.rating IS NULL), r.rating DESC NULLS LAST, f.name
        LIMIT %s;
    """, (f"%{q}%", limit), conn)


def get_fighter(fighter_id, conn=None):
    """Profile: physicals, record, current and peak rating, division."""
    profile = _one(f"""
        WITH {primary_division_cte()}
        SELECT f.id, f.name, f.dob, f.height, f.reach, f.stance,
               r.rating AS current_rating, r.rd AS current_rd, r.date AS last_rated,
               p.rating AS peak_rating, p.date AS peak_date,
               (SELECT weight_class FROM primary_division WHERE fid = f.id) AS division
        FROM fighters f
        LEFT JOIN LATERAL (
            SELECT rating, rd, date FROM ratings
            WHERE fighter_id = f.id ORDER BY date DESC, id DESC LIMIT 1
        ) r ON TRUE
        LEFT JOIN LATERAL (
            SELECT rating, date FROM ratings
            WHERE fighter_id = f.id ORDER BY rating DESC LIMIT 1
        ) p ON TRUE
        WHERE f.id = %s;
    """, (fighter_id,), conn)

    if profile is None:
        return None
    profile["record"] = _one(RECORD_SQL, {"fid": fighter_id}, conn)
    return profile


def get_fighter_stats(fighter_id, conn=None):
    """Career aggregates, plus the same numbers as they stood entering the
    fighter's most recent bout (the point-in-time view)."""
    career = _one("""
        SELECT
            COALESCE(SUM(bs.sig_strikes_landed), 0)      AS sig_landed,
            COALESCE(SUM(bs.sig_strikes_attempted), 0)   AS sig_attempted,
            COALESCE(SUM(bs.takedowns_landed), 0)        AS td_landed,
            COALESCE(SUM(bs.takedowns_attempted), 0)     AS td_attempted,
            COALESCE(SUM(bs.submission_attempts), 0)     AS sub_attempts,
            COALESCE(SUM(bs.knockdowns), 0)              AS knockdowns,
            COALESCE(SUM(bs.control_time_seconds), 0)    AS control_seconds,
            COALESCE(SUM(opp.sig_strikes_landed), 0)     AS sig_absorbed,
            COALESCE(SUM(opp.takedowns_landed), 0)       AS opp_td_landed,
            COALESCE(SUM(opp.takedowns_attempted), 0)    AS opp_td_attempted,
            COALESCE(SUM(opp.knockdowns), 0)             AS knockdowns_absorbed,
            COUNT(*)                                     AS bouts_with_stats
        FROM bout_stats bs
        JOIN bout_stats opp ON opp.bout_id = bs.bout_id AND opp.fighter_id <> bs.fighter_id
        WHERE bs.fighter_id = %s;
    """, (fighter_id,), conn)

    latest = _one("""
        SELECT * FROM bout_snapshots
        WHERE fighter_id = %s ORDER BY date DESC LIMIT 1;
    """, (fighter_id,), conn)

    # Cage time comes from the bouts themselves; the latest snapshot holds time
    # entering that bout, which would omit it and inflate every rate.
    seconds = _one("""
        SELECT COALESCE(SUM(
            (b.round - 1) * 300
            + split_part(b.time, ':', 1)::int * 60
            + split_part(b.time, ':', 2)::int
        ), 0) AS total
        FROM bouts b
        WHERE (b.fighter_a_id = %s OR b.fighter_b_id = %s)
          AND b.round IS NOT NULL AND b.time ~ '^[0-9]+:[0-9]+$';
    """, (fighter_id, fighter_id), conn)

    total_seconds = (seconds or {}).get("total") or 0
    minutes = total_seconds / 60.0 if total_seconds else None

    def rate(total, per=1.0):
        if not minutes or total is None:
            return None
        return round(total / minutes * per, 3)

    def pct(num, den):
        if not den:
            return None
        return round(num / den, 3)

    career_rates = {
        "minutes_fought": round(minutes, 1) if minutes else None,
        "sig_strikes_per_min": rate(career["sig_landed"]),
        "sig_absorbed_per_min": rate(career["sig_absorbed"]),
        "striking_differential": (
            None if not minutes else
            round((career["sig_landed"] - career["sig_absorbed"]) / minutes, 3)),
        "sig_accuracy": pct(career["sig_landed"], career["sig_attempted"]),
        "takedowns_per_15": rate(career["td_landed"], 15),
        "takedown_accuracy": pct(career["td_landed"], career["td_attempted"]),
        "takedown_defence": (
            None if not career["opp_td_attempted"] else
            round(1 - career["opp_td_landed"] / career["opp_td_attempted"], 3)),
        # Share of cage time spent in control, not "seconds per minute".
        "control_share": (
            None if not total_seconds else
            round(career["control_seconds"] / total_seconds, 3)),
        "sub_attempts_per_15": rate(career["sub_attempts"], 15),
        "knockdowns_per_15": rate(career["knockdowns"], 15),
        "knockdowns_absorbed_per_15": rate(career["knockdowns_absorbed"], 15),
    }

    return {"totals": career, "rates": career_rates, "entering_last_bout": latest}


def get_rankings_asof(as_of, limit=25, division=None, conn=None):
    """The board as it stood on any past date -- a single query, because every
    rating at every date is already stored."""
    return _rows(f"""
        WITH latest AS (
            SELECT DISTINCT ON (fighter_id) fighter_id, rating, rd, date
            FROM ratings WHERE date <= %s
            ORDER BY fighter_id, date DESC, id DESC
        ),
        {primary_division_cte("WHERE date <= %s")}
        SELECT ROW_NUMBER() OVER (ORDER BY l.rating - 2 * l.rd DESC) AS rank,
               f.id, f.name, ROUND(l.rating, 1) AS rating, ROUND(l.rd, 1) AS rd,
               ROUND(l.rating - 2 * l.rd, 1) AS adjusted, l.date AS last_bout,
               div.weight_class AS division
        FROM latest l
        JOIN fighters f ON f.id = l.fighter_id
        LEFT JOIN primary_division div ON div.fid = l.fighter_id
        WHERE l.date >= %s::date - make_interval(days => %s)
          AND (%s::text IS NULL OR div.weight_class = %s)
        ORDER BY adjusted DESC
        LIMIT %s;
    """, (as_of, as_of, as_of, as_of, ACTIVE_WINDOW_DAYS, division, division, limit), conn)




def get_p4p_rankings(limit=50, min_bouts=8, conn=None):
    """Ranking table built from what an MMA fan actually compares: title wins,
    championship divisions, best win, and the rating with its uncertainty."""
    return _rows(f"""
        WITH {primary_division_cte()},
        {REAL_TITLE_FIGHTS},
        peak AS (
            SELECT DISTINCT ON (fighter_id) fighter_id, rating, rd, date
            FROM ratings ORDER BY fighter_id, rating DESC
        ),
        counts AS (
            SELECT fighter_id, COUNT(*) AS rated_bouts FROM ratings GROUP BY fighter_id
        ),
        record AS (
            SELECT f.id AS fid,
                   COUNT(*) FILTER (WHERE b.outcome = 'win' AND b.winner_id = f.id)  AS wins,
                   COUNT(*) FILTER (WHERE b.outcome = 'win' AND b.winner_id <> f.id) AS losses,
                   COUNT(*) FILTER (WHERE b.outcome = 'draw')                        AS draws
            FROM fighters f
            JOIN bouts b ON b.fighter_a_id = f.id OR b.fighter_b_id = f.id
            GROUP BY f.id
        ),
        titles AS (
            SELECT winner_id AS fid,
                   COUNT(*) AS title_wins,
                   COUNT(*) FILTER (WHERE winner_id = title_holder_id) AS defences,
                   COUNT(DISTINCT weight_class) AS title_divisions
            FROM real_titles
            WHERE outcome = 'win' AND winner_id IS NOT NULL
            GROUP BY winner_id
        ),
        best_win AS (
            SELECT DISTINCT ON (s.fighter_id) s.fighter_id AS fid,
                   opp.name AS best_win_name,
                   ROUND(os.rating_before, 0) AS best_win_rating,
                   b.date AS best_win_date
            FROM bouts b
            JOIN bout_snapshots s  ON s.bout_id = b.id AND s.fighter_id = b.winner_id
            JOIN bout_snapshots os ON os.bout_id = b.id AND os.fighter_id <> b.winner_id
            JOIN fighters opp ON opp.id = os.fighter_id
            WHERE b.outcome = 'win' AND os.rating_before IS NOT NULL
            ORDER BY s.fighter_id, os.rating_before DESC
        ),
        streak AS (
            SELECT fighter_id AS fid, MAX(win_streak) AS best_streak
            FROM bout_snapshots GROUP BY fighter_id
        )
        SELECT ROW_NUMBER() OVER (ORDER BY ranked.score DESC) AS rank, ranked.*
        FROM (
        SELECT (p.rating - 2 * p.rd) AS score,
               f.id, f.name,
               div.weight_class AS division,
               ROUND(p.rating, 0) AS peak_rating,
               ROUND(p.rd, 0)     AS rd,
               ROUND(p.rating - 2 * p.rd, 0) AS adjusted,
               r.wins, r.losses, r.draws,
               COALESCE(t.title_wins, 0)      AS title_wins,
               COALESCE(t.defences, 0)        AS title_defences,
               COALESCE(t.title_divisions, 0) AS title_divisions,
               bw.best_win_name, bw.best_win_rating,
               GREATEST(COALESCE(st.best_streak, 0),
                        CASE WHEN r.wins > 0 THEN 1 ELSE 0 END) AS best_streak,
               c.rated_bouts
        FROM peak p
        JOIN fighters f ON f.id = p.fighter_id
        JOIN counts c ON c.fighter_id = p.fighter_id
        JOIN record r ON r.fid = p.fighter_id
        LEFT JOIN primary_division div ON div.fid = p.fighter_id
        LEFT JOIN titles t ON t.fid = p.fighter_id
        LEFT JOIN best_win bw ON bw.fid = p.fighter_id
        LEFT JOIN streak st ON st.fid = p.fighter_id
        WHERE c.rated_bouts >= %(min_bouts)s
        ) ranked
        ORDER BY ranked.score DESC
        LIMIT %(limit)s;
    """, {"min_bouts": min_bouts, "limit": limit,
          "min_title_exp": MIN_TITLE_EXPERIENCE}, conn)


def get_career_arc(fighter_id, conn=None):
    """Rating trajectory: one point per rated bout, with opponent and result."""
    return _rows("""
        SELECT r.date, r.rating, r.rd, b.method, b.weight_class, o.name AS opponent,
               CASE WHEN b.outcome <> 'win' THEN b.outcome
                    WHEN b.winner_id = %(f)s THEN 'win' ELSE 'loss' END AS result,
               b.is_title_fight, (b.title_holder_id = %(f)s) AS is_defence, b.id AS bout_id
        FROM ratings r
        JOIN bouts b ON r.bout_id = b.id
        JOIN fighters o ON o.id = CASE WHEN b.fighter_a_id = %(f)s
                                       THEN b.fighter_b_id ELSE b.fighter_a_id END
        WHERE r.fighter_id = %(f)s
        ORDER BY r.date ASC, r.id ASC
    """, {"f": fighter_id}, conn)
