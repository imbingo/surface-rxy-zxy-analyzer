# -*- coding: utf-8 -*-
"""
面型及Rxy分析工具 V3.3
基于 V1 的修复与增强：
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
  [增强] 拟合改为中心化 np.linalg.lstsq，对绝对stage大坐标更稳
  [增强] 有效点<3 禁止拟合；滤波后点数不足自动退回未滤波
  [增强] 多层寄存器记录数据来源文件名；计算前弹窗确认；新增一键清空全部寄存器
  [增强] Gap 计算输出匹配质量报告（RMS/Max/唯一匹配比例）
  [增强] Gap 计算后锁定旧文件映射，防止误点"应用映射"覆盖结果
  [增强] 导出 CSV 带元数据头(变换路径/滤波/删点/拟合系数/Rx/Ry/PV/TTV) + 残差列
  [增强] 变换结果缓存，避免框选时全量重算；选择器重建前断开旧回调
注意：Rx/Ry 符号约定 (Rx≈+dZ/dY, Ry≈-dZ/dX) 需用已知倾角标准件实测校准一次。
"""
import sys
from pathlib import Path
from datetime import datetime
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.widgets import RectangleSelector
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QFileDialog, QLabel,
                             QSplitter, QGroupBox, QGridLayout, QMessageBox,
                             QScrollArea, QComboBox, QTabWidget, QDoubleSpinBox,
                             QSpinBox, QCheckBox)
from PyQt6.QtCore import Qt
from scipy.spatial import cKDTree


class MultiViewCanvas(FigureCanvas):
    def __init__(self, parent=None):
        plt.rcParams['font.sans-serif'] = ['SimHei']
        plt.rcParams['axes.unicode_minus'] = False
        self.fig = plt.figure(figsize=(10, 8), constrained_layout=True)
        self.ax3d = self.fig.add_subplot(221, projection='3d')
        self.ax_xy = self.fig.add_subplot(222)
        self.ax_xz = self.fig.add_subplot(223)
        self.ax_yz = self.fig.add_subplot(224)
        super().__init__(self.fig)
        self.setParent(parent)


class SurfaceAnalyzerPro(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("面型及Rxy分析ZXY版 V3.3")
        self.resize(1750, 950)

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
    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.tabs = QTabWidget()
        self.tabs.setFixedWidth(460)
        self.tabs.setStyleSheet("QTabBar::tab { font-weight: bold; padding: 10px; }")

        tab_main = QWidget()
        self.setup_main_tab(tab_main)
        self.tabs.addTab(tab_main, "📊 单层/主控分析")

        tab_math = QWidget()
        self.setup_math_tab(tab_math)
        self.tabs.addTab(tab_math, "🥪 多层胶厚扣减运算")

        # 右侧图窗：顶部放紧凑显示工具条，避免占用左侧主控区空间
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(6, 0, 0, 0)
        right_layout.setSpacing(4)

        view_bar = QWidget()
        view_bar.setFixedHeight(34)
        view_layout = QHBoxLayout(view_bar)
        view_layout.setContentsMargins(8, 0, 8, 0)
        view_layout.setSpacing(10)

        self.chk_detrend_display = QCheckBox("去倾斜显示")
        self.chk_detrend_display.setToolTip(
            "开启后，3D/XZ/YZ图中的Z轴改为：实测Z - 当前最佳拟合平面，单位 µm。\n"
            "用于更清晰观察物料表面面型起伏；只影响显示和框选，不改变Rx/Ry/PV/TTV计算，也不修改原始数据。")
        self.chk_detrend_display.stateChanged.connect(self._on_detrend_display_changed)
        view_layout.addWidget(self.chk_detrend_display)

        self.lbl_detrend_info = QLabel("当前显示：原始Z高度 mm")
        self.lbl_detrend_info.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        view_layout.addWidget(self.lbl_detrend_info)
        view_layout.addStretch()

        self.canvas = MultiViewCanvas(self)
        right_layout.addWidget(view_bar)
        right_layout.addWidget(self.canvas, 1)

        splitter.addWidget(self.tabs)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(1, 4)
        layout.addWidget(splitter)

    def setup_main_tab(self, parent_widget):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        w = QWidget()
        ll = QVBoxLayout(w)

        # 1. 载入与重置
        file_layout = QHBoxLayout()
        self.btn_open = QPushButton("📂 1. 载入测量数据")
        self.btn_open.setFixedHeight(45)
        self.btn_open.setStyleSheet("background-color: #2c3e50; color: white; font-weight: bold; border-radius: 4px;")
        self.btn_open.clicked.connect(self.load_file)

        self.btn_reset_all = QPushButton("♻️ 全部重置")
        self.btn_reset_all.setFixedHeight(45)
        self.btn_reset_all.setStyleSheet("background-color: #e67e22; color: white; border-radius: 4px;")
        self.btn_reset_all.clicked.connect(self.reset_all)
        file_layout.addWidget(self.btn_open); file_layout.addWidget(self.btn_reset_all)
        ll.addLayout(file_layout)

        self.lbl_source = QLabel("当前数据: 未载入")
        self.lbl_source.setStyleSheet("color: #2c3e50; font-weight: bold; font-size: 12px;")
        self.lbl_source.setWordWrap(True)
        ll.addWidget(self.lbl_source)

        # 2. 列映射
        map_group = QGroupBox("🔀 2. 数据解析映射")
        ml = QGridLayout(map_group)
        ml.addWidget(QLabel("X列:"), 0, 0); self.cb_x_col = QComboBox(); ml.addWidget(self.cb_x_col, 0, 1)
        ml.addWidget(QLabel("原单位:"), 0, 2); self.cb_x_unit = QComboBox(); self.cb_x_unit.addItems(["mm", "µm"]); ml.addWidget(self.cb_x_unit, 0, 3)
        ml.addWidget(QLabel("Y列:"), 1, 0); self.cb_y_col = QComboBox(); ml.addWidget(self.cb_y_col, 1, 1)
        ml.addWidget(QLabel("原单位:"), 1, 2); self.cb_y_unit = QComboBox(); self.cb_y_unit.addItems(["mm", "µm"]); ml.addWidget(self.cb_y_unit, 1, 3)
        ml.addWidget(QLabel("Z列:"), 2, 0); self.cb_z_col = QComboBox(); ml.addWidget(self.cb_z_col, 2, 1)
        ml.addWidget(QLabel("原单位:"), 2, 2); self.cb_z_unit = QComboBox(); self.cb_z_unit.addItems(["mm", "µm", "nm"]); ml.addWidget(self.cb_z_unit, 2, 3)
        self.cb_z_unit.setCurrentText("µm")
        self.btn_apply_map = QPushButton("✅ 应用映射并解析数据"); self.btn_apply_map.setFixedHeight(35)
        self.btn_apply_map.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold;")
        self.btn_apply_map.clicked.connect(self.apply_mapping)
        ml.addWidget(self.btn_apply_map, 3, 0, 1, 4)
        ll.addWidget(map_group)

        # 3. 姿态组合
        trans_group = QGroupBox("🔄 3. 物料旋转组合与原点对齐 (点击叠加)")
        tl = QVBoxLayout(trans_group)
        grid_trans = QGridLayout()
        btns = [
            ("顺时针90°", self.add_cw90), ("逆时针90°", self.add_ccw90),
            ("旋转180°", self.add_rot180), ("X-Y轴对调", self.add_swap),
            ("X轴翻转(前后)", self.add_flipx), ("Y轴翻转(左右)", self.add_flipy),
            ("📍 平移归零(0,0)", self.add_origin), ("↩️ 撤销上一步", self.undo_transform)
        ]
        for i, (name, func) in enumerate(btns):
            btn = QPushButton(name); btn.setFixedHeight(30); btn.clicked.connect(func)
            if name == "📍 平移归零(0,0)":
                btn.setStyleSheet("background-color: #3498db; color: white; font-weight: bold;")
            elif name == "↩️ 撤销上一步":
                btn.setStyleSheet("background-color: #95a5a6; color: white; font-weight: bold;")
            grid_trans.addWidget(btn, i // 2, i % 2)

        tl.addLayout(grid_trans)
        self.lbl_pipeline = QLabel("当前变换路径: 原始状态")
        self.lbl_pipeline.setStyleSheet("color: #d35400; font-family: 'Consolas'; font-size: 11px;")
        self.lbl_pipeline.setWordWrap(True)
        tl.addWidget(self.lbl_pipeline)
        ll.addWidget(trans_group)

        # 4. 分析结果
        res_group = QGroupBox("📊 实时分析结果")
        rg = QGridLayout(res_group)
        self.lbl_eqn = QLabel("--"); self.lbl_z = QLabel("--")
        self.lbl_pv = QLabel("--"); self.lbl_ttv = QLabel("--")
        self.lbl_rx = QLabel("--"); self.lbl_ry = QLabel("--")

        val_style = "font-family: 'Consolas'; font-size: 14px; color: #1e88e5; font-weight: bold;"
        for lbl in [self.lbl_eqn, self.lbl_z, self.lbl_pv, self.lbl_ttv, self.lbl_rx, self.lbl_ry]:
            lbl.setStyleSheet(val_style)

        rg.addWidget(QLabel("平面方程:"), 0, 0); rg.addWidget(self.lbl_eqn, 0, 1)
        rg.addWidget(QLabel("平均厚度 Z (mm):"), 1, 0); rg.addWidget(self.lbl_z, 1, 1)
        lbl_pv_name = QLabel("面型 PV·BF平面法向 (µm):")
        lbl_pv_name.setToolTip("相对最佳拟合平面的法向残差 PV，不是原始高度 PV")
        rg.addWidget(lbl_pv_name, 2, 0); rg.addWidget(self.lbl_pv, 2, 1)
        lbl_ttv_name = QLabel("TTV·原始Z极差 (µm):")
        lbl_ttv_name.setToolTip("原始 Z 最大值 - 最小值（未去平面）")
        rg.addWidget(lbl_ttv_name, 3, 0); rg.addWidget(self.lbl_ttv, 3, 1)
        lbl_rx_name = QLabel("物料 Rx (µrad):")
        lbl_rx_name.setToolTip("Rx ≈ +dZ/dY。符号约定需用已知倾角标准件实测校准一次！")
        rg.addWidget(lbl_rx_name, 4, 0); rg.addWidget(self.lbl_rx, 4, 1)
        lbl_ry_name = QLabel("物料 Ry (µrad):")
        lbl_ry_name.setToolTip("Ry ≈ -dZ/dX。符号约定需用已知倾角标准件实测校准一次！")
        rg.addWidget(lbl_ry_name, 5, 0); rg.addWidget(self.lbl_ry, 5, 1)
        ll.addWidget(res_group)

        # 5. 滤波区
        flt_group = QGroupBox("🛡 异常点滤波")
        fl = QGridLayout(flt_group)
        fl.addWidget(QLabel("模式:"), 0, 0)
        self.cb_filter = QComboBox()
        self.cb_filter.addItems(["关闭", "MAD 全局鲁棒滤波", "局部中位数滤波 (邻域比较)"])
        self.cb_filter.setToolTip(
            "MAD全局: 对拟合残差做鲁棒3.5σ判定，适合零散毛刺。\n"
            "局部中位数: 每个点与其 k 个最近邻的残差中位数比较，偏离超过阈值判为异常。\n"
            "  适合已知面型上限的场景（如已知面型≤5µm 则阈值设5）。\n"
            "  单个离群点不会误杀周围正常点（中位数对少数坏邻居不敏感）。")
        fl.addWidget(self.cb_filter, 0, 1, 1, 3)
        fl.addWidget(QLabel("邻居数 k:"), 1, 0)
        self.spin_k = QSpinBox(); self.spin_k.setRange(3, 50); self.spin_k.setValue(12)
        self.spin_k.setToolTip("k 应大于可能成簇坏点数的 2 倍；坏点成片时调大 k")
        fl.addWidget(self.spin_k, 1, 1)
        fl.addWidget(QLabel("阈值 (µm):"), 1, 2)
        self.spin_thresh = QDoubleSpinBox()
        self.spin_thresh.setDecimals(2); self.spin_thresh.setRange(0.01, 10000.0)
        self.spin_thresh.setValue(5.00); self.spin_thresh.setSingleStep(0.5)
        self.spin_thresh.setToolTip("局部中位数模式的判异阈值，建议设为已知面型/噪声上限")
        fl.addWidget(self.spin_thresh, 1, 3)
        self.lbl_filter_info = QLabel("滤波剔除: 0 点 | 手动删除: 0 点")
        self.lbl_filter_info.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        fl.addWidget(self.lbl_filter_info, 2, 0, 1, 4)
        self.cb_filter.currentIndexChanged.connect(self.update_analysis)
        self.spin_k.valueChanged.connect(self._on_filter_param_changed)
        self.spin_thresh.valueChanged.connect(self._on_filter_param_changed)
        ll.addWidget(flt_group)

        ll.addStretch()

        self.btn_del = QPushButton("🗑 确认删除已框选点")
        self.btn_del.setFixedHeight(45); self.btn_del.setStyleSheet("background-color: #c62828; color: white; font-weight: bold;")
        self.btn_del.clicked.connect(self.apply_manual_deletion)
        ll.addWidget(self.btn_del)
        self.btn_save = QPushButton("💾 导出最终 CSV 数据 (含元数据头)")
        self.btn_save.setFixedHeight(40); self.btn_save.clicked.connect(self.save_file)
        ll.addWidget(self.btn_save)

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
        btn_set_stack.clicked.connect(lambda: self.set_memory_slot('stack'))
        gl_stack.addWidget(self.lbl_stack_status); gl_stack.addWidget(btn_set_stack)
        ll.addWidget(grp_stack)

        grp_base1 = QGroupBox("2️⃣ 单片 1 数据 (Base 1 / 底层)")
        gl_base1 = QVBoxLayout(grp_base1)
        self.lbl_base1_status = QLabel("❌ 尚未设置"); self.lbl_base1_status.setStyleSheet("color: #c0392b; font-weight: bold;")
        self.lbl_base1_status.setWordWrap(True)
        btn_set_base1 = QPushButton("👇 将当前视图设为【单片 1】"); btn_set_base1.setFixedHeight(35)
        btn_set_base1.clicked.connect(lambda: self.set_memory_slot('base1'))
        gl_base1.addWidget(self.lbl_base1_status); gl_base1.addWidget(btn_set_base1)
        ll.addWidget(grp_base1)

        grp_base2 = QGroupBox("3️⃣ 单片 2 数据 (Base 2 / 夹层) [选填]")
        gl_base2 = QVBoxLayout(grp_base2)
        self.lbl_base2_status = QLabel("⭕ 可选空置"); self.lbl_base2_status.setStyleSheet("color: #7f8c8d; font-weight: bold;")
        self.lbl_base2_status.setWordWrap(True)
        hl_b2 = QHBoxLayout()
        btn_set_base2 = QPushButton("📥 设为【单片 2】"); btn_set_base2.setFixedHeight(35); btn_set_base2.clicked.connect(lambda: self.set_memory_slot('base2'))
        btn_clear_base2 = QPushButton("✖ 清除"); btn_clear_base2.setFixedHeight(35); btn_clear_base2.setStyleSheet("background-color: #bdc3c7;")
        btn_clear_base2.clicked.connect(lambda: self.clear_memory_slot('base2'))
        hl_b2.addWidget(btn_set_base2); hl_b2.addWidget(btn_clear_base2)
        gl_base2.addWidget(self.lbl_base2_status); gl_base2.addLayout(hl_b2)
        ll.addWidget(grp_base2)

        btn_clear_all = QPushButton("🧹 清空全部寄存器")
        btn_clear_all.setFixedHeight(35)
        btn_clear_all.setStyleSheet("background-color: #95a5a6; color: white; font-weight: bold;")
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

        self.btn_calc_gap = QPushButton("📐 容差匹配点云并计算胶厚")
        self.btn_calc_gap.setFixedHeight(60)
        self.btn_calc_gap.setStyleSheet("background-color: #8e44ad; color: white; font-weight: bold; font-size: 14px; border-radius: 6px;")
        self.btn_calc_gap.clicked.connect(self.calculate_gap)
        ll.addWidget(self.btn_calc_gap)

        scroll.setWidget(w)
        parent_widget.setLayout(QVBoxLayout())
        parent_widget.layout().addWidget(scroll)
        parent_widget.layout().setContentsMargins(0, 0, 0, 0)

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
    def local_median_filter(x, y, resids, k=12, threshold_mm=0.005):
        """局部中位数滤波（邻域比较）。
        每个点与其 k 个最近邻（不含自身）的残差中位数比较，
        偏离超过 threshold 判为异常。
        中位数要被拉偏需要超过一半邻居都是坏点，
        因此单个离群点既会被自己的正常邻居揪出来，
        也不会污染相邻正常点的判定 —— 不存在连锁误杀。"""
        n = len(resids)
        kk = min(k, n - 1)
        if kk < 1:
            return np.ones(n, dtype=bool)
        tree = cKDTree(np.column_stack([x, y]))
        _, idx = tree.query(np.column_stack([x, y]), k=kk + 1)
        if idx.ndim == 1:
            idx = idx[:, None]
        local_med = np.median(resids[idx[:, 1:]], axis=1)
        return np.abs(resids - local_med) <= threshold_mm

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
        self.lbl_detrend_info.setText("当前显示：原始Z高度 mm")
        self.update_analysis()
        self.statusBar().showMessage("系统已完全重置（滤波已关闭，去倾斜显示已关闭）", 3000)

    # ================= 文件载入与映射 =================
    TEXT_SUFFIXES = ('.csv', '.txt', '.tsv', '.dat', '.asc', '.xyz')
    EXCEL_SUFFIXES = ('.xlsx', '.xls', '.xlsm')

    def _read_table(self, path):
        """鲁棒读取表格文件：
        - 文本类(.csv/.txt/.tsv/.dat/.asc/.xyz): 自动嗅探分隔符(逗号/制表符/分号/空格)，
          依次尝试 utf-8-sig / gbk / utf-16 / latin-1 编码，自动跳过#注释行、空行、坏行
        - Excel类(.xlsx/.xls/.xlsm): pd.read_excel
        - 列名统一清理 BOM 与首尾空白（修复 utf-8-sig 文件列名带 \\ufeff 导致的解析失败）
        - 自动识别无表头文件（首行即数据时列名命名为 Col1..ColN）"""
        suffix = Path(path).suffix.lower()
        used_enc, used_sep = None, None
        if suffix in self.EXCEL_SUFFIXES:
            df = pd.read_excel(path)
        elif suffix in self.TEXT_SUFFIXES or suffix == '':
            df, last_err = None, None
            # sep=None 自动嗅探优先；嗅探失败(如被#注释头干扰)时回退到常见分隔符
            for enc in ('utf-8-sig', 'gbk', 'utf-16', 'latin-1'):
                for sep in (None, ',', '\t', ';', r'\s+'):
                    try:
                        df_try = pd.read_csv(path, sep=sep, engine='python', encoding=enc,
                                             comment='#', skip_blank_lines=True, on_bad_lines='skip')
                        if df_try.shape[1] >= 2:
                            df, used_enc, used_sep = df_try, enc, sep
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
        df.columns = [str(c).replace('﻿', '').strip() for c in df.columns]

        # 无表头检测: 如果所有列名都是数字，说明首行其实是数据，重读
        def _is_num(s):
            try:
                float(str(s)); return True
            except (TypeError, ValueError):
                return False
        if df.shape[1] >= 2 and all(_is_num(c) for c in df.columns):
            if suffix in self.EXCEL_SUFFIXES:
                df = pd.read_excel(path, header=None)
            else:
                df = pd.read_csv(path, sep=used_sep, engine='python', encoding=used_enc,
                                 comment='#', skip_blank_lines=True, on_bad_lines='skip',
                                 header=None)
            df.columns = [f'Col{i+1}' for i in range(df.shape[1])]

        if df.empty or df.shape[1] < 2:
            raise ValueError("文件内容为空或有效列少于 2 列，请检查文件。")
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

            self.cb_x_col.setCurrentIndex(guess_index('x', 1))
            self.cb_y_col.setCurrentIndex(guess_index('y', 2))
            self.cb_z_col.setCurrentIndex(guess_index('z', 0))
            self.apply_mapping()

            # 寄存器保留提示（多层流程需要跨文件保留，故不自动清空）
            if any(s is not None for s in (self.data_stack, self.data_base1, self.data_base2)):
                self.statusBar().showMessage(
                    "⚠ 提示: 多层寄存器仍保留之前的数据，如属不同物料请到[多层]页点击 [🧹 清空全部寄存器]", 10000)
        except Exception as e:
            QMessageBox.critical(self, "导入失败", str(e))

    def apply_mapping(self):
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
            self._df_version += 1
            self.reset_all()
        except Exception as e:
            QMessageBox.critical(self, "解析失败", str(e))

    # ================= 坐标变换（含缓存） =================
    def get_final_transformed_data(self, df):
        """基于包围盒的姿态变换。
        旋转/翻转均以数据包围盒为参照，坐标不从0开始也不会产生偏移。
        90°旋转采用【物料旋转】语义，而非坐标系/视图旋转：
          - CW90：物料顺时针转90°，顶部点转到右侧
          - CCW90：物料逆时针转90°，顶部点转到左侧
        结果带缓存。"""
        key = (self._df_version, tuple(self.transform_pipeline))
        if self._trans_cache_key == key:
            return self._trans_cache_data

        x, y, z = df['X'].values.copy(), df['Y'].values.copy(), df['Z'].values.copy()

        for action in self.transform_pipeline:
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
        # 仅局部中位数模式下参数变化才需要重算
        if self.cb_filter.currentIndex() == 2:
            self.update_analysis()

    def _on_detrend_display_changed(self):
        self.display_detrended = self.chk_detrend_display.isChecked()
        if self.display_detrended:
            self.lbl_detrend_info.setText("当前显示：去倾斜残差 µm（指标不变）")
        else:
            self.lbl_detrend_info.setText("当前显示：原始Z高度 mm")
        if self.df_raw is not None and self.active_idx is not None:
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

            # 1. 初始拟合 + 滤波
            c0 = self.fit_plane(xb, yb, zb)
            resids = zb - (c0[0] * xb + c0[1] * yb + c0[2])

            mode = self.cb_filter.currentIndex()
            self.n_filtered = 0
            if mode == 0 or len(idx) <= 10:
                self.active_idx = idx
            else:
                if mode == 1:
                    keep = self.mad_filter(resids, k=3.5)
                else:
                    keep = self.local_median_filter(
                        xb, yb, resids,
                        k=self.spin_k.value(),
                        threshold_mm=self.spin_thresh.value() * 1e-3)
                if keep.sum() < 3:
                    self.statusBar().showMessage("⚠ 滤波后点数不足 3 个，已自动退回未滤波状态。请调整阈值/k。", 10000)
                    self.active_idx = idx
                else:
                    self.active_idx = idx[keep]
                    self.n_filtered = int(len(idx) - keep.sum())

            self.lbl_filter_info.setText(
                f"滤波剔除: {self.n_filtered} 点 | 手动删除: {int((~self.manual_mask).sum())} 点 | 参与拟合: {len(self.active_idx)} 点")

            fx, fy, fz = tx[self.active_idx], ty[self.active_idx], tz[self.active_idx]

            # 2. 最终拟合与指标
            c = self.fit_plane(fx, fy, fz)
            self.current_coeffs = c

            mean_z = np.mean(fz)
            ttv = (np.max(fz) - np.min(fz)) * 1000

            res_z = fz - (c[0] * fx + c[1] * fy + c[2])
            normal_factor = np.sqrt(c[0] ** 2 + c[1] ** 2 + 1.0)
            res_normal = res_z / normal_factor
            pv = (np.max(res_normal) - np.min(res_normal)) * 1000

            rx = np.arctan(c[1]) * 1e6
            ry = np.arctan(-c[0]) * 1e6

            self.last_metrics = {'a': c[0], 'b': c[1], 'c': c[2],
                                 'mean_z': mean_z, 'pv': pv, 'ttv': ttv,
                                 'rx': rx, 'ry': ry}

            # UI 更新
            self.lbl_eqn.setText(f"Z={c[0]:.4f}X+{c[1]:.4f}Y+{c[2]:.4f}")
            self.lbl_z.setText(f"{mean_z:.5f} mm")
            self.lbl_pv.setText(f"{pv:.3f} µm"); self.lbl_ttv.setText(f"{ttv:.3f} µm")
            self.lbl_rx.setText(f"{rx:.2f} µrad"); self.lbl_ry.setText(f"{ry:.2f} µrad")
            self.draw_plots(tx, ty, tz)
            self.setup_selectors()
        except Exception as e:
            self.statusBar().showMessage(f"⚠ 分析出错: {e}", 10000)

    # ================= 绘图与交互 =================
    def draw_plots(self, tx, ty, tz):
        dx, dy = tx[self.active_idx], ty[self.active_idx]
        plot_z_all, z_axis_label, z_short_label = self._get_plot_z(tx, ty, tz)
        dz = plot_z_all[self.active_idx]

        axes = [self.canvas.ax3d, self.canvas.ax_xy, self.canvas.ax_xz, self.canvas.ax_yz]
        lbs = [("X (mm)", "Y (mm)", z_axis_label),
               ("X (mm)", "Y (mm)"),
               ("X (mm)", z_axis_label),
               ("Y (mm)", z_axis_label)]
        for ax, lb in zip(axes, lbs):
            ax.clear(); ax.grid(True, linestyle=':', alpha=0.5)
            ax.set_xlabel(lb[0]); ax.set_ylabel(lb[1])
            if len(lb) > 2: ax.set_zlabel(lb[2])

        if self.display_detrended:
            self.canvas.ax3d.set_title("3D 去倾斜残差面型")
            self.canvas.ax_xz.set_title("X-残差剖面")
            self.canvas.ax_yz.set_title("Y-残差剖面")
        else:
            self.canvas.ax3d.set_title("3D 原始高度")
            self.canvas.ax_xz.set_title("X-Z剖面")
            self.canvas.ax_yz.set_title("Y-Z剖面")

        if len(dx) == 0:
            self.canvas.draw()
            return

        sc_params = {'c': dz, 'cmap': 'turbo', 's': 20, 'alpha': 0.8}
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
            txs, tys, tzs = tx[self.temp_selected_mask], ty[self.temp_selected_mask], plot_z_all[self.temp_selected_mask]
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
                filter_text += f" (k={self.spin_k.value()}, 阈值={self.spin_thresh.value()}µm)"
            meta = [
                "# ===== 面型及Rxy分析工具 V3.3 导出 =====",
                f"# 导出时间: {datetime.now():%Y-%m-%d %H:%M:%S}",
                f"# 数据来源: {self.current_source_name}",
                f"# 变换路径: {pipeline_text}",
                f"# 滤波模式: {filter_text}",
                f"# 滤波剔除点数: {self.n_filtered} | 手动删除点数: {int((~self.manual_mask).sum())} | 导出点数: {len(fz)}",
                f"# 当前显示模式: {'去倾斜残差显示(仅显示/框选)' if self.display_detrended else '原始Z高度显示'}",
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


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SurfaceAnalyzerPro()
    window.show()
    sys.exit(app.exec())
