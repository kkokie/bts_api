from django.core.management.base import BaseCommand, CommandError
from datetime import datetime

from predictor.logic import get_predictions
from predictor.models import Prediction


class Command(BaseCommand):
    help = 'Generate predictions for a given date and store them in the database.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            type=str,
            default=None,
            help='Target date in YYYY-MM-DD format. Defaults to today.',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Overwrite any existing predictions for this date.',
        )

    def handle(self, *args, **options):
        date_str = options['date']
        if date_str:
            try:
                target_date = datetime.strptime(date_str, '%Y-%m-%d')
            except ValueError:
                raise CommandError(f"Invalid date format: '{date_str}'. Use YYYY-MM-DD.")
        else:
            target_date = datetime.now()

        date_only = target_date.date()

        existing = Prediction.objects.filter(date=date_only)
        if existing.exists() and not options['force']:
            self.stdout.write(self.style.WARNING(
                f"Predictions for {date_only} already exist ({existing.count()} rows). "
                f"Use --force to overwrite."
            ))
            return

        self.stdout.write(f"Generating predictions for {date_only}...")
        a_list, b_list = get_predictions(target_date)

        if not a_list and not b_list:
            self.stdout.write(self.style.ERROR(
                "No predictions generated. Check the date or data availability."
            ))
            return

        count_a, count_b = Prediction.save_from_lists(date_only, a_list, b_list)
        self.stdout.write(self.style.SUCCESS(
            f"Saved {count_a} A-list and {count_b} B-list predictions for {date_only}."
        ))
