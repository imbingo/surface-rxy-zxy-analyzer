# -*- coding: utf-8 -*-
"""
面型及Rxy分析工具 V3.7.0
基于 V1 的修复与增强：
  [优化] V3.7.0 (UI优化·方案A): 整理版左栏 + 顶部结果读数条。仅重排界面，不改动任何算法。
         · 结果指标(平均厚度Z/PV/TTV/Rx/Ry+平面方程)上提为绘图区上方常驻读数条，随分析实时刷新。
         · 左栏工作流改为「1 载入 → 2 列映射 → 3 姿态 → 4 滤波」编号分步，结构与现状一致，迁移成本低。
         · 批量处理按钮紧邻「载入测量数据」；Recipe 导入/导出、大文件策略收进顶部应用栏。
         · 去掉多彩按钮，统一中性灰阶 + 单一强调色 #2F6DB0；删除/危险操作用低饱和红描边。
         · 去倾斜显示开关并入读数条标题行右侧，省掉单独一行，绘图区更高。
  [优化] V3.7.0 (UI优化·方案A收尾): 进一步贴近设计稿，纯界面/样式，不动任何算法。
         · 四视图改为 2×2 四张独立卡片(白底圆角+投影+留缝)，模块感更强。
         · 卡片化投影：顶部读数卡、姿态磁贴、绘图卡均挂柔和投影，悬浮模块质感。
         · 姿态变换改为带线性图标(↻↺⟳⇄↕↔⊕↶)的方块磁贴；去除全部 emoji，按钮统一中性风。
         · 自绘 chevron 替换 Windows 原生下拉/微调箭头；子图标题用 Qt 渲染的「● 标题」，蓝点对齐。
         · 顶部五个结果卡淡底色+加大加粗数值强化读数；PV 卡蓝色高亮。
         · 无边框自绘标题栏：顶栏拖动移动/双击最大化、边缘拉伸缩放、最小化/最大化/关闭按钮。
  [修复] 导出 Z_um 单位错误（原版导出的是 mm 值，列名却是 µm）
  [修复] 旋转/翻转改为基于包围盒(min/max)，坐标不从0开始也不会产生偏移
  [修复] 90°旋转明确为【物料旋转】语义(物料随治具/台面实物转动)，
         而非坐标系旋转/视图旋转。物料顺时针=顶部点转到右侧；物料逆时针=顶部点转到左侧。
  [增强] 新增【去倾斜显示】：绘图与框选可减去最佳拟合平面，直观呈现面型残差(PV)，
         残差显示单位为 µm；仅影响显示与框选，不改变 Rx/Ry/PV/TTV 等指标计算
  [优化] 去倾斜显示控制移到右侧图窗顶部工具条，左侧主控区更紧凑
  [优化] 左侧物料旋转组合去掉长备注，仅保留简洁标题与按钮
  [修复] 文件后缀判断不区分大小写；列名自动清理BOM/空白
  [修复] 框选事件对 x1/y1/x2/y2 全部做 None 判断
  [增强] 鲁棒文件读取: csv/txt/tsv/dat/asc/xyz/xlsx/xls/xlsm，
         自动嗅探分隔符，自动尝试 utf-8/gbk/utf-16/latin-1 编码，
         自动跳过#注释行/空行/坏行，自动识别无表头文件
  [增强] 滤波三模式：关闭 / MAD全局鲁棒 / 局部中位数(邻域比较，阈值=已知面型上限)
  [增强] V3.4: 局部中位数滤波增加【全局残差兜底阈值】，防止成簇/边缘大离群点漏判
  [增强] V3.4: 邻域比较邻居数 k 上限从 50 提高到 200
  [增强] 拟合改为中心化 np.linalg.lstsq，对绝对stage大坐标更稳
  [增强] 有效点<3 禁止拟合；滤波后点数不足自动退回未滤波
  [增强] 多层寄存器记录数据来源文件名；计算前弹窗确认；新增一键清空全部寄存器
  [增强] Gap 计算输出匹配质量报告（RMS/Max/唯一匹配比例）
  [增强] Gap 计算后锁定旧文件映射，防止误点"应用映射"覆盖结果
  [增强] 导出 CSV 带元数据头(变换路径/滤波/删点/拟合系数/Rx/Ry/PV/TTV) + 残差列
  [增强] 变换结果缓存，避免框选时全量重算；选择器重建前断开旧回调
  [增强] V3.5: 大文件导入策略显式化，支持超大TXT/ASC/XYZ按文件位置预抽样导入
  [增强] V3.5: 支持Zeiss类文本中常见缺测值(***、--、NA等)按空值处理
  [增强] V3.5: 导入状态显示文件大小/导入方式/导入点数/显示点数，避免误以为抽样数据是全量数据
  [增强] V3.5: 绘图显示抽样上限可配置，默认最多显示80000点，指标仍按导入后的分析数据计算
  [优化] V3.5.1: 大文件导入/显示策略从左侧移到右侧工具条按钮，弹窗设置，避免左侧拥挤
  [增强] V3.5.1: 新增Recipe导出/导入，保存单位、列映射、物料旋转组合、滤波参数、显示/大文件/Gap设置
  [优化] V3.5.2: Recipe导出/导入按钮移至左侧主控页“导出最终CSV”上方，减轻多层页底部拥挤
  [修复] V3.5.3: 未载入数据时切换去倾斜显示不再触发重绘异常
  [优化] V3.5.3: 局部中位数滤波改为分块近邻查询，降低大点云内存峰值
  [增强] V3.6.0: 新增第4档滤波【迭代σ裁剪】——反复用最佳拟合平面残差的标准差
         裁掉 |残差-均值| > Nσ 的点并重拟合，σ倍数与迭代上限可配置(默认3σ/5次)。
         单调裁剪(只剔不回收)，残差std收敛或无新增剔除即停止。适合“基本是平面+少量毛刺”的工件；
         注意：本档对全局残差判异，面有真实弧度时会削减PV，弧形面建议优先用局部中位数滤波。
  [增强] V3.6.0: 新增【批量处理】——导入时可多选文件，沿用当前界面(或已导入Recipe)的
         列映射/单位/旋转/滤波设置逐个处理；每个文件输出一张包含主页面全部信息(指标+四视图)的
         报告图 result_<原文件名>.png，并汇总生成 result_batch_summary.csv。
         批量仅用自动滤波(无手动框选)，要求所有文件为同一设备、同样列格式。
注意：Rx/Ry 符号约定 (Rx≈+dZ/dY, Ry≈-dZ/dX) 需用已知倾角标准件实测校准一次。
"""
import sys
import os
import re
import mmap
import json
import tempfile
from pathlib import Path
from datetime import datetime
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.widgets import RectangleSelector
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QFileDialog, QLabel,
                             QSplitter, QGroupBox, QGridLayout, QMessageBox,
                             QScrollArea, QComboBox, QTabWidget, QDoubleSpinBox,
                             QSpinBox, QCheckBox, QDialog, QDialogButtonBox,
                             QFrame, QSizePolicy, QGraphicsDropShadowEffect,
                             QSizeGrip)
from PyQt6.QtCore import Qt, QPoint, QPointF, QEvent
from PyQt6.QtGui import QColor, QPixmap, QPainter, QPen
from scipy.spatial import cKDTree


class MultiViewCanvas(QWidget):
    """四视图改为 4 张独立卡片（2×2 网格），每张白底圆角 + 投影 + 顶部「● 标题」，
    模块感更强；标题用 Qt 渲染，蓝点与文字天然对齐。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        # YaHei 同时含中文与 µ(U+00B5)，去倾斜显示的 µm 轴标签也不再出现缺字方块
        plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
        plt.rcParams['axes.unicode_minus'] = False
        plt.rcParams.update({
            'figure.facecolor': '#ffffff', 'axes.facecolor': '#ffffff',
            'axes.edgecolor': '#d8dee4', 'axes.linewidth': 0.8,
            'axes.labelcolor': '#5b6672', 'axes.labelsize': 9,
            'grid.color': '#edf0f3', 'grid.linewidth': 0.7,
            'xtick.color': '#9aa4ae', 'ytick.color': '#9aa4ae',
            'xtick.labelsize': 8, 'ytick.labelsize': 8,
        })
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(12)
        self.ax3d, c3, card3, self.title_3d = self._make_card("3D 原始高度", '3d')
        self.ax_xy, cxy, cardxy, self.title_xy = self._make_card("XY 俯视分布", None)
        self.ax_xz, cxz, cardxz, self.title_xz = self._make_card("X-Z 剖面", None)
        self.ax_yz, cyz, cardyz, self.title_yz = self._make_card("Y-Z 剖面", None)
        self._canvases = [c3, cxy, cxz, cyz]
        grid.addWidget(card3, 0, 0); grid.addWidget(cardxy, 0, 1)
        grid.addWidget(cardxz, 1, 0); grid.addWidget(cardyz, 1, 1)

    def _make_card(self, title, projection):
        card = QFrame(); card.setObjectName("plotCard")
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 9, 10, 8); v.setSpacing(5)
        head = QHBoxLayout(); head.setSpacing(7)
        dot = QLabel(); dot.setObjectName("plotDot"); dot.setFixedSize(8, 8)
        tlabel = QLabel(title); tlabel.setObjectName("plotTitle")
        head.addWidget(dot); head.addWidget(tlabel); head.addStretch()
        v.addLayout(head)
        fig = Figure(constrained_layout=True)
        ax = fig.add_subplot(111, projection=projection)
        canvas = FigureCanvas(fig)
        v.addWidget(canvas, 1)
        eff = QGraphicsDropShadowEffect(card)
        eff.setBlurRadius(20); eff.setXOffset(0); eff.setYOffset(3)
        eff.setColor(QColor(18, 28, 40, 30))
        card.setGraphicsEffect(eff)
        return ax, canvas, card, tlabel

    def set_titles(self, detrended):
        if detrended:
            self.title_3d.setText("3D 去倾斜残差面型"); self.title_xy.setText("XY 俯视分布")
            self.title_xz.setText("X-残差剖面"); self.title_yz.setText("Y-残差剖面")
        else:
            self.title_3d.setText("3D 原始高度"); self.title_xy.setText("XY 俯视分布")
            self.title_xz.setText("X-Z 剖面"); self.title_yz.setText("Y-Z 剖面")

    def draw(self):
        for c in self._canvases:
            c.draw_idle()


class SurfaceAnalyzerPro(QMainWindow):
    DISPLAY_POINT_LIMIT = 80000
    LARGE_TEXT_FILE_BYTES = 512 * 1024 * 1024
    LARGE_TEXT_IMPORT_LIMIT = 500000
    MISSING_TEXT_TOKENS = {'***', '--', 'NA', 'N/A', 'NaN', 'nan', 'null', 'NULL'}

    def __init__(self):
        super().__init__()
        self.setWindowTitle("面型及Rxy分析ZXY版 V3.7.0")
        self.resize(1860, 980)

        # 数据流
        self.absolute_raw_df = None
        self.df_raw = None
        self.manual_mask = None
        self.temp_selected_mask = None
        self.active_idx = None
        self.transform_pipeline = []
        self.current_coeffs = None
        self.current_source_name = "--"   # 当前视图数据来源（文件名 / GAP结果）
        self.n_filtered = 0               # 最近一次滤波剔除点数
        self.last_metrics = None          # 最近一次分析指标（导出元数据用）
        self.display_detrended = False    # 去倾斜显示：只影响绘图/框选，不改变数据与指标
        self.last_import_note = ""        # 最近一次导入说明
        self.last_displayed_points = 0     # 最近一次绘图实际显示点数
        self.import_info = {               # 导入状态：用于UI与导出元数据
            'file_size_bytes': 0,
            'file_size_mb': 0.0,
            'strategy': '--',
            'sampled': False,
            'import_rows': 0,
            'display_limit': self.DISPLAY_POINT_LIMIT,
            'notes': ''
        }
        # V3.5.1: 大文件策略不再占用左侧UI，改为右侧工具条按钮弹窗设置
        self.auto_sample_large_text = True
        self.large_text_threshold_mb = self.LARGE_TEXT_FILE_BYTES // (1024 * 1024)
        self.large_text_import_limit = self.LARGE_TEXT_IMPORT_LIMIT
        self.display_point_limit = self.DISPLAY_POINT_LIMIT
        # Recipe 可在未载入数据前导入；载入文件并完成列填充后自动应用
        self.pending_recipe = None

        # 变换缓存：避免每次框选都全量重算
        self._df_version = 0
        self._trans_cache_key = None
        self._trans_cache_data = None

        # 多层计算寄存器：dict {'x','y','z','name','n'} 或 None
        self.data_base1 = None
        self.data_base2 = None
        self.data_stack = None

        self.selectors = []
        self.init_ui()

    # ================= UI =================
    # V3.7.0 方案A 主题：中性灰阶 + 单一强调色 #2F6DB0，去多彩按钮
    ACCENT = "#2f6db0"

    def _apply_theme(self):
        """全局 QSS：只管视觉，不影响任何逻辑。objectName 驱动强调/危险/卡片样式。"""
        self.setStyleSheet("""
            QMainWindow { background: #eceff3; }
            #centralRoot { background: #eceff3; }
            #appBar { background: #ffffff; border-bottom: 1px solid #e7ebef; }
            #brandDot { background: #2f6db0; border-radius: 5px; }
            #appTitle { color: #1f2933; font-size: 15px; font-weight: bold; }
            #verPill { color: #2f6db0; background: #eaf1f9; border: 1px solid #d6e5f4;
                       border-radius: 9px; padding: 2px 8px; font-size: 11px; font-weight: bold; }
            QTabWidget::pane { border: 1px solid #e7ebef; background: #ffffff; border-radius: 8px; top: -1px; }
            QTabBar::tab { background: #eef1f4; color: #7a858f; padding: 8px 18px;
                           border: 1px solid #e3e7eb; border-bottom: none;
                           border-top-left-radius: 7px; border-top-right-radius: 7px; font-weight: bold; }
            QTabBar::tab:selected { background: #ffffff; color: #1f2933; }
            QScrollArea { border: none; background: #ffffff; }
            QGroupBox { border: 1px solid #eef1f4; border-radius: 8px; margin-top: 8px;
                        background: #ffffff; font-weight: bold; color: #46505a; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
            QLabel { color: #2b333c; }
            QPushButton { background: #ffffff; border: 1px solid #dde2e7; border-radius: 7px;
                          padding: 7px 12px; color: #46505a; }
            QPushButton:hover { border-color: #2f6db0; color: #2f6db0; }
            QPushButton:disabled { color: #b6bdc4; border-color: #e9edf1; }
            QComboBox, QSpinBox, QDoubleSpinBox { border: 1px solid #d9dee3; border-radius: 6px;
                          padding: 4px 8px; background: #ffffff; color: #2b333c; min-height: 22px; }
            QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled { color: #b6bdc4; background: #f6f8fa; }
            #accentBtn { background: #2f6db0; border: none; color: #ffffff; font-weight: bold; }
            #accentBtn:hover { background: #285f9a; color: #ffffff; }
            #accentBtn:disabled { background: #aebfd4; }
            #accentSoftBtn { background: #eaf1f9; border: 1px solid #d2e2f3; color: #2f6db0; font-weight: bold; }
            #accentSoftBtn:hover { background: #dceaf7; color: #2f6db0; }
            #dangerBtn { background: #ffffff; border: 1px solid #ecc9c6; color: #b4453a; font-weight: bold; }
            #dangerBtn:hover { background: #fbf0ef; color: #b4453a; }
            #stepBadge { background: #eef2f6; color: #5b6672; border-radius: 5px; font-weight: bold; font-size: 11px; }
            #stepBadgeActive { background: #2f6db0; color: #ffffff; border-radius: 5px; font-weight: bold; font-size: 11px; }
            #stepTitle { color: #2b333c; font-weight: bold; font-size: 12px; }
            #stepLine { background: #eef1f4; }
            #resultsStrip { background: transparent; border: none; }
            #stripCaption { color: #5b6672; font-weight: bold; font-size: 11px; letter-spacing: 1px; }
            #stripEqn { color: #9aa4ae; font-family: 'Consolas','Microsoft YaHei'; font-size: 11px; }
            #metricCard { background: #f7f9fc; border: 1px solid #e6ebf1; border-radius: 12px; }
            #metricCardAccent { background: #eaf3fd; border: 1px solid #cbe0f4; border-radius: 12px; }
            #metricTitle { color: #7e8893; font-size: 10px; font-weight: bold; }
            #metricTitleAccent { color: #2f6db0; font-size: 10px; font-weight: bold; }
            #metricValue { color: #14202c; font-family: 'Consolas','Microsoft YaHei'; font-size: 20px; font-weight: bold; }
            #metricValueAccent { color: #2f6db0; font-family: 'Consolas','Microsoft YaHei'; font-size: 20px; font-weight: bold; }
            #metricUnit { color: #aab2ba; font-size: 10px; }
            #plotCard { background: #ffffff; border: 1px solid #e9edf1; border-radius: 12px; }
            #plotDot { background: #2f6db0; border-radius: 4px; }
            #plotTitle { color: #2b333c; font-size: 12px; font-weight: bold; }
            QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: center right;
                                   width: 20px; border: none; }
            QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: border;
                                    subcontrol-position: top right; width: 16px; border: none; }
            QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border;
                                    subcontrol-position: bottom right; width: 16px; border: none; }
            #mutedNote { color: #9aa4ae; font-size: 11px; }
            #pipelineNote { color: #6b7682; font-family: 'Consolas','Microsoft YaHei'; font-size: 11px;
                            background: #f6f8fa; border: 1px solid #eef1f4; border-radius: 6px; padding: 5px 8px; }
            #sourceNote { color: #3a444e; font-size: 12px; font-weight: bold;
                          background: #f6f8fa; border: 1px solid #e9edf1; border-radius: 6px; padding: 6px 9px; }
            #poseTile { background: #ffffff; border: 1px solid #eaeef2; border-radius: 11px; }
            #poseTile:hover { border-color: #2f6db0; background: #f6faff; }
            #poseTileActive { background: #eaf1f9; border: 1px solid #cfe0f1; border-radius: 11px; }
            #poseTileActive:hover { background: #dceaf7; }
            #poseIcon { font-size: 16px; color: #55606b; background: transparent; }
            #poseLabel { font-size: 10px; color: #6b7682; background: transparent; }
            #poseTileActive #poseIcon { color: #2f6db0; }
            #poseTileActive #poseLabel { color: #2f6db0; }
            #winBtn { background: transparent; border: none; border-radius: 6px;
                      color: #5b6672; font-size: 14px; padding: 0; }
            #winBtn:hover { background: #eef2f6; color: #1f2933; }
            #winClose { background: transparent; border: none; border-radius: 6px;
                        color: #5b6672; font-size: 13px; padding: 0; }
            #winClose:hover { background: #e15b4d; color: #ffffff; }
        """)
        # 用自绘 chevron 图标替换 Windows 原生下拉/微调箭头，统一为细线 V 形
        down = self._make_arrow_png('down')
        up = self._make_arrow_png('up')
        if down and up:
            self.setStyleSheet(self.styleSheet() + (
                "QComboBox::down-arrow { image: url(%s); width: 11px; height: 11px; margin-right: 5px; }"
                "QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url(%s); width: 9px; height: 9px; }"
                "QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url(%s); width: 9px; height: 9px; }"
            ) % (down, up, down))

    @staticmethod
    def _make_arrow_png(direction='down', color='#8a949e', size=22):
        """生成一个细线 chevron(V形) PNG 并返回正斜杠路径，供 QSS image:url() 用。"""
        try:
            pm = QPixmap(size, size)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            pen = QPen(QColor(color))
            pen.setWidthF(size * 0.11)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            pad = size * 0.30
            cx = size / 2.0
            if direction == 'down':
                a, b, c = QPointF(pad, size * 0.40), QPointF(cx, size * 0.62), QPointF(size - pad, size * 0.40)
            else:
                a, b, c = QPointF(pad, size * 0.60), QPointF(cx, size * 0.38), QPointF(size - pad, size * 0.60)
            p.drawLine(a, b)
            p.drawLine(b, c)
            p.end()
            path = os.path.join(tempfile.gettempdir(), f'sra_chevron_{direction}.png')
            pm.save(path)
            return path.replace('\\', '/')
        except Exception:
            return None

    def _add_shadow(self, widget, blur=22, dy=4, alpha=38):
        """给卡片挂柔和投影，营造模块化悬浮质感（理想车机 / Apple 风）。
        每个 widget 只能有一个 graphics effect，且父子不要同时挂以免重复渲染。"""
        eff = QGraphicsDropShadowEffect(widget)
        eff.setBlurRadius(blur)
        eff.setXOffset(0)
        eff.setYOffset(dy)
        eff.setColor(QColor(18, 28, 40, alpha))
        widget.setGraphicsEffect(eff)
        return widget

    def init_ui(self):
        self._apply_theme()
        # 无边框自绘标题栏：顶部应用栏兼任标题栏（拖动/双击最大化），状态栏带缩放手柄
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setMinimumSize(1180, 700)
        central = QWidget()
        central.setObjectName("centralRoot")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---------- 顶部应用栏：标题 + 版本 + Recipe / 大文件策略 ----------
        appbar = QWidget()
        appbar.setObjectName("appBar")
        appbar.setFixedHeight(50)
        ab = QHBoxLayout(appbar)
        ab.setContentsMargins(16, 0, 14, 0)
        ab.setSpacing(9)
        dot = QLabel(); dot.setObjectName("brandDot"); dot.setFixedSize(10, 10)
        app_title = QLabel("面型及 Rxy 分析"); app_title.setObjectName("appTitle")
        ver_pill = QLabel("V3.7.0"); ver_pill.setObjectName("verPill")
        ab.addWidget(dot); ab.addWidget(app_title); ab.addWidget(ver_pill)
        ab.addStretch()

        self.btn_import_recipe = QPushButton("导入 Recipe")
        self.btn_import_recipe.setToolTip("读取Recipe并自动写入当前UI参数；若尚未载入数据，列映射会在下次载入后自动应用。")
        self.btn_import_recipe.clicked.connect(self.import_recipe)
        self.btn_export_recipe = QPushButton("导出 Recipe")
        self.btn_export_recipe.setToolTip("保存当前单位、列映射、物料旋转组合、滤波、大文件显示和Gap参数。")
        self.btn_export_recipe.clicked.connect(self.export_recipe)
        self.btn_bigfile_settings = QPushButton("大文件策略")
        self.btn_bigfile_settings.setToolTip("设置超大TXT预抽样、导入上限、绘图显示上限。")
        self.btn_bigfile_settings.clicked.connect(self.show_bigfile_settings_dialog)
        for b in (self.btn_import_recipe, self.btn_export_recipe, self.btn_bigfile_settings):
            b.setFixedHeight(30)
            ab.addWidget(b)

        # 窗口控制按钮（最小化 / 最大化-还原 / 关闭）
        ab.addSpacing(8)
        self.btn_win_min = QPushButton("–"); self.btn_win_min.setObjectName("winBtn")
        self.btn_win_max = QPushButton("□"); self.btn_win_max.setObjectName("winBtn")
        self.btn_win_close = QPushButton("✕"); self.btn_win_close.setObjectName("winClose")
        for b in (self.btn_win_min, self.btn_win_max, self.btn_win_close):
            b.setFixedSize(36, 28)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            ab.addWidget(b)
        self.btn_win_min.clicked.connect(self.showMinimized)
        self.btn_win_max.clicked.connect(self._toggle_max_restore)
        self.btn_win_close.clicked.connect(self.close)

        self._appbar = appbar
        appbar.installEventFilter(self)
        root.addWidget(appbar)

        # 隐藏状态标签：仅供内部更新/写入按钮 tooltip 与状态栏，不占UI
        self.lbl_import_status = QLabel("导入状态: --")
        self.lbl_import_status.setVisible(False)

        # ---------- 主体：左控制面板 | 右（结果条 + 工具条 + 四视图）----------
        body = QWidget()
        self._body = body
        body.setMouseTracking(True)
        body.installEventFilter(self)
        body_l = QHBoxLayout(body)
        body_l.setContentsMargins(12, 12, 12, 12)
        body_l.setSpacing(12)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.tabs = QTabWidget()
        self.tabs.setFixedWidth(440)
        tab_main = QWidget()
        self.setup_main_tab(tab_main)
        self.tabs.addTab(tab_main, "单层 / 主控分析")
        tab_math = QWidget()
        self.setup_math_tab(tab_math)
        self.tabs.addTab(tab_math, "多层胶厚扣减")

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)
        right_layout.addWidget(self._build_results_strip())

        self.canvas = MultiViewCanvas(self)
        right_layout.addWidget(self.canvas, 1)

        splitter.addWidget(self.tabs)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(1, 4)
        body_l.addWidget(splitter)
        root.addWidget(body, 1)

        # 无边框窗口：用状态栏右下角缩放手柄实现拖拽缩放
        self.statusBar().setSizeGripEnabled(True)

    def _toggle_max_restore(self):
        if self.isMaximized():
            self.showNormal()
            self.btn_win_max.setText("□")
        else:
            self.showMaximized()
            self.btn_win_max.setText("❐")

    def _resize_edge_at(self, gpos):
        """根据全局光标位置判断是否贴近窗口边缘，返回组合 Qt.Edge 或 None。"""
        if self.isMaximized():
            return None
        p = self.mapFromGlobal(gpos)
        r = self.rect()
        m = 6
        parts = []
        if p.x() <= m:
            parts.append(Qt.Edge.LeftEdge)
        elif p.x() >= r.width() - m:
            parts.append(Qt.Edge.RightEdge)
        if p.y() <= m:
            parts.append(Qt.Edge.TopEdge)
        elif p.y() >= r.height() - m:
            parts.append(Qt.Edge.BottomEdge)
        if not parts:
            return None
        edges = parts[0]
        for x in parts[1:]:
            edges |= x
        return edges

    @staticmethod
    def _cursor_for_edges(edges):
        L, R = Qt.Edge.LeftEdge, Qt.Edge.RightEdge
        T, B = Qt.Edge.TopEdge, Qt.Edge.BottomEdge
        if (bool(edges & L) and bool(edges & T)) or (bool(edges & R) and bool(edges & B)):
            return Qt.CursorShape.SizeFDiagCursor
        if (bool(edges & R) and bool(edges & T)) or (bool(edges & L) and bool(edges & B)):
            return Qt.CursorShape.SizeBDiagCursor
        if bool(edges & L) or bool(edges & R):
            return Qt.CursorShape.SizeHorCursor
        return Qt.CursorShape.SizeVerCursor

    def eventFilter(self, obj, event):
        """应用栏兼任标题栏：左键拖动移动窗口、双击最大化/还原；
        主体外缘 6px 作为缩放边，左键按下触发系统缩放（无边框窗口的边缘拉伸）。"""
        if obj is getattr(self, '_appbar', None):
            et = event.type()
            if et == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                wh = self.windowHandle()
                if wh is not None:
                    wh.startSystemMove()
                    return True
            elif et == QEvent.Type.MouseButtonDblClick:
                self._toggle_max_restore()
                return True
        elif obj is getattr(self, '_body', None):
            et = event.type()
            if et == QEvent.Type.MouseMove and not (event.buttons() & Qt.MouseButton.LeftButton):
                edges = self._resize_edge_at(event.globalPosition().toPoint())
                obj.setCursor(self._cursor_for_edges(edges) if edges else Qt.CursorShape.ArrowCursor)
            elif et == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                edges = self._resize_edge_at(event.globalPosition().toPoint())
                if edges is not None:
                    wh = self.windowHandle()
                    if wh is not None:
                        wh.startSystemResize(edges)
                        return True
        return super().eventFilter(obj, event)

    # ---------- 顶部结果读数条（方案A 核心：指标常驻可见）----------
    def _make_metric_card(self, title, value_label, accent=False):
        card = QFrame()
        card.setObjectName("metricCardAccent" if accent else "metricCard")
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 9, 12, 9)
        v.setSpacing(4)
        t = QLabel(title)
        t.setObjectName("metricTitleAccent" if accent else "metricTitle")
        value_label.setObjectName("metricValueAccent" if accent else "metricValue")
        v.addWidget(t)
        v.addWidget(value_label)
        self._add_shadow(card, blur=20, dy=3, alpha=34)
        return card

    def _build_results_strip(self):
        strip = QFrame()
        strip.setObjectName("resultsStrip")
        outer = QVBoxLayout(strip)
        outer.setContentsMargins(6, 2, 6, 8)
        outer.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        cap = QLabel("实时分析结果")
        cap.setObjectName("stripCaption")
        self.lbl_eqn = QLabel("--")
        self.lbl_eqn.setObjectName("stripEqn")
        head.addWidget(cap)
        head.addWidget(self.lbl_eqn)
        head.addStretch()
        # 去倾斜显示开关并入标题行右侧，省掉单独一行（绘图区垂直空间紧张）
        self.chk_detrend_display = QCheckBox("去倾斜显示")
        self.chk_detrend_display.setToolTip(
            "开启后，3D/XZ/YZ图中的Z轴改为：实测Z - 当前最佳拟合平面，单位 µm。\n"
            "用于更清晰观察物料表面面型起伏；只影响显示和框选，不改变Rx/Ry/PV/TTV计算，也不修改原始数据。")
        self.chk_detrend_display.stateChanged.connect(self._on_detrend_display_changed)
        self.lbl_detrend_info = QLabel("原始Z高度 mm")
        self.lbl_detrend_info.setObjectName("mutedNote")
        head.addWidget(self.chk_detrend_display)
        head.addWidget(self.lbl_detrend_info)
        outer.addLayout(head)

        cards = QHBoxLayout()
        cards.setSpacing(13)
        cards.setContentsMargins(4, 2, 4, 4)
        self.lbl_z = QLabel("--")
        self.lbl_pv = QLabel("--")
        self.lbl_ttv = QLabel("--")
        self.lbl_rx = QLabel("--")
        self.lbl_ry = QLabel("--")
        cards.addWidget(self._make_metric_card("平均厚度 Z (mm)", self.lbl_z), 1)
        cards.addWidget(self._make_metric_card("面型 PV·法向 (µm)", self.lbl_pv, accent=True), 1)
        cards.addWidget(self._make_metric_card("TTV·Z极差 (µm)", self.lbl_ttv), 1)
        cards.addWidget(self._make_metric_card("物料 Rx (µrad)", self.lbl_rx), 1)
        cards.addWidget(self._make_metric_card("物料 Ry (µrad)", self.lbl_ry), 1)
        outer.addLayout(cards)
        return strip

    def _step_header(self, num, text, hint=None, active=False):
        """编号分步标题：序号徽章 + 标题 + 可选提示 + 分隔线。"""
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        badge = QLabel(str(num))
        badge.setObjectName("stepBadgeActive" if active else "stepBadge")
        badge.setFixedSize(20, 20)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel(text)
        title.setObjectName("stepTitle")
        h.addWidget(badge)
        h.addWidget(title)
        if hint:
            hint_lbl = QLabel(hint)
            hint_lbl.setObjectName("mutedNote")
            h.addWidget(hint_lbl)
        line = QWidget()
        line.setObjectName("stepLine")
        line.setFixedHeight(1)
        h.addWidget(line, 1)
        return row

    def _pose_tile(self, icon, label, slot, active=False, tooltip=None):
        """姿态变换磁贴：线性图标在上、文字在下，方块卡片样式（对齐方案A）。"""
        btn = QPushButton()
        btn.setObjectName("poseTileActive" if active else "poseTile")
        btn.setFixedHeight(52)
        btn.clicked.connect(slot)
        if tooltip:
            btn.setToolTip(tooltip)
        v = QVBoxLayout(btn)
        v.setContentsMargins(2, 5, 2, 5)
        v.setSpacing(1)
        ic = QLabel(icon); ic.setObjectName("poseIcon")
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tx = QLabel(label); tx.setObjectName("poseLabel")
        tx.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for lab in (ic, tx):
            lab.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        v.addWidget(ic); v.addWidget(tx)
        self._add_shadow(btn, blur=14, dy=2, alpha=26)
        return btn

    def setup_main_tab(self, parent_widget):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        w = QWidget()
        ll = QVBoxLayout(w)
        ll.setContentsMargins(14, 12, 14, 12)
        ll.setSpacing(9)

        # ---------- 1. 载入数据（批量处理紧邻）----------
        ll.addWidget(self._step_header(1, "载入数据", active=True))
        file_layout = QHBoxLayout()
        file_layout.setSpacing(8)
        self.btn_open = QPushButton("载入测量数据")
        self.btn_open.setObjectName("accentBtn")
        self.btn_open.setFixedHeight(38)
        self.btn_open.clicked.connect(self.load_file)
        self.btn_reset_all = QPushButton("重置")
        self.btn_reset_all.setFixedHeight(38)
        self.btn_reset_all.setFixedWidth(92)
        self.btn_reset_all.clicked.connect(self.reset_all)
        file_layout.addWidget(self.btn_open)
        file_layout.addWidget(self.btn_reset_all)
        ll.addLayout(file_layout)

        self.lbl_source = QLabel("当前数据: 未载入")
        self.lbl_source.setObjectName("sourceNote")
        self.lbl_source.setWordWrap(True)
        ll.addWidget(self.lbl_source)

        self.btn_batch = QPushButton("批量处理 · 多选文件 → 每个出报告图")
        self.btn_batch.setFixedHeight(34)
        self.btn_batch.setToolTip(
            "导入时可多选文件，沿用当前界面(或已导入Recipe)的列映射/单位/旋转/滤波设置逐个处理。\n"
            "每个文件输出一张含主页面全部信息(指标+四视图)的报告图 result_<原文件名>.png，\n"
            "并汇总生成 result_batch_summary.csv。要求所有文件为同一设备、同样列格式。\n"
            "建议先载入其中一个文件调好参数(或导入Recipe)，再点此批量处理。")
        self.btn_batch.clicked.connect(self.batch_process)
        ll.addWidget(self.btn_batch)

        # ---------- 2. 列映射与单位 ----------
        ll.addWidget(self._step_header(2, "列映射与单位"))
        map_group = QGroupBox()
        map_group.setFlat(True)
        ml = QGridLayout(map_group)
        ml.setContentsMargins(2, 2, 2, 2)
        ml.addWidget(QLabel("X列:"), 0, 0); self.cb_x_col = QComboBox(); ml.addWidget(self.cb_x_col, 0, 1)
        ml.addWidget(QLabel("原单位:"), 0, 2); self.cb_x_unit = QComboBox(); self.cb_x_unit.addItems(["mm", "µm"]); ml.addWidget(self.cb_x_unit, 0, 3)
        ml.addWidget(QLabel("Y列:"), 1, 0); self.cb_y_col = QComboBox(); ml.addWidget(self.cb_y_col, 1, 1)
        ml.addWidget(QLabel("原单位:"), 1, 2); self.cb_y_unit = QComboBox(); self.cb_y_unit.addItems(["mm", "µm"]); ml.addWidget(self.cb_y_unit, 1, 3)
        ml.addWidget(QLabel("Z列:"), 2, 0); self.cb_z_col = QComboBox(); ml.addWidget(self.cb_z_col, 2, 1)
        ml.addWidget(QLabel("原单位:"), 2, 2); self.cb_z_unit = QComboBox(); self.cb_z_unit.addItems(["mm", "µm", "nm"]); ml.addWidget(self.cb_z_unit, 2, 3)
        self.cb_z_unit.setCurrentText("µm")
        self.btn_apply_map = QPushButton("应用映射并解析数据")
        self.btn_apply_map.setObjectName("accentSoftBtn")
        self.btn_apply_map.setFixedHeight(33)
        self.btn_apply_map.clicked.connect(self.apply_mapping)
        ml.addWidget(self.btn_apply_map, 3, 0, 1, 4)
        ll.addWidget(map_group)

        # ---------- 3. 姿态变换 ----------
        ll.addWidget(self._step_header(3, "姿态变换", hint="点击叠加"))
        trans_group = QGroupBox()
        trans_group.setFlat(True)
        tl = QVBoxLayout(trans_group)
        tl.setContentsMargins(2, 2, 2, 2)
        grid_trans = QGridLayout()
        grid_trans.setSpacing(6)
        # (图标, 文字, 槽, 是否高亮, tooltip) —— 4列磁贴
        tiles = [
            ("↻", "顺时针90°", self.add_cw90, False, "物料顺时针旋转90°：顶部点转到右侧"),
            ("↺", "逆时针90°", self.add_ccw90, False, "物料逆时针旋转90°：顶部点转到左侧"),
            ("⟳", "旋转180°", self.add_rot180, False, "物料旋转180°"),
            ("⇄", "X-Y对调", self.add_swap, False, "X、Y 轴对调"),
            ("↕", "前后翻转", self.add_flipx, False, "X轴翻转(前后) = Y 镜像"),
            ("↔", "左右翻转", self.add_flipy, False, "Y轴翻转(左右) = X 镜像"),
            ("⊕", "平移归零", self.add_origin, True, "平移归零(0,0)：X,Y 包围盒原点对齐"),
            ("↶", "撤销", self.undo_transform, False, "撤销上一步姿态变换"),
        ]
        for i, (icon, name, func, active, tip) in enumerate(tiles):
            grid_trans.addWidget(self._pose_tile(icon, name, func, active, tip), i // 4, i % 4)

        tl.addLayout(grid_trans)
        self.lbl_pipeline = QLabel("变换路径: 原始状态")
        self.lbl_pipeline.setObjectName("pipelineNote")
        self.lbl_pipeline.setWordWrap(True)
        tl.addWidget(self.lbl_pipeline)
        ll.addWidget(trans_group)

        # ---------- 4. 异常点滤波 ----------
        ll.addWidget(self._step_header(4, "异常点滤波"))
        flt_group = QGroupBox()
        flt_group.setFlat(True)
        fl = QGridLayout(flt_group)
        fl.setContentsMargins(2, 2, 2, 2)
        fl.addWidget(QLabel("模式:"), 0, 0)
        self.cb_filter = QComboBox()
        self.cb_filter.addItems(["关闭", "MAD 全局鲁棒滤波", "局部中位数滤波 (邻域比较)",
                                 "迭代σ裁剪 (残差±Nσ重拟合)"])
        self.cb_filter.setToolTip(
            "MAD全局: 对拟合残差做鲁棒3.5σ判定，适合零散毛刺。\n"
            "局部中位数: 每个点与其 k 个最近邻的残差中位数比较，偏离超过阈值判为异常。\n"
            "  同时启用全局残差兜底：点相对全局残差中位数超过同一阈值也会被剔除。\n"
            "  适合已知面型上限的场景（如已知面型≤5µm 则阈值设5）。\n"
            "  单个离群点不会误杀周围正常点；成簇/边缘大离群点由全局兜底拦截。\n"
            "迭代σ裁剪: 反复用最佳拟合平面残差的标准差σ裁掉 |残差-均值|>Nσ 的点并重拟合，\n"
            "  直到残差std收敛或无新增剔除(单调裁剪，只剔不回收)。\n"
            "  适合“基本是平面+少量毛刺”的工件；面有真实弧度时会削减PV，弧形面请优先用局部中位数。")
        fl.addWidget(self.cb_filter, 0, 1, 1, 3)
        self.lbl_k = QLabel("邻居数 k:")
        fl.addWidget(self.lbl_k, 1, 0)
        self.spin_k = QSpinBox(); self.spin_k.setRange(3, 200); self.spin_k.setValue(12)
        self.spin_k.setToolTip("邻域比较的最近邻数量，范围 3~200；常用 8~20，点云很密或坏点成片时可适当调大")
        fl.addWidget(self.spin_k, 1, 1)
        self.lbl_thresh = QLabel("阈值 (µm):")
        fl.addWidget(self.lbl_thresh, 1, 2)
        self.spin_thresh = QDoubleSpinBox()
        self.spin_thresh.setDecimals(2); self.spin_thresh.setRange(0.01, 10000.0)
        self.spin_thresh.setValue(5.00); self.spin_thresh.setSingleStep(0.5)
        self.spin_thresh.setToolTip("局部中位数模式的判异阈值，同时作为全局残差兜底阈值；建议设为已知面型/噪声上限")
        fl.addWidget(self.spin_thresh, 1, 3)
        self.lbl_sigma = QLabel("σ倍数 N:")
        fl.addWidget(self.lbl_sigma, 2, 0)
        self.spin_sigma = QDoubleSpinBox()
        self.spin_sigma.setDecimals(1); self.spin_sigma.setRange(1.0, 6.0)
        self.spin_sigma.setValue(3.0); self.spin_sigma.setSingleStep(0.5)
        self.spin_sigma.setToolTip("迭代σ裁剪的σ倍数，常用 3.0；越小裁剪越激进")
        fl.addWidget(self.spin_sigma, 2, 1)
        self.lbl_sigma_iter = QLabel("迭代上限:")
        fl.addWidget(self.lbl_sigma_iter, 2, 2)
        self.spin_sigma_iter = QSpinBox()
        self.spin_sigma_iter.setRange(1, 20); self.spin_sigma_iter.setValue(5)
        self.spin_sigma_iter.setToolTip("迭代σ裁剪的最大轮数，达到收敛会提前停止；常用 3~8")
        fl.addWidget(self.spin_sigma_iter, 2, 3)
        self.lbl_filter_info = QLabel("滤波剔除: 0 点 | 手动删除: 0 点")
        self.lbl_filter_info.setObjectName("mutedNote")
        fl.addWidget(self.lbl_filter_info, 3, 0, 1, 4)
        self.cb_filter.currentIndexChanged.connect(self._on_filter_mode_changed)
        self.spin_k.valueChanged.connect(self._on_filter_param_changed)
        self.spin_thresh.valueChanged.connect(self._on_filter_param_changed)
        self.spin_sigma.valueChanged.connect(self._on_filter_param_changed)
        self.spin_sigma_iter.valueChanged.connect(self._on_filter_param_changed)
        self._sync_filter_enabled()
        ll.addWidget(flt_group)

        ll.addStretch()

        # ---------- 底部操作：导出CSV(主) + 删除框选(危险) ----------
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.btn_save = QPushButton("导出最终 CSV")
        self.btn_save.setObjectName("accentBtn")
        self.btn_save.setFixedHeight(38)
        self.btn_save.clicked.connect(self.save_file)
        self.btn_del = QPushButton("删除已框选点")
        self.btn_del.setObjectName("dangerBtn")
        self.btn_del.setFixedHeight(38)
        self.btn_del.setFixedWidth(132)
        self.btn_del.clicked.connect(self.apply_manual_deletion)
        action_row.addWidget(self.btn_save)
        action_row.addWidget(self.btn_del)
        ll.addLayout(action_row)

        scroll.setWidget(w)
        parent_widget.setLayout(QVBoxLayout())
        parent_widget.layout().addWidget(scroll)
        parent_widget.layout().setContentsMargins(0, 0, 0, 0)

    def setup_math_tab(self, parent_widget):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        w = QWidget()
        ll = QVBoxLayout(w)

        guide_lbl = QLabel(
            "<div style='line-height: 1.5; font-size: 12px; color: #333;'>"
            "<b>🥪 堆叠胶厚(Gap)运算流程：</b><br>"
            "公式：<span style='color:red; font-weight:bold;'>Inner Gap = 堆叠总成 - 单片1 [- 单片2]</span><br>"
            "1. 务必确保所有数据在载入后，都点击了<b>[📍 平移归零]</b>，使X,Y坐标网格原点对齐。<br>"
            "2. 依次载入不同层数据并存入下方对应的寄存器中（寄存器会显示来源文件名，请核对）。<br>"
            "3. 【对齐误差窗口】：用于补偿机台定位偏差。容差越大，匹配点越多，但过大可能匹配到错误邻居。"
            "</div>"
        )
        guide_lbl.setWordWrap(True)
        ll.addWidget(guide_lbl)

        grp_stack = QGroupBox("1️⃣ 堆叠总成数据 (Stack / 顶层)")
        gl_stack = QVBoxLayout(grp_stack)
        self.lbl_stack_status = QLabel("❌ 尚未设置"); self.lbl_stack_status.setStyleSheet("color: #c0392b; font-weight: bold;")
        self.lbl_stack_status.setWordWrap(True)
        btn_set_stack = QPushButton("👆 将当前视图设为【堆叠总成】"); btn_set_stack.setFixedHeight(35)
        btn_set_stack.setObjectName("accentSoftBtn")
        btn_set_stack.clicked.connect(lambda: self.set_memory_slot('stack'))
        gl_stack.addWidget(self.lbl_stack_status); gl_stack.addWidget(btn_set_stack)
        ll.addWidget(grp_stack)

        grp_base1 = QGroupBox("2️⃣ 单片 1 数据 (Base 1 / 底层)")
        gl_base1 = QVBoxLayout(grp_base1)
        self.lbl_base1_status = QLabel("❌ 尚未设置"); self.lbl_base1_status.setStyleSheet("color: #c0392b; font-weight: bold;")
        self.lbl_base1_status.setWordWrap(True)
        btn_set_base1 = QPushButton("👇 将当前视图设为【单片 1】"); btn_set_base1.setFixedHeight(35)
        btn_set_base1.setObjectName("accentSoftBtn")
        btn_set_base1.clicked.connect(lambda: self.set_memory_slot('base1'))
        gl_base1.addWidget(self.lbl_base1_status); gl_base1.addWidget(btn_set_base1)
        ll.addWidget(grp_base1)

        grp_base2 = QGroupBox("3️⃣ 单片 2 数据 (Base 2 / 夹层) [选填]")
        gl_base2 = QVBoxLayout(grp_base2)
        self.lbl_base2_status = QLabel("⭕ 可选空置"); self.lbl_base2_status.setStyleSheet("color: #7f8c8d; font-weight: bold;")
        self.lbl_base2_status.setWordWrap(True)
        hl_b2 = QHBoxLayout()
        btn_set_base2 = QPushButton("📥 设为【单片 2】"); btn_set_base2.setFixedHeight(35); btn_set_base2.clicked.connect(lambda: self.set_memory_slot('base2'))
        btn_clear_base2 = QPushButton("✖ 清除"); btn_clear_base2.setFixedHeight(35)
        btn_clear_base2.clicked.connect(lambda: self.clear_memory_slot('base2'))
        hl_b2.addWidget(btn_set_base2); hl_b2.addWidget(btn_clear_base2)
        gl_base2.addWidget(self.lbl_base2_status); gl_base2.addLayout(hl_b2)
        ll.addWidget(grp_base2)

        btn_clear_all = QPushButton("🧹 清空全部寄存器")
        btn_clear_all.setFixedHeight(35)
        btn_clear_all.clicked.connect(self.clear_all_memory_slots)
        ll.addWidget(btn_clear_all)

        ll.addStretch()

        hl_tol = QHBoxLayout()
        lbl_tol = QLabel("⚙️ 坐标对齐误差窗口 (mm):")
        lbl_tol.setStyleSheet("font-weight: bold; color: #2c3e50;")
        hl_tol.addWidget(lbl_tol)

        self.spin_tol = QDoubleSpinBox()
        self.spin_tol.setDecimals(3)
        self.spin_tol.setRange(0.001, 10.000)
        self.spin_tol.setSingleStep(0.01)
        self.spin_tol.setValue(0.050)
        self.spin_tol.setFixedHeight(35)
        hl_tol.addWidget(self.spin_tol)
        ll.addLayout(hl_tol)

        action_row = QHBoxLayout()
        self.btn_calc_gap = QPushButton("📐 容差匹配点云并计算胶厚")
        self.btn_calc_gap.setObjectName("accentBtn")
        self.btn_calc_gap.setFixedHeight(60)
        self.btn_calc_gap.clicked.connect(self.calculate_gap)
        action_row.addWidget(self.btn_calc_gap, 1)
        ll.addLayout(action_row)

        scroll.setWidget(w)
        parent_widget.setLayout(QVBoxLayout())
        parent_widget.layout().addWidget(scroll)
        parent_widget.layout().setContentsMargins(0, 0, 0, 0)

    # ================= Recipe 导入 / 导出 =================
    def _current_recipe_dict(self):
        """导出当前界面参数，不包含测量数据本身。"""
        return {
            'recipe_type': 'SurfaceRxyZxyAnalyzerRecipe',
            'app_version': 'V3.7.0',
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'column_mapping': {
                'x_col': self.cb_x_col.currentText() if hasattr(self, 'cb_x_col') else '',
                'y_col': self.cb_y_col.currentText() if hasattr(self, 'cb_y_col') else '',
                'z_col': self.cb_z_col.currentText() if hasattr(self, 'cb_z_col') else '',
            },
            'units': {'x_unit': self.cb_x_unit.currentText(), 'y_unit': self.cb_y_unit.currentText(), 'z_unit': self.cb_z_unit.currentText()},
            'transform_pipeline': list(self.transform_pipeline),
            'filter': {'mode_index': int(self.cb_filter.currentIndex()), 'mode_text': self.cb_filter.currentText(), 'neighbor_k': int(self.spin_k.value()), 'threshold_um': float(self.spin_thresh.value()), 'sigma_k': float(self.spin_sigma.value()), 'sigma_iters': int(self.spin_sigma_iter.value())},
            'display': {'detrended': bool(self.display_detrended)},
            'large_file': {'auto_sample': bool(self.auto_sample_large_text), 'threshold_mb': int(self.large_text_threshold_mb), 'import_limit': int(self.large_text_import_limit), 'display_limit': int(self.display_point_limit)},
            'gap': {'tolerance_mm': float(self.spin_tol.value()) if hasattr(self, 'spin_tol') else 0.05},
        }

    @staticmethod
    def _safe_set_combo_text(combo, text):
        if combo is None or text in (None, ''):
            return False
        idx = combo.findText(str(text))
        if idx >= 0:
            combo.setCurrentIndex(idx)
            return True
        return False

    def export_recipe(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出Recipe", "Surface_Rxy_ZXY.recipe.json", "Recipe JSON (*.json);;All Files (*)")
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self._current_recipe_dict(), f, ensure_ascii=False, indent=2)
            self.statusBar().showMessage(f"Recipe已导出: {path}", 5000)
            QMessageBox.information(self, "Recipe导出成功", "已保存当前单位、列映射、物料旋转组合、滤波参数、大文件显示策略和Gap容差。")
        except Exception as e:
            QMessageBox.critical(self, "Recipe导出失败", str(e))

    def import_recipe(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入Recipe", "", "Recipe JSON (*.json);;All Files (*)")
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                recipe = json.load(f)
            if recipe.get('recipe_type') not in (None, 'SurfaceRxyZxyAnalyzerRecipe'):
                ret = QMessageBox.question(self, "Recipe类型不一致", "该JSON不一定是Surface Rxy ZXY Analyzer Recipe，是否仍尝试导入？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
                if ret != QMessageBox.StandardButton.Yes:
                    return
            self.apply_recipe(recipe, path_hint=Path(path).name)
        except Exception as e:
            QMessageBox.critical(self, "Recipe导入失败", str(e))

    def apply_recipe(self, recipe, path_hint='', remap_current_data=True):
        """将Recipe写入UI；若尚未载入数据，列映射名称会暂存，下一次载入文件后自动匹配。"""
        self.pending_recipe = recipe
        units = recipe.get('units', {}) or {}
        self._safe_set_combo_text(self.cb_x_unit, units.get('x_unit'))
        self._safe_set_combo_text(self.cb_y_unit, units.get('y_unit'))
        self._safe_set_combo_text(self.cb_z_unit, units.get('z_unit'))
        mapping = recipe.get('column_mapping', {}) or {}
        applied_cols = []
        if self.cb_x_col.count() > 0 and self._safe_set_combo_text(self.cb_x_col, mapping.get('x_col')):
            applied_cols.append('X')
        if self.cb_y_col.count() > 0 and self._safe_set_combo_text(self.cb_y_col, mapping.get('y_col')):
            applied_cols.append('Y')
        if self.cb_z_col.count() > 0 and self._safe_set_combo_text(self.cb_z_col, mapping.get('z_col')):
            applied_cols.append('Z')
        lf = recipe.get('large_file', {}) or {}
        self.auto_sample_large_text = bool(lf.get('auto_sample', self.auto_sample_large_text))
        self.large_text_threshold_mb = int(lf.get('threshold_mb', self.large_text_threshold_mb))
        self.large_text_import_limit = int(lf.get('import_limit', self.large_text_import_limit))
        self.display_point_limit = int(lf.get('display_limit', self.display_point_limit))
        self.import_info['display_limit'] = self.display_point_limit
        gap = recipe.get('gap', {}) or {}
        if hasattr(self, 'spin_tol'):
            self.spin_tol.setValue(float(gap.get('tolerance_mm', self.spin_tol.value())))
        if remap_current_data and self.absolute_raw_df is not None:
            self.apply_mapping(preserve_analysis_settings=True)
        valid_actions = {'CW90', 'CCW90', 'ROT180', 'SWAP', 'FLIPX', 'FLIPY', 'ORIGIN(0,0)'}
        self.transform_pipeline = [a for a in (recipe.get('transform_pipeline', []) or []) if a in valid_actions]
        self._update_pipeline_label()
        flt = recipe.get('filter', {}) or {}
        mode_index = max(0, min(int(flt.get('mode_index', 0)), self.cb_filter.count() - 1))
        self.cb_filter.blockSignals(True); self.cb_filter.setCurrentIndex(mode_index); self.cb_filter.blockSignals(False)
        self.spin_k.setValue(int(flt.get('neighbor_k', self.spin_k.value())))
        self.spin_thresh.setValue(float(flt.get('threshold_um', self.spin_thresh.value())))
        self.spin_sigma.setValue(float(flt.get('sigma_k', self.spin_sigma.value())))
        self.spin_sigma_iter.setValue(int(flt.get('sigma_iters', self.spin_sigma_iter.value())))
        self._sync_filter_enabled()
        detrended = bool((recipe.get('display', {}) or {}).get('detrended', False))
        self.chk_detrend_display.blockSignals(True); self.chk_detrend_display.setChecked(detrended); self.chk_detrend_display.blockSignals(False)
        self.display_detrended = detrended
        self.lbl_detrend_info.setText("去倾斜残差 µm" if detrended else "原始Z高度 mm")
        self._update_import_status_label()
        if self.df_raw is not None:
            self.update_analysis()
            self.pending_recipe = None
        msg = f"Recipe已导入{f'：{path_hint}' if path_hint else ''}。"
        if self.absolute_raw_df is None:
            msg += " 当前尚未载入数据，列映射将在下一次载入文件后自动尝试匹配。"
        elif applied_cols:
            msg += f" 已匹配列映射: {', '.join(applied_cols)}。"
        self.statusBar().showMessage(msg, 8000)
        QMessageBox.information(self, "Recipe导入完成", msg)

    # ================= 滤波算法 =================
    @staticmethod
    def mad_filter(resids, k=3.5):
        """全局 MAD 鲁棒滤波：|r - median| <= k * 1.4826 * MAD"""
        med = np.median(resids)
        mad = np.median(np.abs(resids - med))
        if mad < 1e-12:
            return np.ones(len(resids), dtype=bool)
        return np.abs(resids - med) <= k * 1.4826 * mad

    @staticmethod
    def local_median_filter(x, y, resids, k=12, threshold_mm=0.005, global_threshold_mm=None):
        """局部中位数滤波（邻域比较）+ 全局残差兜底。

        判定逻辑：
        1) 局部条件：每个点与其 k 个最近邻（不含自身）的残差中位数比较，
           偏离超过 threshold_mm 判为局部异常。
        2) 全局兜底：每个点与全局残差中位数比较，
           偏离超过 global_threshold_mm 判为全局大离群。

        最终保留条件 = 局部条件通过 AND 全局兜底通过。

        这样既能保留“邻域比较”对孤立坏点的识别能力，
        也能避免边缘点/小簇异常点因为局部自洽而漏判。
        默认 global_threshold_mm 与 threshold_mm 相同，
        即 UI 中的“阈值(µm)”同时作为局部阈值和全局残差硬阈值。
        """
        n = len(resids)
        kk = min(k, n - 1)
        if kk < 1:
            return np.ones(n, dtype=bool)

        if global_threshold_mm is None:
            global_threshold_mm = threshold_mm

        xy = np.column_stack([x, y])
        tree = cKDTree(xy)

        # 局部一致性：分块查询最近邻，避免一次性生成 n*(k+1) 的超大索引/距离矩阵。
        local_ok = np.empty(n, dtype=bool)
        max_query_values = 2_000_000
        batch_size = max(1000, min(n, max_query_values // (kk + 1)))
        for start in range(0, n, batch_size):
            end = min(start + batch_size, n)
            _, idx = tree.query(xy[start:end], k=kk + 1)
            if idx.ndim == 1:
                idx = idx[:, None]
            local_med = np.median(resids[idx[:, 1:]], axis=1)
            local_ok[start:end] = np.abs(resids[start:end] - local_med) <= threshold_mm

        # 全局兜底：当前点与全局残差中位数比较
        # 用于拦截明显大离群点，尤其是边缘点或成簇坏点。
        global_med = np.median(resids)
        global_ok = np.abs(resids - global_med) <= global_threshold_mm

        return local_ok & global_ok

    @classmethod
    def sigma_clip_filter(cls, x, y, z, sigma_k=3.0, max_iter=5):
        """迭代σ裁剪（sigma-clipping）：
        反复用最佳拟合平面残差的标准差σ裁掉 |残差-均值| > sigma_k·σ 的点并重拟合，
        直到残差std收敛或本轮无新增剔除为止。

        特性：
        - 单调裁剪：每轮在上一轮保留集基础上继续剔除，只剔不回收，结果稳定可复现。
        - 多次重拟合：弥补单遍滤波“重拟合后新暴露的离群点抓不到”的短板。

        注意：σ来自全局残差，若工件面有真实弧度，残差里含真实信号，
        本档会把面型真正的峰/谷当离群点剪掉，导致PV被人为缩小；弧形面请优先用局部中位数滤波。
        """
        n = len(z)
        keep = np.ones(n, dtype=bool)
        max_iter = max(1, int(max_iter))
        for _ in range(max_iter):
            if keep.sum() < 3:
                break
            c = cls.fit_plane(x[keep], y[keep], z[keep])
            resid = z - (c[0] * x + c[1] * y + c[2])
            kept = resid[keep]
            sigma = np.std(kept)
            if sigma < 1e-12:
                break
            mu = np.mean(kept)
            new_keep = keep & (np.abs(resid - mu) <= sigma_k * sigma)
            if new_keep.sum() < 3:
                break
            if int(new_keep.sum()) == int(keep.sum()):
                break  # 本轮无新增剔除，已收敛
            keep = new_keep
        return keep

    @classmethod
    def filter_keep_mask(cls, xb, yb, zb, mode, k=12, threshold_mm=0.005,
                         sigma_k=3.0, sigma_iters=5):
        """按滤波模式返回保留布尔掩码（相对输入点集）。
        mode: 0关闭 / 1 MAD全局 / 2 局部中位数 / 3 迭代σ裁剪。
        主界面与批量处理共用此分发，保证两条路径算法一致。"""
        n = len(zb)
        if mode == 0 or n <= 10:
            return np.ones(n, dtype=bool)
        if mode == 3:
            return cls.sigma_clip_filter(xb, yb, zb, sigma_k=sigma_k, max_iter=sigma_iters)
        c0 = cls.fit_plane(xb, yb, zb)
        resids = zb - (c0[0] * xb + c0[1] * yb + c0[2])
        if mode == 1:
            return cls.mad_filter(resids, k=3.5)
        if mode == 2:
            return cls.local_median_filter(xb, yb, resids, k=k,
                                           threshold_mm=threshold_mm,
                                           global_threshold_mm=threshold_mm)
        return np.ones(n, dtype=bool)

    # ================= 拟合 =================
    @staticmethod
    def fit_plane(x, y, z):
        """中心化最小二乘拟合 Z = aX + bY + c，返回 [a, b, c]。
        中心化避免绝对 stage 大坐标导致的病态法方程。"""
        x0, y0 = x.mean(), y.mean()
        A = np.column_stack([x - x0, y - y0, np.ones_like(x)])
        sol, *_ = np.linalg.lstsq(A, z, rcond=None)
        a, b, c0 = sol
        return np.array([a, b, c0 - a * x0 - b * y0])

    @classmethod
    def compute_plane_metrics(cls, fx, fy, fz):
        """对参与拟合的点拟合平面并计算指标。主界面与批量处理共用，口径一致。
        返回 dict: a/b/c/coeffs/mean_z/pv/ttv/rx/ry。
        PV 为相对最佳拟合平面的法向残差极差(µm)，TTV 为原始Z极差(µm)，Rx/Ry 单位 µrad。"""
        c = cls.fit_plane(fx, fy, fz)
        mean_z = float(np.mean(fz))
        ttv = float((np.max(fz) - np.min(fz)) * 1000)
        res_z = fz - (c[0] * fx + c[1] * fy + c[2])
        normal_factor = np.sqrt(c[0] ** 2 + c[1] ** 2 + 1.0)
        res_normal = res_z / normal_factor
        pv = float((np.max(res_normal) - np.min(res_normal)) * 1000)
        rx = float(np.arctan(c[1]) * 1e6)
        ry = float(np.arctan(-c[0]) * 1e6)
        return {'a': float(c[0]), 'b': float(c[1]), 'c': float(c[2]), 'coeffs': c,
                'mean_z': mean_z, 'pv': pv, 'ttv': ttv, 'rx': rx, 'ry': ry}

    # ================= 撤销 =================
    def undo_transform(self):
        if self.transform_pipeline:
            action = self.transform_pipeline.pop()
            self.update_analysis()
            self.statusBar().showMessage(f"已撤销操作: {action}", 3000)
        else:
            QMessageBox.information(self, "提示", "已经退回原始状态，没有可以撤销的操作了。")

    # ================= 多层寄存器 =================
    def set_memory_slot(self, slot):
        if self.df_raw is None or self.active_idx is None:
            QMessageBox.warning(self, "错误", "主界面尚无数据！请先载入并处理。")
            return

        tx, ty, tz = self.get_final_transformed_data(self.df_raw)
        fx, fy, fz = tx[self.active_idx], ty[self.active_idx], tz[self.active_idx]
        rec = {'x': fx.copy(), 'y': fy.copy(), 'z': fz.copy(),
               'name': self.current_source_name, 'n': len(fz)}

        if slot == 'stack':
            self.data_stack = rec
            self.lbl_stack_status.setText(f"✅ 已存【堆叠总成】\n来源: {rec['name']} (共 {rec['n']} 点)")
            self.lbl_stack_status.setStyleSheet("color: #27ae60; font-weight: bold;")
        elif slot == 'base1':
            self.data_base1 = rec
            self.lbl_base1_status.setText(f"✅ 已存【单片 1】\n来源: {rec['name']} (共 {rec['n']} 点)")
            self.lbl_base1_status.setStyleSheet("color: #27ae60; font-weight: bold;")
        elif slot == 'base2':
            self.data_base2 = rec
            self.lbl_base2_status.setText(f"✅ 已存【单片 2】\n来源: {rec['name']} (共 {rec['n']} 点)")
            self.lbl_base2_status.setStyleSheet("color: #2980b9; font-weight: bold;")

    def clear_memory_slot(self, slot):
        if slot == 'base2':
            self.data_base2 = None
            self.lbl_base2_status.setText("⭕ 可选空置")
            self.lbl_base2_status.setStyleSheet("color: #7f8c8d; font-weight: bold;")

    def clear_all_memory_slots(self):
        self.data_stack = None
        self.data_base1 = None
        self.data_base2 = None
        self.lbl_stack_status.setText("❌ 尚未设置")
        self.lbl_stack_status.setStyleSheet("color: #c0392b; font-weight: bold;")
        self.lbl_base1_status.setText("❌ 尚未设置")
        self.lbl_base1_status.setStyleSheet("color: #c0392b; font-weight: bold;")
        self.lbl_base2_status.setText("⭕ 可选空置")
        self.lbl_base2_status.setStyleSheet("color: #7f8c8d; font-weight: bold;")
        self.statusBar().showMessage("已清空全部寄存器", 3000)

    def calculate_gap(self):
        if self.data_stack is None or self.data_base1 is None:
            QMessageBox.critical(self, "数据缺失", "执行运算至少需要设置【堆叠总成】和【单片 1】！")
            return

        tolerance = self.spin_tol.value()

        # 计算前确认各寄存器来源，防止新旧物料混用
        desc = (f"即将执行: Inner Gap = 堆叠总成 - 单片1{' - 单片2' if self.data_base2 else ''}\n\n"
                f"堆叠总成: {self.data_stack['name']}  ({self.data_stack['n']} 点)\n"
                f"单片 1:   {self.data_base1['name']}  ({self.data_base1['n']} 点)\n")
        if self.data_base2 is not None:
            desc += f"单片 2:   {self.data_base2['name']}  ({self.data_base2['n']} 点)\n"
        desc += f"\n容差窗口: {tolerance} mm\n\n请确认以上数据来源无误（避免新旧物料混用）。"
        ret = QMessageBox.question(self, "确认计算", desc,
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        if ret != QMessageBox.StandardButton.Yes:
            return

        try:
            sx, sy, sz = self.data_stack['x'], self.data_stack['y'], self.data_stack['z']
            b1x, b1y, b1z = self.data_base1['x'], self.data_base1['y'], self.data_base1['z']

            tree1 = cKDTree(np.column_stack([b1x, b1y]))
            dist1, idx1 = tree1.query(np.column_stack([sx, sy]), distance_upper_bound=tolerance)
            valid1 = dist1 <= tolerance

            report_parts = []

            def match_report(tag, dist, idx, valid):
                md = dist[valid]
                if len(md) == 0:
                    return f"[{tag}] 无匹配点！"
                rms_um = np.sqrt(np.mean(md ** 2)) * 1000
                max_um = np.max(md) * 1000
                uniq = len(np.unique(idx[valid])) / valid.sum() * 100
                s = (f"[{tag}] 匹配 {int(valid.sum())} / 未匹配 {int((~valid).sum())}\n"
                     f"    匹配距离 RMS {rms_um:.2f} µm | Max {max_um:.2f} µm\n"
                     f"    唯一匹配比例 {uniq:.1f}%")
                if uniq < 99.9:
                    s += "  ⚠ 存在多对一重复匹配，建议减小容差"
                return s

            report_parts.append(match_report("单片1", dist1, idx1, valid1))

            if self.data_base2 is not None:
                b2x, b2y, b2z = self.data_base2['x'], self.data_base2['y'], self.data_base2['z']
                tree2 = cKDTree(np.column_stack([b2x, b2y]))
                dist2, idx2 = tree2.query(np.column_stack([sx, sy]), distance_upper_bound=tolerance)
                valid2 = dist2 <= tolerance
                report_parts.append(match_report("单片2", dist2, idx2, valid2))

                valid = valid1 & valid2
                final_sx = sx[valid]
                final_sy = sy[valid]
                final_gap_z = sz[valid] - b1z[idx1[valid]] - b2z[idx2[valid]]
            else:
                valid = valid1
                final_sx = sx[valid]
                final_sy = sy[valid]
                final_gap_z = sz[valid] - b1z[idx1[valid]]

            if len(final_gap_z) < 10:
                raise ValueError("容差范围内配对成功的有效点不足！\n请尝试增大【误差窗口】数值，或检查各组数据是否都执行了[平移归零]。")

            gap_name = f"GAP({self.data_stack['name']} - {self.data_base1['name']}"
            if self.data_base2 is not None:
                gap_name += f" - {self.data_base2['name']}"
            gap_name += ")"

            self.df_raw = pd.DataFrame({'Z': final_gap_z, 'X': final_sx, 'Y': final_sy})
            self._df_version += 1
            # 防止误点"应用映射"用旧文件覆盖 Gap 结果
            self.absolute_raw_df = None
            self.current_source_name = gap_name
            self.lbl_source.setText(f"当前数据: {gap_name}")
            self.import_info = {
                'file_size_bytes': 0,
                'file_size_mb': 0.0,
                'strategy': 'Gap计算结果',
                'sampled': False,
                'import_rows': len(self.df_raw),
                'valid_rows': len(self.df_raw),
                'display_limit': self._display_limit(),
                'notes': '由多层点云匹配计算生成'
            }
            self._update_import_status_label()
            self.transform_pipeline = []
            self.manual_mask = np.ones(len(self.df_raw), dtype=bool)
            self.temp_selected_mask = np.zeros(len(self.df_raw), dtype=bool)
            self.current_coeffs = None

            self.tabs.setCurrentIndex(0)
            self.update_analysis()

            msg = (f"成功配对并算出 Inner Gap！\n"
                   f"容差设定: {tolerance} mm\n"
                   f"成功对齐点数: {len(final_gap_z)}\n"
                   f"公式: 堆叠总成 - 单片1{' - 单片2' if self.data_base2 is not None else ''}\n\n"
                   f"—— 匹配质量报告 ——\n" + "\n".join(report_parts) +
                   "\n\n注: 当前视图已切换为 Gap 结果，原文件映射已锁定；"
                   "如需重新分析原数据请重新载入文件。")
            QMessageBox.information(self, "计算成功", msg)

        except Exception as e:
            QMessageBox.critical(self, "运算失败", f"点云对齐错误: {str(e)}")

    # ================= 变换流水线 =================
    def add_cw90(self): self.transform_pipeline.append("CW90"); self.update_analysis()
    def add_ccw90(self): self.transform_pipeline.append("CCW90"); self.update_analysis()
    def add_rot180(self): self.transform_pipeline.append("ROT180"); self.update_analysis()
    def add_swap(self): self.transform_pipeline.append("SWAP"); self.update_analysis()
    def add_flipx(self): self.transform_pipeline.append("FLIPX"); self.update_analysis()
    def add_flipy(self): self.transform_pipeline.append("FLIPY"); self.update_analysis()
    def add_origin(self): self.transform_pipeline.append("ORIGIN(0,0)"); self.update_analysis()

    def reset_all(self):
        if self.df_raw is None: return
        self.transform_pipeline = []
        self.manual_mask = np.ones(len(self.df_raw), dtype=bool)
        self.temp_selected_mask = np.zeros(len(self.df_raw), dtype=bool)
        self.current_coeffs = None
        self.cb_filter.blockSignals(True)
        self.cb_filter.setCurrentIndex(0)
        self.cb_filter.blockSignals(False)
        self.chk_detrend_display.blockSignals(True)
        self.chk_detrend_display.setChecked(False)
        self.chk_detrend_display.blockSignals(False)
        self.display_detrended = False
        self.lbl_detrend_info.setText("原始Z高度 mm")
        self.update_analysis()
        self.statusBar().showMessage("系统已完全重置（滤波已关闭，去倾斜显示已关闭）", 3000)

    # ================= 文件载入与映射 =================
    TEXT_SUFFIXES = ('.csv', '.txt', '.tsv', '.dat', '.asc', '.xyz')
    EXCEL_SUFFIXES = ('.xlsx', '.xls', '.xlsm')

    def _large_text_threshold_bytes(self):
        return int(getattr(self, 'large_text_threshold_mb', self.LARGE_TEXT_FILE_BYTES // (1024 * 1024))) * 1024 * 1024

    def _large_text_import_limit(self):
        return int(getattr(self, 'large_text_import_limit', self.LARGE_TEXT_IMPORT_LIMIT))

    def _display_limit(self):
        return int(getattr(self, 'display_point_limit', self.DISPLAY_POINT_LIMIT))

    def _reset_import_info(self, path=None):
        size = 0
        if path:
            try:
                size = Path(path).stat().st_size
            except Exception:
                size = 0
        self.import_info = {
            'file_size_bytes': size,
            'file_size_mb': size / (1024 * 1024) if size else 0.0,
            'strategy': '--',
            'sampled': False,
            'import_rows': 0,
            'display_limit': self._display_limit(),
            'notes': ''
        }

    def _update_import_status_label(self):
        info = getattr(self, 'import_info', {}) or {}
        strategy = info.get('strategy', '--')
        file_size_mb = info.get('file_size_mb', 0.0)
        import_rows = info.get('import_rows', 0)
        display_limit = self._display_limit()
        shown = self.last_displayed_points if self.last_displayed_points else min(import_rows or 0, display_limit)
        sampled_text = '抽样' if info.get('sampled') else '全量/未抽样'
        notes = info.get('notes') or ''
        valid_rows = info.get('valid_rows', None)
        valid_text = f" | 有效 {int(valid_rows):,} 点" if valid_rows is not None else ""
        text = (f"导入状态: {strategy} | {sampled_text} | 文件 {file_size_mb:.1f} MB | "
                f"读入 {int(import_rows):,} 行{valid_text} | 显示 {int(shown):,}/{int(display_limit):,} 点")
        if notes:
            text += f" | {notes}"
        if hasattr(self, 'lbl_import_status'):
            self.lbl_import_status.setText(text)
        if hasattr(self, 'btn_bigfile_settings'):
            cfg = (f"大文件策略\n"
                   f"自动抽样: {'开启' if self.auto_sample_large_text else '关闭'}\n"
                   f"触发阈值: {self.large_text_threshold_mb} MB\n"
                   f"导入上限: {self.large_text_import_limit:,} 行\n"
                   f"显示上限: {self.display_point_limit:,} 点\n\n{text}")
            self.btn_bigfile_settings.setToolTip(cfg)
        if text and strategy != '--':
            self.statusBar().showMessage(text, 5000)

    def _on_display_limit_changed(self):
        self.import_info['display_limit'] = self._display_limit()
        self._update_import_status_label()
        if self.df_raw is not None and self.active_idx is not None:
            self.update_plots_only()

    def show_bigfile_settings_dialog(self):
        """V3.5.1: 大文件导入/显示策略弹窗。
        正常界面只保留右侧工具条按钮，避免占用左侧主控区。"""
        dlg = QDialog(self)
        dlg.setWindowTitle("大文件导入 / 显示策略")
        dlg.setMinimumWidth(520)
        layout = QVBoxLayout(dlg)

        group = QGroupBox("Zeiss / TXT / ASC / XYZ 大文件策略")
        grid = QGridLayout(group)

        chk_auto = QCheckBox("超大文本自动抽样")
        chk_auto.setChecked(self.auto_sample_large_text)
        chk_auto.setToolTip("开启后，超过阈值的TXT/CSV/ASC/XYZ等文本文件不会全量读入，而是先按文件位置均匀抽样，避免Zeiss大文件卡死。")
        grid.addWidget(chk_auto, 0, 0, 1, 2)

        grid.addWidget(QLabel("触发阈值(MB):"), 1, 0)
        spin_mb = QSpinBox()
        spin_mb.setRange(1, 4096)
        spin_mb.setValue(int(self.large_text_threshold_mb))
        spin_mb.setToolTip("文件大小达到该阈值时触发预抽样导入。默认512MB。")
        grid.addWidget(spin_mb, 1, 1)

        grid.addWidget(QLabel("导入上限(行):"), 2, 0)
        spin_import = QSpinBox()
        spin_import.setRange(10000, 5000000)
        spin_import.setSingleStep(50000)
        spin_import.setValue(int(self.large_text_import_limit))
        spin_import.setToolTip("超大文本预抽样最多导入的行数。注意：该上限影响后续拟合/滤波指标。")
        grid.addWidget(spin_import, 2, 1)

        grid.addWidget(QLabel("显示上限(点):"), 3, 0)
        spin_display = QSpinBox()
        spin_display.setRange(5000, 1000000)
        spin_display.setSingleStep(5000)
        spin_display.setValue(int(self.display_point_limit))
        spin_display.setToolTip("仅限制右侧绘图显示点数，不改变已导入数据和Rx/Ry/PV/TTV计算。")
        grid.addWidget(spin_display, 3, 1)

        note = QLabel("说明：导入抽样会影响参与分析的数据量；显示上限只影响绘图，不改变已导入数据。")
        note.setWordWrap(True)
        note.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        grid.addWidget(note, 4, 0, 1, 2)
        layout.addWidget(group)

        status = QLabel(self.lbl_import_status.text() if hasattr(self, 'lbl_import_status') else "导入状态: --")
        status.setWordWrap(True)
        status.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        layout.addWidget(status)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            old_display_limit = self.display_point_limit
            self.auto_sample_large_text = chk_auto.isChecked()
            self.large_text_threshold_mb = int(spin_mb.value())
            self.large_text_import_limit = int(spin_import.value())
            self.display_point_limit = int(spin_display.value())
            self.import_info['display_limit'] = self.display_point_limit
            self._update_import_status_label()
            if old_display_limit != self.display_point_limit and self.df_raw is not None and self.active_idx is not None:
                self.update_plots_only()
            self.statusBar().showMessage("大文件导入/显示策略已更新", 5000)

    @staticmethod
    def _detect_sep_from_line(line):
        if '\t' in line:
            return '\t'
        if ',' in line:
            return ','
        if ';' in line:
            return ';'
        return r'\s+'

    @staticmethod
    def _split_text_line(line, sep):
        if sep == r'\s+':
            return [t for t in re.split(r'\s+', line.strip()) if t]
        return [t for t in line.strip().split(sep) if t != '']

    @classmethod
    def _is_missing_token(cls, value):
        return str(value).strip() in cls.MISSING_TEXT_TOKENS

    @classmethod
    def _is_float_token(cls, value):
        try:
            float(str(value))
            return True
        except (TypeError, ValueError):
            return False

    @classmethod
    def _is_float_or_missing_token(cls, value):
        if cls._is_missing_token(value):
            return True
        return cls._is_float_token(value)

    @classmethod
    def _looks_like_numeric_text_row(cls, tokens):
        if len(tokens) < 2:
            return False
        numeric_count = sum(cls._is_float_token(t) for t in tokens)
        return numeric_count >= 2 and all(cls._is_float_or_missing_token(t) for t in tokens)

    @classmethod
    def _token_to_float(cls, value):
        if cls._is_missing_token(value):
            return np.nan
        try:
            return float(value)
        except (TypeError, ValueError):
            return np.nan

    @classmethod
    def _detect_text_layout(cls, path, enc, max_scan_lines=5000):
        """扫描文本开头，识别第一行有效数值数据、分隔符、列数和可选表头。
        兼容 Zeiss 类导出：前面可能有仪器信息/单位信息，只要后面存在 X/Y/Z 数值行即可。"""
        last_tokens = None
        last_line_no = None
        last_sep = None
        with open(path, 'r', encoding=enc, errors='ignore') as fh:
            for line_no, line in enumerate(fh):
                if line_no >= max_scan_lines:
                    break
                stripped = line.strip().lstrip('\ufeff')
                if not stripped or stripped.startswith('#'):
                    continue
                sep = cls._detect_sep_from_line(stripped)
                tokens = cls._split_text_line(stripped, sep)
                if cls._looks_like_numeric_text_row(tokens):
                    header_tokens = None
                    if last_tokens and len(last_tokens) == len(tokens) and not cls._looks_like_numeric_text_row(last_tokens):
                        header_tokens = [str(t).replace('\ufeff', '').strip() or f'Col{i+1}'
                                         for i, t in enumerate(last_tokens)]
                    return {
                        'encoding': enc,
                        'sep': sep,
                        'ncols': len(tokens),
                        'data_line_no': line_no,
                        'header_tokens': header_tokens,
                        'first_numeric_line': stripped,
                        'header_line_no': last_line_no,
                        'header_sep': last_sep,
                    }
                last_tokens = tokens
                last_line_no = line_no
                last_sep = sep
        return None

    def _sample_large_text(self, path, enc, sep, ncols, column_names=None):
        """超大文本预抽样：按文件字节位置均匀抽取数据行。
        该步骤发生在 pandas 全量读入之前，目的是避免 Zeiss 大TXT一次性读爆内存。
        注意：这是文件位置抽样，不是空间网格抽样。"""
        file_size = Path(path).stat().st_size
        max_rows = self._large_text_import_limit()
        rows = []
        seen_starts = set()

        with open(path, 'rb') as fh:
            mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
            try:
                offsets = np.linspace(0, max(0, file_size - 1), max_rows, dtype=np.int64)
                for i, offset in enumerate(offsets):
                    offset = int(offset)
                    if offset <= 0:
                        start = 0
                    else:
                        start = mm.find(b'\n', offset)
                        if start < 0:
                            continue
                        start += 1
                    if start in seen_starts:
                        continue
                    seen_starts.add(start)
                    end = mm.find(b'\n', start)
                    if end < 0:
                        end = file_size
                    raw = mm[start:end].strip()
                    if not raw:
                        continue
                    line = raw.decode(enc, errors='ignore').strip().lstrip('\ufeff')
                    if not line or line.startswith('#'):
                        continue
                    tokens = self._split_text_line(line, sep)
                    if not self._looks_like_numeric_text_row(tokens):
                        continue
                    values = [self._token_to_float(t) for t in tokens[:ncols]]
                    if len(values) < ncols:
                        values.extend([np.nan] * (ncols - len(values)))
                    rows.append(values)
                    if i % 5000 == 0:
                        self.statusBar().showMessage(
                            f"正在抽样导入超大TXT: {i + 1:,}/{max_rows:,} | 已取有效行 {len(rows):,}", 1000)
                        QApplication.processEvents()
            finally:
                mm.close()

        if not rows:
            raise ValueError("超大文本抽样未得到有效数值行，请检查文件格式或关闭自动抽样后重试。")

        cols = column_names if column_names and len(column_names) == ncols else [f'Col{i+1}' for i in range(ncols)]
        df = pd.DataFrame(rows, columns=cols)
        self.last_import_note = (
            f"超大文本已预抽样导入，避免全量读入导致卡死。\n"
            f"文件大小: {file_size / (1024 * 1024):.1f} MB\n"
            f"触发阈值: {self.large_text_threshold_mb} MB\n"
            f"抽样上限: {max_rows:,} 行\n"
            f"实际导入行数: {len(df):,} 行\n"
            f"抽样方式: 按文件位置均匀抽样，不是空间网格抽样。\n"
            f"缺测值标记({', '.join(sorted(self.MISSING_TEXT_TOKENS))})已按空值处理。"
        )
        self.import_info.update({
            'strategy': '超大文本预抽样导入',
            'sampled': True,
            'import_rows': len(df),
            'notes': f"抽样上限 {max_rows:,} 行"
        })
        return df

    def _read_table(self, path):
        """鲁棒读取表格文件：
        - 文本类(.csv/.txt/.tsv/.dat/.asc/.xyz): 自动尝试 utf-8-sig/gbk/utf-16/latin-1；自动识别分隔符；
          自动跳过#注释行、空行、坏行；识别无表头/普通表头/Zeiss类复杂头。
        - 超过设定阈值的文本文件可在 pandas 全量读入前预抽样，默认阈值512MB、最多50万行。
        - Excel类(.xlsx/.xls/.xlsm): pd.read_excel，不做预抽样。
        """
        self.last_import_note = ""
        self._reset_import_info(path)
        suffix = Path(path).suffix.lower()
        file_size = Path(path).stat().st_size

        if suffix in self.EXCEL_SUFFIXES:
            df = pd.read_excel(path)
            self.import_info.update({
                'strategy': 'Excel全量读取',
                'sampled': False,
                'import_rows': len(df),
                'notes': 'Excel不做预抽样'
            })
        elif suffix in self.TEXT_SUFFIXES or suffix == '':
            last_err = None
            df = None
            layout = None

            for enc in ('utf-8-sig', 'gbk', 'utf-16', 'latin-1'):
                try:
                    layout = self._detect_text_layout(path, enc)
                    if layout is not None:
                        break
                except Exception as e:
                    last_err = e
                    layout = None

            if layout is not None:
                enc = layout['encoding']
                sep = layout['sep']
                ncols = layout['ncols']
                col_names = layout['header_tokens'] if layout['header_tokens'] else [f'Col{i+1}' for i in range(ncols)]
                auto_sample = bool(getattr(self, 'auto_sample_large_text', True))
                if auto_sample and file_size >= self._large_text_threshold_bytes():
                    df = self._sample_large_text(path, enc, sep, ncols, col_names)
                else:
                    df = pd.read_csv(path, sep=sep, engine='python', encoding=enc,
                                     comment='#', skip_blank_lines=True,
                                     on_bad_lines='skip', header=None,
                                     skiprows=layout['data_line_no'],
                                     na_values=list(self.MISSING_TEXT_TOKENS),
                                     keep_default_na=True)
                    if df.shape[1] >= len(col_names):
                        df = df.iloc[:, :len(col_names)]
                        df.columns = col_names
                    else:
                        df.columns = [f'Col{i+1}' for i in range(df.shape[1])]
                    self.import_info.update({
                        'strategy': '文本全量读取',
                        'sampled': False,
                        'import_rows': len(df),
                        'notes': f"编码 {enc}"
                    })
            else:
                # 回退到 pandas 嗅探；不建议用于超大未知格式文件，因此超过阈值时给出明确提示。
                auto_sample = bool(getattr(self, 'auto_sample_large_text', True))
                if auto_sample and file_size >= self._large_text_threshold_bytes():
                    raise ValueError("文件超过超大文本阈值，但前5000行未识别到有效数值数据行；\n"
                                     "为避免全量读入卡死，已停止导入。请检查Zeiss TXT头部格式，或关闭自动抽样后重试。")
                for enc in ('utf-8-sig', 'gbk', 'utf-16', 'latin-1'):
                    for sep in (None, ',', '\t', ';', r'\s+'):
                        try:
                            df_try = pd.read_csv(path, sep=sep, engine='python', encoding=enc,
                                                 comment='#', skip_blank_lines=True, on_bad_lines='skip',
                                                 na_values=list(self.MISSING_TEXT_TOKENS),
                                                 keep_default_na=True)
                            if df_try.shape[1] >= 2:
                                df = df_try
                                self.import_info.update({
                                    'strategy': '文本全量读取(回退嗅探)',
                                    'sampled': False,
                                    'import_rows': len(df),
                                    'notes': f"编码 {enc}"
                                })
                                break
                        except Exception as e:
                            last_err = e
                    if df is not None:
                        break
                if df is None:
                    raise ValueError(f"文本解析失败（已尝试 utf-8/gbk/utf-16/latin-1 编码与常见分隔符）: {last_err}")
        else:
            raise ValueError(f"不支持的文件格式: {suffix}\n"
                             f"支持: {', '.join(self.TEXT_SUFFIXES + self.EXCEL_SUFFIXES)}")

        # 清理列名: 去 BOM、去首尾空白
        df.columns = [str(c).replace('\ufeff', '').strip() for c in df.columns]

        # 如果列名仍像数字，说明第一行可能是数据；统一改为 Col1..ColN
        def _is_num(s):
            try:
                float(str(s)); return True
            except (TypeError, ValueError):
                return False
        if df.shape[1] >= 2 and all(_is_num(c) for c in df.columns):
            df.columns = [f'Col{i+1}' for i in range(df.shape[1])]

        if df.empty or df.shape[1] < 2:
            raise ValueError("文件内容为空或有效列少于 2 列，请检查文件。")

        self.import_info['import_rows'] = len(df)
        self.import_info['display_limit'] = self._display_limit()
        self._update_import_status_label()
        return df

    def load_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "载入数据", "",
            "Data (*.csv *.txt *.tsv *.dat *.asc *.xyz *.xlsx *.xls *.xlsm);;All Files (*)")
        if not path: return
        try:
            self.absolute_raw_df = self._read_table(path)

            self.current_source_name = Path(path).name
            self.lbl_source.setText(f"当前数据: {self.current_source_name}")

            cols = [str(c) for c in self.absolute_raw_df.columns]
            for cb in [self.cb_x_col, self.cb_y_col, self.cb_z_col]:
                cb.blockSignals(True); cb.clear(); cb.addItems(cols); cb.blockSignals(False)

            def guess_index(tc, di):
                for i, col in enumerate(cols):
                    if tc.lower() in col.lower(): return i
                return di if di < len(cols) else 0

            if len(cols) >= 3 and all(re.fullmatch(r'Col\d+', c) for c in cols):
                # 无表头 X/Y/Z 文本默认按 Col1/Col2/Col3 映射，适配 Zeiss/XYZ 常见导出
                self.cb_x_col.setCurrentIndex(0)
                self.cb_y_col.setCurrentIndex(1)
                self.cb_z_col.setCurrentIndex(2)
            else:
                self.cb_x_col.setCurrentIndex(guess_index('x', 1))
                self.cb_y_col.setCurrentIndex(guess_index('y', 2))
                self.cb_z_col.setCurrentIndex(guess_index('z', 0))
            if self.pending_recipe is not None:
                units = self.pending_recipe.get('units', {}) or {}
                self._safe_set_combo_text(self.cb_x_unit, units.get('x_unit'))
                self._safe_set_combo_text(self.cb_y_unit, units.get('y_unit'))
                self._safe_set_combo_text(self.cb_z_unit, units.get('z_unit'))
                mapping = self.pending_recipe.get('column_mapping', {}) or {}
                self._safe_set_combo_text(self.cb_x_col, mapping.get('x_col'))
                self._safe_set_combo_text(self.cb_y_col, mapping.get('y_col'))
                self._safe_set_combo_text(self.cb_z_col, mapping.get('z_col'))
                self.apply_mapping(preserve_analysis_settings=True)
                self.apply_recipe(self.pending_recipe, path_hint='已随当前文件自动应用', remap_current_data=False)
            else:
                self.apply_mapping()
            self._update_import_status_label()
            if self.last_import_note:
                self.statusBar().showMessage(self.last_import_note.replace('\n', ' | '), 15000)
                QMessageBox.information(self, "超大文件导入说明", self.last_import_note)

            # 寄存器保留提示（多层流程需要跨文件保留，故不自动清空）
            if any(s is not None for s in (self.data_stack, self.data_base1, self.data_base2)):
                self.statusBar().showMessage(
                    "⚠ 提示: 多层寄存器仍保留之前的数据，如属不同物料请到[多层]页点击 [🧹 清空全部寄存器]", 10000)
        except Exception as e:
            QMessageBox.critical(self, "导入失败", str(e))

    def apply_mapping(self, preserve_analysis_settings=False):
        if self.absolute_raw_df is None:
            self.statusBar().showMessage("当前无原始文件可映射（Gap 结果状态下映射已锁定）", 5000)
            return
        try:
            xc, yc, zc = self.cb_x_col.currentText(), self.cb_y_col.currentText(), self.cb_z_col.currentText()
            for name, col in (("X", xc), ("Y", yc), ("Z", zc)):
                if col not in self.absolute_raw_df.columns:
                    raise ValueError(f"{name}列 '{col}' 不在文件列中，请重新选择列映射。")
            temp_df = pd.DataFrame()
            temp_df['X'] = pd.to_numeric(self.absolute_raw_df[xc], errors='coerce')
            temp_df['Y'] = pd.to_numeric(self.absolute_raw_df[yc], errors='coerce')
            temp_df['Z'] = pd.to_numeric(self.absolute_raw_df[zc], errors='coerce')
            temp_df = temp_df.dropna()

            if len(temp_df) < 3:
                raise ValueError("有效数据点少于 3 个，请检查列映射与单位选择。")

            unit_m = {"mm": 1.0, "µm": 1e-3, "nm": 1e-6}
            temp_df['X'] = temp_df['X'] * unit_m[self.cb_x_unit.currentText()]
            temp_df['Y'] = temp_df['Y'] * unit_m[self.cb_y_unit.currentText()]
            temp_df['Z'] = temp_df['Z'] * unit_m[self.cb_z_unit.currentText()]

            self.df_raw = temp_df[['Z', 'X', 'Y']]
            self.import_info['valid_rows'] = len(self.df_raw)
            self.import_info['display_limit'] = self._display_limit()
            self._update_import_status_label()
            self._df_version += 1
            if preserve_analysis_settings:
                self.manual_mask = np.ones(len(self.df_raw), dtype=bool)
                self.temp_selected_mask = np.zeros(len(self.df_raw), dtype=bool)
                self.current_coeffs = None
                self._trans_cache_key = None
                self._trans_cache_data = None
                self.update_analysis()
            else:
                self.reset_all()
        except Exception as e:
            QMessageBox.critical(self, "解析失败", str(e))

    # ================= 坐标变换（含缓存） =================
    @staticmethod
    def _apply_transform_pipeline(x, y, z, pipeline):
        """基于包围盒的姿态变换（无状态，供主界面缓存与批量处理共用）。
        旋转/翻转均以数据包围盒为参照，坐标不从0开始也不会产生偏移。
        90°旋转采用【物料旋转】语义：CW90 顶部点->右侧；CCW90 顶部点->左侧。"""
        x = np.asarray(x, dtype=float).copy()
        y = np.asarray(y, dtype=float).copy()
        z = np.asarray(z, dtype=float).copy()
        for action in pipeline:
            xmin, xmax = np.min(x), np.max(x)
            ymin, ymax = np.min(y), np.max(y)
            if action == "ROT180":
                x, y = xmin + xmax - x, ymin + ymax - y
            elif action == "CW90":      # 物料顺时针旋转90°: 顶部点 -> 右侧（主动旋转）
                x, y = xmin + (y - ymin), ymin + (xmax - x)
            elif action == "CCW90":     # 物料逆时针旋转90°: 顶部点 -> 左侧（主动旋转）
                x, y = xmin + (ymax - y), ymin + (x - xmin)
            elif action == "SWAP":
                x, y = y, x
            elif action == "FLIPX":     # 前后翻转 = Y 镜像
                y = ymin + ymax - y
            elif action == "FLIPY":     # 左右翻转 = X 镜像
                x = xmin + xmax - x
            elif action == "ORIGIN(0,0)":
                x = x - xmin
                y = y - ymin
        return x, y, z

    def get_final_transformed_data(self, df):
        """对当前 df 应用 transform_pipeline，结果带缓存。"""
        key = (self._df_version, tuple(self.transform_pipeline))
        if self._trans_cache_key == key:
            return self._trans_cache_data

        x, y, z = self._apply_transform_pipeline(
            df['X'].values, df['Y'].values, df['Z'].values, self.transform_pipeline)

        self._trans_cache_key = key
        self._trans_cache_data = (x, y, z)
        return x, y, z

    def _update_pipeline_label(self):
        action_names = {
            "CW90": "物料顺时针90°",
            "CCW90": "物料逆时针90°",
            "ROT180": "物料旋转180°",
            "SWAP": "X-Y轴对调",
            "FLIPX": "X轴翻转(前后)",
            "FLIPY": "Y轴翻转(左右)",
            "ORIGIN(0,0)": "平移归零(0,0)",
        }
        t = "原始状态"
        if self.transform_pipeline:
            t += " -> " + " -> ".join(action_names.get(a, a) for a in self.transform_pipeline)
        self.lbl_pipeline.setText(f"变换路径: {t}")

    def _on_filter_param_changed(self):
        # 局部中位数(2) / 迭代σ裁剪(3) 模式下参数变化才需要重算
        if self.cb_filter.currentIndex() in (2, 3):
            self.update_analysis()

    def _sync_filter_enabled(self):
        """按当前滤波模式启用/禁用对应参数控件，避免误以为某参数对当前模式生效。"""
        m = self.cb_filter.currentIndex()
        local_on = (m == 2)
        sigma_on = (m == 3)
        for w in (self.lbl_k, self.spin_k, self.lbl_thresh, self.spin_thresh):
            w.setEnabled(local_on)
        for w in (self.lbl_sigma, self.spin_sigma, self.lbl_sigma_iter, self.spin_sigma_iter):
            w.setEnabled(sigma_on)

    def _on_filter_mode_changed(self):
        self._sync_filter_enabled()
        self.update_analysis()

    def _on_detrend_display_changed(self):
        self.display_detrended = self.chk_detrend_display.isChecked()
        if self.display_detrended:
            self.lbl_detrend_info.setText("去倾斜残差 µm")
        else:
            self.lbl_detrend_info.setText("原始Z高度 mm")
        self.update_plots_only()

    def _get_plot_z(self, tx, ty, tz):
        """返回绘图/框选使用的Z值和轴标签。
        原始模式：Z使用内部单位mm；
        去倾斜模式：Z为相对当前最佳拟合平面的残差，单位µm。
        注意：该函数只服务显示/框选，不改变原始数据和拟合指标。"""
        if self.display_detrended and self.current_coeffs is not None:
            c = self.current_coeffs
            plot_z = (tz - (c[0] * tx + c[1] * ty + c[2])) * 1000.0
            return plot_z, "Resid / 去倾斜残差 (µm)", "去倾斜残差 (µm)"
        return tz, "Z (mm)", "Z"

    # ================= 核心分析 =================
    def update_analysis(self):
        if self.df_raw is None: return
        try:
            tx, ty, tz = self.get_final_transformed_data(self.df_raw)
            self._update_pipeline_label()

            idx = np.where(self.manual_mask)[0]
            if len(idx) < 3:
                self.statusBar().showMessage("⚠ 有效点少于 3 个，无法拟合平面。请点击[♻️ 全部重置]恢复数据。", 10000)
                return
            xb, yb, zb = tx[idx], ty[idx], tz[idx]

            # 1. 滤波（主界面与批量共用同一分发 filter_keep_mask）
            mode = self.cb_filter.currentIndex()
            self.n_filtered = 0
            keep = self.filter_keep_mask(
                xb, yb, zb, mode,
                k=self.spin_k.value(),
                threshold_mm=self.spin_thresh.value() * 1e-3,
                sigma_k=self.spin_sigma.value(),
                sigma_iters=self.spin_sigma_iter.value())
            if mode != 0 and keep.sum() < 3:
                self.statusBar().showMessage("⚠ 滤波后点数不足 3 个，已自动退回未滤波状态。请调整参数。", 10000)
                self.active_idx = idx
            else:
                self.active_idx = idx[keep]
                self.n_filtered = int(len(idx) - keep.sum())

            self.lbl_filter_info.setText(
                f"滤波剔除: {self.n_filtered} 点 | 手动删除: {int((~self.manual_mask).sum())} 点 | 参与拟合: {len(self.active_idx)} 点")

            fx, fy, fz = tx[self.active_idx], ty[self.active_idx], tz[self.active_idx]

            # 2. 最终拟合与指标（与批量处理共用 compute_plane_metrics）
            m = self.compute_plane_metrics(fx, fy, fz)
            c = m['coeffs']
            self.current_coeffs = c
            mean_z, ttv, pv, rx, ry = m['mean_z'], m['ttv'], m['pv'], m['rx'], m['ry']

            self.last_metrics = {'a': m['a'], 'b': m['b'], 'c': m['c'],
                                 'mean_z': mean_z, 'pv': pv, 'ttv': ttv,
                                 'rx': rx, 'ry': ry}

            # UI 更新
            self.lbl_eqn.setText(f"Z = {c[0]:.4f}·X + {c[1]:.4f}·Y + {c[2]:.4f}")
            # 单位已在结果卡片标题中展示，这里只写数值，避免重复
            self.lbl_z.setText(f"{mean_z:.5f}")
            self.lbl_pv.setText(f"{pv:.3f}"); self.lbl_ttv.setText(f"{ttv:.3f}")
            self.lbl_rx.setText(f"{rx:.2f}"); self.lbl_ry.setText(f"{ry:.2f}")
            self.draw_plots(tx, ty, tz)
            self.setup_selectors()
        except Exception as e:
            self.statusBar().showMessage(f"⚠ 分析出错: {e}", 10000)

    # ================= 绘图与交互 =================
    def draw_plots(self, tx, ty, tz):
        plot_idx = self.active_idx
        display_limit = self._display_limit()
        if len(plot_idx) > display_limit:
            pick = np.linspace(0, len(plot_idx) - 1, display_limit, dtype=int)
            plot_idx = plot_idx[pick]
            self.statusBar().showMessage(
                f"数据共 {len(self.active_idx):,} 点；绘图抽样显示 {len(plot_idx):,} 点，指标仍按当前导入后的分析数据计算。", 5000)

        self.last_displayed_points = len(plot_idx)
        self._update_import_status_label()

        dx, dy = tx[plot_idx], ty[plot_idx]
        plot_z_all, z_axis_label, z_short_label = self._get_plot_z(tx, ty, tz)
        dz = plot_z_all[plot_idx]

        axes = [self.canvas.ax3d, self.canvas.ax_xy, self.canvas.ax_xz, self.canvas.ax_yz]
        lbs = [("X (mm)", "Y (mm)", z_axis_label),
               ("X (mm)", "Y (mm)"),
               ("X (mm)", z_axis_label),
               ("Y (mm)", z_axis_label)]
        for ax, lb in zip(axes, lbs):
            ax.clear(); ax.grid(True, linestyle='-', linewidth=0.7, color='#edf0f3')
            ax.set_xlabel(lb[0]); ax.set_ylabel(lb[1])
            if len(lb) > 2: ax.set_zlabel(lb[2])
        # 2D 子图：隐藏上/右边框，坐标轴改浅灰，向方案A的卡片化绘图靠拢
        for ax in (self.canvas.ax_xy, self.canvas.ax_xz, self.canvas.ax_yz):
            ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
            ax.spines['left'].set_color('#d8dee4'); ax.spines['bottom'].set_color('#d8dee4')
            ax.tick_params(colors='#9aa4ae', labelsize=8)
        # 3D 子图：背景面改浅色、网格变淡
        a3 = self.canvas.ax3d
        for pane in (a3.xaxis.pane, a3.yaxis.pane, a3.zaxis.pane):
            pane.set_facecolor('#fbfcfd'); pane.set_edgecolor('#e6eaee'); pane.set_alpha(1.0)
        a3.tick_params(colors='#9aa4ae', labelsize=7)

        self.canvas.set_titles(self.display_detrended)

        if len(dx) == 0:
            self.canvas.draw()
            return

        sc_params = {'c': dz, 'cmap': 'turbo', 's': 14, 'alpha': 0.85, 'edgecolors': 'none'}
        self.canvas.ax3d.scatter(dx, dy, dz, **sc_params)
        self.canvas.ax_xy.scatter(dx, dy, **sc_params)
        self.canvas.ax_xz.scatter(dx, dz, **sc_params)
        self.canvas.ax_yz.scatter(dy, dz, **sc_params)

        # 3D 视图渲染参考平面：原始模式显示最佳拟合平面；去倾斜模式显示残差零平面
        if self.current_coeffs is not None:
            c = self.current_coeffs
            xx, yy = np.meshgrid(np.linspace(dx.min(), dx.max(), 10), np.linspace(dy.min(), dy.max(), 10))
            if self.display_detrended:
                zz = np.zeros_like(xx)
            else:
                zz = c[0] * xx + c[1] * yy + c[2]
            self.canvas.ax3d.plot_surface(xx, yy, zz, color='#3498db', alpha=0.3, edgecolor='none')

        if self.temp_selected_mask is not None and self.temp_selected_mask.sum() > 0:
            selected_idx = np.where(self.temp_selected_mask)[0]
            if len(selected_idx) > display_limit:
                pick = np.linspace(0, len(selected_idx) - 1, display_limit, dtype=int)
                selected_idx = selected_idx[pick]
            txs, tys, tzs = tx[selected_idx], ty[selected_idx], plot_z_all[selected_idx]
            for ax in [self.canvas.ax_xy, self.canvas.ax_xz, self.canvas.ax_yz]:
                sx, sy = (txs, tys) if ax == self.canvas.ax_xy else (txs, tzs) if ax == self.canvas.ax_xz else (tys, tzs)
                ax.scatter(sx, sy, c='red', s=50, marker='x', linewidth=2)

        self.canvas.draw()

    def on_select(self, eclick, erelease, view_type):
        if self.df_raw is None or self.active_idx is None: return
        x1, y1, x2, y2 = eclick.xdata, eclick.ydata, erelease.xdata, erelease.ydata
        if None in (x1, y1, x2, y2): return

        tx, ty, tz = self.get_final_transformed_data(self.df_raw)
        plot_z_all, _, _ = self._get_plot_z(tx, ty, tz)
        ax, ay, az = tx[self.active_idx], ty[self.active_idx], plot_z_all[self.active_idx]
        if view_type == 'XY': in_box = (ax >= min(x1, x2)) & (ax <= max(x1, x2)) & (ay >= min(y1, y2)) & (ay <= max(y1, y2))
        elif view_type == 'XZ': in_box = (ax >= min(x1, x2)) & (ax <= max(x1, x2)) & (az >= min(y1, y2)) & (az <= max(y1, y2))
        elif view_type == 'YZ': in_box = (ay >= min(x1, x2)) & (ay <= max(x1, x2)) & (az >= min(y1, y2)) & (az <= max(y1, y2))
        else: return
        self.temp_selected_mask.fill(False)
        self.temp_selected_mask[self.active_idx[in_box]] = True
        self.update_plots_only()

    def update_plots_only(self):
        if self.df_raw is None or self.active_idx is None:
            return
        tx, ty, tz = self.get_final_transformed_data(self.df_raw)
        self.draw_plots(tx, ty, tz)

    def setup_selectors(self):
        # 断开旧选择器回调，避免重复触发/内存累积
        for sel in self.selectors:
            try:
                sel.disconnect_events()
            except Exception:
                pass
        self.selectors = []
        for ax, vt in zip([self.canvas.ax_xy, self.canvas.ax_xz, self.canvas.ax_yz], ['XY', 'XZ', 'YZ']):
            sel = RectangleSelector(ax, lambda e, r, v=vt: self.on_select(e, r, v),
                                    useblit=True, button=[1],
                                    props=dict(facecolor='red', alpha=0.15, edgecolor='red'))
            self.selectors.append(sel)

    def apply_manual_deletion(self):
        if self.temp_selected_mask is None or self.temp_selected_mask.sum() == 0: return
        if (self.manual_mask & ~self.temp_selected_mask).sum() < 3:
            QMessageBox.warning(self, "无法删除", "删除后有效点将少于 3 个，无法拟合平面。已取消本次删除。")
            return
        self.manual_mask &= (~self.temp_selected_mask)
        self.temp_selected_mask.fill(False)
        self.update_analysis()

    # ================= 导出 =================
    def save_file(self):
        if self.df_raw is None or self.active_idx is None: return
        path, _ = QFileDialog.getSaveFileName(self, "导出", "Result_Data.csv", "CSV (*.csv)")
        if not path: return
        try:
            tx, ty, tz = self.get_final_transformed_data(self.df_raw)
            fx, fy, fz = tx[self.active_idx], ty[self.active_idx], tz[self.active_idx]

            df_out = pd.DataFrame({
                'X_mm': fx,
                'Y_mm': fy,
                'Z_um': fz * 1000.0,   # 内部 mm -> 导出 µm 必须乘 1000
            })
            if self.current_coeffs is not None:
                c = self.current_coeffs
                df_out['Resid_um'] = (fz - (c[0] * fx + c[1] * fy + c[2])) * 1000.0

            pipeline_text = " -> ".join(self.transform_pipeline) if self.transform_pipeline else "原始状态"
            filter_text = self.cb_filter.currentText()
            if self.cb_filter.currentIndex() == 2:
                filter_text += f" (k={self.spin_k.value()}, 局部阈值={self.spin_thresh.value()}µm, 全局兜底阈值={self.spin_thresh.value()}µm)"
            elif self.cb_filter.currentIndex() == 3:
                filter_text += f" (σ={self.spin_sigma.value()}, 迭代上限={self.spin_sigma_iter.value()})"
            meta = [
                "# ===== 面型及Rxy分析工具 V3.7.0 导出 =====",
                f"# 导出时间: {datetime.now():%Y-%m-%d %H:%M:%S}",
                f"# 数据来源: {self.current_source_name}",
                f"# 变换路径: {pipeline_text}",
                f"# 滤波模式: {filter_text}",
                f"# 滤波剔除点数: {self.n_filtered} | 手动删除点数: {int((~self.manual_mask).sum())} | 导出点数: {len(fz)}",
                f"# 当前显示模式: {'去倾斜残差显示(仅显示/框选)' if self.display_detrended else '原始Z高度显示'}",
                f"# 导入方式: {self.import_info.get('strategy', '--')} | 是否抽样: {self.import_info.get('sampled', False)}",
                f"# 源文件大小: {self.import_info.get('file_size_mb', 0.0):.1f} MB | 读入行数: {self.import_info.get('import_rows', 0)} | 有效点数: {self.import_info.get('valid_rows', len(self.df_raw) if self.df_raw is not None else 0)}",
                f"# 显示上限: {self._display_limit()} 点 | 最近一次绘图显示: {self.last_displayed_points} 点",
            ]
            if self.last_metrics is not None:
                m = self.last_metrics
                meta += [
                    f"# 拟合平面: Z = {m['a']:.6e}*X + {m['b']:.6e}*Y + {m['c']:.6e}  (单位 mm)",
                    f"# Rx = {m['rx']:.2f} µrad | Ry = {m['ry']:.2f} µrad (符号约定需标准件校准)",
                    f"# PV(BF平面法向) = {m['pv']:.3f} µm | TTV(原始Z极差) = {m['ttv']:.3f} µm | 平均Z = {m['mean_z']:.5f} mm",
                ]
            meta.append("# 提示: 用 pandas.read_csv(file, comment='#') 可自动跳过本说明头")

            with open(path, 'w', encoding='utf-8-sig', newline='') as f:
                f.write("\n".join(meta) + "\n")
                df_out.to_csv(f, index=False)
            self.statusBar().showMessage(f"已导出 {len(fz)} 点到 {path}", 5000)
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    # ================= 批量处理 =================
    def _capture_batch_params(self):
        """快照当前界面用于批量处理的参数（与具体文件无关）。
        导入Recipe会先把参数写入界面，所以这里读到的就是Recipe或手动调好的设置。"""
        unit_m = {"mm": 1.0, "µm": 1e-3, "nm": 1e-6}
        mode = self.cb_filter.currentIndex()
        filter_text = self.cb_filter.currentText()
        if mode == 2:
            filter_text += f" (k={self.spin_k.value()}, 阈值={self.spin_thresh.value()}µm)"
        elif mode == 3:
            filter_text += f" (σ={self.spin_sigma.value()}, 迭代上限={self.spin_sigma_iter.value()})"
        pipeline = list(self.transform_pipeline)
        return {
            'x_col': self.cb_x_col.currentText(),
            'y_col': self.cb_y_col.currentText(),
            'z_col': self.cb_z_col.currentText(),
            'x_unit': self.cb_x_unit.currentText(),
            'y_unit': self.cb_y_unit.currentText(),
            'z_unit': self.cb_z_unit.currentText(),
            'ux': unit_m[self.cb_x_unit.currentText()],
            'uy': unit_m[self.cb_y_unit.currentText()],
            'uz': unit_m[self.cb_z_unit.currentText()],
            'pipeline': pipeline,
            'pipeline_text': " -> ".join(pipeline) if pipeline else "原始状态",
            'mode': mode,
            'k': self.spin_k.value(),
            'threshold_mm': self.spin_thresh.value() * 1e-3,
            'sigma_k': self.spin_sigma.value(),
            'sigma_iters': self.spin_sigma_iter.value(),
            'filter_text': filter_text,
            'display_detrended': self.display_detrended,
        }

    def batch_process(self):
        """多选文件批量处理：沿用当前界面(或已导入Recipe)的设置，逐个出报告图+汇总表。"""
        if self.cb_x_col.count() == 0:
            QMessageBox.warning(
                self, "请先配置参数",
                "批量处理会沿用当前界面的列映射/单位/旋转/滤波设置。\n"
                "请先载入其中一个文件、调好参数(或导入Recipe)，再点批量处理。")
            return
        files, _ = QFileDialog.getOpenFileNames(
            self, "批量选择测量数据 (可多选)", "",
            "Data (*.csv *.txt *.tsv *.dat *.asc *.xyz *.xlsx *.xls *.xlsm);;All Files (*)")
        if not files:
            return
        outdir = QFileDialog.getExistingDirectory(self, "选择结果输出文件夹", str(Path(files[0]).parent))
        if not outdir:
            return
        p = self._capture_batch_params()
        confirm = (
            f"将批量处理 {len(files)} 个文件，沿用当前设置：\n\n"
            f"列映射: X={p['x_col']}({p['x_unit']})  Y={p['y_col']}({p['y_unit']})  Z={p['z_col']}({p['z_unit']})\n"
            f"变换路径: {p['pipeline_text']}\n"
            f"滤波模式: {p['filter_text']}\n\n"
            f"每个文件输出: result_<原文件名>.png（含主页面指标+四视图）\n"
            f"另生成: result_batch_summary.csv（指标汇总表）\n"
            f"输出目录: {outdir}\n\n"
            f"注意: 批量仅用自动滤波，不含手动框选删点；\n请确认所有文件为同一设备、同样列格式。")
        if QMessageBox.question(
                self, "确认批量处理", confirm,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return

        results = self._run_batch(files, outdir, p)
        ok = [r for r in results if r['status'] == 'ok']
        fail = [r for r in results if r['status'] != 'ok']
        msg = (f"批量处理完成：成功 {len(ok)} / 失败 {len(fail)}\n"
               f"输出目录：{outdir}\n"
               f"报告图：result_<原文件名>.png\n"
               f"汇总表：result_batch_summary.csv" + ("（本次无成功项，未生成）" if not ok else ""))
        if fail:
            preview = "\n".join(f"  ✗ {r['file']}: {r['error']}" for r in fail[:8])
            if len(fail) > 8:
                preview += f"\n  …其余 {len(fail) - 8} 个失败未列出"
            msg += "\n\n失败清单：\n" + preview
        self.statusBar().showMessage(
            f"批量完成：成功 {len(ok)} / 失败 {len(fail)}，输出至 {outdir}", 10000)
        (QMessageBox.warning if fail else QMessageBox.information)(self, "批量处理结果", msg)

    def _run_batch(self, files, outdir, params):
        """逐文件执行 读入→映射→变换→滤波→拟合→出报告图，并写汇总CSV；返回结果列表。
        批量期间会临时改动 import_info，结束后恢复，避免污染主界面当前视图状态。"""
        saved_info = dict(self.import_info)
        saved_note = self.last_import_note
        results, summary_rows = [], []
        out = Path(outdir)
        try:
            for path in files:
                name = Path(path).name
                try:
                    df_raw = self._read_table(path)
                    import_info_snap = dict(self.import_info)
                    for col in (params['x_col'], params['y_col'], params['z_col']):
                        if col not in df_raw.columns:
                            raise ValueError(f"列 '{col}' 不在文件列 {list(df_raw.columns)[:6]} 中（列格式不一致？）")
                    d = pd.DataFrame({
                        'X': pd.to_numeric(df_raw[params['x_col']], errors='coerce'),
                        'Y': pd.to_numeric(df_raw[params['y_col']], errors='coerce'),
                        'Z': pd.to_numeric(df_raw[params['z_col']], errors='coerce'),
                    }).dropna()
                    if len(d) < 3:
                        raise ValueError("有效数据点少于 3 个")
                    x = d['X'].values * params['ux']
                    y = d['Y'].values * params['uy']
                    z = d['Z'].values * params['uz']
                    x, y, z = self._apply_transform_pipeline(x, y, z, params['pipeline'])
                    n_total = len(z)
                    keep = self.filter_keep_mask(
                        x, y, z, params['mode'],
                        k=params['k'], threshold_mm=params['threshold_mm'],
                        sigma_k=params['sigma_k'], sigma_iters=params['sigma_iters'])
                    if params['mode'] != 0 and keep.sum() < 3:
                        keep = np.ones(n_total, dtype=bool)
                        n_filtered = 0
                    else:
                        n_filtered = int(n_total - keep.sum())
                    active_idx = np.where(keep)[0]
                    fx, fy, fz = x[active_idx], y[active_idx], z[active_idx]
                    metrics = self.compute_plane_metrics(fx, fy, fz)
                    fig = self._render_report_figure(
                        name, x, y, z, active_idx, metrics, n_filtered,
                        params['pipeline_text'], params['filter_text'],
                        import_info_snap, params['display_detrended'])
                    out_png = out / f"result_{Path(path).stem}.png"
                    fig.savefig(str(out_png), dpi=150)
                    results.append({'status': 'ok', 'file': name, 'out': str(out_png)})
                    summary_rows.append({
                        '文件': name, '总点数': n_total, '参与拟合': int(len(active_idx)),
                        '滤波剔除': n_filtered,
                        '平均Z_mm': round(metrics['mean_z'], 6),
                        'PV_um': round(metrics['pv'], 3), 'TTV_um': round(metrics['ttv'], 3),
                        'Rx_urad': round(metrics['rx'], 2), 'Ry_urad': round(metrics['ry'], 2),
                        '平面方程': f"Z={metrics['a']:.4f}X+{metrics['b']:.4f}Y+{metrics['c']:.4f}",
                    })
                except Exception as e:
                    results.append({'status': 'fail', 'file': name, 'error': str(e)})
            if summary_rows:
                pd.DataFrame(summary_rows).to_csv(
                    out / 'result_batch_summary.csv', index=False, encoding='utf-8-sig')
        finally:
            self.import_info = saved_info
            self.last_import_note = saved_note
            self._update_import_status_label()
        return results

    def _render_report_figure(self, source_name, tx, ty, tz, active_idx, metrics,
                              n_filtered, pipeline_text, filter_text,
                              import_info, display_detrended):
        """生成包含主页面全部信息(指标文本 + 四视图)的报告图，返回 Figure(Agg后端)。"""
        # YaHei 同时含中文与 µ(U+00B5)，避免报告图里 µm/µrad 出现缺字方块；其余字体兜底
        plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
        plt.rcParams['axes.unicode_minus'] = False
        fig = Figure(figsize=(17, 9), constrained_layout=True)
        FigureCanvasAgg(fig)
        gs = fig.add_gridspec(2, 3, width_ratios=[1.15, 1.0, 1.0])
        # 左栏再切成 元信息 / 结果卡片 / 脚注 三段，互不重叠
        gs_left = gs[:, 0].subgridspec(20, 1)
        ax_meta = fig.add_subplot(gs_left[0:8, 0]); ax_meta.axis('off')
        ax_res = fig.add_subplot(gs_left[8:17, 0]); ax_res.axis('off')
        ax_foot = fig.add_subplot(gs_left[18:20, 0]); ax_foot.axis('off')
        ax3d = fig.add_subplot(gs[0, 1], projection='3d')
        ax_xy = fig.add_subplot(gs[0, 2])
        ax_xz = fig.add_subplot(gs[1, 1])
        ax_yz = fig.add_subplot(gs[1, 2])

        coeffs = metrics['coeffs']
        # 绘图抽样（与主界面口径一致）；指标仍按全部参与拟合点
        plot_idx = active_idx
        limit = self._display_limit()
        if len(plot_idx) > limit:
            pick = np.linspace(0, len(plot_idx) - 1, limit, dtype=int)
            plot_idx = plot_idx[pick]
        dx, dy = tx[plot_idx], ty[plot_idx]
        if display_detrended:
            plot_z_all = (tz - (coeffs[0] * tx + coeffs[1] * ty + coeffs[2])) * 1000.0
            zlab, ttl3d, txt, tyt = "去倾斜残差 (µm)", "3D 去倾斜残差面型", "X-残差剖面", "Y-残差剖面"
        else:
            plot_z_all = tz
            zlab, ttl3d, txt, tyt = "Z (mm)", "3D 原始高度", "X-Z剖面", "Y-Z剖面"
        dz = plot_z_all[plot_idx]

        sc = {'c': dz, 'cmap': 'turbo', 's': 14, 'alpha': 0.85, 'edgecolors': 'none'}
        ax3d.scatter(dx, dy, dz, **sc)
        ax3d.set_title(ttl3d); ax3d.set_xlabel("X (mm)"); ax3d.set_ylabel("Y (mm)"); ax3d.set_zlabel(zlab)
        m_xy = ax_xy.scatter(dx, dy, **sc); ax_xy.set_title("XY 俯视分布"); ax_xy.set_xlabel("X (mm)"); ax_xy.set_ylabel("Y (mm)")
        ax_xz.scatter(dx, dz, **sc); ax_xz.set_title(txt); ax_xz.set_xlabel("X (mm)"); ax_xz.set_ylabel(zlab)
        ax_yz.scatter(dy, dz, **sc); ax_yz.set_title(tyt); ax_yz.set_xlabel("Y (mm)"); ax_yz.set_ylabel(zlab)
        for ax in (ax_xy, ax_xz, ax_yz):
            ax.grid(True, linestyle=':', alpha=0.5)

        if len(dx) > 0:
            xx, yy = np.meshgrid(np.linspace(dx.min(), dx.max(), 10), np.linspace(dy.min(), dy.max(), 10))
            zz = np.zeros_like(xx) if display_detrended else coeffs[0] * xx + coeffs[1] * yy + coeffs[2]
            ax3d.plot_surface(xx, yy, zz, color='#3498db', alpha=0.3, edgecolor='none')
            # 颜色条：标明散点配色对应的高度/残差量级
            cbar = fig.colorbar(m_xy, ax=[ax_xz, ax_yz], location='bottom',
                                shrink=0.65, aspect=40, pad=0.12)
            cbar.set_label(zlab, fontsize=10)
            cbar.ax.tick_params(labelsize=8)

        # 顶部：元信息（较小字号）
        meta_lines = [
            f"报告时间: {datetime.now():%Y-%m-%d %H:%M:%S}",
            f"数据来源: {source_name}",
            f"导入方式: {import_info.get('strategy', '--')} | 抽样: {import_info.get('sampled', False)}",
            f"源文件大小: {import_info.get('file_size_mb', 0.0):.1f} MB | 读入行: {import_info.get('import_rows', 0)}",
            f"有效点数: {import_info.get('valid_rows', len(tz))} | 参与拟合: {len(active_idx)} | 滤波剔除: {n_filtered}",
            f"变换路径: {pipeline_text}",
            f"滤波模式: {filter_text}",
            f"显示模式: {'去倾斜残差 (µm)' if display_detrended else '原始Z高度 (mm)'}",
        ]
        ax_meta.text(0.02, 0.98, "\n".join(meta_lines), va='top', ha='left',
                     fontsize=10.5, linespacing=1.7, color='#34495e', transform=ax_meta.transAxes)

        # 中部：关键结果卡片（大字号 + 高亮底色，手机上一眼可读）
        results_text = (
            "【分析结果】\n\n"
            f"平面方程   Z = {metrics['a']:.4f}·X + {metrics['b']:.4f}·Y + {metrics['c']:.4f}\n\n"
            f"平均厚度 Z    = {metrics['mean_z']:.5f} mm\n"
            f"面型 PV(法向) = {metrics['pv']:.3f} µm\n"
            f"TTV(Z 极差)   = {metrics['ttv']:.3f} µm\n"
            f"物料 Rx       = {metrics['rx']:.2f} µrad\n"
            f"物料 Ry       = {metrics['ry']:.2f} µrad"
        )
        ax_res.text(0.02, 0.97, results_text, va='top', ha='left',
                    fontsize=13.5, linespacing=1.6, color='#11447a', transform=ax_res.transAxes,
                    bbox=dict(boxstyle='round,pad=0.7', facecolor='#eaf2fb', edgecolor='#3498db', linewidth=1.4))

        # 底部：脚注
        ax_foot.text(0.02, 0.9,
                     "注: Rx≈+dZ/dY, Ry≈-dZ/dX，符号约定需标准件校准。\n"
                     "PV 为相对最佳拟合平面的法向残差极差。批量无手动删点。",
                     va='top', ha='left', fontsize=9, style='italic',
                     color='#7f8c8d', transform=ax_foot.transAxes)

        fig.suptitle(f"面型及Rxy分析报告 (V3.7.0) — {source_name}", fontsize=16, fontweight='bold')
        return fig


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SurfaceAnalyzerPro()
    window.show()
    sys.exit(app.exec())
