"""Tale of the tape: what each fighter brings to a bout, frozen with its forecast.

Built by the refresh at forecast time from everything scraped before the card,
so a locked card shows what was known when it locked. A debutant has no UFC
history, so only the physicals scraped from their ufcstats profile appear.
"""

from datetime import date

from src.api.queries import _rows, get_fighter, get_fighter_stats

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
