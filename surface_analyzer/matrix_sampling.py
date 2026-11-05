"""Bounded, single-pass matrix sampling without assuming uniform missingness."""

import math

import numpy as np
import pandas as pd


def pixel_pitch_summary(info):
    if not (info.get('height_matrix') or info.get('input_layout_mode') in
            ('pixel_xy', 'height_matrix', 'zygo_xyz')):
        return ''
    px, py = info.get('sampling_pitch_x_um'), info.get('sampling_pitch_y_um')
    if px is None or py is None:
        return ''
    source = info.get('sampling_pitch_source') or '用户默认'
    fields = (info.get('matrix_metadata') or {}).get('detected_fields', [])
    if source == '可靠文件metadata':
        sx = '文件元数据' if 'Pitch X' in fields or 'XY校准' in fields else '用户默认'
        sy = '文件元数据' if 'Pitch Y' in fields or 'XY校准' in fields else '用户默认'
        source = f'X:{sx}；Y:{sy}'
    text = f'原始像素 X={float(px):g} / Y={float(py):g} µm/像素（{source}）'
    stride = info.get('stride_n') if info.get('sample_method_key') == 'stride' else None
    if stride:
        text += f' | 抽样 N={int(stride)}，点间距 X={float(px)*int(stride):g} / Y={float(py)*int(stride):g} µm'
    elif info.get('sampled'):
        text += ' | 抽样点间距不固定'
    return text


def numeric_matrix_row(line, separator, ncols, value_start=0,
                       trailing_terminator=False):
    """Strict whole-field C conversion; None selects the compatible parser.

    Unlike fromstring, array conversion rejects malformed numeric suffixes.
    Fixed-delimiter splitting preserves empty fields and cannot shift columns.
    """
    if separator not in (',', '\t', ';', '|', r'\s+'):
        return None
    text = line.rstrip('\r\n')
    tokens = text.split() if separator == r'\s+' else text.split(separator)
    if trailing_terminator and tokens and not tokens[-1].strip():
        tokens.pop()
    if len(tokens) != ncols + value_start:
        return None
    try:
        return np.asarray(tokens[value_start:], dtype=float)
    except (TypeError, ValueError):
        return None


def matrix_point_frame(records, rows, pitch_x_um, pitch_y_um):
    rr, cc = records[:, 0].astype(np.int64), records[:, 1].astype(np.int64)
    return pd.DataFrame({'X': cc * float(pitch_x_um) / 1000,
                         'Y': (rows - 1 - rr) * float(pitch_y_um) / 1000,
                         'Z': records[:, 2], '_matrix_row': rr, '_matrix_col': cc})


class MatrixSampler:
    """Keep bounded representatives while the final row count is still unknown.

    Stride doubles on a nested lattice using *positions*, not valid point count.
    Grid row bins merge on powers of two; merges retain first/min/max records.
    Neither method stops reading when the output limit is reached.
    """

    def __init__(self, ncols, limit, method='file_position', stride=10, grid_side=0):
        self.ncols = int(ncols)
        self.limit = max(3, int(limit))
        self.method = 'stride' if method in ('file_position', 'stride') else 'spatial_grid'
        self.stride = max(1, int(stride))
        self.chunks = []
        safe = max(1, math.isqrt(self.limit // 3))
        self.side = min(int(grid_side), safe) if int(grid_side) > 0 else safe
        self.row_bin = 1
        self.cells = {}
        self.rows = 0
        self.edges = np.unique(np.linspace(0, self.ncols, self.side + 1, dtype=int))

    @staticmethod
    def _merge(old, new):
        if old is None:
            return new
        return (min(old[0], new[0], key=lambda p: (p[0], p[1])),
                min(old[1], new[1], key=lambda p: (p[2], p[0], p[1])),
                min(old[2], new[2], key=lambda p: (-p[2], p[0], p[1])))

    def add(self, row, values):
        self.rows = row + 1
        if self.method == 'stride':
            previous = self.stride
            while (row // self.stride + 1) * math.ceil(self.ncols / self.stride) > self.limit:
                self.stride *= 2
            if previous != self.stride:
                self.chunks = [chunk[chunk[:, 1].astype(np.int64) % self.stride == 0]
                               for chunk in self.chunks
                               if int(chunk[0, 0]) % self.stride == 0]
                self.chunks = [chunk for chunk in self.chunks if len(chunk)]
            if row % self.stride == 0:
                cols = np.arange(0, self.ncols, self.stride)
                cols = cols[np.isfinite(values[cols])]
                if len(cols):
                    self.chunks.append(np.column_stack((np.full(len(cols), row), cols, values[cols])))
            return
        while row // self.row_bin >= self.side:
            self.row_bin *= 2
            merged = {}
            for (iy, ix), records in self.cells.items():
                key = (iy // 2, ix)
                merged[key] = self._merge(merged.get(key), records)
            self.cells = merged
        iy = row // self.row_bin
        for ix, (left, right) in enumerate(zip(self.edges[:-1], self.edges[1:])):
            block = values[left:right]
            finite = np.flatnonzero(np.isfinite(block))
            if not len(finite):
                continue
            heights = block[finite]
            picks = (0, int(np.argmin(heights)), int(np.argmax(heights)))
            records = tuple((row, int(left + finite[pick]), float(heights[pick])) for pick in picks)
            key = (iy, ix)
            self.cells[key] = self._merge(self.cells.get(key), records)

    def frame(self, pitch_x_um, pitch_y_um):
        if self.method == 'stride':
            records = np.concatenate(self.chunks) if self.chunks else np.empty((0, 3))
        else:
            unique = {(p[0], p[1]): p for cell in self.cells.values() for p in cell}
            records = np.asarray([unique[key] for key in sorted(unique)], dtype=float).reshape(-1, 3)
        if not len(records):
            raise ValueError('矩阵规则抽样未命中有效点，请减小倍率 N 或选择空间网格采样。')
        frame = matrix_point_frame(records, self.rows, pitch_x_um, pitch_y_um)
        if self.method == 'stride':
            frame['_topology_row'] = frame['_matrix_row'] // self.stride
            frame['_topology_col'] = frame['_matrix_col'] // self.stride
        return frame
