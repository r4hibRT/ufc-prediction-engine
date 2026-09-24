"""Fight cards for the site: forecasts, the tale of the tape, and the record.

A card lists its bouts in ufcstats order, main event first, each with the
forecast, the tale of the tape frozen with it, the model insight paragraph, and
the result once fought. The engine's factor contributions stay server-side.

The record is written for fans rather than statisticians: which picks came in,
which were upsets, and how the confident calls fared, card by card.

The tape is built by the refresh at forecast time (`bout_tape`) from everything
scraped before the card, so a locked card shows what was known when it locked.
A debutant has no UFC history, so only the physicals from their ufcstats profile
appear.
"""

from datetime import date

from src.api.queries import _rows, get_fighter, get_fighter_stats
from src.engine.predict import fight_calendar_today

# Cards stay listed this many days after fight night, so results can be seen.
RECENT_DAYS = 10

# How a fought bout is judged, on every page: a forecast this close to even is a
# toss-up rather than a pick, and a lost pick is an upset only if we gave it UPSET.
TOSS_UP = 0.01
UPSET = 0.65
# Picks at or above this win probability count as confident calls on the record.
CONFIDENT = 0.70

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


def _call(r):
    """Our pick, the winner and the verdict: one rule for the cards and the record,
    so a result reads the same everywhere."""
    p_a = float(r["p_a"])
    pick = None if abs(p_a - 0.5) < TOSS_UP else ("a" if p_a > 0.5 else "b")
    winner = None if r["a_won"] is None else ("a" if r["a_won"] else "b")
    decided = pick is not None and winner is not None
    return {"pick": pick, "pick_chance": round(max(p_a, 1 - p_a), 4), "winner": winner,
            "correct": pick == winner if decided else None,
            "upset": decided and pick != winner and max(p_a, 1 - p_a) >= UPSET}


def _result(r):
    if r["result"] is None:
        return None
    return {"outcome": r["result"], "method": r["method"], "round": r["round"],
            "time": r["time"], **_call(r)}


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


def _scored(r):
    """One fought bout as a fan reads it: who we picked, who won, and how."""
    call = _call(r)
    p_a = float(r["p_a"])
    return {
        "fighters": {"a": {"id": r["fighter_a_id"], "name": r["fighter_a_name"]},
                     "b": {"id": r["fighter_b_id"], "name": r["fighter_b_name"]}},
        **call,
        "winner_chance": None if call["winner"] is None
                         else round(p_a if call["winner"] == "a" else 1 - p_a, 4),
        "outcome": r["result"], "method": r["method"], "round": r["round"], "time": r["time"],
    }


def _tally(bouts):
    picked = [b for b in bouts if b["correct"] is not None]
    confident = [b for b in picked if b["pick_chance"] >= CONFIDENT]
    return {"picks": len(picked), "correct": sum(b["correct"] for b in picked),
            "confident_picks": len(confident),
            "confident_correct": sum(b["correct"] for b in confident)}


def get_record(conn=None):
    """Every scored forecast, card by card, newest first, with fan-level totals."""
    rows = _rows(f"""
        SELECT p.event_url, p.event_name, p.event_date, p.fighter_a_name, p.fighter_b_name,
               p.p_a, p.a_won, p.result, fa.id AS fighter_a_id, fb.id AS fighter_b_id,
               b.method, b.round, b.time
        FROM predictions p {FIGHTER_JOIN}
        LEFT JOIN bouts b ON b.id = p.bout_id
        WHERE p.result IS NOT NULL AND p.result <> 'not_held'
        ORDER BY p.event_date DESC, p.event_url, p.card_position NULLS LAST
    """, conn=conn)

    cards = {}
    for r in rows:
        card = cards.setdefault(r["event_url"], {
            "id": _key(r["event_url"]), "name": r["event_name"], "date": r["event_date"],
            "bouts": []})
        card["bouts"].append(_scored(r))
    cards = list(cards.values())
    for card in cards:
        card.update(_tally(card["bouts"]))

    everything = [b for card in cards for b in card["bouts"]]
    upsets = [b | {"card": card["name"]} for card in cards for b in card["bouts"]
              if b["correct"] is False]
    return {
        "totals": _tally(everything) | {"confident_threshold": CONFIDENT},
        "biggest_upset": min(upsets, key=lambda b: b["winner_chance"]) if upsets else None,
        "cards": cards,
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
