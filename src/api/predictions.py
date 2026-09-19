"""Read-only views of the prediction engine's forecasts, shaped as fight cards.

A card lists its bouts in ufcstats order, main event first, each with the
forecast, the tale of the tape frozen with it, and the result once fought. The
engine's factor contributions stay server-side; explaining a forecast is left
to the narrative layer. The record reports how locked forecasts have scored.
"""

import math

from src.api.queries import _rows
from src.engine import artifact
from src.engine.predict import fight_calendar_today

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
        "narrative": None,
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

    art = artifact.load()
    return {
        "summary": {k: (float(v) if v is not None else None) for k, v in summary.items()}
                   | {"n": summary["n"], "coin_flip_log_loss": COIN_FLIP_LOG_LOSS},
        "model": {"version": art["version"], "trained_through": art["trained_through"],
                  "validation": art["validation"]},
        "recent": [_bout(r) | {"event_name": r["event_name"], "event_date": r["event_date"]}
                   for r in recent],
    }
