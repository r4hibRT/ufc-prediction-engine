from src.db.connection import get_connection

conn = get_connection()
cur = conn.cursor()

print("\n=== OUTCOME SUMMARY ===")

cur.execute("""
    SELECT 
        COUNT(*) FILTER (WHERE winner_id IS NOT NULL) as total_decided,
        COUNT(*) FILTER (WHERE winner_id IS NULL) as total_nc_draw,
        COUNT(*) as total_bouts
    FROM bouts
""")
row = cur.fetchone()
print(f"Total bouts: {row[2]}")
print(f"Decided (winner recorded): {row[0]}")
print(f"NC/Draw (no winner): {row[1]}")

cur.execute("""
    SELECT 
        f.name,
        COUNT(*) FILTER (WHERE b.winner_id = f.id) as wins,
        COUNT(*) FILTER (WHERE b.winner_id != f.id AND b.winner_id IS NOT NULL) as losses,
        COUNT(*) FILTER (WHERE b.winner_id IS NULL) as nc_draws,
        COUNT(*) as total
    FROM fighters f
    JOIN bouts b ON (b.fighter_a_id = f.id OR b.fighter_b_id = f.id)
    GROUP BY f.id, f.name
    ORDER BY total DESC
    LIMIT 10
""")
print("\nTop 10 fighters by total bouts (W/L/NC):")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]}W {row[2]}L {row[3]}NC — {row[4]} total")
print("=== NULL CHECKS ===")

checks = {
    "Bouts with NULL winner_id": "SELECT COUNT(*) FROM bouts WHERE winner_id IS NULL",
    "Bouts with NULL date": "SELECT COUNT(*) FROM bouts WHERE date IS NULL",
    "Bouts with NULL weight_class": "SELECT COUNT(*) FROM bouts WHERE weight_class IS NULL",
    "Bouts with NULL method": "SELECT COUNT(*) FROM bouts WHERE method IS NULL",
    "Bouts with NULL round": "SELECT COUNT(*) FROM bouts WHERE round IS NULL",
    "Bouts with NULL time": "SELECT COUNT(*) FROM bouts WHERE time IS NULL",
    "Fighters with NULL name": "SELECT COUNT(*) FROM fighters WHERE name IS NULL",
    "Fighters with NULL dob": "SELECT COUNT(*) FROM fighters WHERE dob IS NULL",
    "Fighters with NULL height": "SELECT COUNT(*) FROM fighters WHERE height IS NULL",
    "Fighters with NULL reach": "SELECT COUNT(*) FROM fighters WHERE reach IS NULL",
    "Fighters with NULL stance": "SELECT COUNT(*) FROM fighters WHERE stance IS NULL",
    "Bouts with no stats": """
        SELECT COUNT(*) FROM bouts b
        WHERE NOT EXISTS (
            SELECT 1 FROM bout_stats bs WHERE bs.bout_id = b.id
        )
    """,
}

for label, query in checks.items():
    cur.execute(query)
    print(f"{label}: {cur.fetchone()[0]}")

print("\n=== VALIDITY CHECKS ===")

cur.execute("""
    SELECT COUNT(*) FROM bouts
    WHERE winner_id IS NOT NULL
    AND winner_id != fighter_a_id
    AND winner_id != fighter_b_id
""")
print(f"Bouts where winner is neither fighter_a nor fighter_b: {cur.fetchone()[0]}")

cur.execute("""
    SELECT COUNT(*) FROM bouts
    WHERE round IS NOT NULL
    AND (round::integer < 1 OR round::integer > 5)
""")
print(f"Bouts with invalid round (not 1-5): {cur.fetchone()[0]}")

cur.execute("""
    SELECT method, COUNT(*) FROM bouts
    GROUP BY method
    ORDER BY COUNT(*) DESC
""")
print("\nMethod distribution:")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]}")

cur.execute("""
    SELECT weight_class, COUNT(*) FROM bouts
    GROUP BY weight_class
    ORDER BY COUNT(*) DESC
    LIMIT 20
""")
print("\nWeight class distribution (top 20):")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]}")

cur.execute("""
    SELECT stance, COUNT(*) FROM fighters
    GROUP BY stance
    ORDER BY COUNT(*) DESC
""")
print("\nStance distribution:")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]}")

cur.execute("""
    SELECT MIN(height), MAX(height) FROM fighters WHERE height IS NOT NULL
""")
row = cur.fetchone()
print(f"\nHeight (inches) — min: {row[0]}, max: {row[1]}" if row[0] else "\nHeight: no data")

cur.execute("""
    SELECT MIN(reach), MAX(reach) FROM fighters WHERE reach IS NOT NULL
""")
row = cur.fetchone()
print(f"Reach (inches) — min: {row[0]}, max: {row[1]}" if row[0] else "Reach: no data")

cur.execute("""
    SELECT MIN(dob), MAX(dob) FROM fighters WHERE dob IS NOT NULL
""")
row = cur.fetchone()
print(f"DOB range — earliest: {row[0]}, latest: {row[1]}" if row[0] else "DOB: no data")

cur.execute("""
    SELECT 
        MIN(sig_strikes_landed), MAX(sig_strikes_landed),
        MIN(takedowns_landed), MAX(takedowns_landed),
        MIN(control_time_seconds), MAX(control_time_seconds)
    FROM bout_stats
""")
row = cur.fetchone()
print(f"\nBout stats ranges:")
print(f"  Sig strikes landed — min: {row[0]}, max: {row[1]}")
print(f"  Takedowns landed — min: {row[2]}, max: {row[3]}")
print(f"  Control time (secs) — min: {row[4]}, max: {row[5]}")

cur.close()
conn.close()