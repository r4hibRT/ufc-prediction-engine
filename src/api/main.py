"""FastAPI application for the UFC analytics platform.

Read-only, no authentication. Forecasts are served from the prediction
engine's `predictions` table; see docs/engine-plan.md.

Run:
    uvicorn src.api.main:app --port 8420 --reload
"""

from datetime import date
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi import Path as PathParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.api import predictions as pq
from src.api import queries as q
from src.automation.health import check as pipeline_health
from src.db.connection import get_connection
from src.ratings import queries as rq

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DIST = PROJECT_ROOT / "frontend" / "dist"

app = FastAPI(
    title="UFC Analytics API",
    description="Glicko-2 ratings, point-in-time statistics, historical "
                "analysis and fight forecasts over every UFC bout since 1994.",
    version="0.1.0",
)

# API routes live under /api; without the prefix "/rankings" is claimed by
# both the API and the SPA router and a refresh returns raw JSON.
router = APIRouter(prefix="/api")

# The SPA is served separately in development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5180"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@router.get("/health", tags=["meta"])
def health():
    """Liveness, what the database holds, and pipeline health checked live, so a
    weekly job that never ran still surfaces as stale."""
    conn = get_connection()
    cur = conn.cursor()
    counts = {}
    for table in ("fighters", "bouts", "bout_stats", "ratings", "bout_snapshots"):
        cur.execute(f"SELECT COUNT(*) FROM {table};")
        counts[table] = cur.fetchone()[0]
    cur.execute("SELECT MIN(date), MAX(date) FROM bouts;")
    first, last = cur.fetchone()
    cur.close()
    conn.close()
    return {"status": "ok", "counts": counts,
            "coverage": {"first_bout": first, "last_bout": last},
            "pipeline": pipeline_health()}


# --- rankings ---------------------------------------------------------------

@router.get("/rankings", tags=["rankings"])
def rankings(limit: int = Query(50, ge=1, le=200),
             min_bouts: int = Query(8, ge=1)):
    """All-time pound-for-pound board with championship and resume context."""
    return q.get_p4p_rankings(limit=limit, min_bouts=min_bouts)


@router.get("/rankings/asof/{as_of}", tags=["rankings"])
def rankings_asof(as_of: date,
                  limit: int = Query(25, ge=1, le=200),
                  division: str | None = None):
    """The board as it stood on any past date; one query, because every rating
    at every date is already stored."""
    return q.get_rankings_asof(as_of, limit=limit, division=division)


# --- fighters ---------------------------------------------------------------

@router.get("/fighters", tags=["fighters"])
def fighters(q_: str = Query(..., alias="q", min_length=2),
             limit: int = Query(20, ge=1, le=100)):
    return q.search_fighters(q_, limit=limit)


@router.get("/fighters/{fighter_id}", tags=["fighters"])
def fighter(fighter_id: int):
    result = q.get_fighter(fighter_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Fighter not found")
    return result


@router.get("/fighters/{fighter_id}/career", tags=["fighters"])
def fighter_career(fighter_id: int):
    """Rating trajectory: one point per bout, with opponent and method."""
    arc = rq.get_career_arc(fighter_id)
    if not arc:
        raise HTTPException(status_code=404, detail="No rated bouts for this fighter")
    return arc


@router.get("/fighters/{fighter_id}/stats", tags=["fighters"])
def fighter_stats(fighter_id: int):
    """Career aggregates and rates, plus state entering the most recent bout."""
    if q.get_fighter(fighter_id) is None:
        raise HTTPException(status_code=404, detail="Fighter not found")
    return q.get_fighter_stats(fighter_id)


@router.get("/fighters/{fighter_id}/entering/{bout_id}", tags=["fighters"])
def fighter_entering(fighter_id: int, bout_id: int):
    """Point-in-time view: this fighter's numbers walking into one bout."""
    result = q.get_fighter_stats_entering(fighter_id, bout_id)
    if result is None:
        raise HTTPException(status_code=404, detail="No snapshot for that fighter and bout")
    return result


# --- comparison and insights ------------------------------------------------

@router.get("/compare", tags=["insights"])
def compare(a: int, b: int):
    """Descriptive comparison. Deliberately returns no win probability."""
    result = q.compare_fighters(a, b)
    if result is None:
        raise HTTPException(status_code=404, detail="One or both fighters not found")
    return result


@router.get("/movers", tags=["insights"])
def movers(event_date: date | None = None, limit: int = Query(15, ge=1, le=100)):
    """Biggest rating changes from the most recent card."""
    return q.get_movers(event_date=event_date, limit=limit)


@router.get("/insights/upsets", tags=["insights"])
def upsets(limit: int = Query(10, ge=1, le=100)):
    return rq.get_biggest_upsets(limit=limit)


@router.get("/ratings-card", tags=["meta"])
def ratings_card():
    """How the site's rating and the forecasting engine work, and where they stop."""
    return {
        "system": "Glicko-2, implemented from scratch; standard updates, no adjustments",
        "parameters": {"initial_rating": 1500, "initial_rd": 150, "initial_volatility": 0.06,
                       "tau": 0.5, "rating_period_days": 182.5},
        "forecasts": "A separate engine: a shorter-memory Glicko rating recalibrated "
                     "and corrected for age and striking rates, fitted by logistic "
                     "regression. See /api/predictions/record for how it has scored.",
        "known_limitations": [
            "Mean ratings differ between divisions, so pound-for-pound "
            "comparison is not strictly like-for-like.",
            "Ratings use UFC bouts only; every debutant starts at 1500 "
            "regardless of what they achieved elsewhere.",
            "Margin of victory is ignored: a split decision and a ten-second "
            "knockout move the rating identically.",
        ],
    }


# --- predictions ------------------------------------------------------------

@router.get("/cards", tags=["predictions"])
def cards():
    """Upcoming cards, plus recent ones with results, in date order."""
    return pq.get_cards()


@router.get("/cards/{event_id}", tags=["predictions"])
def card(event_id: str = PathParam(..., pattern="^[0-9a-f]{16}$")):
    """A full card: forecasts, the tale of the tape frozen with them, and results."""
    result = pq.get_card(event_id)
    if result is None:
        raise HTTPException(status_code=404, detail="No forecasts for that card")
    return result


@router.get("/predictions/record", tags=["predictions"])
def prediction_record(limit: int = Query(50, ge=1, le=500)):
    """How frozen forecasts scored once fought, with the model's validation metrics."""
    return pq.get_record(limit=limit)


app.include_router(router)


# --- built single-page app --------------------------------------------------
# Registered last so API routes win; serves the whole site on one port.

if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        """Serve a real file if one exists, else index.html so a hard refresh
        on a client-side route returns the app rather than a 404."""
        candidate = (DIST / full_path).resolve()
        if full_path and candidate.is_file() and DIST.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(DIST / "index.html")
