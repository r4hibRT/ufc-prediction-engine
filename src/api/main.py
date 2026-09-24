"""The site's API, and the server for the built frontend.

Read-only, no authentication. Routes live under /api; everything else serves
the React app from frontend/dist, so the whole site runs on one port.

Run:
    uvicorn src.api.main:app --port 8420
"""

from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi import Path as PathParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.api import cards as c
from src.api import queries as q
from src.db import get_connection
from src.refresh import health as pipeline_health

DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

app = FastAPI(
    title="UFC forecasts",
    description="Fight forecasts, Glicko-2 ratings and point-in-time statistics "
                "for every UFC bout since 1994.",
    version="1.0.0",
)

# Without the prefix "/rankings" is claimed by both the API and the SPA router,
# and a hard refresh returns raw JSON.
router = APIRouter(prefix="/api")

# In development the frontend runs on Vite's own port.
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5180"],
                   allow_methods=["GET"], allow_headers=["*"])


@router.get("/health", tags=["meta"])
def health():
    """Liveness, what the database holds, and pipeline health checked live, so a
    scheduled run that never happened still surfaces as stale."""
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


# --- forecasts ----------------------------------------------------------------

@router.get("/cards", tags=["forecasts"])
def cards():
    """Upcoming cards, plus recent ones with results, in date order."""
    return c.get_cards()


@router.get("/cards/{event_id}", tags=["forecasts"])
def card(event_id: str = PathParam(..., pattern="^[0-9a-f]{16}$")):
    """A full card: forecasts, the tale of the tape frozen with them, and results."""
    result = c.get_card(event_id)
    if result is None:
        raise HTTPException(status_code=404, detail="No forecasts for that card")
    return result


@router.get("/record", tags=["forecasts"])
def record():
    """Every locked forecast checked against the result, card by card."""
    return c.get_record()


# --- fighters -----------------------------------------------------------------

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
    arc = q.get_career_arc(fighter_id)
    if not arc:
        raise HTTPException(status_code=404, detail="No rated bouts for this fighter")
    return arc


@router.get("/fighters/{fighter_id}/stats", tags=["fighters"])
def fighter_stats(fighter_id: int):
    """Career aggregates and rates, plus state entering the most recent bout."""
    if q.get_fighter(fighter_id) is None:
        raise HTTPException(status_code=404, detail="Fighter not found")
    return q.get_fighter_stats(fighter_id)


# --- rankings -----------------------------------------------------------------

@router.get("/rankings", tags=["rankings"])
def rankings(limit: int = Query(50, ge=1, le=200), division: str | None = None):
    """All-time ratings: every fighter at their career best, or, for one division,
    at their best there, judged only on the fights they had in it."""
    if division:
        return q.get_division_rankings(division, limit=limit)
    return q.get_p4p_rankings(limit=limit)


app.include_router(router)


# --- the built frontend ---------------------------------------------------------
# Registered last so API routes win.

if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        """Serve a real file if one exists, else index.html, so a hard refresh
        on a client-side route returns the app rather than a 404."""
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="No such API route")
        candidate = (DIST / full_path).resolve()
        if full_path and candidate.is_file() and DIST.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(DIST / "index.html")
