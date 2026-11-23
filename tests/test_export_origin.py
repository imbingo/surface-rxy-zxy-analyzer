import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QMessageBox

from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.mixins import reporting


ROOT = Path(__file__).resolve().parents[1]
DEMO_PATH = ROOT / "demo_data" / "V3.9_StepDemo_XYZ_points.csv"
SETTING_KEY = "export/zero_origin_active_points"
ACTION_TEXT = "导出坐标按有效点左下角归零"


class ExportOriginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = QSettings(
            str(Path(self.temp.name) / "settings.ini"),
            QSettings.Format.IniFormat)
        self.window = SurfaceAnalyzerPro(settings=self.settings)
        self.assertTrue(self.window.load_path(DEMO_PATH))
        self.assertIsNotNone(self.window.last_metrics)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.qt.processEvents()
        self.temp.cleanup()

    def _export_csv(self, path):
        with patch.object(
                reporting.QFileDialog, "getSaveFileName",
                return_value=(str(path), "CSV (*.csv)")), \
             patch.object(reporting, "remember_dialog_path"), \
             patch.object(self.window, "_confirm_estimated_metrics", return_value=True), \
             patch.object(QMessageBox, "critical") as critical:
            self.window.save_file()
        critical.assert_not_called()
        return path.read_text(encoding="utf-8-sig"), pd.read_csv(path, comment="#")

    def test_option_off_exports_interface_coordinates(self):
        self.assertFalse(self.window._export_origin_zero_active_points_enabled())
        tx, ty, _ = self.window.get_final_transformed_data(self.window.df_raw)
        active_idx = self.window.active_idx.copy()
        path = Path(self.temp.name) / "off.csv"

        text, exported = self._export_csv(path)

        np.testing.assert_array_equal(exported["X_mm"], tx[active_idx])
        np.testing.assert_array_equal(exported["Y_mm"], ty[active_idx])
        self.assertEqual(len(exported), len(active_idx))
        self.assertIn("# 导出坐标原点: 与界面坐标一致", text)

    def test_active_point_origin_uses_post_deletion_points_only(self):
        tx, ty, _ = self.window.get_final_transformed_data(self.window.df_raw)
        manual_mask = np.asarray(self.window.manual_mask, dtype=bool).copy()
        min_x = float(np.min(tx[manual_mask]))
        min_y = float(np.min(ty[manual_mask]))
        manual_mask &= ~np.isclose(tx, min_x)
        manual_mask &= ~np.isclose(ty, min_y)
        self.window.manual_mask = manual_mask
        self.window.update_analysis()
        active_idx = self.window.active_idx.copy()
        active_x = tx[active_idx].copy()
        active_y = ty[active_idx].copy()
        dx, dy = float(active_x.min()), float(active_y.min())

        self.window._set_export_origin_zero_active_points(False)
        off_text, exported_off = self._export_csv(Path(self.temp.name) / "off.csv")
        self.window._set_export_origin_zero_active_points(True)
        on_text, exported_on = self._export_csv(Path(self.temp.name) / "on.csv")

        self.assertEqual(len(exported_on), len(exported_off))
        self.assertAlmostEqual(float(exported_on["X_mm"].min()), 0.0, delta=1e-12)
        self.assertAlmostEqual(float(exported_on["Y_mm"].min()), 0.0, delta=1e-12)
        np.testing.assert_array_equal(exported_on["Z_um"], exported_off["Z_um"])
        np.testing.assert_array_equal(exported_on["Resid_um"], exported_off["Resid_um"])
        self.assertIn("# 导出坐标原点: 与界面坐标一致", off_text)
        self.assertIn(
            f"# 导出坐标原点: 有效点左下角归零（导出 X = 界面 X − {dx:.6f} mm，"
            f"导出 Y = 界面 Y − {dy:.6f} mm；拟合平面系数和 ROI 形状仍为界面坐标）",
            on_text)
        current_tx, current_ty, _ = self.window.get_final_transformed_data(
            self.window.df_raw)
        np.testing.assert_array_equal(current_tx, tx)
        np.testing.assert_array_equal(current_ty, ty)

    def test_report_export_shifts_coordinates_metrics_and_roi_overlays(self):
        self.window._set_export_origin_zero_active_points(True)
        tx, ty, _ = self.window.get_final_transformed_data(self.window.df_raw)
        active_idx = self.window.active_idx.copy()
        dx, dy = float(tx[active_idx].min()), float(ty[active_idx].min())
        roi_shapes = [
            {"type": "circle", "view": "XY", "cx": dx + 0.2, "cy": dy + 0.3,
             "radius": 0.1, "enabled": True},
            {"type": "rect", "view": "XY", "cx": dx + 0.4, "cy": dy + 0.5,
             "width": 0.2, "height": 0.3, "enabled": True},
            {"type": "smart_face", "seed_x": dx + 0.6, "seed_y": dy + 0.7,
             "xy_radius_mm": 0.1, "enabled": True},
        ]
        roi_info = {
            "enabled": True,
            "summary": "test",
            "shape_lines": [],
            "shapes": roi_shapes,
            "roi_enabled": True,
        }
        path = Path(self.temp.name) / "report.png"
        original_render = self.window._render_report_figure
        captured = {}

        def render_spy(*args, **kwargs):
            captured["figure"] = original_render(*args, **kwargs)
            captured["args"] = args
            captured["kwargs"] = kwargs
            return captured["figure"]

        with patch.object(
                reporting.QFileDialog, "getSaveFileName",
                return_value=(str(path), "PNG (*.png)")), \
             patch.object(reporting, "remember_dialog_path"), \
             patch.object(self.window, "_confirm_estimated_metrics", return_value=True), \
             patch.object(self.window, "_roi_report_info", return_value=roi_info), \
             patch.object(self.window, "_render_report_figure", side_effect=render_spy), \
             patch.object(self.window, "_draw_roi_overlays",
                           wraps=self.window._draw_roi_overlays) as draw_overlays, \
             patch.object(QMessageBox, "information"), \
             patch.object(QMessageBox, "critical") as critical:
            self.window.export_report_image()

        critical.assert_not_called()
        self.assertTrue(path.is_file())
        self.assertGreater(path.stat().st_size, 0)
        args = captured["args"]
        shifted_x, shifted_y = args[1], args[2]
        np.testing.assert_allclose(shifted_x[active_idx].min(), 0.0, atol=1e-12)
        np.testing.assert_allclose(shifted_y[active_idx].min(), 0.0, atol=1e-12)
        self.assertEqual(captured["kwargs"]["xy_offset"], (dx, dy))
        for key in ("rx", "ry", "pv"):
            self.assertAlmostEqual(
                args[5][key], self.window.last_metrics[key], delta=1e-9)

        translated = draw_overlays.call_args.args[1]
        self.assertAlmostEqual(translated[0]["cx"], roi_shapes[0]["cx"] - dx)
        self.assertAlmostEqual(translated[0]["cy"], roi_shapes[0]["cy"] - dy)
        self.assertAlmostEqual(translated[1]["cx"], roi_shapes[1]["cx"] - dx)
        self.assertAlmostEqual(translated[1]["cy"], roi_shapes[1]["cy"] - dy)
        self.assertAlmostEqual(
            translated[2]["seed_x"], roi_shapes[2]["seed_x"] - dx)
        self.assertAlmostEqual(
            translated[2]["seed_y"], roi_shapes[2]["seed_y"] - dy)
        self.assertAlmostEqual(roi_shapes[0]["cx"], dx + 0.2)
        self.assertTrue(any(
            "导出坐标原点: 有效点左下角" in text.get_text()
            for axis in captured["figure"].axes for text in axis.texts))
        captured["figure"].clear()

    def test_menu_option_is_single_layer_only_and_persists(self):
        self.window.tabs.setCurrentIndex(0)
        self.window._refresh_module_export_menu()
        menu = self.window.btn_export_report.menu()
        action = next(item for item in menu.actions() if item.text() == ACTION_TEXT)
        self.assertTrue(action.isCheckable())
        self.assertFalse(action.isChecked())
        self.assertEqual(
            action.toolTip(),
            "导出时把参与计算的点（删点、ROI、滤波之后）的最小 X、Y 平移到 0；"
            "界面坐标、Rx/Ry/PV 不变")
        action.setChecked(True)
        self.assertIs(self.settings.value(SETTING_KEY, False, type=bool), True)

        for index in (self.window.math_tab_index, self.window.parallel_tab_index):
            self.window.tabs.setCurrentIndex(index)
            self.window._refresh_module_export_menu()
            self.assertNotIn(
                ACTION_TEXT,
                [item.text() for item in self.window.btn_export_report.menu().actions()])


if __name__ == "__main__":
    unittest.main()
