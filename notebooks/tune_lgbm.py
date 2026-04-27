"""
ML model comparison + tuning for Beat the Streak hit prediction.
Run from project root: arch -arm64 venv/bin/python notebooks/tune_lgbm.py

Models tried:
  1. Logistic Regression (baseline ML)
  2. Random Forest
  3. LightGBM (default)
  4. LightGBM (tuned, RandomizedSearchCV + TimeSeriesSplit)
  5. XGBoost (tuned)
  6. CatBoost
  7. Stacking ensemble (LR + LGBM + XGB base → LR meta)
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
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import RandomizedSearchCV, TimeSeriesSplit
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier, StackingClassifier
from scipy.stats import randint, uniform
from lightgbm import LGBMClassifier
from xgboost import XGBClassifier
from catboost import CatBoostClassifier
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
print(f'Hit rate: {df["got_hit"].mean():.1%}')

# ── Parse score_breakdown ─────────────────────────────────────────────────────
def parse_breakdown(s):
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
    result = {}
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

df['date'] = pd.to_datetime(df['date'])
df = df.sort_values('date').reset_index(drop=True)
X = df[FEATURE_COLS].copy()
y = df['got_hit'].astype(int)

split_idx = int(len(df) * 0.8)
X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]
print(f'Train: {len(X_train)} | Test: {len(X_test)} | Features: {len(FEATURE_COLS)}')

# ── Helpers ───────────────────────────────────────────────────────────────────
tscv = TimeSeriesSplit(n_splits=5)

def evaluate(name, pipeline, fit=True):
    if fit:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            pipeline.fit(X_train, y_train)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        proba = pipeline.predict_proba(X_test)[:, 1]
    auc = roc_auc_score(y_test, proba)
    print(f'  {name:<35} AUC: {auc:.4f}')
    return auc, proba

impute      = Pipeline([('impute', SimpleImputer(strategy='median'))])
impute_scale= Pipeline([('impute', SimpleImputer(strategy='median')), ('scale', StandardScaler())])

results = {}

# ── Baseline ──────────────────────────────────────────────────────────────────
baseline_auc = roc_auc_score(y_test, X_test[['score']].values / 100.0)
print(f'\n{"─"*50}')
print(f'  {"Baseline (hand-crafted)":<35} AUC: {baseline_auc:.4f}')

# ── 1. Logistic Regression ────────────────────────────────────────────────────
print('\n[1] Logistic Regression')
lr = Pipeline([('pre', impute_scale), ('clf', LogisticRegression(max_iter=1000, random_state=42))])
results['logistic_regression'], lr_proba = evaluate('Logistic Regression', lr)

# ── 2. Random Forest ──────────────────────────────────────────────────────────
print('\n[2] Random Forest')
rf = Pipeline([('pre', impute), ('clf', RandomForestClassifier(n_estimators=300, max_depth=6, random_state=42, n_jobs=-1))])
results['random_forest'], rf_proba = evaluate('Random Forest', rf)

# ── 3. LightGBM default ───────────────────────────────────────────────────────
print('\n[3] LightGBM (default)')
lgbm = Pipeline([('pre', impute), ('clf', LGBMClassifier(
    n_estimators=300, max_depth=4, learning_rate=0.05,
    subsample=0.8, colsample_bytree=0.8, random_state=42, n_jobs=-1, verbose=-1,
))])
results['lightgbm_default'], lgbm_proba = evaluate('LightGBM default', lgbm)

# ── 4. LightGBM tuned ─────────────────────────────────────────────────────────
print('\n[4] LightGBM (tuned, 60 iters × 5-fold TimeSeriesSplit)')
lgbm_param_dist = {
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
lgbm_search = RandomizedSearchCV(
    Pipeline([('pre', impute), ('clf', LGBMClassifier(random_state=42, n_jobs=-1, verbose=-1))]),
    param_distributions=lgbm_param_dist, n_iter=60, scoring='roc_auc',
    cv=tscv, random_state=42, n_jobs=-1, verbose=0,
)
with warnings.catch_warnings():
    warnings.simplefilter('ignore')
    lgbm_search.fit(X_train, y_train)
lgbm_tuned = lgbm_search.best_estimator_
results['lightgbm_tuned'], lgbm_tuned_proba = evaluate('LightGBM tuned', lgbm_tuned, fit=False)
print(f'    Best CV AUC: {lgbm_search.best_score_:.4f} | params: {lgbm_search.best_params_}')

# ── 5. XGBoost tuned ─────────────────────────────────────────────────────────
print('\n[5] XGBoost (tuned, 60 iters × 5-fold TimeSeriesSplit)')
xgb_param_dist = {
    'clf__n_estimators':  randint(200, 700),
    'clf__max_depth':     randint(3, 8),
    'clf__learning_rate': uniform(0.02, 0.12),
    'clf__subsample':     uniform(0.6, 0.4),
    'clf__colsample_bytree': uniform(0.6, 0.4),
    'clf__min_child_weight': randint(1, 10),
    'clf__reg_alpha':     uniform(0, 0.5),
    'clf__reg_lambda':    uniform(0.5, 1.5),
    'clf__gamma':         uniform(0, 0.3),
}
xgb_search = RandomizedSearchCV(
    Pipeline([('pre', impute), ('clf', XGBClassifier(
        random_state=42, n_jobs=-1, verbosity=0, eval_metric='logloss',
    ))]),
    param_distributions=xgb_param_dist, n_iter=60, scoring='roc_auc',
    cv=tscv, random_state=42, n_jobs=-1, verbose=0,
)
with warnings.catch_warnings():
    warnings.simplefilter('ignore')
    xgb_search.fit(X_train, y_train)
xgb_tuned = xgb_search.best_estimator_
results['xgboost_tuned'], xgb_tuned_proba = evaluate('XGBoost tuned', xgb_tuned, fit=False)
print(f'    Best CV AUC: {xgb_search.best_score_:.4f} | params: {xgb_search.best_params_}')

# ── 6. CatBoost ───────────────────────────────────────────────────────────────
print('\n[6] CatBoost')
# CatBoost handles NaNs natively — no imputer needed
X_train_np = X_train.values.astype(float)
X_test_np  = X_test.values.astype(float)

catboost_param_dist = {
    'iterations':    [200, 300, 400, 500],
    'depth':         [4, 5, 6, 7, 8],
    'learning_rate': uniform(0.02, 0.12),
    'l2_leaf_reg':   uniform(1, 9),
    'subsample':     uniform(0.6, 0.4),
    'colsample_bylevel': uniform(0.6, 0.4),
}
best_cb_auc, best_cb_params = 0, {}
cb_proba = None

# Manual grid since CatBoost doesn't play nicely with sklearn CV on NaN data
from itertools import product
import random
random.seed(42)

cb_candidates = [
    {'iterations': it, 'depth': d, 'learning_rate': round(lr, 3),
     'l2_leaf_reg': round(l2, 2), 'subsample': round(ss, 2)}
    for it in [300, 400, 500]
    for d in [4, 5, 6]
    for lr in [0.03, 0.05, 0.08, 0.10]
    for l2 in [1, 3, 5]
    for ss in [0.7, 0.8, 0.9]
]
random.shuffle(cb_candidates)
cb_candidates = cb_candidates[:30]  # sample 30

print(f'    Testing {len(cb_candidates)} CatBoost configs...')
for params in cb_candidates:
    cb = CatBoostClassifier(**params, random_state=42, verbose=0, allow_writing_files=False)
    # Simple time-split CV
    fold_aucs = []
    for fold_train_idx, fold_val_idx in tscv.split(X_train_np):
        X_f_tr, X_f_val = X_train_np[fold_train_idx], X_train_np[fold_val_idx]
        y_f_tr, y_f_val = y_train.iloc[fold_train_idx], y_train.iloc[fold_val_idx]
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            cb.fit(X_f_tr, y_f_tr)
        fold_aucs.append(roc_auc_score(y_f_val, cb.predict_proba(X_f_val)[:, 1]))
    cv_auc = np.mean(fold_aucs)
    if cv_auc > best_cb_auc:
        best_cb_auc = cv_auc
        best_cb_params = params

best_cb = CatBoostClassifier(**best_cb_params, random_state=42, verbose=0, allow_writing_files=False)
with warnings.catch_warnings():
    warnings.simplefilter('ignore')
    best_cb.fit(X_train_np, y_train)
    cb_proba_raw = best_cb.predict_proba(X_test_np)[:, 1]
cb_auc = roc_auc_score(y_test, cb_proba_raw)
results['catboost'], cb_proba = cb_auc, cb_proba_raw
print(f'  {"CatBoost (tuned)":<35} AUC: {cb_auc:.4f}')
print(f'    Best CV AUC: {best_cb_auc:.4f} | params: {best_cb_params}')

# ── 7. Stacking ensemble ──────────────────────────────────────────────────────
# Note: TimeSeriesSplit can't be used with StackingClassifier (requires partitions)
# Use cv=5 for meta-feature generation; base models were already tuned with TimeSeriesSplit
print('\n[7] Stacking ensemble (LR + LGBM + CatBoost base → LR meta)')

class CatBoostSklearn(BaseEstimator, ClassifierMixin):
    """Thin sklearn-compatible wrapper for CatBoost."""
    def __init__(self, **params):
        self.params = params
        self.model_ = None
        self.classes_ = np.array([0, 1])
    def fit(self, X, y):
        self.model_ = CatBoostClassifier(**self.params, random_state=42, verbose=0, allow_writing_files=False)
        self.model_.fit(X.values if hasattr(X, 'values') else X, y)
        return self
    def predict_proba(self, X):
        return self.model_.predict_proba(X.values if hasattr(X, 'values') else X)
    def predict(self, X):
        return self.model_.predict(X.values if hasattr(X, 'values') else X)

base_estimators = [
    ('lr',   Pipeline([('pre', impute_scale), ('clf', LogisticRegression(max_iter=1000, random_state=42))])),
    ('lgbm', lgbm_tuned),
    ('cb',   Pipeline([('pre', impute), ('clf', CatBoostSklearn(**best_cb_params))])),
]
stack = StackingClassifier(
    estimators=base_estimators,
    final_estimator=LogisticRegression(max_iter=1000, random_state=42),
    cv=5,
    passthrough=False,
    n_jobs=1,
)
with warnings.catch_warnings():
    warnings.simplefilter('ignore')
    stack.fit(X_train, y_train)
    stack_proba = stack.predict_proba(X_test)[:, 1]
stack_auc = roc_auc_score(y_test, stack_proba)
results['stacking_lr_lgbm_cb'] = stack_auc
print(f'  {"Stacking (LR+LGBM+CB → LR)":<35} AUC: {stack_auc:.4f}')

# ── 8. Probability blend (top 3 models) ───────────────────────────────────────
print('\n[8] Probability blend')
# Blend top performers: LightGBM tuned + CatBoost + LR (equal weights)
blend_proba = (lgbm_tuned_proba + cb_proba_raw + lr_proba) / 3.0
blend_auc = roc_auc_score(y_test, blend_proba)
results['blend_lgbm_cb_lr'] = blend_auc
print(f'  {"Blend (LGBM+CB+LR equal weights)":<35} AUC: {blend_auc:.4f}')

# Weighted blend: give more weight to best individual model
blend_w_proba = (0.5 * lgbm_tuned_proba + 0.3 * cb_proba_raw + 0.2 * lr_proba)
blend_w_auc = roc_auc_score(y_test, blend_w_proba)
results['blend_weighted'] = blend_w_auc
print(f'  {"Blend weighted (0.5+0.3+0.2)":<35} AUC: {blend_w_auc:.4f}')

# ── Summary ───────────────────────────────────────────────────────────────────
print(f'\n{"═"*50}')
print(f'  {"SUMMARY":<35}')
print(f'{"─"*50}')
print(f'  {"Baseline (hand-crafted)":<35} {baseline_auc:.4f}')
for name, auc in sorted(results.items(), key=lambda x: -x[1]):
    delta = auc - baseline_auc
    marker = ' ← BEST' if auc == max(results.values()) else ''
    print(f'  {name:<35} {auc:.4f}  ({delta:+.4f}){marker}')

# ── Save best model ───────────────────────────────────────────────────────────
best_name = max(results, key=lambda k: results[k])
best_auc  = results[best_name]

model_map = {
    'logistic_regression':  lr,
    'random_forest':        rf,
    'lightgbm_default':     lgbm,
    'lightgbm_tuned':       lgbm_tuned,
    'xgboost_tuned':        xgb_tuned,
    'catboost':             Pipeline([('pre', impute), ('clf', CatBoostSklearn(**best_cb_params))]),
    'stacking_lr_lgbm_cb':  stack,
}

# Blends can't be saved as a single sklearn pipeline — fall back to best non-blend
if best_name.startswith('blend'):
    print(f'\nNote: best model is a blend ({best_name}) — saving lightgbm_tuned as deployable model.')
    save_name = 'lightgbm_tuned'
else:
    save_name = best_name

save_model = model_map[save_name]

# For catboost, refit on full training data with the CatBoostSklearn wrapper
if save_name == 'catboost':
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        save_model.fit(X_train, y_train)

models_dir = os.path.join(PROJECT_ROOT, 'data', 'models')
os.makedirs(models_dir, exist_ok=True)
model_path = os.path.join(models_dir, 'hit_predictor_lightgbm.joblib')
joblib.dump({
    'model': save_model,
    'feature_cols': FEATURE_COLS,
    'auc': results[save_name],
    'baseline_auc': baseline_auc,
    'model_name': save_name,
}, model_path)

print(f'\nSaved: {save_name} (AUC {results[save_name]:.4f}) → {model_path}')
if best_name != save_name:
    print(f'(Best overall was {best_name} at {best_auc:.4f} but it\'s a blend — not deployable as-is)')
