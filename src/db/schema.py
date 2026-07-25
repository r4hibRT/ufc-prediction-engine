from src.db.connection import get_connection

def create_tables():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS fighters (
            id SERIAL PRIMARY KEY,
            url VARCHAR(200) UNIQUE,
            name VARCHAR(100) NOT NULL,
            dob DATE,
            height NUMERIC(5,2),
            reach NUMERIC(5,2),
            stance VARCHAR(20)
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS bouts (
            id SERIAL PRIMARY KEY,
            date DATE,
            fighter_a_id INTEGER REFERENCES fighters(id),
            fighter_b_id INTEGER REFERENCES fighters(id),
            winner_id INTEGER REFERENCES fighters(id),
            method VARCHAR(20),
            method_detail VARCHAR(100),
            round INTEGER,
            time VARCHAR(10),
            weight_class VARCHAR(100),
            is_title_fight BOOLEAN DEFAULT FALSE,
            is_defence BOOLEAN DEFAULT FALSE,
            UNIQUE(date, fighter_a_id, fighter_b_id)
        );
    """)

    cur.execute("""
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
    """)

    cur.execute("""
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
    """)

    conn.commit()
    cur.close()
    conn.close()
    print("Tables created successfully")

if __name__ == "__main__":
    create_tables()