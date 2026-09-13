"""Local Recipe library, atomic JSON writes and searchable metadata."""
import json
import math
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def validate_recipe(data):
    if not isinstance(data, dict):
        raise ValueError('Recipe 顶层必须是 JSON 对象')
    if data.get('recipe_type') not in (None, 'SurfaceRxyZxyAnalyzerRecipe'):
        raise ValueError('该文件不是面型分析工具 Recipe')
    if not any(k in data for k in ('column_mapping', 'filter', 'input', 'roi')):
        raise ValueError('未找到面型分析参数')
    try:
        schema = int(data.get('schema_version', 1) or 1)
    except (ValueError, TypeError, OverflowError):
        raise ValueError('Recipe schema_version 必须是有效整数') from None
    if not 1 <= schema <= 8:
        raise ValueError(f'不支持 Recipe schema {schema}；当前支持 1–8')
    for key in ('column_mapping', 'units', 'filter', 'input', 'display', 'roi',
                'manual_deletion', 'large_file', 'gap', 'library_metadata'):
        if key in data and not isinstance(data[key], dict):
            raise ValueError(f'Recipe {key} 必须是对象')
    def finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError('Recipe 包含无效数值')
        if isinstance(value, dict):
            for item in value.values():
                finite(item)
        elif isinstance(value, list):
            for item in value:
                finite(item)
    finite(data)
    for section, fields in {
        'input': {'x_col_index': int, 'y_col_index': int, 'z_col_index': int,
                  'pixel_origin_x': float, 'pixel_origin_y': float},
        'filter': {'mode_index': int, 'neighbor_k': int, 'threshold_um': float,
                   'sigma_k': float, 'sigma_iters': int},
        'gap': {'tolerance_mm': float},
    }.items():
        for key, convert in fields.items():
            if key in data.get(section, {}):
                try:
                    value = convert(data[section][key])
                    if not math.isfinite(value):
                        raise ValueError()
                except (TypeError, ValueError, OverflowError):
                    raise ValueError(f'Recipe {section}.{key} 数值无效') from None
    if 'transform_pipeline' in data and (not isinstance(data['transform_pipeline'], list)
            or not all(isinstance(v, str) for v in data['transform_pipeline'])):
        raise ValueError('姿态变换必须是字符串列表')
    shapes = data.get('roi', {}).get('shapes', [])
    if not isinstance(shapes, list) or not all(isinstance(s, dict) for s in shapes):
        raise ValueError('ROI 必须是区域对象列表')
    return data


def read_recipe(path):
    return validate_recipe(json.loads(Path(path).read_text(encoding='utf-8-sig')))


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name+'.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class RecipeLibrary:
    def __init__(self, root=None):
        self.root = Path(root) if root else Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'SurfaceRxyZxyAnalyzer' / 'recipes'

    def state(self):
        path = self.root / '.library_state.json'
        if not path.exists():
            return {'favorites': [], 'recent': {}}
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or not isinstance(data.get('favorites', []), list) or not isinstance(data.get('recent', {}), dict):
            raise ValueError('Recipe 库索引损坏，请备份并检查 .library_state.json')
        return data

    def mark_used(self, path):
        state = self.state()
        state.setdefault('recent', {})[Path(path).name] = datetime.now(timezone.utc).isoformat()
        atomic_json(self.root / '.library_state.json', state)

    def favorite(self, path):
        state = self.state()
        favorites = state.setdefault('favorites', [])
        name = Path(path).name
        if name in favorites:
            favorites.remove(name)
        else:
            favorites.append(name)
        atomic_json(self.root / '.library_state.json', state)

    def save(self, data, name):
        validate_recipe(data)
        name = name.strip()
        if not name:
            raise ValueError('请输入 Recipe 名称')
        stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip('. ')[:100] or 'Recipe'
        # Prefix prevents Windows device-name collisions; never overwrite a library entry.
        path = self.root / f'recipe_{stem}_{uuid4().hex[:12]}.json'
        payload = dict(data)
        payload['library_metadata'] = {'name': name, 'saved_at': datetime.now().isoformat(timespec='seconds')}
        atomic_json(path, payload)
        return path

    def import_file(self, source):
        data = read_recipe(source)
        for existing in self.root.glob('*.json'):
            if existing.name.startswith('.'):
                continue
            try:
                saved = read_recipe(existing)
                if {k:v for k,v in data.items() if k != 'library_metadata'} == {k:v for k,v in saved.items() if k != 'library_metadata'}:
                    return existing
            except (ValueError, OSError):
                continue
        name = data.get('library_metadata', {}).get('name') or Path(source).stem
        return self.save(data, str(name))

    def entries(self):
        state = self.state()
        entries, errors = [], []
        for path in self.root.glob('*.json'):
            if path.name.startswith('.'):
                continue
            try:
                data = read_recipe(path)
                entries.append(dict(path=path, name=data.get('library_metadata', {}).get('name') or path.stem,
                                    version=data.get('app_version', ''),
                                    modified=datetime.fromtimestamp(path.stat().st_mtime).strftime('%Y-%m-%d %H:%M'),
                                    favorite=path.name in state.get('favorites', []),
                                    recent=state.get('recent', {}).get(path.name, '')))
            except (OSError, ValueError) as exc:
                errors.append(f'{path.name}: {exc}')
        entries.sort(key=lambda e: (e['favorite'], e['recent'], e['modified']), reverse=True)
        return entries, errors
