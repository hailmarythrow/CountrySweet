"""Small non-UI helpers: save reproducible runs without changing original inputs."""
from datetime import datetime
import json
from pathlib import Path
import uuid
import zipfile
import io
import subprocess
import sys
import pandas as pd

INPUTS = ['history', 'future', 'products', 'crops', 'promotions']
ALLOWED = {f'{name}.csv' for name in INPUTS} | {'config.json', 'weather_normal.csv', 'weather_adverse.csv', 'weather_favorable.csv'}

def read_dataset(path):
    path = Path(path)
    frames = {name: pd.read_csv(path / f'{name}.csv') for name in INPUTS}
    config = json.loads((path / 'config.json').read_text())
    required = {'synthetic_demo', 'total_available_acres', 'yield_multipliers',
                'high_demand_multiplier', 'require_full_coverage', 'missing_cost_per_acre',
                'validation_horizon_weeks', 'validation_folds', 'min_training_weeks',
                'moving_average_weights', 'trees', 'min_samples_leaf', 'seed', 'optional_defaults'}
    if required - config.keys():
        raise ValueError(f'config.json missing settings: {sorted(required - config.keys())}. Use the input template.')
    for name, columns in [('history', ['week', 'sku']), ('future', ['week', 'sku']),
                          ('products', ['sku', 'crop_group', 'organic_status', 'package_format'])]:
        if set(columns) - set(frames[name]):
            raise ValueError(f'{name}.csv missing required columns: {columns}.')
    for name in ['history', 'future']:
        frames[name]['week'] = pd.to_datetime(frames[name].week, errors='raise').dt.strftime('%Y-%m-%d')
        if frames[name].empty or frames[name].week.isna().any():
            raise ValueError(f'{name}.csv must contain dated rows.')
    extra = {name: (path / name).read_bytes() for name in ALLOWED if name.startswith('weather_') and (path / name).exists()}
    return frames, config, extra

def import_zip(content, root):
    """Read allowed input bytes only; never extract arbitrary ZIP paths or code."""
    target = Path(root) / ('import_' + uuid.uuid4().hex[:10])
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        selected = {}
        for entry in archive.infolist():
            name = Path(entry.filename.replace('\\', '/')).name
            if name not in ALLOWED:
                continue
            if name in selected:
                raise ValueError(f'Duplicate input filename: {name}. Include only one dataset.')
            if entry.file_size > 30_000_000 or sum(len(v) for v in selected.values()) + entry.file_size > 100_000_000:
                raise ValueError('Dataset ZIP exceeds the local app upload limit.')
            selected[name] = archive.read(entry)
    required = {f'{name}.csv' for name in INPUTS} | {'config.json'}
    if required - selected.keys():
        raise ValueError(f'ZIP missing required inputs: {sorted(required - selected.keys())}')
    target.mkdir(parents=True)
    for name, content in selected.items():
        (target / name).write_bytes(content)
    return target

def run_plan(frames, config, extra, root):
    """Each run saves input snapshots and all CSV results in a distinct folder."""
    directory = Path(root) / ('run_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6])
    inputs, outputs = directory / 'inputs', directory / 'outputs'
    inputs.mkdir(parents=True)
    for name, frame in frames.items():
        frame.to_csv(inputs / f'{name}.csv', index=False)
    (inputs / 'config.json').write_text(json.dumps(config, indent=2))
    for name, content in extra.items():
        if name in ALLOWED and name.startswith('weather_'):
            (inputs / name).write_bytes(content)
    # Run the same tested CLI engine. No model logic is duplicated in the app.
    result = subprocess.run([sys.executable, str(Path(__file__).with_name('main.py')),
        '--data', str(inputs.resolve()), '--output', str(outputs.resolve())], capture_output=True, text=True, timeout=600)
    (directory / 'run_log.txt').write_text(result.stdout + '\n' + result.stderr)
    if result.returncode:
        raise ValueError(result.stderr.strip().splitlines()[-1] if result.stderr.strip() else 'Model run failed; see run_log.txt.')
    tables = {name: pd.read_csv(outputs / f'{name}.csv') for name in ['forecast', 'acreage_recommendation', 'scenario_comparison', 'model_accuracy']}
    assumptions = json.loads((outputs / 'assumptions.json').read_text())
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for file in directory.rglob('*'):
            if file.is_file():
                archive.write(file, file.relative_to(directory))
    return dict(tables=tables, assumptions=assumptions, directory=str(directory.resolve()), zip=buffer.getvalue())
