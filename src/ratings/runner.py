from src.db.connection import get_connection
from src.ratings.glicko2 import Glicko2Fighter, update_ratings, _expected_score, _scale_down

SKIP_METHODS = {'Overturned', 'Other'}
MAX_RATING_GAIN = 200
UPSET_THRESHOLD = 0.25
UPSET_DISCOUNT = 0.85


def get_bouts_chronological(cur):
    cur.execute("""
        SELECT id, date, fighter_a_id, fighter_b_id, winner_id, method, is_title_fight
        FROM bouts
        ORDER BY date ASC, id ASC
    """)
    return cur.fetchall()


def determine_outcome(winner_id, fighter_a_id, method, is_title_fight, expected_a):
    if method in SKIP_METHODS:
        return None
    if winner_id is None:
        if method == 'Decision':
            return 0.5
        return None
    if winner_id == fighter_a_id:
        outcome = 1.1 if is_title_fight else 1.0
        if expected_a < UPSET_THRESHOLD:
            outcome = outcome * UPSET_DISCOUNT
        return outcome
    else:
        outcome = 0.9 if is_title_fight else 0.0
        expected_b = 1 - expected_a
        if expected_b < UPSET_THRESHOLD:
            outcome = (1 - outcome) * UPSET_DISCOUNT
            outcome = 1 - outcome
        return outcome


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
    fighters = {}
    print(f"Processing {len(bouts)} bouts...")

    rating_rows = []

    for bout in bouts:
        bout_id, date, fighter_a_id, fighter_b_id, winner_id, method, is_title_fight = bout

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

        new_a, new_b = update_ratings(fighter_a, fighter_b, outcome)

        # Apply rating gain cap
        new_a = apply_cap(fighter_a.rating, new_a)
        new_b = apply_cap(fighter_b.rating, new_b)

        fighters[fighter_a_id] = new_a
        fighters[fighter_b_id] = new_b

        rating_rows.append((fighter_a_id, bout_id, date, new_a.rating, new_a.rd, new_a.volatility, expected_a))
        rating_rows.append((fighter_b_id, bout_id, date, new_b.rating, new_b.rd, new_b.volatility, expected_b))

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