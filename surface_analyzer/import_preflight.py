"""Bounded import sniffing and fail-fast policy for V4.7.1."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata

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


def _normalize_header_label(value):
    text = unicodedata.normalize("NFKC", str(value).replace("\ufeff", "").strip())
    text = text.replace("µ", "u").replace("μ", "u").lower()
    text = re.sub(r"[\[\](){}_/\\-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _header_axis_kind(value):
    """Return an XYZ semantic without assuming that XYZ are columns 1..3."""
    normalized = _normalize_header_label(value)
    compact = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", normalized)
    words = set(re.findall(r"[a-z]+|[\u4e00-\u9fff]+", normalized))

    def axis_match(axis):
        if compact in (axis, f"{axis}mm", f"{axis}um", f"{axis}nm"):
            return True
        if f"{axis}坐标" in compact or f"坐标{axis}" in compact:
            return True
        return axis in words and bool(words & {"pos", "position", "coordinate", "coord"})

    for axis in ("x", "y", "z"):
        if axis_match(axis):
            return axis
    if any(term in compact for term in ("height", "thickness", "厚度", "高度")):
        return "z"
    return None


def _header_semantics(tokens):
    axes = {"x": [], "y": [], "z": []}
    pixel = {"x": [], "y": [], "z": []}
    for index, token in enumerate(tokens):
        normalized = _normalize_header_label(token)
        compact = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", normalized)
        axis = _header_axis_kind(token)
        if axis:
            axes[axis].append(index)
        if compact in {"pixelx", "xpixel", "xindex", "column", "col", "像素x", "x像素"}:
            pixel["x"].append(index)
        elif compact in {"pixely", "ypixel", "yindex", "row", "像素y", "y像素"}:
            pixel["y"].append(index)
        elif axis == "z":
            pixel["z"].append(index)

    def unique_mapping(candidates):
        mapping = {axis: values[0] for axis, values in candidates.items() if len(values) == 1}
        return mapping if len(mapping) == 3 and len(set(mapping.values())) == 3 else {}

    return unique_mapping(axes), unique_mapping(pixel)


def sniff_text_file(path, cancel_event=None, policy=IMPORT_GUARD_POLICY):
    """Inspect a bounded prefix and return a conservative format diagnosis."""
    text, encoding = _decode_sample(path, cancel_event, policy)
    physical_lines = text.splitlines()[:policy.sniff_max_lines]
    rows = []
    header_candidates = []
    numeric_line_count = 0
    for index, line in enumerate(physical_lines):
        if index % 32 == 0:
            _cancel(cancel_event)
        stripped = line.strip().lstrip("\ufeff")
        if not stripped:
            continue
        if stripped.startswith("#"):
            stripped = stripped[1:].strip()
            if not stripped:
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
        elif any(re.search(r"[A-Za-z\u4e00-\u9fff]", value) for value in tokens):
            # Keep all bounded candidates: vendor metadata often precedes the
            # actual wide XYZ header, so the first text line is not reliable.
            header_candidates.append((tokens, sep, index))

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
    common_sep = Counter(row[2] for row in matching).most_common(1)[0][0]
    compatible_headers = [candidate for candidate in header_candidates
                          if len(candidate[0]) == common_width and candidate[1] == common_sep]
    header = []
    xyz_mapping = {}
    pixel_mapping = {}
    # Prefer the nearest semantic header. A same-width descriptive header is
    # retained for diagnostics but is not by itself proof of XYZ semantics.
    for tokens, _, _ in reversed(compatible_headers):
        axes, pixels = _header_semantics(tokens)
        if pixels or axes:
            header, xyz_mapping, pixel_mapping = tokens, axes, pixels
            break
    if not header and compatible_headers:
        header = compatible_headers[-1][0]
    xyz_header = bool(xyz_mapping)
    pixel_header = bool(pixel_mapping)

    xyz_values = []
    coordinate_indices = ([pixel_mapping[axis] for axis in ("x", "y", "z")]
                          if pixel_mapping else
                          [xyz_mapping[axis] for axis in ("x", "y", "z")]
                          if xyz_mapping else [0, 1, 2])
    for tokens, _, _ in matching:
        if (max(coordinate_indices, default=2) < len(tokens) and
                all(_numeric(tokens[index]) for index in coordinate_indices)):
            xyz_values.append(tuple(float(tokens[index]) for index in coordinate_indices))
    xyz_ratio = len(xyz_values) / max(1, len(matching))
    integer_xy_ratio = 0.0
    if xyz_values:
        arr = np.asarray(xyz_values, dtype=float)
        integer_xy_ratio = float(np.mean(
            (np.abs(arr[:, 0] - np.rint(arr[:, 0])) <= 1e-6) &
            (np.abs(arr[:, 1] - np.rint(arr[:, 1])) <= 1e-6)))

    if pixel_header and xyz_ratio >= policy.minimum_xyz_valid_ratio:
        kind = "Pixel XY point table"
    elif xyz_header and xyz_ratio >= policy.minimum_xyz_valid_ratio:
        kind = "Physical XYZ point table"
    elif common_width == 3 and xyz_ratio >= policy.minimum_xyz_valid_ratio:
        kind = "Physical XYZ point table"
    elif (common_width >= policy.minimum_matrix_width and
          width_stability >= policy.minimum_matrix_width_stability and
          valid_ratio >= policy.minimum_numeric_ratio and numeric_cell_ratio >= 0.90 and
          not xyz_header):
        kind = "Z Matrix"
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
        "xyz_mapping": dict(xyz_mapping),
        "pixel_mapping": dict(pixel_mapping),
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
