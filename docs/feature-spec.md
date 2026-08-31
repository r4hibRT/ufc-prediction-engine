# Feature specification

Candidate features for the fight-outcome model, the rules every feature must
obey, and the protocol for deciding which ones survive.

This is a spec to review and argue with, not a description of what exists.
Nothing here is built yet.

---

## 1. Why this document

The current model uses 12 features and scores Brier 0.2250 / 63.7% accuracy on
912 held-out fights. Diagnostics say the model class is not the constraint:

- Logistic regression **underfits** — test accuracy (0.6371) is *higher* than
  train accuracy (0.5963).
- LightGBM and RandomForest **overfit** (+3.6pp and +5pp train-test gaps) and
  still lose on Brier.
- Ensembling the three gains +0.9pp accuracy but *worsens* Brier, because the
  base models correlate 0.86–0.93.

That combination means the features are the binding constraint. Two specific
gaps motivate the list below:

1. **`bout_stats` is completely unused.** 17,624 rows of strikes, takedowns,
   knockdowns, control time and submission attempts never reach the model.
2. **`rating_diff` is weak and getting weaker.** Picking the higher-rated
   fighter is right 61.9% of the time in 1994–2004 but only 54.9% in 2018–2024.

---

## 2. Rules every feature must obey

### 2.1 Point-in-time

A feature may only use information available **strictly before** the bout it
describes. `build_raw_features()` already enforces this structurally: it walks
bouts in date order, emits the row from `state` as it stands, and only then
updates `state`. Every new feature must be computed inside that existing loop,
before the state update.

The specific trap: `state["last_rating"]` holds the rating *after* a fighter's
previous bout, which is correctly their *pre*-bout rating for the next one.
Reading `rating_lookup[(fighter, this_bout)]` instead would leak the result of
the fight being predicted.

### 2.2 Mirroring

Every bout is emitted twice, once from each fighter's perspective, so the model
cannot learn anything from which fighter the scraper happened to list first.
Each feature declares one of three behaviours on the mirrored row:

| Rule | Meaning | Example |
|---|---|---|
| **NEG** | Value negates | `rating_diff` → `-rating_diff` |
| **SWAP** | The a/b pair exchanges values | `age_a`, `age_b` |
| **SAME** | Value is unchanged | `rd_sum`, `is_title_fight` |

Getting this wrong is silent — no error, just a corrupted dataset. Every
feature needs an assertion that its mirror behaves as declared.

**Consequence worth understanding:** a SAME feature is mathematically forced to
a zero coefficient in a linear model on a mirrored dataset. That is why
`rd_sum`, `is_title_fight` and `same_stance` all report `+0.0000` today. SAME
features can only contribute through interactions, which is a direct argument
for tree models.

### 2.3 Missing values

Prefer an explicit indicator over silent imputation. A fighter with no
takedowns attempted has *undefined* takedown accuracy, not 0% — the current
`.replace(0, 1)` idiom in the deleted clustering code produced exactly this bug
(fighters who never faced a takedown attempt got 100% takedown defence).

---

## 3. Evaluation protocol

### 3.1 Three-way split

| Split | Period | Purpose |
|---|---|---|
| **Train** | 1994 → 2022-12-31 | Fit model parameters |
| **Validation** | 2023-01-01 → 2024-06-28 | Feature selection, hyperparameters, calibration |
| **Test** | 2024-06-29 → present | Touched **once**, at the very end |

The test set is the existing 912-fight holdout. It must not be looked at during
selection. Every subset comparison, every hyperparameter, every calibration
decision happens on validation.

### 3.2 Walk-forward cross-validation

A single validation block gives one noisy estimate. For selection, prefer
expanding-window folds within train+validation:

| Fold | Fit on | Validate on |
|---|---|---|
| 1 | ≤ 2018 | 2019 |
| 2 | ≤ 2019 | 2020 |
| 3 | ≤ 2020 | 2021 |
| 4 | ≤ 2021 | 2022 |
| 5 | ≤ 2022 | 2023 → 2024-06 |

A feature earns its place by helping **across folds**, not by winning one.
Never use random K-fold: it lets the model fit later fights and validate on
earlier ones.

### 3.3 The noise floor — read this before comparing anything

On 912 fights the standard error of Brier is roughly **±0.01**. Every model
measured so far — 0.2250, 0.2252, 0.2258, 0.2272, 0.2284 — falls inside one
standard error of the others. They are statistically indistinguishable.

**Bootstrap the confidence interval before declaring any winner.** Resample
fights (not rows — mirrored rows are not independent) 1,000 times and report
the interval. A feature that moves Brier by 0.003 has not been shown to do
anything.

### 3.4 Search strategy

Exhaustive subset search over ~40 features is 2⁴⁰ combinations; the winner of
that many comparisons is overfit by construction. Instead:

1. **Block-level first.** Test the nine blocks below as units — drop each block,
   retrain, measure the change. Nine comparisons, not forty, and the result is
   interpretable.
2. **Elastic-net regularisation** to prune continuously within surviving blocks
   rather than making discrete keep/drop calls.
3. **Drop-column importance at block level**, not permutation importance —
   permutation importance is misleading when features are correlated, and these
   will be.
4. **Stability over rank.** Judge a feature by whether it survives across folds.

---

## 4. Candidate features

Source key: `B` = bouts, `F` = fighters, `R` = ratings, `S` = bout_stats,
`D` = derived in the replay loop.

### Block A — Skill beyond current rating

Hypothesis: a single current rating hides trajectory. A faded former champion
and a rising contender can share a rating while being very different bets.

| Feature | Definition | Src | Mirror |
|---|---|---|---|
| `rating_diff` | Pre-bout rating difference *(exists)* | R | NEG |
| `rd_sum` | Sum of both RDs — joint uncertainty *(exists)* | R | SAME |
| `peak_rating_diff` | Career-max rating to date, differenced | R/D | NEG |
| `rating_momentum_diff` | Rating change over last 3 bouts, differenced | R/D | NEG |
| `time_since_peak_diff` | Days since each fighter's peak rating, differenced | R/D | NEG |
| `volatility_diff` | Glicko-2 volatility, differenced | R | NEG |

### Block B — Form and momentum

Hypothesis: recent results carry information the rating smooths away.

| Feature | Definition | Src | Mirror |
|---|---|---|---|
| `win_streak_diff` | Consecutive wins entering the bout, differenced | D | NEG |
| `recent_form_3_diff` | Win rate over last 3 bouts, differenced | D | NEG |
| `recent_form_5_diff` | Win rate over last 5 bouts, differenced | D | NEG |
| `last_was_ko_loss_a/b` | Previous bout was a KO/TKO loss | D | SWAP |
| `losing_streak_diff` | Consecutive losses entering the bout | D | NEG |

### Block C — Durability and mileage

Hypothesis: accumulated damage predicts decline better than age alone. This
block needs `bout_stats` and is currently entirely absent from the model.

| Feature | Definition | Src | Mirror |
|---|---|---|---|
| `ko_loss_rate_diff` | KO/TKO losses ÷ bouts so far, differenced | D | NEG |
| `sub_loss_rate_diff` | Submission losses ÷ bouts so far, differenced | D | NEG |
| `finish_rate_diff` | (KO + sub wins) ÷ bouts so far, differenced | D | NEG |
| `career_rounds_diff` | Total rounds fought to date, differenced | B/D | NEG |
| `strikes_absorbed_total_diff` | Career significant strikes absorbed, differenced | S/D | NEG |
| `knockdowns_absorbed_rate_diff` | Knockdowns absorbed per 15 min, differenced | S/D | NEG |

### Block D — Activity and layoff

Hypothesis: ring rust and over-activity are both informative, and non-linear.

| Feature | Definition | Src | Mirror |
|---|---|---|---|
| `layoff_diff` | Days since last bout, differenced *(exists)* | D | NEG |
| `layoff_a/b` | Raw days since last bout, per fighter | D | SWAP |
| `fights_last_1yr_diff` | Bouts in trailing 365 days, differenced | D | NEG |
| `fights_last_2yr_diff` | Bouts in trailing 730 days, differenced | D | NEG |

### Block E — Strength of schedule

Hypothesis: two fighters with identical records differ if one fought harder
opposition. The rating partly captures this; these make it explicit.

| Feature | Definition | Src | Mirror |
|---|---|---|---|
| `avg_opponent_rating_diff` | Mean of opponents' **pre-bout** ratings, differenced | R/D | NEG |
| `max_opponent_rating_diff` | Highest opponent rating faced, differenced | R/D | NEG |
| `elite_share_diff` | Share of bouts vs top-quartile opponents, differenced | R/D | NEG |

> **Leak warning.** `avg_opponent_rating_diff` must use the opponent's rating
> *before* the shared bout. `state["last_rating"]` at emit time is correct;
> `rating_lookup[(opponent, this_bout)]` is the post-bout value and would leak.

### Block F — Physical

Hypothesis: age is non-monotonic and reach matters in absolute terms. The raw
per-fighter values exist specifically so a tree can learn the age curve.

| Feature | Definition | Src | Mirror |
|---|---|---|---|
| `reach_diff` | Reach difference *(exists)* | F | NEG |
| `height_diff` | Height difference *(exists)* | F | NEG |
| `age_diff` | Age difference *(exists)* | F | NEG |
| `age_a`, `age_b` | Raw ages at bout date | F | SWAP |
| `abs_reach_diff` | Magnitude of reach mismatch | F | SAME |
| `age_from_peak_a/b` | Absolute distance from ~29 years | F | SWAP |
| `southpaw_vs_orthodox` | Explicit stance clash, not just `same_stance` | F | NEG |

### Block G — Performance rates, point-in-time

Hypothesis: **the single largest untapped source.** These are the statistics
the archetype clustering used, computed as career-to-date rolling rates instead
of career-long aggregates — the same signal without the leak that killed it.

| Feature | Definition | Src | Mirror |
|---|---|---|---|
| `sig_strikes_pm_diff` | Significant strikes landed per minute, differenced | S/D | NEG |
| `strikes_absorbed_pm_diff` | Significant strikes absorbed per minute, differenced | S/D | NEG |
| `striking_differential_diff` | (landed − absorbed) per minute, differenced | S/D | NEG |
| `td_accuracy_diff` | Takedowns landed ÷ attempted, differenced | S/D | NEG |
| `td_defence_diff` | 1 − (opp TD landed ÷ opp TD attempted), differenced | S/D | NEG |
| `td_rate_diff` | Takedowns landed per 15 min, differenced | S/D | NEG |
| `control_time_pm_diff` | Control seconds per minute, differenced | S/D | NEG |
| `sub_attempt_rate_diff` | Submission attempts per 15 min, differenced | S/D | NEG |
| `knockdown_rate_diff` | Knockdowns landed per 15 min, differenced | S/D | NEG |

### Block H — Stylistic mismatch

Hypothesis: what `style_matchup_prob` was reaching for, expressed as a direct
interaction of point-in-time rates rather than a leaky cluster matrix.

| Feature | Definition | Src | Mirror |
|---|---|---|---|
| `grappler_vs_defence` | A's takedown rate × (1 − B's takedown defence) | S/D | NEG |
| `volume_vs_absorption` | A's strikes/min × B's strikes-absorbed/min | S/D | NEG |
| `finisher_vs_durability` | A's knockdown rate × B's KO-loss rate | S/D | NEG |

### Block I — Context

Hypothesis: championship and division context changes fight dynamics.

| Feature | Definition | Src | Mirror |
|---|---|---|---|
| `is_title_fight` | Championship bout *(exists)* | B | SAME |
| `is_defence` | Reigning champion defending *(newly derived)* | B | SAME |
| `division_experience_a/b` | Has fought in this division before *(exists)* | D | SWAP |
| `five_round_experience_interaction` | Title × distance-going difference *(exists)* | D | NEG |
| `division_debut_days_diff` | Days since first bout in this division | D | NEG |

> **Provisional.** `is_title_fight` matches any bout title containing "title",
> which includes *The Ultimate Fighter* tournament finals (bug B22). That
> corrupts this block and, via the 1.1 rating bonus, `rating_diff` itself.
> Roughly 40–60 bouts of 470 title fights. Treat Block I results as unreliable
> until B22 is fixed; re-running selection afterwards costs only compute.

---

## 5. Model candidates

Run both through the identical protocol. Do not pre-commit.

**Elastic-net logistic regression — the baseline to beat.** Currently the best
performer and the only one not overfitting. Naturally calibrated. Structurally
blind to SAME features and to non-monotonic effects like the age curve.

**LightGBM — expected to overtake once Blocks F and G land.** Can use SAME
features through interactions and can learn the age curve from raw ages. Must
stay heavily regularised: the effective sample is ~5,157 *fights*, not 10,314
rows, which is a modest-data regime. Start from small `num_leaves` and high
`min_data_in_leaf`, tune on validation only.

**Calibration.** Tree models produce poorly calibrated probabilities by
default. If boosting wins, fit isotonic or Platt calibration on the validation
fold before touching test. Calibrated probabilities are the entire point — they
are what makes a comparison against betting odds meaningful.

**Ensembling — deferred, and only if built for decorrelation.** Averaging the
current three models gains +0.9pp accuracy and *loses* Brier, because they
correlate 0.86–0.93 on shared features. If revisited, partition features by
block so base models see genuinely different information, then stack.

---

## 6. Open questions

**B13 — should debut bouts be included?** Currently every fighter's first bout
is dropped, because the loop requires both fighters to have a prior rating.
Seeding debutants at 1500/`INITIAL_RD` is defensible, but leaves `layoff` and
all Block B/C/G features undefined for them, requiring either imputation or
explicit debut indicators. Decide empirically: measure with and without.

**Should pre-2012 data be used at all?** Training only on 2012+ scores Brier
0.2243 vs 0.2250 on everything — the same or slightly better with 18 fewer
years. Test era-weighting or a cutoff as an explicit experiment.

**Does the era adjustment hurt the ratings?** `rating_diff` accuracy decays from
0.619 (1994–2004) to 0.549 (2018–2024). An adjustment that only ever compresses
results (bug B4) is a plausible cause. Belongs to the rating grid-search step,
but it is a live hypothesis, not a closed question.

---

## 7. Order of work

1. Implement Blocks A–I inside the existing chronological loop
2. Assert every mirroring rule holds
3. Assert no feature reads data at or after its own bout date
4. Establish the three-way split and walk-forward folds
5. Bootstrap the Brier confidence interval so "better" is defined
6. Block-level selection, then within-block pruning
7. Compare elastic-net LR against regularised LightGBM on validation
8. Calibrate the winner
9. **One** evaluation on test
10. Fix B22, re-run selection, confirm Block I
11. Benchmark against de-vigged closing odds
