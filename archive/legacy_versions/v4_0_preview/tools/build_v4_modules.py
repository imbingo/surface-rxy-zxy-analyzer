"""Mechanically split the V3.9.3 monolith into the V4.0 package.

This script preserves method bodies byte-for-byte (after UTF-8 decoding) and only
changes class ownership. It is kept in the repository so the migration can be
audited or regenerated before V4.0 is committed.
"""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = next(ROOT.glob("*Rxy*ZXY*.py"))
PACKAGE = ROOT / "surface_analyzer"
MIXINS = PACKAGE / "mixins"


COMMON_IMPORTS = """\
import sys
import os
import re
import mmap
import json
import tempfile
from collections import deque
from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch, Rectangle as MplRectangle, Circle as MplCircle
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.widgets import RectangleSelector
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QFileDialog, QLabel, QSplitter, QGroupBox, QGridLayout, QMessageBox,
    QScrollArea, QComboBox, QTabWidget, QDoubleSpinBox, QSpinBox, QCheckBox,
    QDialog, QDialogButtonBox, QFrame, QSizePolicy, QGraphicsDropShadowEffect,
    QStackedWidget, QSizeGrip,
)
from PyQt6.QtCore import Qt, QPoint, QPointF, QEvent
from PyQt6.QtGui import QColor, QPixmap, QPainter, QPen
from scipy.spatial import cKDTree
"""


GROUPS = {
    "parallelism": {
        "_current_parallel_record", "set_parallel_surface", "swap_parallel_surfaces",
        "clear_parallel_surfaces", "_slot_status_text", "_update_parallel_ui",
        "_compute_parallel_result", "calculate_parallelism", "_fmt_metric",
        "_update_parallel_result_ui", "_parallel_result_text", "copy_parallel_result",
        "export_parallel_csv", "_short_report_text", "_parallel_report_default_name",
        "_draw_parallel_report_surface", "_render_parallel_report_figure",
        "export_parallel_report", "export_parallel_preview",
    },
    "recipe": {
        "_current_recipe_dict", "_safe_set_combo_text", "export_recipe",
        "import_recipe", "apply_recipe",
    },
    "analysis": {
        "mad_filter", "local_median_filter", "sigma_clip_filter", "filter_keep_mask",
        "fit_plane", "compute_plane_metrics", "_apply_transform_pipeline",
    },
    "gap": {
        "set_memory_slot", "clear_memory_slot", "clear_all_memory_slots",
        "_update_gap_diagnostic", "calculate_gap",
    },
    "data_io": {
        "_bigfile_mode_label", "_bigfile_mode_description", "_sample_method_label",
        "_grid_count_label", "_matching_bigfile_mode", "_large_text_threshold_bytes",
        "_large_text_import_limit", "_display_limit", "_reset_import_info",
        "_update_import_status_label", "_on_display_limit_changed",
        "show_bigfile_settings_dialog", "_detect_sep_from_line", "_split_text_line",
        "_is_missing_token", "_is_float_token", "_is_float_or_missing_token",
        "_looks_like_numeric_text_row", "_token_to_float", "_detect_text_layout",
        "_normalize_unit_label", "_height_matrix_header_meta",
        "_looks_like_height_matrix_layout", "_height_matrix_dataframe",
        "_sample_large_height_matrix_by_stride", "_sample_large_height_matrix",
        "_read_height_matrix_table", "_infer_xyz_column_indices", "_max_safe_grid_side",
        "_auto_spatial_grid_side", "_sample_large_text", "_sample_large_text_by_stride",
        "_sample_large_text_by_position", "_sample_large_text_by_spatial_grid",
        "_read_table", "load_file", "apply_mapping",
    },
    "roi": {
        "_roi_is_active", "_roi_shape_label", "_clean_roi_shapes",
        "_estimate_xy_neighbor_radius", "_matrix_rc_for_current_data",
        "_recommend_smart_tolerance_mm", "_update_smart_tolerance_recommendation",
        "_smart_face_keep_mask_matrix", "_smart_face_keep_mask_auto_xy",
        "_smart_face_keep_mask_plane_residual", "_smart_face_keep_mask_for_arrays",
        "_roi_keep_mask_for_arrays", "_sync_roi_input_state", "_on_roi_changed",
        "_refresh_roi_ui", "_add_roi_shape", "add_roi_from_inputs", "start_mouse_roi",
        "set_delete_selection_mode", "_selected_roi_index", "toggle_selected_roi",
        "delete_selected_roi", "clear_rois", "_roi_report_info", "_draw_roi_overlays",
        "on_canvas_click", "add_smart_face_roi_from_seed", "on_select",
        "setup_selectors", "apply_manual_deletion",
    },
    "reporting": {
        "export_report_image", "save_file", "_capture_batch_params", "batch_process",
        "_run_batch", "_render_report_figure",
    },
}


MIXIN_CLASS_NAMES = {
    "parallelism": "ParallelismMixin",
    "recipe": "RecipeMixin",
    "analysis": "AnalysisMixin",
    "gap": "GapAnalysisMixin",
    "data_io": "DataIOMixin",
    "roi": "ROIMixin",
    "reporting": "ReportingMixin",
}


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8", newline="\n")


def method_source(lines: list[str], node: ast.FunctionDef) -> str:
    start = min([d.lineno for d in node.decorator_list] or [node.lineno]) - 1
    return "\n".join(lines[start:node.end_lineno])


def main() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source)
    classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
    window = classes["SurfaceAnalyzerPro"]
    methods = {
        n.name: n for n in window.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assigned: dict[str, str] = {}
    for group, names in GROUPS.items():
        unknown = names - methods.keys()
        if unknown:
            raise RuntimeError(f"Unknown methods in {group}: {sorted(unknown)}")
        for name in names:
            if name in assigned:
                raise RuntimeError(f"Method {name} assigned twice")
            assigned[name] = group

    widget_names = [
        "NoWheelSpinBox", "NoWheelDoubleSpinBox", "NoWheelComboBox",
        "MultiViewCanvas", "ParallelismCanvas", "GapMatchCanvas",
    ]
    widget_parts = [COMMON_IMPORTS]
    for name in widget_names:
        node = classes[name]
        widget_parts.append("\n".join(lines[node.lineno - 1:node.end_lineno]))
    write(PACKAGE / "widgets.py", "\n\n\n".join(widget_parts))

    for group, names in GROUPS.items():
        class_name = MIXIN_CLASS_NAMES[group]
        ordered = [n for n in window.body if isinstance(n, ast.FunctionDef) and n.name in names]
        header = COMMON_IMPORTS
        if group == "analysis":
            header = "import numpy as np\nfrom scipy.spatial import cKDTree\n"
        elif group == "data_io":
            header += "\nfrom ..widgets import NoWheelSpinBox, NoWheelDoubleSpinBox, NoWheelComboBox\n"
        body = "\n\n".join(method_source(lines, n) for n in ordered)
        class_prelude = ""
        if group == "data_io":
            class_prelude = (
                "    TEXT_SUFFIXES = ('.csv', '.txt', '.tsv', '.dat', '.asc', '.xyz')\n"
                "    EXCEL_SUFFIXES = ('.xlsx', '.xls', '.xlsm')\n\n"
            )
            body = body.replace(
                "        if not path: return\n        try:\n",
                "        if not path:\n"
                "            return False\n"
                "        return self.load_path(path)\n\n"
                "    def load_path(self, path):\n"
                "        \"\"\"Load a known path; used by both the file dialog and platform integration.\"\"\"\n"
                "        path = str(Path(path).expanduser().resolve())\n"
                "        try:\n",
                1,
            )
            body = body.replace(
                "        except Exception as e:\n"
                "            QMessageBox.critical(self, \"导入失败\", str(e))\n\n"
                "    def apply_mapping",
                "            return True\n"
                "        except Exception as e:\n"
                "            QMessageBox.critical(self, \"导入失败\", str(e))\n"
                "            return False\n\n"
                "    def apply_mapping",
                1,
            )
        write(
            MIXINS / f"{group}.py",
            f'"""{class_name} extracted from the V3.9.3 application."""\n\n'
            f"{header}\n\n\nclass {class_name}:\n{class_prelude}{body}\n",
        )

    main_methods = [
        n for n in window.body
        if isinstance(n, ast.FunctionDef) and n.name not in assigned
    ]
    app_body = "\n\n".join(method_source(lines, n) for n in main_methods)
    app_imports = COMMON_IMPORTS + """

from .config import (
    ACCENT, APP_VERSION, BIGFILE_MODE_PRESETS, DISPLAY_POINT_LIMIT,
    LARGE_TEXT_FILE_BYTES, LARGE_TEXT_IMPORT_LIMIT, MISSING_TEXT_TOKENS,
)
from .widgets import (
    NoWheelSpinBox, NoWheelDoubleSpinBox, NoWheelComboBox,
    MultiViewCanvas, ParallelismCanvas, GapMatchCanvas,
)
from .mixins.analysis import AnalysisMixin
from .mixins.data_io import DataIOMixin
from .mixins.gap import GapAnalysisMixin
from .mixins.parallelism import ParallelismMixin
from .mixins.recipe import RecipeMixin
from .mixins.roi import ROIMixin
from .mixins.reporting import ReportingMixin
"""
    bases = (
        "AnalysisMixin, DataIOMixin, GapAnalysisMixin, ParallelismMixin, "
        "RecipeMixin, ROIMixin, ReportingMixin, QMainWindow"
    )
    class_attrs = """\
    APP_VERSION = APP_VERSION
    DISPLAY_POINT_LIMIT = DISPLAY_POINT_LIMIT
    LARGE_TEXT_FILE_BYTES = LARGE_TEXT_FILE_BYTES
    LARGE_TEXT_IMPORT_LIMIT = LARGE_TEXT_IMPORT_LIMIT
    BIGFILE_MODE_PRESETS = BIGFILE_MODE_PRESETS
    MISSING_TEXT_TOKENS = MISSING_TEXT_TOKENS
    ACCENT = ACCENT

"""
    write(
        PACKAGE / "app.py",
        '"""Qt application shell for Surface Analyzer V4.0."""\n\n'
        f"{app_imports}\n\n\nclass SurfaceAnalyzerPro({bases}):\n{class_attrs}{app_body}\n",
    )

    init_lines = [
        '"""Mixins used by the V4.0 application shell."""',
        "",
    ]
    for group, class_name in MIXIN_CLASS_NAMES.items():
        init_lines.append(f"from .{group} import {class_name}")
    init_lines.append("")
    init_lines.append("__all__ = [")
    init_lines.extend(f'    "{name}",' for name in MIXIN_CLASS_NAMES.values())
    init_lines.append("]")
    write(MIXINS / "__init__.py", "\n".join(init_lines))


if __name__ == "__main__":
    main()
