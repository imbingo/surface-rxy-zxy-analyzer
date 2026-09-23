import tempfile
import unittest
from pathlib import Path

from PyQt6.QtCore import QSettings

from surface_analyzer.dialog_paths import (
    dialog_initial_path, remember_dialog_path, remembered_directory,
)


class DialogPathMemoryTests(unittest.TestCase):
    KEYS = ('file_dialog/last_import_directory',
            'file_dialog/last_export_directory')

    def setUp(self):
        self.settings = QSettings('SurfaceRxyZxyAnalyzer', 'SurfaceAnalyzer')
        self.previous = {key: self.settings.value(key, None) for key in self.KEYS}

    def tearDown(self):
        for key, value in self.previous.items():
            if value is None:
                self.settings.remove(key)
            else:
                self.settings.setValue(key, value)
        self.settings.sync()

    def test_import_and_export_directories_are_independent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            incoming = root / 'incoming'; incoming.mkdir()
            outgoing = root / 'outgoing'; outgoing.mkdir()
            remember_dialog_path('import', str(incoming / 'measure.csv'))
            remember_dialog_path('export', str(outgoing / 'report.png'))
            self.assertEqual(remembered_directory('import'), incoming)
            self.assertEqual(remembered_directory('export'), outgoing)
            self.assertEqual(dialog_initial_path('export', 'result.csv'),
                             str(outgoing / 'result.csv'))

    def test_selected_output_directory_is_remembered_directly(self):
        with tempfile.TemporaryDirectory() as folder:
            remember_dialog_path('export', folder, directory=True)
            self.assertEqual(remembered_directory('export'), Path(folder))

    def test_missing_saved_directory_falls_back_to_home(self):
        self.settings.setValue(self.KEYS[0], str(Path.home() / '__missing_dialog_dir__'))
        self.assertEqual(remembered_directory('import'), Path.home())


if __name__ == '__main__':
    unittest.main()
