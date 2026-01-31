import pandas as pd
from pybaseball import batting_stats_range, batting_stats, schedule_and_record
from datetime import datetime, timedelta
from unidecode import unidecode

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


def clean_name_string(name):
    if not isinstance(name, str): return name
    name = name.replace("\\'", "'")
    try:
        name = name.encode('latin1').decode('utf-8')
    except:
        pass
    return unidecode(name)


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
        if 'Pos' in df.columns: cols_to_keep.append('Pos')
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
            return game.iloc[0]['Opp'], "TBD"
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

    # Hot Teams Logic
    hot_teams = []
    if 'R' in df_7d.columns:
        team_stats = df_7d.groupby('Tm')['R'].sum().sort_values(ascending=False)
        hot_teams = team_stats.head(10).index.tolist()

    for index, row in candidates.iterrows():
        ba = row['BA'] if 'BA' in row else (row['AVG'] if 'AVG' in row else 0.250)

        score = ba * 100
        if 'K%' in row and row['K%'] < 12.0: score += 10
        if row['Tm'] in hot_teams: score += 10
        candidates.at[index, 'Score'] = score

    top_picks = candidates.sort_values('Score', ascending=False).head(20).copy()

    # Merge Metadata (Defensive)
    meta = get_player_metadata(SEASON)
    if not meta.empty:
        # Only merge on columns that exist in the metadata
        cols_to_merge = [c for c in meta.columns if c in ['Name', 'Pos', 'Bats']]
        merged = pd.merge(top_picks, meta[cols_to_merge], on='Name', how='left')
    else:
        merged = top_picks

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
        if pos == 'C': reason_for_demotion.append("Is Catcher")
        if bats == 'L': reason_for_demotion.append("Lefty Batter")

        opponent, pitcher = get_matchup_info(team, simulation_date)

        player_info = {
            'Name': name,
            'Team': team,
            'Pos': pos,
            'Bats': bats,
            'Score': round(row['Score'], 1),
            'Opponent': opponent,
            'Probable_Pitcher': pitcher,
            'Notes': ", ".join(reason_for_demotion)
        }

        if reason_for_demotion:
            final_b_list.append(player_info)
        else:
            final_a_list.append(player_info)

    print(f"  [DEBUG] Done. A-List: {len(final_a_list)}, B-List: {len(final_b_list)}")
    return final_a_list, final_b_list