from src.db.connection import get_connection
from src.ratings.glicko2 import Glicko2Fighter, update_ratings, _expected_score, _scale_down

SKIP_METHODS = {'Overturned', 'Other'}
MAX_RATING_GAIN = 100
UPSET_THRESHOLD = 0.25
UPSET_DISCOUNT = 0.85
BASELINE_FINISH_RATE = 0.502


def get_bouts_chronological(cur):
    cur.execute("""
        SELECT id, date, fighter_a_id, fighter_b_id, winner_id, method, is_title_fight
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




def determine_outcome(winner_id, fighter_a_id, method, is_title_fight, expected_a):
    if method in SKIP_METHODS:
        return None
    if winner_id is None:
        if method == 'Decision':
            return 0.5
        return None

    expected_b = 1 - expected_a

    if winner_id == fighter_a_id:
        outcome = 1.1 if is_title_fight else 1.0
        if expected_a < UPSET_THRESHOLD:
            outcome = outcome * UPSET_DISCOUNT
        return outcome
    else:
        if expected_b < UPSET_THRESHOLD:
            outcome = 1.0 * UPSET_DISCOUNT
            return 1 - outcome
        return 0.0


def apply_cap(old_rating, new_fighter):
    gain = new_fighter.rating - old_rating
    if gain > MAX_RATING_GAIN:
        return Glicko2Fighter(
            old_rating + MAX_RATING_GAIN,
            new_fighter.rd,
            new_fighter.volatility
        )
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
    rating_rows = []

    for bout in bouts:
        bout_id, date_, fighter_a_id, fighter_b_id, winner_id, method, is_title_fight = bout

        if fighter_a_id not in fighters:
            fighters[fighter_a_id] = Glicko2Fighter()
        if fighter_b_id not in fighters:
            fighters[fighter_b_id] = Glicko2Fighter()

        fighter_a = fighters[fighter_a_id]
        fighter_b = fighters[fighter_b_id]

        mu_a, phi_a = _scale_down(fighter_a)
        mu_b, phi_b = _scale_down(fighter_b)
        expected_a = _expected_score(mu_a, mu_b, phi_b)
        expected_b = 1 - expected_a

        outcome = determine_outcome(winner_id, fighter_a_id, method, is_title_fight, expected_a)

        if outcome is None:
            continue

        # Apply era adjustment
        outcome = era_adjust_outcome(outcome, bout_id, finish_rates)

        new_a, new_b = update_ratings(fighter_a, fighter_b, outcome)

        new_a = apply_cap(fighter_a.rating, new_a)
        new_b = apply_cap(fighter_b.rating, new_b)

        fighters[fighter_a_id] = new_a
        fighters[fighter_b_id] = new_b

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