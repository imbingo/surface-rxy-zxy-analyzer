import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtWidgets import QApplication

from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.mixins.data_io import DataIOMixin
from surface_analyzer.mixins.roi import ROIMixin
from surface_analyzer.mixins.analysis import AnalysisMixin
from surface_analyzer.smart_roi import build_adaptive_topology, grow_surface_roi


class _RoiHarness(ROIMixin, AnalysisMixin):
    def __init__(self):
        self.roi_next_id = 1


class V450Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_semantic_headers_keep_original_text_and_units(self):
        cases = [
            ('X,Y,Z', ('X', 'Y', 'Z'), {}),
            ('X(mm),Y(mm),Z(mm)', ('X(mm)', 'Y(mm)', 'Z(mm)'), {'x': 'mm', 'y': 'mm', 'z': 'mm'}),
            ('X [µm],Y [μm],Z [um]', ('X [µm]', 'Y [μm]', 'Z [um]'),
             {'x': 'µm', 'y': 'µm', 'z': 'µm'}),
            ('X Pos [mm],Y Pos [mm],Thickness', ('X Pos [mm]', 'Y Pos [mm]', 'Thickness'),
             {'x': 'mm', 'y': 'mm'}),
            ('X Position,Y Position,Z Position', ('X Position', 'Y Position', 'Z Position'), {}),
            ('Position X,Position Y,Height Z', ('Position X', 'Position Y', 'Height Z'), {}),
        ]
        with tempfile.TemporaryDirectory() as directory:
            for index, (header, expected, units) in enumerate(cases):
                path = Path(directory) / f'header_{index}.csv'
                path.write_text(header + '\n\n# comment\n0,0,1\n1,0,2\n0,1,3\n1,1,4\n', encoding='utf-8')
                window = SurfaceAnalyzerPro()
                window.input_layout_mode = 'point_table'
                frame = window._read_table(path)
                self.assertEqual(tuple(frame.columns), expected)
                self.assertEqual(window.import_info['header_confidence'], 'semantic')
                self.assertEqual(window.import_info['header_source_line'], 1)
                self.assertEqual(window.import_info['header_unit_hints'], units)
                window.close()

    def test_custom_header_survives_preamble_comments_and_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'custom.dat'
            path.write_text(
                'Instrument=Demo;Serial=123;Mode=Fast\n'
                'StagePos1;StagePos1;Thickness_AVG\n\n# operator comment\n'
                '0;0;1\n1;0;2\n0;1;3\n1;1;4\n', encoding='utf-8')
            window = SurfaceAnalyzerPro()
            window.input_layout_mode = 'point_table'
            frame = window._read_table(path)
            self.assertEqual(list(frame.columns), ['StagePos1', 'StagePos1_2', 'Thickness_AVG'])
            self.assertEqual(window.import_info['header_confidence'], 'candidate')
            self.assertNotEqual(list(frame.columns), ['Col1', 'Col2', 'Col3'])
            window.close()

    def test_comment_header_and_multicolumn_excel_preamble(self):
        with tempfile.TemporaryDirectory() as directory:
            text_path = Path(directory) / 'comment.xyz'
            text_path.write_text('# X;Y;Z\n# note\n0;0;1\n1;0;2\n0;1;3\n1;1;4\n', encoding='utf-8')
            window = SurfaceAnalyzerPro()
            window.input_layout_mode = 'point_table'
            frame = window._read_table(text_path)
            self.assertEqual(list(frame.columns), ['X', 'Y', 'Z'])
            self.assertEqual(window.import_info['header_confidence'], 'semantic')
            window.close()

            excel_path = Path(directory) / 'multi.xlsx'
            rows = [
                ['Instrument=Demo', None, None, None],
                ['X Pos [mm]', 'Y Pos [mm]', 'Thickness', 'Intensity'],
                [None, None, None, None],
                ['# note', None, None, None],
                [0, 0, 1, 100], [1, 0, 2, 101], [0, 1, 3, 102], [1, 1, 4, 103],
            ]
            pd.DataFrame(rows).to_excel(excel_path, header=False, index=False)
            window = SurfaceAnalyzerPro()
            window.input_layout_mode = 'point_table'
            frame = window._read_table(excel_path)
            self.assertEqual(list(frame.columns), ['X Pos [mm]', 'Y Pos [mm]', 'Thickness', 'Intensity'])
            self.assertEqual(window.import_info['header_confidence'], 'semantic')
            window.close()

    def test_uncertain_large_file_spatial_sampling_downgrades(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'large_custom.csv'
            path.write_text('\n'.join(f'{i},{i + 1},{i + 2}' for i in range(100)) + '\n', encoding='utf-8')
            window = SurfaceAnalyzerPro()
            window.large_file_sample_method = 'spatial_grid'
            window.large_text_import_limit = 30
            window._reset_import_info(path)
            frame = window._sample_large_text(path, 'utf-8', ',', 3,
                                              ['StagePos1', 'StagePos2', 'Thickness_AVG'])
            self.assertTrue(window.import_info['sampling_downgraded'])
            self.assertEqual(window.import_info['sample_method_key'], 'file_position')
            self.assertLessEqual(len(frame), 30)
            window.close()

    def test_precitec_serpentine_topology_preserves_bad_row_hole(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'precitec.dat'
            lines = [
                'Precitec Optronik - FSS Explorer v2.749 - SCAN PATH DATA;',
                '#Object: AreaScan; PointsPerLine: 5; NumberOfLines: 4;',
                '#Encoder V;Encoder Z;Encoder Y;Encoder X;Thickness 1;Intensity;X Pos [mm];Y Pos [mm]',
            ]
            for row in range(4):
                xs = range(5) if row % 2 == 0 else range(4, -1, -1)
                for raw_col, x in enumerate(xs):
                    thickness = 'bad' if row == 1 and raw_col == 2 else f'{200 + row + x * 0.1:.3f}'
                    lines.append(f'1;2;3;4;{thickness};50;{x:.3f};{row * 0.2:.3f}')
            path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
            window = SurfaceAnalyzerPro()
            window.input_layout_mode = 'point_table'
            frame = window._read_table(path)
            self.assertEqual(window.import_info['bad_rows'], 1)
            self.assertTrue(window.import_info['precitec_topology_valid'])
            self.assertTrue(window.import_info['precitec_topology_usable'])
            self.assertEqual(len(frame), 19)
            row_one = frame[frame['_matrix_row'] == 1]
            mapping = dict(zip(row_one['X Pos [mm]'].astype(float), row_one['_matrix_col']))
            self.assertEqual(mapping[0.0], 0)
            self.assertEqual(mapping[4.0], 4)
            self.assertNotIn(2, set(row_one['_matrix_col']))
            window.close()

    def test_v2_surface_following_keeps_bow_and_stops_other_island(self):
        yy, xx = np.mgrid[0:30, 0:40]
        x1 = xx.ravel() * 0.05
        y1 = yy.ravel() * 0.08
        z1 = 1.0 + 0.003 * (x1 - np.mean(x1)) ** 2 + 0.002 * (y1 - np.mean(y1)) ** 2
        x = np.concatenate([x1, x1 + 5.0])
        y = np.concatenate([y1, y1])
        z = np.concatenate([z1, z1 + 0.2])
        topology = build_adaptive_topology(x, y, sensitivity='standard')
        keep = grow_surface_roi(x, y, z, np.mean(x1), np.mean(y1), 0.01, topology,
                                mode='surface_following', sensitivity='standard')
        self.assertEqual(int(keep[:len(x1)].sum()), len(x1))
        self.assertEqual(int(keep[len(x1):].sum()), 0)

    def test_adaptive_knn_fallback_and_v2_ui_defaults(self):
        yy, xx = np.mgrid[0:8, 0:12]
        x = xx.ravel() * 0.1
        y = yy.ravel() * 0.3
        topology = build_adaptive_topology(x, y, sensitivity='standard', delaunay_limit=10)
        self.assertEqual(topology['method'], 'adaptive_knn')
        self.assertIn('超过 Delaunay 上限', topology['fallback_reason'])

        window = SurfaceAnalyzerPro()
        self.assertEqual(window.cb_smart_mode.currentData(), 'surface_following')
        self.assertEqual(window.cb_smart_sensitivity.currentData(), 'standard')
        self.assertTrue(hasattr(window, 'btn_undo_del'))
        window.close()

    def test_v2_matrix_strict_mode_uses_real_matrix_neighbors(self):
        yy, xx = np.mgrid[0:20, 0:25]
        x = xx.ravel() * 0.02
        y = yy.ravel() * 0.20
        z = 1.0 + 0.001 * x + 0.002 * y
        topology = build_adaptive_topology(x, y, matrix_rc=(yy.ravel(), xx.ravel()))
        keep = grow_surface_roi(x, y, z, x[200], y[200], 1e-6, topology,
                                mode='plane_residual', sensitivity='standard')
        self.assertEqual(topology['method'], 'matrix8')
        self.assertEqual(int(keep.sum()), len(x))

    def test_legacy_smart_roi_and_manual_delete_undo(self):
        harness = _RoiHarness()
        cleaned = harness._clean_roi_shapes([{
            'type': 'smart_face', 'seed_x': 0, 'seed_y': 0, 'seed_z': 1,
            'z_tolerance_mm': 0.01, 'smart_mode': 'plane_residual',
        }])
        self.assertEqual(cleaned[0]['smart_algorithm_version'], 1)
        self.assertEqual(cleaned[0]['sensitivity'], 'legacy')

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'undo.csv'
            path.write_text('X,Y,Z\n0,0,1\n1,0,1\n2,0,1\n0,1,1\n1,1,1\n2,1,1\n', encoding='utf-8')
            window = SurfaceAnalyzerPro()
            window.input_layout_mode = 'point_table'
            self.assertTrue(window.load_path(path))
            window.on_select(SimpleNamespace(xdata=-0.1, ydata=-0.1),
                             SimpleNamespace(xdata=0.1, ydata=1.1), 'XY')
            window.apply_manual_deletion()
            self.assertEqual(int((~window.manual_mask).sum()), 2)
            window.undo_manual_deletion()
            self.assertEqual(int((~window.manual_mask).sum()), 0)
            self.assertEqual(window.manual_delete_operations, [])
            window.close()


if __name__ == '__main__':
    unittest.main()
