"""PostgreSQL access: the connection, the schema, and SQL shared across modules.

Credentials come from .env (see .env.example). Every table is created with
`create_tables()`, which is idempotent and runs at the start of each refresh.

Tables, in the order the pipeline fills them:
    fighters, bouts, bout_stats   scraped from ufcstats (src/scrape.py)
    events                        the scrape checkpoint, one row per event
    ratings                       the site's Glicko-2 history (src/ratings.py)
    bout_snapshots                point-in-time fighter state (src/history.py)
    predictions                   the engine's locked forecasts (src/engine/)

Run:
    python -m src.db              # create any missing tables
"""

import os

import psycopg2
from dotenv import load_dotenv

load_dotenv()

# A one-off catchweight bout should never define a fighter's home division.
NON_DIVISIONS = ("Catch Weight", "Open Weight")

# ufcstats flags tournament finals (TUF, Road to UFC) as title fights. A real
# title fight has at least one fighter with this many prior UFC appearances.
MIN_TITLE_EXPERIENCE = 5


def get_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def primary_division_cte(date_filter=""):
    """Home division by bout count, not latest bout. Inferring from the latest
    misfiled anyone who moved up once -- Usman read as a middleweight."""
    return f"""
        primary_division AS (
            SELECT DISTINCT ON (fid) fid, weight_class
            FROM (
                SELECT fid, weight_class, COUNT(*) AS n, MAX(date) AS last_date,
                       (weight_class NOT IN {NON_DIVISIONS!r}) AS is_division
                FROM (
                    SELECT fighter_a_id AS fid, weight_class, date FROM bouts {date_filter}
                    UNION ALL
                    SELECT fighter_b_id, weight_class, date FROM bouts {date_filter}
                ) all_bouts
                WHERE weight_class IS NOT NULL
                GROUP BY fid, weight_class
            ) counts
            ORDER BY fid, is_division DESC, n DESC, last_date DESC
        )
    """


SCHEMA = """
    CREATE TABLE IF NOT EXISTS fighters (
        id SERIAL PRIMARY KEY,
        url VARCHAR(200) UNIQUE,
        name VARCHAR(100) NOT NULL,
        dob DATE,
        height NUMERIC(5,2),
        reach NUMERIC(5,2),
        stance VARCHAR(20)
    );

    CREATE TABLE IF NOT EXISTS bouts (
        id SERIAL PRIMARY KEY,
        date DATE,
        fighter_a_id INTEGER REFERENCES fighters(id),
        fighter_b_id INTEGER REFERENCES fighters(id),
        winner_id INTEGER REFERENCES fighters(id),
        outcome VARCHAR(10),
        method VARCHAR(20),
        method_detail VARCHAR(100),
        round INTEGER,
        time VARCHAR(10),
        weight_class VARCHAR(100),
        is_title_fight BOOLEAN DEFAULT FALSE,
        is_defence BOOLEAN DEFAULT FALSE,
        title_holder_id INTEGER REFERENCES fighters(id),
        UNIQUE(date, fighter_a_id, fighter_b_id)
    );

    CREATE TABLE IF NOT EXISTS bout_stats (
        id SERIAL PRIMARY KEY,
        bout_id INTEGER REFERENCES bouts(id),
        fighter_id INTEGER REFERENCES fighters(id),
        sig_strikes_landed INTEGER,
        sig_strikes_attempted INTEGER,
        total_strikes_landed INTEGER,
        total_strikes_attempted INTEGER,
        takedowns_landed INTEGER,
        takedowns_attempted INTEGER,
        submission_attempts INTEGER,
        knockdowns INTEGER,
        control_time_seconds INTEGER,
        UNIQUE(bout_id, fighter_id)
    );

    CREATE TABLE IF NOT EXISTS events (
        url VARCHAR(200) PRIMARY KEY,
        name VARCHAR(200),
        date DATE,
        status VARCHAR(10) NOT NULL,
        bouts_scraped INTEGER,
        attempts INTEGER NOT NULL DEFAULT 1,
        scraped_at TIMESTAMP NOT NULL DEFAULT now()
    );

    CREATE TABLE IF NOT EXISTS ratings (
        id SERIAL PRIMARY KEY,
        fighter_id INTEGER REFERENCES fighters(id),
        bout_id INTEGER REFERENCES bouts(id),
        date DATE,
        rating NUMERIC(8,2),
        rd NUMERIC(8,2),
        volatility NUMERIC(8,6),
        expected_score NUMERIC(6,4),
        UNIQUE(fighter_id, bout_id)
    );

    CREATE TABLE IF NOT EXISTS bout_snapshots (
        bout_id INTEGER REFERENCES bouts(id),
        fighter_id INTEGER REFERENCES fighters(id),
        date DATE NOT NULL,
        bouts_before INTEGER,
        appearances_before INTEGER,
        wins_before INTEGER,
        win_streak INTEGER,
        loss_streak INTEGER,
        recent_form_5 NUMERIC(4,3),
        rating_before NUMERIC(8,2),
        rd_before NUMERIC(8,2),
        peak_rating_before NUMERIC(8,2),
        layoff_days INTEGER,
        ko_losses INTEGER,
        sub_losses INTEGER,
        finishes INTEGER,
        career_seconds INTEGER,
        sig_landed INTEGER,
        sig_attempted INTEGER,
        sig_absorbed INTEGER,
        td_landed INTEGER,
        td_attempted INTEGER,
        opp_td_landed INTEGER,
        opp_td_attempted INTEGER,
        control_seconds INTEGER,
        sub_attempts INTEGER,
        knockdowns INTEGER,
        knockdowns_absorbed INTEGER,
        avg_opponent_rating NUMERIC(8,2),
        max_opponent_rating NUMERIC(8,2),
        PRIMARY KEY (bout_id, fighter_id)
    );
    CREATE INDEX IF NOT EXISTS idx_bout_snapshots_fighter ON bout_snapshots (fighter_id, date);

    -- The engine's prospective record: written before a card, frozen on fight
    -- day, scored once the result is scraped. Keyed by ufcstats fight URL.
    CREATE TABLE IF NOT EXISTS predictions (
        bout_url VARCHAR(200) PRIMARY KEY,
        event_url VARCHAR(200) NOT NULL,
        event_name VARCHAR(200),
        event_date DATE NOT NULL,
        weight_class VARCHAR(50),
        card_position INTEGER,
        fighter_a_url VARCHAR(200) NOT NULL,
        fighter_a_name VARCHAR(100),
        fighter_b_url VARCHAR(200) NOT NULL,
        fighter_b_name VARCHAR(100),
        p_a DOUBLE PRECISION NOT NULL,
        glicko_p DOUBLE PRECISION,
        contributions JSONB,
        tape JSONB,
        narrative TEXT,
        narrative_hash VARCHAR(32),
        narrative_model VARCHAR(40),
        narrative_version VARCHAR(10),
        narrated_at TIMESTAMP,
        model_version VARCHAR(20) NOT NULL,
        predicted_at TIMESTAMP NOT NULL DEFAULT now(),
        bout_id INTEGER REFERENCES bouts(id),
        result VARCHAR(10),
        a_won BOOLEAN,
        log_loss DOUBLE PRECISION,
        scored_at TIMESTAMP
    );
"""


def create_tables():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(SCHEMA)
    conn.commit()
    cur.close()
    conn.close()


if __name__ == "__main__":
    create_tables()
    print("Tables ready.")
