"""The logic the site's numbers rest on, tested without a database.

Glicko-2 is checked against the worked example in Glickman's paper, the engine
against the symmetry it is built to have, the narrator's fact check against an
invented statistic, and the record page's rules against the cases fans see.
"""

import math

import numpy as np
import pandas as pd
import pytest

from src.api.cards import _scored, _tally
from src.engine.narrate import ungrounded
from src.engine.predict import check_compatible, load, predict
from src.ratings import (Glicko2Fighter, _expected_score, _g, _new_volatility, _scale_down,
                         update_ratings)


# --- Glicko-2 -------------------------------------------------------------------

def test_glicko2_matches_glickmans_worked_example():
    """Glickman (2012), section 3: a 1500 player beats a 1400, loses to a 1550 and a 1700."""
    mu, phi = _scale_down(Glicko2Fighter(1500, 200, 0.06))
    games = [(Glicko2Fighter(1400, 30), 1), (Glicko2Fighter(1550, 100), 0),
             (Glicko2Fighter(1700, 300), 0)]
    opponents = [(*_scale_down(opp), s) for opp, s in games]

    v = 1 / sum(_g(pj) ** 2 * _expected_score(mu, mj, pj) * (1 - _expected_score(mu, mj, pj))
                for mj, pj, _ in opponents)
    gain = sum(_g(pj) * (s - _expected_score(mu, mj, pj)) for mj, pj, s in opponents)
    volatility = _new_volatility(phi, 0.06, v, v * gain, tau=0.5)
    new_phi = 1 / math.sqrt(1 / (phi ** 2 + volatility ** 2) + 1 / v)
    new_mu = mu + new_phi ** 2 * gain

    assert volatility == pytest.approx(0.05999, abs=1e-5)
    assert new_mu * 173.7178 + 1500 == pytest.approx(1464.06, abs=0.01)
    assert new_phi * 173.7178 == pytest.approx(151.52, abs=0.01)


def test_rating_moves_the_right_way():
    winner, loser = update_ratings(Glicko2Fighter(), Glicko2Fighter(), 1.0)
    assert winner.rating > 1500 > loser.rating
    assert winner.rating - 1500 == pytest.approx(1500 - loser.rating)
    assert winner.rd < 150 and loser.rd < 150  # a fight is information either way

    held_a, held_b = update_ratings(Glicko2Fighter(), Glicko2Fighter(), 0.5)
    assert held_a.rating == pytest.approx(1500) and held_b.rating == pytest.approx(1500)


def test_upsets_move_ratings_further_than_expected_wins():
    favourite, underdog = Glicko2Fighter(1700, 80), Glicko2Fighter(1400, 80)
    expected, _ = update_ratings(favourite, underdog, 1.0)
    _, upset = update_ratings(favourite, underdog, 0.0)
    assert upset.rating - 1400 > expected.rating - 1700 > 0


# --- the forecasting engine -------------------------------------------------------

def test_published_model_matches_the_code():
    check_compatible(load())  # raises if the feature constants have drifted


def test_forecasts_are_symmetric_and_sum_to_one():
    art = load()
    cols = list(art["coefficients"])
    rows = pd.DataFrame(np.random.default_rng(0).normal(size=(200, len(cols))), columns=cols)
    p = predict(art, rows)
    assert np.all((p > 0) & (p < 1))
    assert np.allclose(p + predict(art, -rows), 1)       # swapping corners flips the odds
    assert np.allclose(predict(art, rows * 0), 0.5)      # identical fighters are a coin flip


def test_a_better_rating_means_a_better_chance():
    art = load()
    rows = pd.DataFrame(0.0, index=range(3), columns=list(art["coefficients"]))
    rows["rating"] = [-1.0, 0.0, 1.0]
    assert list(np.argsort(predict(art, rows))) == [0, 1, 2]


# --- the narrator's fact check -------------------------------------------------------

SHEET = {"forecast": {"win_probability_percent": 76},
         "fighters": [{"name": "Joshua Van", "age": 24, "slpm": 8.43},
                      {"name": "Alexandre Pantoja", "age": 36, "slpm": 4.38}]}


def test_narration_may_use_and_round_the_facts_it_was_given():
    text = "Van, 24, is a 76 percent pick and lands 8.4 strikes a minute to 4.38 for the 36-year-old."
    assert ungrounded(text, SHEET) == []


def test_narration_with_an_invented_number_is_rejected():
    assert ungrounded("Van lands 12.7 strikes a minute and has 9 knockouts.", SHEET) == ["12.7", "9"]


# --- the record page -------------------------------------------------------------------

def _row(p_a, a_won=True, result="win"):
    return {"p_a": p_a, "a_won": a_won, "result": result, "fighter_a_id": 1, "fighter_b_id": 2,
            "fighter_a_name": "Gable Steveson", "fighter_b_name": "Sean Sharaf",
            "method": "KO/TKO", "round": 1, "time": "0:12"}


def test_record_calls_picks_upsets_and_toss_ups_the_way_fans_would():
    hit = _scored(_row(0.76))
    upset = _scored(_row(0.91, a_won=False))
    close_loss = _scored(_row(0.57, a_won=False))
    toss_up = _scored(_row(0.505))
    draw = _scored(_row(0.70, a_won=None, result="draw"))

    assert hit["correct"] is True and hit["upset"] is False
    assert upset["correct"] is False and upset["upset"] is True
    assert upset["winner_chance"] == pytest.approx(0.09)
    assert close_loss["correct"] is False and close_loss["upset"] is False
    assert toss_up["pick"] is None and toss_up["correct"] is None
    assert draw["correct"] is None

    assert _tally([hit, upset, close_loss, toss_up, draw]) == {
        "picks": 3, "correct": 1, "confident_picks": 2, "confident_correct": 1}
