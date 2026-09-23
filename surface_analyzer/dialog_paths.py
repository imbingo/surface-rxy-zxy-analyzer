"""Persistent import/export locations shared by every file dialog."""

from pathlib import Path

from PyQt6.QtCore import QSettings


_ORG = "SurfaceRxyZxyAnalyzer"
_APP = "SurfaceAnalyzer"
_KEYS = {
    'import': 'file_dialog/last_import_directory',
    'export': 'file_dialog/last_export_directory',
}


def remembered_directory(kind: str) -> Path:
    """Return the last existing directory for an import or export dialog."""
    key = _KEYS[kind]
    saved = str(QSettings(_ORG, _APP).value(key, '') or '').strip()
    directory = Path(saved).expanduser() if saved else Path.home()
    return directory if directory.is_dir() else Path.home()


def dialog_initial_path(kind: str, filename: str = '') -> str:
    directory = remembered_directory(kind)
    return str(directory / filename) if filename else str(directory)


def remember_dialog_path(kind: str, selected_path: str, directory=False) -> None:
    """Persist a selected file's parent or a selected directory itself."""
    if not selected_path:
        return
    selected = Path(selected_path).expanduser()
    location = selected if directory else selected.parent
    if location.is_dir():
        QSettings(_ORG, _APP).setValue(_KEYS[kind], str(location.resolve()))
