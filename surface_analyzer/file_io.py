"""Small, UI-free file reader used by the public integration API."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .config import MISSING_TEXT_TOKENS


TEXT_SUFFIXES = {".csv", ".txt", ".tsv", ".dat", ".asc", ".xyz", ""}
EXCEL_SUFFIXES = {".xlsx", ".xls", ".xlsm"}


@dataclass(frozen=True)
class LoadedPoints:
    x: np.ndarray
    y: np.ndarray
    z: np.ndarray
    sampled: bool
    strategy: str


def _split(line: str, separator: str) -> list[str]:
    if separator == "whitespace":
        return re.split(r"\s+", line.strip())
    return [part.strip() for part in line.strip().split(separator)]


def _separator(line: str) -> str:
    counts = {",": line.count(","), ";": line.count(";"), "\t": line.count("\t")}
    separator, count = max(counts.items(), key=lambda item: item[1])
    return separator if count else "whitespace"


def _number_or_missing(token: str) -> bool:
    if token.strip() in MISSING_TEXT_TOKENS:
        return True
    try:
        float(token)
        return True
    except ValueError:
        return False


def _find_layout(
    path: Path,
    encoding: str,
    required_indices: tuple[int, int, int],
    scan_limit: int = 50_000,
) -> tuple[int, str, int] | None:
    run_start = -1
    run_sep = ""
    run_columns = 0
    run_length = 0
    with path.open("r", encoding=encoding, errors="strict") as handle:
        for line_number, line in enumerate(handle):
            if line_number >= scan_limit:
                break
            stripped = line.strip().lstrip("\ufeff")
            if not stripped or stripped.startswith("#"):
                run_length = 0
                continue
            separator = _separator(stripped)
            tokens = _split(stripped, separator)
            numeric = (
                len(tokens) > max(required_indices)
                and all(_number_or_missing(tokens[index]) for index in required_indices)
            )
            if numeric and separator == run_sep and len(tokens) == run_columns:
                run_length += 1
            elif numeric:
                run_start = line_number
                run_sep = separator
                run_columns = len(tokens)
                run_length = 1
            else:
                run_length = 0
                run_sep = ""
                run_columns = 0
            if run_length >= 3:
                return run_start, run_sep, run_columns
    return None


def _resolve_column(frame: pd.DataFrame, value: int | str, label: str):
    if isinstance(value, int):
        if value < 0 or value >= frame.shape[1]:
            raise ValueError(f"{label} 列索引 {value} 超出范围")
        return frame.columns[value]
    if value not in frame.columns:
        raise ValueError(f"找不到 {label} 列 {value!r}")
    return value


def _read_excel(path: Path, x_column: int | str, y_column: int | str, z_column: int | str) -> LoadedPoints:
    frame = pd.read_excel(path)
    xc = _resolve_column(frame, x_column, "X")
    yc = _resolve_column(frame, y_column, "Y")
    zc = _resolve_column(frame, z_column, "Z")
    xyz = frame[[xc, yc, zc]].apply(pd.to_numeric, errors="coerce").dropna().to_numpy(dtype=float)
    return LoadedPoints(xyz[:, 0], xyz[:, 1], xyz[:, 2], False, "Excel全量读取")


def _read_text(
    path: Path,
    x_column: int | str,
    y_column: int | str,
    z_column: int | str,
    max_points: int,
) -> LoadedPoints:
    if not all(isinstance(value, int) for value in (x_column, y_column, z_column)):
        raise ValueError("无表头或多行参数头文本的接口读取请使用 0 基列索引指定 X/Y/Z")
    indices = [int(x_column), int(y_column), int(z_column)]
    if min(indices) < 0:
        raise ValueError("X/Y/Z 列索引不能为负数")

    layout = None
    encoding = ""
    error = None
    for candidate in ("utf-8-sig", "gbk", "utf-16", "latin-1"):
        try:
            layout = _find_layout(path, candidate, tuple(indices))
            if layout:
                encoding = candidate
                break
        except (UnicodeError, OSError) as exc:
            error = exc
    if layout is None:
        raise ValueError(f"前 50000 行未找到连续 XYZ 数值区: {error or '未知文本布局'}")

    start_line, separator, column_count = layout
    if min(indices) < 0 or max(indices) >= column_count:
        raise ValueError(f"X/Y/Z 列索引超出数据列数 {column_count}")

    file_size = path.stat().st_size
    max_points = max(3, int(max_points))
    sampled = file_size >= 64 * 1024 * 1024
    if sampled:
        # Estimate a deterministic line stride. The file is streamed once and memory stays bounded.
        average_line_bytes = max(16.0, file_size / max(1, 5_000_000))
        estimated_rows = max_points + 1
        with path.open("rb") as raw:
            sample = raw.read(min(file_size, 2 * 1024 * 1024))
            newline_count = sample.count(b"\n")
            if newline_count:
                average_line_bytes = len(sample) / newline_count
                estimated_rows = int(file_size / average_line_bytes)
        stride = max(1, math.ceil(estimated_rows / max_points))
        rows: list[tuple[float, float, float]] = []
        with path.open("r", encoding=encoding, errors="replace") as handle:
            for line_number, line in enumerate(handle):
                if line_number < start_line:
                    continue
                data_index = line_number - start_line
                if data_index % stride:
                    continue
                tokens = _split(line, separator)
                if len(tokens) != column_count:
                    continue
                try:
                    row = tuple(float(tokens[index]) for index in indices)
                except (ValueError, TypeError):
                    continue
                if all(np.isfinite(row)):
                    rows.append(row)
                if len(rows) >= max_points:
                    break
        xyz = np.asarray(rows, dtype=float)
        strategy = f"文件位置均匀抽样（约每 {stride} 行取 1 行）"
    else:
        sep = r"\s+" if separator == "whitespace" else separator
        frame = pd.read_csv(
            path,
            sep=sep,
            engine="python",
            encoding=encoding,
            header=None,
            skiprows=start_line,
            comment="#",
            on_bad_lines="skip",
            na_values=list(MISSING_TEXT_TOKENS),
        )
        xyz = frame.iloc[:, indices].apply(pd.to_numeric, errors="coerce").dropna().to_numpy(dtype=float)
        strategy = "文本全量读取"

    if xyz.ndim != 2 or xyz.shape[0] < 3:
        raise ValueError("读取后有效 XYZ 点少于 3")
    return LoadedPoints(xyz[:, 0], xyz[:, 1], xyz[:, 2], sampled, strategy)


def load_xyz_points(
    path: str | Path,
    *,
    x_column: int | str = 0,
    y_column: int | str = 1,
    z_column: int | str = 2,
    max_points: int = 100_000,
) -> LoadedPoints:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"文件不存在: {source}")
    suffix = source.suffix.lower()
    if suffix in EXCEL_SUFFIXES:
        return _read_excel(source, x_column, y_column, z_column)
    if suffix in TEXT_SUFFIXES:
        return _read_text(source, x_column, y_column, z_column, max_points)
    raise ValueError(f"接口暂不支持 {suffix or '无后缀'}；支持常规 XYZ 文本和 Excel")
