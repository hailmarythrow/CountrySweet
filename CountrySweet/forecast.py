"""Four forecasting methods and fair, fixed-origin chronological backtests."""
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import OneHotEncoder
from sklearn.ensemble import RandomForestRegressor
from features import KEYS, CATEGORICAL, NUMERIC, training_features, next_features

METHODS = ['naive', 'moving_average_4', 'weighted_moving_average', 'machine_learning']

def fit_model(history, config):
    # Preprocessing is fitted ONLY on training data at each origin.
    numeric = SimpleImputer(strategy='median', keep_empty_features=True, add_indicator=True)
    categorical = OneHotEncoder(handle_unknown='ignore', sparse_output=False)
    preprocess = ColumnTransformer([('numeric', numeric, NUMERIC), ('category', categorical, CATEGORICAL)])
    model = Pipeline([('features', preprocess), ('regressor', RandomForestRegressor(
        n_estimators=config['trees'], min_samples_leaf=config['min_samples_leaf'],
        random_state=config['seed'], n_jobs=config.get('model_jobs', 1)))])
    rows = training_features(history)
    model.fit(rows[CATEGORICAL + NUMERIC], rows.sales_lbs)
    return model

def predict(history, future, method, config, model=None):
    """Recursive multi-week prediction. Each method owns its predicted sales history."""
    past = history.copy()
    results = []
    for week, rows in future.groupby('week', sort=True):
        rows = next_features(rows.copy(), past)
        if method == 'machine_learning':
            estimates = model.predict(rows[CATEGORICAL + NUMERIC])
        else:
            estimates = []
            for _, row in rows.iterrows():
                values = past[(past.sku == row.sku) & (past.customer == row.customer)].sort_values('week').sales_lbs.to_numpy()
                if method == 'naive':
                    value = values[-1]
                elif method == 'moving_average_4':
                    value = values[-4:].mean()
                else:
                    weights = np.asarray(config['moving_average_weights'], dtype=float)
                    if len(weights) != 4 or not np.isfinite(weights).all() or (weights < 0).any() or weights.sum() <= 0:
                        raise ValueError('Supply four nonnegative moving-average weights, oldest to newest.')
                    recent = values[-4:]
                    value = np.average(recent, weights=weights[-len(recent):])
                estimates.append(value)
        rows['prediction_lbs'] = np.maximum(estimates, 0)
        rows['model'] = method
        results.append(rows)
        simulated = rows.copy()
        simulated['sales_lbs'] = simulated.prediction_lbs
        past = pd.concat([past, simulated], ignore_index=True)
    return pd.concat(results, ignore_index=True)

def metrics(rows):
    actual, predicted = rows.actual_lbs.to_numpy(), rows.prediction_lbs.to_numpy()
    error = predicted - actual  # Positive bias means overforecasting.
    nonzero = actual != 0
    return dict(n=len(actual), MAE_lbs=np.abs(error).mean(), RMSE_lbs=np.sqrt((error ** 2).mean()),
                MAPE_pct=100 * np.mean(np.abs(error[nonzero] / actual[nonzero])) if nonzero.any() else np.nan,
                MAPE_excluded_zero_rows=int((~nonzero).sum()),
                WAPE_pct=100 * np.abs(error).sum() / actual.sum() if actual.sum() else np.nan,
                bias_lbs=error.mean(), bias_pct=100 * error.sum() / actual.sum() if actual.sum() else np.nan)

def validate(history, config):
    weeks = sorted(history.week.unique())
    horizon, folds = config['validation_horizon_weeks'], config['validation_folds']
    if horizon < 1 or folds < 1 or len(weeks) < config['min_training_weeks'] + horizon * folds:
        raise ValueError('Insufficient history for configured time-ordered validation; reduce folds/horizon explicitly.')
    predictions = []
    for fold in range(folds):
        start = len(weeks) - (folds - fold) * horizon
        train = history[history.week < weeks[start]]
        test = history[history.week.isin(weeks[start:start + horizon])]
        # Holdout sales are not passed into feature construction or recursive lags.
        known = test.drop(columns='sales_lbs')
        model = fit_model(train, config)
        for method in METHODS:
            pred = predict(train, known, method, config, model)
            pred = pred.merge(test[KEYS + ['week', 'sales_lbs']], on=KEYS + ['week'], validate='one_to_one')
            pred = pred.rename(columns={'sales_lbs': 'actual_lbs'})
            pred['fold'] = fold + 1
            pred['origin'] = train.week.max()
            predictions.append(pred)
    scored = pd.concat(predictions, ignore_index=True)
    tables = []
    for method, group in scored.groupby('model'):
        tables.append(dict(model=method, scope='all', sku='ALL', customer='ALL', **metrics(group)))
    for (method, sku, customer), group in scored.groupby(['model'] + KEYS):
        tables.append(dict(model=method, scope='series', sku=sku, customer=customer, **metrics(group)))
    return pd.DataFrame(tables), scored
