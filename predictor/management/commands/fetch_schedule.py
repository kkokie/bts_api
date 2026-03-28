import json
import requests
from datetime import date
from pathlib import Path
from django.core.management.base import BaseCommand

CACHE_FILE = Path(__file__).resolve().parents[4] / 'schedule_cache.json'


class Command(BaseCommand):
    help = 'Fetch today\'s MLB game schedule and store the first game time for lineup checks.'

    def handle(self, *args, **options):
        today = date.today().strftime('%Y-%m-%d')
        url = f'https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today}'

        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            game_times = []
            for date_obj in data.get('dates', []):
                for game in date_obj.get('games', []):
                    game_time = game.get('gameDate')  # e.g. "2025-04-15T17:10:00Z" (UTC)
                    if game_time:
                        game_times.append(game_time)

            if not game_times:
                self.stdout.write(f'[{today}] No games scheduled today.')
                CACHE_FILE.write_text(json.dumps({'date': today, 'first_game_utc': None}))
                return

            first_game_utc = min(game_times)

            cache = {'date': today, 'first_game_utc': first_game_utc}
            CACHE_FILE.write_text(json.dumps(cache))

            self.stdout.write(self.style.SUCCESS(
                f'[{today}] First game at {first_game_utc} UTC — '
                f'lineup checks will start 1 hr before.'
            ))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Failed to fetch schedule: {e}'))
