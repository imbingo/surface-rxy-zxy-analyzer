"""Application defaults shared by the GUI and integration layer."""

from .version import APP_VERSION


ACCENT = "#2f6db0"
DISPLAY_POINT_LIMIT = 30_000
LARGE_TEXT_FILE_BYTES = 64 * 1024 * 1024
LARGE_TEXT_IMPORT_LIMIT = 100_000
MISSING_TEXT_TOKENS = {"***", "--", "NA", "N/A", "NaN", "nan", "null", "NULL"}

BIGFILE_MODE_PRESETS = {
    "fast": {
        "label": "快速",
        "auto_sample": True,
        "threshold_mb": 64,
        "import_limit": 60_000,
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
        "display_limit": 60_000,
        "sample_method": "spatial_grid",
        "grid_count": 0,
        "description": "保留更多点参与拟合，导入和绘图会更慢，适合最终复核。",
    },
}
