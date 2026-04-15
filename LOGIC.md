# Prediction Logic

How the Beat the Streak AI works — data sources, scoring, candidate filtering, and known quirks.

---

## Table of Contents

1. [How Predictions Work](#how-predictions-work)
   - [Data Sources](#data-sources)
   - [Candidate Filters](#candidate-filters)
   - [Scoring Formula](#scoring-formula)
   - [A-List vs B-List](#a-list-vs-b-list)
   - [Batting Order Calculation](#batting-order-calculation)
   - [Pitcher Data](#pitcher-data)
   - [Hit Result Verification](#hit-result-verification)
   - [Caching Strategy](#caching-strategy)
2. [File Reference](#file-reference)
3. [Database Schema](#database-schema)
4. [Migration History](#migration-history)
5. [Dashboard UI](#dashboard-ui)
6. [Known Quirks & Notes](#known-quirks--notes)

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
- Pitch-by-pitch data used for:
  1. **Average batting order position** — computed from each batter's first plate appearance per game over the prior 7 days
  2. **Pitcher throwing hand** — extracted from `player_name` (pitcher) and `p_throws` columns
  3. **Hit verification** — for past dates, checks whether each predicted player actually got a hit
  4. **Sprint speed, hard hit rate, xBA** — pulled from Statcast aggregates for each batter

**MLB Stats API**
- Probable pitchers for upcoming games
- Lineup confirmation status

**FanGraphs park factors**
- 3-year park factor for each stadium (offense; 100 = league average)

---

### Candidate Filters

Before any scoring, players are filtered down to a viable candidate pool:

| Filter | Threshold | Reason |
|---|---|---|
| Plate Appearances (last 7 days) | ≥ 10 PA | Eliminates injured, benched, or statistically insignificant samples |
| Strikeout Rate (last 7 days) | < 25% K% | Free-swingers are too inconsistent for a single-game hit prediction |

---

### Scoring Formula

Each candidate receives a score representing **P(Hit) — probability of getting at least one hit**:

```
P(Hit) = 1 - (1 - BA_eff)^(AB/game)
Score   = P(Hit) × 100   (displayed as 0–100 percentage)
```

**BA_eff** is the effective batting average after adjustments:

| Adjustment | Condition | Effect |
|---|---|---|
| xBA blend | Always | `0.55 × xBA + 0.45 × BA_weighted` |
| K% bonus | K% < 12% | +4% |
| K% penalty | K% > 22% | −3% |
| Hard hit bonus | HH% > 45% | +2% |
| Hard hit penalty | HH% < 28% | −2% |
| Sprint speed bonus | > 30 ft/s | +3% |
| Sprint speed bonus | > 28 ft/s | +1.5% |
| Sprint speed penalty | < 25 ft/s | −2% |
| Team rank bonus | Top 10 offenses | +2% |

**AB/game** is the player's average at-bats per game (7-day), adjusted by lineup position:
- Top of order: +0.32 ABs
- 9-hole: −0.32 ABs
- Range clamped to ≥ 2.5

---

### A-List vs B-List

After scoring, the top 20 candidates are split based on platoon matchup:

- **A-List** — cross-handed advantage (LHB vs RHP, RHB vs LHP) or pitcher hand unknown
- **B-List** — same-handed disadvantage detected

The B-list is not a disqualification — it's a flag to manually review the matchup before committing.

---

### ML Ranking vs P(Hit)

Every player gets two scores:

| Score | How it's computed | AUC (2025 test set) |
|---|---|---|
| **P(Hit)** | Hand-crafted formula weighting BA, xBA, K%, HH%, speed, etc. | 0.4386 — worse than random |
| **ML score** | LightGBM trained on 2025 outcomes — learned which features actually predicted hits | 0.5627 — meaningfully better than random |

**The final ranking order uses ML score** (not P(Hit)). P(Hit) drives the human-readable badges on the dashboard so you can understand *why* a player scored well, but the order you see them in — who's #1, who's #2 — is determined by the ML model.

**Which to trust when they disagree:**
- ML score wins. A 0.01 difference in P(Hit) is noise — the hand-crafted score compresses everyone into a narrow 67–99 range. If Player A has a higher P(Hit) but Player B has a higher ML score, pick Player B.
- The only exception: a clear red flag the model can't see, like a very recent injury or a last-minute pitcher change not yet reflected in stats.

**Why P(Hit) underperforms:** The formula was designed by intuition. The weights it assigns (e.g. how much to reward a 5% K% reduction) may not match what actually predicted hits in real games. LightGBM learned those weights from 2,132 labeled examples instead of guessing.

**Model file:** `data/models/hit_predictor_lightgbm.joblib` — loaded once at server startup in `logic.py`. If the file is missing, predictions fall back to P(Hit) ranking with no ML score shown.

---

### Batting Order Calculation

Average batting order position is computed from Statcast data over the 7 days prior to the prediction date:

1. Fetch statcast data for the 7-day window (each row = one pitch)
2. Isolate each batter's **first plate appearance** per game: `n_priorpa_thisgame_player_at_bat == 0`, then `.drop_duplicates(subset=['game_pk', 'batter'])` — without dedup, every pitch in the first AB gets a separate rank
3. Rank within each team's half-inning using `game_pk + '_' + inning_topbot` as a grouping key (corrects for `at_bat_number` counting across both teams)
4. Filter to `lineup_pos <= 9` to exclude pinch hitters and late substitutes
5. Average across all games in the window per batter
6. Map mlbam IDs back to names via `playerid_reverse_lookup()`, storing ASCII-normalized fallbacks for accented names

---

### Pitcher Data

For each player's matchup, the app identifies:

- **Opposing pitcher name** — from the MLB Stats API probable pitchers endpoint
- **Pitcher ERA and WHIP** — from FanGraphs full-season pitching stats, keyed by `(last_name, team)`
- **Pitcher throwing hand (L/R)** — from the MLB Stats API
- **Recent pitcher form** — last ~4 starts: K/9, BB/9, IP, ERA (displayed as z-scores vs season averages)

---

### Hit Result Verification

For past dates (prediction date < today), the dashboard verifies outcomes:

1. Statcast data is fetched for the exact game date
2. The `events` column identifies completed at-bats. Any `single`, `double`, `triple`, or `home_run` counts as a hit
3. `playerid_reverse_lookup()` converts batter mlbam IDs to names
4. Results are stored in `got_hit` as `True`/`False`/`None`

In the dashboard, player names are colored green (hit), red (no hit), or unstyled (future/off-day).

---

### Caching Strategy

All predictions are cached in PostgreSQL by date:

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

First load: 30–60s. Cached load: <100ms.

To force a re-fetch for a specific date:

```bash
python manage.py shell -c "from predictor.models import Prediction; Prediction.objects.filter(date='2025-09-28').delete()"
```

---

## File Reference

### `predictor/logic.py`

The entire prediction engine (~1300 lines). Key functions:

| Function | Purpose |
|---|---|
| `get_window_stats(days_back, ref_date)` | Fetches batting stats for the last N days. Tries Baseball-Reference first, falls back to FanGraphs. Standardizes column names and normalizes K% from decimal if needed. |
| `get_player_metadata(season)` | FanGraphs full-season batting stats for Name, Tm, Bats. Resolves traded-player team issues. |
| `get_pitcher_stats(season)` | FanGraphs full-season pitching stats. Returns a dict keyed by `(last_name, team)` with ERA, WHIP, GS. |
| `get_all_matchups(game_date)` | MLB Stats API — returns opponent and probable pitcher per team for a given date. |
| `get_probable_pitchers(game_date)` | MLB Stats API — returns pitcher name + throwing hand. |
| `get_lineup_status(game_date)` | MLB Stats API — returns which lineups are confirmed. |
| `get_avg_batting_order(start, end)` | Statcast — returns `(batting_order_dict, pitcher_hand_dict)`. |
| `get_hit_results(game_date)` | Statcast — returns `{player_name: True/False}` for every batter with a completed at-bat. |
| `get_hit_streaks(season)` | Statcast — returns active consecutive-game hit streaks per batter. |
| `get_sprint_speed(season)` | Statcast — returns sprint speed in ft/s per batter. |
| `get_park_factors()` | FanGraphs 3-year park factors. Returns dict of `{team: factor}`. |
| `get_team_rankings(season)` | pybaseball — team offensive rankings 1–30. |
| `clean_name_string(name)` | Handles pybaseball encoding quirks: literal `\xhh` escape sequences and mojibake. |
| `get_predictions(simulation_date)` | Main orchestrator. Calls all data functions, scores candidates, splits into A/B lists. |

**Important constants:**

```python
SEASON = 2026   # Controls which season's stats are fetched from FanGraphs/BRef

FG_TO_BREF_TEAM = {
    'SFG': 'SF', 'WSN': 'WSH', 'TBR': 'TB', 'KCR': 'KC', 'SDP': 'SD',
}
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
| `bats` | CharField(1) | `'L'`, `'R'`, or `'S'` |
| `score` | FloatField | P(Hit) × 100 — used for sorting |
| `score_breakdown` | CharField(800) | Pipe-delimited factor string for display |
| `opponent` | CharField(20) | Opposing team abbreviation |
| `probable_pitcher` | CharField(100) | Opposing pitcher name |
| `pitcher_era` | FloatField (nullable) | Opposing pitcher season ERA |
| `pitcher_whip` | FloatField (nullable) | Opposing pitcher season WHIP |
| `pitcher_hand` | CharField(1) | `'L'`, `'R'`, or `''` |
| `avg_batting_order` | FloatField (nullable) | Average lineup slot over prior 7 days |
| `is_home` | BooleanField | Whether the player's team is at home |
| `park_factor` | IntegerField (nullable) | Venue park factor (100 = league avg) |
| `team_rank` | IntegerField (nullable) | Team offensive rank 1–30 |
| `hit_streak` | IntegerField | Active consecutive-game hit streak |
| `stadium` | CharField(100) | Venue name |
| `notes` | CharField(200) | Risk flag description |
| `got_hit` | BooleanField (nullable) | `True`/`False` for past dates; `None` for future |
| `lineup_confirmed` | BooleanField (nullable) | Whether the lineup is confirmed |

`unique_together = ('date', 'name')` — one row per player per date.

`save_from_lists(date, a_list, b_list)` wipes existing predictions for the date and bulk-inserts all new ones.

---

### `predictor/views.py`

**`dashboard(request)`** — main page at `/`
- Reads `?date=` query param
- Checks DB cache; calls `get_predictions()` on miss
- For past dates, calls `get_hit_results()` if `got_hit` is null, then bulk-updates
- Passes `a_list`, `b_list`, `selected_date`, `pretty_date` to template

**`scoring_guide(request)`** — methodology page at `/scoring/`

---

### `predictor/templates/dashboard.html`

Bootstrap 5 single-page dashboard:

- Two tables: A-List (green header) and B-List (yellow header)
- Two-row layout per player: main row (name, team, bats, avg order) + factor sub-row (badges)
- Player name coloring: green if `got_hit=True`, red if `got_hit=False`, unstyled if `None`
- Factor badges rendered by JavaScript at page load, parsing the `score_breakdown` string from a `data-breakdown` attribute

**Badge color logic (JavaScript):**

| Badge | Coloring rule |
|---|---|
| `BA: X (+Yσ)` | Blue ≥+2σ, Green ≥+1σ, Yellow ≥0σ, Orange ≥-1σ, Red <-1σ |
| `K%: X% (+Yσ)` | Inverted (lower is better): Blue ≤-2σ, Green ≤-1σ, Yellow ≤0σ, Orange ≤+1σ, Red >+1σ |
| `Team Rank: #X` | Always gray |
| `Lineup: #X` | Always gray |

---

### `predictor/management/commands/generate_predictions.py`

Pre-generates predictions for a specific date without a browser:

```bash
python manage.py generate_predictions
python manage.py generate_predictions --date 2026-04-09
python manage.py generate_predictions --date 2026-04-09 --force
```

---

## Database Schema

One custom table: `predictor_prediction`. Django also creates system tables for admin, auth, sessions, etc.

Active database: PostgreSQL (`bts` on `db:5432` in Docker, or `beat_the_streak` on `localhost:5432` for native dev).

The `db.sqlite3` file in the repo root is not used.

---

## Migration History

| Migration | Change |
|---|---|
| `0001_initial` | Created `predictor_prediction` with core fields |
| `0002_alter_prediction_team` | Increased `team` max_length 10 → 50 |
| `0003` | Added `pitcher_era`, `pitcher_whip` |
| `0004` | Added `avg_batting_order` |
| `0005` | Added `pitcher_hand`, `score_breakdown` |
| `0006` | Added `got_hit` nullable boolean |
| `0007` | Added `stadium` |

---

## Dashboard UI

```
⚾ Beat the Streak AI
Predicting hits for April 9, 2026
[How scores are calculated →]

[ Date picker: 2026-04-09 ] [ Run Prediction ]

✅ The A-List (Safe Picks)
┌─────────────────────────────────────┬────────────────────────────────────────┐
│ Player                              │ Matchup                                │
├─────────────────────────────────────┼────────────────────────────────────────┤
│ George Springer — TOR               │ vs NYY                                 │
│ Bats R • Avg Order: 1.2             │ [R] Gerrit Cole — ERA: 2.63 / WHIP:0.87│
│ [P(Hit): 74%] [BA: 0.312] [xBA: 0.298] [K%: 9.1%] [Speed: 28.4]             │
└─────────────────────────────────────┴────────────────────────────────────────┘

⚠️ The B-List (Risks Detected)
...
```

---

## Known Quirks & Notes

**FanGraphs K% is a decimal proportion**
FanGraphs returns K% as `0.185` (meaning 18.5%) while Baseball-Reference returns `18.5`. The app detects this by checking `df['K%'].max() < 1.5` and multiplying by 100 if needed.

**FanGraphs `Pos` column is not a position**
It's the positional runs adjustment used in WAR (e.g. -12.5 for first basemen), not a position label. The app stores `'Unknown'` for position.

**Traded players show `---` in date-range stats**
Baseball-Reference assigns `---`, `TOT`, `2TM`, or `3TM` to players who changed teams mid-season. The app resolves this via a fallback dict from FanGraphs full-season data.

**Statcast `player_name` is the pitcher, not the batter**
In pybaseball's statcast output, `player_name` contains the pitcher's name in "Last, First" format. The batter is identified only by mlbam ID.

**Statcast `n_priorpa_thisgame_player_at_bat == 0` matches every pitch in the first AB**
Without `.drop_duplicates(subset=['game_pk', 'batter'])`, every pitch in a batter's first PA gets ranked separately, inflating the batting order average.

**Accented player names**
pybaseball sometimes returns names with literal `\xhh` escape sequences instead of proper unicode (e.g. `V\xe1zquez` instead of `Vázquez`). `clean_name_string()` decodes these. Lookups also store ASCII-normalized fallback keys.

**Season constant**
`SEASON = 2026` in `logic.py` controls which season's FanGraphs data is fetched. Update at the start of each new season.
