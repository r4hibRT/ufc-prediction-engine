"""Glicko-2, implemented from scratch, and the site's ratings built with it.

Standard Glicko-2 (Glickman, 2012) updated per bout rather than per rating
period: a win scores 1, a draw 0.5, and a no-contest or overturned result is
void. Uncertainty (RD) grows with time out of the cage, measured in six-month
rating periods and capped at the starting RD, so a long layoff never reads as
"more unknown than a debutant".

The site's ratings use long-memory constants suited to historical rankings and
are recomputed from scratch into the `ratings` table on every refresh. The
forecasting engine replays the same equations in memory with its own tuned
constants (src/engine/features.py) and never reads this table.

Run:
    python -m src.ratings
"""

import math

from src.db import get_connection

TAU = 0.5
INITIAL_RATING = 1500
INITIAL_RD = 150
INITIAL_VOLATILITY = 0.06
SCALE = 173.7178

# One rating period in days. Six months matches UFC cadence and is what turns
# a layoff into growing uncertainty.
RATING_PERIOD_DAYS = 182.5


class Glicko2Fighter:
    def __init__(self, rating=INITIAL_RATING, rd=INITIAL_RD, volatility=INITIAL_VOLATILITY):
        self.rating = rating
        self.rd = rd
        self.volatility = volatility


def _scale_down(fighter):
    return (fighter.rating - 1500) / SCALE, fighter.rd / SCALE


def _g(phi):
    return 1 / math.sqrt(1 + 3 * phi ** 2 / math.pi ** 2)


def _expected_score(mu, mu_j, phi_j):
    return 1 / (1 + math.exp(-_g(phi_j) * (mu - mu_j)))


def _new_volatility(phi, volatility, v, delta, tau):
    """Glickman's step 5: the Illinois algorithm on f(x) = 0."""
    a = math.log(volatility ** 2)

    def f(x):
        ex = math.exp(x)
        return (ex * (delta ** 2 - phi ** 2 - v - ex) / (2 * (phi ** 2 + v + ex) ** 2)
                - (x - a) / tau ** 2)

    A = a
    if delta ** 2 > phi ** 2 + v:
        B = math.log(delta ** 2 - phi ** 2 - v)
    else:
        k = 1
        while f(a - k * tau) < 0:
            k += 1
        B = a - k * tau

    fa, fb = f(A), f(B)
    for _ in range(100):
        if abs(B - A) <= 1e-6:
            break
        C = A + (A - B) * fa / (fb - fa)
        fc = f(C)
        if fc * fb <= 0:
            A, fa = B, fb
        else:
            fa /= 2
        B, fb = C, fc
    return math.exp(A / 2)


def update_ratings(fighter, opponent, outcome, periods_fighter=1.0, periods_opponent=1.0,
                   tau=TAU, max_rd=INITIAL_RD):
    """Update both ratings for one bout; outcome is 1.0/0.5/0.0 for the first
    fighter. periods_* are rating periods since each last competed."""
    results = []
    for f, opp, s, periods in ((fighter, opponent, outcome, periods_fighter),
                               (opponent, fighter, 1 - outcome, periods_opponent)):
        mu, phi = _scale_down(f)
        mu_j, phi_j = _scale_down(opp)
        e = _expected_score(mu, mu_j, phi_j)
        v = 1 / (_g(phi_j) ** 2 * e * (1 - e))
        delta = v * (_g(phi_j) * (s - e))
        volatility = _new_volatility(phi, f.volatility, v, delta, tau)

        # Uncertainty grows with time, clamped so a long layoff cannot exceed
        # "completely unknown".
        phi_star = min(math.sqrt(phi ** 2 + volatility ** 2 * max(periods, 0.0)), max_rd / SCALE)
        new_phi = 1 / math.sqrt(1 / phi_star ** 2 + 1 / v)
        new_mu = mu + new_phi ** 2 * (_g(phi_j) * (s - e))
        results.append(Glicko2Fighter(new_mu * SCALE + 1500, new_phi * SCALE, volatility))
    return results[0], results[1]


def score(outcome, winner_id, fighter_a_id, method):
    """Fighter A's score: 1, 0.5 or 0; None leaves both ratings untouched."""
    if outcome == "win" and winner_id is not None:
        return 1.0 if winner_id == fighter_a_id else 0.0
    if outcome == "draw" and method != "Overturned":
        return 0.5
    return None


def elapsed_periods(previous_date, bout_date):
    """Rating periods since a fighter last competed; a debut counts as one."""
    if previous_date is None:
        return 1.0
    return max((bout_date - previous_date).days, 0) / RATING_PERIOD_DAYS


def run_ratings(log=print):
    """Rebuild the site's rating history from the first bout, into `ratings`."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM ratings;")
    cur.execute("""
        SELECT id, date, fighter_a_id, fighter_b_id, winner_id, method, outcome
        FROM bouts ORDER BY date ASC, id ASC
    """)
    bouts = cur.fetchall()

    fighters, last, rows = {}, {}, []
    for bout_id, date_, a_id, b_id, winner_id, method, outcome in bouts:
        a = fighters.setdefault(a_id, Glicko2Fighter())
        b = fighters.setdefault(b_id, Glicko2Fighter())
        mu_a, _ = _scale_down(a)
        mu_b, phi_b = _scale_down(b)
        expected_a = _expected_score(mu_a, mu_b, phi_b)

        s = score(outcome, winner_id, a_id, method)
        if s is None:
            continue
        new_a, new_b = update_ratings(a, b, s, elapsed_periods(last.get(a_id), date_),
                                      elapsed_periods(last.get(b_id), date_))
        fighters[a_id], fighters[b_id] = new_a, new_b
        last[a_id] = last[b_id] = date_
        rows.append((a_id, bout_id, date_, new_a.rating, new_a.rd, new_a.volatility, expected_a))
        rows.append((b_id, bout_id, date_, new_b.rating, new_b.rd, new_b.volatility, 1 - expected_a))

    cur.executemany("""
        INSERT INTO ratings (fighter_id, bout_id, date, rating, rd, volatility, expected_score)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (fighter_id, bout_id) DO NOTHING
    """, rows)
    conn.commit()
    cur.close()
    conn.close()
    log(f"Ratings rebuilt: {len(fighters)} fighters, {len(rows)} rows.")
    return {"fighters": len(fighters), "rows": len(rows)}


if __name__ == "__main__":
    run_ratings()
