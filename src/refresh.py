"""The unattended refresh, and the health checks that watch it.

Scheduled twice a week (Sydney time): Monday 12:00, once the weekend's cards
are on ufcstats, to score them and forecast the next ones; Saturday 08:00
(Friday evening in New York), after weigh-ins, to catch late changes. Every
step is idempotent, so a re-run after a partial failure is safe, and a failed
step stops the run rather than let later steps build on half-updated tables.

Steps:
  1. scrape    newly completed events (src/scrape.py)
  2. ratings   the site's Glicko-2 history, rebuilt from scratch (src/ratings.py)
  3. history   point-in-time snapshots (src/history.py)
  4. forecast  score last card, forecast every listed card (src/engine/predict.py)
  5. narrate   the model insight paragraph per new forecast (src/engine/narrate.py)

Health is checked against live state rather than the last log line, at the end
of every run and on every /api/health request, so a scheduled run that never
happened at all still shows up as stale on the site.

Run:
    python -m src.refresh                  # the whole pipeline
    python -m src.refresh --skip-scrape    # rebuild from what is stored
    python -m src.refresh --dry-run        # report, write nothing
    python -m src.refresh --health         # run the health checks only
"""

import argparse
import io
import json
import os
import sys
import time
import traceback
from datetime import date, datetime, timezone
from pathlib import Path

from src.db import create_tables, get_connection

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = PROJECT_ROOT / "logs"
STATUS_FILE = LOG_DIR / "last_run.json"
HEALTH_FILE = LOG_DIR / "health.json"
LOCK_FILE = LOG_DIR / "refresh.lock"
LOCK_STALE_SECONDS = 6 * 60 * 60

MAX_RUN_AGE_DAYS = 8
MAX_DATA_AGE_DAYS = 14   # UFC takes the odd weekend off
STUCK_ATTEMPTS = 2


def log(message):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")


# --- steps --------------------------------------------------------------------

def step_scrape(dry_run):
    from src.scrape import run
    return run(dry_run=dry_run, log=log)


def step_ratings(dry_run):
    from src.ratings import run_ratings
    return {"skipped": "dry-run"} if dry_run else run_ratings(log=log)


def step_history(dry_run):
    from src.history import write_snapshots
    return {"skipped": "dry-run"} if dry_run else write_snapshots(log=log)


def step_forecast(dry_run):
    from src.engine.predict import run
    return run(dry_run=dry_run, log=log)


def step_narrate(dry_run):
    from src.engine.narrate import run
    return run(dry_run=dry_run, log=log)


STEPS = [("scrape", step_scrape), ("ratings", step_ratings), ("history", step_history),
         ("forecast", step_forecast), ("narrate", step_narrate)]


# --- health -------------------------------------------------------------------

def _check(name, ok, detail):
    return {"name": name, "ok": bool(ok), "detail": detail}


def _last_run_checks():
    if not STATUS_FILE.exists():
        missing = "no refresh has recorded a status"
        return [_check("last_run_succeeded", False, missing),
                _check("last_run_recent", False, missing)], None
    status = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    started = datetime.fromisoformat(status["started"])
    age = (datetime.now(timezone.utc) - started).days
    ok = status.get("ok", False)
    return [
        _check("last_run_succeeded", ok, "ok" if ok else f"failed at {status.get('failed_step')}"),
        _check("last_run_recent", age <= MAX_RUN_AGE_DAYS, f"{age} day{'s' if age != 1 else ''} ago"),
    ], started


def _data_checks(cur):
    cur.execute("SELECT MAX(date), COUNT(*) FROM bouts;")
    newest, bouts = cur.fetchone()
    age = (date.today() - newest).days if newest else None
    cur.execute("SELECT COUNT(*) FROM bout_snapshots;")
    snapshots = cur.fetchone()[0]
    cur.execute("""
        SELECT name, url, attempts FROM events
        WHERE status = 'partial' AND attempts >= %s ORDER BY date DESC NULLS LAST;
    """, (STUCK_ATTEMPTS,))
    stuck = cur.fetchall()
    return [
        _check("data_fresh", age is not None and age <= MAX_DATA_AGE_DAYS,
               f"newest bout {newest}, {age} days old" if newest else "no bouts"),
        _check("snapshots_consistent", snapshots == 2 * bouts,
               f"{snapshots} snapshots for {bouts} bouts"),
        _check("no_stuck_events", not stuck,
               "none" if not stuck else "; ".join(f"{n or u} ({a} attempts)" for n, u, a in stuck)),
    ], newest


def health():
    """Every check in one report; never raises."""
    checks, started, newest = [], None, None
    try:
        run_checks, started = _last_run_checks()
        checks += run_checks
    except (OSError, ValueError, KeyError) as exc:
        checks.append(_check("last_run_readable", False, str(exc)))
    try:
        conn = get_connection()
        cur = conn.cursor()
        data_checks, newest = _data_checks(cur)
        checks += data_checks
        cur.close()
        conn.close()
    except Exception as exc:
        checks.append(_check("database_reachable", False, f"{type(exc).__name__}: {exc}"))
    return {
        "ok": all(c["ok"] for c in checks),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "last_run": started.isoformat() if started else None,
        "newest_bout": newest.isoformat() if newest else None,
        "checks": checks,
    }


def report_health():
    """Record health after a run; a broken check must not mask the run itself."""
    try:
        report = health()
        HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        HEALTH_FILE.write_text(json.dumps(report, indent=2), encoding="utf-8")
        failing = [c["name"] for c in report["checks"] if not c["ok"]]
        log("Health: ok" if report["ok"] else f"Health: UNHEALTHY -- {', '.join(failing)}")
        return report
    except Exception as exc:
        log(f"Health check itself failed: {type(exc).__name__}: {exc}")
        return None


# --- the run ------------------------------------------------------------------

class _Tee:
    """Write to the console and the run log at once."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            s.write(data)
            s.flush()

    def flush(self):
        for s in self.streams:
            s.flush()


def _force_utf8_stdio():
    """Task Scheduler gives a cp1252 console and the scraper prints non-ASCII,
    which would otherwise raise UnicodeEncodeError and kill a healthy run."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _acquire_lock():
    """Stop two refreshes writing at once; the ratings step truncates a table."""
    if LOCK_FILE.exists():
        age = time.time() - LOCK_FILE.stat().st_mtime
        if age < LOCK_STALE_SECONDS:
            log(f"Another refresh is running (lock is {age / 60:.0f} min old). Exiting.")
            return False
        log(f"Clearing stale lock ({age / 3600:.1f}h old).")
        LOCK_FILE.unlink()
    LOCK_FILE.write_text(f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}\n")
    return True


def run(skip=(), dry_run=False):
    started = datetime.now(timezone.utc)
    results, failed_step = {}, None
    log(f"Refresh started {started.isoformat()}" + ("  [DRY RUN]" if dry_run else ""))
    if not dry_run:
        if not _acquire_lock():
            return 0
        create_tables()
    try:
        for name, step in STEPS:
            if name in skip:
                log(f"--- {name}: skipped ---")
                results[name] = {"skipped": "flag"}
                continue
            log(f"--- {name} ---")
            step_started = time.time()
            try:
                results[name] = step(dry_run)
                log(f"--- {name} ok ({time.time() - step_started:.1f}s) ---")
            except Exception as exc:
                failed_step = name
                results[name] = {"error": f"{type(exc).__name__}: {exc}"}
                log(f"--- {name} FAILED: {type(exc).__name__}: {exc} ---")
                traceback.print_exc(file=sys.stdout)
                break
    finally:
        if not dry_run:
            LOCK_FILE.unlink(missing_ok=True)

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    log(f"Refresh finished in {elapsed:.1f}s"
        + (f" -- FAILED at '{failed_step}'" if failed_step else " -- ok"))
    if not dry_run:
        STATUS_FILE.write_text(json.dumps({
            "started": started.isoformat(), "elapsed_seconds": round(elapsed, 1),
            "ok": failed_step is None, "failed_step": failed_step, "results": results,
        }, indent=2, default=str), encoding="utf-8")
        report_health()
    return 1 if failed_step else 0


def main():
    parser = argparse.ArgumentParser(description="Refresh data, ratings, history and forecasts.")
    for name, _ in STEPS:
        parser.add_argument(f"--skip-{name}", action="store_true", help=f"skip the {name} step")
    parser.add_argument("--dry-run", action="store_true", help="report, write nothing")
    parser.add_argument("--health", action="store_true", help="run the health checks only")
    args = parser.parse_args()

    _force_utf8_stdio()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if args.health:
        report = report_health() or {"ok": False, "checks": []}
        for c in report["checks"]:
            print(f"  {'ok  ' if c['ok'] else 'FAIL'}  {c['name']:<22} {c['detail']}")
        return 0 if report["ok"] else 1

    log_path = LOG_DIR / f"refresh-{datetime.now().strftime('%Y-%m-%d')}.log"
    with io.open(log_path, "a", encoding="utf-8", newline="\n") as log_file:
        original, sys.stdout = sys.stdout, _Tee(sys.stdout, log_file)
        try:
            return run(skip={n for n, _ in STEPS if getattr(args, f"skip_{n}")},
                       dry_run=args.dry_run)
        finally:
            sys.stdout = original


if __name__ == "__main__":
    sys.exit(main())
