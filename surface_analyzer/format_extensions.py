"""Versioned, optional file-reader extensions (no UI or metrology code)."""
from __future__ import annotations

import importlib.util
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .version import APP_VERSION
from .workers import TaskCancelled

API_VERSION = 1


def extension_root():
    base = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local' / 'share'))
    return base / 'SurfaceRxyZxyAnalyzer' / 'format_extensions'


def _version(value):
    parts = str(value).lstrip('Vv').split('.')
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError('扩展版本必须为 x.y.z')
    return tuple(map(int, parts))


@dataclass
class ReadContext:
    progress: object = None
    cancel_event: object = None
    layer_id: str | None = None

    def check_cancel(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise TaskCancelled()

    def report(self, percent, message):
        self.check_cancel()
        if self.progress:
            self.progress(percent, message)


def validate_result(result):
    """API v1 uses physical mm; preserve NaNs and explicit matrix topology."""
    if not isinstance(result, dict):
        raise ValueError('扩展没有返回数据对象')
    if result.get('units') != 'mm':
        raise ValueError('扩展 API v1 数据单位必须为 mm')
    kind = result.get('kind')
    if kind == 'height_matrix':
        z = np.asarray(result['z'], dtype=np.float64)
        if z.ndim != 2 or min(z.shape) < 1:
            raise ValueError('扩展高度矩阵尺寸无效')
        for key in ('pitch_x_mm', 'pitch_y_mm'):
            if not np.isfinite(result[key]) or result[key] <= 0:
                raise ValueError('扩展采样间距无效')
        origin = np.asarray(result.get('origin_xy_mm', [0., 0.]), dtype=float)
        if origin.shape != (2,) or not np.all(np.isfinite(origin)):
            raise ValueError('扩展坐标原点无效')
    elif kind == 'point_table':
        arrays = [np.asarray(result[k], dtype=np.float64) for k in ('x', 'y', 'z')]
        if any(a.ndim != 1 for a in arrays) or len({len(a) for a in arrays}) != 1:
            raise ValueError('扩展 XYZ 点表长度不一致')
        z = arrays[2]
    else:
        raise ValueError('扩展返回了不支持的数据种类')
    if not np.any(np.isfinite(z)):
        raise ValueError('扩展数据没有有效高度')
    return result


class FormatRegistry:
    """Discover manifests without executing code; import only selected readers."""
    def __init__(self, root=None, app_version=APP_VERSION):
        self.root = Path(root) if root is not None else extension_root()
        self.app_version = app_version

    def entries(self):
        entries = []
        for path in sorted(self.root.glob('*/extension.json')):
            item = {'id': path.parent.name, 'name': path.parent.name,
                    'path': path.parent, 'status': 'ready'}
            try:
                manifest = json.loads(path.read_text(encoding='utf-8'))
                if not re.fullmatch(r'[a-z][a-z0-9_-]{2,63}', manifest['id']):
                    raise ValueError('无效扩展 ID')
                if manifest['id'] != path.parent.name:
                    raise ValueError('扩展目录与 ID 不一致')
                if manifest['api_version'] != API_VERSION:
                    raise ValueError('扩展 API 版本不兼容')
                _version(manifest['version'])
                if _version(self.app_version) < _version(manifest['minimum_app_version']):
                    raise ValueError('请升级主程序后使用此扩展')
                maximum = manifest.get('maximum_app_version')
                if maximum and _version(self.app_version) > _version(maximum):
                    raise ValueError('请升级扩展以兼容当前主程序')
                suffixes = manifest['suffixes']
                if not suffixes or any(not re.fullmatch(r'\.[a-z0-9]+', s) for s in suffixes):
                    raise ValueError('扩展文件后缀无效')
                module_path = (path.parent / manifest['entrypoint']).resolve()
                if not module_path.is_relative_to(path.parent.resolve()) or module_path.suffix != '.py' or not module_path.is_file():
                    raise ValueError('扩展入口路径无效')
                item.update(manifest)
                item['module_path'] = module_path
                if (path.parent / 'disabled').exists():
                    item['status'] = 'disabled'
            except (ValueError, KeyError, TypeError, OSError) as exc:
                item['status'] = str(exc)
            entries.append(item)
        return entries

    def reader_for(self, suffix):
        matches = [e for e in self.entries() if suffix.lower() in e.get('suffixes', [])]
        ready = [e for e in matches if e['status'] == 'ready']
        if len(ready) > 1:
            raise ValueError('多个扩展声明了相同格式，请在格式扩展管理中只启用一个')
        if ready:
            return ready[0]
        if matches:
            raise ValueError('格式扩展不可用：' + '; '.join(e['status'] for e in matches))
        return None

    def read(self, entry, path, context):
        context.check_cancel()
        spec = importlib.util.spec_from_file_location(
            '_surface_format_' + entry['id'].replace('-', '_'), entry['module_path'])
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = validate_result(module.read(Path(path), context))
        context.check_cancel()
        return result

    def file_filter(self):
        suffixes = sorted({s for e in self.entries() if e['status'] == 'ready'
                           for s in e['suffixes']})
        base = '*.csv *.txt *.tsv *.dat *.asc *.xyz *.xlsx *.xls *.xlsm'
        return f"Data ({base} {' '.join('*' + s for s in suffixes)});;All Files (*)"
