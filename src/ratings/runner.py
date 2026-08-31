import math

from src.db.connection import get_connection
from src.ratings.glicko2 import (
    Glicko2Fighter, update_ratings, _expected_score, _scale_down,
    RATING_PERIOD_DAYS,
)

SKIP_METHODS = {'Overturned', 'Other'}
MAX_RATING_CHANGE = 100
UPSET_THRESHOLD = 0.25
UPSET_DISCOUNT = 0.85
TITLE_FIGHT_WEIGHT = 1.1
BASELINE_FINISH_RATE = 0.502


def get_bouts_chronological(cur):
    cur.execute("""
        SELECT id, date, fighter_a_id, fighter_b_id, winner_id, method, is_title_fight, outcome
        FROM bouts
        ORDER BY date ASC, id ASC
    """)
    return cur.fetchall()


def compute_rolling_finish_rates(bouts):
    finish_rates = {}
    decided = [
        (b[1], 1 if b[5] in ('KO/TKO', 'Submission') else 0)
        for b in bouts
        if b[4] is not None
    ]
    for bout in bouts:
        bout_id = bout[0]
        bout_date = bout[1]
        try:
            window_start = bout_date.replace(year=bout_date.year - 3)
        except ValueError:
            window_start = bout_date.replace(year=bout_date.year - 3, day=28)
        window_bouts = [
            is_finish for date_, is_finish in decided
            if window_start <= date_ < bout_date
        ]
        if len(window_bouts) >= 30:
            finish_rates[bout_id] = sum(window_bouts) / len(window_bouts)
        else:
            finish_rates[bout_id] = BASELINE_FINISH_RATE
    return finish_rates


def era_adjust_outcome(outcome, bout_id, finish_rates):
    if outcome == 0.5:
        return 0.5
    era_rate = finish_rates.get(bout_id, BASELINE_FINISH_RATE)
    adjustment = BASELINE_FINISH_RATE / era_rate
    adjustment = min(adjustment, 1.0)
    return 0.5 + (outcome - 0.5) * adjustment




def determine_outcome(winner_id, fighter_a_id, method, is_title_fight, expected_a, outcome=None):
    if method in SKIP_METHODS:
        return None
    if winner_id is None:
        # A draw is a real result worth half a win; a no contest never
        # happened and must not move either rating.
        if outcome == 'draw':
            return 0.5
        if outcome == 'nc':
            return None
        # Rows predating the outcome column: fall back to the old guess.
        return 0.5 if method == 'Decision' else None

    # Score the WINNER, then map back to fighter A's perspective. Scoring from
    # A's side meant the title weighting only ever reached fighter A, who wins
    # 342 of 462 decided title fights purely because of scrape order.
    winner_is_a = (winner_id == fighter_a_id)
    expected_winner = expected_a if winner_is_a else 1.0 - expected_a

    score = TITLE_FIGHT_WEIGHT if is_title_fight else 1.0
    if expected_winner < UPSET_THRESHOLD:
        score = score * UPSET_DISCOUNT

    return score if winner_is_a else 1.0 - score


def elapsed_periods(previous_date, bout_date):
    if previous_date is None:
        return 1.0
    days = (bout_date - previous_date).days
    return max(days, 0) / RATING_PERIOD_DAYS


def apply_cap(old_rating, new_fighter):
    """Bound a single bout's rating move in BOTH directions.

    Capping only gains meant a fighter could shed 300 points in a night but
    never add more than 100. At the current INITIAL_RD of 150 the largest
    possible move is about 86, so this never fires -- but it fired on 28% of
    moves at the original INITIAL_RD of 350, so the symmetry matters the
    moment that constant is retuned.
    """
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

    finish_rates = compute_rolling_finish_rates(bouts)
    print(f"Rolling 3-year finish rates computed for {len(finish_rates)} bouts")

    fighters = {}
    last_bout_date = {}
    rating_rows = []

    for bout in bouts:
        bout_id, date_, fighter_a_id, fighter_b_id, winner_id, method, is_title_fight, bout_outcome = bout

        if fighter_a_id not in fighters:
            fighters[fighter_a_id] = Glicko2Fighter()
        if fighter_b_id not in fighters:
            fighters[fighter_b_id] = Glicko2Fighter()

        # Rating periods since each fighter last competed. A debut counts as
        # one period so a first bout behaves as it always did.
        periods_a = elapsed_periods(last_bout_date.get(fighter_a_id), date_)
        periods_b = elapsed_periods(last_bout_date.get(fighter_b_id), date_)

        fighter_a = fighters[fighter_a_id]
        fighter_b = fighters[fighter_b_id]

        mu_a, phi_a = _scale_down(fighter_a)
        mu_b, phi_b = _scale_down(fighter_b)
        expected_a = _expected_score(mu_a, mu_b, phi_b)
        expected_b = 1 - expected_a

        outcome = determine_outcome(winner_id, fighter_a_id, method, is_title_fight, expected_a, bout_outcome)

        if outcome is None:
            continue

        # Apply era adjustment
        outcome = era_adjust_outcome(outcome, bout_id, finish_rates)

        new_a, new_b = update_ratings(fighter_a, fighter_b, outcome, periods_a, periods_b)

        new_a = apply_cap(fighter_a.rating, new_a)
        new_b = apply_cap(fighter_b.rating, new_b)

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