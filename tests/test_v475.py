import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PyQt6.QtWidgets import QApplication

from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.plotting import DEFAULT_3D_AZIMUTH, DEFAULT_3D_ELEVATION


class V475RegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _write_wide_xyz(path, width):
        if width == 3:
            header = ['X', 'Y', 'Z']
            coordinate_columns = (0, 1, 2)
        else:
            header = [f'Aux{n}' for n in range(width)]
            coordinate_columns = (2, 4, 6)
            header[2], header[4], header[6] = 'X_mm', 'Y_mm', 'Z_um'
        rows = []
        for i in range(24):
            values = [1000 + i + n for n in range(width)]
            values[coordinate_columns[0]] = i * .1
            values[coordinate_columns[1]] = (i % 6) * .2
            values[coordinate_columns[2]] = 400 + i * .01
            rows.append(','.join(map(str, values)))
        path.write_text(','.join(header) + '\n' + '\n'.join(rows), encoding='utf-8')

    def test_user_selected_xyz_imports_three_eight_and_twelve_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            for width in (3, 8, 12):
                with self.subTest(width=width):
                    path = Path(directory) / f'xyz_{width}.csv'
                    self._write_wide_xyz(path, width)
                    window = SurfaceAnalyzerPro()
                    self.addCleanup(window.close)
                    window.input_layout_mode = 'point_table'
                    self.assertTrue(window.load_path(path))
                    self.assertEqual(len(window.df_raw), 24)
                    self.assertIn('X', window.df_raw.columns)
                    self.assertIn('Y', window.df_raw.columns)
                    self.assertIn('Z', window.df_raw.columns)

    def test_xy_first_two_and_thickness_seventh_ignore_intensity_columns(self):
        headers = [
            'X', 'Y', 'Intensity A', 'Intensity B', 'Quality', 'Signal',
            'Thickness', 'Peak', 'SNR', 'Exposure', 'Status', 'Temperature',
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'wide_xy_thickness.csv'
            rows = [','.join(headers)]
            for index in range(30):
                values = [
                    index * .1, (index % 6) * .2, 100 + index,
                    200 + index, 99, 500 + index, 400 + index * .01,
                    700 + index, 50 + index, 10, 1, 23.5,
                ]
                rows.append(','.join(map(str, values)))
            path.write_text('\n'.join(rows), encoding='utf-8')

            window = SurfaceAnalyzerPro()
            self.addCleanup(window.close)
            window.input_layout_mode = 'point_table'
            self.assertTrue(window.load_path(path))
            self.assertEqual(
                (window.cb_x_col.currentText(), window.cb_y_col.currentText(),
                 window.cb_z_col.currentText()),
                ('X', 'Y', 'Thickness'))
            np.testing.assert_allclose(
                window.df_raw[['X', 'Y', 'Z']].iloc[0].to_numpy(),
                [0.0, 0.0, 0.4])
            np.testing.assert_allclose(
                window.df_raw[['X', 'Y', 'Z']].iloc[-1].to_numpy(),
                [2.9, 1.0, 0.40029])

    def test_default_and_home_camera_use_confirmed_c_view(self):
        window = SurfaceAnalyzerPro()
        self.addCleanup(window.close)
        self.assertEqual(DEFAULT_3D_ELEVATION, 30.0)
        self.assertEqual(DEFAULT_3D_AZIMUTH, -135.0)
        self.assertEqual(window.canvas.ax3d.elev, DEFAULT_3D_ELEVATION)
        self.assertEqual(window.canvas.ax3d.azim, DEFAULT_3D_AZIMUTH)

    def test_3d_scene_zoom_is_fitted_to_landscape_canvas(self):
        window = SurfaceAnalyzerPro()
        self.addCleanup(window.close)
        axis = np.linspace(-15.0, 15.0, 45)
        x, y = np.meshgrid(axis, axis)
        z = 0.45 + 1e-4 * x - 2e-4 * y
        window.df_raw = __import__('pandas').DataFrame({
            'X': x.ravel(), 'Y': y.ravel(), 'Z': z.ravel()})
        window.manual_mask = np.ones(len(window.df_raw), dtype=bool)
        window.active_idx = np.arange(len(window.df_raw))
        window.draw_plots(x.ravel(), y.ravel(), z.ravel())
        self.assertGreaterEqual(window._last_3d_fitted_zoom, 0.65)
        self.assertLessEqual(window._last_3d_fitted_zoom, 1.40)

    def test_xy_xz_yz_hover_uses_source_coordinates_and_original_z(self):
        window = SurfaceAnalyzerPro()
        self.addCleanup(window.close)
        tx = np.array([0.0, 1.0, 2.0])
        ty = np.array([0.0, 1.5, 3.0])
        raw_z = np.array([0.400, 0.410, 0.420])
        shown_z = np.array([1.0, 2.0, 3.0])
        window._configure_projection_hover(
            tx, ty, raw_z, shown_z, np.arange(3), np.arange(3),
            '1阶去除后残差 (µm)')
        for view, axis, point in (
                ('XY', window.canvas.ax_xy, (tx[1], ty[1])),
                ('XZ', window.canvas.ax_xz, (tx[1], shown_z[1])),
                ('YZ', window.canvas.ax_yz, (ty[1], shown_z[1]))):
            with self.subTest(view=view):
                axis.figure.canvas.draw()
                pixel = axis.transData.transform(point)
                window._projection_hover_last_motion = 0.0
                window.on_projection_hover(SimpleNamespace(
                    inaxes=axis, x=float(pixel[0]), y=float(pixel[1]),
                    key='control'))
                annotation = window._projection_hover[view]['annotation']
                self.assertTrue(annotation.get_visible())
                self.assertIn('X: 1 mm', annotation.get_text())
                self.assertIn('Y: 1.5 mm', annotation.get_text())
                self.assertIn('当前显示 Z: 2 µm', annotation.get_text())
                self.assertIn('原始 Z: 0.41 mm', annotation.get_text())

    def test_hover_ignores_points_outside_eight_screen_pixels(self):
        window = SurfaceAnalyzerPro()
        self.addCleanup(window.close)
        values = np.array([0.0])
        window._configure_projection_hover(
            values, values, values, values, [0], [0], 'Z (mm)')
        axis = window.canvas.ax_xy
        axis.figure.canvas.draw()
        pixel = axis.transData.transform((0.0, 0.0))
        window._projection_hover_last_motion = 0.0
        window.on_projection_hover(SimpleNamespace(
            inaxes=axis, x=float(pixel[0] + 20), y=float(pixel[1] + 20),
            key='control'))
        self.assertFalse(window._projection_hover['XY']['annotation'].get_visible())

    def test_hover_does_no_point_search_without_control(self):
        window = SurfaceAnalyzerPro()
        self.addCleanup(window.close)
        values = np.array([0.0])
        window._configure_projection_hover(
            values, values, values, values, [0], [0], 'Z (mm)')
        axis = window.canvas.ax_xy
        axis.figure.canvas.draw()
        pixel = axis.transData.transform((0.0, 0.0))
        window._projection_hover_last_motion = 0.0
        window.on_projection_hover(SimpleNamespace(
            inaxes=axis, x=float(pixel[0]), y=float(pixel[1]), key=None))
        self.assertFalse(window._projection_hover['XY']['annotation'].get_visible())
        self.assertNotIn('XY', window._projection_hover_trees)


if __name__ == '__main__':
    unittest.main()
