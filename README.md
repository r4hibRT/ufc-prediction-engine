# UFC Ratings

A self-updating analytics platform over every UFC bout since 1994. Rates every
fighter through time with a from-scratch Glicko-2 implementation, and publishes
point-in-time statistics that career totals cannot show.

## What it does

- **Pound-for-pound rankings** — all-time peak and currently active, ranked by
  rating less two rating deviations so a high rating earned over few bouts is
  discounted against one earned over many
- **Time machine** — the board as it stood on any past date, from stored
  ratings rather than a reconstruction
- **Career trajectories** — every fighter's rating over time with its
  uncertainty band, wins and losses marked
- **Point-in-time statistics** — what a fighter's numbers looked like *entering*
  any given bout, not just their career totals
- **Weekly refresh** — scrapes new events, recomputes every rating and rewrites
  snapshots with no manual step

## Stack

- **Scraping** — Playwright, BeautifulSoup
- **Database** — PostgreSQL
- **Backend** — FastAPI
- **Frontend** — React, Vite, Recharts
- **Ratings** — custom Glicko-2

## Running it

Copy `.env.example` to `.env` and fill in the database credentials, then:

```
python -m src.db.schema          # create tables
scripts\refresh.cmd              # scrape, rate, snapshot
scripts\serve.cmd                # build and serve on http://localhost:8000
```

For development with hot reload, `scripts\dev.cmd` runs the API on `:8000` and
Vite on `localhost:5173`.

## Layout

```
src/analytics/    replay of bout history into per-bout state
src/api/          FastAPI app and analytics queries
src/automation/   weekly refresh and one-off backfills
src/db/           schema, connection, inserts, snapshots
src/ratings/      Glicko-2 engine and rating queries
src/scraper/      ufcstats extraction and ingest pipeline
frontend/         React single-page app
docs/             specifications
```

## On predictions

The site reports history and deliberately shows no fight predictions. The
rating's ability to pick a winner collapses as fighters become established --
AUC 0.64 when one fighter has two or fewer prior UFC bouts, 0.53 when both have
ten or more. That is matchmaking engineering competitive parity among ranked
fighters, not a defect in the engine, and it means a confident-looking
probability would not be honest. Forecasting work is tracked separately on
`epic/prediction-engine`.

## Status

In active development.
