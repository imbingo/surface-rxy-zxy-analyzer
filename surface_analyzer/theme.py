"""Light / dark UI theme.

The light colors written throughout the UI stay the single source of truth.
Dark mode derives every stylesheet, icon and plot-chrome color from them, so
modules keep one visual language without duplicating each stylesheet.
Measurement colormaps and exported reports are never rethemed.
"""
import re
import weakref

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.colors import to_rgba
from PyQt6.QtCore import QEvent, QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication, QPalette
from PyQt6.QtWidgets import QApplication, QWidget

THEME_SETTINGS_KEY = "ui/theme"
THEME_MODES = ("light", "dark", "system")
THEME_LABELS = {"light": "浅色", "dark": "深色", "system": "跟随系统"}

WORKSPACE = "#0e0e0f"
PANEL = "#141415"
CARD = "#19191b"
FIELD = "#111112"
BORDER = "#2c2c2f"
DIVIDER = "#232326"
TEXT = "#ededed"
MUTED = "#9a9a9f"
DISABLED = "#5c5c61"
SELECTED = "#26262a"
HOVER = "#303035"
PRIMARY = "#ededed"
PRIMARY_TEXT = "#111112"
PASS = "#5cc98f"
FAIL = "#ff6b6b"
WARN = "#e8b45a"
INFO = "#8ab4f8"
VIOLET = "#c4b5fd"

_DARK_VALUES = {c.lower() for c in (
    WORKSPACE, PANEL, CARD, FIELD, BORDER, DIVIDER, TEXT, MUTED, DISABLED, SELECTED, HOVER,
    PRIMARY, PRIMARY_TEXT, PASS, FAIL, WARN, INFO, VIOLET, "#2a2418", "#2a1d1e", "#3a3a3e")}
_WORKSPACE_BG = {"#eceff3", "#f2f1f0"}
_PANEL_BG = {"#faf9f8", "#f7f6f5", "#f6f8fa", "#f9fafb", "#fafafa", "#fdfcfb", "#efeeed", "#eef1f4"}
_PRIMARY_BG = {"#4779a6", "#2f6db0", "#285f9a", "#4b7ca8", "#39698f"}
_COLOR = re.compile(r"#[0-9a-fA-F]{6}\b|\b(?:white|black)\b|rgba\(\s*255,\s*255,\s*255,\s*0?\.\d+\s*\)")
_DECL = re.compile(r"([\w-]+)\s*:\s*([^;{}]+)")
_BLOCK = re.compile(r"(?<=\{)[^{}]+(?=\})")

_mode = "light"


def is_dark():
    return _mode == "dark"


def dark_color(prop, source):
    """Map one light-theme color to its dark counterpart for a QSS property."""
    key = source.lower().replace(" ", "")
    if key in _DARK_VALUES:
        return source
    rgb = QColor("white" if key.startswith("rgba") else key)
    r, g, b, _ = rgb.getRgb()
    value = (r + g + b) / 3
    sat = max(r, g, b) - min(r, g, b)
    if prop in ("color", "selection-color"):
        if key in ("#b42318", "#b4453a"):
            return FAIL
        if key == "white" or (value < 70 and sat < 26):
            return TEXT
        if key in ("#b6bdc4", "#aebfd0", "#c8ced4"):
            return DISABLED
        if sat < 37:
            return MUTED
        if r > 1.27 * g and r > 1.23 * b:
            return WARN if g > 1.45 * b else FAIL
        if g > 1.19 * r and g > 1.12 * b:
            return PASS
        if r > 1.18 * g and b > r:
            return VIOLET
        return TEXT
    if prop.startswith("border") or prop == "outline":
        if sat > 38 and value < 169:
            return HOVER
        return DIVIDER if value > 220 else BORDER
    if prop.startswith("background") or prop in ("selection-background-color", "alternate-background-color"):
        if key in _WORKSPACE_BG:
            return WORKSPACE
        if key in _PANEL_BG:
            return PANEL
        if key in _PRIMARY_BG:
            return PRIMARY
        if key in ("#fff7ed", "#fff8e6", "#fff4df"):
            return "#2a2418"
        if key in ("#fbf0ef", "#fff1f0"):
            return "#2a1d1e"
        if key in ("#aebfd4", "#aebfd0"):
            return "#3a3a3e"
        if value < 160 and sat > 60:
            return key
        if sat > 12:
            return SELECTED
        return CARD
    return source


def dark_qss(css):
    def substitute(match):
        prop, rhs = match.groups()
        return f"{prop}: {_COLOR.sub(lambda c: dark_color(prop.lower(), c.group()), rhs)}"
    if "{" in css:
        return _BLOCK.sub(lambda block: _DECL.sub(substitute, block.group()), css)
    return _DECL.sub(substitute, css)


def ui_color(source, prop="color"):
    """Color for custom painting / rich text that follows the active theme."""
    return dark_color(prop, source) if is_dark() else source


def icon_color(source="#52657a"):
    if not is_dark():
        return source
    key = source.lower()
    if key in ("#ffffff", "white"):
        return PRIMARY_TEXT
    if key in ("#286b9f", "#2f6db0"):
        return TEXT
    return MUTED


def bind_icon(widget, factory, setter="setIcon"):
    """Set a widget icon from 'factory' and rebuild it whenever the theme changes."""
    def apply():
        getattr(widget, setter)(factory())
    widget._theme_icon = apply
    apply()


# Overrides appended to the main window in dark mode: these elements carry an
# emphasis role whose dark form is not a 1:1 color translation.
DARK_WINDOW_QSS = f"""
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{ background: {FIELD}; border-color: {BORDER}; color: {TEXT}; }}
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{ background: {PANEL}; color: {DISABLED}; }}
QPushButton:disabled {{ color: {DISABLED}; border-color: {DIVIDER}; }}
QTabBar::tab:selected {{ background: {SELECTED}; color: {TEXT}; }}
#workflowTabBar::tab:selected {{ background: transparent; color: {TEXT}; border-bottom: 2px solid {PRIMARY}; }}
#accentBtn, #exportMenu {{ background: {PRIMARY}; color: {PRIMARY_TEXT}; border: none; }}
#accentBtn:hover, #exportMenu:hover {{ background: #ffffff; color: {PRIMARY_TEXT}; }}
#accentBtn:disabled, #exportMenu:disabled {{ background: #3a3a3e; color: #77777c; }}
#accentSoftBtn {{ background: {SELECTED}; border: 1px solid {BORDER}; color: {TEXT}; }}
#accentSoftBtn:hover {{ background: {HOVER}; color: {TEXT}; }}
#moduleNav:checked {{ background: {SELECTED}; color: {TEXT}; border-left: 2px solid {PRIMARY}; }}
#moduleNav:hover, #themeButton:hover {{ background: {SELECTED}; }}
#metricCardAccent {{ background: transparent; border: none; }}
#metricTitleAccent {{ color: {MUTED}; }}
#metricValueAccent {{ color: {TEXT}; }}
#brandDot {{ background: #4f8cff; }}
#plotDot {{ background: #6b6b70; }}
#verPill {{ background: transparent; border: none; color: {MUTED}; }}
#stepBadgeActive {{ background: {PRIMARY}; color: {PRIMARY_TEXT}; }}
"""

DARK_APP_QSS = f"""
QMenu, QComboBox QAbstractItemView {{ background: {CARD}; color: {TEXT}; border: 1px solid {BORDER};
    selection-background-color: {SELECTED}; selection-color: {TEXT}; }}
QMenu::item:selected {{ background: {SELECTED}; color: {TEXT}; }}
QMenu::item:disabled {{ color: {DISABLED}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}
QToolTip {{ background: {CARD}; color: {TEXT}; border: 1px solid {BORDER}; }}
QScrollBar:vertical {{ background: {PANEL}; width: 11px; margin: 0; }}
QScrollBar:horizontal {{ background: {PANEL}; height: 11px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDER}; min-height: 22px; border-radius: 5px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; min-width: 22px; border-radius: 5px; margin: 2px; }}
QScrollBar::handle:hover {{ background: #3d3d42; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QHeaderView::section {{ background: {PANEL}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER}; padding: 4px 6px; }}
QTableView, QTreeView, QListView {{ background: {FIELD}; color: {TEXT}; gridline-color: {DIVIDER};
    alternate-background-color: {CARD}; selection-background-color: {SELECTED}; selection-color: {TEXT}; }}
QTableCornerButton::section {{ background: {PANEL}; border: none; }}
QDialog, QMessageBox {{ background: {PANEL}; }}
QProgressBar {{ background: {FIELD}; border: 1px solid {BORDER}; border-radius: 5px; color: {TEXT}; text-align: center; }}
QProgressBar::chunk {{ background: {INFO}; border-radius: 4px; }}
"""

LIGHT_POPUP_QSS = """
QComboBox QAbstractItemView, QMenu {
    background: #ffffff; color: #233445; border: 1px solid #d4dce5;
    selection-background-color: #dce8f3; selection-color: #173b5e;
}
QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected,
QComboBox QAbstractItemView::item:focus, QMenu::item:selected {
    background: #dce8f3; color: #173b5e;
}
QComboBox QAbstractItemView::item:disabled, QMenu::item:disabled { color: #89939f; }
"""


def light_palette(base):
    # Do not capture an OS dark palette as the app's light baseline. In that
    # case a white QSS popup inherited white Text/HighlightedText on Windows.
    palette = QPalette(base)
    values = dict(Window='#f7f6f5', WindowText='#233445', Base='#ffffff',
        AlternateBase='#f3f6f9', Text='#233445', Button='#f7f6f5', ButtonText='#233445',
        ToolTipBase='#ffffff', ToolTipText='#233445', Highlight='#dce8f3',
        HighlightedText='#173b5e', PlaceholderText='#63758a')
    for role, color in values.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(color))
    for role in ('Text', 'WindowText', 'ButtonText'):
        palette.setColor(QPalette.ColorGroup.Disabled, getattr(QPalette.ColorRole, role), QColor('#89939f'))
    return palette


_PALETTE = {
    "Window": PANEL, "WindowText": TEXT, "Base": FIELD, "AlternateBase": CARD,
    "ToolTipBase": CARD, "ToolTipText": TEXT, "Text": TEXT, "Button": CARD,
    "ButtonText": TEXT, "BrightText": FAIL, "Light": BORDER, "Midlight": BORDER,
    "Mid": DIVIDER, "Dark": WORKSPACE, "Shadow": WORKSPACE, "Highlight": SELECTED,
    "HighlightedText": TEXT, "Link": INFO, "PlaceholderText": MUTED,
}


def dark_palette(base):
    palette = QPalette(base)
    for role, value in _PALETTE.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(value))
    for role in ("WindowText", "Text", "ButtonText"):
        palette.setColor(QPalette.ColorGroup.Disabled, getattr(QPalette.ColorRole, role), QColor(DISABLED))
    return palette


# ---------------------------------------------------------------- matplotlib
def _same(a, b):
    try:
        return a is not None and b is not None and all(
            abs(x - y) < 1e-3 for x, y in zip(to_rgba(a), to_rgba(b)))
    except (TypeError, ValueError):
        return False


def _theme(artist, prop, dark_value, store=None):
    """Swap 'prop' between its light value (as last set by code) and 'dark_value'."""
    store = artist if store is None else store
    getter = getattr(artist, "get_" + prop)
    setter = getattr(artist, "set_" + prop)
    current = getter()
    record = store.__dict__.get("_theme_" + prop)
    light = record[0] if record and _same(current, record[1]) else current
    if isinstance(light, str) and light.lower() == "none":
        return
    try:
        alpha = to_rgba(light)[3]
    except (TypeError, ValueError):
        return
    if alpha == 0:
        return
    target = to_rgba(dark_value, alpha) if is_dark() else light
    setter(target)
    store.__dict__["_theme_" + prop] = (light, target)


def _theme_text(text, role):
    if not text.get_text():
        return
    if role is None:
        color = text.get_color()
        record = text.__dict__.get("_theme_color")
        light = record[0] if record and _same(color, record[1]) else color
        try:
            role = dark_color("color", QColor(*[int(c * 255) for c in to_rgba(light)]).name())
        except (TypeError, ValueError):
            return
    _theme(text, "color", role)
    bbox = text.get_bbox_patch()
    if bbox is not None:
        _theme(bbox, "facecolor", PANEL)
        _theme(bbox, "edgecolor", BORDER)


def _theme_ticks(axis):
    ticks = axis.get_major_ticks()
    if not ticks:
        return
    tick = ticks[0]
    current = (tick.label1.get_color(), tick.tick1line.get_color(), tick.gridline.get_color())
    record = axis.__dict__.get("_theme_ticks")
    if record and all(_same(a, b) for a, b in zip(current, record[1])):
        light = record[0]
    else:
        light = current
    target = (MUTED, BORDER, DIVIDER) if is_dark() else light
    axis.set_tick_params(which="both", labelcolor=target[0], color=target[1], grid_color=target[2])
    axis.__dict__["_theme_ticks"] = (light, target)


def apply_figure_theme(figure):
    """Retheme figure chrome (background, axes, ticks, grid, labels, legend) only."""
    _theme(figure.patch, "facecolor", CARD)
    for text in figure.texts:
        _theme_text(text, TEXT)
    for ax in figure.axes:
        _theme(ax.patch, "facecolor", CARD)
        for spine in ax.spines.values():
            _theme(spine, "edgecolor", BORDER)
        for title in (ax.title, ax._left_title, ax._right_title):
            _theme_text(title, TEXT)
        for text in ax.texts:
            _theme_text(text, None)
        axes = [ax.xaxis, ax.yaxis] + ([ax.zaxis] if hasattr(ax, "zaxis") else [])
        for axis in axes:
            _theme_text(axis.label, MUTED)
            _theme_text(axis.get_offset_text(), MUTED)
            _theme_ticks(axis)
            if hasattr(axis, "pane"):
                _theme(axis.pane, "facecolor", PANEL)
                _theme(axis.pane, "edgecolor", BORDER)
                _theme(axis.line, "color", BORDER)
                grid = axis._axinfo.setdefault("grid", {})
                record = axis.__dict__.get("_theme_grid3d")
                current = grid.get("color")
                light = record[0] if record and _same(current, record[1]) else current
                if light is not None:
                    grid["color"] = DIVIDER if is_dark() else light
                    axis.__dict__["_theme_grid3d"] = (light, grid["color"])
        legend = ax.get_legend()
        if legend is not None:
            _theme(legend.get_frame(), "facecolor", PANEL)
            _theme(legend.get_frame(), "edgecolor", BORDER)
            for text in legend.get_texts():
                _theme_text(text, TEXT)


class ThemedFigureCanvas(FigureCanvasQTAgg):
    """Qt canvas whose chrome follows the active theme on every draw."""
    def draw(self):
        apply_figure_theme(self.figure)
        super().draw()


# ---------------------------------------------------------------- manager
_LIGHT_QSS = "_themeLightQss"
_APPLIED_QSS = "_themeAppliedQss"


class ThemeManager(QObject):
    changed = pyqtSignal(str)

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.preference = "light"
        self.mode = "light"
        self.windows = weakref.WeakSet()
        self._light_palette = light_palette(app.palette())
        self._light_app_qss = app.styleSheet()
        self._busy = False
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(self._on_system_scheme)

    def _on_system_scheme(self, *_):
        if self.preference == "system":
            self.set_preference("system")

    @staticmethod
    def system_mode():
        hints = QGuiApplication.styleHints()
        scheme = hints.colorScheme() if hasattr(hints, "colorScheme") else None
        return "dark" if scheme == Qt.ColorScheme.Dark else "light"

    def register(self, window):
        self.windows.add(window)
        self._sync(window)

    def set_preference(self, preference):
        global _mode
        preference = preference if preference in THEME_MODES else "light"
        self.preference = preference
        mode = self.system_mode() if preference == "system" else preference
        changed = mode != self.mode
        self.mode = _mode = mode
        if mode == "dark":
            self.app.installEventFilter(self)
        else:
            self.app.removeEventFilter(self)
        self.app.setPalette(dark_palette(self._light_palette) if mode == "dark" else self._light_palette)
        self.app.setStyleSheet(self._light_app_qss + (DARK_APP_QSS if mode == "dark" else LIGHT_POPUP_QSS))
        widgets = self.app.allWidgets()
        for widget in widgets:
            self._sync(widget)
        for widget in widgets:
            replay = getattr(widget, "_theme_icon", None)
            if callable(replay):
                replay()
            if isinstance(widget, FigureCanvasQTAgg):
                apply_figure_theme(widget.figure)
                if widget.isVisible():
                    widget.draw_idle()
        if changed:
            self.changed.emit(mode)
        return mode

    def _target(self, widget, light):
        if self.mode != "dark":
            return light + (LIGHT_POPUP_QSS if widget in self.windows else "")
        css = dark_qss(light)
        if widget in self.windows:
            css += DARK_WINDOW_QSS
        return css

    def _sync(self, widget):
        css = widget.styleSheet()
        applied = widget.property(_APPLIED_QSS)
        if applied is not None and css == applied:
            light = widget.property(_LIGHT_QSS) or ""
        else:
            light = css
        if not light and widget not in self.windows:
            return
        target = self._target(widget, light)
        widget.setProperty(_LIGHT_QSS, light)
        widget.setProperty(_APPLIED_QSS, target)
        if target != css:
            self._busy = True
            try:
                widget.setStyleSheet(target)
            finally:
                self._busy = False

    def light_stylesheet(self, widget):
        """Stylesheet as written by the code, independent of the active theme."""
        css = widget.styleSheet()
        if widget.property(_APPLIED_QSS) == css:
            return widget.property(_LIGHT_QSS) or ""
        return css

    def eventFilter(self, obj, event):
        if (not self._busy and self.mode == "dark"
                and event.type() in (QEvent.Type.Polish, QEvent.Type.StyleChange)
                and isinstance(obj, QWidget)):
            self._sync(obj)
        return False


def manager(app=None):
    app = app or QApplication.instance()
    existing = getattr(app, "_surface_theme_manager", None)
    if existing is None:
        existing = ThemeManager(app)
        app._surface_theme_manager = existing
    return existing
