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
- [ ] Run `backfill_training_data` after each month of 2026 season
- [ ] Retrain on 2025 + 2026 combined data — expect meaningful AUC gains
- [ ] Add management command to retrain on a schedule
- [ ] Track model accuracy per week in the DB (predicted vs actual hit rate)

### Feature engineering ideas
- Rolling 7/14/30-day batting averages (currently only 7-day)
- Pitcher vs. batter handedness splits (career and recent)
- Days of rest since last game
- Ballpark-specific batter performance
- Weather (temperature, wind)
- Time of day (day/night split)
