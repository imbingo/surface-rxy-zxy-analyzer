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
    pixmap = QPixmap(48, 48)
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
