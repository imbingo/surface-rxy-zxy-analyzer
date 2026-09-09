# V4.6.4 装调补偿：阶段 1–3

本轮仅新增无界面的数学核心与测试，应用版本仍为 V4.6.3。

## 阶段一 A–F 审查

A. 当前数学定义：AnalysisMixin.fit_plane 使用中心化最小二乘拟合
Z=aX+bY+c；compute_plane_metrics 使用 Rx=atan(b)、Ry=atan(-a)，
角度输出为 µrad。Mean Z 为参与拟合点的 Z 均值；PV/RMS 使用法向残差，
TTV 为原始 Z 极差。装调模块不修改这些定义。

B. ParallelismMixin._current_parallel_record 保存 x/y/z 点集副本、metrics、
来源、滤波与处理链信息。直接复用 metrics 内的 a/b/c，无需重新拟合点云。

C. _compute_parallel_result_from_records 中 drx/dry 均为 measurement-reference。
新模块 error=current-target，adjustment=target-current；不直接把旧 drx/dry 当调整量。

D. 当前 step_height 在两面 XY 质心中点计算 z_measure-z_base，单位 mm；
它既不等于一般情况下的 Mean Z 差，也不必等于新装调原点处的高度差。

E. 装调原点默认支撑点几何中心，亦可选 (0,0) 或自定义。
H=ΔZ0+Δa(X-X0)+Δb(Y-Y0)，ΔZ0=target(X0,Y0)-current(X0,Y0)。
更换原点不会改变固定两平面间每个支点的物理调整量。
自定义目标用 Plane.from_pose(rx_urad, ry_urad, z0_um, origin) 建立：
a=-tan(Ry)、b=tan(Rx)、c=Z0-aX0-bY0；角度先转 rad。

F. 新增 surface_analyzer/adjustment.py、tests/test_adjustment.py 和本文档。
不改现有平行度、Recipe、界面及拟合实现。Recipe 当前 schema=7。
设置、交换、清空两面会清空 parallel_result；记录是设置时的快照，主控 ROI
变化并不自动重写已保存记录。后续 UI 集成必须明确快照更新与装调失效联动。
之前 app.py / test_v461.py 的 3D 选中点修复保持原样。

## 调用与结果契约

calculate_adjustment(current, target, supports, config) 接受 Plane、SupportPoint
列表及 AdjustmentConfig，返回不可变 AdjustmentResult；不导入 Qt，不读取源点云。
内部 XY/Z 使用 mm，垫片和高度输出 µm，角度 µrad，转换集中在模块顶部。
SupportPoint 要求提供名称、真实接触点 XY、基础厚度、min/max，可选每点 step。

三点、四点与更多点共用同一几何验证与最小二乘算法。XY 居中后按共同最大
偏移量归一化，检查 SVD 条件数，超过 1e8、重复点或少于三点均拒绝。
不得逐轴独立归一化以掩盖近共线布局。

每点 E=current-target，H=-E，正数加片、负数抽片。
T_ideal=T_base+H。固定步进对最终厚度取最近值，十进制恰半步远离零；
每点 step 优先，否则默认全局 5 µm。不量化时保持理论厚度。
H_actual=T_recommended-T_base，quantization_error=T_recommended-T_ideal。
理论或推荐厚度超限不截断，executable=False；基础厚度超限附警告但可继续评估。

对 H_actual 拟合实际调整平面，与 current 相加得到 predicted。
残余 Rx/Ry 为 predicted 与 target 的精确 atan 角度之差，
残余 DZ0 为 predicted-target 在同一装调原点的高度差。
N>=4 标为最小二乘几何预测，并提示不等于真实机械接触姿态；超限方案提示不可执行。
正式支撑共面 PV/RMS、报警、UI、Recipe、导出与发布属于后续阶段。

## 验证

运行 `.venv/Scripts/python.exe -m unittest tests.test_adjustment -v`。
覆盖纯 DZ、纯 Rx/Ry 的 tan 精确值、组合姿态恢复、N>4、原点不变性、
交换符号、输入不变性、负调整、最终厚度量化、每点步进、半步边界、
超限不截断、基础厚度警告、无效几何/配置、大坐标、Mean Z 分离。
普通尺度残余容差为 1e-8 µrad/µm；1e9 mm 大坐标场景为 1e-4。

本轮执行结果：全量 discovery 148 项通过（50.959 秒）；其后补充四点量化
姿态与自定义目标原点交叉验证，装调专项最终 10 项全部通过。
完整应用、Recipe 与发布仍未接入，当前仅可通过 Python 调用该数学模块。
