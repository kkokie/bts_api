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
| Backfill full 2025 season (Apr 1 – Sep 28) | **Done — 2,093 rows, 1,350 hits / 743 outs** |
| Train and evaluate first models | Next |
| Document feature importances | Pending |
| XGBoost / LightGBM tuning | Pending |
| Wire model into `logic.py` | Pending |

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
