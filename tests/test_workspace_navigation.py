import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
from surface_analyzer.app import SurfaceAnalyzerPro

class WorkspaceNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = SurfaceAnalyzerPro()
        self.window.show()
        self.app.processEvents()
        self.addCleanup(self.window.close)

    def test_batch_and_workspace_reset_live_only_on_data_page(self):
        w = self.window
        for index in range(3):
            w.workflow_tabs.setCurrentIndex(index)
            self.app.processEvents()
            self.assertEqual(w.btn_batch.isVisible(), index == 0)
            self.assertEqual(w.btn_reset_all.isVisible(), index == 0)
            self.assertTrue(w.cb_surface_display.isVisible())

    def test_region_list_controls_existing_roi_enablement(self):
        w = self.window
        w.roi_shapes = [dict(id=1, name='ROI 1', type='rectangle', view='XY',
                             cx=0, cy=0, width=2, height=2, enabled=True)]
        w._refresh_roi_ui()
        self.assertEqual(w.roi_region_list.count(), 1)
        with patch.object(w, 'update_analysis'):
            w.roi_region_list.item(0).setCheckState(Qt.CheckState.Unchecked)
        self.assertFalse(w.roi_shapes[0]['enabled'])
        self.assertEqual(w.cb_roi_select.currentData(), 1)

    def test_roi_shortcuts_and_filter_visibility_follow_real_modes(self):
        w = self.window
        w.workflow_tabs.setCurrentIndex(2)
        w.roi_tool_buttons[0][0].click()
        self.assertEqual(w.cb_roi_shape.currentIndex(), 1)
        w.workflow_tabs.setCurrentIndex(1)
        with patch.object(w, 'update_analysis'):
            w.cb_filter.setCurrentIndex(3)
        self.assertFalse(w.spin_sigma.isHidden())
        self.assertTrue(w.spin_gaussian_sigma.isHidden())
        self.assertTrue(w.spin_k.isHidden())

    def test_filter_controls_expand_without_overlap_after_mode_switch(self):
        w = self.window
        w.workflow_tabs.setCurrentIndex(1)
        with patch.object(w, 'update_analysis'):
            w.cb_filter.setCurrentIndex(3)
        for _ in range(5):
            self.app.processEvents()
        controls = [w.cb_filter, w.spin_sigma, w.spin_sigma_iter, w.cb_sigma_residual]
        for above, below in zip(controls, controls[1:]):
            self.assertLess(above.geometry().bottom(), below.geometry().top())
        self.assertIs(w.top_import_button.menu(), w.recent_files_menu)

    def test_sidebar_restore_stays_accessible_in_focused_plot(self):
        w = self.window
        w.btn_toggle_parameters.click()
        w.canvas.set_focused_view('3D')
        self.app.processEvents()
        self.assertTrue(w.btn_toggle_parameters.isVisible())
        self.assertTrue(w.sidebar_shell.isHidden())
        w.btn_toggle_parameters.click()
        self.app.processEvents()
        self.assertFalse(w.sidebar_shell.isHidden())
        self.assertTrue(w.tabs.isVisible())

    def test_module_rail_and_parameter_pages_keep_parallel_two_plots(self):
        w = self.window
        self.assertEqual(w.module_rail.width(), 72)
        self.assertTrue(w.tabs.tabBar().isHidden())
        base_axis, measure_axis = w.parallel_canvas.ax_base, w.parallel_canvas.ax_measure
        for index, button in enumerate(w.module_buttons):
            button.click()
            self.app.processEvents()
            self.assertEqual(w.tabs.currentIndex(), index)
            self.assertTrue(button.isChecked())
        self.assertIs(w.parallel_canvas.ax_base, base_axis)
        self.assertIs(w.parallel_canvas.ax_measure, measure_axis)
        self.assertIsNot(base_axis, measure_axis)
        self.assertEqual([w.gap_parameter_tabs.tabText(i) for i in range(3)],
                         ['数据层', '配准', '计算与导出'])
        self.assertEqual([w.parallel_parameter_tabs.tabText(i) for i in range(3)],
                         ['数据面', '计算与导出', '装调'])

    def test_export_menu_routes_only_to_current_module(self):
        w = self.window
        routes = [(0, 'export_report_image'), (1, 'export_gap_report'),
                  (2, 'export_parallel_report')]
        w.last_metrics = {}
        w.parallel_result = {'ready': True}
        w.btn_export_gap_report.setEnabled(True)
        for index, callback in routes:
            with self.subTest(module=index), patch.object(w, callback) as exporter:
                w.tabs.setCurrentIndex(index)
                w._refresh_module_export_menu()
                action = w.btn_export_report.menu().actions()[0]
                self.assertTrue(action.isEnabled())
                action.trigger()
                exporter.assert_called_once_with()

    def test_long_source_name_preserves_identity_without_growing_card(self):
        w = self.window
        label = w.lbl_source
        original_height = label.height()
        name = '当前数据: ' + '多孔曲面_面阵噪声版_' * 20 + '.csv'
        label.setText(name)
        for width in (280, 380, 300):
            w.main_splitter.setSizes([width, 1200])
            self.app.processEvents()
            self.assertEqual(label.text(), name)
            self.assertEqual(label.toolTip(), name)
            self.assertFalse(label.wordWrap())
            self.assertEqual(label.height(), original_height)
            label.grab()

    def test_region_switch_mouse_and_keyboard_update_roi(self):
        from PyQt6.QtTest import QTest
        w = self.window
        w.workflow_tabs.setCurrentIndex(2)
        w.roi_shapes = [dict(id=1, name='ROI 1', type='rectangle', view='XY',
                             cx=0, cy=0, width=2, height=2, enabled=True)]
        w._refresh_roi_ui()
        self.app.processEvents()
        view = w.roi_region_list
        rect = view.visualItemRect(view.item(0))
        hit = view.itemDelegate().switch_rect(rect).center().toPoint()
        with patch.object(w, 'update_analysis'):
            QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=hit)
            self.assertFalse(w.roi_shapes[0]['enabled'])
            view.setFocus()
            QTest.keyClick(view, Qt.Key.Key_Space)
            self.assertTrue(w.roi_shapes[0]['enabled'])

    def test_surface_summary_stays_to_right_of_display_selector(self):
        w=self.window
        w.resize(1600,900)
        summary=w.lbl_surface_residual_metrics
        summary.setText('残差 PV 39.909 µm | RMS 4.924 µm')
        self.app.processEvents()
        selector=w.cb_surface_display
        self.assertIs(summary.parentWidget(),selector.parentWidget())
        self.assertGreaterEqual(summary.x(),selector.geometry().right())
        self.assertFalse(summary.wordWrap())
        self.assertEqual(summary.toolTip(),summary.text())
