"""Weather first: independent live outlook plus explicitly labeled planning data."""
import json
from pathlib import Path
import pandas as pd
import streamlit as st
from weather import fetch_outlook

@st.cache_data(ttl=1800, show_spinner=False)
def cached_outlook(latitude, longitude):
    # Cache per coordinate so a farm change cannot display another location's weather.
    return fetch_outlook(latitude, longitude)

def show_weather(root, config, generation):
    defaults = json.loads((Path(root) / 'farm_location.json').read_text())
    previous = config.get('weather_location', {})
    old_placeholder = (abs(float(previous.get('latitude', 35.2)) - 35.2) < 1e-5 and
                       abs(float(previous.get('longitude', -119.05)) + 119.05) < 1e-5)
    # Migrate the former approximate default in an already-open app session.
    # This changes the display location, never previously fetched model inputs.
    location = defaults.copy() if old_placeholder and not previous.get('address') else {**defaults, **previous}
    st.subheader('Farm weather · Mettler area')
    st.caption(location['address'])
    with st.expander('Farm location and weather source'):
        latitude = st.number_input('Farm latitude', min_value=-90., max_value=90.,
            value=float(location['latitude']), format='%.6f', key=f'farm_lat_v3_{generation}')
        longitude = st.number_input('Farm longitude', min_value=-180., max_value=180.,
            value=float(location['longitude']), format='%.6f', key=f'farm_lon_v3_{generation}')
        verified = st.checkbox('These coordinates have been checked against our farm location',
            value=location.get('verified', False), key=f'farm_verified_v3_{generation}')
        st.caption('Approximate address geocode, not a surveyed field boundary. Adjust the point for your actual field. Temperatures: °F; rainfall: inches; times: Pacific.')
        st.map(pd.DataFrame({'lat': [latitude], 'lon': [longitude]}), zoom=12)
        st.markdown('[Address geocode source](' + defaults['geocoder_url'] + ') · [Open-Meteo forecasts](https://open-meteo.com/en/docs) · [API usage terms](https://open-meteo.com/en/terms)')
    location.update(latitude=latitude, longitude=longitude, verified=verified)
    # Changing the location invalidates any claim that the address itself has been checked.
    if (latitude, longitude) != (defaults['latitude'], defaults['longitude']):
        location['location_basis'] = 'User-edited coordinate point near the supplied approximate farm address'
    config['weather_location'] = location
    refresh = st.button('Refresh live weather', key='refresh_live_weather')
    if refresh:
        cached_outlook.clear()
    try:
        with st.spinner('Loading farm-area weather...'):
            daily, current, metadata = cached_outlook(latitude, longitude)
    except Exception as error:
        st.warning(f'Live weather is unavailable: {error}. You can still use the planning inputs and run the model.')
        return location, None
    today = daily.iloc[0]
    cards = st.columns(4)
    cards[0].metric('Current temperature', f"{current['temperature_2m']:.0f} °F")
    cards[1].metric('Today · high / low', f"{today.maximum_temperature:.0f} / {today.minimum_temperature:.0f} °F")
    cards[2].metric('Today · rain chance', f'{today.rain_probability_pct:.0f}%')
    cards[3].metric('Next 7 days · rainfall', f'{daily.precipitation.iloc[:7].sum():.2f} in')
    st.caption(f"Modeled current estimate: {current['time'].replace('T', ' ')} Pacific · Forecast fetched {pd.Timestamp(metadata['fetched_at_utc']).tz_convert('America/Los_Angeles').strftime('%b %d, %I:%M %p')} · Cached up to 30 minutes")
    horizon = st.radio('Live forecast horizon', ['Next 7 days', 'Next 16 days'], horizontal=True, key='live_weather_horizon')
    selected = daily.iloc[:7 if horizon == 'Next 7 days' else 16]
    if metadata.get('unavailable_dates'):
        st.caption('Some later dates have unavailable provider values. Blank cells mean unavailable; no weather values are invented.')
    display = selected.rename(columns={'date': 'Date', 'maximum_temperature': 'High °F',
        'minimum_temperature': 'Low °F', 'average_temperature': 'Average °F',
        'precipitation': 'Rainfall in', 'rain_probability_pct': 'Rain chance %'}).copy()
    display['Date'] = display.Date.dt.strftime('%a %b %d')
    st.dataframe(display[['Date', 'High °F', 'Low °F', 'Rain chance %', 'Rainfall in']], hide_index=True,
        column_config={'Rainfall in': st.column_config.NumberColumn(format='%.3f'),
                       'High °F': st.column_config.NumberColumn(format='%.1f'),
                       'Low °F': st.column_config.NumberColumn(format='%.1f')})
    with st.expander('Forecast charts and download'):
        st.line_chart(selected.set_index('date')[['maximum_temperature', 'minimum_temperature']])
        st.bar_chart(selected.set_index('date')[['precipitation']])
        st.download_button('Download daily farm forecast', daily.to_csv(index=False), 'farm_daily_forecast.csv', 'text/csv')
        st.download_button('Download live weather source details', json.dumps(metadata, indent=2), 'live_weather_source.json', 'application/json')
    st.caption('Live regional weather is separate from synthetic sales/demo weather. Beyond 16 days, the model uses supplied planning assumptions, not a live meteorological forecast. Weather does not automatically change crop yield.')
    config['displayed_weather_provenance'] = metadata
    st.divider()
    return location, dict(daily=daily, current=current, metadata=metadata)

