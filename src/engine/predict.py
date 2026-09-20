"""Prospective predictions: forecast upcoming cards, then score them.

Before each card the weekly refresh writes P(fighter A wins) for every listed
upcoming bout into `predictions`, using the newest artifact in `models/`. A row
may be rewritten until fight day and is frozen from then on, so the table is an
honest out-of-sample record of a fixed model. Once a card is scraped each row is
matched to its bout and scored by log loss; a fight that never happened is
marked not_held.

Run:
    python -m src.engine.predict            # score, then predict upcoming cards
    python -m src.engine.predict --dry-run  # print predictions, write nothing
"""

import json
import math
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from psycopg2.extras import execute_values

from src.db.connection import get_connection
from src.engine import artifact as engine_artifact
from src.engine import features
from src.engine.rating import Params

# Card dates are US calendar dates; the machine runs in Sydney, a day ahead.
FIGHT_TZ = ZoneInfo("America/New_York")

COLUMNS = ["bout_url", "event_url", "event_name", "event_date", "weight_class", "card_position",
           "fighter_a_url", "fighter_a_name", "fighter_b_url", "fighter_b_name",
           "p_a", "glicko_p", "contributions", "tape", "model_version"]


def fight_calendar_today():
    """Today's date where cards are dated, which decides when a forecast freezes."""
    return datetime.now(FIGHT_TZ).date()


def check_compatible(art):
    """Refuse an artifact built with feature constants the code no longer uses."""
    stale = {k: v for k, v in art["feature_constants"].items() if getattr(features, k) != v}
    if stale:
        raise RuntimeError(f"artifact {art['version']} expects feature constants {stale}")


def fetch_cards(page):
    """Every matchup on every listed upcoming card."""
    from src.scraper.bouts import scrape_upcoming_card
    from src.scraper.events import scrape_upcoming_events
    from src.scraper.pipeline import parse_date

    rows = []
    for event in scrape_upcoming_events(page):
        event_date = parse_date(event["date"])
        for position, bout in enumerate(scrape_upcoming_card(event["url"], page), 1):
            rows.append({**bout, "event_url": event["url"], "event_name": event["name"],
                         "event_date": event_date, "card_position": position})
    return pd.DataFrame(rows)


def resolve_fighters(cards, page):
    """Database ids for known fighters; debutants get negative ids and a scraped profile."""
    from src.scraper.fighters import scrape_fighter_profile

    conn = get_connection()
    ids = dict(pd.read_sql("SELECT url, id FROM fighters", conn).itertuples(index=False))
    conn.close()
    urls = pd.unique(cards[["fighter_a_url", "fighter_b_url"]].to_numpy().ravel())
    profiles = []
    for url in urls:
        if url not in ids:
            ids[url] = -(len(profiles) + 1)
            profiles.append({"fighter_id": ids[url], **scrape_fighter_profile(url, page)})
    return ids, pd.DataFrame(profiles, columns=["fighter_id", "dob", "height", "reach", "stance"])


def forecast(cards, ids, profiles, art):
    """Artifact probabilities for each card row, with per-column log-odds contributions."""
    extra = pd.DataFrame({
        "bout_id": -np.arange(1, len(cards) + 1),
        "date": cards["event_date"].to_numpy(),
        "fighter_a_id": cards["fighter_a_url"].map(ids).to_numpy(),
        "fighter_b_id": cards["fighter_b_url"].map(ids).to_numpy(),
        "winner_id": None, "method": None, "outcome": "upcoming",
        "weight_class": cards["weight_class"].to_numpy(), "round": None, "time": None,
    })
    built = features.build(params=Params(**art["rating_params"]), extra=extra,
                           extra_fighters=profiles)
    rows = built.set_index("bout_id").loc[extra["bout_id"]]

    out = cards.reset_index(drop=True).copy()
    out["p_a"] = engine_artifact.predict(art, rows)
    out["glicko_p"] = rows["glicko_p"].to_numpy()
    out["contributions"] = [json.dumps(engine_artifact.contributions(art, r).round(4).to_dict())
                            for _, r in rows.iterrows()]
    out["model_version"] = art["version"]
    out["tape"] = tapes(out, ids, profiles)
    return out


def tapes(cards, ids, profiles):
    """Tale of the tape for each bout as of its card date, as JSON."""
    from src.api.tape import bout_tape

    by_id = {r["fighter_id"]: r for r in profiles.to_dict("records")}
    conn = get_connection()
    try:
        return [json.dumps(bout_tape(ids[r.fighter_a_url], r.fighter_a_name, ids[r.fighter_b_url],
                                     r.fighter_b_name, r.event_date, by_id, conn), default=str)
                for r in cards.itertuples(index=False)]
    finally:
        conn.close()


def write(preds, conn):
    """Upsert future bouts only; a row on or after its fight day is never rewritten.
    Future rows for fights no longer listed are dropped, since none is frozen yet."""
    today = fight_calendar_today()
    future = preds[pd.to_datetime(preds["event_date"]).dt.date > today]
    if future.empty:
        return 0
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in COLUMNS[1:])
    cur = conn.cursor()
    cur.execute("DELETE FROM predictions WHERE event_date > %s AND bout_url <> ALL(%s)",
                (today, list(future["bout_url"])))
    execute_values(cur, f"""
        INSERT INTO predictions ({", ".join(COLUMNS)}) VALUES %s
        ON CONFLICT (bout_url) DO UPDATE SET {updates}, predicted_at = now()
        WHERE predictions.event_date > '{today.isoformat()}'::date
    """, [tuple(r) for r in future[COLUMNS].itertuples(index=False)])
    # Rows written before a tape field existed get one; the forecast is untouched.
    cur.executemany("UPDATE predictions SET tape = %s WHERE bout_url = %s AND result IS NULL "
                    "AND (tape IS NULL OR NOT tape -> 'a' ? 'rating')",
                    list(zip(preds["tape"], preds["bout_url"])))
    conn.commit()
    cur.close()
    return len(future)


def score(conn):
    """Match past, unscored predictions to scraped bouts and record the outcome."""
    cur = conn.cursor()
    cur.execute("""
        SELECT p.bout_url, p.fighter_a_url, p.p_a, b.id, b.outcome, w.url,
               (e.status = 'complete') AS event_done
        FROM predictions p
        LEFT JOIN fighters fa ON fa.url = p.fighter_a_url
        LEFT JOIN fighters fb ON fb.url = p.fighter_b_url
        LEFT JOIN bouts b ON b.date BETWEEN p.event_date - 1 AND p.event_date + 1
             AND ((b.fighter_a_id = fa.id AND b.fighter_b_id = fb.id)
               OR (b.fighter_a_id = fb.id AND b.fighter_b_id = fa.id))
        LEFT JOIN fighters w ON w.id = b.winner_id
        LEFT JOIN events e ON e.url = p.event_url
        WHERE p.result IS NULL AND p.event_date < %s
    """, (fight_calendar_today(),))
    scored = 0
    for bout_url, a_url, p_a, bout_id, outcome, winner_url, event_done in cur.fetchall():
        if bout_id is None and not event_done:
            continue
        a_won = (winner_url == a_url) if outcome == "win" and winner_url else None
        loss = None if a_won is None else -math.log(p_a if a_won else 1 - p_a)
        cur.execute("""
            UPDATE predictions SET bout_id = %s, result = %s, a_won = %s, log_loss = %s,
                   scored_at = now()
            WHERE bout_url = %s
        """, (bout_id, outcome if bout_id else "not_held", a_won, loss, bout_url))
        scored += 1
    conn.commit()
    cur.close()
    return scored


def run(dry_run=False, log=print):
    from playwright.sync_api import sync_playwright
    from src.db.schema import create_tables

    art = engine_artifact.load()
    check_compatible(art)
    if not dry_run:
        create_tables()
        conn = get_connection()
        scored = score(conn)
        conn.close()
        log(f"Scored {scored} past predictions.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            cards = fetch_cards(page)
            if cards.empty:
                log("No upcoming bouts listed.")
                return {"predicted": 0}
            ids, profiles = resolve_fighters(cards, page)
        finally:
            browser.close()

    preds = forecast(cards, ids, profiles, art)
    log(f"Forecast {len(preds)} bouts on {preds['event_url'].nunique()} cards "
        f"({len(profiles)} debutants) with model {art['version']}.")
    if dry_run:
        for r in preds.itertuples(index=False):
            log(f"  {r.event_date}  {r.fighter_a_name:>24} {r.p_a:5.1%}  vs  {r.fighter_b_name}")
        return {"predicted": len(preds), "written": 0}

    conn = get_connection()
    written = write(preds, conn)
    conn.close()
    log(f"Wrote {written} predictions.")
    return {"scored": scored, "predicted": len(preds), "written": written,
            "model_version": art["version"]}


if __name__ == "__main__":
    import argparse
    import warnings
    warnings.filterwarnings("ignore")
    parser = argparse.ArgumentParser(description="Score past predictions and forecast upcoming cards.")
    parser.add_argument("--dry-run", action="store_true", help="print forecasts, write nothing")
    run(dry_run=parser.parse_args().dry_run)
