import json
import shutil
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import numpy as np

from surface_analyzer.format_extensions import FormatRegistry, ReadContext, validate_result
from surface_analyzer.mixins.data_io import DataIOMixin
from surface_analyzer.workers import TaskCancelled


ROOT = Path(__file__).resolve().parents[1]


class Reader(DataIOMixin):
    LARGE_TEXT_IMPORT_LIMIT = 100000

    def __init__(self):
        self.import_info = {}
        self.large_text_import_limit = 20
        self.auto_sample_large_text = True


def fixture(path, z, extra_layer=False, truncate=False):
    xml = f'''<xml><GENERAL><IMAGE_SIZE_X>{z.shape[1]}</IMAGE_SIZE_X>
      <IMAGE_SIZE_Y>{z.shape[0]}</IMAGE_SIZE_Y><FOV_X>2</FOV_X><FOV_Y>3</FOV_Y>
      </GENERAL><LAYER_0><FILENAME_Z>LAYER_0.raw</FILENAME_Z></LAYER_0>
      {'<LAYER_1><FILENAME_Z>LAYER_0.raw</FILENAME_Z></LAYER_1>' if extra_layer else ''}</xml>'''
    raw = z.astype('<f4').tobytes()
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('index.xml', xml)
        archive.writestr('LAYER_0.raw', raw[:-1] if truncate else raw)


class ExtensionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / 'extensions/sensofar-plux', self.root / 'sensofar-plux')
        self.registry = FormatRegistry(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_discovery_disable_version_and_conflicts(self):
        entry = self.registry.reader_for('.plux')
        self.assertEqual(entry['id'], 'sensofar-plux')
        self.assertIn('*.plux', self.registry.file_filter())
        (entry['path'] / 'disabled').touch()
        with self.assertRaises(ValueError):
            self.registry.reader_for('.plux')
        (entry['path'] / 'disabled').unlink()
        old = FormatRegistry(self.root, '4.7.6').entries()[0]
        self.assertIn('升级主程序', old['status'])
        other = self.root / 'other-reader'
        shutil.copytree(entry['path'], other)
        manifest = json.loads((other / 'extension.json').read_text())
        manifest['id'] = 'other-reader'
        (other / 'extension.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, '多个扩展'):
            self.registry.reader_for('.plux')

    def test_plux_preserves_height_scale_holes_and_axes(self):
        z = np.arange(30, dtype=float).reshape(5, 6)
        z[2, 3] = np.nan
        path = self.root / 'test.plux'
        fixture(path, z)
        result = self.registry.read(self.registry.reader_for('.plux'), path, ReadContext())
        np.testing.assert_allclose(result['z'], z * .001, equal_nan=True)
        self.assertEqual(result['pitch_x_mm'], .002)
        self.assertEqual(result['pitch_y_mm'], .003)
        self.assertEqual(result['layer_id'], 'LAYER_0')

    def test_failures_and_cancellation(self):
        path = self.root / 'test.plux'
        z = np.zeros((10, 10))
        entry = self.registry.reader_for('.plux')
        fixture(path, z, extra_layer=True)
        with self.assertRaisesRegex(ValueError, '多个高度层'):
            self.registry.read(entry, path, ReadContext())
        result = self.registry.read(entry, path, ReadContext(layer_id='LAYER_1'))
        self.assertEqual(result['layer_id'], 'LAYER_1')
        fixture(path, z, truncate=True)
        with self.assertRaisesRegex(ValueError, '字节数'):
            self.registry.read(entry, path, ReadContext())
        event = threading.Event(); event.set()
        with self.assertRaises(TaskCancelled):
            self.registry.read(entry, path, ReadContext(cancel_event=event))

    def test_host_matrix_cap_topology_provenance_and_full_mode(self):
        path = self.root / 'test.plux'
        z = np.arange(100, dtype=float).reshape(10, 10)
        z[0, 0] = np.nan
        fixture(path, z)
        entry = self.registry.reader_for('.plux')
        reader = Reader()
        with patch('surface_analyzer.mixins.data_io.FormatRegistry', return_value=self.registry):
            frame = reader._read_extension_table(entry, path)
            self.assertLessEqual(len(frame), 20)
            self.assertEqual(reader.import_info['source_valid_rows'], 99)
            self.assertTrue(reader.import_info['sampled'])
            self.assertIn('_matrix_row', frame)
            stride = reader.import_info['analysis_stride_n']
            np.testing.assert_array_equal(frame['_topology_row'], frame['_matrix_row'] // stride)
            np.testing.assert_array_equal(frame['_topology_col'], frame['_matrix_col'] // stride)
            self.assertEqual(reader.import_info['format_extension_version'], '1.0.0')
            reader.auto_sample_large_text = False
            full = reader._read_extension_table(entry, path)
            self.assertEqual(len(full), 99)
            np.testing.assert_allclose(full['X'], full['_matrix_col'] * .002)
            np.testing.assert_allclose(full['Y'], full['_matrix_row'] * .003)

    def test_invalid_units_and_future_api_rejected(self):
        with self.assertRaisesRegex(ValueError, '单位'):
            validate_result({'kind': 'height_matrix', 'units': 'um'})
        manifest_path = self.root / 'sensofar-plux/extension.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['api_version'] = 99
        manifest_path.write_text(json.dumps(manifest))
        self.assertIn('API', self.registry.entries()[0]['status'])

    def test_main_window_loads_extension_and_preserves_data_on_failure(self):
        from PyQt6.QtWidgets import QApplication
        from surface_analyzer.app import SurfaceAnalyzerPro
        app = QApplication.instance() or QApplication([])
        path = self.root / 'test.plux'
        z = np.arange(120, dtype=float).reshape(10, 12)
        fixture(path, z)
        with patch('surface_analyzer.format_extensions.extension_root', return_value=self.root):
            window = SurfaceAnalyzerPro()
            try:
                window.auto_sample_large_text = False
                window.input_layout_mode = 'pixel_xy'
                window.import_z_unit = 'µm'
                self.assertTrue(window.load_path(path))
                self.assertEqual(len(window.df_raw), 120)
                self.assertEqual(window.cb_z_unit.currentText(), 'mm')
                np.testing.assert_allclose(window.absolute_raw_df['Z'], z.ravel() * .001)
                previous = window.df_raw.copy()
                fixture(path, z, truncate=True)
                with patch('surface_analyzer.mixins.data_io.QMessageBox.critical'):
                    self.assertFalse(window.load_path(path))
                import pandas as pd
                pd.testing.assert_frame_equal(window.df_raw, previous)
            finally:
                window.close()
                app.processEvents()


if __name__ == '__main__':
    unittest.main()
