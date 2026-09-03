# V4.6.1 Release Notes

V4.6.1 为低风险 Hotfix，量测算法、ROI/Smart ROI 数学、Rx/Ry/PV/TTV/RMS/Mean Z
以及高阶拟合定义均保持不变。

## 主要变更

- Z Matrix 表头声明的 Rows/垂直行数降级为提示与追溯信息；与正文行数不一致时正常导入并记录
  `declared_rows` / `actual_rows` / `row_count_mismatch`，不再阻断导入。
- 只有用户手动填写的 Matrix Rows 仍是硬约束，文本 Z Matrix 与 Excel Z Matrix 行为统一。
- 普通临时选择在 XY、XZ、YZ 和 3D 四视图同步高亮；3D 使用当前显示 Z 值并只对绘制覆盖层做
  display-only 均匀抽样，不修改真实 `temp_selected_mask`、选中数量、删除、ROI 或计算输入。
- XY、XZ、YZ 双击恢复各自初始显示范围；3D 双击同时恢复初始 X/Y/Z 范围和相机角度；
  操作不触发重新分析。
- 新增 VR 风格 Demo `demo_data/V4.6.1_Keyence_VR_声明15行_实际13行_Demo.csv`，
  用于验证表头声明行数与实际正文不一致时仍可正常导入并给出警告，且保留整行缺测的 raster 几何。

## 兼容与验证

- Recipe 仍为 schema 7，公共 `load_xyz_points` 签名和 `LoadedPoints` 结构不变。
- V4.5.4–V4.6.0 Golden Sample 量测、ROI 点集与拓扑回归全部保持通过。
- 完整回归测试：117 passed，17 subtests passed。
