# Prediction engine — scope, plan and resume guide

The single source of truth for building the prediction engine. If a session ends
mid-build, a new one should read this file first, then `git log` on this branch,
then continue from the first unticked step.

---

## 1. Scope (final)

**One engine, not a model comparison.** A strong, reliable, explainable
probability for every UFC bout, used as the backend for the frontend.

> logit P(A beats B) = β₀ · logit(Glicko expected score) + Σ βᵢ · (Aᵢ − Bᵢ)

- **Base:** Glicko-2 expected score. The rating measures skill only.
- **Corrections:** a small set of point-in-time difference features for context
  the rating cannot see (age, layoff, reach, experience, form, schedule, style).
- **Model:** L2-regularised logistic regression, **no intercept**, difference
  features only, so P(A) + P(B) = 1 exactly by construction.
- **β₀ is the calibration dial:** if the rating is overconfident, β₀ < 1.
- Explanations come straight from the model: each fight decomposes into the
  rating's probability plus named corrections.

**Out of scope:** XGBoost/neural-network comparisons, betting odds as a feature
(odds are only ever a benchmark), external features that need new scraping.

---

## 2. Design decisions (settled — do not reopen without cause)

| Decision | Choice |
|---|---|
| Target | Binary win/loss. Draws and no-contests excluded from training |
| Training loss | Log loss |
| Selection metric | Walk-forward log loss. Brier, calibration, AUC reported; accuracy never used to select |
| Rating updates | Standard Glicko-2 equations, updated per bout; draw = 0.5; no-contest = void |
| Rating adjustments | Title weight, upset discount, rating cap, era factor **removed** from the rating. Context belongs in the corrections layer |
| Rating constants | Initial RD, τ, debut rating chosen by a small grid on downstream walk-forward log loss. Decay constant c stays 0 (already swept) |
| Features | Point-in-time only, as A − B differences; noisy per-minute rates shrunk toward the division mean by cage time |
| Debuts | Included; shrinkage gives debutants the division prior |
| Feature gate | A feature stays only if it improves walk-forward log loss beyond the noise floor **and** keeps the same coefficient sign in most folds |
| Holdout | Prospective: fights after the model is frozen are the real test |
| Results language | Provisional until frozen. No "findings" before the model is final |

---

## 3. Step-by-step execution

Tick each box as it is committed. One step = one commit on this branch.

- [x] **0. Setup** — worktree `E:\Projects\ufc-engine` on `epic/prediction-engine`,
  synced with main, this plan written.
- [ ] **1. Clean slate** — delete the old duplicate pipeline `src/simulation/`
  and `docs/feature-spec.md`. The engine builds on `src/analytics/`, never on a
  second replay.
- [ ] **2. Evaluation harness** — `src/engine/harness.py`: expanding-window folds
  by year (validate 2019 … 2025), log loss / Brier / AUC / calibration, bootstrap
  intervals resampled by fight, baselines (50/50 and Glicko-only). Fixed before
  any model is fitted.
- [ ] **3. Rating layer** — `src/engine/rating.py`: parameterised Glicko-2 replay
  computed **in memory** (never writes the shared `ratings` table), adjustments
  removed. Grid over initial RD × τ × debut rating judged by the harness.
- [ ] **4. Feature builder** — `src/engine/features.py`: per-bout difference rows
  from pre-bout state (extend the analytics replay), shrinkage, debuts included,
  plus a truncation test proving no lookahead.
- [ ] **5. Model** — `src/engine/model.py`: no-intercept logistic regression;
  forward block selection through the feature gate; calibration check; freeze
  the configuration.
- [ ] **6. Artifact** — persist fitted model + scaler + feature list + rating
  constants + metrics sidecar to `models/` (versioned by date).
- [ ] **7. Inference and record** — current-state features for upcoming bouts,
  a `predictions` table written **before** each card and scored after, both
  wired into the weekly refresh.
- [ ] **8. Adopt tuned constants** — update `src/ratings/` so the site's ratings
  are the engine's ratings; merge up to main and `epic/interface`.
- [ ] **9. API** — `/api/predictions` for the frontend.

Rough effort: steps 1–3 a day, 4–5 two days, 6–9 two days, part-time.

---

## 4. Environment and gotchas

- **Two worktrees.** `E:\Projects\UFC-GOAT-Simulator` is on `epic/interface`:
  it runs the site (port 8420) and the Sunday 12:00 scheduled refresh. **Do not
  develop there.** `E:\Projects\ufc-engine` is on `epic/prediction-engine`: engine
  work only.
- **Shared database.** Both worktrees hit the same PostgreSQL. The engine must
  not write `ratings`, `bout_snapshots` or `bouts` until step 8.
- **Python:** `C:\Users\Hp\anaconda3\python.exe` only (the default Python lacks
  numpy/pandas). Run with `PYTHONPATH` set to the worktree root.
- `.env` is gitignored and must be copied into a new worktree by hand.
- **Ports:** API 8420, Vite 5180. Never 5173 or 8000 (another project).
- **Bash heredocs halve backslashes.** Use the Write/Edit tools for any code
  containing `\n` or other escapes.
- **Stopping servers:** kill by listening port; `pkill` pattern matching was
  unreliable and once left a stale server running.
- **Checkpoint** of scraped events lives in the `events` table (not a file).
- **Health** of the pipeline: `python -m src.automation.health`.

## 5. Working conventions (the user's standing rules)

- Walk through a plan before building anything new.
- Commits only with explicit instruction. For this build, one commit per
  completed step is authorised.
- Commit messages: single line, Conventional Commits (`feat:`, `fix:`,
  `chore:`, `docs:`), **no** co-author trailer.
- Code comments at most two lines; only top-of-file docstrings may be longer.
- Results from an unfinalised model are provisional, never "findings".
