import unicodedata
from django.shortcuts import render
from django.db.models import F
from datetime import datetime, date as date_class, timedelta

from .models import Prediction
from .logic import get_predictions, get_hit_results


def scoring_guide(request):
    # Pre-compute the lineup bonus table so the template stays logic-free
    lineup_rows = []
    for pos in range(1, 10):
        bonus = round(max(0.0, (9 - pos) / 8 * 5), 1)
        lineup_rows.append({'pos': pos, 'bonus': bonus})

    context = {'lineup_rows': lineup_rows}
    return render(request, 'scoring_guide.html', context)


def accuracy(request):
    """Historical accuracy: how often did the top A-list pick get a hit?"""
    today = date_class.today()

    # Dates that have at least one resolved A-list pick
    dates = list(
        Prediction.objects
        .filter(list_type=Prediction.LIST_A, got_hit__isnull=False)
        .values_list('date', flat=True)
        .distinct()
        .order_by('-date')
    )

    # Top pick per day — prefer ml_score ranking when available
    rows = []
    for d in dates:
        top = (
            Prediction.objects
            .filter(date=d, list_type=Prediction.LIST_A, got_hit__isnull=False)
            .order_by(F('ml_score').desc(nulls_last=True), '-score')
            .first()
        )
        if top:
            rows.append({
                'date': d,
                'player': top.name,
                'score': top.score,
                'ml_score': top.ml_score,
                'got_hit': top.got_hit,
            })

    def window_stats(days=None):
        if days:
            cutoff = today - timedelta(days=days)
            subset = [r for r in rows if r['date'] >= cutoff]
        else:
            subset = rows
        total = len(subset)
        hits  = sum(1 for r in subset if r['got_hit'])
        return {
            'total': total,
            'hits': hits,
            'pct': round(hits / total * 100, 1) if total else None,
        }

    stats_all = window_stats()
    context = {
        'rows': rows,
        'stats': [
            ('Last 7 Days',  window_stats(7)),
            ('Last 30 Days', window_stats(30)),
            ('All Time',     stats_all),
        ],
        'stats_all': stats_all,
    }
    return render(request, 'accuracy.html', context)


def dashboard(request):
    today = date_class.today()
    selected_date = datetime(today.year, today.month, today.day)

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

    # Double Down pair: top 2 A-list picks with different game times (independent outcomes)
    double_down_pair = []
    seen_times = set()
    for pick in a_list:
        if pick.lineup_confirmed is False:
            continue
        game_key = pick.game_time or pick.opponent
        if game_key not in seen_times:
            double_down_pair.append(pick)
            seen_times.add(game_key)
        if len(double_down_pair) == 2:
            break

    context = {
        'a_list': a_list,
        'b_list': b_list,
        'double_down_pair': double_down_pair,
        'selected_date': selected_date.strftime('%Y-%m-%d'),
        'pretty_date': selected_date.strftime('%B %d, %Y'),
    }

    return render(request, 'dashboard.html', context)
