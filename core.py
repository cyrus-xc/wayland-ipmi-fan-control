"""Temperature sampling and curve evaluation for the IPMI controller."""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import subprocess

STATE = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'gigabyte-fan-control'
DEFAULT = {'points': [[30, 35], [45, 45], [60, 65], [75, 85], [85, 100]]}


def save_json(path, data):
    """Atomically replace a private state file."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix('.tmp')
    with open(tmp, 'w', encoding='utf-8', opener=lambda p, f: os.open(p, f, 0o600)) as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
    tmp.replace(path)


def validate(config):
    """Validate a local temperature-to-duty curve."""
    points = config['points']
    if len(points) < 2:
        raise ValueError('至少需要两个温度点')
    previous = (-1, 0)
    for temperature, duty in points:
        if not all(math.isfinite(v) for v in (temperature, duty)):
            raise ValueError('曲线必须是有限数值')
        if not (0 <= temperature <= 110 and 30 <= duty <= 100):
            raise ValueError('温度范围 0–110°C，占空比范围 30–100%')
        if temperature <= previous[0] or duty < previous[1]:
            raise ValueError('温度必须递增，转速不能随温度升高而降低')
        previous = temperature, duty
    if points[-1][1] != 100 or points[-1][0] > 90:
        raise ValueError('最后一个点必须在不高于 90°C 时达到 100%')


def duty_at(points, temperature):
    """Calculate the target duty percentage from the curve."""
    if temperature >= 90:
        return 100
    for index, (temp, duty) in enumerate(points):
        if temperature <= temp:
            if index == 0:
                return int(math.ceil(duty))
            t0, d0 = points[index - 1]
            return int(math.ceil(d0 + (duty - d0) * (temperature - t0) / (temp - t0)))
    return 100


def sample_temperatures():
    """Require CPU hwmon and NVIDIA temperatures; include other GPU hwmon."""
    sensors = json.loads(subprocess.check_output(['sensors', '-j'], timeout=4))
    cpu, gpu = [], []
    for name, chip in sensors.items():
        target = cpu if name.startswith(('k10temp-', 'coretemp-')) else gpu if name.startswith(('nouveau-', 'amdgpu-')) else None
        if target is None:
            continue
        for label, values in chip.items():
            if isinstance(values, dict):
                for key, value in values.items():
                    if key.endswith('_input') and key.startswith('temp'):
                        target.append((f'{name} / {label}', float(value)))
    output = subprocess.check_output(['nvidia-smi', '--query-gpu=name,temperature.gpu', '--format=csv,noheader,nounits'], timeout=4, text=True)
    for line in output.splitlines():
        name, temperature = line.rsplit(',', 1)
        gpu.append((name.strip(), float(temperature)))
    if not cpu or not gpu or not all(math.isfinite(v) and 0 <= v <= 130 for _, v in cpu + gpu):
        raise ValueError('CPU 或 GPU 温度缺失/异常')
    return {'cpu': max(v for _, v in cpu), 'gpu': max(v for _, v in gpu), 'sensors': cpu + gpu}
