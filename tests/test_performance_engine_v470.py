import dataclasses
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from surface_analyzer.config import PERFORMANCE_POLICY
from surface_analyzer.mixins.data_io import DataIOMixin
import surface_analyzer.mixins.data_io as data_io_module
import surface_analyzer.smart_roi as smart_roi_module
from surface_analyzer.smart_roi import build_adaptive_topology, grow_surface_roi
from surface_analyzer.mixins.analysis import AnalysisMixin
from surface_analyzer.rendering.lod import (
    _spatial_lod_indices_reference, spatial_lod_indices)


class _Reader(DataIOMixin):
    input_layout_mode = 'point_table'

    def __init__(self):
        self.import_info = {}


class PerformanceEngineV470Tests(unittest.TestCase):
    def test_empty_transform_pipeline_returns_shared_arrays(self):
        x = np.arange(12.0); y = x + 1; z = x - 1
        tx, ty, tz = AnalysisMixin._apply_transform_pipeline(x, y, z, [])
        self.assertTrue(np.shares_memory(tx, x))
        self.assertTrue(np.shares_memory(ty, y))
        self.assertTrue(np.shares_memory(tz, z))

    def test_chunked_lod_matches_reference_bit_for_bit(self):
        rng = np.random.default_rng(470)
        count = 260_000
        x = rng.normal(size=count); y = rng.normal(size=count)
        # Exercise deterministic duplicate/tie behavior and invalid points.
        x[100:120] = x[0]; y[100:120] = y[0]
        x[200] = np.nan
        source = rng.permutation(count)
        required = np.array([3, 40, 9000])
        expected = _spatial_lod_indices_reference(
            x, y, source, 3_000, required)
        actual = spatial_lod_indices(x, y, source, 3_000, required)
        np.testing.assert_array_equal(actual, expected)

    def test_csv_fast_path_matches_robust_parser(self):
        content = (
            '# machine metadata\n'
            '0,1,2,alpha\n'
            '1,2,No Data,"plain"\n'
            '2,3,4,\n'
            'bad,row,kept,aux\n'
        )
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'points.csv'
            path.write_text(content, encoding='utf-8')
            reader = _Reader()
            robust = reader._read_full_delimited_text_robust(
                path, 'utf-8', ',', 4, ['X', 'Y', 'Z', 'Note'], 1)
            reader.import_info = {}
            fast = reader._read_full_delimited_text(
                path, 'utf-8', ',', 4, ['X', 'Y', 'Z', 'Note'], 1)
        self.assertEqual(list(fast.columns), list(robust.columns))
        self.assertEqual(len(fast), len(robust))
        for column in ('X', 'Y', 'Z'):
            fast_values = np.asarray([reader._token_to_float(v) for v in fast[column]])
            robust_values = np.asarray([reader._token_to_float(v) for v in robust[column]])
            np.testing.assert_allclose(fast_values, robust_values, equal_nan=True)
        self.assertEqual(reader.import_info['parser_engine'], 'pandas_c')

    def test_csv_simple_table_selects_c_engine(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'points.csv'
            path.write_text('0,1,2,a\n1,2,,b\n2,3,4,\n', encoding='utf-8')
            reader = _Reader()
            frame = reader._read_full_delimited_text(
                path, 'utf-8', ',', 4, ['X', 'Y', 'Z', 'Note'], 0)
        self.assertEqual(reader.import_info['parser_engine'], 'pandas_c')
        self.assertEqual(frame.iloc[1, 2], '')
        self.assertEqual(frame.iloc[2, 3], '')

    def test_implicit_matrix_neighbors_match_legacy_graph_with_holes(self):
        row, col = np.mgrid[:9, :11]
        keep = ~(((row == 4) & (col >= 3) & (col <= 7)) |
                 ((row == 1) & (col == 1)))
        rows, cols = row[keep], col[keep]
        x, y = cols * .04, rows * .06
        fast = build_adaptive_topology(x, y, matrix_rc=(rows, cols))
        legacy_policy = dataclasses.replace(
            PERFORMANCE_POLICY, matrix_fast_component_enabled=False)
        with patch.object(smart_roi_module, 'PERFORMANCE_POLICY', legacy_policy):
            legacy = build_adaptive_topology(x, y, matrix_rc=(rows, cols))
        self.assertEqual(fast['health'], legacy['health'])
        self.assertAlmostEqual(fast['local_spacing_mm'], legacy['local_spacing_mm'])
        for index in range(len(x)):
            np.testing.assert_array_equal(
                fast['adjacency'][index], legacy['adjacency'][index])

    def test_strict_matrix_component_is_bit_identical_to_bfs(self):
        row, col = np.mgrid[:40, :50]
        x, y = col.ravel() * .03, row.ravel() * .03
        z = (0.004*x - 0.002*y).astype(float)
        z[:,] += np.where((x > .7) & (x < .8), .05, 0.0)
        topology = build_adaptive_topology(
            x, y, matrix_rc=(row.ravel(), col.ravel()))
        kwargs = dict(seed_x=x[500], seed_y=y[500], tolerance_mm=.002,
                      topology=topology, mode='plane_residual')
        fast = grow_surface_roi(x, y, z, **kwargs)
        legacy_policy = dataclasses.replace(
            PERFORMANCE_POLICY, matrix_fast_component_enabled=False)
        with patch.object(smart_roi_module, 'PERFORMANCE_POLICY', legacy_policy):
            legacy = grow_surface_roi(x, y, z, **kwargs)
        np.testing.assert_array_equal(fast, legacy)

    def test_strict_matrix_fast_path_honors_pre_cancel(self):
        row, col = np.mgrid[:5, :5]
        x, y = col.ravel(), row.ravel()
        topology = build_adaptive_topology(
            x, y, matrix_rc=(row.ravel(), col.ravel()))
        cancel = threading.Event(); cancel.set()
        from surface_analyzer.workers import TaskCancelled
        with self.assertRaises(TaskCancelled):
            grow_surface_roi(x, y, np.zeros(len(x)), 1, 1, 1, topology,
                             mode='plane_residual', cancel_event=cancel)


if __name__ == '__main__':
    unittest.main()
