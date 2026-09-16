import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
from PyQt6.QtWidgets import QApplication

from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.smart_roi import grow_surface_roi


class ImportHardCapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.window = SurfaceAnalyzerPro()
        self.addCleanup(self.window.close)
        self.window.auto_sample_large_text = True
        self.window.large_text_import_limit = 10_000
        self.window.large_text_threshold_mb = 64
        self.window.large_file_sample_method = 'file_position'

    def _assert_cap(self, frame):
        self.assertLessEqual(len(frame), 10_000)
        self.assertTrue(self.window.import_info['sampled'])
        self.assertLessEqual(self.window.import_info['analysis_rows'], 10_000)
        self.assertIn('source_record_rows', self.window.import_info)

    def test_physical_xyz_and_pixel_xy_100k_under_mb_threshold(self):
        for mode, header in (('point_table', 'X,Y,Z'),
                             ('pixel_xy', 'PixelX,PixelY,Z')):
            with self.subTest(mode=mode):
                path = Path(self.directory.name) / f'{mode}.csv'
                with path.open('w', encoding='utf-8') as stream:
                    stream.write(header + '\n')
                    for index in range(100_000):
                        stream.write(f'{index % 1000},{index // 1000},{index * 1e-6:.6f}\n')
                self.window.input_layout_mode = mode
                frame = self.window._read_table(path)
                self._assert_cap(frame)

    def test_text_matrix_100k_respects_import_limit_below_matrix_threshold(self):
        path = Path(self.directory.name) / 'matrix.csv'
        with path.open('w', encoding='utf-8') as stream:
            for row in range(250):
                stream.write(','.join(str(row * 400 + col) for col in range(400)) + '\n')
        self.window.input_layout_mode = 'height_matrix'
        self.window.height_matrix_z_unit = 'mm'
        self.window.matrix_analysis_threshold = 500_000
        frame = self.window._read_table(path)
        self._assert_cap(frame)
        self.assertEqual(self.window.import_info['topology_method'], 'sampled_matrix8')

    def test_zygo_100k_uses_record_trigger_without_mb_trigger(self):
        width, height = 1000, 100
        path = Path(self.directory.name) / 'zygo.xyz'
        header = [
            'Zygo XYZ Data File - Format 1',
            '0 3 0 0 "Tue Aug 25 16:59:23 2026"',
            f'0 0 {width} {height} 1 255',
            f'0 0 {width} {height}', '', '', '',
            '0 0 6.328e-07 0.5 1 0 5.067e-5 1787677163',
            '3 2 0 0 0 666666 0 ""',
            '0 0 1 33 0 0 0.1 60.4318 2 50',
            '0 1 100 0 0 0 0 0 0 0', '0 ""', '1 5 0', '#']
        with path.open('w', encoding='utf-8') as stream:
            stream.write('\n'.join(header) + '\n')
            for index in range(width * height):
                stream.write(f'{index % width} {index // width} {index * 1e-4:.4f}\n')
            stream.write('#\n')
        self.window.input_layout_mode = 'zygo_xyz'
        frame = self.window._read_table(path)
        self._assert_cap(frame)
        self.assertEqual(self.window.import_info['source_valid_rows'], 100_000)

    def test_precitec_100k_uses_declared_record_trigger(self):
        width, height = 1000, 100
        path = Path(self.directory.name) / 'precitec.dat'
        with path.open('w', encoding='utf-8') as stream:
            stream.write('Precitec Optronik - FSS Explorer v2.749 - SCAN PATH DATA;\n')
            stream.write(f'#Object: AreaScan; PointsPerLine: {width}; NumberOfLines: {height};\n')
            stream.write('#Encoder V;Encoder Z;Encoder Y;Encoder X;Thickness 1;Intensity;X Pos [mm];Y Pos [mm]\n')
            for index in range(width * height):
                stream.write(f'1;2;0;0;{200 + index % 17};3.5;{index % width};{index // width};\n')
        self.window.input_layout_mode = 'point_table'
        frame = self.window._read_table(path)
        self._assert_cap(frame)
        self.assertEqual(self.window.import_info['source_valid_rows'], 100_000)

    def test_excel_xyz_100k_has_post_read_hard_cap(self):
        path = Path(self.directory.name) / 'points.xlsx'
        pd.DataFrame({
            'X': np.arange(100_000) % 1000,
            'Y': np.arange(100_000) // 1000,
            'Z': np.arange(100_000) * 1e-6,
        }).to_excel(path, index=False)
        self.window.input_layout_mode = 'point_table'
        frame = self.window._read_table(path)
        self._assert_cap(frame)
        self.assertTrue(self.window.import_info['post_read_cap'])

    def test_auto_sampling_off_allows_full_analysis_input(self):
        path = Path(self.directory.name) / 'full.csv'
        path.write_text('X,Y,Z\n' + '\n'.join(
            f'{i},{i % 11},{i * 1e-6}' for i in range(12_000)), encoding='utf-8')
        self.window.input_layout_mode = 'point_table'
        self.window.auto_sample_large_text = False
        self.window.large_text_import_limit = 1000
        frame = self.window._read_table(path)
        self.assertEqual(len(frame), 12_000)
        self.assertFalse(self.window.import_info['sampled'])
        self.assertEqual(self.window.import_info['analysis_cap_status'], 'disabled')


class SmartTopologyDomainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = SurfaceAnalyzerPro()
        self.addCleanup(self.window.close)

    @staticmethod
    def _four_clusters():
        points = []
        for cx, cy in ((0, 0), (20, 0), (0, 20), (20, 20)):
            for row in range(5):
                for col in range(5):
                    points.append((cx + col, cy + row, 0.001 * (col + row)))
        return tuple(np.asarray(values, dtype=float) for values in zip(*points))

    def test_deleted_clusters_never_enter_topology(self):
        x, y, z = self._four_clusters()
        eligible = (x >= 20) & (y >= 20)
        entry, _ = self.window._get_or_build_smart_topology(
            x, y, z, None, 'standard', eligible)
        self.assertEqual(entry['point_count'], int(eligible.sum()))
        np.testing.assert_array_equal(entry['finite_idx'], np.flatnonzero(eligible))

    def test_deleted_matrix_bridge_is_blocked_before_component_growth(self):
        rows, cols = np.mgrid[0:3, 0:9]
        x = cols.ravel().astype(float); y = rows.ravel().astype(float)
        z = np.zeros_like(x)
        eligible = cols.ravel() != 4
        matrix_rc = (rows.ravel(), cols.ravel())
        entry, _ = self.window._get_or_build_smart_topology(
            x, y, z, matrix_rc, 'standard', eligible)
        idx = entry['finite_idx']
        keep = grow_surface_roi(
            x[idx], y[idx], z[idx], 1.0, 1.0, 0.01,
            entry['topology'], mode='plane_residual', sensitivity='standard')
        selected_global = idx[keep]
        self.assertTrue(np.all(x[selected_global] < 4))
        self.assertFalse(np.any(x[selected_global] > 4))

    def test_manual_roi_restricts_topology_domain(self):
        rows, cols = np.mgrid[0:5, 0:10]
        x = cols.ravel().astype(float); y = rows.ravel().astype(float)
        z = np.zeros_like(x)
        manual_roi = {
            'type': 'rect', 'view': 'XY', 'enabled': True,
            'cx': 2.0, 'cy': 2.0, 'width': 4.2, 'height': 5.0,
        }
        domain = self.window._manual_roi_domain_mask(
            x, y, z, [manual_roi], enabled=True)
        entry, _ = self.window._get_or_build_smart_topology(
            x, y, z, (rows.ravel(), cols.ravel()), 'standard', domain)
        self.assertEqual(entry['point_count'], int(domain.sum()))
        self.assertTrue(np.all(x[entry['finite_idx']] <= 4.1))


if __name__ == '__main__':
    unittest.main()
