# Surface Rxy ZXY Analyzer V4.5.1

面型及 Rxy 分析工具当前版本为 **V4.5.1**。GitHub `main` 根目录保留当前版本的运行入口、模块、文档、测试和 Demo；旧版本文件统一放在 [`archive/legacy_versions`](archive/legacy_versions/README.md)。

## 当前版本入口

- `面型及Rxy分析工具V4.5.1.py`：V4.5.1 Python 启动入口。
- `start_surface_analyzer_v4_5_1.bat`：Windows 推荐启动脚本，自动创建并复用仓库内 `.venv`。
- `surface_analyzer/`：模块化 GUI、分析、文件导入、ROI、Recipe、报告和公共接口实现。
- `requirements.txt`：Python 依赖清单。

## 运行

Windows 推荐双击：

```text
start_surface_analyzer_v4_5_1.bat
```

命令行运行：

```powershell
.\start_surface_analyzer_v4_5_1.bat
```

只检查环境和模块导入：

```powershell
.\start_surface_analyzer_v4_5_1.bat --check
```

也可以直接使用 Python 入口：

```powershell
python .\面型及Rxy分析工具V4.5.1.py
python -m surface_analyzer
```

## V4.5.1 重点

- Smart ROI 完成后缓存最终掩码，同一数据的多个 ROI 复用拓扑；删点、撤销、滤波、残差显示、重绘和切页不再重复建图或抓面。
- 连续曲面生长采用 Fast Accept / Fast Reject / 灰区精细拟合的粗到细策略，并传播复用局部平面；平滑面基准约提升 8.7~9.8 倍。
- XY、XZ、YZ 均可按实际显示点选择真实 XYZ 种子，解决 XY 重叠多层面无法区分的问题。
- 支持在 XY/XZ/YZ 预先框定候选范围，多个范围取交集；显示残差只用于选点，抓面始终使用真实变换后 Z。
- 首次抓面无论点数多少均在后台执行，提供真实阶段进度和取消；内部记录拓扑、Grow、Fast/Slow、拟合、Mask、分析、绘图和总耗时。
- Recipe 升级为 schema 6，新建 Smart ROI 使用算法 V3；V4.5.0 V2 与更早 legacy ROI 保留原算法重放，不静默改变历史点数。
- 加入 V4.0.3 长期使用 Demo 的 Golden Sample，四种滤波模式下 Rx/Ry/TTV/PV/RMS 与 V4.0.3 逐项一致。

## V4.5.0 重点

- 普通 CSV/TXT/TSV/DAT/ASC/XYZ、单列分隔 Excel 和多列 Excel 共用新的表头状态机；支持空行、注释式表头、自定义设备字段和重复列名稳定改名。
- X/Y/Z、Position、Coordinate、Height、Thickness 及 `µm/μm/um/mm/nm` 单位统一语义识别；UI 保留原始列名并允许用户修改自动映射。
- 超大文件只有在 X/Y/Z 语义唯一时才执行空间网格采样；语义不确定会明确降级文件位置采样，不再静默使用前三列。
- Precitec 按 `PointsPerLine × NumberOfLines` 和原始记录序号重建网格，坏行保留为空洞；自动识别蛇形扫描并校验行内连续性、行间分离和尺寸完整性。
- 新版智能抓面默认“连续曲面抓取”，矩阵/Precitec 使用真实 8 邻域，普通点云优先 Delaunay、失败时回退自适应 kNN，可连续跟随 Bow/Warpage 并在孔洞、台阶和侧壁停止。
- 智能抓面增加严格/标准/宽松三档；状态和报告记录实际拓扑、局部点距、抓取点数及回退原因。
- Recipe 升级为 schema 5；旧智能 ROI 缺少算法版本时继续按 legacy 算法重放，不静默改变历史点数。
- 新增“撤销删点”按钮，只撤销最近一次已确认的手动删除，不影响姿态变换、滤波或 ROI。

## V4.4.0 重点

- 导入策略新增显式 `Zygo XYZ` 类型，严格解析 `Zygo XYZ Data File - Format 1` 的 Phase 参数、固定 CameraRes 字段和两个 `#` 之间的正文。
- Zygo 坐标始终由用户填写的 X/Y 采样间距生成；CameraRes 仅作为检测值，相差超过 1% 时提示但不覆盖手动参数。
- Zygo `No Data` 保留缺测统计与网格定位，不参与拟合；空格与 Tab 分隔均可读取，不在导入阶段翻转 Y。
- `XYZ 点表`模式确定性识别 Precitec FSS Explorer `.dat`，保留 Encoder、Intensity 和扫描参数，并自动映射 `X Pos [mm]`、`Y Pos [mm]`、`Thickness 1`。
- Precitec 导入核对 `PointsPerLine × NumberOfLines`，坏行和完整性警告写入状态、CSV 元数据与报告。
- Recipe 升级为 schema 4，新增通用采样间距字段，同时保留旧矩阵 Pitch 别名用于版本回退。

## V4.3.0 重点

- 采用显微镜、测量光束和彩色三阶曲面的透明背景品牌图标，统一应用于窗口、EXE 和 Setup。
- 实时结果卡片统一标题字体、字号、行高和数值样式，平面残差 PV 不再出现中英文字号割裂。
- Gap 页面和状态提示移除 emoji；X-Z/Y-Z 明确标注为“投影”；平移归零仅在实际应用后高亮。
- 窗口按可用屏幕自适应，左侧控制区可拖动调整；新增可记忆的 Windows 系统边框选项和可访问名称。
- 超大文件说明改为状态栏与导入策略提示，不再每次弹出模态窗口。
- SHA-256、批量处理、平行度和多层 Gap 计算加入后台任务、进度反馈和取消操作。
- 新增拖放载入、最近文件以及 `Ctrl+O`、`Ctrl+S`、`Ctrl+R` 快捷键；重置前增加确认。

## V4.2.1 修正

- 调整品牌图标中的三阶鞍形曲面位置和振幅，在物镜与曲面之间保留明确工作距离；只有测量光束接触曲面，避免小尺寸图标出现镜头与曲面粘连。

## V4.2.0 重点

- 全新透明背景品牌图标：简化光学探头与三阶鞍形彩色面型，并应用于窗口、任务栏、EXE 与 Setup。
- 启动时立即显示阶段式启动画面；启动失败会明确提示并写入本地错误日志。
- 高度矩阵无显式缺测标记时只兼容精确历史哨兵，不再误删所有小于 -999 的真实深负值。
- Recipe 大文件参数统一按 UI 合法范围钳制，避免异常配置造成过量内存分配。
- 批量报告对重名源文件自动追加序号，并在汇总中记录源文件与报告完整路径。
- BF 平面残差 PV 明确标注“仅去倾斜、未去曲率”；高阶拟合病态时在 UI 与报告中警告。
- 抽样分析报告强化估算状态，批量汇总增加 `sampled`、`extrema_preserved` 和 `warnings` 字段。
- 正式构建把真实 Git commit 写入安装包，`--check` 可追溯到准确源码提交。

## V4.1.0 重点

- 文件导入策略中显式选择 `XYZ 点表` 或 `Z 矩阵`，选择会自动记忆并写入 Recipe。
- 去除原先依赖“8列宽度”的点表/矩阵自动猜测，避免宽点表被误判为高度矩阵。
- 支持 TXT/Excel 单个物理列内用英文/中文分号等分隔的 XYZ 逻辑字段。
- 前置探头型号、光强设置、扫描参数等说明不会阻断导入，并进入可追溯元数据。
- 新增一阶、二阶、三阶去除后残差四视图，以及各阶残差 PV、RMS、R² 诊断结果。
- 保留现有最佳拟合平面法向 PV、Rx/Ry 和 TTV 口径，高阶显示不会静默修改权威结果。

## V4.0.3 基础能力

- 新增面型量测品牌图标，并统一应用于窗口、任务栏、EXE 和 Setup 安装程序。
- 支持 XYZ、DAT、ASC、CSV、Excel 和 VR/基恩士高度矩阵。
- 支持 Zeiss/菲索类多行参数头和大文件预抽样。
- 高度矩阵可比较多个数值候选区，自动处理顶部列坐标、左侧行号和尾部空列。
- 支持矩形、圆形和智能抓面 ROI，并可保存到 Recipe 和报告。
- 支持局部中位数、Sigma 迭代滤波、手动删点及删除操作 Recipe 重放。
- 支持多层胶厚扣减、平行度、台阶高度、报告图和 CSV 导出。
- 提供无界面 Python/CLI 接口，便于 C# 平台调用。
- 主控和报告的 XY 视图保持物理等比例，不随窗口比例拉伸。
- 3D 视图按真实 X/Y 范围自适应，仅对过小的 Z 范围做受控视觉增强。
- 平行度页面在低高度窗口中使用纵向滚动，报告结果文字不再重叠。

## 文档

- [V4.5.0 表头与智能抓面 Demo](demo_data/V4.5.0_表头与智能抓面Demo说明.md)
- [V4.4.0 Zygo / Precitec 设备格式 Demo](demo_data/V4.4.0_设备格式Demo说明.md)
- [V4.0.2 接口文档](docs/V4.0.2_接口文档.md)
- [V4.0.2 模块架构](docs/V4.0.2_架构说明.md)
- [V4.0.2 本地测试清单](docs/V4.0.2_本地测试清单.md)
- [历史版本归档索引](archive/legacy_versions/README.md)

## 自动测试

```powershell
python -m unittest discover -s tests -v
```

## 历史版本

旧单文件、旧启动器和 V4.0 模块拆分工具已移至 `archive/legacy_versions`。旧 BAT 仅用于保留历史文件，不等同于冻结源码；需要准确恢复某一版本时，请按归档索引使用对应 Git 提交。
