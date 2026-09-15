"""Bounded import sniffing and fail-fast policy for V4.7.1."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import re

import numpy as np

from .delimited_text import detect_delimiter, tokenize_delimited_line
from .workers import TaskCancelled


@dataclass(frozen=True)
class ImportGuardPolicy:
    sniff_max_bytes: int = 512 * 1024
    sniff_max_lines: int = 400
    minimum_body_rows: int = 3
    minimum_numeric_ratio: float = 0.70
    minimum_xyz_valid_ratio: float = 0.65
    minimum_matrix_width: int = 4
    minimum_matrix_width_stability: float = 0.80
    maximum_consecutive_failures: int = 128
    early_quality_window: int = 256
    early_minimum_valid_ratio: float = 0.20


IMPORT_GUARD_POLICY = ImportGuardPolicy()


class ImportPreflightError(ValueError):
    """A selected import strategy clearly conflicts with the bounded sample."""

    def __init__(self, selected: str, detected: str | None, detail: str):
        recommendation = (f"更像 {detected}" if detected else
                          "未检测到稳定的 XYZ 列结构或矩阵结构")
        suggestion = ("请重新选择导入策略。" if detected else
                      "请检查文件格式和高级设置。")
        super().__init__(
            "当前文件与所选导入策略不匹配。\n\n"
            f"当前选择：{selected}\n"
            f"检测结果：{recommendation}\n"
            f"原因：{detail}\n"
            f"建议：{suggestion}")
        self.selected = selected
        self.detected = detected


def _cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise TaskCancelled()


def _decode_sample(path, cancel_event, policy):
    _cancel(cancel_event)
    with Path(path).open("rb") as stream:
        data = stream.read(policy.sniff_max_bytes)
    _cancel(cancel_event)
    if not data:
        raise ImportPreflightError("当前策略", None, "文件为空")
    for encoding in ("utf-8-sig", "gbk", "utf-16", "latin-1"):
        try:
            return data.decode(encoding), encoding
        except UnicodeError:
            continue
    raise ImportPreflightError("当前策略", None, "无法识别文本编码")


def _numeric(token):
    value = str(token).strip()
    if not value or value.casefold() in {"nan", "na", "null", "nodata", "no data", "inf", "-inf"}:
        return False
    try:
        return bool(np.isfinite(float(value)))
    except ValueError:
        return False


def sniff_text_file(path, cancel_event=None, policy=IMPORT_GUARD_POLICY):
    """Inspect a bounded prefix and return a conservative format diagnosis."""
    text, encoding = _decode_sample(path, cancel_event, policy)
    physical_lines = text.splitlines()[:policy.sniff_max_lines]
    rows = []
    header = []
    numeric_line_count = 0
    for index, line in enumerate(physical_lines):
        if index % 32 == 0:
            _cancel(cancel_event)
        stripped = line.strip().lstrip("\ufeff")
        if not stripped or stripped.startswith("#"):
            continue
        sep = detect_delimiter(stripped)
        tokens = [str(value).strip() for value in tokenize_delimited_line(stripped, sep)]
        while tokens and not tokens[-1]:
            tokens.pop()
        if not tokens:
            continue
        numeric_count = sum(_numeric(value) for value in tokens)
        if numeric_count:
            numeric_line_count += 1
        if numeric_count >= 2 and numeric_count / len(tokens) >= policy.minimum_numeric_ratio:
            rows.append((tokens, numeric_count, sep))
        elif not header and any(re.search(r"[A-Za-z\u4e00-\u9fff]", value) for value in tokens):
            header = tokens

    if len(rows) < policy.minimum_body_rows:
        return {"kind": "unknown", "encoding": encoding,
                "numeric_line_count": numeric_line_count,
                "detail": "样本中有效数值行不足"}
    widths = [len(row[0]) for row in rows]
    common_width, common_count = Counter(widths).most_common(1)[0]
    width_stability = common_count / len(widths)
    matching = [row for row in rows if len(row[0]) == common_width]
    valid_ratio = len(matching) / len(rows)
    numeric_cell_ratio = float(np.mean(
        [numeric_count / max(1, len(tokens)) for tokens, numeric_count, _ in matching]))
    header_text = " ".join(header).casefold()
    pixel_header = ("pixelx" in header_text or "pixel x" in header_text) and (
        "pixely" in header_text or "pixel y" in header_text)
    xyz_header = all(re.search(rf"(^|\W){axis}($|\W)", header_text)
                     for axis in ("x", "y", "z"))

    first3 = []
    for tokens, _, _ in matching:
        if len(tokens) >= 3 and all(_numeric(value) for value in tokens[:3]):
            first3.append(tuple(float(value) for value in tokens[:3]))
    xyz_ratio = len(first3) / max(1, len(matching))
    integer_xy_ratio = 0.0
    if first3:
        arr = np.asarray(first3, dtype=float)
        integer_xy_ratio = float(np.mean(
            (np.abs(arr[:, 0] - np.rint(arr[:, 0])) <= 1e-6) &
            (np.abs(arr[:, 1] - np.rint(arr[:, 1])) <= 1e-6)))

    if pixel_header:
        kind = "Pixel XY point table"
    elif common_width == 3 and xyz_ratio >= policy.minimum_xyz_valid_ratio:
        kind = "Physical XYZ point table"
    elif (common_width >= policy.minimum_matrix_width and
          width_stability >= policy.minimum_matrix_width_stability and
          valid_ratio >= policy.minimum_numeric_ratio and numeric_cell_ratio >= 0.90 and
          not xyz_header):
        kind = "Z Matrix"
    elif xyz_header and xyz_ratio >= policy.minimum_xyz_valid_ratio:
        kind = "Physical XYZ point table"
    else:
        kind = "unknown"
    return {
        "kind": kind, "encoding": encoding, "rows_checked": len(rows),
        "numeric_line_count": numeric_line_count,
        "common_width": common_width, "width_stability": width_stability,
        "xyz_valid_ratio": xyz_ratio, "integer_xy_ratio": integer_xy_ratio,
        "numeric_cell_ratio": numeric_cell_ratio,
        "header_width": len(header),
        "xyz_header": xyz_header,
        "pixel_header": pixel_header,
        "detail": f"样本 {len(rows)} 行，主列数 {common_width}，列宽稳定率 {width_stability:.0%}",
    }


def validate_selected_layout(path, layout_mode, cancel_event=None,
                             policy=IMPORT_GUARD_POLICY):
    result = sniff_text_file(path, cancel_event, policy)
    selected = {
        "point_table": "Physical XYZ",
        "pixel_xy": "Pixel XY",
        "height_matrix": "Z Matrix",
        "zygo_xyz": "Zygo XYZ",
    }.get(layout_mode, str(layout_mode))
    kind = result["kind"]
    if (layout_mode == 'point_table' and kind == 'Z Matrix' and
            result.get('header_width') == result.get('common_width')):
        kind = 'Physical XYZ point table'
        result['kind'] = kind
    if (layout_mode == 'height_matrix' and kind == 'unknown' and
            int(result.get('common_width', 0)) >= policy.minimum_matrix_width and
            float(result.get('width_stability', 0.0)) >= 0.70):
        kind = 'Z Matrix'
        result['kind'] = kind
    if (layout_mode == 'height_matrix' and int(result.get('rows_checked', 0)) >= 8 and
            float(result.get('width_stability', 1.0)) < 0.50):
        raise ImportPreflightError(
            selected, None, "矩阵列数持续严重不一致；" + result["detail"])
    incompatible = {
        "point_table": {"Z Matrix", "Pixel XY point table"},
        "pixel_xy": {"Z Matrix", "Physical XYZ point table"},
        "height_matrix": {"Physical XYZ point table", "Pixel XY point table"},
    }
    clearly_incompatible = kind in incompatible.get(layout_mode, set())
    if layout_mode in ('height_matrix', 'pixel_xy') and kind == 'Physical XYZ point table':
        clearly_incompatible = bool(result.get('xyz_header'))
    if clearly_incompatible:
        raise ImportPreflightError(selected, kind, result["detail"])
    if kind == "unknown" and int(result.get('numeric_line_count', 0)) == 0:
        raise ImportPreflightError(selected, None, result["detail"])
    return result
