"""Fight cards for the site: forecasts, the tale of the tape, and the record.

A card lists its bouts in ufcstats order, main event first, each with the
forecast, the tale of the tape frozen with it, the model insight paragraph, and
the result once fought. The engine's factor contributions stay server-side.

The tape is built by the refresh at forecast time (`bout_tape`) from everything
scraped before the card, so a locked card shows what was known when it locked.
A debutant has no UFC history, so only the physicals from their ufcstats profile
appear.
"""

import math
from datetime import date

from src.api.queries import _rows, get_fighter, get_fighter_stats
from src.engine.predict import fight_calendar_today, load

COIN_FLIP_LOG_LOSS = math.log(2)
# Cards stay listed this many days after fight night, so results can be seen.
RECENT_DAYS = 10

FIGHTER_JOIN = """
    LEFT JOIN fighters fa ON fa.url = p.fighter_a_url
    LEFT JOIN fighters fb ON fb.url = p.fighter_b_url
"""


def _key(url):
    return url.rstrip("/").rsplit("/", 1)[-1]


def _status(event_date, scored, bouts, today):
    if event_date > today:
        return "upcoming"
    return "scored" if scored and scored == bouts else "locked"


def _corner(tape, fighter_id, name):
    corner = dict(tape or {"name": name})
    corner["id"] = corner.get("id") or fighter_id
    return corner


def _result(r):
    if r["result"] is None:
        return None
    winner = None if r["a_won"] is None else ("a" if r["a_won"] else "b")
    return {"outcome": r["result"], "winner": winner, "method": r["method"],
            "round": r["round"], "time": r["time"],
            "correct": None if winner is None else (r["p_a"] >= 0.5) == r["a_won"]}


def _bout(r):
    tape = r["tape"] or {}
    return {
        "id": _key(r["bout_url"]), "position": r["card_position"],
        "weight_class": r["weight_class"],
        "prediction": {"p_a": round(r["p_a"], 4), "p_b": round(1 - r["p_a"], 4),
                       "predicted_at": r["predicted_at"]},
        "market": None,
        "narrative": r["narrative"],
        "result": _result(r),
        "fighters": {"a": _corner(tape.get("a"), r["fighter_a_id"], r["fighter_a_name"]),
                     "b": _corner(tape.get("b"), r["fighter_b_id"], r["fighter_b_name"])},
    }


def get_cards(conn=None):
    """Upcoming cards plus those fought in the last RECENT_DAYS, in date order."""
    today = fight_calendar_today()
    rows = _rows("""
        SELECT event_url, event_name, event_date, COUNT(*) AS bouts,
               COUNT(*) FILTER (WHERE result IS NOT NULL) AS scored,
               MAX(CASE WHEN card_position = 1
                        THEN fighter_a_name || ' vs. ' || fighter_b_name END) AS headliner
        FROM predictions
        WHERE event_date >= %s::date - %s AND result IS DISTINCT FROM 'not_held'
        GROUP BY event_url, event_name, event_date
        ORDER BY event_date, event_url
    """, (today, RECENT_DAYS), conn)
    return [{"id": _key(r["event_url"]), "name": r["event_name"], "date": r["event_date"],
             "bouts": r["bouts"], "headliner": r["headliner"],
             "status": _status(r["event_date"], r["scored"], r["bouts"], today)}
            for r in rows]


def get_card(event_id, conn=None):
    """One card in full, or None if no forecasts exist for it."""
    rows = _rows(f"""
        SELECT p.*, fa.id AS fighter_a_id, fb.id AS fighter_b_id,
               b.method, b.round, b.time
        FROM predictions p {FIGHTER_JOIN}
        LEFT JOIN bouts b ON b.id = p.bout_id
        WHERE p.event_url LIKE %s AND p.result IS DISTINCT FROM 'not_held'
        ORDER BY p.card_position NULLS LAST, p.bout_url
    """, ("%/" + event_id,), conn)
    if not rows:
        return None
    first = rows[0]
    scored = sum(r["result"] is not None for r in rows)
    return {
        "event": {"id": event_id, "name": first["event_name"], "date": first["event_date"],
                  "status": _status(first["event_date"], scored, len(rows),
                                    fight_calendar_today())},
        "model": {"version": first["model_version"]},
        "bouts": [_bout(r) for r in rows],
    }


def get_record(limit=50, conn=None):
    """Summary of scored forecasts on decided bouts, plus the most recent ones."""
    summary = _rows("""
        SELECT COUNT(*) AS n,
               AVG(log_loss) AS log_loss,
               AVG(POWER(CASE WHEN a_won THEN 1 - p_a ELSE p_a END, 2)) AS brier,
               AVG(CASE WHEN (p_a >= 0.5) = a_won THEN 1.0 ELSE 0.0 END) AS favourite_won
        FROM predictions WHERE a_won IS NOT NULL
    """, conn=conn)[0]
    recent = _rows(f"""
        SELECT p.*, fa.id AS fighter_a_id, fb.id AS fighter_b_id,
               b.method, b.round, b.time
        FROM predictions p {FIGHTER_JOIN}
        LEFT JOIN bouts b ON b.id = p.bout_id
        WHERE p.result IS NOT NULL AND p.result <> 'not_held'
        ORDER BY p.event_date DESC, p.card_position NULLS LAST
        LIMIT %s
    """, (limit,), conn)

    art = load()
    return {
        "summary": {k: (float(v) if v is not None else None) for k, v in summary.items()}
                   | {"n": summary["n"], "coin_flip_log_loss": COIN_FLIP_LOG_LOSS},
        "model": {"version": art["version"], "trained_through": art["trained_through"],
                  "validation": art["validation"]},
        "recent": [_bout(r) | {"event_name": r["event_name"], "event_date": r["event_date"]}
                   for r in recent],
    }


# --- the tale of the tape -------------------------------------------------------

# Tape stat key -> key in get_fighter_stats()["rates"].
RATES = {
    "slpm": "sig_strikes_per_min",
    "str_acc": "sig_accuracy",
    "sapm": "sig_absorbed_per_min",
    "td_avg": "takedowns_per_15",
    "td_acc": "takedown_accuracy",
    "td_def": "takedown_defence",
    "sub_avg": "sub_attempts_per_15",
    "kd_avg": "knockdowns_per_15",
}


def _missing(v):
    return v is None or v != v  # NaN and NaT are not equal to themselves


def _num(v):
    return None if _missing(v) else float(v)


def _age(dob, on):
    if _missing(dob):
        return None
    return round((on - dob).days / 365.25, 1)


def _streak(fighter_id, conn):
    """Current run of wins or losses, e.g. "W6"; no contests are skipped."""
    rows = _rows("""
        SELECT CASE WHEN outcome = 'win' AND winner_id = %(f)s THEN 'W'
                    WHEN outcome = 'win' THEN 'L'
                    WHEN outcome = 'draw' THEN 'D' END AS r
        FROM bouts WHERE %(f)s IN (fighter_a_id, fighter_b_id) AND outcome <> 'nc'
        ORDER BY date DESC, id DESC
    """, {"f": fighter_id}, conn)
    results = [r["r"] for r in rows]
    if not results:
        return None
    run = next((i for i, r in enumerate(results) if r != results[0]), len(results))
    return f"{results[0]}{run}"


def fighter_tape(fighter_id, name, on, profile=None, conn=None):
    """One corner of the tape as of `on`; `profile` stands in for a debutant."""
    if fighter_id is None or fighter_id < 0:
        p = profile or {}
        return {"id": None, "name": name, "debut": True, "record": None, "streak": None,
                "age": _age(p.get("dob"), on), "height": _num(p.get("height")),
                "reach": _num(p.get("reach")), "stance": p.get("stance") if isinstance(p.get("stance"), str) else None,
                "days_since_last": None, "rating": None, "stats": None, "wins_by": None}

    f = get_fighter(fighter_id, conn=conn)
    s = get_fighter_stats(fighter_id, conn=conn)
    rates, bouts = s["rates"], s["totals"]["bouts_with_stats"]
    rec = f["record"]
    stats = {k: _num(rates.get(v)) for k, v in RATES.items()}
    stats["avg_fight_min"] = (round(float(rates["minutes_fought"]) / bouts, 2)
                              if bouts and rates.get("minutes_fought") else None)
    return {
        "id": fighter_id, "name": name, "debut": False,
        "record": {"w": rec["wins"], "l": rec["losses"], "d": rec["draws"],
                   "nc": rec["no_contests"]},
        "streak": _streak(fighter_id, conn),
        "age": _age(f["dob"], on),
        "height": _num(f["height"]), "reach": _num(f["reach"]), "stance": f["stance"],
        "days_since_last": (on - rec["last_bout"]).days if rec["last_bout"] else None,
        "rating": None if f["current_rating"] is None else round(float(f["current_rating"])),
        "stats": stats,
        "wins_by": {"ko": rec["ko_wins"], "sub": rec["sub_wins"], "dec": rec["dec_wins"]},
    }


def bout_tape(a_id, a_name, b_id, b_name, on: date, profiles=None, conn=None):
    profiles = profiles or {}
    return {"a": fighter_tape(a_id, a_name, on, profiles.get(a_id), conn),
            "b": fighter_tape(b_id, b_name, on, profiles.get(b_id), conn)}
