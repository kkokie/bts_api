"""
LightGBM hyperparameter tuning with TimeSeriesSplit.
Run from project root: python notebooks/tune_lgbm.py
"""
import sys
import os
import warnings

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PROJECT_ROOT)
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings.dev'
os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = 'true'
os.environ['DATABASE_URL'] = f'sqlite:///{PROJECT_ROOT}/db.sqlite3'

import django
django.setup()

import re
import numpy as np
import pandas as pd
from predictor.models import Prediction
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import RandomizedSearchCV, TimeSeriesSplit
from scipy.stats import randint, uniform
from lightgbm import LGBMClassifier
import joblib

# ── Load data ────────────────────────────────────────────────────────────────
qs = Prediction.objects.exclude(got_hit=None).values(
    'date', 'name', 'bats', 'list_type', 'score',
    'pitcher_hand', 'pitcher_era', 'pitcher_whip',
    'is_home', 'avg_batting_order', 'park_factor',
    'team_rank', 'hit_streak', 'score_breakdown', 'got_hit',
)
df = pd.DataFrame.from_records(qs)
print(f'Loaded {len(df)} rows across {df["date"].nunique()} dates')

# ── Parse score_breakdown ─────────────────────────────────────────────────────
def parse_breakdown(s):
    result = {}
    patterns = {
        'ba':     r'BA:\s*([0-9.]+)',
        'xba':    r'xBA:\s*([0-9.]+)',
        'hh_pct': r'HH%:\s*([0-9.]+)',
        'k_pct':  r'K%:\s*([0-9.]+)',
        'bb_pct': r'BB%:\s*([0-9.]+)',
        'speed':  r'Speed:\s*([0-9.]+)',
        'p_era':  r'P\.ERA:\s*([0-9.]+)',
        'p_whip': r'P\.WHIP:\s*([0-9.]+)',
        'p_k9':   r'P\.K/9:\s*([0-9.]+)',
    }
    for key, pat in patterns.items():
        m = re.search(pat, s or '')
        result[key] = float(m.group(1)) if m else np.nan
    return result

parsed_df = pd.DataFrame(df['score_breakdown'].apply(parse_breakdown).tolist(), index=df.index)
df = pd.concat([df.reset_index(drop=True), parsed_df.reset_index(drop=True)], axis=1)

# ── Feature matrix ────────────────────────────────────────────────────────────
df['bats_L']    = (df['bats'] == 'L').astype(int)
df['bats_S']    = (df['bats'] == 'S').astype(int)
df['pitch_R']   = (df['pitcher_hand'] == 'R').astype(int)
df['pitch_L']   = (df['pitcher_hand'] == 'L').astype(int)
df['platoon']   = (((df['bats'] == 'L') & (df['pitcher_hand'] == 'R')) |
                   ((df['bats'] == 'R') & (df['pitcher_hand'] == 'L'))).astype(int)
df['is_a_list'] = (df['list_type'] == 'A').astype(int)

FEATURE_COLS = [
    'score', 'ba', 'xba', 'hh_pct', 'k_pct', 'bb_pct', 'speed',
    'is_home', 'park_factor', 'avg_batting_order', 'hit_streak',
    'pitcher_era', 'pitcher_whip', 'p_era',
    'team_rank', 'bats_L', 'bats_S', 'pitch_R', 'pitch_L', 'platoon', 'is_a_list',
]
FEATURE_COLS = [c for c in FEATURE_COLS if df[c].notna().any()]
print(f'Features: {FEATURE_COLS}')

df['date'] = pd.to_datetime(df['date'])
df = df.sort_values('date').reset_index(drop=True)
X = df[FEATURE_COLS].copy()
y = df['got_hit'].astype(int)

split_idx = int(len(df) * 0.8)
X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]
print(f'Train: {len(X_train)} rows | Test: {len(X_test)} rows')

# ── Baseline ──────────────────────────────────────────────────────────────────
baseline_auc = roc_auc_score(y_test, X_test[['score']].values / 100.0)
print(f'Baseline (hand-crafted score) AUC: {baseline_auc:.4f}')

# ── Default LightGBM ──────────────────────────────────────────────────────────
lgbm_default = Pipeline([
    ('pre', Pipeline([('impute', SimpleImputer(strategy='median'))])),
    ('clf', LGBMClassifier(
        n_estimators=300, max_depth=4, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        random_state=42, n_jobs=-1, verbose=-1,
    )),
])
with warnings.catch_warnings():
    warnings.simplefilter('ignore')
    lgbm_default.fit(X_train, y_train)
    lgbm_default_proba = lgbm_default.predict_proba(X_test)[:, 1]
lgbm_default_auc = roc_auc_score(y_test, lgbm_default_proba)
print(f'Default LightGBM AUC: {lgbm_default_auc:.4f}')

# ── Hyperparameter tuning (TimeSeriesSplit) ───────────────────────────────────
print('\nRunning RandomizedSearchCV (60 iters × 5 folds = 300 fits)...')

tscv = TimeSeriesSplit(n_splits=5)

param_dist = {
    'clf__n_estimators':      randint(200, 700),
    'clf__num_leaves':        randint(20, 80),
    'clf__max_depth':         [-1, 3, 4, 5, 6, 8],
    'clf__learning_rate':     uniform(0.02, 0.12),
    'clf__subsample':         uniform(0.6, 0.4),
    'clf__colsample_bytree':  uniform(0.6, 0.4),
    'clf__min_child_samples': randint(10, 60),
    'clf__reg_alpha':         uniform(0, 0.5),
    'clf__reg_lambda':        uniform(0, 0.5),
}

lgbm_base = Pipeline([
    ('pre', Pipeline([('impute', SimpleImputer(strategy='median'))])),
    ('clf', LGBMClassifier(random_state=42, n_jobs=-1, verbose=-1)),
])

search = RandomizedSearchCV(
    lgbm_base,
    param_distributions=param_dist,
    n_iter=60,
    scoring='roc_auc',
    cv=tscv,
    random_state=42,
    n_jobs=-1,
    verbose=1,
)

with warnings.catch_warnings():
    warnings.simplefilter('ignore')
    search.fit(X_train, y_train)

print(f'\nBest CV AUC (TimeSeriesSplit): {search.best_score_:.4f}')
print(f'Best params:')
for k, v in search.best_params_.items():
    print(f'  {k}: {v}')

lgbm_tuned = search.best_estimator_
with warnings.catch_warnings():
    warnings.simplefilter('ignore')
    lgbm_tuned_proba = lgbm_tuned.predict_proba(X_test)[:, 1]
lgbm_tuned_auc = roc_auc_score(y_test, lgbm_tuned_proba)

print(f'\n{"─"*45}')
print(f'Baseline AUC         : {baseline_auc:.4f}')
print(f'LightGBM default AUC : {lgbm_default_auc:.4f}  ({lgbm_default_auc - baseline_auc:+.4f})')
print(f'LightGBM tuned AUC   : {lgbm_tuned_auc:.4f}  ({lgbm_tuned_auc - baseline_auc:+.4f})')
print(f'Tuning delta         : {lgbm_tuned_auc - lgbm_default_auc:+.4f}')

# ── Save best model ───────────────────────────────────────────────────────────
if lgbm_tuned_auc >= lgbm_default_auc:
    winner_model, winner_auc, winner_name = lgbm_tuned, lgbm_tuned_auc, 'lightgbm_tuned'
else:
    winner_model, winner_auc, winner_name = lgbm_default, lgbm_default_auc, 'lightgbm'
    print('\nTuned model did not beat default — keeping default.')

models_dir = os.path.join(PROJECT_ROOT, 'data', 'models')
os.makedirs(models_dir, exist_ok=True)
model_path = os.path.join(models_dir, 'hit_predictor_lightgbm.joblib')
joblib.dump({
    'model': winner_model,
    'feature_cols': FEATURE_COLS,
    'auc': winner_auc,
    'baseline_auc': baseline_auc,
    'model_name': winner_name,
}, model_path)

print(f'\nSaved: {winner_name} → {model_path}')
print(f'AUC: {winner_auc:.4f}')
