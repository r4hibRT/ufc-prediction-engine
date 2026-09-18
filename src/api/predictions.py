"""Read-only views of the prediction engine's prospective record.

Upcoming forecasts come grouped by card, main event first. Each carries its
explanation: the log-odds contribution of the rating and of each correction
block, which sum to the forecast's log-odds. The record reports how frozen
forecasts scored once the results came in.
"""

import math

from src.api.queries import _rows
from src.engine import artifact
from src.engine.features import BLOCKS

COIN_FLIP_LOG_LOSS = math.log(2)

FIGHTER_JOIN = """
    LEFT JOIN fighters fa ON fa.url = p.fighter_a_url
    LEFT JOIN fighters fb ON fb.url = p.fighter_b_url
"""


def _explain(contributions):
    """Column contributions summed into the rating and each correction block."""
    contributions = contributions or {}
    out = {"rating": round(contributions.get("rating", 0.0), 4)}
    for block, cols in BLOCKS.items():
        if any(c in contributions for c in cols):
            out[block] = round(sum(contributions.get(c, 0.0) for c in cols), 4)
    return out


def _bout(r):
    return {
        "bout_url": r["bout_url"], "weight_class": r["weight_class"],
        "card_position": r["card_position"],
        "fighter_a": {"id": r["fighter_a_id"], "name": r["fighter_a_name"]},
        "fighter_b": {"id": r["fighter_b_id"], "name": r["fighter_b_name"]},
        "p_a": round(r["p_a"], 4), "p_b": round(1 - r["p_a"], 4),
        "explanation": _explain(r["contributions"]),
        "model_version": r["model_version"], "predicted_at": r["predicted_at"],
    }


def get_upcoming(conn=None):
    """Every forecast not yet fought, as a list of cards in date order."""
    rows = _rows(f"""
        SELECT p.*, fa.id AS fighter_a_id, fb.id AS fighter_b_id
        FROM predictions p {FIGHTER_JOIN}
        WHERE p.event_date >= CURRENT_DATE AND p.result IS NULL
        ORDER BY p.event_date, p.event_url, p.card_position NULLS LAST
    """, conn=conn)
    cards = {}
    for r in rows:
        card = cards.setdefault(r["event_url"], {
            "event_name": r["event_name"], "event_date": r["event_date"], "bouts": []})
        card["bouts"].append(_bout(r))
    return list(cards.values())


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
        SELECT p.*, fa.id AS fighter_a_id, fb.id AS fighter_b_id
        FROM predictions p {FIGHTER_JOIN}
        WHERE p.result IS NOT NULL
        ORDER BY p.event_date DESC, p.card_position NULLS LAST
        LIMIT %s
    """, (limit,), conn=conn)

    art = artifact.load()
    return {
        "summary": {k: (float(v) if v is not None else None) for k, v in summary.items()}
                   | {"n": summary["n"], "coin_flip_log_loss": COIN_FLIP_LOG_LOSS},
        "model": {"version": art["version"], "trained_through": art["trained_through"],
                  "validation": art["validation"]},
        "recent": [_bout(r) | {"event_name": r["event_name"], "event_date": r["event_date"],
                               "result": r["result"], "a_won": r["a_won"],
                               "log_loss": r["log_loss"]} for r in recent],
    }
