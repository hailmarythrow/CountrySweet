"""Coordinate-specific Kern County weather, without agronomic yield claims.

Open-Meteo data is a modeled grid-cell estimate, not a farm weather station.
We keep complete Monday-Sunday weeks only. Missing days/hours cause an error.
Temperature is Fahrenheit, rainfall is inches, and GDD is Fahrenheit-days.
"""
from datetime import datetime, timezone
import json
from urllib.parse import urlencode
from urllib.request import urlopen
import numpy as np
import pandas as pd

def fetch_outlook(latitude, longitude):
    """Live weather and 16 DAILY forecast dates, independent of sales/demo dates.

    A longer production horizon is not a reason to hide the available forecast.
    These observations are display-only until explicitly applied to model inputs.
    """
    if not np.isfinite([latitude, longitude]).all() or not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError('Invalid latitude/longitude.')
    params = dict(latitude=latitude, longitude=longitude, forecast_days=16,
        current='temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m',
        hourly='temperature_2m',
        daily='temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max',
        temperature_unit='fahrenheit', precipitation_unit='inch', wind_speed_unit='mph',
        timezone='America/Los_Angeles')
    with urlopen('https://api.open-meteo.com/v1/forecast?' + urlencode(params), timeout=25) as response:
        payload = json.load(response)
    daily = pd.DataFrame(dict(date=pd.to_datetime(payload['daily']['time']),
        maximum_temperature=payload['daily']['temperature_2m_max'],
        minimum_temperature=payload['daily']['temperature_2m_min'],
        precipitation=payload['daily']['precipitation_sum'],
        rain_probability_pct=payload['daily']['precipitation_probability_max']))
    hours = pd.DataFrame(dict(date=pd.to_datetime([v[:10] for v in payload['hourly']['time']]),
                             temperature=payload['hourly']['temperature_2m']))
    counts = hours.groupby('date').temperature.agg(['count', 'size'])
    complete = (counts['count'] == counts['size']) & (counts['count'] >= 23)
    means = hours.groupby('date').temperature.mean().where(complete)
    daily['average_temperature'] = daily.date.map(means)
    numeric = daily.drop(columns='date').to_numpy(dtype=float)
    if len(daily) != 16 or np.isinf(numeric).any():
        raise ValueError('Provider returned invalid daily forecasts; try refreshing later.')
    # Providers sometimes lack the final day's values. Display the gap explicitly;
    # do not turn it into a made-up forecast or suppress the other available days.
    required = ['maximum_temperature', 'minimum_temperature', 'precipitation', 'rain_probability_pct']
    if not np.isfinite(daily.iloc[0][required].to_numpy(dtype=float)).all():
        raise ValueError('Provider has no complete forecast for today; try refreshing later.')
    current = payload.get('current', {})
    if not current.get('time') or current.get('temperature_2m') is None:
        raise ValueError('Provider returned no current weather estimate.')
    metadata = dict(provider='Open-Meteo', source_kind='live_outlook',
        source_url='https://open-meteo.com/en/docs',
        requested_latitude=latitude, requested_longitude=longitude,
        grid_latitude=payload.get('latitude'), grid_longitude=payload.get('longitude'),
        fetched_at_utc=datetime.now(timezone.utc).isoformat(),
        current_time_local=current['time'], temperature_unit='F', precipitation_unit='inches',
        timezone='America/Los_Angeles', start_date=str(daily.date.min().date()),
        end_date=str(daily.date.max().date()),
        unavailable_dates=daily.loc[daily[required].isna().any(axis=1), 'date'].dt.strftime('%Y-%m-%d').tolist(),
        caveat='Modeled grid estimate, not an on-farm station; outlook is separate from planning inputs until explicitly applied.')
    return daily, current, metadata

def aggregate_weekly(daily, weeks, heat_threshold=None, gdd_base=None):
    """Mean air temperature, weekly extremes, summed rain and optional user rules."""
    daily = daily.copy()
    daily['date'] = pd.to_datetime(daily.date).dt.normalize()
    if daily.date.duplicated().any():
        raise ValueError('Duplicate weather dates.')
    columns = ['average_temperature', 'maximum_temperature', 'minimum_temperature', 'precipitation']
    if not np.isfinite(daily[columns].to_numpy(dtype=float)).all():
        raise ValueError('Weather has missing/nonfinite values; no values are invented.')
    if (daily.precipitation < 0).any() or (daily.maximum_temperature < daily.minimum_temperature).any():
        raise ValueError('Weather rainfall or temperature bounds are invalid.')
    output = []
    for week in sorted(pd.to_datetime(pd.Series(weeks)).unique()):
        week = pd.Timestamp(week)
        if week.dayofweek != 0:
            raise ValueError('Weather weeks must begin on Monday.')
        days = daily[daily.date.between(week, week + pd.Timedelta(days=6))]
        if len(days) != 7:
            raise ValueError(f'No complete seven-day weather coverage for {week.date()}. Supply weather for the missing dates.')
        midpoint = (days.maximum_temperature + days.minimum_temperature) / 2
        output.append(dict(week=week, average_temperature=days.average_temperature.mean(),
            maximum_temperature=days.maximum_temperature.max(),
            minimum_temperature=days.minimum_temperature.min(), precipitation=days.precipitation.sum(),
            growing_degree_days=np.maximum(midpoint - gdd_base, 0).sum() if gdd_base is not None else np.nan,
            heat_event=int((days.maximum_temperature >= heat_threshold).any()) if heat_threshold is not None else np.nan))
    return pd.DataFrame(output)

def fetch_weather(latitude, longitude, weeks, kind='historical', heat_threshold=None, gdd_base=None):
    """Fetch only selected coordinates/dates; return weekly observations and provenance."""
    if not np.isfinite([latitude, longitude]).all() or not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError('Invalid latitude/longitude.')
    weeks = pd.to_datetime(pd.Series(weeks)).drop_duplicates().sort_values()
    if weeks.empty:
        raise ValueError('No weeks supplied.')
    start, end = weeks.min(), weeks.max() + pd.Timedelta(days=6)
    today = pd.Timestamp.now(tz='America/Los_Angeles').tz_localize(None).normalize()
    if kind == 'historical':
        if end >= today:
            raise ValueError('Historical weather requires completed dates; recent observations may be delayed.')
        endpoint = 'https://archive-api.open-meteo.com/v1/archive'
    elif kind == 'forecast':
        if start < today or end > today + pd.Timedelta(days=15):
            raise ValueError('Live weather supports complete weeks within the next 16 days only. Use supplied scenario weather for longer planning horizons.')
        endpoint = 'https://api.open-meteo.com/v1/forecast'
    else:
        raise ValueError('Weather kind must be historical or forecast.')
    params = dict(latitude=latitude, longitude=longitude, start_date=str(start.date()),
        end_date=str(end.date()), hourly='temperature_2m',
        daily='temperature_2m_max,temperature_2m_min,precipitation_sum',
        temperature_unit='fahrenheit', precipitation_unit='inch', timezone='America/Los_Angeles')
    with urlopen(endpoint + '?' + urlencode(params), timeout=60) as response:
        payload = json.load(response)
    if 'daily' not in payload or 'hourly' not in payload:
        raise ValueError('Weather provider returned no usable data.')
    hour = pd.DataFrame({'date': pd.to_datetime([s[:10] for s in payload['hourly']['time']]),
                         'temperature': payload['hourly']['temperature_2m']})
    # A local day can have 23 or 25 hours at daylight-saving transitions.
    counts = hour.groupby('date').temperature.agg(['count', 'size'])
    if (counts['count'] != counts['size']).any() or (counts['count'] < 23).any():
        raise ValueError('Incomplete hourly weather; refusing to silently fill temperature.')
    means = hour.groupby('date').temperature.mean()
    daily = pd.DataFrame({'date': pd.to_datetime(payload['daily']['time']),
        'maximum_temperature': payload['daily']['temperature_2m_max'],
        'minimum_temperature': payload['daily']['temperature_2m_min'],
        'precipitation': payload['daily']['precipitation_sum']})
    daily['average_temperature'] = daily.date.map(means)
    weekly = aggregate_weekly(daily, weeks, heat_threshold, gdd_base)
    metadata = dict(provider='Open-Meteo', source_kind=kind,
        source_url='https://open-meteo.com/en/docs/historical-weather-api' if kind == 'historical' else 'https://open-meteo.com/en/docs',
        requested_latitude=latitude, requested_longitude=longitude,
        grid_latitude=payload.get('latitude'), grid_longitude=payload.get('longitude'),
        fetched_at_utc=datetime.now(timezone.utc).isoformat(),
        temperature_unit='F', precipitation_unit='inches', timezone='America/Los_Angeles',
        heat_threshold_F=heat_threshold, gdd_base_F=gdd_base,
        gdd_definition='sum(max(0, daily(max+min)/2 - user_base)); no upper cap' if gdd_base is not None else 'not computed',
        weekly_definition='Monday-Sunday; hourly-derived daily mean averaged; maximum and minimum are week extremes; precipitation is seven-day sum',
        start_date=str(start.date()), end_date=str(end.date()),
        caveat='Modeled grid-cell weather; not farm station data. No soil/yield effect inferred.')
    return weekly, metadata
