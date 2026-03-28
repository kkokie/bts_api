import json
import unicodedata
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from django.core.management.base import BaseCommand
from predictor.models import Prediction
from predictor.logic import get_lineup_status

CACHE_FILE = Path(__file__).resolve().parents[4] / 'schedule_cache.json'

# How long before first pitch to start checking lineups
WINDOW_OPEN_BEFORE  = timedelta(hours=1)
# How long after first pitch to keep checking (in case of last-minute scratches)
WINDOW_CLOSE_AFTER  = timedelta(minutes=30)


class Command(BaseCommand):
    help = 'Check confirmed MLB starting lineups. Self-exits if outside the pre-game window.'

    def handle(self, *args, **options):
        today = date.today()
        now_utc = datetime.now(timezone.utc)

        # --- Read schedule cache ---
        if not CACHE_FILE.exists():
            self.stdout.write('No schedule cache found — run fetch_schedule first.')
            return

        try:
            cache = json.loads(CACHE_FILE.read_text())
        except json.JSONDecodeError:
            self.stdout.write('Schedule cache is corrupted — run fetch_schedule again.')
            return

        if cache.get('date') != str(today):
            self.stdout.write(f'Schedule cache is from {cache.get("date")}, not today. Run fetch_schedule.')
            return

        first_game_str = cache.get('first_game_utc')
        if not first_game_str:
            self.stdout.write('No games scheduled today.')
            return

        first_game_utc = datetime.fromisoformat(first_game_str.replace('Z', '+00:00'))
        window_open  = first_game_utc - WINDOW_OPEN_BEFORE
        window_close = first_game_utc + WINDOW_CLOSE_AFTER

        # --- Window check: self-exit if too early or too late ---
        if now_utc < window_open:
            mins_until = int((window_open - now_utc).total_seconds() / 60)
            self.stdout.write(
                f'Too early — lineup window opens in {mins_until} min '
                f'(at {window_open.strftime("%H:%M UTC")}).'
            )
            return

        if now_utc > window_close:
            self.stdout.write('Window has passed — all lineups should be confirmed.')
            return

        # --- Check if there's anything left to do ---
        preds = Prediction.objects.filter(date=today)
        if not preds.exists():
            self.stdout.write(f'No predictions found for {today}.')
            return

        if not preds.filter(lineup_confirmed__isnull=True).exists():
            self.stdout.write('All lineup statuses already confirmed — nothing to update.')
            return

        # --- Fetch lineups ---
        confirmed_names, lineup_posted_teams = get_lineup_status(
            datetime.combine(today, datetime.min.time())
        )

        if not lineup_posted_teams:
            self.stdout.write('Lineups not posted yet — will retry next cycle.')
            return

        updated = 0
        scratched = 0
        preds_to_update = []

        for pred in preds:
            if pred.team not in lineup_posted_teams:
                continue  # that game's lineup isn't posted yet
            name = pred.name
            ascii_name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode()
            in_lineup = name in confirmed_names or ascii_name in confirmed_names
            if pred.lineup_confirmed != in_lineup:
                pred.lineup_confirmed = in_lineup
                preds_to_update.append(pred)
                if not in_lineup:
                    scratched += 1
                    self.stdout.write(self.style.WARNING(f'  ⚠️  {name} NOT in lineup'))
            updated += 1

        if preds_to_update:
            Prediction.objects.bulk_update(preds_to_update, ['lineup_confirmed'])

        self.stdout.write(self.style.SUCCESS(
            f'[{now_utc.strftime("%H:%M UTC")}] Checked {updated} players across '
            f'{len(lineup_posted_teams)} teams. {scratched} scratched.'
        ))
