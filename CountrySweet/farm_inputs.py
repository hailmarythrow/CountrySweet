"""Farmer-facing units and explicit unknown values, separate from CSV decimals."""
import pandas as pd
import streamlit as st

def optional_number(column, label, value, key, help=None, minimum=0.):
    return column.number_input(label, min_value=float(minimum),
        value=None if value is None or pd.isna(value) else float(value),
        key=key, placeholder='Not supplied', help=help)

def show_farm_inputs(crops, config, generation):
    crops = crops.copy()
    st.subheader('Farm resources and production limits')
    limits = config.setdefault('farm_constraints', {})
    columns = st.columns(3)
    for col, field, label in zip(columns,
        ['available_irrigation_acre_ft', 'maximum_harvest_lbs_per_week', 'maximum_packing_lbs_per_week'],
        ['Available irrigation water · acre-ft', 'Maximum harvest capacity · lb/week', 'Maximum packing capacity · lb/week']):
        limits[field] = optional_number(col, label, limits.get(field), f'{field}_{generation}')
    st.caption('Blank means not assessed. Capacities limit average throughput across the planning horizon; they do not verify weekly peaks or harvest timing.')
    dates = st.columns(2)
    start = pd.to_datetime(limits.get('planting_start')).date() if limits.get('planting_start') else None
    end = pd.to_datetime(limits.get('planting_end')).date() if limits.get('planting_end') else None
    start = dates[0].date_input('Planting window · start', value=start, key=f'planting_start_{generation}')
    end = dates[1].date_input('Planting window · end', value=end, key=f'planting_end_{generation}')
    limits['planting_start'] = str(start) if start else None
    limits['planting_end'] = str(end) if end else None
    if start and end and start > end:
        st.error('Planting window starts after its end. Correct the dates before running.')
    st.caption('Planting window and maturity are recorded context. This prototype does not schedule planting or harvest dates.')
    st.subheader('Crop production assumptions')
    for i, row in crops.iterrows():
        suffix = f'{generation}_{i}'
        with st.expander(f'{row.crop_group} · {row.organic_status}', expanded=i == crops.index[0]):
            columns = st.columns(3)
            crops.loc[i, 'expected_lbs_per_acre'] = columns[0].number_input('Expected yield · lb/ac', min_value=1.,
                value=float(row.expected_lbs_per_acre), key=f'expected_yield_{suffix}')
            for col, field, label in [(columns[1], 'historical_yield_low', 'Historical yield range · low lb/ac'),
                                       (columns[2], 'historical_yield_high', 'Historical yield range · high lb/ac')]:
                crops.loc[i, field] = optional_number(col, label, row.get(field), f'{field}_{suffix}')
            columns = st.columns(3)
            crops.loc[i, 'packout_rate'] = columns[0].number_input('Packout rate · %', min_value=.01, max_value=100.,
                value=float(row.packout_rate) * 100, key=f'packout_{suffix}') / 100
            storage = optional_number(columns[1], 'Storage loss · %', row.get('storage_loss_rate') * 100 if pd.notna(row.get('storage_loss_rate')) else None,
                f'storage_{suffix}', help='Blank assumes zero loss in the optimizer and is flagged.')
            crops.loc[i, 'storage_loss_rate'] = storage / 100 if storage is not None else float('nan')
            crops.loc[i, 'safety_stock_pct'] = columns[2].number_input('Safety stock · %', min_value=0.,
                value=float(row.safety_stock_pct) * 100, key=f'safety_{suffix}') / 100
            columns = st.columns(2)
            crops.loc[i, 'water_need_acre_ft_per_acre'] = optional_number(columns[0], 'Estimated water need · acre-ft/ac',
                row.get('water_need_acre_ft_per_acre'), f'water_{suffix}', help='User-supplied water estimate, not an agronomic recommendation.')
            crops.loc[i, 'expected_maturity_days'] = optional_number(columns[1], 'Expected maturity · days',
                row.get('expected_maturity_days'), f'maturity_{suffix}', minimum=1.)
            columns = st.columns(3)
            for col, field, label in [(columns[0], 'cost_per_acre', 'Production cost · currency/ac'),
                                       (columns[1], 'shortage_penalty_per_lb', 'Underforecast / shortage cost · currency/lb'),
                                       (columns[2], 'excess_penalty_per_lb', 'Overforecast / excess cost · currency/lb')]:
                original = row.get(field)
                if original is None or pd.isna(original):
                    original = config['missing_cost_per_acre'] if field == 'cost_per_acre' else 0.
                    config.setdefault('assumptions', []).append(f'{row.crop_group}/{row.organic_status}: missing {field} shown as {original}; review this assumption.')
                crops.loc[i, field] = col.number_input(label, min_value=0., value=float(original), key=f'{field}_{suffix}')
            st.caption('The historical yield range documents observed variability; it does not automatically change expected yield or weather multipliers.')
    return crops
