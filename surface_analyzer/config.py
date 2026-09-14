"""Application defaults shared by the GUI and integration layer."""

from __future__ import annotations

from dataclasses import dataclass
import os
import json
import logging

from .version import APP_VERSION


ACCENT = "#2f6db0"
DISPLAY_POINT_LIMIT = 30_000
LARGE_TEXT_FILE_BYTES = 64 * 1024 * 1024
LARGE_TEXT_IMPORT_LIMIT = 100_000
MISSING_TEXT_TOKENS = {
    "***", "--", "NA", "N/A", "NaN", "nan", "NAN", "null", "NULL",
    "NoData", "Nodata", "NO DATA", "No Data",
}


def _physical_core_count() -> int:
    """Return a conservative cross-platform core estimate without dependencies."""
    logical = os.cpu_count() or 1
    # Most supported operator PCs expose SMT. A conservative half-logical
    # estimate avoids oversubscribing OpenBLAS, Qt and worker threads together.
    return max(1, logical // 2) if logical >= 4 else logical


@dataclass(frozen=True)
class PerformancePolicy:
    perf_debug: bool = False
    thread_budget: int = max(1, _physical_core_count() - 1)
    parser_fast_path_enabled: bool = True
    matrix_fast_component_enabled: bool = True
    lod_cache_enabled: bool = True


PERFORMANCE_POLICY = PerformancePolicy(
    perf_debug=os.environ.get('SURFACE_PERF_DEBUG', '').strip().casefold()
               in {'1', 'true', 'yes', 'on'},
)


def perf_event(stage: str, seconds: float, **details) -> None:
    """Emit structured timing only when SURFACE_PERF_DEBUG is enabled."""
    if not PERFORMANCE_POLICY.perf_debug:
        return
    payload = {'stage': str(stage), 'seconds': round(float(seconds), 6), **details}
    logging.getLogger('surface_analyzer.performance').info(
        'PERF %s', json.dumps(payload, ensure_ascii=False, sort_keys=True))

BIGFILE_MODE_PRESETS = {
    "fast": {
        "label": "快速",
        "auto_sample": True,
        "threshold_mb": 64,
        "import_limit": 60_000,
        "matrix_analysis_threshold": 150_000,
        "display_limit": 20_000,
        "sample_method": "file_position",
        "grid_count": 0,
        "description": "优先流畅：较早触发抽样，适合先快速判断面型趋势和导入格式。",
    },
    "standard": {
        "label": "标准",
        "auto_sample": True,
        "threshold_mb": 64,
        "import_limit": 100_000,
        "matrix_analysis_threshold": 400_000,
        "display_limit": 30_000,
        "sample_method": "file_position",
        "grid_count": 0,
        "description": "默认推荐：按文件位置均匀抽样，优先保证普通电脑流畅导入和交互。",
    },
    "precise": {
        "label": "精确",
        "auto_sample": True,
        "threshold_mb": 256,
        "import_limit": 300_000,
        "matrix_analysis_threshold": 1_000_000,
        "display_limit": 60_000,
        "sample_method": "spatial_grid",
        "grid_count": 0,
        "description": "保留更多点参与拟合，导入和绘图会更慢，适合最终复核。",
    },
}
