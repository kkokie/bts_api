import re
import unicodedata
import pandas as pd
from pybaseball import batting_stats_range, batting_stats, schedule_and_record, pitching_stats, statcast, playerid_reverse_lookup
from datetime import datetime, timedelta

# --- CONFIGURATION ---
SEASON = 2025

# MAPPING: Full Name (from Stats) -> BRef Code (for Schedule)
NAME_TO_ABBR = {
    'Arizona': 'ARI', 'Atlanta': 'ATL', 'Baltimore': 'BAL', 'Boston': 'BOS',
    'Chicago White Sox': 'CWS', 'Chicago Cubs': 'CHC', 'Cincinnati': 'CIN', 'Cleveland': 'CLE',
    'Colorado': 'COL', 'Detroit': 'DET', 'Houston': 'HOU', 'Kansas City': 'KC',
    'Los Angeles Angels': 'LAA', 'Los Angeles Dodgers': 'LAD', 'Miami': 'MIA', 'Milwaukee': 'MIL',
    'Minnesota': 'MIN', 'New York Yankees': 'NYY', 'New York Mets': 'NYM', 'Oakland': 'OAK',
    'Philadelphia': 'PHI', 'Pittsburgh': 'PIT', 'San Diego': 'SD', 'San Francisco': 'SF',
    'Seattle': 'SEA', 'St. Louis': 'STL', 'Tampa Bay': 'TB', 'Texas': 'TEX',
    'Toronto': 'TOR', 'Washington': 'WSH'
}

# FanGraphs uses slightly different abbreviations than baseball-reference
FG_TO_BREF_TEAM = {
    'SFG': 'SF', 'WSN': 'WSH', 'TBR': 'TB', 'KCR': 'KC', 'SDP': 'SD',
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

    if len(bref_team) > 3: return "Unknown", "Unknown"

    try:
        schedule = schedule_and_record(SEASON, bref_team)
        short_date = game_date.strftime("%b %-d")
        game = schedule[schedule['Date'].astype(str).str.contains(short_date, case=False)]

        if not game.empty:
            row = game.iloc[0]
            opponent = row['Opp']
            wl = str(row.get('W/L', ''))
            if 'W' in wl:
                pitcher = row.get('Loss', None)
            elif 'L' in wl:
                pitcher = row.get('Win', None)
            else:
                pitcher = None
            pitcher = str(pitcher) if pitcher and str(pitcher) not in ('None', 'nan') else 'TBD'
            return opponent, pitcher
        return "Off Day", "N/A"
    except:
        return "Unknown", "Unknown"


def get_predictions(simulation_date):
    print(f"\n--- 🕵️‍♂️ STARTING DEBUG ANALYSIS FOR: {simulation_date.strftime('%Y-%m-%d')} ---")

    df_4d = get_window_stats(4, simulation_date)
    df_7d = get_window_stats(7, simulation_date)

    if df_7d.empty:
        print("  [DEBUG] 🛑 STOPPING: 7-Day Data is empty.")
        return [], []

        # Filter candidates
    candidates = df_7d[(df_7d['PA'] >= 10)].copy()

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
        team_rank = {team: rank + 1 for rank, team in enumerate(team_stats.index)}

    # BA z-score parameters across all candidates so the color tiers are relative
    # to the pool being displayed (blue = genuinely elite within this group)
    _ba_col = 'BA' if 'BA' in candidates.columns else ('AVG' if 'AVG' in candidates.columns else None)
    if _ba_col:
        _ba_vals = candidates[_ba_col].dropna()
        ba_mean = float(_ba_vals.mean()) if not _ba_vals.empty else 0.250
        ba_std  = float(_ba_vals.std())  if len(_ba_vals) > 1   else 0.030
        if ba_std == 0 or pd.isna(ba_std):
            ba_std = 0.030
    else:
        ba_mean, ba_std = 0.250, 0.030

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
        ba = row['BA'] if 'BA' in row else (row['AVG'] if 'AVG' in row else 0.250)
        ba_z = round((ba - ba_mean) / ba_std, 1)

        score = ba * 100
        breakdown = [f"BA: {round(ba * 100, 1)} ({ba_z:+.1f}σ)"]

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

    # Fix "---" / "TOT" / "2TM" team values using FanGraphs latest team
    def resolve_team(name, team):
        t = str(team).strip()
        if t.replace('-', '').replace(' ', '') in BAD_TEAMS or '---' in t:
            return team_fallback.get(name, t)
        return t

    merged['Tm'] = merged.apply(lambda r: resolve_team(r['Name'], r['Tm']), axis=1)

    # Batch schedule lookups — one HTTP request per unique team, not per player
    unique_teams = merged['Tm'].dropna().unique().tolist()
    matchup_cache = {team: get_matchup_info(team, simulation_date) for team in unique_teams}

    # One FanGraphs call for all pitcher stats
    pitcher_lookup = get_pitcher_stats(SEASON)

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

        opponent, pitcher = matchup_cache.get(team, ("Unknown", "Unknown"))

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

        score_breakdown = str(row.get('Score_Breakdown', ''))

        avg_order = batting_order_lookup.get(name) or batting_order_lookup.get(
            unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode()
        )

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
        }

        if reason_for_demotion:
            final_b_list.append(player_info)
        else:
            final_a_list.append(player_info)

    print(f"  [DEBUG] Done. A-List: {len(final_a_list)}, B-List: {len(final_b_list)}")
    return final_a_list, final_b_list
