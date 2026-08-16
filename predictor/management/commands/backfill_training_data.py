"""
Management command to backfill historical predictions and hit results for ML training.

Usage:
    python manage.py backfill_training_data
    python manage.py backfill_training_data --start 2025-04-01 --end 2025-09-28
    python manage.py backfill_training_data --results-only     # only fill got_hit, skip prediction generation
    python manage.py backfill_training_data --force            # regenerate predictions even if they exist
"""

import unicodedata
from datetime import date, timedelta

from django.core.management.base import BaseCommand

from predictor.logic import get_predictions, get_hit_results
from predictor.models import Prediction

# 2025 MLB regular season
DEFAULT_START = date(2025, 9, 27)
DEFAULT_END = date(2025, 9, 28)


class Command(BaseCommand):
    help = 'Backfill historical predictions and hit results for ML training data.'

    def add_arguments(self, parser):
        parser.add_argument('--start', type=str, default=None,
                            help=f'Start date YYYY-MM-DD (default: {DEFAULT_START})')
        parser.add_argument('--end', type=str, default=None,
                            help=f'End date YYYY-MM-DD (default: {DEFAULT_END})')
        parser.add_argument('--force', action='store_true',
                            help='Regenerate predictions even if they already exist.')
        parser.add_argument('--results-only', action='store_true',
                            help='Only update got_hit; skip prediction generation entirely.')

    def handle(self, *args, **options):
        start = date.fromisoformat(options['start']) if options['start'] else DEFAULT_START
        end = date.fromisoformat(options['end']) if options['end'] else DEFAULT_END
        force = options['force']
        results_only = options['results_only']

        today = date.today()
        if end >= today:
            end = today - timedelta(days=1)
            self.stdout.write(self.style.WARNING(
                f"End date clamped to yesterday ({end}) — can't get results for future dates."
            ))

        total_days = (end - start).days + 1
        self.stdout.write(f"Backfilling {start} → {end} ({total_days} calendar days)\n")

        predictions_generated = 0
        predictions_skipped = 0
        results_updated = 0
        dates_with_no_games = 0
        errors = []

        current = start
        day_num = 0
        while current <= end:
            day_num += 1
            self.stdout.write(f"[{day_num}/{total_days}] {current} ... ", ending='')

            # --- Step 1: Generate predictions ---
            if not results_only:
                existing = Prediction.objects.filter(date=current)
                if existing.exists() and not force:
                    self.stdout.write("predictions exist, ", ending='')
                    predictions_skipped += 1
                else:
                    try:
                        from datetime import datetime as dt
                        a_list, b_list = get_predictions(dt.combine(current, dt.min.time()))
                        if a_list or b_list:
                            Prediction.save_from_lists(current, a_list, b_list)
                            predictions_generated += 1
                            self.stdout.write(
                                f"generated {len(a_list)}A+{len(b_list)}B, ", ending=''
                            )
                        else:
                            dates_with_no_games += 1
                            self.stdout.write("no games, ")
                            current += timedelta(days=1)
                            continue
                    except Exception as e:
                        errors.append((current, str(e)))
                        self.stdout.write(self.style.ERROR(f"ERROR: {e}"))
                        current += timedelta(days=1)
                        continue

            # --- Step 2: Populate got_hit ---
            needs_results = Prediction.objects.filter(date=current, got_hit__isnull=True)
            if needs_results.exists():
                try:
                    hit_results = get_hit_results(current)
                    if hit_results:
                        to_update = []
                        for pred in Prediction.objects.filter(date=current):
                            name = pred.name
                            ascii_name = (
                                unicodedata.normalize('NFKD', name)
                                .encode('ascii', 'ignore').decode()
                            )
                            if name in hit_results:
                                pred.got_hit = hit_results[name]
                                to_update.append(pred)
                            elif ascii_name in hit_results:
                                pred.got_hit = hit_results[ascii_name]
                                to_update.append(pred)
                        if to_update:
                            Prediction.objects.bulk_update(to_update, ['got_hit'])
                            results_updated += len(to_update)
                            self.stdout.write(self.style.SUCCESS(f"results: {len(to_update)} updated"))
                        else:
                            self.stdout.write("results: no name matches")
                    else:
                        self.stdout.write("results: no statcast data")
                except Exception as e:
                    errors.append((current, f"results: {e}"))
                    self.stdout.write(self.style.ERROR(f"results ERROR: {e}"))
            else:
                self.stdout.write("results already populated")

            current += timedelta(days=1)

        # --- Summary ---
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(self.style.SUCCESS(
            f"Done.\n"
            f"  Predictions generated : {predictions_generated}\n"
            f"  Predictions skipped   : {predictions_skipped}\n"
            f"  Dates with no games   : {dates_with_no_games}\n"
            f"  got_hit rows updated  : {results_updated}\n"
            f"  Errors                : {len(errors)}"
        ))
        if errors:
            self.stdout.write("\nErrors:")
            for d, msg in errors:
                self.stdout.write(f"  {d}: {msg}")

        # Final DB stats
        total = Prediction.objects.exclude(got_hit=None).count()
        hits = Prediction.objects.filter(got_hit=True).count()
        self.stdout.write(f"\nDB total rows with outcomes: {total} ({hits} hits, {total - hits} outs)")
