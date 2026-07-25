from src.db.connection import get_connection
from src.db.insert import insert_rating
from src.ratings.glicko2 import Glicko2Fighter, update_ratings, _expected_score, _scale_down

def get_bouts_chronological():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, date, fighter_a_id, fighter_b_id, winner_id, method
        FROM bouts
        ORDER BY date ASC, id ASC
    """)
    bouts = cur.fetchall()
    cur.close()
    conn.close()
    return bouts


SKIP_METHODS = {'Overturned', 'Other'}
def determine_outcome(winner_id, fighter_a_id, method):
    if method in SKIP_METHODS:
        return None
    if winner_id is None:
        if method == 'Decision':
            return 0.5  # Draw
        return None  # NC — skip
    if winner_id == fighter_a_id:
        return 1.0
    return 0.0


def run_ratings():
    bouts = get_bouts_chronological()
    fighters = {}  # fighter_id -> Glicko2Fighter

    print(f"Processing {len(bouts)} bouts...")

    for bout in bouts:
        bout_id, date, fighter_a_id, fighter_b_id, winner_id, method = bout

        # Initialise fighters if first appearance
        if fighter_a_id not in fighters:
            fighters[fighter_a_id] = Glicko2Fighter()
        if fighter_b_id not in fighters:
            fighters[fighter_b_id] = Glicko2Fighter()

        fighter_a = fighters[fighter_a_id]
        fighter_b = fighters[fighter_b_id]

        outcome = determine_outcome(winner_id, fighter_a_id, method)

        if outcome is None:
            continue  # skip NC

        # Compute expected score before update
        mu_a, phi_a = _scale_down(fighter_a)
        mu_b, phi_b = _scale_down(fighter_b)
        expected_a = _expected_score(mu_a, mu_b, phi_b)
        expected_b = 1 - expected_a

        # Update ratings
        new_a, new_b = update_ratings(fighter_a, fighter_b, outcome)

        # Store updated ratings
        fighters[fighter_a_id] = new_a
        fighters[fighter_b_id] = new_b

        # Insert into ratings table
        insert_rating(fighter_a_id, bout_id, date, new_a.rating, new_a.rd, new_a.volatility, expected_a)
        insert_rating(fighter_b_id, bout_id, date, new_b.rating, new_b.rd, new_b.volatility, expected_b)

    print(f"Ratings complete. {len(fighters)} fighters rated.")


if __name__ == "__main__":
    run_ratings()