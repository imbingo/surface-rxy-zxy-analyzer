import os,tempfile,unittest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from unittest.mock import patch
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication,QDialog,QComboBox,QDoubleSpinBox,QSpinBox,QTabWidget
from surface_analyzer.app import SurfaceAnalyzerPro

class ImportSettingsLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_tabs_preserve_values_on_cancel_and_commit_on_accept(self):
        w=SurfaceAnalyzerPro();self.addCleanup(w.close)
        old=(w.input_layout_mode,w.height_matrix_pitch_x_um,w.import_search_start_row)
        def edit(dialog,result):
            tabs=dialog.findChild(QTabWidget,'importSettingsTabs')
            layout=dialog.findChild(QComboBox,'inputLayout')
            pitch=dialog.findChild(QDoubleSpinBox,'pitchX')
            start=dialog.findChild(QSpinBox,'bodyStart')
            layout.setCurrentIndex(layout.findData('height_matrix'))
            pitch.setValue(12.3456);start.setValue(17)
            tabs.setCurrentIndex(3);tabs.setCurrentIndex(0)
            self.assertEqual(pitch.value(),12.3456)
            layout.setCurrentIndex(layout.findData('point_table'))
            self.assertTrue(pitch.isHidden())
            layout.setCurrentIndex(layout.findData('height_matrix'))
            self.assertEqual(pitch.value(),12.3456)
            return result
        with patch.object(QDialog,'exec',lambda dialog:edit(dialog,QDialog.DialogCode.Rejected)):
            w.show_bigfile_settings_dialog()
        self.assertEqual((w.input_layout_mode,w.height_matrix_pitch_x_um,w.import_search_start_row),old)
        with tempfile.TemporaryDirectory() as directory:
            settings=QSettings(str(Path(directory)/'settings.ini'),QSettings.Format.IniFormat)
            w.settings=settings
            with patch('surface_analyzer.mixins.data_io.QSettings',return_value=settings),patch.object(QDialog,'exec',lambda dialog:edit(dialog,QDialog.DialogCode.Accepted)):
                w.show_bigfile_settings_dialog()
        self.assertEqual(w.input_layout_mode,'height_matrix')
        self.assertEqual(w.height_matrix_pitch_x_um,12.3456)
        self.assertEqual(w.import_search_start_row,17)
