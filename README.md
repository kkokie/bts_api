# Beat the Streak AI

A Django web application that analyzes MLB player statistics and produces daily hit predictions for use in ESPN's Beat the Streak contest. The app pulls data from multiple baseball data sources, scores each candidate using a composite formula, and presents results through a dashboard with color-coded factor badges.

---

## Table of Contents

1. [Tech Stack](#tech-stack)
2. [Project Structure](#project-structure)
3. [Setup & Installation](#setup--installation)
4. [Running the App](#running-the-app)
5. [How Predictions Work](#how-predictions-work)
   - [Data Sources](#data-sources)
   - [Candidate Filters](#candidate-filters)
   - [Scoring Formula](#scoring-formula)
   - [A-List vs B-List](#a-list-vs-b-list)
   - [Batting Order Calculation](#batting-order-calculation)
   - [Pitcher Data](#pitcher-data)
   - [Hit Result Verification](#hit-result-verification)
   - [Caching Strategy](#caching-strategy)
6. [File Reference](#file-reference)
7. [Database Schema](#database-schema)
8. [Migration History](#migration-history)
9. [Dashboard UI](#dashboard-ui)
10. [Scoring Guide Page](#scoring-guide-page)
11. [CLI Command](#cli-command)
12. [Known Quirks & Notes](#known-quirks--notes)

---

## Tech Stack

| Component | Technology | Version |
|---|---|---|
| Web framework | Django | 5.2.10 |
| Database | PostgreSQL | any recent |
| Python | CPython | 3.10.0 |
| Baseball data | pybaseball | 2.2.7 |
| Data processing | pandas | 2.3.3 |
| Numerics | numpy | 2.2.6 |
| DB driver | psycopg2-binary | 2.9.11 |
| Frontend | Bootstrap | 5.3.0 (CDN) |

---

## Project Structure

```
beat_the_streak_2026/
│
├── config/                          # Django project configuration
│   ├── settings.py                  # Database, installed apps, templates config
│   ├── urls.py                      # Root URL routing (delegates to predictor/)
│   └── wsgi.py                      # WSGI entry point
│
├── predictor/                       # Main Django app
│   ├── logic.py                     # All data fetching, scoring, and prediction logic
│   ├── models.py                    # Prediction database model
│   ├── views.py                     # HTTP request handlers
│   ├── urls.py                      # App-level URL routing
│   ├── admin.py                     # Django admin registration
│   ├── apps.py                      # App config
│   │
│   ├── migrations/                  # Database schema change history
│   │   ├── 0001_initial.py
│   │   ├── 0002_alter_prediction_team.py
│   │   ├── 0003_prediction_pitcher_era_prediction_pitcher_whip.py
│   │   ├── 0004_prediction_avg_batting_order.py
│   │   ├── 0005_prediction_pitcher_hand_prediction_score_breakdown.py
│   │   └── 0006_prediction_got_hit.py
│   │
│   ├── templates/
│   │   ├── dashboard.html           # Main prediction dashboard
│   │   └── scoring_guide.html       # Scoring methodology explanation page
│   │
│   └── management/
│       └── commands/
│           └── generate_predictions.py  # CLI command for pre-generating predictions
│
├── manage.py                        # Django management entry point
├── db.sqlite3                       # Not used (PostgreSQL is the active database)
└── venv/                            # Python virtual environment
```

---

## Setup & Installation

### Prerequisites

- Python 3.10+
- PostgreSQL (via [Postgres.app](https://postgresapp.com) on macOS or equivalent)
- A virtual environment

### 1. Create and activate virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### 2. Install dependencies

```bash
pip install django psycopg2-binary pybaseball pandas numpy
```

### 3. Create the PostgreSQL database

Open `psql` and run:

```sql
CREATE DATABASE beat_the_streak;
```

### 4. Configure database credentials

In `config/settings.py`, the database block is:

```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'beat_the_streak',
        'USER': '',       # leave blank to use your macOS username (Postgres.app default)
        'PASSWORD': '',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}
```

On Postgres.app for macOS, leaving `USER` blank uses your system username automatically. Set it explicitly if needed.

### 5. Run migrations

```bash
python manage.py migrate
```

This creates the `predictor_prediction` table and all Django system tables.

---

## Running the App

```bash
source venv/bin/activate
python manage.py runserver
```

Then open `http://localhost:8000` in a browser.

The default date shown is **September 28, 2025** (the final day of the 2025 MLB season), which has full historical data available. Use the date picker to change the prediction date.

---

## How Predictions Work

### Data Sources

The app uses three external data sources via [pybaseball](https://github.com/jldbc/pybaseball):

**Baseball-Reference** (`batting_stats_range`)
- Primary source for recent batting stats (BA, K%, PA, Runs)
- Called twice: once for a 4-day window and once for a 7-day window before the prediction date
- The 7-day window is the main scoring input
- Falls back to FanGraphs full-season stats if no data is available for the range (e.g. early in a new season)

**FanGraphs** (`batting_stats`, `pitching_stats`)
- Full-season batting stats used for player metadata: batter handedness (`Bats`) and current team (`Tm`)
- Critical for players traded mid-season — Baseball-Reference date-range stats show their team as `---` or `TOT`, while FanGraphs always reflects the current team
- Full-season pitching stats used to look up opposing pitcher ERA and WHIP

**Baseball Savant / Statcast** (`statcast`, `playerid_reverse_lookup`)
- Pitch-by-pitch data used for two purposes:
  1. **Average batting order position** — computed from each batter's first plate appearance per game over the prior 7 days
  2. **Pitcher throwing hand** — extracted from the `player_name` (pitcher) and `p_throws` columns in the same statcast pull
  3. **Hit verification** — for past dates, checks whether each predicted player actually got a hit

**Baseball-Reference Schedule** (`schedule_and_record`)
- Per-team schedule with Win/Loss results used to identify the opposing pitcher for each game
- The app reads the `Win` or `Loss` column from the schedule: if the team won, the opponent's losing pitcher is extracted; if the team lost, the opponent's winning pitcher is extracted

---

### Candidate Filters

Before any scoring, players are filtered down to a viable candidate pool. Both conditions must be met:

| Filter | Threshold | Reason |
|---|---|---|
| Plate Appearances (last 7 days) | ≥ 10 PA | Eliminates injured, benched, or statistically insignificant samples |
| Strikeout Rate (last 7 days) | < 25% K% | Free-swingers are too inconsistent for a single-game hit prediction |

---

### Scoring Formula

Each candidate receives a composite score used to rank and sort the predictions:

```
Score = (BA × 100)
      + [10 if K% < 12%]
      + [10 if team is in top 10 for runs scored over last 7 days]
      + max(0, (9 − avg_lineup_pos) ÷ 8 × 10)
```

**BA × 100 (base score)**
The player's batting average over the last 7 days, multiplied by 100. A .300 hitter scores 30 points; a .350 hitter scores 35. This is the dominant component and determines the baseline ranking.

**Low K% bonus (+10)**
A flat +10 is added if the player's strikeout rate is below 12% in the same 7-day window. Elite contact hitters (think Luis Arraez-type profiles) are more reliably going to make contact than average hitters.

**Hot Team bonus (+10)**
If the player's team is in the top 10 for total runs scored in the last 7 days, a +10 bonus applies. Offenses run in streaks, and a player in a hot lineup has a better overall environment for producing hits.

**Lineup position bonus (0–10)**
A continuous bonus based on where the player typically bats in the lineup, derived from statcast data. Leadoff hitters get more plate appearances per game and therefore more opportunities to get a hit. The formula:

| Lineup Slot | Bonus |
|---|---|
| #1 (leadoff) | +10.0 |
| #2 | +8.8 |
| #3 | +7.5 |
| #4 | +6.2 |
| #5 | +5.0 |
| #6 | +3.8 |
| #7 | +2.5 |
| #8 | +1.2 |
| #9 | +0.0 |

The score is used only for **sorting** candidates. It is not displayed in the UI — instead, the dashboard shows each factor individually as color-coded badges.

---

### A-List vs B-List

After scoring, the top 20 candidates are split:

- **A-List (safe picks)** — no risk flags detected
- **B-List (risks detected)** — left-handed batter flagged as a platoon risk

The B-list is not a disqualification. A left-handed hitter facing a right-handed pitcher is actually a platoon advantage. The flag is a prompt to manually check the `[L]` or `[R]` pitcher hand shown in the matchup column before committing to the pick.

---

### Batting Order Calculation

Average batting order position is computed from Statcast data over the 7 days prior to the prediction date. The steps:

1. **Fetch statcast data** for the 7-day window. Each row is one pitch.

2. **Isolate each batter's first plate appearance** per game by filtering to `n_priorpa_thisgame_player_at_bat == 0` (zero completed prior PAs in that game). Then `.drop_duplicates(subset=['game_pk', 'batter'])` is applied — critical because every pitch within the first at-bat also satisfies `n_priorpa == 0`, and without deduplication each pitch gets a separate rank, inflating the average. After dedup: exactly one row per batter per game.

3. **Rank within each team's half of the game.** The `at_bat_number` column counts sequentially across both teams in a game, so the home team's leadoff batter might have `at_bat_number = 10` after the away team's half inning. To correct this, a `game_side` key is constructed as `game_pk + '_' + inning_topbot` (e.g. `'746123_Bot'`). Away batters always appear in "Top" innings; home batters always in "Bot." Ranking by `at_bat_number` within each `game_side` group gives the correct 1–9 lineup position.

4. **Filter to starters only.** Pinch hitters and late substitutes enter mid-game with high `at_bat_number` values. Keeping only `lineup_pos <= 9` excludes them.

5. **Average across all games** in the 7-day window per batter.

6. **Map mlbam IDs back to names** using `playerid_reverse_lookup()`. Both a regular and ASCII-normalized copy of each name are stored to handle accented characters (e.g. Vázquez → Vazquez fallback matching).

---

### Pitcher Data

For each player's matchup, the app identifies:

- **Opposing pitcher name** — extracted from the Baseball-Reference schedule's `Win`/`Loss` columns based on the game's W/L result
- **Pitcher ERA and WHIP** — looked up from FanGraphs full-season pitching stats by `(last_name, team)` key
- **Pitcher throwing hand (L/R)** — extracted from the statcast `player_name` + `p_throws` columns in the same statcast pull used for batting order. The pitcher last name is the lookup key.

All schedule lookups are batched — one HTTP request per unique team, not per player — to avoid making 20 sequential requests on first load.

---

### Hit Result Verification

For past dates (prediction date < today), the dashboard automatically verifies whether each predicted player actually got a hit:

1. Statcast data is fetched for the exact game date
2. The `events` column identifies completed at-bats. Any `single`, `double`, `triple`, or `home_run` event counts as a hit
3. `playerid_reverse_lookup()` converts batter mlbam IDs to names
4. Results are stored in the `got_hit` field on the `Prediction` model as `True`/`False`/`None`

In the dashboard, player names are colored:
- **Green** — got a hit on that date
- **Red** — played but went hitless
- **Unstyled** — future date or team had an off day

---

### Caching Strategy

All predictions are cached in PostgreSQL by date. The flow on each page load:

```
Request for date X
    │
    ├── Prediction rows exist for date X in DB?
    │       YES → serve instantly from DB (no external calls)
    │       NO  → call get_predictions(), save to DB, then serve
    │
    └── Date X is in the past AND got_hit is NULL for any row?
            YES → call get_hit_results(), bulk_update got_hit column
            NO  → skip
```

This means the expensive data fetching (multiple pybaseball HTTP calls) only happens once per date, ever. Subsequent loads for the same date are instant DB reads.

To force a re-fetch (e.g. after a logic change), clear the cache:

```bash
python manage.py shell -c "from predictor.models import Prediction; Prediction.objects.all().delete()"
```

Or for a specific date:

```bash
python manage.py shell -c "from predictor.models import Prediction; Prediction.objects.filter(date='2025-09-28').delete()"
```

---

## File Reference

### `predictor/logic.py`

The entire prediction engine. Key functions:

| Function | Purpose |
|---|---|
| `get_window_stats(days_back, reference_date)` | Fetches batting stats for the last N days. Tries Baseball-Reference first, falls back to FanGraphs full season. Standardizes column names (`Team→Tm`, `AVG→BA`) and normalizes K% from decimal to percentage if FanGraphs is the source. |
| `get_player_metadata(season)` | Fetches FanGraphs full-season batting stats for `Name`, `Tm`, `Bats` only. Used to resolve traded-player team issues and get batter handedness. |
| `get_pitcher_stats(season)` | Fetches FanGraphs full-season pitching stats. Returns a dict keyed by `(last_name, team)` with ERA, WHIP, and GS. |
| `get_matchup_info(team, date)` | Fetches Baseball-Reference schedule for one team on one date. Returns `(opponent, pitcher_name)`. |
| `get_avg_batting_order(start_date, end_date)` | Fetches statcast for the window. Returns a tuple: `(batting_order_dict, pitcher_hand_dict)`. Batting order dict maps player names to average lineup slot. Pitcher hand dict maps pitcher last names to `'L'` or `'R'`. |
| `get_hit_results(game_date)` | Fetches statcast for one specific date. Returns `{player_name: True/False}` for every batter who had a completed at-bat. |
| `clean_name_string(name)` | Handles two pybaseball encoding quirks: literal `\xhh` escape sequences and mojibake (UTF-8 bytes stored as latin-1). Preserves proper unicode (accented characters). |
| `get_predictions(simulation_date)` | Main orchestrator. Calls all data functions, scores candidates, splits into A/B lists, returns two lists of player dicts. |

**Important constants in `logic.py`:**

```python
SEASON = 2025   # Controls which season's stats are fetched from FanGraphs/BRef

FG_TO_BREF_TEAM = {
    'SFG': 'SF', 'WSN': 'WSH', 'TBR': 'TB', 'KCR': 'KC', 'SDP': 'SD',
}
# FanGraphs uses slightly different team abbreviations than Baseball-Reference.
# This mapping is applied when resolving pitcher stats and schedule lookups.
```

---

### `predictor/models.py`

Defines the `Prediction` model — one row per player per date.

| Field | Type | Description |
|---|---|---|
| `date` | DateField (indexed) | The game/prediction date |
| `list_type` | CharField | `'A'` or `'B'` |
| `name` | CharField(100) | Player full name |
| `team` | CharField(50) | Team abbreviation |
| `position` | CharField(20) | Currently unused (`'Unknown'`) — FanGraphs `Pos` column is a WAR positional adjustment float, not a position name |
| `bats` | CharField(1) | `'L'` or `'R'` |
| `score` | FloatField | Composite score (used for sorting only, not displayed) |
| `score_breakdown` | CharField(500) | Pipe-delimited factor string, e.g. `"BA: 31.2 (+2.1σ) \| K%: 9.8% (-2.3σ) \| Team Rank: #3 \| Lineup: #1.2"` |
| `opponent` | CharField(20) | Opposing team abbreviation |
| `probable_pitcher` | CharField(100) | Opposing pitcher name |
| `pitcher_era` | FloatField (nullable) | Opposing pitcher season ERA |
| `pitcher_whip` | FloatField (nullable) | Opposing pitcher season WHIP |
| `pitcher_hand` | CharField(1) | `'L'`, `'R'`, or `''` |
| `avg_batting_order` | FloatField (nullable) | Player's average lineup slot over prior 7 days |
| `notes` | CharField(200) | Risk flag description (e.g. `"Lefty Batter"`) |
| `got_hit` | BooleanField (nullable) | `True`/`False` for past dates; `None` for future or unverified |

`unique_together = ('date', 'name')` prevents duplicate rows for the same player on the same date.

`save_from_lists(date, a_list, b_list)` is a class method that deletes any existing predictions for the date and bulk-inserts all new ones in a single query.

---

### `predictor/views.py`

Two view functions:

**`dashboard(request)`** — main page at `/`
- Reads `?date=` query param (defaults to 2025-09-28)
- Checks DB cache; calls `get_predictions()` on cache miss
- For past dates, calls `get_hit_results()` if `got_hit` is still null, then bulk-updates
- Passes `a_list`, `b_list`, `selected_date`, `pretty_date` to template

**`scoring_guide(request)`** — methodology page at `/scoring/`
- Pre-computes the lineup bonus table (positions 1–9 with their bonus values)
- Passes `lineup_rows` to the scoring guide template

---

### `predictor/templates/dashboard.html`

Bootstrap 5 single-page dashboard. Key design decisions:

- **Two tables**: A-List (green header) and B-List (yellow header), both with 2 columns: Player and Matchup
- **Two-row layout per player**: main row (name, team, bats, avg order) + factor sub-row (badges spanning both columns)
- **Player name coloring**: green if `got_hit=True`, red if `got_hit=False`, unstyled if `None`
- **Team shown inline**: displayed as `Name — TEAM` on the same line, no separate column
- **Factor badges** are rendered by JavaScript at page load by parsing the `score_breakdown` string (split on ` | `) from a `data-breakdown` attribute on each sub-row div

**Badge color logic (JavaScript):**

| Badge type | Coloring rule |
|---|---|
| `BA: X (+Yσ)` | Blue ≥+2σ, Green ≥+1σ, Yellow ≥0σ, Orange ≥-1σ, Red <-1σ |
| `K%: X% (+Yσ)` | Inverted (lower K% is better): Blue ≤-2σ, Green ≤-1σ, Yellow ≤0σ, Orange ≤+1σ, Red >+1σ |
| `Team Rank: #X` | Always gray (secondary) |
| `Lineup: #X` | Always gray (secondary) |

---

### `predictor/templates/scoring_guide.html`

Standalone page at `/scoring/` explaining how the scores are built. Covers:
- Candidate filter thresholds
- Each scoring component with formula and rationale
- Lineup bonus table (all 9 positions)
- A-list vs B-list split criteria
- Data source table

---

### `predictor/management/commands/generate_predictions.py`

A Django management command for pre-generating predictions from the terminal without a browser:

```bash
# Generate for today
python manage.py generate_predictions

# Generate for a specific date
python manage.py generate_predictions --date 2025-09-28

# Overwrite existing predictions for a date
python manage.py generate_predictions --date 2025-09-28 --force
```

Useful for scripting a nightly cron job to pre-warm the cache before the day's games start.

---

## Database Schema

The active database is PostgreSQL (`beat_the_streak` database on localhost:5432). The `db.sqlite3` file in the repo root is not used.

The only custom table is `predictor_prediction`. Django also creates its own system tables for sessions, admin, auth, etc.

---

## Migration History

| Migration | Change |
|---|---|
| `0001_initial` | Created `predictor_prediction` table with core fields |
| `0002_alter_prediction_team` | Increased `team` max_length from 10 → 50 (full team names like "Los Angeles Dodgers" were being truncated) |
| `0003_prediction_pitcher_era_prediction_pitcher_whip` | Added `pitcher_era` and `pitcher_whip` float fields |
| `0004_prediction_avg_batting_order` | Added `avg_batting_order` float field |
| `0005_prediction_pitcher_hand_prediction_score_breakdown` | Added `pitcher_hand` char field and `score_breakdown` char field |
| `0006_prediction_got_hit` | Added `got_hit` nullable boolean field |

---

## Dashboard UI

```
⚾ Beat the Streak AI
Predicting hits for September 28, 2025
[How scores are calculated →]

[ Date picker: 2025-09-28 ] [ Run Prediction ]

✅ The A-List (Safe Picks)
┌─────────────────────────────────────┬────────────────────────────────────────┐
│ Player                              │ Matchup                                │
├─────────────────────────────────────┼────────────────────────────────────────┤
│ George Springer — TOR               │ vs NYY                                 │
│ Bats R • Avg Order: 1.2             │ [R] Gerrit Cole — ERA: 2.63 / WHIP:0.87│
│ [BA: 34.5 (+2.3σ)] [K%: 9.1%(-2.1σ)] [Team Rank: #4] [Lineup: #1.2]         │
├─────────────────────────────────────┼────────────────────────────────────────┤
│ ...                                 │ ...                                    │
└─────────────────────────────────────┴────────────────────────────────────────┘

⚠️ The B-List (Risks Detected)
┌─────────────────────────────────────┬────────────────────────────────────────┐
│ Player                              │ Matchup                                │
├─────────────────────────────────────┼────────────────────────────────────────┤
│ Freddie Freeman — LAD               │ vs SD                                  │
│ Bats L • Avg Order: 3.1             │ [R] Dylan Cease — ERA: 3.41 / WHIP:1.12│
│ ⚠ Lefty Batter                      │                                        │
│ [BA: 31.0 (+1.8σ)] [K%: 14.2%(+0.1σ)] [Team Rank: #7] [Lineup: #3.1]        │
└─────────────────────────────────────┴────────────────────────────────────────┘
```

---

## Scoring Guide Page

Accessible at `/scoring/` or via the "How scores are calculated →" link on the dashboard. Shows:

- Candidate filter thresholds with explanations
- Full scoring formula and per-component breakdown table
- Lineup position bonus table (positions 1–9)
- A-list vs B-list criteria
- Data source attribution table

---

## Known Quirks & Notes

**FanGraphs K% is a decimal proportion**
FanGraphs returns K% as `0.185` (meaning 18.5%) while Baseball-Reference returns it as `18.5`. The app detects this by checking `df['K%'].max() < 1.5` after fetching — if the max value is below 1.5, it multiplies the entire column by 100.

**FanGraphs `Pos` column is not a position**
The `Pos` column in FanGraphs batting stats is the positional runs adjustment used in WAR calculations (e.g. -12.5 for first basemen). It is not a position label. The app does not attempt to display position.

**Traded players show `---` in date-range stats**
Baseball-Reference date-range stats assign `---`, `TOT`, `2TM`, or `3TM` to players who changed teams mid-season. The app resolves this by building a fallback dict from the FanGraphs full-season data (which always reflects the current team) and substituting where the BRef value is ambiguous.

**Statcast `player_name` is the pitcher, not the batter**
In pybaseball's statcast output, the `player_name` column contains the **pitcher's** name in "Last, First" format. The batter is identified only by the `batter` column (mlbam ID). `playerid_reverse_lookup()` is used to convert IDs back to names.

**Statcast `n_priorpa_thisgame_player_at_bat == 0` matches every pitch in the first AB**
This column is 0 for every pitch in a batter's first plate appearance (not just the first pitch). Without `.drop_duplicates(subset=['game_pk', 'batter'])`, every pitch gets ranked separately, inflating the batting order average. A leadoff batter with a 4-pitch AB would average rank 2.5 instead of 1.

**Accented player names**
pybaseball sometimes returns names with literal `\xhh` escape sequences instead of proper unicode (e.g. `V\xe1zquez` instead of `Vázquez`). The `clean_name_string()` function decodes these. Name lookups also store an ASCII-normalized fallback key (via `unicodedata.normalize('NFKD')`) to handle mismatches between data sources.

**Season constant**
`SEASON = 2025` in `logic.py` controls which season's FanGraphs data is fetched. Update this at the start of each new season.

---

## Future Work

- Machine learning model to replace the hand-crafted scoring formula
- Proper position data source (MLB Stats API or a static lookup CSV) — currently shows `Unknown` for all players
- Support for the upcoming 2026 season once data becomes available
- Nightly cron job using the `generate_predictions` management command
