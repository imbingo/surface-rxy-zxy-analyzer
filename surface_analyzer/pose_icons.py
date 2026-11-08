"""Font-independent line icons for the eight pose operations."""
from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer


def pose_pixmap(key):
    paths = {
        'CW': 'M19 9a7 7 0 1 1-4-4 M19 3v6h-6',
        'CCW': 'M5 9a7 7 0 1 0 4-4 M5 3v6h6',
        '180': 'M5 8a8 8 0 0 1 14 0 M19 3v5h-5 M19 16a8 8 0 0 1-14 0 M5 21v-5h5',
        'X/Y': 'M4 8h16l-4-4 M20 16H4l4 4',
        'Y': 'M12 3v18 M8 7l4-4 4 4 M8 17l4 4 4-4',
        'X': 'M3 12h18 M7 8l-4 4 4 4 M17 8l4 4-4 4',
        '⊕': 'M12 3v18 M3 12h18 M12 5a7 7 0 1 0 0 14 7 7 0 1 0 0-14',
        '↶': 'M4 9h9a7 7 0 0 1 0 14 M9 4 4 9l5 5',
    }
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="{paths[key]}" fill="none" stroke="#52657a" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    pixmap = QPixmap(60, 60)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(3)
    return pixmap


def focus_icon(restore=False):
    """Return a font-independent focus/restore icon rendered from SVG."""
    if restore:
        path = 'M8 7V4h12v12h-3 M4 8h12v12H4z'
    else:
        path = 'M9 4H4v5 M15 4h5v5 M9 20H4v-5 M15 20h5v-5'
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
           f'<path d="{path}" fill="none" stroke="#52657a" stroke-width="1.7" '
           'stroke-linecap="round" stroke-linejoin="round"/></svg>')
    pixmap = QPixmap(54, 54)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(3)
    return QIcon(pixmap)


def workspace_icon(name, color="#52657a"):
    """Use the pose toolbar's monochrome line style for workspace commands."""
    paths = {
        'surface': 'M5 5C10 1 14 10 21 6 M3 10C9 5 14 15 20 11 M2 15C8 10 13 20 19 16 M9 4C6 8 6 12 5 14 M17 7C14 11 14 15 13 18',
        'recipe': 'M5 2h10l4 4v16H5z M14 2v5h5 M8 11h8 M8 15h8 M8 19h5',
        'layers': 'M12 3 2 8l10 5 10-5z M2 12l10 5 10-5 M2 16l10 5 10-5',
        'parallel': 'M4 10 14 3 M4 17 21 5 M11 21l10-7',
        'export': 'M12 15V2 M8 6l4-4 4 4 M5 8H3v13h18V8h-2',
        'sidebar': 'M3 4h18v16H3z M8 4v16',
        'grid': 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
        '3d': 'M12 2 3 7v10l9 5 9-5V7z M3 7l9 5 9-5 M12 12v10 M7.5 4.5l9 5',
        'xy': 'M4 3v17h17 M8 15l4-6 4 3 4-7',
        'fit': 'M9 3H3v6 M15 3h6v6 M3 15v6h6 M21 15v6h-6 M8 8h8v8H8z',
        'query': 'M12 2v5 M12 17v5 M2 12h5 M17 12h5 M12 5a7 7 0 1 0 0 14 7 7 0 1 0 0-14',
        'open': 'M3 7V4h6l2 3h10v13H3z M3 10h18',
        'save': 'M4 3h13l4 4v14H3V3z M7 3v6h10V3 M7 21v-8h10v8',
    }
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
           f'<path d="{paths[name]}" fill="none" stroke="{color}" '
           'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>')
    pixmap = QPixmap(54, 54)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(3)
    return QIcon(pixmap)
