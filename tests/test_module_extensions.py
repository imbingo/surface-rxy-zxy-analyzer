import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from surface_analyzer.module_extensions import ModuleExtensionRegistry


def write_module(root, extension_id='test-module', **manifest_updates):
    path = Path(root) / extension_id
    path.mkdir(parents=True)
    manifest = {
        'id': extension_id,
        'name': 'Test module',
        'version': '1.0.0',
        'api_version': 1,
        'minimum_app_version': '5.0.0',
        'entrypoint': '__init__.py',
    }
    manifest.update(manifest_updates)
    (path / 'extension.json').write_text(json.dumps(manifest), encoding='utf-8')
    (path / '__init__.py').write_text(
        'from .helper import VALUE\n'
        'def create_module(host):\n'
        '    return {"host_api": host.api_version, "value": VALUE}\n',
        encoding='utf-8',
    )
    (path / 'helper.py').write_text('VALUE = "loaded relatively"\n', encoding='utf-8')
    return path


class ModuleExtensionRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_manifest_validation_disabled_marker_and_entrypoint_containment(self):
        valid = write_module(self.root)
        bad_version = write_module(
            self.root, 'bad-version', minimum_app_version='5.0.6')
        bad_path = write_module(
            self.root, 'bad-path', entrypoint='../escape.py')
        disabled = write_module(self.root, 'disabled-module')
        (disabled / 'disabled').touch()

        entries = {
            item['id']: item
            for item in ModuleExtensionRegistry(self.root, '5.0.5').entries()
        }
        self.assertEqual(entries['test-module']['status'], 'ready')
        self.assertIn('升级主程序', entries['bad-version']['status'])
        self.assertIn('入口必须', entries['bad-path']['status'])
        self.assertEqual(entries['disabled-module']['status'], 'disabled')
        self.assertTrue(valid.is_dir())

    def test_package_relative_import_and_create_module_failure_are_isolated(self):
        write_module(self.root)
        broken = write_module(self.root, 'broken-module')
        (broken / '__init__.py').write_text(
            'def create_module(host):\n'
            '    raise RuntimeError("factory exploded")\n',
            encoding='utf-8',
        )
        registry = ModuleExtensionRegistry(self.root, '5.0.6')
        entries = {entry['id']: entry for entry in registry.entries()}
        loaded = registry.load(entries['test-module'], SimpleNamespace(api_version=1))
        self.assertEqual(loaded, {'host_api': 1, 'value': 'loaded relatively'})
        failed = registry.load(entries['broken-module'], SimpleNamespace(api_version=1))
        self.assertIsNone(failed)
        self.assertIn('factory exploded', entries['broken-module']['status'])
        self.assertEqual(entries['test-module']['status'], 'ready')

    def test_import_failure_and_invalid_manifest_are_reported(self):
        broken = write_module(self.root, 'broken-import')
        (broken / '__init__.py').write_text('import missing_module_for_test\n', encoding='utf-8')
        invalid = Path(self.root) / 'invalid-manifest'
        invalid.mkdir()
        (invalid / 'extension.json').write_text('{', encoding='utf-8')

        registry = ModuleExtensionRegistry(self.root, '5.0.6')
        entries = {entry['id']: entry for entry in registry.entries()}
        self.assertIsNone(registry.load(
            entries['broken-import'], SimpleNamespace(api_version=1)))
        self.assertIn('加载失败', entries['broken-import']['status'])
        self.assertNotEqual(entries['invalid-manifest']['status'], 'ready')

    def test_environment_root_is_overridable(self):
        with patch.dict(os.environ, {'LOCALAPPDATA': self.temp.name}):
            root = ModuleExtensionRegistry().root
        self.assertEqual(
            root, Path(self.temp.name) / 'SurfaceRxyZxyAnalyzer' / 'module_extensions')

    def test_cli_check_loads_extension_and_writes_json(self):
        from surface_analyzer.cli import _run_check_module_extension

        extension = write_module(self.root)
        output = self.root / 'check.json'
        self.assertEqual(
            _run_check_module_extension(str(extension), str(output)), 0)
        payload = json.loads(output.read_text(encoding='utf-8'))
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['id'], 'test-module')
        self.assertEqual(payload['self_check'], {'ok': True})


class ExtensionDialogTests(unittest.TestCase):
    def test_dialog_combines_format_and_module_types(self):
        from PyQt6.QtWidgets import QApplication

        from surface_analyzer.extension_dialog import ExtensionDialog
        from surface_analyzer.format_extensions import FormatRegistry

        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            format_root = root / 'format_extensions'
            module_root = root / 'module_extensions'
            format_path = format_root / 'test-reader'
            format_path.mkdir(parents=True)
            (format_path / 'extension.json').write_text(json.dumps({
                'id': 'test-reader',
                'name': 'Test reader',
                'version': '1.0.0',
                'api_version': 1,
                'minimum_app_version': '5.0.0',
                'entrypoint': 'reader.py',
                'suffixes': ['.test'],
            }), encoding='utf-8')
            (format_path / 'reader.py').write_text('', encoding='utf-8')
            module_path = write_module(module_root)
            (module_path / 'disabled').touch()
            dialog = ExtensionDialog(
                None,
                FormatRegistry(format_root, '5.0.6'),
                ModuleExtensionRegistry(module_root, '5.0.6'),
            )
            try:
                self.assertEqual(dialog.tree.topLevelItemCount(), 2)
                self.assertEqual(dialog.tree.topLevelItem(0).text(1), '格式')
                self.assertEqual(dialog.tree.topLevelItem(1).text(1), '模块')
                self.assertEqual(dialog.tree.topLevelItem(1).text(4), '已禁用')
                self.assertEqual(dialog.tree.headerItem().text(1), '类型')
            finally:
                dialog.close()
                app.processEvents()


if __name__ == '__main__':
    unittest.main()
