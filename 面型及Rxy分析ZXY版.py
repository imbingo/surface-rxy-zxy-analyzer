import sys
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.widgets import RectangleSelector
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QFileDialog, QLabel, 
                             QSplitter, QGroupBox, QGridLayout, QMessageBox, 
                             QScrollArea, QComboBox, QTabWidget, QDoubleSpinBox)
from PyQt6.QtCore import Qt
from scipy.optimize import lsq_linear
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
        self.setWindowTitle("面型及Rxy分析ZXY版V1")
        self.resize(1750, 950)

        # 数据流
        self.absolute_raw_df = None 
        self.df_raw = None          
        self.manual_mask = None
        self.sigma_active = False 
        self.temp_selected_mask = None
        self.active_idx = None
        self.transform_pipeline = [] 
        self.current_coeffs = None  # 新增：用于存储当前拟合平面的系数

        # 多层计算寄存器
        self.data_base1 = None   
        self.data_base2 = None
        self.data_stack = None  

        self.init_ui()

    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # --- 左侧选项卡 ---
        self.tabs = QTabWidget()
        self.tabs.setFixedWidth(460)
        self.tabs.setStyleSheet("QTabBar::tab { font-weight: bold; padding: 10px; }")

        tab_main = QWidget()
        self.setup_main_tab(tab_main)
        self.tabs.addTab(tab_main, "📊 单层/主控分析")

        tab_math = QWidget()
        self.setup_math_tab(tab_math)
        self.tabs.addTab(tab_math, "🥪 多层胶厚扣减运算")

        # --- 右侧四分图 ---
        self.canvas = MultiViewCanvas(self)
        
        splitter.addWidget(self.tabs)
        splitter.addWidget(self.canvas)
        splitter.setStretchFactor(1, 4)
        layout.addWidget(splitter)
        self.selectors = []

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
        trans_group = QGroupBox("🔄 3. 姿态组合与原点对齐 (点击叠加)")
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
        for lbl in [self.lbl_eqn, self.lbl_z, self.lbl_pv, self.lbl_ttv, self.lbl_rx, self.lbl_ry]: lbl.setStyleSheet(val_style)
            
        rg.addWidget(QLabel("平面方程:"), 0, 0); rg.addWidget(self.lbl_eqn, 0, 1)
        rg.addWidget(QLabel("平均厚度 Z (mm):"), 1, 0); rg.addWidget(self.lbl_z, 1, 1)
        rg.addWidget(QLabel("面型 PV (µm):"), 2, 0); rg.addWidget(self.lbl_pv, 2, 1)
        rg.addWidget(QLabel("全厚差 TTV (µm):"), 3, 0); rg.addWidget(self.lbl_ttv, 3, 1)
        rg.addWidget(QLabel("物料 Rx (µrad):"), 4, 0); rg.addWidget(self.lbl_rx, 4, 1)
        rg.addWidget(QLabel("物料 Ry (µrad):"), 5, 0); rg.addWidget(self.lbl_ry, 5, 1)
        ll.addWidget(res_group)

        ll.addStretch()
        
        # 5. 滤波区
        self.btn_sigma = QPushButton("🛡 3-Sigma 自动滤波: 关闭")
        self.btn_sigma.setCheckable(True); self.btn_sigma.setFixedHeight(40); self.btn_sigma.clicked.connect(self.toggle_sigma)
        ll.addWidget(self.btn_sigma)
        self.btn_del = QPushButton("🗑 确认删除已框选点")
        self.btn_del.setFixedHeight(45); self.btn_del.setStyleSheet("background-color: #c62828; color: white; font-weight: bold;")
        self.btn_del.clicked.connect(self.apply_manual_deletion)
        ll.addWidget(self.btn_del)
        self.btn_save = QPushButton("💾 导出最终 CSV 数据")
        self.btn_save.setFixedHeight(40); self.btn_save.clicked.connect(self.save_file)
        ll.addWidget(self.btn_save)

        scroll.setWidget(w)
        parent_widget.setLayout(QVBoxLayout())
        parent_widget.layout().addWidget(scroll)
        parent_widget.layout().setContentsMargins(0,0,0,0)

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
            "2. 依次载入不同层数据并存入下方对应的寄存器中。<br>"
            "3. 【对齐误差窗口】：用于补偿机台定位偏差。容差越大，匹配到的点数越多，但过大可能匹配到错误邻居。"
            "</div>"
        )
        guide_lbl.setWordWrap(True)
        ll.addWidget(guide_lbl)

        # 寄存器区
        grp_stack = QGroupBox("1️⃣ 堆叠总成数据 (Stack / 顶层)")
        gl_stack = QVBoxLayout(grp_stack)
        self.lbl_stack_status = QLabel("❌ 尚未设置"); self.lbl_stack_status.setStyleSheet("color: #c0392b; font-weight: bold;")
        btn_set_stack = QPushButton("👆 将当前视图设为【堆叠总成】"); btn_set_stack.setFixedHeight(35)
        btn_set_stack.clicked.connect(lambda: self.set_memory_slot('stack'))
        gl_stack.addWidget(self.lbl_stack_status); gl_stack.addWidget(btn_set_stack)
        ll.addWidget(grp_stack)

        grp_base1 = QGroupBox("2️⃣ 单片 1 数据 (Base 1 / 底层)")
        gl_base1 = QVBoxLayout(grp_base1)
        self.lbl_base1_status = QLabel("❌ 尚未设置"); self.lbl_base1_status.setStyleSheet("color: #c0392b; font-weight: bold;")
        btn_set_base1 = QPushButton("👇 将当前视图设为【单片 1】"); btn_set_base1.setFixedHeight(35)
        btn_set_base1.clicked.connect(lambda: self.set_memory_slot('base1'))
        gl_base1.addWidget(self.lbl_base1_status); gl_base1.addWidget(btn_set_base1)
        ll.addWidget(grp_base1)

        grp_base2 = QGroupBox("3️⃣ 单片 2 数据 (Base 2 / 夹层) [选填]")
        gl_base2 = QVBoxLayout(grp_base2)
        self.lbl_base2_status = QLabel("⭕ 可选空置"); self.lbl_base2_status.setStyleSheet("color: #7f8c8d; font-weight: bold;")
        hl_b2 = QHBoxLayout()
        btn_set_base2 = QPushButton("📥 设为【单片 2】"); btn_set_base2.setFixedHeight(35); btn_set_base2.clicked.connect(lambda: self.set_memory_slot('base2'))
        btn_clear_base2 = QPushButton("✖ 清除"); btn_clear_base2.setFixedHeight(35); btn_clear_base2.setStyleSheet("background-color: #bdc3c7;")
        btn_clear_base2.clicked.connect(lambda: self.clear_memory_slot('base2'))
        hl_b2.addWidget(btn_set_base2); hl_b2.addWidget(btn_clear_base2)
        gl_base2.addWidget(self.lbl_base2_status); gl_base2.addLayout(hl_b2)
        ll.addWidget(grp_base2)

        ll.addStretch()

        # 对齐容差输入框
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

        # 计算按钮
        self.btn_calc_gap = QPushButton("📐 容差匹配点云并计算胶厚")
        self.btn_calc_gap.setFixedHeight(60)
        self.btn_calc_gap.setStyleSheet("background-color: #8e44ad; color: white; font-weight: bold; font-size: 14px; border-radius: 6px;")
        self.btn_calc_gap.clicked.connect(self.calculate_gap)
        ll.addWidget(self.btn_calc_gap)

        scroll.setWidget(w)
        parent_widget.setLayout(QVBoxLayout())
        parent_widget.layout().addWidget(scroll)
        parent_widget.layout().setContentsMargins(0,0,0,0)

    # --- 撤销操作逻辑 ---
    def undo_transform(self):
        if self.transform_pipeline:
            action = self.transform_pipeline.pop()
            self.update_analysis()
            self.statusBar().showMessage(f"已撤销操作: {action}", 3000)
        else:
            QMessageBox.information(self, "提示", "已经退回原始状态，没有可以撤销的操作了。")

    # --- 数据寄存与胶厚计算逻辑 ---
    def set_memory_slot(self, slot):
        if self.df_raw is None:
            QMessageBox.warning(self, "错误", "主界面尚无数据！请先载入并处理。")
            return
        
        tx, ty, tz = self.get_final_transformed_data(self.df_raw)
        fx, fy, fz = tx[self.active_idx], ty[self.active_idx], tz[self.active_idx]
        
        if slot == 'stack':
            self.data_stack = (fx, fy, fz)
            self.lbl_stack_status.setText(f"✅ 已存【堆叠总成】(共 {len(fz)} 点)")
            self.lbl_stack_status.setStyleSheet("color: #27ae60; font-weight: bold;")
        elif slot == 'base1':
            self.data_base1 = (fx, fy, fz)
            self.lbl_base1_status.setText(f"✅ 已存【单片 1】(共 {len(fz)} 点)")
            self.lbl_base1_status.setStyleSheet("color: #27ae60; font-weight: bold;")
        elif slot == 'base2':
            self.data_base2 = (fx, fy, fz)
            self.lbl_base2_status.setText(f"✅ 已存【单片 2】(共 {len(fz)} 点)")
            self.lbl_base2_status.setStyleSheet("color: #2980b9; font-weight: bold;")

    def clear_memory_slot(self, slot):
        if slot == 'base2':
            self.data_base2 = None
            self.lbl_base2_status.setText("⭕ 可选空置")
            self.lbl_base2_status.setStyleSheet("color: #7f8c8d; font-weight: bold;")

    def calculate_gap(self):
        if self.data_stack is None or self.data_base1 is None:
            QMessageBox.critical(self, "数据缺失", "执行运算至少需要设置【堆叠总成】和【单片 1】！")
            return

        try:
            sx, sy, sz = self.data_stack
            b1x, b1y, b1z = self.data_base1
            tolerance = self.spin_tol.value()
            
            tree1 = cKDTree(np.column_stack([b1x, b1y]))
            dist1, idx1 = tree1.query(np.column_stack([sx, sy]), distance_upper_bound=tolerance)
            valid1 = dist1 <= tolerance 
            
            if self.data_base2 is not None:
                b2x, b2y, b2z = self.data_base2
                tree2 = cKDTree(np.column_stack([b2x, b2y]))
                dist2, idx2 = tree2.query(np.column_stack([sx, sy]), distance_upper_bound=tolerance)
                valid2 = dist2 <= tolerance
                
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
                raise ValueError("容差范围内配对成功的有效点不足！\n请尝试增大【误差窗口】数值，或检查两组数据是否都执行了[平移归零]。")

            self.df_raw = pd.DataFrame({'Z': final_gap_z, 'X': final_sx, 'Y': final_sy})
            self.transform_pipeline = []
            self.manual_mask = np.ones(len(self.df_raw), dtype=bool)
            self.temp_selected_mask = np.zeros(len(self.df_raw), dtype=bool)
            self.current_coeffs = None
            
            self.tabs.setCurrentIndex(0)
            self.update_analysis()
            
            msg = f"成功配对并算出 Inner Gap！\n容差设定: {tolerance} mm\n成功对齐点数: {len(final_gap_z)}\n公式: 堆叠总成 - 单片1"
            if self.data_base2: msg += " - 单片2"
            QMessageBox.information(self, "计算成功", msg)

        except Exception as e:
            QMessageBox.critical(self, "运算失败", f"点云对齐错误: {str(e)}")

    # --- 变换流水线 ---
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
        self.update_analysis()
        self.statusBar().showMessage("系统已完全重置", 3000)

    def load_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "载入数据", "", "Data (*.csv *.xlsx *.xls *.txt)")
        if not path: return
        try:
            if path.endswith('.csv') or path.endswith('.txt'):
                self.absolute_raw_df = pd.read_csv(path, sep=None, engine='python')
            else:
                self.absolute_raw_df = pd.read_excel(path)
            
            cols = [str(c) for c in self.absolute_raw_df.columns]
            for cb in [self.cb_x_col, self.cb_y_col, self.cb_z_col]:
                cb.clear(); cb.addItems(cols)

            def guess_index(tc, di):
                for i, col in enumerate(cols):
                    if tc.lower() in col.lower(): return i
                return di if di < len(cols) else 0

            self.cb_x_col.setCurrentIndex(guess_index('x', 1))
            self.cb_y_col.setCurrentIndex(guess_index('y', 2))
            self.cb_z_col.setCurrentIndex(guess_index('z', 0))
            self.apply_mapping()
        except Exception as e:
            QMessageBox.critical(self, "导入失败", str(e))

    def apply_mapping(self):
        if self.absolute_raw_df is None: return
        try:
            xc, yc, zc = self.cb_x_col.currentText(), self.cb_y_col.currentText(), self.cb_z_col.currentText()
            temp_df = pd.DataFrame()
            temp_df['X'] = pd.to_numeric(self.absolute_raw_df[xc], errors='coerce')
            temp_df['Y'] = pd.to_numeric(self.absolute_raw_df[yc], errors='coerce')
            temp_df['Z'] = pd.to_numeric(self.absolute_raw_df[zc], errors='coerce')
            temp_df = temp_df.dropna()

            unit_m = {"mm": 1.0, "µm": 1e-3, "nm": 1e-6}
            temp_df['X'] = temp_df['X'] * unit_m[self.cb_x_unit.currentText()]
            temp_df['Y'] = temp_df['Y'] * unit_m[self.cb_y_unit.currentText()]
            temp_df['Z'] = temp_df['Z'] * unit_m[self.cb_z_unit.currentText()]

            self.df_raw = temp_df[['Z', 'X', 'Y']]
            self.reset_all()
        except Exception as e:
            QMessageBox.critical(self, "解析失败", str(e))

    def get_final_transformed_data(self, df):
        x, y, z = df['X'].values.copy(), df['Y'].values.copy(), df['Z'].values.copy()
        
        for action in self.transform_pipeline:
            mx, my = np.max(x), np.max(y)
            if action == "ROT180": x, y = mx - x, my - y
            elif action == "CW90": x, y = my - y, x
            elif action == "CCW90": x, y = y, mx - x
            elif action == "SWAP": x, y = y, x
            elif action == "FLIPX": y = my - y
            elif action == "FLIPY": x = mx - x
            elif action == "ORIGIN(0,0)":
                x = x - np.min(x)
                y = y - np.min(y)
        
        path_text = "原始状态"
        if self.transform_pipeline: path_text += " -> " + " -> ".join(self.transform_pipeline)
        self.lbl_pipeline.setText(f"变换路径: {path_text}")
        return x, y, z

    def toggle_sigma(self):
        self.sigma_active = self.btn_sigma.isChecked()
        self.btn_sigma.setText(f"🛡 3-Sigma 自动滤波: {'开启' if self.sigma_active else '关闭'}")
        self.btn_sigma.setStyleSheet(f"background-color: {'#d4edda' if self.sigma_active else '#f8d7da'}; font-weight: bold;")
        self.update_analysis()

    def update_analysis(self):
        if self.df_raw is None: return
        tx, ty, tz = self.get_final_transformed_data(self.df_raw)
        idx = np.where(self.manual_mask)[0]
        xb, yb, zb = tx[idx], ty[idx], tz[idx]
        
        # 1. 初始拟合
        res_f = lsq_linear(np.column_stack([xb, yb, np.ones_like(xb)]), zb).x
        if self.sigma_active and len(idx) > 10:
            resids = zb - (res_f[0]*xb + res_f[1]*yb + res_f[2])
            keep = np.abs(resids - np.mean(resids)) <= 3 * np.std(resids)
            self.active_idx = idx[keep]
        else:
            self.active_idx = idx

        fx, fy, fz = tx[self.active_idx], ty[self.active_idx], tz[self.active_idx]
        
        # 2. 最终拟合与极致严谨的物理指标计算
        c = lsq_linear(np.column_stack([fx, fy, np.ones_like(fx)]), fz).x
        self.current_coeffs = c # 存储系数用于 3D 画平面
        
        mean_z = np.mean(fz) 
        ttv = (np.max(fz) - np.min(fz)) * 1000
        
        res_z = fz - (c[0]*fx + c[1]*fy + c[2])
        normal_factor = np.sqrt(c[0]**2 + c[1]**2 + 1.0)
        res_normal = res_z / normal_factor
        pv = (np.max(res_normal) - np.min(res_normal)) * 1000
        
        rx = np.arctan(c[1]) * 1e6
        ry = np.arctan(-c[0]) * 1e6

        # --- UI 更新 ---
        self.lbl_eqn.setText(f"Z={c[0]:.4f}X+{c[1]:.4f}Y+{c[2]:.4f}")
        self.lbl_z.setText(f"{mean_z:.5f} mm") 
        self.lbl_pv.setText(f"{pv:.3f} µm"); self.lbl_ttv.setText(f"{ttv:.3f} µm")
        self.lbl_rx.setText(f"{rx:.2f} µrad"); self.lbl_ry.setText(f"{ry:.2f} µrad")
        self.draw_plots(tx, ty, tz)
        self.setup_selectors()

    def draw_plots(self, tx, ty, tz):
        dx, dy, dz = tx[self.active_idx], ty[self.active_idx], tz[self.active_idx]
        axes = [self.canvas.ax3d, self.canvas.ax_xy, self.canvas.ax_xz, self.canvas.ax_yz]
        lbs = [("X", "Y", "Z"), ("X (mm)", "Y (mm)"), ("X (mm)", "Z (mm)"), ("Y (mm)", "Z (mm)")]
        for ax, lb in zip(axes, lbs):
            ax.clear(); ax.grid(True, linestyle=':', alpha=0.5)
            ax.set_xlabel(lb[0]); ax.set_ylabel(lb[1])
            if len(lb)>2: ax.set_zlabel(lb[2])

        sc_params = {'c': dz, 'cmap': 'turbo', 's': 20, 'alpha': 0.8}
        self.canvas.ax3d.scatter(dx, dy, dz, **sc_params)
        self.canvas.ax_xy.scatter(dx, dy, **sc_params)
        self.canvas.ax_xz.scatter(dx, dz, **sc_params)
        self.canvas.ax_yz.scatter(dy, dz, **sc_params)
        
        # 🌟 新增：在 3D 视图中渲染半透明拟合基准平面 🌟
        if self.current_coeffs is not None:
            c = self.current_coeffs
            xx, yy = np.meshgrid(np.linspace(dx.min(), dx.max(), 10), np.linspace(dy.min(), dy.max(), 10))
            zz = c[0] * xx + c[1] * yy + c[2]
            # 采用柔和的青蓝色，透明度 0.3，不遮挡散点
            self.canvas.ax3d.plot_surface(xx, yy, zz, color='#3498db', alpha=0.3, edgecolor='none')

        if self.temp_selected_mask.sum() > 0:
            txs, tys, tzs = tx[self.temp_selected_mask], ty[self.temp_selected_mask], tz[self.temp_selected_mask]
            for ax in [self.canvas.ax_xy, self.canvas.ax_xz, self.canvas.ax_yz]:
                sx, sy = (txs, tys) if ax == self.canvas.ax_xy else (txs, tzs) if ax == self.canvas.ax_xz else (tys, tzs)
                ax.scatter(sx, sy, c='red', s=50, marker='x', linewidth=2)
        self.canvas.draw()

    def on_select(self, eclick, erelease, view_type):
        tx, ty, tz = self.get_final_transformed_data(self.df_raw)
        ax, ay, az = tx[self.active_idx], ty[self.active_idx], tz[self.active_idx]
        x1, y1, x2, y2 = eclick.xdata, eclick.ydata, erelease.xdata, erelease.ydata
        if x1 is None: return
        if view_type == 'XY': in_box = (ax >= min(x1,x2)) & (ax <= max(x1,x2)) & (ay >= min(y1,y2)) & (ay <= max(y1,y2))
        elif view_type == 'XZ': in_box = (ax >= min(x1,x2)) & (ax <= max(x1,x2)) & (az >= min(y1,y2)) & (az <= max(y1,y2))
        elif view_type == 'YZ': in_box = (ay >= min(x1,x2)) & (ay <= max(x1,x2)) & (az >= min(y1,y2)) & (az <= max(y1,y2))
        self.temp_selected_mask.fill(False)
        self.temp_selected_mask[self.active_idx[in_box]] = True
        self.update_plots_only()

    def update_plots_only(self):
        tx, ty, tz = self.get_final_transformed_data(self.df_raw)
        self.draw_plots(tx, ty, tz)

    def setup_selectors(self):
        self.selectors = []
        for ax, vt in zip([self.canvas.ax_xy, self.canvas.ax_xz, self.canvas.ax_yz], ['XY', 'XZ', 'YZ']):
            sel = RectangleSelector(ax, lambda e, r, v=vt: self.on_select(e, r, v), useblit=True, button=[1], props=dict(facecolor='red', alpha=0.15, edgecolor='red'))
            self.selectors.append(sel)

    def apply_manual_deletion(self):
        if self.temp_selected_mask.sum() == 0: return
        self.manual_mask &= (~self.temp_selected_mask)
        self.temp_selected_mask.fill(False)
        self.update_analysis() 

    def save_file(self):
        if self.df_raw is None: return
        path, _ = QFileDialog.getSaveFileName(self, "导出", "Result_Data.csv", "CSV (*.csv)")
        if path:
            tx, ty, tz = self.get_final_transformed_data(self.df_raw)
            pd.DataFrame({'Z_um': tz[self.active_idx], 'X_mm': tx[self.active_idx], 'Y_mm': ty[self.active_idx]}).to_csv(path, index=False)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SurfaceAnalyzerPro()
    window.show()
    sys.exit(app.exec())