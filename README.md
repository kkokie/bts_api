# Beat the Streak AI

A Django web application that analyzes MLB player statistics and produces daily hit predictions for use in ESPN's Beat the Streak contest. The app pulls data from multiple baseball data sources, scores each candidate using a composite formula, and presents results through a dashboard with color-coded factor badges, pitcher matchup stats, park factors, and real-time lineup confirmation.

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
   - [Batting Order Calculation](#batting-order-calculation)
   - [Pitcher Data](#pitcher-data)
   - [Park Factors & Stadiums](#park-factors--stadiums)
   - [Hit Streak](#hit-streak)
   - [Hit Result Verification](#hit-result-verification)
   - [Caching Strategy](#caching-strategy)
6. [Automated Daily Pipeline](#automated-daily-pipeline)
   - [How It Works](#how-it-works)
   - [Running on a Server](#running-on-a-server)
7. [Management Commands](#management-commands)
8. [File Reference](#file-reference)
9. [Database Schema](#database-schema)
10. [Migration History](#migration-history)
11. [Dashboard UI](#dashboard-ui)
12. [Known Quirks & Notes](#known-quirks--notes)

---

## Tech Stack

| Component | Technology | Version |
|---|---|---|
| Web framework | Django | 5.2.10 |
| Database | PostgreSQL | any recent |
| Python | CPython | 3.10.0 |
| Baseball data | pybaseball | 2.2.7 |
| HTTP requests | requests | (bundled with pybaseball) |
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
│   │
│   ├── migrations/                  # Database schema change history (0001–0012)
│   │
│   ├── templates/
│   │   ├── dashboard.html           # Main prediction dashboard
│   │   └── scoring_guide.html       # Scoring methodology explanation page
│   │
│   └── management/commands/
│       ├── generate_predictions.py  # Pre-generate predictions for a date
│       ├── fetch_schedule.py        # Fetch today's game times (runs at 9:30 AM)
│       └── check_lineups.py        # Check confirmed starting lineups (runs every 10 min)
│
├── schedule_cache.json              # Written daily by fetch_schedule; read by check_lineups
├── lineup_cron.log                  # Cron job output log
├── manage.py                        # Django management entry point
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
pip install django psycopg2-binary pybaseball pandas numpy requests
```

### 3. Create the PostgreSQL database

```bash
psql -c "CREATE DATABASE beat_the_streak;"
```

### 4. Configure database credentials

In `config/settings.py`:

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

### 5. Run migrations

```bash
python manage.py migrate
```

---

## Running the App

```bash
source venv/bin/activate
python manage.py runserver
```

Open `http://localhost:8000`. The default date is **September 28, 2025** (last day of the 2025 season), which has full historical data. Use the date picker to change dates.

---

## How Predictions Work

### Data Sources

| Source | Library call | Used for |
|---|---|---|
| Baseball-Reference (date range) | `batting_stats_range` | Recent batting stats (H, G, PA, K%, R) — primary scoring input |
| FanGraphs (full season) | `batting_stats` | Player metadata: batter handedness, current team (for traded players) |
| FanGraphs (full season) | `pitching_stats` | Opposing pitcher season ERA and WHIP |
| Baseball-Reference (date range) | `pitching_stats_range` | Pitcher recent form (~4 starts): H, ERA, BB, IP with z-scores; bullpen H allowed |
| Baseball-Reference (schedule) | `schedule_and_record` | Today's opponent, probable pitcher name, game time, home/away |
| MLB Stats API | direct HTTP | Confirmed starting lineups (1–2 hrs before game time) |
| Baseball Savant / Statcast | `statcast` | Average batting order, pitcher throwing hand, hit verification, hit streaks |

The Baseball-Reference date-range batting stats are attempted first. If unavailable (early season, All-Star break, off-season), the app falls back to FanGraphs full-season stats. To keep both on the same scale, the score is based on **projected weekly hits** (`H / G × 7`) rather than raw hits — this way a player with 5 hits in 4 games and a player with 95 hits in 76 games both produce comparable numbers (~8.75 projected weekly hits).

---

### Candidate Filters

| Filter | Threshold | Reason |
|---|---|---|
| Plate Appearances (last 7 days) | ≥ 15 PA | Eliminates injured, benched, or statistically thin samples |
| Strikeout Rate (last 7 days) | < 25% K% | Eliminates free-swingers too inconsistent for single-game hit prediction |

---

### Scoring Formula

Each candidate receives a composite score used only for **sorting**. It is not displayed in the UI — instead the dashboard shows each component individually as color-coded badges.

```
Score = (H / G × 7)                           ← projected weekly hits (base score)
      + [10 if K% < 12%]                       ← elite contact hitter bonus
      + [10 if team in top 10 runs scored]     ← hot team bonus
      + max(0, (9 − avg_lineup_pos) / 8 × 10) ← lineup position bonus (0–10)
```

**Projected weekly hits** (`H / G × 7`) is the dominant component. It normalizes across both the BRef 7-day window (where H might be 3–14) and the FanGraphs full-season fallback (where H might be 95–180), so the score stays consistent regardless of which data source was used.

**Park factors** are shown as display-only badges and are **not** included in the score — otherwise a player at Coors Field would always outscore an equivalent player at Petco Park regardless of their actual form.

The lineup position bonus formula by slot:

| Lineup slot | Bonus |
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

---

### Batting Order Calculation

Average batting order position is computed from Statcast data over the 7 days prior to the prediction date.

1. Fetch statcast pitch-by-pitch data for the window
2. Filter to `n_priorpa_thisgame_player_at_bat == 0` then `.drop_duplicates(subset=['game_pk', 'batter'])` — one row per batter per game. The dedup is critical: every pitch within the first at-bat satisfies the filter, so without it a leadoff batter with a 4-pitch AB would average rank 2.5 instead of 1
3. Build a `game_side` key (`game_pk + '_' + inning_topbot`) to rank lineup positions within each team's half-inning separately — `at_bat_number` counts across both teams, so home batters would show ~10 without this correction
4. Keep only `lineup_pos ≤ 9` to exclude pinch hitters
5. Average across all games per batter ID, then reverse-lookup IDs to names via `playerid_reverse_lookup()`

---

### Pitcher Data

**Season ERA / WHIP** — from FanGraphs full-season pitching stats, keyed by `(last_name, team)`.

**Recent form (~4 starts)** — from Baseball-Reference `pitching_stats_range` over the last 28 days, filtered to starters (`GS > 0`). Computed for each opposing pitcher: H allowed, ERA, BB, IP — each shown with a z-score relative to all starters in that window. High z-scores for H/ERA/BB mean a worse pitcher = favorable matchup for the batter.

**Bullpen** — from the same `pitching_stats_range` call filtered to relievers (`GS == 0`), grouped by team. Shows total hits allowed by the opponent's bullpen over the last 15 days with a z-score.

**Throwing hand** — extracted from statcast `p_throws` column in the same pull used for batting order. Displayed as `[L]` or `[R]` before the pitcher name.

All schedule lookups are batched — one HTTP request per unique team, not per player.

---

### Park Factors & Stadiums

Each game is assigned the home team's park factor from a static dict (`PARK_FACTORS` in `logic.py`) sourced from FanGraphs 3-year park factors for offense (non-HR). 100 = league average; higher means more hits.

| Example | Park Factor |
|---|---|
| Coors Field (COL) | 115 |
| Fenway Park (BOS) | 108 |
| Wrigley Field (CHC) | 107 |
| League average | 100 |
| Petco Park (SD) | 95 |
| Angel Stadium (LAA) | 93 |

Stadium names come from a static `TEAM_TO_STADIUM` dict keyed by the same abbreviations. For away games, the park team is the opponent (the home team's park). Game time is pulled from the BRef schedule's `Time` column — only used if the value contains AM/PM (future games show start time; past games show duration like `2:34`).

---

### Hit Streak

For each predicted player, the app computes how many **consecutive games** they have gotten at least one hit, counting backwards from the day before the prediction date. Off days (days the player didn't appear in a game) are skipped — only a hitless appearance ends the streak.

This is computed from 30 days of Statcast data. Results are shown as a meta-chip next to the player name:

- `4G streak🔥` — 4 consecutive games with a hit (1 fire emoji per 3 games)
- `9G streak🔥🔥🔥` — 9-game hitting streak

---

### Hit Result Verification

For past dates, the app automatically checks whether each predicted player got a hit:

1. Fetch Statcast for the exact date
2. Events column: `single`, `double`, `triple`, `home_run` = got a hit; any other finished event = no hit
3. Convert batter mlbam IDs to names via `playerid_reverse_lookup()`
4. Store result in `got_hit` field — `True`/`False`/`None`

In the dashboard, player names are colored **green** (hit) or **red** (no hit). `None` means the date is in the future or the player's team had an off day.

---

### Caching Strategy

All predictions are cached in PostgreSQL by date. The flow on each page load:

```
Request for date X
    │
    ├── Prediction rows exist for date X in DB?
    │       YES → serve instantly (no external API calls)
    │       NO  → call get_predictions(), save to DB, then serve
    │
    └── Date X is in the past AND got_hit is NULL for any row?
            YES → call get_hit_results(), bulk_update got_hit
            NO  → skip
```

The expensive data fetching (multiple pybaseball HTTP calls, ~30–60 seconds) only happens once per date. Subsequent loads are instant DB reads.

To force a re-fetch after a logic change:

```bash
# Clear all dates
python manage.py shell -c "from predictor.models import Prediction; Prediction.objects.all().delete()"

# Clear a specific date
python manage.py shell -c "from predictor.models import Prediction; Prediction.objects.filter(date='2025-09-28').delete()"
```

---

## Automated Daily Pipeline

The app runs two scheduled tasks every day during the season to automatically check confirmed starting lineups and mark any predicted players who have been scratched.

### How It Works

**Why cron?** If lineup checks only ran on page load, a scratched player would only show the strikethrough when someone happens to visit the site. A cron job runs silently in the background on a schedule — no user interaction needed.

**Two management commands work together:**

#### 1. `fetch_schedule` — runs once at 9:30 AM ET

Hits the MLB Stats API to get today's full game schedule and find the earliest first pitch. Writes the result to `schedule_cache.json`:

```json
{"date": "2025-04-15", "first_game_utc": "2025-04-15T17:10:00Z"}
```

This handles all edge cases automatically — Monday/Thursday getaway day early games, Patriots Day (Boston 11 AM game), Spring Training, etc. Whatever the MLB API says is the first game that day is the trigger.

#### 2. `check_lineups` — runs every 10 minutes all day, but self-exits immediately unless in the pre-game window

On each run, it:
1. Reads `schedule_cache.json`
2. Checks: is current time ≥ (first game time − 1 hour)? If not → prints "Too early" and exits in < 1 second
3. If in the window: hits the MLB Stats API lineup endpoint
4. For each predicted player whose **team's** lineup has been posted, marks them `True` (in lineup) or `False` (not in lineup)
5. Teams whose lineup hasn't been posted yet are left as `None` and re-checked next cycle
6. Once all predictions have `lineup_confirmed` set, exits with "All confirmed — nothing to do"

**Timeline example for a 1:10 PM ET first game:**

| Time | What happens |
|---|---|
| 9:30 AM | `fetch_schedule` runs — saves `first_game_utc: 17:10Z` |
| Every 10 min until ~12:10 PM | `check_lineups` reads cache → "Too early, opens in X min" → exits instantly |
| ~12:00–12:10 PM | Teams post lineup cards |
| 12:10 PM | `check_lineups` enters window, calls MLB API, marks scratched players |
| Every 10 min until 1:40 PM | Continues checking until all lineups confirmed |
| 1:40 PM+ | "All confirmed" → exits immediately |

In the dashboard, scratched players show with a strikethrough name and "not in lineup" label. Players whose game lineup hasn't been posted yet show normally (their lineup status is still unknown).

**Output log** — every run is logged to `lineup_cron.log` in the project root:

```bash
cat lineup_cron.log
```

### Running on a Server

**Local macOS (current setup):**
The cron jobs are installed in your user crontab and run as long as your Mac is on and you're logged in:

```bash
crontab -l   # view current cron jobs
crontab -e   # edit them
```

Current crontab:
```
# Fetch today's game schedule at 9:30 AM ET
30 9 * * * cd /path/to/project && venv/bin/python manage.py fetch_schedule >> lineup_cron.log 2>&1

# Check lineups every 10 minutes — self-exits if outside pre-game window
*/10 * * * * cd /path/to/project && venv/bin/python manage.py check_lineups >> lineup_cron.log 2>&1
```

**Linux VPS / cloud server:**
Same concept. SSH in and run `crontab -e` to install the same entries. The time in crontab uses the server's local timezone — set it to ET with `TZ=America/New_York` at the top of the crontab:

```
TZ=America/New_York
30 9 * * * cd /path/to/project && venv/bin/python manage.py fetch_schedule >> lineup_cron.log 2>&1
*/10 * * * * cd /path/to/project && venv/bin/python manage.py check_lineups >> lineup_cron.log 2>&1
```

**Heroku / Railway / Render:**
These platforms have their own scheduler add-ons instead of system cron:
- **Heroku**: add the [Heroku Scheduler](https://devcenter.heroku.com/articles/scheduler) add-on, then add `python manage.py fetch_schedule` at 9:30 AM and `python manage.py check_lineups` every 10 minutes
- **Railway**: use the built-in Cron Service with the same commands
- **Render**: add a Cron Job service pointing to the same commands

The `check_lineups` command is designed to be safe to run every 10 minutes on any platform — it self-exits in milliseconds when outside the window, so it costs almost nothing outside game days.

---

## Management Commands

### `generate_predictions`
Pre-generate and cache predictions from the terminal without a browser:

```bash
# Generate for today
python manage.py generate_predictions

# Generate for a specific date
python manage.py generate_predictions --date 2025-09-28

# Overwrite existing predictions
python manage.py generate_predictions --date 2025-09-28 --force
```

### `fetch_schedule`
Fetch today's MLB game schedule and store the first game time:

```bash
python manage.py fetch_schedule
# [2025-04-15] First game at 2025-04-15T17:10:00Z UTC — lineup checks will start 1 hr before.
```

Writes `schedule_cache.json` to the project root. Run this manually if testing lineup checks mid-day without waiting for the 9:30 AM cron.

### `check_lineups`
Check confirmed starting lineups and mark scratched players:

```bash
python manage.py check_lineups
# Too early — lineup window opens in 247 min (at 16:10 UTC).
# -- or --
# [16:15 UTC] Checked 18 players across 6 teams. 1 scratched.
#   ⚠️  Marcus Semien NOT in lineup
```

Run manually at any time to test. Self-exits if outside the 1-hour pre-game window.

---

## File Reference

### `predictor/logic.py`

The entire prediction engine. Key functions:

| Function | Purpose |
|---|---|
| `get_window_stats(days_back, reference_date)` | Fetches batting stats for the last N days. Tries BRef first, falls back to FanGraphs. Standardizes column names and normalizes K% from decimal to percentage if needed. |
| `get_player_metadata(season)` | FanGraphs full-season batting stats for `Name`, `Tm`, `Bats`. Resolves traded-player team issues. |
| `get_pitcher_stats(season)` | FanGraphs full-season pitching stats keyed by `(last_name, team)`. Returns ERA, WHIP, GS. |
| `get_pitcher_recent_stats(reference_date, days_back=28)` | BRef pitching stats over ~28 days for starters. Returns lookup dict + z-score params for H, ERA, BB, IP. |
| `get_bullpen_stats(reference_date, days_back=15)` | BRef pitching stats over 15 days for relievers, grouped by team. Returns total H allowed per team + z-score params. |
| `get_matchup_info(team, date)` | BRef schedule for one team on one date. Returns `(opponent, pitcher, game_time, is_home)`. |
| `get_avg_batting_order(start_date, end_date)` | Statcast for the window. Returns `(batting_order_dict, pitcher_hand_dict)`. |
| `get_hit_streaks(reference_date, days_back=30)` | Statcast for 30 days. Returns `{player_name: consecutive_game_hit_streak}`. |
| `get_hit_results(game_date)` | Statcast for one date. Returns `{player_name: True/False}` for every batter. |
| `get_lineup_status(game_date)` | MLB Stats API lineup endpoint. Returns `(confirmed_names_dict, teams_with_lineup_posted_set)`. |
| `clean_name_string(name)` | Fixes pybaseball encoding quirks: literal `\xhh` sequences and mojibake. |
| `get_predictions(simulation_date)` | Main orchestrator. Calls all data functions, scores candidates, returns two lists of player dicts. |

**Key constants:**

```python
SEASON = 2025           # Update at start of each new season
PARK_FACTORS = {...}    # FanGraphs 3-year park factors keyed by team abbreviation
TEAM_TO_STADIUM = {...} # Home stadium name per team
BREF_OPP_NORM = {...}   # BRef Opp column abbreviation fixes (CHW→CWS, KCR→KC, etc.)
NAME_TO_ABBR = {...}    # Full BRef city names → internal abbreviations
```

---

### `predictor/models.py`

The `Prediction` model — one row per player per date.

| Field | Type | Description |
|---|---|---|
| `date` | DateField (indexed) | Game/prediction date |
| `list_type` | CharField | `'A'` or `'B'` (B-list is stored but not displayed) |
| `name` | CharField(100) | Player full name |
| `team` | CharField(50) | Team abbreviation |
| `bats` | CharField(1) | `'L'` or `'R'` |
| `score` | FloatField | Composite score (sorting only, not displayed) |
| `score_breakdown` | CharField(800) | Pipe-delimited factor string for badge rendering |
| `opponent` | CharField(20) | Opposing team abbreviation |
| `probable_pitcher` | CharField(100) | Opposing pitcher name |
| `pitcher_era` | FloatField (nullable) | Opposing pitcher season ERA |
| `pitcher_whip` | FloatField (nullable) | Opposing pitcher season WHIP |
| `pitcher_hand` | CharField(1) | `'L'`, `'R'`, or `''` |
| `avg_batting_order` | FloatField (nullable) | Average lineup slot over prior 7 days |
| `stadium` | CharField(100) | Home stadium name |
| `game_time` | CharField(20) | Scheduled start time (e.g. `"7:10 PM ET"`) or `''` |
| `park_factor` | IntegerField (nullable) | Park factor of the home stadium |
| `is_home` | BooleanField | True if the player's team is at home |
| `team_rank` | IntegerField (nullable) | Team's offensive rank by runs scored (last 7 days) |
| `hit_streak` | IntegerField | Consecutive games with at least one hit |
| `lineup_confirmed` | BooleanField (nullable) | `True`=in lineup, `False`=scratched, `None`=not yet checked |
| `got_hit` | BooleanField (nullable) | `True`/`False` for past dates; `None` for future |
| `notes` | CharField(200) | Risk flags (e.g. `"Lefty Batter"`) |

`unique_together = ('date', 'name')` — no duplicate rows per player per date.

`fire_emojis` property — returns `'🔥' * (hit_streak // 3)` for display.

---

### `predictor/views.py`

**`dashboard(request)`** — main page at `/`
- Reads `?date=` query param (defaults to 2025-09-28)
- Checks DB cache; calls `get_predictions()` on miss
- For past dates, backfills `got_hit` via `get_hit_results()` if any row still has `None`
- Lineup status is updated separately by the cron job, not on page load

**`scoring_guide(request)`** — methodology page at `/scoring/`
- Pre-computes lineup bonus table (positions 1–9)

---

## Database Schema

Active database: PostgreSQL (`beat_the_streak` on localhost:5432). The `db.sqlite3` file in the repo root is unused.

Custom table: `predictor_prediction`. Django system tables (sessions, admin, auth, etc.) are also created by `migrate`.

---

## Migration History

| Migration | Change |
|---|---|
| `0001_initial` | Core `predictor_prediction` table |
| `0002_alter_prediction_team` | `team` max_length 10 → 50 (full team names were truncating) |
| `0003` | Added `pitcher_era`, `pitcher_whip` |
| `0004` | Added `avg_batting_order` |
| `0005` | Added `pitcher_hand`, `score_breakdown` |
| `0006` | Added `got_hit` nullable boolean |
| `0007` | Added `game_time`, `park_factor`, `stadium` |
| `0008` | Added `is_home` |
| `0009` | `score_breakdown` max_length 500 → 800 (pitcher recent stats made strings longer) |
| `0010` | Added `team_rank` |
| `0011` | Added `hit_streak` |
| `0012` | Added `lineup_confirmed` |

---

## Dashboard UI

```
⚾ Beat the Streak AI
Predicting hits for April 15, 2025
[How scores are calculated →]

[ Date picker: 2025-04-15 ] [ Run Prediction ]

⚾ Our Model Suggests...
┌──────────────────────────────────────────┬──────────────────────────────────────────┐
│ Player                                   │ Matchup                                  │
├──────────────────────────────────────────┼──────────────────────────────────────────┤
│ Luis Arráez — SD                         │ @ CHC                                    │
│ Bats L  #1.0 lineup  Rank #3  PF 107     │ [R] Justin Steele — ERA: 3.41 / WHIP:1.10│
│                                          │ Wrigley Field — 2:20 PM ET               │
│ [H: 10.5 (+2.3σ)] [K%: 5.1% (-3.1σ)]   │        [P.H: 28 (+1.2σ)] [P.ERA: 4.12]  │
├──────────────────────────────────────────┼──────────────────────────────────────────┤
│ ~~Marcus Semien~~ — TEX  not in lineup   │ vs HOU                                   │
│ Bats R  #2.0 lineup  9G streak🔥🔥🔥     │ [R] Framber Valdez — ERA: 2.89 / WHIP:1.0│
│                                          │ Globe Life Field — 8:05 PM ET            │
│ [H: 9.8 (+1.9σ)] [K%: 14.2% (+0.1σ)]   │        [BP: 62 (+1.8σ)]                  │
└──────────────────────────────────────────┴──────────────────────────────────────────┘
```

**Badge layout:**
- **Left side of badge row** — batter stats: `H:` projected weekly hits, `K%` strikeout rate
- **Right side of badge row** — pitcher stats: `P.H` hits allowed, `P.ERA`, `P.BB` walks, `P.IP` innings, `BP` bullpen hits
- **Meta-chips** (inline with player name) — `#X lineup`, `Rank #X`, `PF XXX`, `XG streak🔥`

**Badge color scale** (based on z-score):

| Color | Meaning |
|---|---|
| Blue | ≥ +2σ (exceptional) |
| Green | ≥ +1σ (above average) |
| Yellow | ≥ 0σ (average) |
| Orange | ≥ -1σ (below average) |
| Red | < -1σ (poor) |

K% is inverted (lower strikeout rate = better). Pitcher stats (H, ERA, BB, BP) follow the same scale — high means the pitcher has been worse than average, which is favorable for the batter.

**Lineup status:**
- Normal name — no lineup data yet
- **Green name** — got a hit (past dates)
- **Red name** — went hitless (past dates)
- ~~Strikethrough~~ gray + "not in lineup" — scratched from today's confirmed lineup

---

## Known Quirks & Notes

**BRef Opp column uses different abbreviations**
The `Opp` column in BRef schedules uses `CHW`, `KCR`, `SFG`, `TBR`, `WSN`, `ATH` while the rest of the app uses `CWS`, `KC`, `SF`, `TB`, `WSH`, `OAK`. The `BREF_OPP_NORM` dict in `logic.py` normalizes these on every schedule read.

**BRef full city names in batting stats**
`batting_stats_range()` returns `Tm` as full city names (`'Seattle'`, `'San Diego'`). Ambiguous cities (`'Chicago'`, `'Los Angeles'`, `'New York'`) are resolved using FanGraphs metadata which returns the specific team abbreviation (`CHC`/`CWS`, `LAD`/`LAA`, `NYY`/`NYM`).

**Traded players show `---` in date-range stats**
BRef assigns `---`, `TOT`, `2TM`, or `3TM` to mid-season trades. The app falls back to FanGraphs full-season team data, which always reflects the current team.

**FanGraphs K% is a decimal proportion**
FanGraphs returns `K%` as `0.185` (18.5%) while BRef returns `18.5`. Detected by checking `max() < 1.5` after fetch and multiplying by 100 if true.

**FanGraphs `Pos` column is not a position name**
`Pos` in FanGraphs batting stats is the WAR positional adjustment float (e.g. -12.5 for first base). Position is not displayed.

**Statcast `player_name` is the pitcher**
In pybaseball statcast output, `player_name` is the **pitcher** in `"Last, First"` format. Batters are identified by `batter` (mlbam ID). `playerid_reverse_lookup()` converts IDs back to names.

**BRef `Time` column shows duration for past games**
For completed games, the `Time` column shows game duration (e.g. `"2:34"`). For future/unplayed games it shows scheduled start time (e.g. `"7:10 PM"`). The app only uses the value if it contains `AM` or `PM`.

**Accented player names**
pybaseball sometimes returns names as literal `\xhh` sequences instead of proper unicode. `clean_name_string()` decodes these. All name lookups also store an ASCII-normalized fallback key to handle mismatches between data sources.

**`SEASON` constant**
`SEASON = 2025` in `logic.py` controls which year's FanGraphs data is fetched. **Update this at the start of each new season.**

**Cron jobs are machine-specific**
The cron entries installed via `crontab` only run on this machine while it is on and the user is logged in. If the app moves to a server, reinstall the crontab there. See [Running on a Server](#running-on-a-server).
