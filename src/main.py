import pandas as pd
from pybaseball import batting_stats


def debug_data(season=2025):
    print(f"--- DEBUGGING SEASON {season} ---")

    # 1. Fetch raw data
    df = batting_stats(season)

    # 2. Print the available columns (to check for name changes)
    print(f"\nTotal Rows Fetched: {len(df)}")
    print("Columns found:", df.columns.tolist())

    # 3. Inspect the specific columns we care about
    # We use .get() to avoid crashing if the column doesn't exist
    relevant_cols = ['Name', 'Team', 'AVG', 'Contact%', 'K%', 'PA']

    print("\n--- SAMPLE RAW DATA (First 5 Rows) ---")
    # Check if columns exist before printing
    existing_cols = [c for c in relevant_cols if c in df.columns]
    print(df[existing_cols].head())

    print("\n--- DATA TYPES ---")
    print(df[existing_cols].dtypes)


if __name__ == "__main__":
    debug_data(2025)
    # If 2025 is empty, try 2024 to see if it's a specific year issue
    # debug_data(2024)