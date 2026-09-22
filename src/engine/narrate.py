"""AI narration of each forecast: the "Model insights" paragraph on a fight card.

Written during the refresh from a fact sheet built only out of what is already
stored with the forecast, and frozen with it: a paragraph is written while the
fight is still unfought and never rewritten afterwards, so it cannot drift once
the result is known. Factor contributions are turned into words before the model
sees them, so neither the prompt nor the page exposes the engine's internals.

Every paragraph is checked before it is stored: each number in it must appear in
the fact sheet, the length must sit in the voice's range, and only the two
fighters may be named. Anything that fails is retried once and then skipped,
leaving the card's placeholder in place.

Run:
    python -m src.engine.narrate --dry-run   # print fact sheets, call nothing
    python -m src.engine.narrate             # write missing narratives
"""

import hashlib
import json
import os
import re
import time

import requests
from dotenv import load_dotenv

from src.db import get_connection
from src.engine.features import BLOCKS

try:  # kept out of version control; without it the site simply shows no paragraphs
    from src.engine import voice
except ImportError:
    voice = None

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MAX_TOKENS = 400
TIMEOUT = 60
ATTEMPTS = 2
# Free tiers rate-limit and occasionally 503; back off rather than lose the run.
RETRY_STATUS = (429, 500, 502, 503, 529)
BACKOFF = (3, 12, 30)
PACE = 1.5
# Only the next cards are narrated: further-out ones change, and free-tier
# quotas are small.
DAYS_AHEAD = 10

# Log-odds a block must carry before it is worth a mention, and where the
# wording steps up.
WEIGHTS = ((0.6, "decisive"), (0.3, "large"), (0.12, "moderate"), (0.04, "slight"))
BLOCK_LABELS = {"rating": "career results", "age": "age", "striking": "striking",
                "grappling": "grappling", "physical": "size", "form": "recent form",
                "layoff": "time out", "experience": "experience", "debut": "inexperience",
                "schedule": "quality of opposition", "power": "power and durability",
                "finishing": "finishing", "stance": "stance"}


def drivers(contributions, a_name, b_name):
    """Contributions as ranked words: what moves the forecast, and how much."""
    blocks = {"rating": ["rating"], **BLOCKS}
    totals = {block: sum(contributions.get(c, 0.0) for c in cols if c in contributions)
              for block, cols in blocks.items()}
    totals = {block: total for block, total in totals.items() if total}
    if not totals:
        return []

    def named(block, total):
        weight = next((w for cut, w in WEIGHTS if abs(total) >= cut), "marginal")
        return {"factor": BLOCK_LABELS.get(block, block),
                "favours": a_name if total > 0 else b_name, "weight": weight}

    # In an even fight nothing clears the bar, so the strongest driver is always named.
    strongest = max(totals, key=lambda block: abs(totals[block]))
    out = [named(block, total) for block, total in totals.items()
           if abs(total) >= WEIGHTS[-1][0] or block == strongest]
    order = [w for _, w in WEIGHTS] + ["marginal"]
    return sorted(out, key=lambda d: order.index(d["weight"]))


def _recent(fighter_id, before, conn, limit=3):
    """Last few results going into the card, newest first."""
    if not fighter_id:
        return None
    cur = conn.cursor()
    cur.execute("""
        SELECT CASE WHEN b.outcome <> 'win' THEN b.outcome
                    WHEN b.winner_id = %(f)s THEN 'won' ELSE 'lost' END,
               o.name, b.method
        FROM bouts b
        JOIN fighters o ON o.id = CASE WHEN b.fighter_a_id = %(f)s
                                       THEN b.fighter_b_id ELSE b.fighter_a_id END
        WHERE %(f)s IN (b.fighter_a_id, b.fighter_b_id) AND b.date < %(d)s
        ORDER BY b.date DESC, b.id DESC LIMIT %(n)s
    """, {"f": fighter_id, "d": before, "n": limit})
    rows = cur.fetchall()
    cur.close()
    return [f"{r} vs {name} ({method})" for r, name, method in rows] or None


def _met_before(a_id, b_id, before, conn):
    if not (a_id and b_id):
        return False
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM bouts WHERE date < %s
          AND ((fighter_a_id = %s AND fighter_b_id = %s)
            OR (fighter_a_id = %s AND fighter_b_id = %s))
    """, (before, a_id, b_id, b_id, a_id))
    n = cur.fetchone()[0]
    cur.close()
    return n > 0


def _prune(value):
    """Drop empty fields: a paragraph cannot use what is not there."""
    if isinstance(value, dict):
        return {k: _prune(v) for k, v in value.items() if v is not None and v != []}
    return value


def _corner(tape, recent):
    """The tape trimmed to what a paragraph can use, with rates rounded."""
    stats = tape.get("stats") or {}
    keep = {k: round(v, 2) for k, v in stats.items()
            if v is not None and k in ("slpm", "sapm", "td_def", "td_avg")}
    record = tape.get("record")
    age = tape.get("age")
    return _prune({
        "name": tape.get("name"), "age": age and int(age),
        "reach": tape.get("reach"), "stance": tape.get("stance"),
        "rating": tape.get("rating"), "streak": tape.get("streak"),
        "debut": True if tape.get("debut") else None,
        "record": record and f"{record['w']}-{record['l']}-{record['d']}",
        "months_out": tape.get("days_since_last") and round(tape["days_since_last"] / 30.4),
        "wins_by": tape.get("wins_by"), "last_fights": recent, **keep})


def fact_sheet(row, conn):
    """Everything the model may use, and nothing else."""
    tape, contributions = row["tape"] or {}, row["contributions"] or {}
    a, b = tape.get("a", {}), tape.get("b", {})
    p_a = float(row["p_a"])
    favourite, p = (a.get("name"), p_a) if p_a >= 0.5 else (b.get("name"), 1 - p_a)
    sheet = {
        "fight": {"billing": "main event" if row["card_position"] == 1 else "undercard",
                  "division": row["weight_class"],
                  "rematch": _met_before(row["a_id"], row["b_id"], row["event_date"], conn)},
        "forecast": {"favourite": favourite, "win_probability_percent": round(p * 100),
                     "coin_flip": abs(p_a - 0.5) < 0.04},
        "drivers": drivers(contributions, a.get("name"), b.get("name")),
        "fighters": [_corner(a, _recent(row["a_id"], row["event_date"], conn)),
                     _corner(b, _recent(row["b_id"], row["event_date"], conn))],
    }
    return _prune(sheet)


def sheet_hash(sheet):
    """Covers the voice too, so retuning it rewrites the paragraphs."""
    payload = json.dumps([voice.VERSION, sheet], sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _numbers(value, into):
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        into.add(round(float(value), 2))
    elif isinstance(value, dict):
        for v in value.values():
            _numbers(v, into)
    elif isinstance(value, list):
        for v in value:
            _numbers(v, into)
    elif isinstance(value, str):
        for token in re.findall(r"\d+(?:\.\d+)?", value):
            into.add(round(float(token), 2))


def ungrounded(text, sheet):
    """Numbers in the paragraph that are not in the fact sheet, rounding allowed."""
    allowed = set()
    _numbers(sheet, allowed)
    allowed |= {round(v) for v in allowed} | {round(v, 1) for v in allowed}
    bad = []
    for token in re.findall(r"\d+(?:\.\d+)?", text):
        value = float(token)
        if not any(abs(value - a) < 0.051 for a in allowed):
            bad.append(token)
    return bad


def problems(text, sheet):
    """Everything wrong with a paragraph, empty when it may be stored."""
    found = []
    words = len(text.split())
    if not voice.WORDS[0] - 5 <= words <= voice.WORDS[1] + 5:
        found.append(f"{words} words, wanted {voice.WORDS[0]}-{voice.WORDS[1]}")
    if "\n" in text.strip():
        found.append("more than one paragraph")
    bad = ungrounded(text, sheet)
    if bad:
        found.append(f"numbers not in the fact sheet: {', '.join(bad)}")
    return found


class QuotaExhausted(Exception):
    """A model's daily free-tier allowance is gone; try the next one."""


def _daily_quota_gone(response):
    if response.status_code != 429:
        return False
    details = response.json().get("error", {}).get("details", [])
    return any("PerDay" in v.get("quotaId", "")
               for d in details for v in d.get("violations", []))


def _post(url, body, headers):
    """One request, retried through rate limits and transient server errors."""
    for wait in BACKOFF + (None,):
        response = requests.post(url, json=body, headers=headers, timeout=TIMEOUT)
        if _daily_quota_gone(response):
            raise QuotaExhausted(url)
        if response.status_code not in RETRY_STATUS or wait is None:
            response.raise_for_status()
            return response
        time.sleep(wait)


def _anthropic(messages, api_key, model):
    body = {"model": model, "max_tokens": MAX_TOKENS, "temperature": voice.TEMPERATURE,
            "system": voice.system_prompt(), "messages": messages}
    response = _post(ANTHROPIC_URL, body,
                     {"x-api-key": api_key, "content-type": "application/json",
                      "anthropic-version": ANTHROPIC_VERSION})
    return "".join(part.get("text", "") for part in response.json()["content"])


def _gemini(messages, api_key, model):
    """Same conversation in Gemini's shape; thinking off, since this is one short paragraph."""
    body = {"system_instruction": {"parts": [{"text": voice.system_prompt()}]},
            "contents": [{"role": "model" if m["role"] == "assistant" else "user",
                          "parts": [{"text": m["content"]}]} for m in messages],
            "generationConfig": {"temperature": voice.TEMPERATURE,
                                 "maxOutputTokens": MAX_TOKENS,
                                 "thinkingConfig": {"thinkingBudget": 0}}}
    # Key goes in a header, never the URL, so it cannot leak into logs or tracebacks.
    response = _post(GEMINI_URL.format(model=model), body,
                     {"x-goog-api-key": api_key, "content-type": "application/json"})
    parts = response.json()["candidates"][0]["content"].get("parts", [])
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


CALLERS = {"anthropic": _anthropic, "gemini": _gemini}


def generate(sheet, api_key, model):
    """One paragraph from the API, or None if it never passed the checks."""
    messages = [{"role": "user",
                 "content": voice.USER.format(sheet=json.dumps(sheet, indent=1))}]
    call = CALLERS[voice.PROVIDER]
    for attempt in range(ATTEMPTS):
        text = call(messages, api_key, model).strip()
        found = problems(text, sheet)
        if not found:
            return text, None
        if attempt == ATTEMPTS - 1:
            return None, "; ".join(found)
        # Say what was wrong, so the rewrite fixes it rather than rolling again.
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": f"Rejected: {'; '.join(found)}. "
                                                 "Rewrite the paragraph, same facts."}]
    return None, "no attempts"


def pending(conn):
    """Unfought forecasts whose paragraph is missing or out of date."""
    cur = conn.cursor()
    cur.execute("""
        SELECT p.bout_url, p.event_name, p.event_date, p.weight_class, p.card_position,
               p.fighter_a_name, p.fighter_b_name,
               p.p_a, p.contributions, p.tape, p.narrative_hash,
               fa.id AS a_id, fb.id AS b_id
        FROM predictions p
        LEFT JOIN fighters fa ON fa.url = p.fighter_a_url
        LEFT JOIN fighters fb ON fb.url = p.fighter_b_url
        WHERE p.result IS NULL AND p.tape IS NOT NULL
          AND p.event_date <= CURRENT_DATE + %s
        ORDER BY p.event_date, p.card_position NULLS LAST
    """, (DAYS_AHEAD,))
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    cur.close()
    return rows


def store(conn, bout_url, text, digest, model):
    cur = conn.cursor()
    cur.execute("""
        UPDATE predictions SET narrative = %s, narrative_hash = %s, narrative_model = %s,
               narrative_version = %s, narrated_at = now()
        WHERE bout_url = %s AND result IS NULL
    """, (text, digest, model, voice.VERSION, bout_url))
    conn.commit()
    cur.close()


def run(dry_run=False, log=print, limit=None):
    if voice is None:
        log("No src/engine/voice.py; skipping narration.")
        return {"skipped": "no voice"}

    load_dotenv()
    api_key = os.getenv(voice.KEY_ENV)
    if not api_key and not dry_run:
        log(f"No {voice.KEY_ENV} set; skipping narration.")
        return {"skipped": "no api key"}

    conn = get_connection()
    models = list(voice.MODELS)
    try:
        rows = pending(conn)
        written = failed = 0
        for row in rows:
            sheet = fact_sheet(row, conn)
            digest = sheet_hash(sheet)
            if digest == row["narrative_hash"]:
                continue
            if dry_run:
                log(json.dumps(sheet, indent=1))
                written += 1
            elif models:
                time.sleep(PACE)
                fight = f"{row['fighter_a_name']} vs {row['fighter_b_name']}"
                try:
                    text, why = generate(sheet, api_key, models[0])
                except QuotaExhausted:
                    log(f"  {models.pop(0)} is out of free quota for today")
                    continue
                except requests.RequestException as exc:
                    log(f"  {fight} failed: {type(exc).__name__}")
                    failed += 1
                    continue
                if text:
                    store(conn, row["bout_url"], text, digest, models[0])
                    written += 1
                else:
                    failed += 1
                    log(f"  rejected {fight}: {why}")
            if limit and written >= limit:
                break
        log(f"Narrated {written} fights" + (f", {failed} rejected" if failed else "")
            + ("" if models else ", free quota used up") + (" (dry run)" if dry_run else ""))
        return {"narrated": written, "rejected": failed, "voice": voice.VERSION}
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Write the Model insights paragraphs.")
    parser.add_argument("--dry-run", action="store_true", help="print fact sheets, call nothing")
    parser.add_argument("--limit", type=int, help="stop after this many fights")
    args = parser.parse_args()
    run(dry_run=args.dry_run, limit=args.limit)
