"""Recompute the site's Glicko-2 ratings from scratch into the ratings table.

Standard Glicko-2 updated per bout: a win scores 1, a draw 0.5, and a no-contest
or overturned result is void. The earlier title weight, upset discount, rating
cap and era factor were removed so the rating measures results alone. The site
keeps long-memory constants for historical rankings; the prediction engine
replays its own tuned rating in memory (src/engine/rating.py).
"""

from src.db.connection import get_connection
from src.ratings.glicko2 import (
    Glicko2Fighter, update_ratings, _expected_score, _scale_down,
    RATING_PERIOD_DAYS,
)

def get_bouts_chronological(cur):
    cur.execute("""
        SELECT id, date, fighter_a_id, fighter_b_id, winner_id, method, outcome
        FROM bouts
        ORDER BY date ASC, id ASC
    """)
    return cur.fetchall()


def determine_outcome(winner_id, fighter_a_id, method, outcome):
    """Fighter A's score: 1, 0.5 or 0; None leaves both ratings untouched."""
    if outcome == 'win' and winner_id is not None:
        return 1.0 if winner_id == fighter_a_id else 0.0
    if outcome == 'draw' and method != 'Overturned':
        return 0.5
    return None


def elapsed_periods(previous_date, bout_date):
    if previous_date is None:
        return 1.0
    days = (bout_date - previous_date).days
    return max(days, 0) / RATING_PERIOD_DAYS


def apply_cap(old_rating, new_fighter):
    """Bound a bout's rating move in both directions. Inert at INITIAL_RD 150,
    but it fired on 28% of moves at 350, so symmetry matters if that is retuned."""
    delta = new_fighter.rating - old_rating
    if abs(delta) > MAX_RATING_CHANGE:
        capped = old_rating + math.copysign(MAX_RATING_CHANGE, delta)
        return Glicko2Fighter(capped, new_fighter.rd, new_fighter.volatility)
    return new_fighter


def run_ratings():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("DELETE FROM ratings;")

    bouts = get_bouts_chronological(cur)
    print(f"Processing {len(bouts)} bouts...")

    fighters = {}
    last_bout_date = {}
    rating_rows = []

    for bout in bouts:
        bout_id, date_, fighter_a_id, fighter_b_id, winner_id, method, bout_outcome = bout

        if fighter_a_id not in fighters:
            fighters[fighter_a_id] = Glicko2Fighter()
        if fighter_b_id not in fighters:
            fighters[fighter_b_id] = Glicko2Fighter()

        # Rating periods since each last competed; a debut counts as one.
        periods_a = elapsed_periods(last_bout_date.get(fighter_a_id), date_)
        periods_b = elapsed_periods(last_bout_date.get(fighter_b_id), date_)

        fighter_a = fighters[fighter_a_id]
        fighter_b = fighters[fighter_b_id]

        mu_a, phi_a = _scale_down(fighter_a)
        mu_b, phi_b = _scale_down(fighter_b)
        expected_a = _expected_score(mu_a, mu_b, phi_b)
        expected_b = 1 - expected_a

        outcome = determine_outcome(winner_id, fighter_a_id, method, bout_outcome)
        if outcome is None:
            continue

        new_a, new_b = update_ratings(fighter_a, fighter_b, outcome, periods_a, periods_b)

        fighters[fighter_a_id] = new_a
        fighters[fighter_b_id] = new_b
        last_bout_date[fighter_a_id] = date_
        last_bout_date[fighter_b_id] = date_

        rating_rows.append((fighter_a_id, bout_id, date_, new_a.rating, new_a.rd, new_a.volatility, expected_a))
        rating_rows.append((fighter_b_id, bout_id, date_, new_b.rating, new_b.rd, new_b.volatility, expected_b))

    cur.executemany("""
        INSERT INTO ratings (fighter_id, bout_id, date, rating, rd, volatility, expected_score)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (fighter_id, bout_id) DO NOTHING
    """, rating_rows)

    conn.commit()
    cur.close()
    conn.close()

    print(f"Ratings complete. {len(fighters)} fighters rated. {len(rating_rows)} rows inserted.")


if __name__ == "__main__":
    run_ratings()