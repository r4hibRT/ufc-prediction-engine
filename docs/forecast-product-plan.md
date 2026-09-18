# Fight forecasting — plan and honest assessment

Working document for the next cycle. Written to be argued with, and to be the
thing that stops a fourth unplanned pivot.

---

## 1. The idea

A page, per upcoming card, that shows **our win probability beside the
bookmaker's** for each fight — and keeps a public scorecard of how we have
done. Around that centrepiece sit descriptive panels that make each number
interpretable without any statistics background.

The position is deliberately not "we beat the market". It is:

> A transparent, explained probability, next to an opaque market number, on the
> hardest fights in sport, with an honest record kept in public.

That survives losing. "We beat the bookmakers" would not.

---

## 2. Why this is different from what exists

Fight-preview sites give picks with no probability. Odds aggregators give
probabilities with no explanation. Model-based sites publish accuracy claims
with no track record and no calibration.

Nobody publishes all four together: a probability, the reasoning behind it, the
market's number for comparison, and a running record of when they were wrong.

---

## 3. What makes numbers interpretable

The governing rule: **every number gets a reference class.** Three layers, each
needing a different kind of comparator.

### Layer 1 — the stick for the probability

**Calibration record.** *"We say 62%. When we have said 60–65%, the favourite
won 61% of the time, across 340 fights."* Built from thousands of bouts, gets
more convincing every week, and answers "what does this number mean" and "should
I trust you" simultaneously. This is the most important element on the page.

**The market line.** Already a reference point every fan understands. Framed as
divergence — *"six points off the line, on the underdog"* — not as an edge.

### Layer 2 — the stick for each statistic

**Division percentiles on everything.** Not "71-inch reach" but *"71 inches,
82nd percentile at featherweight."* Not "4.2 significant strikes per minute" but
*"4.2, 71st percentile."* Highest comprehension gained per unit of work in the
whole plan, and it runs off data already stored.

### Layer 3 — the stick for the matchup

- **Common opponents** — the oldest argument in the sport, presented properly
- **Biggest test** — *"the highest-rated opponent he has ever faced"*
- **Trajectory overlay** — both rating curves on one axis, needs no explanation
- **Momentum** — rating change over the last three bouts
- **Strength of schedule** — average opponent rating, percentiled
- **Comparable fights** — three real bouts that resembled this one, as narrative
  only, with **no aggregate percentage attached** (see §6)

Layer 3 is descriptive and does not feed the model. The page must not imply
otherwise.

---

## 4. Deliverables

| # | Deliverable | Notes |
|---|---|---|
| D1 | Persisted model artifact | Fitted pipeline + calibrator, versioned, with a metrics sidecar |
| D2 | Current-state store | Each fighter's present feature vector, refreshed weekly |
| D3 | Inference path | Two fighter ids + context → calibrated probability, reusing the training feature code |
| D4 | Odds ingest | Upcoming markets, de-vigged, stored with a timestamp |
| D5 | `predictions` table | Written **before** each card, scored after |
| D6 | Calibration record | Bucketed predicted vs actual, updated after every event |
| D7 | Card page | Both probabilities, calibration line, per-fight expansion |
| D8 | Head-to-head page | Layers 2 and 3 |
| D9 | Weekly loop extension | Predict → store → (card happens) → score |
| D10 | Health check | Alert when a step fails or data goes stale |

---

## 5. Plan

**Phase 0 — rating hygiene** *(done)*
A Glicko-1 style decay constant was implemented and swept from 0 to 80 against
held-out fights. Every increase degraded Brier, AUC and accuracy monotonically,
so it stays at 0. Inactivity and age remain unmodelled, a known limitation.

**Phase 1 — make the model servable**
D1, D2, D3. Restore the feature pipeline from `epic/prediction-engine`, persist
the fitted model, build the inference path against current state. Nothing
user-facing.

**Phase 2 — start the record**
D5, D9. Predict every bout on every card, store before the event, score after.
**This begins accruing value immediately and must start before any UI work**,
because the calibration record is the product and it needs history.

**Phase 3 — odds**
D4. Upcoming markets only. Establish coverage and a fallback state.

**Phase 4 — the page**
D6, D7. Card view, both numbers, calibration line.

**Phase 5 — depth**
D8. Percentiles, common opponents, trajectory overlay, comparables.

**Phase 6 — durability**
D10, then deployment.

Phases 1–2 are the ones with no visible output and the highest risk of being
skipped. They are also the ones the whole thing rests on.

---

## 6. Feasibility

### Verified

- **Upcoming cards scrape cleanly.** The next event returned 13 announced bouts
  with 12 of 13 having both fighters already rated.
- **Live odds are the cheap case.** Upcoming markets sit inside free API tiers;
  it is *historical* odds that are paywalled. The opposite of what was assumed.
- **The feature pipeline is leakage-free and proven so** — rebuilt with history
  truncated, all 54 features byte-identical across 7,664 rows.
- **Rating and snapshot recomputation is cheap** — full rebuild in seconds.

### Failure modes

**Analogues cannot estimate probability.** Tested and rejected. Forty
comparable fights gave 60% for a +24 rating gap, 70% for +72, and **48% for
+83** — non-monotonic, i.e. noise. Standardising the distance also buried the
rating gap under age and experience, making a 200-point mismatch "nearer" than a
six-year age difference. Comparable fights ship as illustration only.

**We will lose to the market on featured fights.** AUC 0.528 where both fighters
have ten or more bouts, against a market at roughly 65% accuracy. Title fights
are definitionally two established fighters. Survivable only with honest framing.

**Track record needs volume.** Roughly 50–60 headline fights a year gives a
Brier standard error near ±0.04 — wider than any plausible gap to the market,
meaning three to five years before the record says anything. **Mitigation:
predict all ~500 fights a year, feature the headliners.** Non-negotiable.

**The odds feed is a single point of failure.** One API underpins the
centrepiece. Needs a coverage check on regional cards and a graceful degraded
state.

**Closing-line timing.** Odds move until the bell; the weekly job runs Sunday.
Comparing against a mid-week snapshot is fine but must be labelled, because
opening lines are a softer benchmark and would flatter us dishonestly.

**People will bet on this.** A 0.53-AUC model displayed beside market odds is an
implicit value-bet finder. Requires explicit framing that it is not advice, and
the divergence view presented as interest rather than edge.

**Silent failure.** Already happened once — the scheduled task died and nothing
reported it. A live product showing a stale card is worse than no product.

**Scope after three pivots.** This is the largest scope yet, and the phases with
no visible output come first.

---

## 7. Honest assessment

### The idea

**As a business: 3/10.** The audience is MMA analytics enthusiasts, which is
small and does not pay. There is no moat — anyone with the same data and three
weekends could copy it. The core proposition, a transparent probability, is not
what most fans want; most want a pick. It will not make money and should not be
built as though it might.

**As a portfolio artifact: 8/10.** It demonstrates an end-to-end system — ingest,
modelling, evaluation, serving, interface — with genuine statistical discipline
and a public, falsifiable claim. That combination is rare and legible to anyone
technical who looks at it.

**As a project worth your time: 7/10.** It sustains interest because it changes
every week, it has a clear finish line, and the calibration record makes progress
visible. The main threat is not technical.

### The project so far

| Component | Rating | Why |
|---|---|---|
| Ingest and data quality | 8/10 | Resumable, idempotent, self-updating. 156 corrupted outcomes found and repaired; weight classes normalised; title defences derived. Genuinely solid. |
| Point-in-time snapshot layer | **9/10** | The single most valuable thing built. Does not exist publicly anywhere, and is proven leakage-free. |
| Rating engine | 6/10 | Faithful Glicko-2, beats Elo on AUC (0.600 vs 0.552), but weak in absolute terms, with an inert decay constant and five untuned parameters. |
| Analytics platform | 7/10 | Works, is differentiated, four of six tabs are still stubs. |
| Prediction model | 4/10 | Honest and well-evaluated, but 43 of 54 features ablate to noise and it cannot beat picking the higher-rated fighter by much. |
| Engineering discipline | **9/10** | Leak proofs, seed-noise controls, bootstrap intervals, block ablation, a documented defect audit. This is the standout and it is what makes the rest credible. |

### A provisional observation, not a finding

The current, unfinalised rating shows discrimination falling as fighters become
established — AUC 0.643 at ≤2 prior bouts, 0.528 at 10+ — consistent with the
UFC matching established fighters for parity. The current ablation also has
most engineered features performing near random noise. Neither is a conclusion:
both come from a model with open defects and no final feature selection, and
must be re-measured once the model is settled.

### The real risk

Not feasibility. **Attrition.** Three pivots have already happened, each
reasonable, each costing momentum. This plan front-loads two phases with no
visible output. If Phase 1 and 2 stall, everything downstream is worthless, and
the temptation will be to jump to the visible parts and retrofit the record —
which destroys the only thing that makes the product credible.

---

## 8. Kill criteria

Decided now, while it is cheap to be objective.

- **Odds coverage below ~80% of bouts** on a typical card → the centrepiece does
  not work; fall back to analytics-only.
- **Phase 2 not running reliably after four consecutive cards** → the
  self-scoring loop is the product; if it cannot stay up, stop.
- **Calibration materially off after ~150 scored fights** (predicted 60% landing
  near 45%) → the model is not honest enough to publish; fix or withdraw the
  probability and keep the descriptive pages.
- **The weekly job needing manual intervention more than once a month** →
  maintenance cost exceeds the value.

Failing any of these is not failure of the project. The analytics platform
stands on its own, and the snapshot dataset and the matchmaking finding are both
independently worth having.
