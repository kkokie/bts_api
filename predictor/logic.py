import re
import unicodedata
import requests
import pandas as pd
from pybaseball import batting_stats_range, batting_stats, schedule_and_record, pitching_stats, pitching_stats_range, statcast, playerid_reverse_lookup
from datetime import datetime, timedelta

# --- CONFIGURATION ---
SEASON = 2025

# MAPPING: Full Name (from Stats) -> BRef Code (for Schedule)
NAME_TO_ABBR = {
    'Arizona': 'ARI', 'Atlanta': 'ATL', 'Baltimore': 'BAL', 'Boston': 'BOS',
    'Chicago White Sox': 'CWS', 'Chicago Cubs': 'CHC', 'Cincinnati': 'CIN', 'Cleveland': 'CLE',
    'Colorado': 'COL', 'Detroit': 'DET', 'Houston': 'HOU', 'Kansas City': 'KC',
    'Los Angeles Angels': 'LAA', 'Los Angeles Dodgers': 'LAD', 'Miami': 'MIA', 'Milwaukee': 'MIL',
    'Minnesota': 'MIN', 'New York Yankees': 'NYY', 'New York Mets': 'NYM', 'Oakland': 'OAK', 'Athletics': 'OAK',
    'Philadelphia': 'PHI', 'Pittsburgh': 'PIT', 'San Diego': 'SD', 'San Francisco': 'SF',
    'Seattle': 'SEA', 'St. Louis': 'STL', 'Tampa Bay': 'TB', 'Texas': 'TEX',
    'Toronto': 'TOR', 'Washington': 'WSH'
}

# FanGraphs uses slightly different abbreviations than baseball-reference
FG_TO_BREF_TEAM = {
    'SFG': 'SF', 'WSN': 'WSH', 'TBR': 'TB', 'KCR': 'KC', 'SDP': 'SD',
}

# BRef Opp column uses different abbreviations than team schedule URLs.
# Normalize them to our internal keys before any dict lookup.
BREF_OPP_NORM = {
    'CHW': 'CWS', 'KCR': 'KC', 'SFG': 'SF', 'SDP': 'SD',
    'TBR': 'TB', 'WSN': 'WSH', 'ATH': 'OAK',
}

# Home stadium per team (BRef abbreviations + ATH alias for relocated Athletics)
TEAM_TO_STADIUM = {
    'ARI': 'Chase Field', 'ATL': 'Truist Park', 'BAL': 'Camden Yards',
    'BOS': 'Fenway Park', 'CHC': 'Wrigley Field', 'CWS': 'Guaranteed Rate Field',
    'CIN': 'Great American Ball Park', 'CLE': 'Progressive Field', 'COL': 'Coors Field',
    'DET': 'Comerica Park', 'HOU': 'Minute Maid Park', 'KC': 'Kauffman Stadium',
    'LAA': 'Angel Stadium', 'LAD': 'Dodger Stadium', 'MIA': 'loanDepot Park',
    'MIL': 'American Family Field', 'MIN': 'Target Field', 'NYM': 'Citi Field',
    'NYY': 'Yankee Stadium', 'PHI': 'Citizens Bank Park', 'PIT': 'PNC Park',
    'SD': 'Petco Park', 'SF': 'Oracle Park', 'SEA': 'T-Mobile Park',
    'STL': 'Busch Stadium', 'TB': 'Tropicana Field', 'TEX': 'Globe Life Field',
    'TOR': 'Rogers Centre', 'WSH': 'Nationals Park',
    'OAK': 'Sutter Health Park', 'ATH': 'Sutter Health Park',  # Athletics (Sacramento, 2025+)
}

# FanGraphs 3-year park factors (offense, non-HR). 100 = league average.
PARK_FACTORS = {
    'COL': 115, 'BOS': 108, 'CHC': 107, 'NYY': 105, 'CIN': 104,
    'HOU': 103, 'MIL': 103, 'PHI': 102, 'BAL': 101, 'CLE': 100,
    'LAD': 100, 'NYM': 100, 'STL': 100, 'TOR': 100, 'ATL': 99,
    'DET': 99, 'MIN': 99, 'TEX': 99, 'CWS': 98, 'SF': 97,
    'SEA': 97, 'TB': 97, 'ARI': 96, 'KC': 96, 'WSH': 96,
    'PIT': 96, 'SD': 95, 'MIA': 94, 'OAK': 94, 'LAA': 93,
    'ATH': 94,  # Sacramento Athletics (same franchise as OAK)
}


def get_pitcher_stats(season):
    """Fetch season pitcher stats and return a lookup dict keyed by (last_name, team)."""
    print(f"  [DEBUG] Fetching pitcher stats for {season}...")
    try:
        df = pitching_stats(season, qual=1)

        if 'Team' in df.columns:
            df['Team'] = df['Team'].map(lambda x: FG_TO_BREF_TEAM.get(x, x))

        lookup = {}
        for _, row in df.iterrows():
            name = row.get('Name', '')
            if not isinstance(name, str) or not name:
                continue
            last_name = name.split()[-1].lower()
            team = row.get('Team', '')
            key = (last_name, team)
            if key not in lookup or row.get('GS', 0) > lookup[key].get('GS', 0):
                lookup[key] = {
                    'ERA': row.get('ERA'),
                    'WHIP': row.get('WHIP'),
                    'GS': row.get('GS', 0),
                }

        print(f"  [DEBUG] Loaded stats for {len(lookup)} pitchers.")
        return lookup
    except Exception as e:
        print(f"  [DEBUG] ❌ CRASH in get_pitcher_stats: {e}")
        return {}


def _normalize_pitcher_team(raw):
    """Normalize BRef pitching Tm column (full names or abbrevs) to internal abbreviations."""
    t = str(raw).strip()
    return NAME_TO_ABBR.get(t, FG_TO_BREF_TEAM.get(t, t))


def get_pitcher_recent_stats(reference_date, days_back=28):
    """
    Fetch starter pitching stats over a ~28-day window (~4 starts).
    Returns (lookup, h_params, era_params, bb_params, ip_params)
      lookup: {(last_name_lower, team_abbr): {H, ERA, BB, IP}}
      *_params: (mean, std) for z-scoring across the pitcher pool.
    """
    end = reference_date - timedelta(days=1)
    start = end - timedelta(days=days_back)
    print(f"  [DEBUG] Fetching pitcher recent stats ({start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')})...")
    try:
        df = pitching_stats_range(start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d'))
        BAD = {'TOT', '2TM', '3TM', '---', ''}
        df = df[~df['Tm'].astype(str).str.strip().isin(BAD)].copy()
        if 'GS' in df.columns:
            df = df[df['GS'] > 0].copy()

        lookup = {}
        for _, row in df.iterrows():
            name = str(row.get('Name', '')).strip()
            if not name:
                continue
            last_name = name.split()[-1].lower()
            team = _normalize_pitcher_team(row.get('Tm', ''))
            key = (last_name, team)
            lookup[key] = {
                'H':   row.get('H'),
                'ERA': row.get('ERA'),
                'BB':  row.get('BB'),
                'IP':  row.get('IP'),
            }

        def _params(stat):
            vals = [v[stat] for v in lookup.values()
                    if v[stat] is not None and not pd.isna(v[stat])]
            if len(vals) < 2:
                return (0.0, 1.0)
            s = pd.Series(vals)
            return (float(s.mean()), max(float(s.std()), 0.1))

        print(f"  [DEBUG] Pitcher recent stats: {len(lookup)} starters.")
        return lookup, _params('H'), _params('ERA'), _params('BB'), _params('IP')
    except Exception as e:
        print(f"  [DEBUG] ❌ CRASH in get_pitcher_recent_stats: {e}")
        return {}, (0.0, 1.0), (0.0, 1.0), (0.0, 1.0), (0.0, 1.0)


def get_bullpen_stats(reference_date, days_back=15):
    """
    Fetch bullpen (non-starter) hits allowed per team over the last N days.
    Returns (lookup, (mean, std)) where lookup = {team_abbr: total_H_allowed}.
    """
    end = reference_date - timedelta(days=1)
    start = end - timedelta(days=days_back)
    print(f"  [DEBUG] Fetching bullpen stats ({start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')})...")
    try:
        df = pitching_stats_range(start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d'))
        BAD = {'TOT', '2TM', '3TM', '---', ''}
        df = df[~df['Tm'].astype(str).str.strip().isin(BAD)].copy()
        if 'GS' in df.columns:
            df = df[df['GS'] == 0].copy()

        df['_team'] = df['Tm'].apply(_normalize_pitcher_team)
        df = df[df['_team'].isin(TEAM_TO_STADIUM)].copy()
        bullpen = df.groupby('_team')['H'].sum().to_dict()

        vals = list(bullpen.values())
        mean = float(pd.Series(vals).mean()) if len(vals) > 1 else 0.0
        std  = max(float(pd.Series(vals).std()) if len(vals) > 1 else 1.0, 0.1)

        print(f"  [DEBUG] Bullpen stats: {len(bullpen)} teams.")
        return bullpen, (mean, std)
    except Exception as e:
        print(f"  [DEBUG] ❌ CRASH in get_bullpen_stats: {e}")
        return {}, (0.0, 1.0)


def get_avg_batting_order(start_date, end_date):
    """
    Fetch statcast for the date window and compute each batter's average lineup slot.
    Uses batter mlbam ID (not player_name which is the pitcher) then reverse-looks up names.
    Returns a TUPLE: (batting_order_dict, pitcher_hand_dict)
      - batting_order_dict: { 'First Last': avg_order_float }
      - pitcher_hand_dict: { 'last_name_lower': 'L' or 'R' }
    """
    start_str = start_date.strftime('%Y-%m-%d')
    end_str = end_date.strftime('%Y-%m-%d')
    print(f"  [DEBUG] Fetching statcast for batting order ({start_str} to {end_str})...")
    try:
        df = statcast(start_str, end_str)

        needed = ['game_pk', 'batter', 'at_bat_number', 'n_priorpa_thisgame_player_at_bat', 'inning_topbot']
        if not all(c in df.columns for c in needed):
            print("  [DEBUG] ⚠️ Statcast missing expected columns for batting order.")
            return {}, {}

        # First PA of each batter per game = their lineup slot.
        # drop_duplicates is critical: n_priorpa==0 matches EVERY pitch in the first AB,
        # not just one row per batter. Without it the rank inflates (e.g. leadoff batter
        # with a 4-pitch AB gets ranks 1,2,3,4 → average 2.5 instead of 1).
        first_pa = df[df['n_priorpa_thisgame_player_at_bat'] == 0][
            ['game_pk', 'batter', 'at_bat_number', 'inning_topbot']
        ].drop_duplicates(subset=['game_pk', 'batter']).copy()

        # at_bat_number counts across both teams in a game, so rank within
        # (game, side) separately — otherwise home team leadoff shows up as ~10
        first_pa['game_side'] = first_pa['game_pk'].astype(str) + '_' + first_pa['inning_topbot']
        first_pa['lineup_pos'] = first_pa.groupby('game_side')['at_bat_number'].rank(method='first').astype(int)

        # Drop pinch hitters / late substitutes — keep only the starting 9 per team per game
        first_pa = first_pa[first_pa['lineup_pos'] <= 9]

        # Average lineup position per batter ID
        avg_by_id = first_pa.groupby('batter')['lineup_pos'].mean().round(1)

        # Reverse-lookup: mlbam ID → "First Last" name
        batter_ids = avg_by_id.index.tolist()
        id_df = playerid_reverse_lookup(batter_ids, key_type='mlbam')

        def ascii_normalize(s):
            return unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode()

        result = {}
        for _, row in id_df.iterrows():
            mlbam_id = row['key_mlbam']
            first = str(row['name_first']).strip().title()
            last = str(row['name_last']).strip().title()
            full_name = clean_name_string(f"{first} {last}")
            if mlbam_id in avg_by_id.index:
                val = float(avg_by_id[mlbam_id])
                result[full_name] = val
                # Also store ASCII-stripped key so accented names (e.g. Vázquez) still match
                ascii_key = ascii_normalize(full_name)
                if ascii_key != full_name:
                    result[ascii_key] = val

        print(f"  [DEBUG] Got avg batting order for {len(result)} players.")

        # Build pitcher hand lookup from statcast player_name (pitcher) and p_throws
        pitcher_hand = {}
        if 'player_name' in df.columns and 'p_throws' in df.columns:
            ph_df = df[['player_name', 'p_throws']].dropna().drop_duplicates('player_name')
            for _, ph_row in ph_df.iterrows():
                raw = ph_row['player_name']
                if ',' in raw:
                    last = raw.split(',')[0].strip().lower()
                    pitcher_hand[last] = ph_row['p_throws']

        print(f"  [DEBUG] Got pitcher hand for {len(pitcher_hand)} pitchers.")
        return result, pitcher_hand
    except Exception as e:
        print(f"  [DEBUG] ❌ CRASH in get_avg_batting_order: {e}")
        return {}, {}


def get_hit_streaks(reference_date, days_back=30):
    """
    Returns {player_name: consecutive_game_hit_streak} counting backwards from
    the day before reference_date. Skips days the batter didn't play (off days
    don't break a streak — only a hitless appearance does).
    """
    end = reference_date - timedelta(days=1)
    start = end - timedelta(days=days_back)
    start_str = start.strftime('%Y-%m-%d')
    end_str = end.strftime('%Y-%m-%d')
    print(f"  [DEBUG] Fetching hit streaks ({start_str} to {end_str})...")
    try:
        df = statcast(start_str, end_str)
        if df.empty:
            return {}

        hit_events = {'single', 'double', 'triple', 'home_run'}
        finished = df[df['events'].notna()][['batter', 'game_date', 'events']].copy()
        finished['game_date'] = pd.to_datetime(finished['game_date'])

        # Per (batter, game_date): True if they got at least one hit
        game_hits = (
            finished.groupby(['batter', 'game_date'])['events']
            .apply(lambda evts: any(e in hit_events for e in evts))
            .reset_index()
        )
        game_hits.columns = ['batter', 'game_date', 'got_hit']

        # Count consecutive hit games from most recent appearance backwards
        streaks = {}
        for batter_id, grp in game_hits.groupby('batter'):
            games = grp.sort_values('game_date', ascending=False)
            streak = 0
            for _, g in games.iterrows():
                if g['got_hit']:
                    streak += 1
                else:
                    break  # hitless game ends the streak
            if streak > 0:
                streaks[int(batter_id)] = streak

        if not streaks:
            return {}

        id_df = playerid_reverse_lookup(list(streaks.keys()), key_type='mlbam')

        def ascii_normalize(s):
            return unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode()

        result = {}
        for _, row in id_df.iterrows():
            mlbam_id = int(row['key_mlbam'])
            first = str(row['name_first']).strip().title()
            last = str(row['name_last']).strip().title()
            full_name = clean_name_string(f"{first} {last}")
            s = streaks.get(mlbam_id, 0)
            if s > 0:
                result[full_name] = s
                ascii_key = ascii_normalize(full_name)
                if ascii_key != full_name:
                    result[ascii_key] = s

        print(f"  [DEBUG] Hit streaks: {len(result)} players with active streaks.")
        return result
    except Exception as e:
        print(f"  [DEBUG] ❌ CRASH in get_hit_streaks: {e}")
        return {}


def get_hit_results(game_date):
    """
    For a past game date, returns {player_name: True/False} indicating whether
    each batter got at least one hit. Uses statcast events column.
    Also stores ASCII-normalized keys for accent-insensitive matching.
    """
    date_str = game_date.strftime('%Y-%m-%d')
    print(f"  [DEBUG] Fetching hit results for {date_str}...")
    try:
        df = statcast(date_str, date_str)
        if df.empty:
            print("  [DEBUG] No statcast data for this date.")
            return {}

        hit_events = {'single', 'double', 'triple', 'home_run'}
        # Only look at rows where an AB/PA ended (events is not null)
        finished = df[df['events'].notna()][['batter', 'events']].copy()
        hit_batters = set(finished[finished['events'].isin(hit_events)]['batter'].dropna().astype(int).unique())
        all_batter_ids = list(finished['batter'].dropna().astype(int).unique())

        if not all_batter_ids:
            return {}

        id_df = playerid_reverse_lookup(all_batter_ids, key_type='mlbam')

        def ascii_normalize(s):
            return unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode()

        result = {}
        for _, row in id_df.iterrows():
            mlbam_id = int(row['key_mlbam'])
            first = str(row['name_first']).strip().title()
            last = str(row['name_last']).strip().title()
            full_name = clean_name_string(f"{first} {last}")
            got_hit = mlbam_id in hit_batters
            result[full_name] = got_hit
            ascii_key = ascii_normalize(full_name)
            if ascii_key != full_name:
                result[ascii_key] = got_hit

        hits = sum(1 for v in result.values() if v)
        print(f"  [DEBUG] Hit results: {hits} hits / {len(all_batter_ids)} batters.")
        return result
    except Exception as e:
        print(f"  [DEBUG] ❌ CRASH in get_hit_results: {e}")
        return {}


def clean_name_string(name):
    if not isinstance(name, str): return name
    name = name.replace("\\'", "'")
    # pybaseball sometimes returns literal \xhh escape sequences instead of unicode
    if '\\x' in name:
        try:
            name = re.sub(r'\\x([0-9a-fA-F]{2})', lambda m: chr(int(m.group(1), 16)), name)
            name = name.encode('latin1').decode('utf-8')
        except:
            pass
    else:
        try:
            name = name.encode('latin1').decode('utf-8')
        except:
            pass
    return name


def get_window_stats(days_back, reference_date):
    """
    Robust fetcher: Tries specific range, falls back to Season stats.
    Standardizes column names (Team -> Tm) so logic never crashes.
    """
    end_date = reference_date - timedelta(days=1)
    start_date = end_date - timedelta(days=days_back)
    start_str = start_date.strftime('%Y-%m-%d')
    end_str = end_date.strftime('%Y-%m-%d')

    print(f"  [DEBUG] Attempting to fetch specific range: {start_str} to {end_str}")

    df = pd.DataFrame()

    try:
        # ATTEMPT 1: Granular Date Range (Baseball-Reference)
        df = batting_stats_range(start_str, end_str)
        if df.empty: raise ValueError("Empty Dataframe")
        print(f"  [DEBUG] ✅ Success! Found granular data for {days_back}d window.")

    except (IndexError, ValueError, Exception) as e:
        # ATTEMPT 2: Fallback to Full Season (FanGraphs)
        print(f"  [DEBUG] ⚠️ Range fetch failed ({e}). Falling back to FULL SEASON stats.")
        try:
            df = batting_stats(SEASON)
            print(f"  [DEBUG] ✅ Fallback successful. Fetched full season stats.")
        except Exception as e2:
            print(f"  [DEBUG] ❌ CRASH: Even Season stats failed: {e2}")
            return pd.DataFrame()

    # --- STANDARDIZATION ---
    if 'Team' in df.columns and 'Tm' not in df.columns:
        df = df.rename(columns={'Team': 'Tm'})

    if 'AVG' in df.columns and 'BA' not in df.columns:
        df = df.rename(columns={'AVG': 'BA'})

    if 'Name' in df.columns:
        df['Name'] = df['Name'].apply(clean_name_string)

    if 'K%' not in df.columns and 'PA' in df.columns and 'SO' in df.columns:
        df['K%'] = (df['SO'] / df['PA']) * 100
    elif 'K%' in df.columns:
        if df['K%'].dtype == 'object':
            df['K%'] = df['K%'].astype(str).str.replace('%', '').astype(float)
        # FanGraphs returns K% as a proportion (0.185 = 18.5%); Baseball-Reference
        # already gives percentages (18.5). Normalise to percentage form.
        if not df['K%'].dropna().empty and df['K%'].dropna().max() < 1.5:
            df['K%'] = df['K%'] * 100

    return df


def get_player_metadata(season):
    print(f"  [DEBUG] Fetching player metadata for {season}...")
    try:
        df = batting_stats(season)
        if 'Name' in df.columns:
            df['Name'] = df['Name'].apply(clean_name_string)

        # Standardize Team
        if 'Team' in df.columns: df = df.rename(columns={'Team': 'Tm'})

        # DEFENSIVE COLUMN SELECTION (The Fix)
        # We only ask for columns that actually exist.
        cols_to_keep = ['Name', 'Tm']
        # FanGraphs 'Pos' is the positional adjustment in runs (a float), not position name — skip it
        if 'Bats' in df.columns: cols_to_keep.append('Bats')

        return df[cols_to_keep]

    except Exception as e:
        print(f"  [DEBUG] ❌ CRASH in get_player_metadata: {e}")
        return pd.DataFrame()


def get_matchup_info(team_name, game_date):
    clean_name = team_name.strip()
    bref_team = NAME_TO_ABBR.get(clean_name, clean_name)

    if len(bref_team) > 3: return "Unknown", "Unknown", "", True

    try:
        schedule = schedule_and_record(SEASON, bref_team)
        short_date = game_date.strftime("%b %-d")
        game = schedule[schedule['Date'].astype(str).str.contains(short_date, case=False)]

        if not game.empty:
            row = game.iloc[0]
            opponent = BREF_OPP_NORM.get(row['Opp'], row['Opp'])
            wl = str(row.get('W/L', ''))
            if 'W' in wl:
                pitcher = row.get('Loss', None)
            elif 'L' in wl:
                pitcher = row.get('Win', None)
            else:
                pitcher = None
            pitcher = str(pitcher) if pitcher and str(pitcher) not in ('None', 'nan') else 'TBD'

            # Game time — BRef 'Time' column shows scheduled start time (e.g. "7:10 PM")
            # for future games, but game duration (e.g. "2:34") for completed ones.
            # Only use it if it has AM/PM, meaning it's actually a start time.
            raw_time = str(row.get('Time', '')).strip()
            if raw_time and ('PM' in raw_time or 'AM' in raw_time):
                game_time = f"{raw_time} ET"
            else:
                game_time = ''

            # Home/away — BRef uses an unnamed column with '@' for away games
            ha_col = next(
                (c for c in schedule.columns if schedule[c].astype(str).str.strip().eq('@').any()),
                None
            )
            is_home = True
            if ha_col is not None:
                is_home = str(row.get(ha_col, '')).strip() != '@'

            return opponent, pitcher, game_time, is_home
        return "Off Day", "N/A", "", True
    except:
        return "Unknown", "Unknown", "", True


def get_lineup_status(game_date):
    """
    Fetches confirmed starting lineups from the MLB Stats API for game_date.
    Returns (confirmed_names, lineup_posted_teams) where:
      confirmed_names    : {player_full_name: True} for every confirmed starter
      lineup_posted_teams: set of team abbreviations whose lineup has been posted
    Returns ({}, set()) if no lineups are posted yet or on error.
    """
    date_str = game_date.strftime('%Y-%m-%d')
    url = (
        f"https://statsapi.mlb.com/api/v1/schedule"
        f"?sportId=1&date={date_str}&hydrate=lineups"
    )
    print(f"  [DEBUG] Fetching lineup status for {date_str}...")
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        confirmed_names = {}
        lineup_posted_teams = set()

        for date_obj in data.get('dates', []):
            for game in date_obj.get('games', []):
                lineups = game.get('lineups', {})
                home_players = lineups.get('homePlayers', [])
                away_players = lineups.get('awayPlayers', [])

                home_abbr = (game.get('teams', {}).get('home', {})
                             .get('team', {}).get('abbreviation', ''))
                away_abbr = (game.get('teams', {}).get('away', {})
                             .get('team', {}).get('abbreviation', ''))

                if home_players:
                    lineup_posted_teams.add(home_abbr)
                    for p in home_players:
                        name = p.get('fullName', '')
                        if name:
                            confirmed_names[name] = True

                if away_players:
                    lineup_posted_teams.add(away_abbr)
                    for p in away_players:
                        name = p.get('fullName', '')
                        if name:
                            confirmed_names[name] = True

        print(f"  [DEBUG] Lineup status: {len(lineup_posted_teams)} teams posted "
              f"({len(confirmed_names)} starters confirmed).")
        return confirmed_names, lineup_posted_teams
    except Exception as e:
        print(f"  [DEBUG] ❌ CRASH in get_lineup_status: {e}")
        return {}, set()


def get_predictions(simulation_date):
    print(f"\n--- 🕵️‍♂️ STARTING DEBUG ANALYSIS FOR: {simulation_date.strftime('%Y-%m-%d')} ---")

    df_4d = get_window_stats(4, simulation_date)
    df_7d = get_window_stats(7, simulation_date)

    if df_7d.empty:
        print("  [DEBUG] 🛑 STOPPING: 7-Day Data is empty.")
        return [], []

        # Filter candidates — 15 PA minimum to exclude backup/callup noise
    # while staying usable in the first week (3 games × ~4 PA = 12 PA for a starter)
    candidates = df_7d[(df_7d['PA'] >= 15)].copy()

    if 'K%' in candidates.columns:
        candidates = candidates[(candidates['K%'] < 25.0)].copy()

    candidates['Score'] = 0.0
    candidates['Score_Breakdown'] = ''

    # Fetch batting order early so it can influence scoring
    order_end = simulation_date - timedelta(days=1)
    order_start = order_end - timedelta(days=7)
    batting_order_lookup, pitcher_hand_lookup = get_avg_batting_order(order_start, order_end)

    # Hot Teams Logic — rank all teams by runs scored in the 7-day window
    hot_teams = []
    team_rank = {}
    if 'R' in df_7d.columns:
        team_stats = df_7d.groupby('Tm')['R'].sum().sort_values(ascending=False)
        hot_teams = team_stats.head(10).index.tolist()
        team_rank_raw = {team: rank + 1 for rank, team in enumerate(team_stats.index)}
        # BRef groupby keys are full city names ('San Diego') — normalize to abbreviations
        team_rank = {NAME_TO_ABBR.get(t, FG_TO_BREF_TEAM.get(t, t)): r for t, r in team_rank_raw.items()}

    # Projected weekly hit rate (H/G × 7) — normalizes BRef 7-day AND FanGraphs season
    # fallback to the same scale, and handles first week / All-Star gaps cleanly.
    if 'H' in candidates.columns and 'G' in candidates.columns:
        h_proj = (candidates['H'] / candidates['G'].clip(lower=1)) * 7
        h_mean = float(h_proj.mean()) if not h_proj.empty else 6.0
        h_std  = float(h_proj.std())  if len(h_proj) > 1   else 2.0
        if h_std == 0 or pd.isna(h_std):
            h_std = 2.0
    else:
        h_mean, h_std = 6.0, 2.0

    # K% z-score parameters — lower K% is better, so z-score is inverted in display
    if 'K%' in candidates.columns:
        _kpct_vals = candidates['K%'].dropna()
        kpct_mean = float(_kpct_vals.mean()) if not _kpct_vals.empty else 20.0
        kpct_std  = float(_kpct_vals.std())  if len(_kpct_vals) > 1   else 5.0
        if kpct_std == 0 or pd.isna(kpct_std):
            kpct_std = 5.0
    else:
        kpct_mean, kpct_std = 20.0, 5.0

    for index, row in candidates.iterrows():
        hits = float(row['H']) if 'H' in row and pd.notna(row['H']) else 0.0
        games = max(float(row['G']) if 'G' in row and pd.notna(row['G']) else 1.0, 1.0)
        h_proj = (hits / games) * 7  # projected hits in a 7-game week
        h_z = round((h_proj - h_mean) / h_std, 1)

        score = h_proj
        breakdown = [f"H: {round(h_proj, 1)} ({h_z:+.1f}σ)"]

        # Always show actual K% value with z-score; bonus still applied if < 12%
        if 'K%' in row and pd.notna(row['K%']):
            k_pct = float(row['K%'])
            k_z = round((k_pct - kpct_mean) / kpct_std, 1)
            breakdown.append(f"K%: {round(k_pct, 1)}% ({k_z:+.1f}σ)")
            if k_pct < 12.0:
                score += 10

        # Show team's offensive rank among all teams; bonus still applied if top 10
        rank = team_rank.get(str(row['Tm']).strip())
        if rank is not None:
            breakdown.append(f"Team Rank: #{rank}")
        if row['Tm'] in hot_teams:
            score += 10

        avg_order = batting_order_lookup.get(row['Name']) or batting_order_lookup.get(
            unicodedata.normalize('NFKD', row['Name']).encode('ascii', 'ignore').decode()
        )
        if avg_order is not None:
            bonus = round(max(0.0, (9 - avg_order) / 8 * 10), 1)
            score += bonus
            breakdown.append(f"Lineup: #{avg_order}")

        candidates.at[index, 'Score'] = score
        candidates.at[index, 'Score_Breakdown'] = ' | '.join(breakdown)

    top_picks = candidates.sort_values('Score', ascending=False).head(20).copy()

    # Merge Metadata (Defensive)
    meta = get_player_metadata(SEASON)
    if not meta.empty:
        # Only merge on columns that exist in the metadata
        cols_to_merge = [c for c in meta.columns if c in ['Name', 'Pos', 'Bats']]
        merged = pd.merge(top_picks, meta[cols_to_merge], on='Name', how='left')
    else:
        merged = top_picks

    # Build team fallback from FanGraphs metadata (has current team for traded players)
    BAD_TEAMS = {'- - -', '---', 'TOT', '2TM', '3TM', ''}
    team_fallback = {}
    if not meta.empty and 'Tm' in meta.columns:
        for _, mrow in meta.iterrows():
            t = str(mrow.get('Tm', '')).strip()
            if t.replace('-', '').replace(' ', '') not in BAD_TEAMS:
                team_fallback[mrow['Name']] = t

    # Cities with two MLB teams — need FanGraphs metadata to disambiguate
    AMBIGUOUS_CITIES = {'Chicago', 'Los Angeles', 'New York'}

    # Fix "---" / "TOT" / "2TM" team values, convert BRef full names to abbreviations,
    # and disambiguate shared-city teams using FanGraphs metadata.
    def resolve_team(name, team):
        t = str(team).strip()
        if t.replace('-', '').replace(' ', '') in BAD_TEAMS or '---' in t:
            return team_fallback.get(name, t)
        # Ambiguous city → use FanGraphs abbreviation (CHC/CWS, LAD/LAA, NYY/NYM)
        if t in AMBIGUOUS_CITIES:
            fg_abbr = team_fallback.get(name, '')
            if fg_abbr:
                return FG_TO_BREF_TEAM.get(fg_abbr, fg_abbr)
        # Full BRef name (e.g. 'Seattle', 'San Diego') → abbreviation
        return NAME_TO_ABBR.get(t, FG_TO_BREF_TEAM.get(t, t))

    merged['Tm'] = merged.apply(lambda r: resolve_team(r['Name'], r['Tm']), axis=1)

    # Batch schedule lookups — one HTTP request per unique team, not per player
    unique_teams = merged['Tm'].dropna().unique().tolist()
    matchup_cache = {team: get_matchup_info(team, simulation_date) for team in unique_teams}  # → (opp, pitcher, time, is_home)

    # One FanGraphs call for all pitcher stats
    pitcher_lookup = get_pitcher_stats(SEASON)

    # Pitcher recent form (~4 starts) and bullpen stats
    pitcher_recent, ph_params, pera_params, pbb_params, pip_params = get_pitcher_recent_stats(simulation_date)
    bullpen_stats, bp_params = get_bullpen_stats(simulation_date)

    # Consecutive game hit streaks
    hit_streak_lookup = get_hit_streaks(simulation_date)

    final_a_list = []
    final_b_list = []

    for index, row in merged.iterrows():
        name = row['Name']
        team = row['Tm']

        # Use .get() to avoid KeyErrors if columns are missing
        pos = row.get('Pos', 'Unknown')
        if pd.isna(pos): pos = 'Unknown'

        bats = row.get('Bats', 'R')
        if pd.isna(bats): bats = 'R'

        reason_for_demotion = []
        if bats == 'L': reason_for_demotion.append("Lefty Batter")

        opponent, pitcher, game_time, is_home = matchup_cache.get(team, ("Unknown", "Unknown", "", True))

        # Park factor belongs to the home team's stadium.
        # team is already a clean abbreviation after resolve_team().
        park_team = team if is_home else opponent
        park_factor = PARK_FACTORS.get(park_team, 100)
        stadium = TEAM_TO_STADIUM.get(park_team, '')

        # Look up pitcher ERA/WHIP — try (last_name, team) then (last_name, any team)
        pitcher_era = None
        pitcher_whip = None
        if pitcher and pitcher not in ('TBD', 'Unknown', 'N/A'):
            last_name = pitcher.split()[-1].lower()
            stats = pitcher_lookup.get((last_name, opponent))
            if not stats:
                stats = next((v for k, v in pitcher_lookup.items() if k[0] == last_name), None)
            if stats:
                pitcher_era = stats.get('ERA')
                pitcher_whip = stats.get('WHIP')

        # Pitcher throwing hand from statcast
        pitcher_hand = ''
        if pitcher and pitcher not in ('TBD', 'Unknown', 'N/A'):
            last_name = pitcher.split()[-1].lower()
            pitcher_hand = pitcher_hand_lookup.get(last_name, '')

        # Park factor — display only, not factored into score
        score_breakdown = str(row.get('Score_Breakdown', ''))
        pf_label = f"Park: {park_factor}"
        score_breakdown = f"{score_breakdown} | {pf_label}" if score_breakdown else pf_label

        # Opposing pitcher recent stats (~4 starts) — display only
        if pitcher and pitcher not in ('TBD', 'Unknown', 'N/A'):
            p_last = pitcher.split()[-1].lower()
            p_stats = pitcher_recent.get((p_last, opponent)) or next(
                (v for k, v in pitcher_recent.items() if k[0] == p_last), None
            )
            if p_stats:
                def _pz(val, params):
                    if val is None or pd.isna(val): return None
                    return round((float(val) - params[0]) / params[1], 1)

                ph  = p_stats.get('H');   phz  = _pz(ph,  ph_params)
                pera = p_stats.get('ERA'); peraz = _pz(pera, pera_params)
                pbb  = p_stats.get('BB');  pbbz  = _pz(pbb,  pbb_params)
                pip  = p_stats.get('IP');  pipz  = _pz(pip,  pip_params)

                parts = []
                if ph   is not None and phz  is not None: parts.append(f"P.H: {int(ph)} ({phz:+.1f}σ)")
                if pera is not None and peraz is not None: parts.append(f"P.ERA: {float(pera):.2f} ({peraz:+.1f}σ)")
                if pbb  is not None and pbbz  is not None: parts.append(f"P.BB: {int(pbb)} ({pbbz:+.1f}σ)")
                if pip  is not None and pipz  is not None: parts.append(f"P.IP: {float(pip):.1f} ({pipz:+.1f}σ)")
                if parts:
                    score_breakdown += ' | ' + ' | '.join(parts)

        # Bullpen hits allowed (opponent team, last 15 days) — display only
        bp_h = bullpen_stats.get(opponent)
        if bp_h is not None:
            bp_z = round((float(bp_h) - bp_params[0]) / bp_params[1], 1)
            score_breakdown += f" | BP: {int(bp_h)} ({bp_z:+.1f}σ)"

        avg_order = batting_order_lookup.get(name) or batting_order_lookup.get(
            unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode()
        )

        hit_streak = hit_streak_lookup.get(name) or hit_streak_lookup.get(
            unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode(), 0
        ) or 0

        player_info = {
            'Name': name,
            'Team': team,
            'Pos': pos,
            'Bats': bats,
            'Score': round(row['Score'], 1),
            'Opponent': opponent,
            'Probable_Pitcher': pitcher,
            'Pitcher_ERA': pitcher_era,
            'Pitcher_WHIP': pitcher_whip,
            'Avg_Order': avg_order,
            'Notes': ", ".join(reason_for_demotion),
            'Pitcher_Hand': pitcher_hand,
            'Score_Breakdown': score_breakdown,
            'Stadium': stadium,
            'Game_Time': game_time,
            'Park_Factor': park_factor,
            'Is_Home': is_home,
            'Team_Rank': team_rank.get(str(team).strip()),
            'Hit_Streak': hit_streak,
        }

        if reason_for_demotion:
            final_b_list.append(player_info)
        else:
            final_a_list.append(player_info)

    print(f"  [DEBUG] Done. A-List: {len(final_a_list)}, B-List: {len(final_b_list)}")
    return final_a_list, final_b_list
