"""Is the pipeline healthy? Checked against live state, not the last log line.

Runs at the end of every refresh and on every /api/health request. Because the
site asks on each load, a weekly job that never ran at all still shows up as
stale -- which a check living only inside that job could never report.

Usage:
    python -m src.automation.health
"""

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from src.db.connection import get_connection

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATUS_FILE = PROJECT_ROOT / "logs" / "last_run.json"
HEALTH_FILE = PROJECT_ROOT / "logs" / "health.json"

MAX_RUN_AGE_DAYS = 8
MAX_DATA_AGE_DAYS = 14   # UFC takes the odd weekend off
STUCK_ATTEMPTS = 2


def _result(name, ok, detail):
    return {"name": name, "ok": bool(ok), "detail": detail}


def _last_run_checks():
    if not STATUS_FILE.exists():
        missing = "no refresh has recorded a status"
        return [_result("last_run_succeeded", False, missing),
                _result("last_run_recent", False, missing)], None

    status = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    started = datetime.fromisoformat(status["started"])
    age = (datetime.now(timezone.utc) - started).days
    ok = status.get("ok", False)
    return [
        _result("last_run_succeeded", ok,
                "ok" if ok else f"failed at {status.get('failed_step')}"),
        _result("last_run_recent", age <= MAX_RUN_AGE_DAYS,
                f"{age} day{'s' if age != 1 else ''} ago"),
    ], started


def _data_checks(cur):
    cur.execute("SELECT MAX(date), COUNT(*) FROM bouts;")
    newest, bouts = cur.fetchone()
    age = (date.today() - newest).days if newest else None

    cur.execute("SELECT COUNT(*) FROM bout_snapshots;")
    snapshots = cur.fetchone()[0]

    cur.execute("""
        SELECT name, url, attempts FROM events
        WHERE status = 'partial' AND attempts >= %s
        ORDER BY date DESC NULLS LAST;
    """, (STUCK_ATTEMPTS,))
    stuck = cur.fetchall()

    return [
        _result("data_fresh", age is not None and age <= MAX_DATA_AGE_DAYS,
                f"newest bout {newest}, {age} days old" if newest else "no bouts"),
        _result("snapshots_consistent", snapshots == 2 * bouts,
                f"{snapshots} snapshots for {bouts} bouts"),
        _result("no_stuck_events", not stuck,
                "none" if not stuck else
                "; ".join(f"{n or u} ({a} attempts)" for n, u, a in stuck)),
    ], newest


def check():
    """Run every check and return one report; never raises."""
    checks, started, newest = [], None, None
    try:
        run_checks, started = _last_run_checks()
        checks += run_checks
    except (OSError, ValueError, KeyError) as exc:
        checks.append(_result("last_run_readable", False, str(exc)))

    try:
        conn = get_connection()
        cur = conn.cursor()
        data_checks, newest = _data_checks(cur)
        checks += data_checks
        cur.close()
        conn.close()
    except Exception as exc:
        checks.append(_result("database_reachable", False, f"{type(exc).__name__}: {exc}"))

    return {
        "ok": all(c["ok"] for c in checks),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "last_run": started.isoformat() if started else None,
        "newest_bout": newest.isoformat() if newest else None,
        "checks": checks,
    }


def write_report(report):
    HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    HEALTH_FILE.write_text(json.dumps(report, indent=2), encoding="utf-8")


def main():
    report = check()
    write_report(report)
    for c in report["checks"]:
        print(f"  {'ok  ' if c['ok'] else 'FAIL'}  {c['name']:<22} {c['detail']}")
    print(f"\n  {'HEALTHY' if report['ok'] else 'UNHEALTHY'}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
