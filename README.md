# Beat the Streak AI

A Django app that analyzes MLB player statistics and produces daily hit predictions for ESPN's Beat the Streak contest.

---

## Docs

| Doc | What's in it |
|---|---|
| [LOGIC.md](LOGIC.md) | How predictions work — data sources, scoring formula, candidate filters, known quirks |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Docker setup, local dev workflow, production deploy on Coolify + Hetzner |
| [ML.md](ML.md) | Machine learning — backfill training data, first model, feature list, roadmap |

---

## ML Status

| Step | Status |
|---|---|
| `backfill_training_data` command | Done |
| `notebooks/train_model.ipynb` | Done |
| Backfill full 2025 season (Apr 1 – Sep 28) | Done — 2,093 rows, 1,350 hits / 743 outs |
| Fix FanGraphs 403 → BRef fallback in `logic.py` | Done |
| Train and evaluate first models (v1) | Done — see results below |
| Re-backfill with BRef fix (`--force`) | Done — 2,132 rows |
| Re-train models on clean data (v2) | Done — LR 0.5363, RF < LR |
| XGBoost / LightGBM tuning | Pending |
| Wire model into `logic.py` | Pending |

### First model results (v1 — Apr 12 2026, incomplete pitcher data)

Training data: 2,093 rows across 178 dates (2025 regular season)
Train: Apr 1 – Aug 30 | Test: Aug 30 – Sep 28

| Model | AUC | Notes |
|---|---|---|
| Hand-crafted score (baseline) | 0.4492 | Below random — score range too compressed (67–99) |
| Logistic Regression | 0.5122 | +0.063 vs baseline |
| Random Forest | **0.5169** | +0.068 vs baseline — saved to `data/models/` |

**Why AUC is low:** FanGraphs was returning 403 errors during backfill, so `pitcher_era`,
`pitcher_whip`, `p_whip`, and `p_k9` were mostly/entirely null in v1 training data.
BRef fallback was added to `logic.py` — re-backfill with `--force` will fix this.

**Features used (v1):** `score`, `ba`, `xba`, `hh_pct`, `k_pct`, `bb_pct`, `speed`,
`is_home`, `park_factor`, `avg_batting_order`, `hit_streak`, `pitcher_era`, `p_era`,
`team_rank`, `bats_L`, `bats_S`, `pitch_R`, `pitch_L`, `platoon`, `is_a_list`

**Features dropped (all null):** `p_whip`, `p_k9`

---

## Quick Start

```bash
# First time
docker compose up --build
docker compose run web python manage.py migrate

# Normal startup
docker compose up
```

App runs at `http://localhost:8000`.

---

## Stack

| Component | Technology |
|---|---|
| Web framework | Django 5.2 |
| Database | PostgreSQL |
| Baseball data | pybaseball, MLB Stats API, Statcast |
| ML | scikit-learn (logistic regression, random forest) |
| Frontend | Bootstrap 5 |
| Production | Gunicorn + WhiteNoise on Hetzner via Coolify |

---

## Project Structure

```
beat_the_streak_2026/
├── config/                  # Django settings (base / dev / prod)
├── predictor/
│   ├── logic.py             # Prediction engine (~1300 lines)
│   ├── models.py            # Prediction model
│   ├── views.py             # Dashboard + scoring guide views
│   ├── templates/           # dashboard.html, scoring_guide.html
│   └── management/commands/
│       ├── generate_predictions.py   # Pre-generate predictions for a date
│       └── backfill_training_data.py # Build ML training dataset
├── notebooks/
│   └── train_model.ipynb    # First ML model (logistic regression + random forest)
├── data/
│   └── models/              # Saved trained models (joblib)
├── Dockerfile
├── docker-compose.yml       # Local dev
└── docker-compose.prod.yml  # Production (Coolify)
```
