"""Discovery and loading for optional application-module extensions."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from pathlib import Path

from .format_extensions import _version
from .version import APP_VERSION

API_VERSION = 1


def validate_module_object(module, entry):
    required = (
        'key', 'label', 'caption', 'export_label', 'icon',
        'build_sidebar', 'build_panel', 'export_actions',
        'status_text', 'on_idle',
    )
    if any(not hasattr(module, name) for name in required):
        raise ValueError("扩展模块缺少必需接口")
    if str(module.key) != entry['id']:
        raise ValueError("模块 key 必须与 extension.json 的 id 一致")
    if not all(callable(getattr(module, name)) for name in (
            'icon', 'build_sidebar', 'build_panel', 'export_actions',
            'status_text', 'on_idle')):
        raise ValueError("扩展模块接口必须可调用")
    return module


def module_extension_root():
    base = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local' / 'share'))
    return base / 'SurfaceRxyZxyAnalyzer' / 'module_extensions'


class ModuleExtensionRegistry:
    def __init__(self, root=None, app_version=APP_VERSION):
        self.root = Path(root) if root is not None else module_extension_root()
        self.app_version = app_version
        self._entries = None

    def entries(self):
        if self._entries is None:
            self.refresh()
        return list(self._entries)

    def refresh(self):
        entries = []
        try:
            manifests = sorted(self.root.glob('*/extension.json'))
        except OSError:
            manifests = []
        for manifest_path in manifests:
            path = manifest_path.parent
            item = {
                'id': path.name,
                'name': path.name,
                'path': path,
                'manifest_path': manifest_path,
                'status': 'ready',
                'type': 'module',
            }
            try:
                manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                extension_id = manifest['id']
                if not isinstance(extension_id, str) or not re.fullmatch(
                        r'[a-z][a-z0-9_-]{2,63}', extension_id):
                    raise ValueError('无效扩展 ID')
                if extension_id != path.name:
                    raise ValueError('扩展目录与 ID 不一致')
                if not isinstance(manifest['name'], str) or not manifest['name'].strip():
                    raise ValueError('无效扩展名称')
                if type(manifest['api_version']) is not int or manifest['api_version'] != API_VERSION:
                    raise ValueError('扩展 API 版本不兼容')
                _version(manifest['version'])
                if _version(self.app_version) < _version(manifest['minimum_app_version']):
                    raise ValueError('请升级主程序后使用此扩展')
                maximum = manifest.get('maximum_app_version')
                if maximum is not None and _version(self.app_version) > _version(maximum):
                    raise ValueError('请升级扩展以兼容当前主程序')
                entrypoint = manifest['entrypoint']
                if entrypoint != '__init__.py':
                    raise ValueError('模块扩展入口必须为包目录中的 __init__.py')
                module_path = (path / entrypoint).resolve()
                if (not module_path.is_relative_to(path.resolve())
                        or module_path.suffix != '.py'
                        or not module_path.is_file()):
                    raise ValueError('扩展入口路径无效')
                item.update(manifest)
                item['path'] = path
                item['manifest_path'] = manifest_path
                item['module_path'] = module_path
                if (path / 'disabled').exists():
                    item['status'] = 'disabled'
            except Exception as exc:
                item['status'] = str(exc) or type(exc).__name__
            entries.append(item)
        self._entries = entries
        return list(entries)

    def mark_error(self, entry, message):
        status = str(message) or '扩展加载失败'
        entry['status'] = status
        for current in self.entries():
            if current['path'] == entry['path']:
                current['status'] = status
                break

    @staticmethod
    def _remove_package_modules(module_name):
        for name in tuple(sys.modules):
            if name == module_name or name.startswith(module_name + '.'):
                sys.modules.pop(name, None)

    def unload(self, entry):
        self._remove_package_modules(f"_surface_module_{entry['id']}")

    def load(self, entry, host):
        if entry.get('status') != 'ready':
            return None
        module_name = f"_surface_module_{entry['id']}"
        self._remove_package_modules(module_name)
        try:
            spec = importlib.util.spec_from_file_location(
                module_name,
                entry['module_path'],
                submodule_search_locations=[str(entry['path'])],
            )
            if spec is None or spec.loader is None:
                raise ImportError('无法创建扩展包加载器')
            package = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = package
            spec.loader.exec_module(package)
            factory = getattr(package, 'create_module', None)
            if not callable(factory):
                raise ValueError('扩展入口必须提供 create_module(host)')
            extension = factory(host)
            if extension is None:
                raise ValueError('create_module(host) 未返回模块对象')
            entry['_package'] = package
            entry['_module'] = extension
            return extension
        except Exception as exc:
            self._remove_package_modules(module_name)
            self.mark_error(entry, f"加载失败：{exc}")
            return None


class ModuleHost:
    """Stable, namespaced facade provided to an optional module extension."""

    __slots__ = ('__window', '__extension_id', '__registered_controls')

    def __init__(self, window, extension_id):
        self.__window = window
        self.__extension_id = str(extension_id)
        self.__registered_controls = []

    @property
    def api_version(self):
        return API_VERSION

    @property
    def app_version(self):
        return APP_VERSION

    def current_layer_snapshot(self):
        record = self.__window._current_gap_record()
        record.setdefault(
            'source_path', str((record.get('import_info') or {}).get('source_path') or ''))
        return record

    def save_composite_recipe(self, module):
        return self.__window.save_composite_recipe(module)

    def choose_composite_recipe(self, module):
        return self.__window.choose_composite_recipe(module)

    def load_result_copy(self, x, y, z_mm, *, name, quality=None, metadata=None):
        """Load a detached mm dataset into the existing single-layer workflow."""
        return self.__window.load_memory_result(x, y, z_mm, name=name,
                                                quality=quality, metadata=metadata)

    def confirm_estimated_metrics(self, action):
        return self.__window._confirm_estimated_metrics(str(action))

    def run_task(self, name, fn, on_success, on_error=None, on_cancel=None):
        return self.__window._run_background_task(
            name, fn, on_success, on_error=on_error, on_cancel=on_cancel)

    def register_task_controls(self, widgets):
        controls = self.__window._task_controls
        for widget in widgets:
            if widget not in controls:
                controls.append(widget)
                self.__registered_controls.append(widget)

    def unregister_task_controls(self):
        controls = self.__window._task_controls
        for widget in self.__registered_controls:
            if widget in controls:
                controls.remove(widget)
        self.__registered_controls.clear()

    def settings_value(self, key, default=None):
        return self.__window.settings.value(
            f"extensions/{self.__extension_id}/{key}", default)

    def set_setting(self, key, value):
        self.__window.settings.setValue(
            f"extensions/{self.__extension_id}/{key}", value)

    def make_canvas(self, figure):
        from .theme import ThemedFigureCanvas

        return ThemedFigureCanvas(figure)

    def make_judgment_card(self, module_key, metric_labels, keys):
        return self.__window._make_extension_judgment_card(
            str(module_key), metric_labels, tuple(keys))

    def refresh_judgment(self, module_key, values):
        return self.__window._refresh_extension_judgment(str(module_key), values)

    def current_judgment(self, module_key, values):
        return self.__window.current_judgment(str(module_key), values)

    def judgment_lines(self, module_key, values):
        from .judgment import judgment_lines

        return judgment_lines(self.current_judgment(module_key, values))

    def judgment_summary(self, module_key, values):
        from .judgment import judgment_summary

        return judgment_summary(self.current_judgment(module_key, values))

    @property
    def verdict_text(self):
        from .judgment import VERDICT_TEXT

        return dict(VERDICT_TEXT)

    def export_origin_offset(self, x, y):
        window = self.__window
        if not window._export_origin_zero_active_points_enabled():
            return 0.0, 0.0, "与界面坐标一致"
        dx, dy = window._export_origin_offset(x, y)
        line = (
            f"有效点左下角归零（导出 X = 界面 X − {dx:.6f} mm，"
            f"导出 Y = 界面 Y − {dy:.6f} mm；拟合平面系数和 ROI 形状仍为界面坐标）")
        return dx, dy, line

    def save_path(self, title, default_name, file_filter):
        from PyQt6.QtWidgets import QFileDialog

        from .dialog_paths import dialog_initial_path, remember_dialog_path

        path, _ = QFileDialog.getSaveFileName(
            self.__window, title, dialog_initial_path('export', default_name), file_filter)
        if path:
            remember_dialog_path('export', path)
        return path or None

    def status(self, message):
        self.__window._show_status(str(message), 6000)

    def refresh_module_chrome(self):
        window = self.__window
        window._sync_status_widgets()
        window._refresh_module_export_menu()
