"""FastAPI application for the UFC analytics platform.

Read-only. No authentication, no prediction surface -- see
docs/product-spec.md for why prediction is deferred.

Run:
    uvicorn src.api.main:app --port 8420 --reload
"""

from datetime import date
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.api import queries as q
from src.db.connection import get_connection
from src.ratings import queries as rq

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DIST = PROJECT_ROOT / "frontend" / "dist"

app = FastAPI(
    title="UFC Analytics API",
    description="Glicko-2 ratings, point-in-time statistics and historical "
                "analysis over every UFC bout since 1994.",
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
    """Liveness plus a summary of what the database currently holds."""
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
            "coverage": {"first_bout": first, "last_bout": last}}


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
    """How the rating engine works and where it stops working; the collapse in
    discrimination with experience is why this site shows no predictions."""
    return {
        "system": "Glicko-2, implemented from scratch",
        "parameters": {"initial_rating": 1500, "initial_rd": 150,
                       "tau": 0.5, "rating_period_days": 182.5},
        "discrimination_by_experience": [
            {"less_experienced_fighter_had": "<=2 prior UFC bouts", "n": 114, "auc": 0.643},
            {"less_experienced_fighter_had": "3-5", "n": 737, "auc": 0.614},
            {"less_experienced_fighter_had": "6-9", "n": 1013, "auc": 0.562},
            {"less_experienced_fighter_had": "10+", "n": 1639, "auc": 0.528},
        ],
        "note": "Among two established fighters the rating is close to a coin "
                "flip. That is UFC matchmaking engineering competitive parity, "
                "not a defect in the engine -- and it is why this site reports "
                "history rather than predictions.",
        "known_limitations": [
            "Mean ratings differ by about 43 points between divisions, so "
            "pound-for-pound comparison is not strictly like-for-like.",
            "Ratings use UFC bouts only; every debutant starts at 1500 "
            "regardless of what they achieved elsewhere.",
            "Margin of victory is ignored: a split decision and a ten-second "
            "knockout move the rating identically.",
        ],
    }


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
