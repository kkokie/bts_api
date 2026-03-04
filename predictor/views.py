import unicodedata
from django.shortcuts import render
from datetime import datetime, date as date_class

from .models import Prediction
from .logic import get_predictions, get_hit_results


def scoring_guide(request):
    # Pre-compute the lineup bonus table so the template stays logic-free
    lineup_rows = []
    for pos in range(1, 10):
        bonus = round(max(0.0, (9 - pos) / 8 * 10), 1)
        lineup_rows.append({'pos': pos, 'bonus': bonus})

    context = {'lineup_rows': lineup_rows}
    return render(request, 'scoring_guide.html', context)


def dashboard(request):
    selected_date = datetime(2025, 9, 28)

    if request.GET.get('date'):
        date_str = request.GET.get('date')
        try:
            selected_date = datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            pass

    date_only = selected_date.date()

    # Check DB first — if already cached, serve instantly
    if not Prediction.objects.filter(date=date_only).exists():
        # Not cached yet — compute and store (first-time only for this date)
        a_list_data, b_list_data = get_predictions(selected_date)
        if a_list_data or b_list_data:
            Prediction.save_from_lists(date_only, a_list_data, b_list_data)

    # For past dates, backfill hit results once (got_hit stays None until fetched)
    if date_only < date_class.today():
        needs_hit_data = Prediction.objects.filter(date=date_only, got_hit__isnull=True)
        if needs_hit_data.exists():
            hit_results = get_hit_results(selected_date)
            if hit_results:
                preds_to_update = []
                for pred in Prediction.objects.filter(date=date_only):
                    name = pred.name
                    ascii_name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode()
                    if name in hit_results:
                        pred.got_hit = hit_results[name]
                        preds_to_update.append(pred)
                    elif ascii_name in hit_results:
                        pred.got_hit = hit_results[ascii_name]
                        preds_to_update.append(pred)
                if preds_to_update:
                    Prediction.objects.bulk_update(preds_to_update, ['got_hit'])

    a_list = Prediction.objects.filter(date=date_only, list_type=Prediction.LIST_A).order_by('-score')
    b_list = Prediction.objects.filter(date=date_only, list_type=Prediction.LIST_B).order_by('-score')

    context = {
        'a_list': a_list,
        'b_list': b_list,
        'selected_date': selected_date.strftime('%Y-%m-%d'),
        'pretty_date': selected_date.strftime('%B %d, %Y'),
    }

    return render(request, 'dashboard.html', context)
