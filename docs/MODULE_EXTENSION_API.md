# Module Extension API v1

Surface Analyzer V5.0.6 supports optional application modules. Extensions are
separately installed executable Python packages and are not included in the
main application installer.

Install each extension at
`%LOCALAPPDATA%/SurfaceRxyZxyAnalyzer/module_extensions/<id>/`. The main app
does not remove this directory during upgrades. Only install extension packages
from sources you trust.

## Manifest and package loading

`extension.json` requires `id`, `name`, semantic `version`, `api_version` (1),
`minimum_app_version`, and `entrypoint` (`__init__.py`). An optional
`maximum_app_version` can cap compatibility. The directory name must equal the
manifest ID, and the entry point must remain inside that directory. A `disabled`
marker file disables the extension.

The main app imports the entry point as a package, so relative imports are
supported. Export `create_module(host)` returning an object with:

- `key`, `label`, `caption`, and `export_label`
- `icon(color) -> QIcon`
- `build_sidebar(parent) -> QWidget`
- `build_panel() -> QWidget`
- `export_actions() -> [(label, callback, ready)]`
- `status_text() -> str`
- `on_idle()`

Discovery, import, factory, and UI construction errors are isolated to that
extension and shown in **扩展管理…**. Other modules remain available.

## Stable `ModuleHost` facade

The extension may use only the host facade for application interaction; do not
access private main-window attributes. API v1 provides:

- `api_version`, `app_version`
- `current_layer_snapshot()` — active single-layer X/Y/Z values in mm, source
  name/path, point count, import metadata, sample status, and metric quality.
  It raises `ValueError` when analysis is unavailable.
- `confirm_estimated_metrics(action)` — apply the app's estimated-metric write
  confirmation before snapshotting.
- `run_task(name, fn, on_success, on_error=None, on_cancel=None)` — `fn` receives
  `(progress, cancel_event)` and runs on the app background worker.
- `register_task_controls(widgets)` — disable controls during background work.
  `on_idle()` is called when the task finishes.
- `settings_value(key, default)` and `set_setting(key, value)` — values are
  namespaced under `extensions/<id>/`.
- `make_canvas(figure)` — create a theme-aware `ThemedFigureCanvas`.
- `make_judgment_card(module_key, metric_labels, keys)`,
  `refresh_judgment(module_key, values)`, and
  `current_judgment(module_key, values)`.
- `judgment_lines(module_key, values)`, `judgment_summary(module_key, values)`,
  and `verdict_text` for consistent report judgment output.
- `export_origin_offset(x, y) -> (dx, dy, origin_line)`, which honors the
  single-layer active-points export-origin setting.
- `save_path(title, default_name, file_filter)`, `status(message)`, and
  `refresh_module_chrome()`.

`surface_analyzer.workers.TaskCancelled` is the stable cancellation exception.
Use `progress(percent, text)` and check cancellation cooperatively.

## Guaranteed modules

The frozen main app includes Python, NumPy, pandas, SciPy (including
`scipy.optimize.curve_fit` and `least_squares`, and `scipy.spatial.cKDTree`),
Matplotlib with QtAgg, PyQt6, and the public `surface_analyzer.workers` module.
Extensions must not assume Numba or any other optional package is installed.
Keep report figures white; screen figures should use the theme-aware canvas.

API v1 coordinates and analyzer snapshots use millimetres. API changes require
an API version increment and must not silently change existing meanings.

Validate a package with
`python -m surface_analyzer --check-module-extension <extension-directory>`.
Build the VIG image extension installer with
`powershell -File scripts/build_module_extension.ps1`.
