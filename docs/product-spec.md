# Product specification — analytics platform

The first version of the thing a person actually opens. Everything here is
buildable from data already in the database. No bookmaker odds, no model
serving, no prediction surface.

Prediction, the self-scoring loop and the market benchmark are deferred to the
next cycle.

---

## 1. Scope and rationale

The project has never had an end-to-end artifact — ratings, features and models
exist only as terminal output. Building the analytics front end now:

- **De-risks the outcome.** If next cycle's market benchmark is unflattering,
  a working analytics product still stands on its own.
- **Removes external dependencies.** Odds sourcing is unresolved. Nothing here
  waits on it.
- **Plays to the measured strength of the data.**

### Why analytics and not prediction

Measured on validation, the rating's discrimination collapses as fighters gain
experience:

| Less-experienced fighter has | n | AUC |
|---|---|---|
| ≤2 prior UFC bouts | 114 | 0.643 |
| 3–5 | 737 | 0.614 |
| 6–9 | 1,013 | 0.562 |
| 10+ | 1,639 | **0.528** |

Among two established fighters the rating is close to a coin flip. That is UFC
matchmaking engineering competitive parity, not a defect in the engine — and it
means a prediction surface would have to present numbers the data does not
support. The same ratings are strong for ranking, trajectory and historical
comparison. **Lead with description.**

---

## 2. What is missing

One gap, and it is smaller than it was when prediction was in scope.

### Per-bout snapshots

The replay in `build_dataset.py` computes each fighter's state as it walks
forward through history, then discards everything but the feature row.
Point-in-time statistics — the product's main differentiator — need that state
persisted per bout.

```
bout_snapshots
  bout_id              INTEGER REFERENCES bouts(id)
  fighter_id           INTEGER REFERENCES fighters(id)
  date                 DATE NOT NULL
  -- record entering the bout
  bouts_before         INTEGER
  wins_before          INTEGER
  win_streak           INTEGER
  loss_streak          INTEGER
  recent_form_5        NUMERIC(4,3)
  -- rating entering the bout
  rating_before        NUMERIC(8,2)
  rd_before            NUMERIC(8,2)
  peak_rating_before   NUMERIC(8,2)
  layoff_days          INTEGER
  -- durability entering the bout
  ko_losses            INTEGER
  sub_losses           INTEGER
  finishes             INTEGER
  career_seconds       INTEGER
  -- cumulative fight statistics entering the bout
  sig_landed           INTEGER
  sig_attempted        INTEGER
  sig_absorbed         INTEGER
  td_landed            INTEGER
  td_attempted         INTEGER
  opp_td_landed        INTEGER
  opp_td_attempted     INTEGER
  control_seconds      INTEGER
  sub_attempts         INTEGER
  knockdowns           INTEGER
  knockdowns_absorbed  INTEGER
  -- strength of schedule entering the bout
  avg_opponent_rating  NUMERIC(8,2)
  max_opponent_rating  NUMERIC(8,2)
  PRIMARY KEY (bout_id, fighter_id)
```

Roughly 17,700 rows, written by the same replay that builds the dataset.
Queryable columns rather than a JSONB blob, because these are read directly by
the analytics endpoints.

Everything else the platform needs already exists.

---

## 3. Architecture

```
PostgreSQL ──> FastAPI (src/api/) ──> React SPA (frontend/)
     ^
     └── weekly refresh: scrape → rate → features → snapshots → movers
```

FastAPI because the read-only query layer already exists in
`src/ratings/queries.py` — 188 lines currently imported by nothing. React to
match the README and diversify against existing Vue work.

No authentication. Read-only public data.

---

## 4. API surface

| Method | Path | Returns |
|---|---|---|
| GET | `/rankings` | All-time peak rankings, RD-adjusted. Params: `limit`, `min_bouts`, `division` |
| GET | `/rankings/current` | Active fighters by current rating |
| GET | `/rankings/asof/{date}` | **Time machine** — rankings as they stood on any date |
| GET | `/fighters` | Search. Params: `q`, `limit` |
| GET | `/fighters/{id}` | Profile: physicals, record, current and peak rating |
| GET | `/fighters/{id}/career` | Rating trajectory with opponent, method, result per bout |
| GET | `/fighters/{id}/stats` | Aggregates — career, point-in-time, opponent-adjusted |
| GET | `/compare?a={id}&b={id}` | Tale of the tape, both trajectories, common opponents |
| GET | `/divisions` | List with current champion and top contenders |
| GET | `/divisions/{name}/lineage` | **Title history** — champions and defences over time |
| GET | `/divisions/{name}/trends` | Finish rate, fight duration, grappling volume over time |
| GET | `/events/upcoming` | Next cards with both fighters' ratings, records, form |
| GET | `/movers` | Biggest rating gains and losses from the most recent event |
| GET | `/records` | Streaks, title defences, rating milestones, biggest swings |
| GET | `/insights/era` | Rating inflation and era-level aggregates |
| GET | `/insights/upsets` | Biggest upsets by pre-bout expected score |
| GET | `/ratings-card` | How the engine works, its calibration and its limits |

Most wrap existing queries. `/rankings/asof`, `/divisions/*/lineage`,
`/movers` and `/records` are new but are single queries against data already
present.

---

## 5. Views

**Rankings** — landing page. Sortable, division filter, peak vs current toggle.
RD rendered as a visible uncertainty band: a fighter with 13 bouts and one with
33 should not look equally certain.

**Time machine** — the same table with a date control. Every rating at every
date already exists; nobody else publishes this.

**Fighter** — rating trajectory as the hero chart, bouts as points with
opponent and method on hover. Below: record, career aggregates, and the
point-in-time panel.

**Compare** — two trajectories on one axis, tale of the tape, common opponents,
shared-era context. Descriptive only; no probability.

**Division** — current champion, contenders, title lineage, and trend charts.

**Upcoming** — next card with both fighters' ratings, records, recent form and
physical comparison. No prediction.

**Insights** — era inflation (mean rating 1509 → 1570), rating discrimination
by experience, biggest upsets, records and streaks.

**Ratings card** — how Glicko-2 works, what RD means, calibration of its own
expected scores, and the AUC-by-experience table. This explains to a visitor
*why* there are no predictions, and turns a limitation into the most memorable
thing on the site.

---

## 6. Statistics and aggregates

Career totals alone duplicate ufcstats. Include them because a rating without a
record is unreadable — but the value is in the two views nobody else has.

**Career aggregates** (from `bout_stats`): record by method, significant strikes
landed and absorbed per minute, striking accuracy and differential, takedowns
per 15, takedown accuracy and defence, control time per minute, submission
attempts per 15, knockdown rate, average fight time, finish rate.

**Point-in-time** — what a fighter's numbers looked like *entering* any given
bout, straight from `bout_snapshots`. "Volkanovski's takedown defence going
into the Makhachev fight" exists nowhere else.

**Opponent-adjusted** — rate statistics weighted by the pre-bout rating of the
opposition faced, plus percentile rank within division and era. 4.2 strikes per
minute means nothing until you know it was against opponents averaging 1580.

**Division-level** — finish rates, fight duration and grappling volume by
division over time.

---

## 7. Weekly loop

Two steps added to the existing three:

1. scrape new events *(exists)*
2. recompute ratings *(exists)*
3. rebuild features and dataset *(exists)*
4. **write per-bout snapshots**
5. **compute movers for the most recent event**

Step 5 is what gives the site a reason to be revisited: biggest rating gains and
losses, rankings changes, new entrants to the top 25 — refreshed after every
card.

---

## 8. Deliberately excluded

- **Prediction of any kind.** Deferred to next cycle with the market benchmark.
- **Anything market-comparative.** No odds data.
- **The GOAT bracket.** Unfalsifiable, and it would undercut a product whose
  distinguishing feature is honesty about uncertainty.
- **Authentication, accounts, saved views.** No user need in v1.

---

## 9. Open questions

**Hosting.** Local-only or deployed? Deployment means managed Postgres and
changes how the weekly job runs.

**Division assignment.** A fighter's "division" is currently inferred from their
most recent bout. Fighters who move divisions will be filed under the latest
one, which may misrepresent their career. Consider primary division by bout
count instead.

**Cross-division rating drift.** Mean ratings differ by 43 points on average
(81 at worst) between divisions, so the pound-for-pound comparison is not
strictly like-for-like. Either normalise, or state the caveat on the rankings
page. The README's claim of ratings "normalised across weight classes" is
currently not true.

---

## 10. Build order

1. `bout_snapshots` table and the snapshot writer
2. Analytics query layer — extend `src/ratings/queries.py` or add
   `src/api/queries.py` for the new aggregates
3. FastAPI skeleton, CORS, the rankings and fighter endpoints
4. React shell, routing, Rankings view
5. Fighter view with the trajectory chart
6. Aggregates — career, point-in-time, opponent-adjusted
7. Compare and Division views
8. Time machine, lineage, records
9. Movers, weekly step 5, Upcoming view
10. Insights and ratings card
11. Deployment, if in scope
