"""The site end to end against the real database: every route the frontend uses,
the rule that engine internals never reach the page, and the no-lookahead proof.

Skipped automatically when the database in .env cannot be reached, so the rest
of the suite still runs anywhere. Run just these with `pytest -m db`.
"""

import pytest
from fastapi.testclient import TestClient

from src.db import get_connection


def _database_up():
    try:
        get_connection().close()
        return True
    except Exception:
        return False


pytestmark = [pytest.mark.db,
              pytest.mark.skipif(not _database_up(), reason="needs the PostgreSQL database in .env")]


@pytest.fixture(scope="module")
def api():
    from src.api.main import app
    return TestClient(app)


def get(api, path):
    response = api.get("/api" + path)
    assert response.status_code == 200, f"{path} -> {response.status_code}"
    return response.json()


def test_fight_cards(api):
    cards = get(api, "/cards")
    assert cards, "no cards listed"
    card = get(api, f"/cards/{cards[0]['id']}")
    for bout in card["bouts"]:
        p = bout["prediction"]
        assert p["p_a"] + p["p_b"] == pytest.approx(1, abs=1e-3)
        assert bout["fighters"]["a"]["name"] and bout["fighters"]["b"]["name"]


def test_engine_internals_never_reach_the_page(api):
    for path in ["/cards", f"/cards/{get(api, '/cards')[0]['id']}", "/record"]:
        body = api.get("/api" + path).text
        assert "contributions" not in body and "glicko_p" not in body, path


def test_record(api):
    record = get(api, "/record")
    totals = record["totals"]
    assert 0 <= totals["correct"] <= totals["picks"]
    assert sum(card["picks"] for card in record["cards"]) == totals["picks"]


def test_ratings_overall_and_by_division(api):
    overall = get(api, "/rankings")
    assert len(overall) == 50 and [r["rank"] for r in overall] == list(range(1, 51))
    division = get(api, "/rankings?division=Welterweight")
    assert division and all(r["bouts"] >= 3 for r in division)


def test_fighter_pages(api):
    fighter_id = get(api, "/fighters?q=jon jones")[0]["id"]
    assert get(api, f"/fighters/{fighter_id}")["name"] == "Jon Jones"
    assert get(api, f"/fighters/{fighter_id}/career")
    assert "rates" in get(api, f"/fighters/{fighter_id}/stats")


def test_bad_requests_fail_cleanly(api):
    assert api.get("/api/no-such-route").status_code == 404
    assert api.get("/api/cards/not-an-id").status_code == 422
    assert api.get("/api/fighters/999999999").status_code == 404


def test_no_feature_can_see_the_future():
    from src.engine.features import truncation_test
    assert truncation_test() > 5000
