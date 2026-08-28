"""Generate deterministic V4.5.0 import/topology demos and an offscreen GUI preview."""

import os
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / 'demo_data'
sys.path.insert(0, str(ROOT))

from surface_analyzer.app import SurfaceAnalyzerPro


def write_header_demo():
    path = DEMO / 'V4.5.0_自定义表头兼容_Demo.dat'
    path.write_text(
        'Instrument=Demo;Serial=V450;Mode=Surface\n'
        'StagePos1;StagePos2;Thickness_AVG;Intensity\n\n'
        '# 表头与数据之间允许空行和注释，真实列名仍会保留\n'
        '0;0;0.102;51000\n1;0;0.103;50900\n0;1;0.104;50800\n1;1;0.105;50700\n',
        encoding='utf-8')
    return path


def write_precitec_demo():
    points_per_line = 30
    number_of_lines = 20
    path = DEMO / 'V4.5.0_Precitec_蛇形Bow_Demo.dat'
    lines = [
        'Precitec Optronik - FSS Explorer v2.749 - SCAN PATH DATA;',
        'ScanProgram: <V450TopologyDemo>;',
        f'#Object: AreaScan; PointsPerLine: {points_per_line}; NumberOfLines: {number_of_lines};',
        '#Encoder V;Encoder Z;Encoder Y;Encoder X;Thickness 1;Intensity;X Pos [mm];Y Pos [mm]',
    ]
    for row in range(number_of_lines):
        x_order = range(points_per_line) if row % 2 == 0 else range(points_per_line - 1, -1, -1)
        for raw_col, col in enumerate(x_order):
            x = col * 0.08
            y = row * 0.18
            bow_um = 220.0 + 2.8 * (x - 1.16) ** 2 + 1.2 * (y - 1.71) ** 2
            value = 'bad' if row == 7 and raw_col == 11 else f'{bow_um:.6f}'
            intensity = 50000 + row * 10 + col
            lines.append(f'1;2;3;4;{value};{intensity};{x:.6f};{y:.6f}')
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return path


def write_readme():
    path = DEMO / 'V4.5.0_表头与智能抓面Demo说明.md'
    path.write_text(
        '# V4.5.0 表头与智能抓面 Demo\n\n'
        '- `V4.5.0_自定义表头兼容_Demo.dat`：参数行、真实自定义表头、空行和注释并存；'
        '导入后应保留 `StagePos1/StagePos2/Thickness_AVG/Intensity`。\n'
        '- `V4.5.0_Precitec_蛇形Bow_Demo.dat`：30×20 蛇形扫描、非等距 X/Y 点距、一个坏 Thickness；'
        '应重建二维索引并以连续曲面模式一次抓取 Bow 面。\n'
        '- `V4.5.0_智能抓面GUI.png`：离屏 GUI 验收截图。\n', encoding='utf-8')
    return path


def make_gui_preview(data_path):
    app = QApplication.instance() or QApplication([])
    window = SurfaceAnalyzerPro()
    window.resize(1600, 900)
    if not window.load_path(data_path):
        raise RuntimeError('Demo data failed to load')
    window.chk_roi_advanced.setChecked(True)
    window.cb_roi_shape.setCurrentIndex(2)
    window._sync_roi_input_state()
    window.add_smart_face_roi_from_seed(1.16, 1.71)
    window.show()
    app.processEvents()
    output = DEMO / 'V4.5.0_智能抓面GUI.png'
    if not window.grab().save(str(output)):
        raise RuntimeError('GUI screenshot could not be saved')
    window.close()
    return output


def main():
    DEMO.mkdir(parents=True, exist_ok=True)
    write_header_demo()
    precitec = write_precitec_demo()
    write_readme()
    make_gui_preview(precitec)


if __name__ == '__main__':
    main()
