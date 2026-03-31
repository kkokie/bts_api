import re
import unicodedata
import requests
import pandas as pd
from pybaseball import batting_stats_range, batting_stats, schedule_and_record, pitching_stats, pitching_stats_range, statcast, playerid_reverse_lookup
from datetime import datetime, timedelta, date as date_class, timezone

# --- CONFIGURATION ---
SEASON = 2026

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

# MLB Stats API returns full team names — map to our internal abbreviations
MLB_FULLNAME_TO_ABBR = {
    'Arizona Diamondbacks': 'ARI', 'Atlanta Braves': 'ATL', 'Baltimore Orioles': 'BAL',
    'Boston Red Sox': 'BOS', 'Chicago White Sox': 'CWS', 'Chicago Cubs': 'CHC',
    'Cincinnati Reds': 'CIN', 'Cleveland Guardians': 'CLE', 'Colorado Rockies': 'COL',
    'Detroit Tigers': 'DET', 'Houston Astros': 'HOU', 'Kansas City Royals': 'KC',
    'Los Angeles Angels': 'LAA', 'Los Angeles Dodgers': 'LAD', 'Miami Marlins': 'MIA',
    'Milwaukee Brewers': 'MIL', 'Minnesota Twins': 'MIN', 'New York Yankees': 'NYY',
    'New York Mets': 'NYM', 'Oakland Athletics': 'OAK', 'Athletics': 'OAK',
    'Philadelphia Phillies': 'PHI', 'Pittsburgh Pirates': 'PIT', 'San Diego Padres': 'SD',
    'San Francisco Giants': 'SF', 'Seattle Mariners': 'SEA', 'St. Louis Cardinals': 'STL',
    'Tampa Bay Rays': 'TB', 'Texas Rangers': 'TEX', 'Toronto Blue Jays': 'TOR',
    'Washington Nationals': 'WSH',
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
    """Fetch season pitcher stats and return a lookup dict keyed by (last_name, team).
    Falls back to season-1 if current season data is unavailable (e.g. pre-season)."""
    for try_season in [season, season - 1]:
        print(f"  [DEBUG] Fetching pitcher stats for {try_season}...")
        try:
            df = pitching_stats(try_season, qual=1)
            if df.empty:
                print(f"  [DEBUG] ⚠️ pitching_stats({try_season}) returned empty, trying fallback.")
                continue

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

            print(f"  [DEBUG] Loaded stats for {len(lookup)} pitchers (season {try_season}).")
            return lookup
        except Exception as e:
            print(f"  [DEBUG] ⚠️ pitcher stats for {try_season} failed: {e}")
            continue
    print(f"  [DEBUG] ❌ Could not fetch pitcher stats for {season} or {season - 1}.")
    return {}


def _normalize_pitcher_team(raw):
    """Normalize BRef pitching Tm column (full names or abbrevs) to internal abbreviations."""
    t = str(raw).strip()
    return NAME_TO_ABBR.get(t, FG_TO_BREF_TEAM.get(t, t))


def get_pitcher_recent_stats(reference_date, days_back=28):
    """
    Fetch starter pitching stats over a ~28-day window (~4 starts).
    Falls back to full-season stats if the range returns too few starters
    (e.g. early in the season when the window hits spring training).
    Returns (lookup, h_params, era_params, bb_params, ip_params)
      lookup: {(last_name_lower, team_abbr): {H, ERA, BB, IP}}
      *_params: (mean, std) for z-scoring across the pitcher pool.
    """
    end = reference_date - timedelta(days=1)
    start = end - timedelta(days=days_back)
    print(f"  [DEBUG] Fetching pitcher recent stats ({start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')})...")

    def _build_lookup(df):
        # FanGraphs uses 'Team', BRef range uses 'Tm' — handle both
        if 'Tm' not in df.columns and 'Team' in df.columns:
            df = df.rename(columns={'Team': 'Tm'})
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
            lookup[(last_name, team)] = {
                'H':   row.get('H'),
                'ERA': row.get('ERA'),
                'BB':  row.get('BB'),
                'IP':  row.get('IP'),
            }
        return lookup

    def _params(lookup, stat):
        vals = [v[stat] for v in lookup.values()
                if v[stat] is not None and not pd.isna(v[stat])]
        if len(vals) < 2:
            return (0.0, 1.0)
        s = pd.Series(vals)
        return (float(s.mean()), max(float(s.std()), 0.1))

    # Attempt 1: date range
    lookup = {}
    try:
        df = pitching_stats_range(start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d'))
        lookup = _build_lookup(df)
        print(f"  [DEBUG] Pitcher recent stats (range): {len(lookup)} starters.")
    except Exception as e:
        print(f"  [DEBUG] ⚠️ Pitcher range fetch failed ({e}).")

    # Fallback: full season if range returned too few starters
    if len(lookup) < 10:
        print(f"  [DEBUG] ⚠️ Too few pitchers from range, falling back to full season stats.")
        for try_season in [reference_date.year, reference_date.year - 1]:
            try:
                df = pitching_stats(try_season)
                if not df.empty:
                    lookup = _build_lookup(df)
                    print(f"  [DEBUG] ✅ Pitcher fallback: {len(lookup)} starters from {try_season} season.")
                    break
            except Exception as e2:
                print(f"  [DEBUG] ⚠️ Pitcher season {try_season} fallback failed: {e2}")

    return lookup, _params(lookup, 'H'), _params(lookup, 'ERA'), _params(lookup, 'BB'), _params(lookup, 'IP')


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

        # --- xBA (expected batting average on contact, from Statcast) ---
        xba_lookup = {}
        if 'estimated_ba_using_speedangle' in df.columns:
            xba_data = df[df['estimated_ba_using_speedangle'].notna()][
                ['batter', 'estimated_ba_using_speedangle']
            ].copy()
            xba_by_id = xba_data.groupby('batter')['estimated_ba_using_speedangle'].mean()
            for _, row in id_df.iterrows():
                mlbam_id = row['key_mlbam']
                first = str(row['name_first']).strip().title()
                last  = str(row['name_last']).strip().title()
                full_name = clean_name_string(f"{first} {last}")
                if mlbam_id in xba_by_id.index:
                    val = float(xba_by_id[mlbam_id])
                    xba_lookup[full_name] = val
                    ascii_key = ascii_normalize(full_name)
                    if ascii_key != full_name:
                        xba_lookup[ascii_key] = val
        print(f"  [DEBUG] Got xBA for {len(xba_lookup)} players.")

        # --- Hard Hit Rate (exit velocity >= 95 mph) ---
        hard_hit_lookup = {}
        if 'launch_speed' in df.columns:
            batted = df[df['launch_speed'].notna()][['batter', 'launch_speed']].copy()
            batted['hard_hit'] = batted['launch_speed'] >= 95
            hh_by_id = batted.groupby('batter')['hard_hit'].mean()
            for _, row in id_df.iterrows():
                mlbam_id = row['key_mlbam']
                first = str(row['name_first']).strip().title()
                last  = str(row['name_last']).strip().title()
                full_name = clean_name_string(f"{first} {last}")
                if mlbam_id in hh_by_id.index:
                    val = float(hh_by_id[mlbam_id])
                    hard_hit_lookup[full_name] = val
                    ascii_key = ascii_normalize(full_name)
                    if ascii_key != full_name:
                        hard_hit_lookup[ascii_key] = val
        print(f"  [DEBUG] Got hard hit rate for {len(hard_hit_lookup)} players.")

        # --- Batter handedness from statcast (stand column: 'L' or 'R') ---
        # Reliable fallback when FanGraphs season data is unavailable (e.g. pre-season)
        batter_hand_lookup = {}
        if 'stand' in df.columns:
            stand_df = df[['batter', 'stand']].dropna().drop_duplicates('batter')
            hand_by_id = stand_df.set_index('batter')['stand'].to_dict()
            for _, row in id_df.iterrows():
                mlbam_id = row['key_mlbam']
                if mlbam_id in hand_by_id:
                    first = str(row['name_first']).strip().title()
                    last  = str(row['name_last']).strip().title()
                    full_name = clean_name_string(f"{first} {last}")
                    val = hand_by_id[mlbam_id]
                    batter_hand_lookup[full_name] = val
                    ascii_key = ascii_normalize(full_name)
                    if ascii_key != full_name:
                        batter_hand_lookup[ascii_key] = val
        print(f"  [DEBUG] Got batter handedness for {len(batter_hand_lookup)} players.")

        return result, pitcher_hand, xba_lookup, hard_hit_lookup, batter_hand_lookup
    except Exception as e:
        print(f"  [DEBUG] ❌ CRASH in get_avg_batting_order: {e}")
        return {}, {}, {}, {}, {}


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
        # Only count regular season games — exclude spring training (game_type='S'), playoffs, etc.
        if 'game_type' in df.columns:
            df = df[df['game_type'] == 'R']
        if df.empty:
            return {}
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
        # ATTEMPT 2 & 3: Fallback to Full Season (FanGraphs), try date's year then prior year
        print(f"  [DEBUG] ⚠️ Range fetch failed ({e}). Falling back to FULL SEASON stats.")
        df = pd.DataFrame()
        for try_season in [reference_date.year, reference_date.year - 1]:
            try:
                df = batting_stats(try_season)
                if not df.empty:
                    print(f"  [DEBUG] ✅ Fallback successful with {try_season} season stats.")
                    break
            except Exception as e2:
                print(f"  [DEBUG] ⚠️ Season {try_season} fallback failed: {e2}")
        if df.empty:
            print(f"  [DEBUG] ❌ CRASH: All season fallbacks failed.")
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
    """Fetch player metadata (Name, Team, Bats). Falls back to season-1 if unavailable."""
    for try_season in [season, season - 1]:
        print(f"  [DEBUG] Fetching player metadata for {try_season}...")
        try:
            df = batting_stats(try_season)
            if df.empty:
                print(f"  [DEBUG] ⚠️ batting_stats({try_season}) returned empty, trying fallback.")
                continue
            if 'Name' in df.columns:
                df['Name'] = df['Name'].apply(clean_name_string)
            if 'Team' in df.columns:
                df = df.rename(columns={'Team': 'Tm'})
            cols_to_keep = ['Name', 'Tm']
            if 'Bats' in df.columns:
                cols_to_keep.append('Bats')
            print(f"  [DEBUG] Player metadata loaded (season {try_season}, has Bats: {'Bats' in df.columns}).")
            return df[cols_to_keep]
        except Exception as e:
            print(f"  [DEBUG] ⚠️ player metadata for {try_season} failed: {e}")
            continue
    print(f"  [DEBUG] ❌ Could not fetch player metadata for {season} or {season - 1}.")
    return pd.DataFrame()


def get_matchup_info(team_name, game_date):
    clean_name = team_name.strip()
    bref_team = NAME_TO_ABBR.get(clean_name, clean_name)

    if len(bref_team) > 3: return "Unknown", "Unknown", "", True

    try:
        schedule = schedule_and_record(game_date.year, bref_team)
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
    except Exception as e:
        print(f"  [DEBUG] ❌ get_matchup_info({team_name}, {game_date.date()}): {e}")
        return "Unknown", "Unknown", "", True


def _mlb_team_name_to_abbr(name):
    """Convert MLB Stats API full team name to our internal abbreviation."""
    return MLB_FULLNAME_TO_ABBR.get(name, NAME_TO_ABBR.get(name, name))


def _get_boxscore_starters(game_pk):
    """Fetch actual starting pitchers for a completed game via MLB Stats API boxscore."""
    try:
        url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
        data = requests.get(url, timeout=10).json()
        result = {}
        for side in ('home', 'away'):
            team_data = data.get('teams', {}).get(side, {})
            pitchers = team_data.get('pitchers', [])
            if pitchers:
                starter_id = pitchers[0]
                player = team_data.get('players', {}).get(f'ID{starter_id}', {})
                name = player.get('person', {}).get('fullName', '')
                hand_code = (player.get('person', {}).get('pitchHand', {}) or {}).get('code', '')
                if hand_code == 'S':
                    hand_code = ''
                team_name = data.get('teams', {}).get(side, {}).get('team', {}).get('name', '')
                abbr = _mlb_team_name_to_abbr(team_name)
                if abbr and name:
                    result[abbr] = (name, hand_code)
        return result
    except Exception:
        return {}


def get_all_matchups(game_date):
    """
    Fetch all matchups for a date from the MLB Stats API.
    - Future/today: uses probablePitcher field
    - Past games: fetches boxscore for actual starting pitcher
    Returns {team_abbr: (opponent_abbr, pitcher_name, game_time, is_home)}.
    """
    date_str = game_date.strftime('%Y-%m-%d')
    is_past = game_date.date() < date_class.today()
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date_str}&hydrate=probablePitcher"
    print(f"  [DEBUG] Fetching all matchups from MLB API for {date_str}...")
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        result = {}
        for date_obj in data.get('dates', []):
            for game in date_obj.get('games', []):
                home_info = game.get('teams', {}).get('home', {})
                away_info = game.get('teams', {}).get('away', {})
                home_abbr = _mlb_team_name_to_abbr(home_info.get('team', {}).get('name', ''))
                away_abbr = _mlb_team_name_to_abbr(away_info.get('team', {}).get('name', ''))
                if not home_abbr or not away_abbr:
                    continue

                # Game time from gameDate (UTC ISO string)
                game_time = ''
                game_dt_str = game.get('gameDate', '')
                if game_dt_str:
                    try:
                        utc_dt = datetime.strptime(game_dt_str, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
                        et_dt = utc_dt + timedelta(hours=-4)  # EDT
                        game_time = et_dt.strftime('%-I:%M %p') + ' ET'
                    except Exception:
                        pass

                if is_past:
                    # For completed games, get actual starters from boxscore
                    starters = _get_boxscore_starters(game['gamePk'])
                    home_pitcher = starters.get(home_abbr, ('TBD', ''))[0]
                    away_pitcher = starters.get(away_abbr, ('TBD', ''))[0]
                else:
                    # For upcoming games, use announced probable pitchers
                    home_pitcher = (home_info.get('probablePitcher') or {}).get('fullName', 'TBD') or 'TBD'
                    away_pitcher = (away_info.get('probablePitcher') or {}).get('fullName', 'TBD') or 'TBD'

                result[home_abbr] = (away_abbr, away_pitcher, game_time, True)
                result[away_abbr] = (home_abbr, home_pitcher, game_time, False)

        print(f"  [DEBUG] MLB API matchups: {len(result) // 2} games found.")
        return result
    except Exception as e:
        print(f"  [DEBUG] ❌ CRASH in get_all_matchups: {e}")
        return {}


def get_probable_pitchers(game_date):
    """
    Fetches probable pitchers from MLB Stats API for game_date.
    Returns {team_abbr: (pitcher_full_name, pitcher_hand)} for both home and away teams.
    Batch-fetches pitcher hand from the people API since the schedule endpoint
    doesn't include pitchHand in the probablePitcher object.
    """
    date_str = game_date.strftime('%Y-%m-%d')
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date_str}&hydrate=probablePitcher"
    print(f"  [DEBUG] Fetching probable pitchers for {date_str}...")
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        # First pass: collect (team_abbr, pitcher_id, pitcher_name)
        entries = []
        for date_obj in data.get('dates', []):
            for game in date_obj.get('games', []):
                for side in ('home', 'away'):
                    team_info = game.get('teams', {}).get(side, {})
                    abbr = _mlb_team_name_to_abbr(team_info.get('team', {}).get('name', ''))
                    probable = team_info.get('probablePitcher') or {}
                    name = probable.get('fullName', '')
                    pid = probable.get('id')
                    if abbr and name and pid:
                        entries.append((abbr, pid, name))

        if not entries:
            print(f"  [DEBUG] Probable pitchers: 0 teams with pitchers announced.")
            return {}

        # Batch-fetch pitcher hand from people API (one call for all pitchers)
        all_ids = ','.join(str(e[1]) for e in entries)
        people_resp = requests.get(
            f"https://statsapi.mlb.com/api/v1/people?personIds={all_ids}",
            timeout=10
        )
        hand_by_id = {}
        if people_resp.ok:
            for person in people_resp.json().get('people', []):
                pid = person.get('id')
                hand_info = person.get('pitchHand', {})
                code = hand_info.get('code', '') if isinstance(hand_info, dict) else ''
                hand_by_id[pid] = '' if code == 'S' else code

        result = {}
        for abbr, pid, name in entries:
            hand = hand_by_id.get(pid, '')
            result[abbr] = (name, hand)

        print(f"  [DEBUG] Probable pitchers: {len(result)} teams with pitchers announced.")
        return result
    except Exception as e:
        print(f"  [DEBUG] ❌ CRASH in get_probable_pitchers: {e}")
        return {}


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

        # Filter candidates — dynamic PA minimum: 15 normally, 3 in first two weeks of season
    # when full-season fallback data has very low PA counts
    max_pa = df_7d['PA'].max() if 'PA' in df_7d.columns else 100
    pa_threshold = 3 if max_pa < 20 else 15
    candidates = df_7d[(df_7d['PA'] >= pa_threshold)].copy()

    if 'K%' in candidates.columns:
        candidates = candidates[(candidates['K%'] < 25.0)].copy()

    candidates['Score'] = 0.0
    candidates['Score_Breakdown'] = ''

    # Fetch batting order early so it can influence scoring
    order_end = simulation_date - timedelta(days=1)
    order_start = order_end - timedelta(days=7)
    batting_order_lookup, pitcher_hand_lookup, xba_lookup, hard_hit_lookup, batter_hand_lookup = get_avg_batting_order(order_start, order_end)

    # Hot Teams Logic — rank all teams by runs scored in the 7-day window
    hot_teams = []
    team_rank = {}
    if 'R' in df_7d.columns:
        team_stats = df_7d.groupby('Tm')['R'].sum().sort_values(ascending=False)
        hot_teams = team_stats.head(10).index.tolist()
        team_rank_raw = {team: rank + 1 for rank, team in enumerate(team_stats.index)}
        # BRef groupby keys are full city names ('San Diego') — normalize to abbreviations
        team_rank = {NAME_TO_ABBR.get(t, FG_TO_BREF_TEAM.get(t, t)): r for t, r in team_rank_raw.items()}

    # Build 4-day stats lookup for exponential-decay BA weighting
    ba_4d_lookup = {}
    if not df_4d.empty and 'Name' in df_4d.columns:
        for _, r4 in df_4d.iterrows():
            name_4d = str(r4.get('Name', '')).strip()
            if not name_4d:
                continue
            h4 = r4.get('H')
            ab4 = r4.get('AB')
            if h4 is not None and ab4 is not None and not pd.isna(h4) and not pd.isna(ab4):
                ba_4d_lookup[name_4d] = {'H': float(h4), 'AB': float(ab4)}

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
        name = str(row.get('Name', ''))
        ascii_name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode()

        hits  = float(row['H'])  if 'H'  in row and pd.notna(row['H'])  else 0.0
        ab_7d = float(row['AB']) if 'AB' in row and pd.notna(row['AB']) else max(hits / 0.270, 1.0)
        games = max(float(row['G']) if 'G' in row and pd.notna(row['G']) else 1.0, 1.0)

        # Exponential-decay weighted BA: last 4d × 2 weight, older days-5-7 × 1 weight
        p4d = ba_4d_lookup.get(name) or ba_4d_lookup.get(ascii_name)
        if p4d and ab_7d > p4d['AB'] and p4d['AB'] > 0:
            h_old  = max(hits - p4d['H'], 0.0)
            ab_old = max(ab_7d - p4d['AB'], 0.0)
            denom  = p4d['AB'] * 2 + ab_old
            ba_weighted = (p4d['H'] * 2 + h_old) / denom if denom > 0 else hits / ab_7d
        else:
            ba_weighted = hits / ab_7d if ab_7d > 0 else 0.260
        ba_weighted = max(0.050, min(ba_weighted, 0.600))

        # Blend with xBA (Statcast expected BA — more stable, luck-neutral predictor)
        xba = xba_lookup.get(name) or xba_lookup.get(ascii_name)
        if xba is not None:
            ba_effective = 0.55 * float(xba) + 0.45 * ba_weighted
        else:
            ba_effective = ba_weighted

        # K% modifies effective BA (elite contact → higher; free-swinger → lower)
        k_pct = float(row['K%']) if 'K%' in candidates.columns and pd.notna(row.get('K%')) else 20.0
        k_z   = round((k_pct - kpct_mean) / kpct_std, 1)
        if k_pct < 12.0:
            ba_effective *= 1.04
        elif k_pct > 22.0:
            ba_effective *= 0.97

        # Hard hit rate adjustment
        hard_hit = hard_hit_lookup.get(name) or hard_hit_lookup.get(ascii_name)
        if hard_hit is not None:
            if hard_hit > 0.45:
                ba_effective *= 1.02
            elif hard_hit < 0.28:
                ba_effective *= 0.98

        # Team offensive environment
        rank = team_rank.get(str(row['Tm']).strip())
        if row['Tm'] in hot_teams:
            ba_effective *= 1.02

        # Lineup-adjusted AB per game (top of order gets ~0.4 more ABs than 9-hole)
        avg_order = batting_order_lookup.get(name) or batting_order_lookup.get(ascii_name)
        ab_per_game = ab_7d / games
        if avg_order is not None:
            ab_per_game = max(2.5, ab_per_game + (5.0 - float(avg_order)) * 0.08)

        # P(hit) = 1 - (1 - BA_eff)^(AB/game)  — Bernoulli probability of ≥1 hit
        p_hit = 1.0 - (1.0 - min(ba_effective, 0.995)) ** max(ab_per_game, 1.0)
        score = round(p_hit * 100, 1)

        # Breakdown string
        breakdown = [f"P(Hit): {score}%"]
        breakdown.append(f"BA: {ba_weighted:.3f}")
        if xba is not None:
            breakdown.append(f"xBA: {float(xba):.3f}")
        if hard_hit is not None:
            breakdown.append(f"HH%: {round(hard_hit * 100)}%")
        breakdown.append(f"K%: {round(k_pct, 1)}% ({k_z:+.1f}σ)")
        if rank is not None:
            breakdown.append(f"Team Rank: #{rank}")
        if avg_order is not None:
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

    # Build matchup cache — try MLB Stats API first (works for current/future season),
    # fall back to per-team BRef schedule_and_record for past seasons
    unique_teams = merged['Tm'].dropna().unique().tolist()
    mlb_matchups = get_all_matchups(simulation_date)
    if mlb_matchups:
        matchup_cache = {team: mlb_matchups.get(team, ("Off Day", "N/A", "", True)) for team in unique_teams}
        # Inject pitcher hands from MLB API matchups
        for team, (opp, pitcher, game_time, is_home) in matchup_cache.items():
            if pitcher and pitcher not in ('TBD', 'N/A', 'Unknown'):
                last = pitcher.split()[-1].lower()
                # Try to get hand from probable_pitchers (fetched separately with hand info)
    else:
        # Fallback: per-team BRef calls (works for past seasons)
        matchup_cache = {team: get_matchup_info(team, simulation_date) for team in unique_teams}

    # Supplement with MLB Stats API probable pitchers (has throwing hand info)
    probable_pitchers = get_probable_pitchers(simulation_date)
    for team, (opp, pitcher, game_time, is_home) in list(matchup_cache.items()):
        if pitcher in ('TBD', 'N/A', 'Unknown') and team in probable_pitchers:
            pp_name, pp_hand = probable_pitchers[team]
            matchup_cache[team] = (opp, pp_name, game_time, is_home)
        if team in probable_pitchers:
            pp_name, pp_hand = probable_pitchers[team]
            if pp_hand and pp_name:
                last = pp_name.split()[-1].lower()
                pitcher_hand_lookup[last] = pp_hand

    # Final fallback: for any pitcher in today's matchups whose hand is still unknown,
    # look them up from the MLB people API by name search
    unknown_pitchers = set()
    for team, (opp, pitcher, game_time, is_home) in matchup_cache.items():
        if pitcher and pitcher not in ('TBD', 'N/A', 'Unknown'):
            last = pitcher.split()[-1].lower()
            if last not in pitcher_hand_lookup:
                unknown_pitchers.add(pitcher)
    if unknown_pitchers:
        print(f"  [DEBUG] Looking up hand for {len(unknown_pitchers)} unknown pitcher(s)...")
        for pitcher_name in unknown_pitchers:
            try:
                r = requests.get(
                    f"https://statsapi.mlb.com/api/v1/people/search?names={requests.utils.quote(pitcher_name)}&sportId=1",
                    timeout=5
                )
                people = r.json().get('people', [])
                if people:
                    person = people[0]
                    hand_info = person.get('pitchHand', {})
                    code = hand_info.get('code', '') if isinstance(hand_info, dict) else ''
                    if code and code != 'S':
                        last = pitcher_name.split()[-1].lower()
                        pitcher_hand_lookup[last] = code
                        print(f"  [DEBUG] Found hand for {pitcher_name}: {code}")
            except Exception:
                pass

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

        bats = row.get('Bats', None)
        if bats is None or (isinstance(bats, float) and pd.isna(bats)):
            # FanGraphs metadata unavailable (e.g. pre-season) — fall back to Statcast stand
            _asc = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode()
            bats = batter_hand_lookup.get(name) or batter_hand_lookup.get(_asc) or 'R'

        opponent, pitcher, game_time, is_home = matchup_cache.get(team, ("Unknown", "Unknown", "", True))

        # Skip players whose team has no game today
        if opponent in ("Unknown", "Off Day", "N/A"):
            continue

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

        # Pitcher throwing hand — must be resolved before platoon decision
        pitcher_hand = ''
        if pitcher and pitcher not in ('TBD', 'Unknown', 'N/A'):
            last_name = pitcher.split()[-1].lower()
            pitcher_hand = pitcher_hand_lookup.get(last_name, '')

        # Platoon-aware A/B list split.
        # Cross-handed (LHB vs RHP, RHB vs LHP) = platoon ADVANTAGE → A-list.
        # Same-handed (LHB vs LHP, RHB vs RHP) = platoon DISADVANTAGE → B-list.
        # Unknown pitcher hand → conservatively demote to B-list.
        reason_for_demotion = []
        if pitcher_hand:
            is_cross_handed = (bats == 'L' and pitcher_hand == 'R') or (bats == 'R' and pitcher_hand == 'L')
            if not is_cross_handed:
                reason_for_demotion.append(f"{bats} vs {pitcher_hand}")
        else:
            reason_for_demotion.append(f"{bats} (hand unknown)")

        # Park factor and platoon matchup label — display only, not factored into score
        score_breakdown = str(row.get('Score_Breakdown', ''))
        pf_label = f"Park: {park_factor}"
        score_breakdown = f"{score_breakdown} | {pf_label}" if score_breakdown else pf_label
        if pitcher_hand and bats:
            score_breakdown += f" | Matchup: {bats}v{pitcher_hand}"

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

        _ascii_name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode()
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
            'XBA': xba_lookup.get(name) or xba_lookup.get(_ascii_name),
        }

        if reason_for_demotion:
            final_b_list.append(player_info)
        else:
            final_a_list.append(player_info)

    print(f"  [DEBUG] Done. A-List: {len(final_a_list)}, B-List: {len(final_b_list)}")
    return final_a_list, final_b_list
