import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pandas as pd
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox
from matplotlib.figure import Figure

from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.judgment import VERDICT_TEXT


class VigExtensionModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.localdata = Path(self.temp.name) / 'local'
        extension = Path(__file__).parents[1] / 'extensions' / 'vig-image'
        install = (self.localdata / 'SurfaceRxyZxyAnalyzer'
                   / 'module_extensions' / 'vig-image')
        install.parent.mkdir(parents=True)
        shutil.copytree(extension, install)
        self.localdata_patcher = patch.dict(
            os.environ, {'LOCALAPPDATA': str(self.localdata)})
        self.localdata_patcher.start()
        self.settings = QSettings(
            str(Path(self.temp.name) / 'settings.ini'), QSettings.Format.IniFormat)
        self.window = SurfaceAnalyzerPro(settings=self.settings)
        self.module = self.window._extension_modules[0]
        self.window._confirm_estimated_metrics = lambda action: True
        self._load_layers()
        self.qt.processEvents()

    def tearDown(self):
        self.window.set_theme_preference('light', persist=False)
        self.window.close()
        self.window.deleteLater()
        self.qt.processEvents()
        self.localdata_patcher.stop()
        self.temp.cleanup()

    def _set_layer(self, name, z_mm):
        window = self.window
        window.current_source_name = name
        window.import_info['source_path'] = str(Path(self.temp.name) / name)
        window.df_raw = pd.DataFrame({
            'X': self.x, 'Y': self.y, 'Z': np.asarray(z_mm, dtype=float),
        })
        window.absolute_raw_df = window.df_raw.copy()
        window.active_idx = np.arange(len(self.x))
        window._analysis_z_full = None
        window._df_version += 1

    def _load_layers(self):
        x, y = np.meshgrid(
            np.array([-4.0, -1.2, 1.7, 5.0]),
            np.array([-3.5, 0.4, 4.1]))
        self.x, self.y = x.ravel(), y.ravel()
        self.vig_up = np.array(
            [40, 52, 40, 64, 52, 40, 76, 64, 52, 40, 76, 64], dtype=float)
        self.vig_low = np.array(
            [180, 160, 180, 196, 160, 180, 214, 196, 160, 180, 214, 196],
            dtype=float)
        self.t_up, self.t_mid, self.t_low = 120.0, 150.0, 80.0
        two = self.vig_low + self.t_mid + self.t_low
        three = self.vig_up + two + self.t_up
        self._set_layer('two.csv', two / 1000.0)
        self.assertTrue(self.module.write_slot('two'))
        self._set_layer('three.csv', three / 1000.0)
        self.assertTrue(self.module.write_slot('three'))
        self.two_record = self.module.slots['two']
        self.three_record = self.module.slots['three']

    def _compute(self):
        package = sys.modules['_surface_module_vig-image']
        return package.engine.compute_vig_image(
            self.two_record, self.three_record,
            {'up': self.t_up, 'mid': self.t_mid, 'low': self.t_low})

    def test_navigation_snapshot_defaults_and_result_cards(self):
        self.assertEqual(len(self.window.module_buttons), 4)
        self.window.module_buttons[3].click()
        self.qt.processEvents()
        self.assertEqual(self.window.module_caption.text(), 'VIG 像面分析')
        self.assertEqual(self.window.right_stack.currentIndex(), 3)
        self.assertEqual(self.module.thickness_spins['up'].value(), 150.0)
        self.assertEqual(self.module.tolerance_spin.value(), 0.01)
        self.assertEqual(self.module.clear_buttons['up'].minimumWidth(), 82)
        payload = self._compute()
        self.module._apply_vig_payload(payload)
        self.module.canvas.draw()
        self.assertEqual(payload['n_points'], 12)
        self.assertAlmostEqual(
            self.module.ax.get_position().height,
            self.module.colorbar.ax.get_position().height,
            delta=0.01)
        self.assertEqual(self.module.colorbar.ax.get_ylabel(), 'image (µm)')
        self.assertAlmostEqual(
            float(self.module.metric_labels['rx'].text()), payload['plane']['rx'] * 1e6, places=3)
        self.assertAlmostEqual(
            float(self.module.metric_labels['ry'].text()), payload['plane']['ry'] * 1e6, places=3)
        self.assertIn('匹配 12/12', self.module.hint_label.text())
        self.module.host.set_setting('test_key', 'value')
        self.assertEqual(self.settings.value('extensions/vig-image/test_key'), 'value')

    def test_judgment_rx_ry_only_csv_origin_and_white_report_export(self):
        payload = self._compute()
        self.module._apply_vig_payload(payload)
        self.window.set_judgment_spec({'rx': 1e-9, 'ry': 1e-9, 'pv': 0.0, 'ttv': 0.0})
        result = self.module.host.current_judgment(
            self.module.key, {'rx': payload['plane']['rx'] * 1e6,
                              'ry': payload['plane']['ry'] * 1e6})
        self.assertEqual(result['verdict'], 'fail')
        self.assertEqual({item['key'] for item in result['items']}, {'rx', 'ry'})
        self.window.set_judgment_spec({'rx': 1e9, 'ry': 1e9, 'pv': 0.0})
        self.assertEqual(self.module.host.current_judgment(
            self.module.key, {'rx': 0.0, 'ry': 0.0})['verdict'], 'pass')
        self.window.settings.setValue('export/zero_origin_active_points', True)
        csv_path = Path(self.temp.name) / 'vig.csv'
        report_path = Path(self.temp.name) / 'vig.png'
        with patch.object(QFileDialog, 'getSaveFileName',
                          side_effect=[(str(csv_path), 'CSV'), (str(report_path), 'PNG')]):
            self.module.export_vig_csv()
            self.module.export_vig_report()
        self.assertTrue(report_path.is_file())
        report = self.module._render_vig_report_figure()
        report.canvas.draw()
        map_axis = next(axis for axis in report.axes
                        if axis.get_title() == 'VIG 像面 image 分布')
        report_xy = np.asarray(map_axis.collections[0].get_offsets())
        self.assertAlmostEqual(report_xy[:, 0].min(), 0.0, delta=1e-12)
        self.assertAlmostEqual(report_xy[:, 1].min(), 0.0, delta=1e-12)
        colorbar_axis = next(axis for axis in report.axes
                             if axis.get_ylabel() == 'image (µm)')
        self.assertAlmostEqual(
            map_axis.get_position().height,
            colorbar_axis.get_position().height,
            delta=0.01)
        report_text = '\n'.join(
            [axis.get_title() for axis in report.axes]
            + [text.get_text() for axis in report.axes for text in axis.texts])
        for expected in (
                'VIG 像面报告', 'V5.0.6', 'vig-image', '1.0.0',
                'two.csv', 'three.csv', 'XY 匹配容差', '匹配摘要',
                '导出坐标原点 有效点左下角归零', '结果判定',
                '参与计算 12 点', 'Rx', 'P97/2', '合格'):
            self.assertIn(expected, report_text)
        self.window.set_judgment_spec({})
        unset_report = self.module._render_vig_report_figure()
        header_lines = [
            text.get_text()
            for text in unset_report.axes[0].texts
        ][-1].splitlines()
        self.assertIn('结果判定  尚未设置判定规格', header_lines)
        text = csv_path.read_text(encoding='utf-8-sig')
        self.assertIn('# 导出坐标原点: 有效点左下角归零', text)
        data = pd.read_csv(csv_path, comment='#', encoding='utf-8-sig')
        self.assertEqual(list(data.columns), [
            'X_mm', 'Y_mm', 'VIG_up_um', 'VIG_low_um',
            'thickness_up_um', 'thickness_mid_um', 'thickness_low_um',
            'f_img_um', 'img_principle_z_um', 'image_um', 'Resid_um'])
        self.assertAlmostEqual(data['X_mm'].min(), 0.0, delta=1e-12)
        self.assertAlmostEqual(data['Y_mm'].min(), 0.0, delta=1e-12)
        self.assertEqual(VERDICT_TEXT['fail'], '不合格')

    def test_theme_switch_and_per_point_thickness(self):
        record = dict(self.two_record)
        record['name'] = 'up electrode'
        record['z'] = np.full(len(self.x), self.t_up / 1000.0)
        self.module.slots['up'] = record
        self.module._sync_slot_labels()
        self.assertIn('电极厚度 固定/逐点', self.module.status_text())
        self.module.slots['mid'] = record
        self.module.slots['low'] = record
        self.module._sync_slot_labels()
        self.assertIn('电极厚度 逐点', self.module.status_text())
        self.module.slots['mid'] = None
        self.module.slots['low'] = None
        self.module._sync_slot_labels()
        payload = self._compute()
        self.module._apply_vig_payload(payload)
        self.window.set_theme_preference('dark', persist=False)
        self.qt.processEvents()
        self.module.canvas.draw()
        self.assertEqual(self.module.ax.collections[0].get_cmap().name, 'turbo')

    def test_export_write_errors_show_warnings_without_escaping(self):
        self.module._apply_vig_payload(self._compute())
        with (
                patch.object(self.module.host, 'save_path', return_value='blocked.csv'),
                patch('builtins.open', side_effect=PermissionError('denied')),
                patch.object(QMessageBox, 'warning') as warning):
            self.module.export_vig_csv()
        self.assertIn('VIG CSV 导出失败', warning.call_args.args[2])

        with (
                patch.object(self.module.host, 'save_path', return_value='blocked.png'),
                patch.object(Figure, 'savefig', side_effect=PermissionError('denied')),
                patch.object(QMessageBox, 'warning') as warning):
            self.module.export_vig_report()
        self.assertIn('VIG 报告图导出失败', warning.call_args.args[2])

    def test_parameter_changes_invalidate_result_and_exports(self):
        self.module._apply_vig_payload(self._compute())
        self.assertTrue(any(ready for _, _, ready in self.module.export_actions()))
        self.module.thickness_spins['up'].setValue(151.0)
        self.assertIsNone(self.module.result)
        self.assertFalse(any(ready for _, _, ready in self.module.export_actions()))
        self.assertEqual(
            self.settings.value('extensions/vig-image/thickness_up_um'), 151.0)

        self.module._apply_vig_payload(self._compute())
        self.module.tolerance_spin.setValue(0.02)
        self.assertIsNone(self.module.result)
        self.assertEqual(
            self.settings.value('extensions/vig-image/tolerance_mm'), 0.02)

    def test_estimated_input_quality_reaches_panel_report_csv_and_judgment(self):
        package = sys.modules['_surface_module_vig-image']
        estimated_two = dict(self.two_record)
        estimated_two['metric_quality'] = {
            'estimated': True, 'label': '抽样估计', 'warning': '上游为抽样估计数据。'}
        estimated_two['sampled'] = True
        payload = package.engine.compute_vig_image(
            estimated_two, self.three_record,
            {'up': self.t_up, 'mid': self.t_mid, 'low': self.t_low})
        self.assertTrue(payload['estimated'])
        self.assertTrue(payload['sampled'])
        self.assertTrue(payload['metric_quality']['estimated'])
        self.module._apply_vig_payload(payload)

        self.assertIn('不可直接用于产线放行', self.module.hint_label.text())
        self.assertIn('不可直接用于产线放行', '\n'.join(self.module._export_metadata()))
        judgment_label, = (self.window._judgment_widgets[self.module.key][1],)
        self.assertIn('抽样估计', judgment_label.text())
        self.assertIn('不可直接用于产线放行', judgment_label.parentWidget().toolTip())
        report = self.module._render_vig_report_figure()
        header_text = '\n'.join(
            text.get_text() for axis in report.axes for text in axis.texts)
        self.assertIn('不可直接用于产线放行', header_text)


if __name__ == '__main__':
    unittest.main()
