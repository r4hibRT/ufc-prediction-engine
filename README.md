# UFC fight forecasts

Win probabilities for every upcoming UFC fight, published before each card,
locked on fight night and scored once the results are in. Behind them is a
self-updating pipeline over every UFC bout since 1994: a scraper, a Glicko-2
rating system written from scratch, point-in-time fighter history, and a
forecasting model validated on seven years of fights it never saw.

## What the site shows

- **Fight cards.** Each upcoming card is laid out main event first. Every fight
  shows both fighters' win probabilities, a tale of the tape, and a short written
  read of the matchup.
- **The record.** Every forecast is frozen on fight day and scored against the
  result. Nothing is revised after the fact.
- **Fighters and rankings.** Career rating trajectories, point-in-time
  statistics, and an all-time pound-for-pound board. There is also a "time
  machine" that shows the board as it stood on any past date.

## How it works

A scheduled refresh runs twice a week. Monday runs score the weekend's cards and
forecast the next ones; Saturday morning runs pick up weigh-in changes. Each step
rebuilds its output from scratch, so a failed run can simply be run again:

```
scrape  ->  ratings  ->  history  ->  forecast  ->  narrate
```

1. **Scrape.** New events, bouts, per-fight statistics and fighter profiles come
   from ufcstats.com. The checkpoint lives in the database, so an event is
   fetched until every one of its bouts is stored, and never again after that.
2. **Ratings.** Standard Glicko-2, updated after every bout. A fighter's rating
   uncertainty grows while they are out of the cage. These are the site's
   long-memory ratings, used for historical rankings.
3. **History.** Every bout is replayed in date order to record what each fighter
   had done *before* it: record, streaks, finishing rates, striking and grappling
   rates. ufcstats publishes career totals; this gives the numbers as they stood
   going into each fight. Title defences are derived here too.
4. **Forecast.** Every listed upcoming bout is run through the frozen model, and
   the result is stored with the tale of the tape as it stood at that moment.
5. **Narrate.** A language model writes a short read of each fight. It is given
   only the stored facts, and every number it writes is checked against them
   before it is published.

Health checks run after every refresh and on every page load. A banner appears
on the site if data goes stale or a run fails.

## The forecasting model

```
logit P(A beats B) = b0 * logit(Glicko probability) + sum of b_i * (A_i - B_i)
```

- **Rating.** Its own Glicko-2 replay with shorter memory than the site's
  ratings. Its constants were tuned for forecasting, and history rankings would
  suffer from them.
- **Corrections.** These are differences between the two fighters in what they
  had shown before the bout. Noisy per-minute rates are shrunk toward the
  division average, so a debutant starts from a sensible prior rather than a
  blank.
- **Regression.** L2 logistic regression with no intercept on antisymmetric
  inputs, so the two fighters' probabilities always sum to exactly 1.
- **Feature selection.** A group of features entered the model only if it
  improved out-of-sample log loss beyond bootstrap noise and every coefficient
  kept the same sign across validation years. Of twelve candidate groups, **age**
  and **striking** passed. Grappling, reach, layoff, experience, form and the
  rest did not.
- **No lookahead.** A truncation test rebuilds the features with history cut at
  a date and checks they match the full build exactly.

**Validation.** The model was trained on everything before each year and tested
on that year, for 2019–2025 (3,482 fights):

| | Log loss | Brier | AUC |
|---|---|---|---|
| Coin flip | 0.693 | 0.250 | 0.500 |
| Rating alone | 0.676 | 0.242 | 0.601 |
| **Rating + age + striking** | **0.652** | **0.230** | **0.661** |

The model was then frozen. From that point, the forecasts it publishes are the
real test of it, and the site keeps that record in full.

## Running it locally

Requirements: Python 3.13, PostgreSQL, Node 22, and Playwright's Chromium
(`python -m playwright install chromium`).

1. Copy `.env.example` to `.env` and fill in the database credentials.
2. Run:

   ```
   python -m src.db               # create the tables
   python -m src.refresh          # scrape, rate, replay, forecast (first run takes hours)
   scripts\serve.cmd              # build the site and serve it on http://localhost:8420
   ```

For development with hot reload, run `scripts\dev.cmd`. It starts the API on
port 8420 and the frontend on http://localhost:5180.

The written match reads are optional. Without `src/engine/voice.py` and an API
key, they are skipped and the site shows a placeholder in their place.
`src/engine/voice.example.py` describes the file.

## Layout

```
src/
  db.py              connection, schema, shared SQL
  scrape.py          ufcstats parsing, storage, incremental scrape
  ratings.py         Glicko-2 and the site's ratings
  history.py         point-in-time replay, snapshots, title defences
  refresh.py         the scheduled pipeline and its health checks
  engine/
    features.py      the tuned rating and the correction features
    train.py         evaluation, rating tuning, feature selection, artifact
    predict.py       forecasting cards, locking, scoring
    narrate.py       the written match reads, with fact checks
  api/
    main.py          routes, and the server for the built frontend
    cards.py         fight cards, tale of the tape, the record
    queries.py       fighter and rankings queries
frontend/src/        React app: Fights, Fighters, Rankings
models/              frozen model artifacts (JSON)
scripts/             Windows launchers: dev, serve, scheduled refresh
```

## Data

Fight data comes from [ufcstats.com](http://www.ufcstats.com). This repository
contains the code that builds the dataset, not the dataset itself.

No licence has been chosen yet, so standard copyright applies: you are welcome to
read the code, but please ask before reusing it.
