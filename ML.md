# Machine Learning

Building an ML model to replace (or augment) the hand-crafted scoring formula.

---

## Table of Contents

1. [Goal](#goal)
2. [Current State](#current-state)
3. [Step 1 — Backfill Training Data](#step-1--backfill-training-data)
4. [Step 2 — Train First Model](#step-2--train-first-model)
5. [Features](#features)
6. [Roadmap](#roadmap)

---

## Goal

Replace the hand-crafted `P(Hit)` formula in `predictor/logic.py` with a trained classifier that learns the relationship between player/matchup features and whether a player actually got a hit that day.

The target variable is `got_hit` (True/False) stored in the `Prediction` model.

---

## Current State

The `Prediction` table stores one row per player per day with outcome tracking. As of April 2026:

```bash
python manage.py shell -c "
from predictor.models import Prediction
total = Prediction.objects.exclude(got_hit=None).count()
hits  = Prediction.objects.filter(got_hit=True).count()
print(f'{total} rows with outcomes ({hits} hits, {total-hits} outs)')
print(f'Across {Prediction.objects.exclude(got_hit=None).values(\"date\").distinct().count()} dates')
"
```

Training requires ~500+ rows with outcomes. If you're below that, run the backfill first.

---

## Step 1 — Backfill Training Data

`backfill_training_data` generates predictions for historical dates and populates `got_hit` from Statcast results. It's a one-time operation — expect it to take several hours for a full season.

```bash
# Full 2025 regular season (March 27 – September 28) — run overnight
python manage.py backfill_training_data

# Specific range — use this to test it works first
python manage.py backfill_training_data --start 2025-07-01 --end 2025-07-31

# Already have predictions for some dates but got_hit is missing?
python manage.py backfill_training_data --results-only

# Regenerate predictions for dates that already exist
python manage.py backfill_training_data --force
```

Progress is printed per date. Summary at the end shows total rows generated and any errors.

---

## Step 2 — Train First Model

Open `notebooks/train_model.ipynb` in PyCharm or Jupyter after the backfill.

The notebook:
1. Loads all `Prediction` rows with outcomes from the Django DB
2. Parses `score_breakdown` to extract raw numeric features
3. Encodes categoricals (batter hand, pitcher hand, platoon matchup)
4. Splits train/test **by date** (not random — this is time series data)
5. Trains logistic regression and random forest
6. Plots ROC curves comparing both models vs the existing hand-crafted score
7. Shows feature importances
8. Saves the winning model to `data/models/`

```bash
# Run from project root
jupyter notebook notebooks/train_model.ipynb
```

---

## Features

### From DB fields (no parsing needed)

| Feature | Field | Notes |
|---|---|---|
| Hand-crafted P(Hit) | `score` | Baseline — the model should beat this |
| Pitcher ERA | `pitcher_era` | Season ERA |
| Pitcher WHIP | `pitcher_whip` | Season WHIP |
| Pitcher hand | `pitcher_hand` | R/L |
| Home/away | `is_home` | |
| Batting order | `avg_batting_order` | 7-day average |
| Park factor | `park_factor` | 100 = league average |
| Team rank | `team_rank` | 1–30 offensive rank |
| Hit streak | `hit_streak` | Active consecutive-game streak |
| Batter hand | `bats` | L/R/S |

### Parsed from `score_breakdown` string

| Feature | Breakdown key | Example |
|---|---|---|
| Batting average | `BA: 0.285` | 7-day rolling |
| Expected BA | `xBA: 0.310` | Statcast xBA |
| Hard hit rate | `HH%: 42%` | % balls hit ≥ 95 mph |
| Strikeout rate | `K%: 15.2%` | 7-day K% |
| Walk rate | `BB%: 8.1%` | 7-day BB% |
| Sprint speed | `Speed: 28.3 ft/s` | Statcast sprint speed |
| Recent P ERA | `P.ERA: 3.45` | Pitcher last ~4 starts |
| Recent P WHIP | `P.WHIP: 1.12` | Pitcher last ~4 starts |

### Engineered

| Feature | Derivation |
|---|---|
| `platoon` | 1 if cross-handed matchup (LHB vs RHP or RHB vs LHP) |
| `is_a_list` | 1 if A-list (cross-handed advantage confirmed) |

---

## Model Results Log

Each run logged here after training. Compare AUCs to track improvement over time.

---

### v6 — Apr 26 2026 (full model comparison)

**Data:** 2,281 rows, 193 dates (2025 full season + Apr 2026)
**Train:** 1,824 rows | **Test:** 457 rows

| Model | AUC | Delta vs baseline | Notes |
|---|---|---|---|
| Hand-crafted score (baseline) | 0.4692 | — | |
| XGBoost tuned | 0.4925 | +0.023 | Overfit badly — bottom of the pack |
| Stacking (LR+LGBM+CatBoost → LR meta) | 0.5105 | +0.041 | Meta-learner found no useful signal |
| LightGBM default | 0.5228 | +0.054 | |
| Logistic Regression | 0.5260 | +0.057 | |
| CatBoost (tuned, 30 configs) | 0.5313 | +0.062 | |
| Random Forest | 0.5323 | +0.063 | |
| Blend equal (LGBM+CB+LR) | 0.5497 | +0.081 | |
| Blend weighted (0.5 LGBM + 0.3 CB + 0.2 LR) | 0.5514 | +0.082 | |
| **LightGBM tuned** | **0.5515** | **+0.082** | ✓ saved — best deployable |

**Key findings:**
- Tuned LightGBM is the best single deployable model
- Weighted blend is virtually tied (0.5514) — not worth the added complexity
- XGBoost overfits badly on this dataset — avoid
- Stacking hurt — LR/LGBM/CatBoost outputs are too correlated for a meta-learner to exploit
- We've hit the ceiling for ML technique improvements on current features

**Next lever: better features** — `p_whip` and `p_k9` are 100% null in training data. Adding real pitcher peripherals should unlock meaningful AUC gains.

---

### v5 — Apr 26 2026 (2026 data added + retrain)

**Data:** 2,281 rows, 193 dates (2025 full season + Apr 2026)
**Train:** ~1,824 rows | **Test:** ~457 rows

| Model | AUC | Notes |
|---|---|---|
| Hand-crafted score (baseline) | 0.4692 | |
| LightGBM default | 0.5228 | |
| **LightGBM tuned** | **0.5515** ✓ saved | +0.029 over default — tuning helps with more data |

**Best params:** `n_estimators=334, num_leaves=22, max_depth=5, learning_rate=0.079, subsample=0.637, colsample_bytree=0.607, min_child_samples=17, reg_alpha=0.329, reg_lambda=0.284`

**Key finding:** Hyperparameter tuning now helps (+0.029) vs last run where it hurt (-0.073). More data (2,281 vs 2,132 rows) gave the search enough signal to generalize.

---

### v4 — Apr 14 2026 (hyperparameter tuning)

**Data:** 2,132 rows, 181 dates (same as v3)
**Train:** Apr 1 – Aug 29 | **Test:** Aug 29 – Sep 28

| Model | CV AUC | Test AUC | Notes |
|---|---|---|---|
| LightGBM default | — | 0.5627 | Retained as best |
| LightGBM tuned (RandomizedSearch 60 iters, TimeSeriesSplit 5-fold) | 0.5127 | 0.4893 | Worse — overfit |

**Best tuned params:** `n_estimators=395, num_leaves=77, learning_rate=0.117, subsample=0.951, colsample_bytree=0.609, min_child_samples=16, reg_alpha=0.348, reg_lambda=0.314`

**Conclusion:** Dataset too small (1,705 train rows) for hyperparameter search to reliably improve generalization. Default LightGBM retained. Retrain when 2026 season data accumulates.

---

### v3 — Apr 13 2026

**Data:** 2,132 rows, 181 dates (same as v2)
**Train:** Apr 1 – Aug 29 | **Test:** Aug 29 – Sep 28

| Model | AUC |
|---|---|
| Hand-crafted score (baseline) | 0.4386 |
| Logistic Regression | 0.5363 |
| Random Forest | 0.5347 |
| **LightGBM** | **0.5627** ✓ saved |

**Notes:** Added LightGBM (XGBoost had arm64/libomp issues on Mac). LightGBM beat all previous models.
Best improvement yet — AUC 0.5363 → 0.5627 (+0.026) over LR with same data.

---

### v2 — Apr 12 2026

**Data:** 2,132 rows, 181 dates (2025 Apr 1 – Sep 28, re-backfilled with BRef fallback)
**Train:** Apr 1 – Aug 30 | **Test:** Aug 30 – Sep 28
**Hit rate:** 64.6%

| Model | AUC |
|---|---|
| Hand-crafted score (baseline) | 0.4386 |
| **Logistic Regression** | **0.5363** ✓ saved |
| Random Forest | < LR |

**Notes:** Re-backfill with BRef fallback fixed pitcher stats. LR beat RF this time and was saved.
AUC improved from 0.5169 → 0.5363 (+0.019) just from cleaner data.

---

### v1 — Apr 12 2026

**Data:** 2,093 rows, 178 dates (2025 Apr 1 – Sep 28)
**Train:** Apr 1 – Aug 30 | **Test:** Aug 30 – Sep 28
**Hit rate:** 64.5%

| Model | AUC |
|---|---|
| Hand-crafted score (baseline) | 0.4492 |
| Logistic Regression | 0.5122 |
| **Random Forest** | **0.5169** ✓ saved |

**Known issues with this run:**
- FanGraphs was returning 403 during backfill → `pitcher_whip`, `p_whip`, `p_k9` were null/empty
- BRef fallback added to `logic.py` after the fact
- Re-backfill with `--force` + retrain will be v2

**Features used:** `score`, `ba`, `xba`, `hh_pct`, `k_pct`, `bb_pct`, `speed`, `is_home`,
`park_factor`, `avg_batting_order`, `hit_streak`, `pitcher_era`, `p_era`, `team_rank`,
`bats_L`, `bats_S`, `pitch_R`, `pitch_L`, `platoon`, `is_a_list`

**Features dropped (all null):** `p_whip`, `p_k9`

---

## Roadmap

### Week 1–2: Backfill + first model ✓
- [x] `backfill_training_data` management command
- [x] `notebooks/train_model.ipynb` with logistic regression + random forest
- [x] Run backfill for full 2025 season
- [x] Train and evaluate first models (v1, v2, v3)
- [x] Re-backfill with BRef fallback fix
- [x] LightGBM (AUC 0.5627, best result)

### Week 3–4: Tuning ✓ (no gain on small dataset)
- [x] `notebooks/tune_lgbm.py` — RandomizedSearchCV × 60 iters with TimeSeriesSplit
- [x] Wired LightGBM into `logic.py` — live ML scoring in production
- Result: tuning hurt generalization (CV 0.5127 → test 0.4893). Default params retained.
- Root cause: only 1,705 training rows / 181 dates — too small for hyperparameter search to help reliably.
- **Fix:** accumulate 2026 season data, retrain in June/July.

### Week 5+: Accumulate data + retrain
- [x] Run `backfill_training_data` for Apr 2026 — 2,281 rows total
- [x] Retrain on 2025 + 2026 combined — tuned AUC 0.5515, tuning now helps
- [ ] Repeat monthly as season progresses (May, June, ...)
- [ ] Add `p_whip` / `p_k9` features via MLB Stats API (currently 100% null)
- [ ] Batter vs pitcher handedness career splits (richer than binary platoon flag)
- [ ] Rolling 14/30-day BA alongside 7-day

### Feature engineering ideas
- Rolling 7/14/30-day batting averages (currently only 7-day)
- Pitcher vs. batter handedness splits (career and recent)
- Days of rest since last game
- Ballpark-specific batter performance
- Weather (temperature, wind)
- Time of day (day/night split)
